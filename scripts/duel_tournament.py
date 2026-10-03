"""Duel policy v1 vs v2 offline: the go/no-go tables of docs/night/w2b-duel-v2.md, as Markdown on stdout.

    uv run python scripts/duel_tournament.py              # the full report (~1 min)
    uv run python scripts/duel_tournament.py --quick      # 40 scenarios per cell instead of 200

Nothing here talks to the game: `bazaar_agent.duel_arena` plays both policies against its rival zoo and
replays the practice payloads in tests/fixtures/evals/duels_done.json.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from bazaar_agent import duel_arena as arena
from bazaar_agent import guardrails as gr
from bazaar_agent.agents.duel_v2 import V2Params

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evals" / "duels_done.json"


def table(header: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def by(outcomes: list[arena.Outcome], **match: object) -> list[arena.Outcome]:
    return [o for o in outcomes if all(getattr(o, k) == v for k, v in match.items())]


def ratio(a: float, b: float) -> str:
    return f"{a / b:.2f}×" if b else "n/a"


def compare(res: Mapping[str, list[arena.Outcome]], groups: Iterable[tuple[str, dict[str, object]]]) -> str:
    rows = []
    for label, match in groups:
        one, two = arena.summarize(by(res["v1"], **match)), arena.summarize(by(res["v2"], **match))
        rows.append(
            (
                label,
                one.n,
                f"{one.mean_result:.2f}",
                f"{two.mean_result:.2f}",
                ratio(two.mean_result, one.mean_result),
                f"{one.deal_rate:.3f}",
                f"{two.deal_rate:.3f}",
                f"{one.mean_rounds:.1f}",
                f"{two.mean_rounds:.1f}",
                one.outside + two.outside,
            )
        )
    header = ("rivals", "duels", "v1 P/duel", "v2 P/duel", "v2/v1", "v1 deals", "v2 deals", "v1 rounds", "v2 rounds")
    return table((*header, "outside"), rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="40 scenarios per cell instead of 200")
    parser.add_argument("--safety", type=int, default=10_000, help="duels in the outside-limit check")
    args = parser.parse_args()
    n = 40 if args.quick else 200
    rules = gr.load_guardrails().rules
    params = V2Params.from_rules(rules)  # GUARDRAILS.md's v2 knobs, as `duel_policy` = v2 would play them
    pols = {"v1": arena.v1_policy(rules.duel_anchor, rules.duel_floor_margin, rules.duel_endgame_ticks)}
    pols["v2"] = arena.v2_policy(params)
    styles = [(s, {"style": s}) for s in arena.STYLES]
    out = [f"v2 parameters: {params}", ""]

    out.append("## Go/no-go: 6 styles × 200 scenarios × 2 roles × decays 0.08 and 0.10 × 12 and 16 ticks")
    out.append("Six duels per session share one deadline and the team's one accept per tick.\n")
    head = arena.tournament(pols, scenarios=n, decays=(0.08, 0.10), ticks=(12, 16))
    out.append(compare(head, [*styles, ("all", {})]))
    conceders = [o for o in head["v1"] if o.style in ("linear", "convex", "simbot")]
    conceders2 = [o for o in head["v2"] if o.style in ("linear", "convex", "simbot")]
    c1, c2 = arena.summarize(conceders), arena.summarize(conceders2)
    o1, o2 = arena.summarize(by(head["v1"], style="oneshot")), arena.summarize(by(head["v2"], style="oneshot"))
    a1, a2 = arena.summarize(head["v1"]), arena.summarize(head["v2"])
    out.append("")
    out.append(
        table(
            ("criterion", "bar", "measured", "verdict"),
            [
                (
                    "mean result v2 / v1",
                    "≥ 1.40",
                    f"{a2.mean_result / a1.mean_result:.2f}",
                    "GO" if a2.mean_result >= 1.4 * a1.mean_result else "NO-GO",
                ),
                (
                    "deal rate vs conceders (linear, convex, simbot)",
                    f"≥ v1 {c1.deal_rate:.3f}",
                    f"{c2.deal_rate:.3f}",
                    "GO" if c2.deal_rate >= c1.deal_rate else "NO-GO",
                ),
                (
                    "deal rate vs one-shot rivals",
                    f"≥ 0.9 × v1 = {0.9 * o1.deal_rate:.3f}",
                    f"{o2.deal_rate:.3f}",
                    "GO" if o2.deal_rate >= 0.9 * o1.deal_rate else "NO-GO",
                ),
            ],
        )
    )

    out.append("\n## Decay by decay (all six styles, 12 and 16 ticks)\n")
    every = arena.tournament(pols, scenarios=n, decays=(0.06, 0.08, 0.10), ticks=(12, 16))
    out.append(compare(every, [(f"decay {d}", {"decay": d}) for d in (0.06, 0.08, 0.10)]))

    out.append("\n## Accept-slot congestion: 2 duels per session instead of 6 (decays 0.08 and 0.10)\n")
    pairs = arena.tournament(pols, scenarios=n, decays=(0.08, 0.10), ticks=(12, 16), per_session=2)
    out.append(compare(pairs, [*styles, ("all", {})]))

    out.append("\n## Two-issue duels (price and days), decays 0.08 and 0.10\n")
    out.append("Both sides draw a signed day weight in −4..4, as the simulator does. `truth signed` scores days as the")
    out.append("simulator's `days_meaning` says; `truth worst` charges every day at |weight|.\n")
    rows = []
    for signed in (False, True):
        p = replace(params, days_signed=signed)
        pol = {"v1": pols["v1"], "v2": arena.v2_policy(p)}
        guard = {"v2": gr.Guardrails(duel_policy="v2", duel_days_signed=signed)}
        r = arena.tournament(pol, scenarios=n, decays=(0.08, 0.10), ticks=(12,), two_issue=True, rules=guard)
        for name in ("v1", "v2"):
            if name == "v1" and signed:
                continue
            truth_s, truth_w = arena.summarize(r[name]), arena.summarize(r[name], worst=True)
            worst_outside = sum(1 for o in r[name] if o.status == "deal" and o.gain_worst <= 0)
            signed_outside = sum(1 for o in r[name] if o.status == "deal" and o.gain_signed <= 0)
            label = name if name == "v1" else f"v2, duel_days_signed = {str(signed).lower()}"
            rows.append(
                (
                    label,
                    truth_s.n,
                    f"{truth_s.mean_result:.2f}",
                    f"{truth_w.mean_result:.2f}",
                    f"{truth_s.deal_rate:.3f}",
                    signed_outside,
                    worst_outside,
                    truth_s.denied,
                )
            )
    out.append(
        table(
            (
                "policy",
                "duels",
                "P/duel truth signed",
                "P/duel truth worst",
                "deals",
                "outside if truth signed",
                "outside if truth worst",
                "refused",
            ),
            rows,
        )
    )

    out.append(f"\n## Safety: {args.safety:,} duels per policy, default parameters, outside-limit closes\n")
    per_cell = max(1, args.safety // (len(arena.STYLES) * 2 * 3 * 2 * 2))
    safe_rows = []
    for two in (False, True):
        r = arena.tournament(pols, scenarios=per_cell, decays=(0.06, 0.08, 0.10), ticks=(12, 16), two_issue=two)
        for name in ("v1", "v2"):
            s = arena.summarize(r[name])
            safe_rows.append(("two-issue" if two else "price only", name, s.n, s.outside, s.denied))
    out.append(table(("duels", "policy", "n", "outside our limit", "moves the guardrail refused"), safe_rows))

    out.append("\n## Robustness rivals (not in the go/no-go), decays 0.08 and 0.10\n")
    out.append("`late`: silent 3–5 ticks, then concedes (each of its messages then adds a round to our free offers).")
    out.append("`stubborn`: one price, repeated every tick (duel 274).\n")
    rob = arena.tournament(pols, styles=arena.ROBUSTNESS, scenarios=n, decays=(0.08, 0.10), ticks=(12, 16))
    out.append(compare(rob, [(s, {"style": s}) for s in arena.ROBUSTNESS]))

    out.append("\n## Sensitivity: `duel_max_own_offers` (decays 0.08 and 0.10, six styles)\n")
    sens_rows = []
    base = arena.summarize(head["v1"]).mean_result
    for cap in (1, 2, 3, 4):
        r = arena.tournament(
            {"v2": arena.v2_policy(replace(params, max_own_offers=cap))},
            scenarios=n,
            decays=(0.08, 0.10),
            ticks=(12, 16),
        )
        s = arena.summarize(r["v2"])
        sens_rows.append((cap, f"{s.mean_result:.2f}", ratio(s.mean_result, base), f"{s.deal_rate:.3f}"))
    out.append(table(("duel_max_own_offers", "v2 P/duel", "v2/v1", "deals"), sens_rows))

    out.append("\n## Sensitivity: `duel_accept_margin_ticks` (decays 0.08 and 0.10, six styles)\n")
    out.append("1 = accept by D − 2 (the default). 0 = by D − 1, which assumes an accept made on D − 1 settles.\n")
    margin_rows = []
    for margin in (1, 0):
        p = replace(params, accept_margin=margin)
        r = arena.tournament({"v2": arena.v2_policy(p)}, scenarios=n, decays=(0.08, 0.10), ticks=(12, 16))
        s = arena.summarize(r["v2"])
        rep = sum(row["result"] for row in arena.replay(arena.load_practice(FIXTURE), arena.v2_policy(p)))
        margin_rows.append(
            (margin, f"{s.mean_result:.2f}", ratio(s.mean_result, base), f"{s.deal_rate:.3f}", f"{rep:.1f}")
        )
    out.append(table(("duel_accept_margin_ticks", "v2 P/duel", "v2/v1", "deals", "replay P"), margin_rows))

    out.append("\n## Replay: the 12 practice duels we never answered (2026-10-02)\n")
    out.append("The rival sends exactly its recorded offers; it takes ours only if ours is at least as good for it.")
    out.append("Every duel on one clock, one accept per tick. `best offer` = accepting the rival's best, no rounds.\n")
    practice = arena.load_practice(FIXTURE)
    r1 = {r["duel"]: r for r in arena.replay(practice, pols["v1"])}
    r2 = {r["duel"]: r for r in arena.replay(practice, pols["v2"])}
    rows = []
    for d in practice:
        did = int(d["duel"])
        if did not in r1:
            continue
        rival = [m["price"] for m in d["messages"] if m.get("from") == d.get("rival") and m.get("price") is not None]
        seller, limit = d["role"] == "seller", int(d["your_limit"])
        best = max(rival) if seller and rival else (min(rival) if rival else None)
        ideal = max(0, (best - limit) if seller else (limit - best)) if best is not None else 0
        rows.append(
            (did, d["role"], limit, ideal, r1[did]["result"], r1[did]["rounds"], r2[did]["result"], r2[did]["rounds"])
        )
    total = ("total", "", "", sum(r[3] for r in rows), round(sum(r[4] for r in rows), 1), "")
    rows.append((*total, round(sum(r[6] for r in rows), 1), ""))
    out.append(table(("duel", "role", "limit", "best offer", "v1 P", "v1 rounds", "v2 P", "v2 rounds"), rows))
    print("\n".join(out))


if __name__ == "__main__":
    main()
