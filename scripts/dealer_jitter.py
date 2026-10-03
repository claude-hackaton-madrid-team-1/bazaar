"""B12 grid: every `dealer_jitter_*` setting against Friday's dealers, cost vs predictability.

    uv run python scripts/dealer_jitter.py --runs 2000 --json docs/night/b12-dealer-jitter.json \\
        --md docs/night/b12-dealer-jitter-tables.md

Plans: W3's floor plans (`ladder.plan_for`, q 0.5, uncapped so El Chato and the pack are measured too)
and today's lowest-fill ladders (`ladder_floor_quantile` = 0). Dealers: W3's models fitted to every
team's Friday threads (`tests/fixtures/evals/dealer_threads.json`), under W3's rule and under B12's
step-capped rule, plus the real threads replayed with their limit at the top and bottom of their bracket.
A level passes when, in every gated cell (Abuela) and at both reply speeds, share ≥ `--share-bar` × the
deterministic plan's. A missed deal scores 0, so the deal rate is inside the share; its change is shown.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Any

from bazaar_agent.agents.dealer import BidPlan
from bazaar_agent.jitter_eval import OFF, Cell, Level, episodes, evaluate, replay_episodes
from bazaar_agent.ladder import Conversation, FloorRow, floor_table, from_rows, main_rows, plan_for
from bazaar_agent.ladder_cli import FRIDAY
from bazaar_agent.ladder_replay import fit

W3 = ("abuela", "card:common"), ("abuela", "card:uncommon"), ("abuela", "pack:sobre_barrio")
CHATO = ("chato", "card:uncommon"), ("chato", "card:rare")
TODAY = {  # today's lowest-fill ladders (docs/night/w3-ladder.md, "today" rows)
    ("abuela", "card:common"): BidPlan(7, 1, 12),
    ("abuela", "card:uncommon"): BidPlan(17, 1, 26),
    ("abuela", "pack:sobre_barrio"): BidPlan(17, 1, 20),
}


def levels() -> list[Level]:
    out = [OFF, Level("minstep", force=True)]
    grid = itertools.product((0, 1, 2, 3), (0.0, 0.5, 1.0), (0.0, 0.05, 0.1, 0.2, 0.35, 0.5), (2, 3, 4), (0, 2, 3))
    for spread, below, band, top, gap in grid:
        if spread <= 1 and below > 0:  # a jump from one under the start lands on it: the base step
            continue
        if (band == 0 and (gap or (below == 0 and top != 3))) or spread == band == 0:
            continue
        name = f"s{spread}" + (f"j{below:g}" if below else "") + (f"b{band:g}" if band else "") + f"m{top}"
        out.append(Level(name + (f"g{gap}" if gap else ""), spread, below, band, top, band_gap=gap))
    return out


Tagged = tuple[str, Cell]  # (source: "model:w3", "replay-hi:today", ...; the cell)
BaseKey = tuple[str, str, str, tuple[int, int, int], str]


def base_key(src: str, c: Cell) -> BaseKey:
    return (src, c.dealer, c.price_class, c.plan, c.rule)


def gate(cells: Sequence[Tagged], base: dict[BaseKey, Cell], bar: float) -> dict[str, Any]:
    """The worst change against the deterministic plan over `cells` (one round per tick and two), and
    whether it passes: share ≥ `bar` × the plan's at both speeds (a missed deal is share 0, so the deal
    rate is inside it; its change is reported, not gated)."""
    worst = {"share_ratio": 9.0, "lag_share_ratio": 9.0, "deal": 0.0, "lag_deal": 0.0, "fill": 0.0, "lag_fill": 0.0}
    for src, c in cells:
        b = base[base_key(src, c)]
        worst["share_ratio"] = min(worst["share_ratio"], c.ratio(b))
        worst["lag_share_ratio"] = min(worst["lag_share_ratio"], c.ratio(b, lagged=True))
        worst["deal"] = min(worst["deal"], c.summary.deal_rate - b.summary.deal_rate)
        worst["lag_deal"] = min(worst["lag_deal"], c.lagged.deal_rate - b.lagged.deal_rate)
        worst["fill"] = min(worst["fill"], c.summary.fill_within - b.summary.fill_within)
        worst["lag_fill"] = min(worst["lag_fill"], c.lagged.fill_within - b.lagged.fill_within)
    ok = min(worst["share_ratio"], worst["lag_share_ratio"]) >= bar
    return {**{k: round(v, 3) for k, v in worst.items()}, "pass": ok}


def _friday() -> tuple[list[Conversation], dict[tuple[str, str], FloorRow]]:
    convs = from_rows(json.loads(FRIDAY.read_text())["rows"])
    return convs, main_rows(floor_table(convs))


def families(key: tuple[str, str]) -> list[str]:
    return ["w3", "today"] if key in TODAY else ["w3"]


def job(spec: tuple[tuple[str, str], str, int, int, int]) -> list[Tagged]:
    """One dealer × class × plan family: every level on the fitted dealers (both rules) and the replays."""
    key, family, runs, seed, replays = spec
    convs, rows = _friday()
    row = rows[key]
    plan = TODAY[key] if family == "today" else plan_for(row, None).plan
    assert plan is not None, key
    lv = levels()
    eps = episodes(fit(convs, *key, row.opening), runs, seed)
    out = [(f"model:{family}", c) for c in evaluate(*key, plan, eps, lv, seed=seed)]
    for at in ("hi", "lo"):
        real = replay_episodes(convs, *key, at) * replays
        out += [(f"replay-{at}:{family}", c) for c in evaluate(*key, plan, real, lv, rules=("w3",), seed=seed)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--share-bar", type=float, default=0.95)
    ap.add_argument("--replays", type=int, default=10, help="jitter draws per real thread replayed")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--cells", action="store_true", help="also write every cell to --json (~10 MB)")
    ap.add_argument("--md", type=Path)
    args = ap.parse_args()

    lv = levels()
    jobs = [(key, family, args.runs, args.seed, args.replays) for key in (*W3, *CHATO) for family in families(key)]
    cells: list[Tagged] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for (key, family, *_), done in zip(jobs, pool.map(job, jobs), strict=True):
            cells += done
            print(f"{key} {family} done", flush=True)

    base = {base_key(src, c): c for src, c in cells if c.level == OFF.name}
    frontier = []
    for level in lv:
        mine = [(src, c) for src, c in cells if c.level == level.name]
        abuela = [(src, c) for src, c in mine if (c.dealer, c.price_class) in W3]
        chato = [(src, c) for src, c in mine if (c.dealer, c.price_class) in CHATO]
        w3_model = [c for src, c in abuela if src == "model:w3" and c.rule == "w3"]
        frontier.append(
            {
                "level": level.name,
                "knobs": {
                    "start_spread": level.start_spread,
                    "jump_share": level.jump_share,
                    "band_jump_share": level.band_jump_share,
                    "jump_max": level.jump_max,
                    "band_gap": level.band_gap,
                },
                "abuela": gate(abuela, base, args.share_bar),
                "chato": gate(chato, base, args.share_bar),
                "mean_share_ratio": round(mean(c.ratio(base[base_key(src, c)]) for src, c in abuela), 3),
                "hit_next": round(mean(c.predict.hit_rate for c in w3_model), 3),
                "hit_first": round(mean(c.predict.first_hit_rate for c in w3_model), 3),
                "hit_all": round(mean(c.predict.overall_hit_rate for c in w3_model), 3),
                "entropy_bits": round(mean(c.predict.step_entropy_bits for c in w3_model), 3),
            }
        )
    frontier.sort(key=lambda f: (not f["abuela"]["pass"], f["hit_all"]))
    detail = [
        {"source": src, "dealer": c.dealer, "class": c.price_class, "plan": c.plan, "level": c.level, "rule": c.rule,
         **c.summary.as_dict(), "lagged": c.lagged.as_dict(), "mean_bids": round(c.mean_bids, 2),
         "predict": c.predict.as_dict()}
        for src, c in cells
    ]  # fmt: skip
    if args.json:
        doc = {"frontier": frontier, **({"cells": detail} if args.cells else {})}
        args.json.write_text(json.dumps(doc, indent=1) + "\n")
    lines = render(frontier)
    if args.md:
        args.md.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:60]))


COLUMNS = (
    ("level", lambda f: f["level"]),
    ("spread", lambda f: f["knobs"]["start_spread"]),
    ("below", lambda f: f"{f['knobs']['jump_share']:g}"),
    ("band", lambda f: f"{f['knobs']['band_jump_share']:g}"),
    ("jump max", lambda f: f["knobs"]["jump_max"]),
    ("gap", lambda f: f["knobs"]["band_gap"]),
    ("Abuela gate", lambda f: "pass" if f["abuela"]["pass"] else "fail"),
    ("worst share ×", lambda f: f"{f['abuela']['share_ratio']:.3f}"),
    ("worst share × (2 ticks/round)", lambda f: f"{f['abuela']['lag_share_ratio']:.3f}"),
    ("worst deal Δ", lambda f: f"{min(f['abuela']['deal'], f['abuela']['lag_deal']):+.3f}"),
    ("worst fill-8 Δ (2 ticks/round)", lambda f: f"{f['abuela']['lag_fill']:+.3f}"),
    ("mean share ×", lambda f: f"{f['mean_share_ratio']:.3f}"),
    ("Chato worst share ×", lambda f: f"{min(f['chato']['share_ratio'], f['chato']['lag_share_ratio']):.3f}"),
    ("hit all", lambda f: f"{f['hit_all']:.3f}"),
    ("hit next", lambda f: f"{f['hit_next']:.3f}"),
    ("hit first", lambda f: f"{f['hit_first']:.3f}"),
    ("bits", lambda f: f"{f['entropy_bits']:.2f}"),
)


def render(frontier: list[dict[str, Any]]) -> list[str]:
    head = [name for name, _ in COLUMNS]
    out = [
        "# B12 tables (generated by `scripts/dealer_jitter.py`)",
        "",
        "Gate (Abuela common, uncommon, pack; W3's and today's plans; W3's fitted dealers, the step-capped ones",
        "and the real threads replayed at the top and bottom of their bracket; one round per tick and two):",
        "share ≥ 0.95 × the deterministic plan's in every cell (a missed deal scores 0; deal and fill changes shown).",
        "`hit` = a rival's exact-price hit rate on our bids (W3 plans, fitted dealers); today's ladder: 1.000.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    out += ["| " + " | ".join(str(get(f)) for _, get in COLUMNS) + " |" for f in frontier]
    return out


if __name__ == "__main__":
    main()
