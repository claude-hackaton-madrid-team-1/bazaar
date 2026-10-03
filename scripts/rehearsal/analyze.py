"""Numbers for one rehearsal run: crashes, refusals, guardrail denials, accept slots, cash vs floor, venue,
Market Tests, duels, deals. Reads run-<name>/ logs, me.jsonl, final.json and the run's throwaway Postgres."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(os.environ.get("REHEARSAL_DIR") or Path(__file__).resolve().parents[2] / ".local" / "rehearsal")
MARKERS = ("Traceback (most recent call last)", "tick loop:", "SmokeNetworkError", "rate_limited", " 429")


def rule_of(denial: str) -> str:
    for key, pat in [
        ("venue_bond_reserve (cash floor + 270)", r"venue_bond_reserve"),
        ("cash_floor", r"cash_floor"),
        ("max_spend_per_game_hour", r"max_spend_per_game_hour"),
        ("max_price_<rarity>", r"max_price_"),
        ("block_buying_held_cards", r"block_buying_held_cards"),
        ("accept slot", r"accept\(s\) already this tick|accept slot"),
        ("duel_inside_limit", r"duel_inside_limit"),
        ("max_packs_per_game_hour", r"max_packs"),
        ("sell floor", r"sell price|your_value"),
        ("kill switch", r"trading_enabled|pause file"),
        ("allow_venue_open", r"allow_venue_open"),
    ]:
        if re.search(pat, denial):
            return key
    return "other: " + denial[:60]


def main(name: str, db: str | None) -> dict:
    run = HERE / f"run-{name}"
    out: dict = {"run": name}
    logs = {p.stem: p.read_text(errors="replace") for p in run.glob("*.log") if p.stem != "db-init"}
    out["crash_markers"] = {k: {m: v.count(m) for m in MARKERS if v.count(m)} for k, v in logs.items()}
    refused = Counter()
    for k, v in logs.items():
        for m in re.finditer(r"refused ([a-z_]+)", v):
            refused[f"{k}: {m.group(1)}"] += 1
    out["refused_by_server"] = dict(refused)
    ticks = {}
    for k in ("taker", "maker", "duels"):
        t = [int(x) for x in re.findall(r"^tick (\d+)", logs.get(k, ""), re.M)]
        ticks[k] = (min(t), max(t), len(set(t))) if t else None
    out["ticks_seen(min,max,distinct)"] = ticks
    denials = Counter()
    spend_by_tick = defaultdict(int)
    for k, v in logs.items():
        for line in v.splitlines():
            m = re.search(r"(?:denied|GUARDRAIL)[: ]+(.*)", line)
            if m and ("denied" in line or "GUARDRAIL" in line):
                for part in m.group(1).split(";"):
                    denials[f"{k}: {rule_of(part)}"] += 1
                if "max_spend_per_game_hour" in line:
                    t = re.match(r"tick (\d+)", line)
                    if t:
                        spend_by_tick[int(t.group(1)) // 25 * 25] += 1
    out["guardrail_denials"] = dict(sorted(denials.items(), key=lambda x: -x[1]))
    out["spend_cap_denials_per_25_ticks"] = dict(sorted(spend_by_tick.items()))
    timing = Counter()
    for k, v in logs.items():
        for pat in (
            "no time left",
            "DROPPED",
            "tick window closed",
            "budget spent before sending",
            "took the rest of the tick",
            "re-deciding next tick",
            "late",
        ):
            c = v.count(pat)
            if c:
                timing[f"{k}: {pat}"] = c
    out["tick_window_misses"] = dict(timing)
    maker = logs.get("maker", "")
    out["venue_opened"] = re.findall(r"tick (\d+) venue: OPENED (\S+)", maker)
    out["broker_matches_sent"] = len(re.findall(r"broker: match ", maker))
    out["market_tests"] = re.findall(r"Market Test (b\d+) over[^\n]*", maker)
    out["holds"] = {k: v.count("holding") for k, v in logs.items() if v.count("holding")}

    samples = []
    for line in (run / "me.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if "me" in rec:
            me = rec["me"]
            v = me.get("venue")
            ours = bool(v) and not me.get("starter_broker_key")
            samples.append((rec["tick"], me["cash"], ours, me.get("score", {}).get("score")))
    if samples:
        floor_breaks = [(t, c) for t, c, ours, _ in samples if c < (100 if ours else 370)]
        out["cash_samples"] = {
            "n": len(samples),
            "first": samples[0][:2],
            "last": samples[-1][:2],
            "min": min(s[1] for s in samples),
            # before the venue the effective floor is 370; the opening itself spends the 270 reserve (-> >= 100)
            "below_effective_floor": floor_breaks[:10],
        }
        out["score_trajectory"] = [(t, s) for t, _, _, s in samples[:: max(1, len(samples) // 12)]]
    final = json.loads((run / "final.json").read_text())
    me = final["me"]
    out["final"] = {
        "tick": final["clock"]["tick"],
        "t_hours": final["clock"].get("t_hours"),
        "cash": me["cash"],
        "level": me["level"],
        "venue": me.get("venue"),
        "score": {
            k: me["score"].get(k)
            for k in (
                "score",
                "rank",
                "market",
                "negotiating",
                "mm_points",
                "duel_points",
                "ladder_points",
                "bench_efficiency",
                "deals",
                "album_filled",
            )
        },
        "open_offers": sum(len(v) for v in final["offers"].values() if isinstance(v, list)),
        "exit": final["exit"],
        "wall_s": final["wall_s"],
    }
    duels = final["duels_done"]["duels"]
    res = []
    for d in duels:
        ours = [m for m in d["messages"] if m["from"] == "you" and m.get("price") is not None]
        res.append(
            {
                "duel": d["duel"],
                "status": d["status"],
                "role": d["role"],
                "limit": d["your_limit"],
                "price": (d.get("deal") or {}).get("price") if isinstance(d.get("deal"), dict) else d.get("price"),
                "rounds": d.get("rounds"),
                "result": d.get("result"),
                "our_priced_msgs": len(ours),
                "inside_limit": None,
            }
        )
    out["duels"] = {
        "n": len(res),
        "status": dict(Counter(r["status"] for r in res)),
        "mean_rounds_deal": round(
            sum(r["rounds"] or 0 for r in res if r["status"] == "deal")
            / max(1, sum(r["status"] == "deal" for r in res)),
            2,
        ),
        "rows": res,
    }
    if db:
        import psycopg

        pw = os.environ["REHEARSAL_PG_PASSWORD"]  # the throwaway container's, never a real one
        with psycopg.connect(f"postgresql://b5:{pw}@[::1]:55491/{db}") as c:
            out["ledger_rows"] = dict(c.execute("select kind, count(*) from ledger group by kind").fetchall())
            multi = c.execute(
                "select tick, count(*), string_agg(source || ':' || item, ', ') from ledger"
                " where kind='accept' group by tick having count(*) > 1"
            ).fetchall()
            out["ticks_with_more_than_one_accept_booked"] = multi
            out["accepts_by_source"] = dict(
                c.execute("select source, count(*) from ledger where kind='accept' group by source").fetchall()
            )
            try:
                out["decisions_by_agent_status"] = {
                    f"{a}:{s}": n
                    for a, s, n in c.execute(
                        "select agent, status, count(*) from decisions group by 1,2 order by 1,2"
                    ).fetchall()
                }
            except Exception as e:
                out["decisions_by_agent_status"] = repr(e)
    return out


if __name__ == "__main__":
    name, db = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    result = main(name, db)
    (HERE / f"analysis-{name}.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: v for k, v in result.items() if k != "duels"}, indent=1, default=str))
    print(json.dumps({k: v for k, v in result["duels"].items() if k != "rows"}, default=str))
