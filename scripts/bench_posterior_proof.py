"""Proof for BAZAAR_BENCH_POLICY=lookahead_safe / lookahead_bold (`agents/bench_posterior.py`) against the stall,
#292's `lookahead` and `exact` on the calibrated tournament worlds.

    uv run python scripts/bench_posterior_proof.py [--seeds 300] [--samples 96] [--robust] [--worlds a,b]

Runs `scripts/bench_tournament.py`'s worlds (fitted to sessions 7 and 8's recorded books) with the same books for
every policy and scores each session with the real points rule (alone above the stall: 1.0; at it: 0.5; under it:
0.5 x E/Es). Columns: mean efficiency margin over the stall, P(above), P(below), the worst margin and E[points];
`E0[points]` is the same under a rule that gives nothing below the stall (the downside if Saturday's fit is wrong).
The real-book replay (posterior draws behind sessions 7 and 8's quote paths) is
`bench_tournament.py --plugin scripts/bench_posterior_proof.py --replay b120 b137` (DATABASE_URL, read-only).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections.abc import Sequence
from multiprocessing import Pool
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bench_tournament as bt  # noqa: E402

from bazaar_agent.agents.bench_posterior import PosteriorPolicy, PosteriorPrior  # noqa: E402

SAMPLES = 96
POLICIES = {
    "lookahead_safe": lambda traders: PosteriorPolicy(
        PosteriorPrior(side_traders=len(traders) // 2), SAMPLES, below=0.0
    ),
    "lookahead_bold": lambda traders: PosteriorPolicy(
        PosteriorPrior(side_traders=len(traders) // 2), SAMPLES, below=0.5
    ),
}
NAMES = ("stall", "exact", "lookahead", "lookahead_safe", "lookahead_bold")  # lookahead: #292's


class Row(NamedTuple):  # not a dataclass: bench_tournament.py --plugin loads this file outside sys.modules
    world: str
    policy: str
    books: int
    margin: float
    above: float
    below: float
    worst: float
    points: float
    points0: float  # nothing below the stall


def _book(args: tuple[str, int, bool, int]) -> dict[str, tuple[float, float]]:
    world, seed, robust, samples = args
    global SAMPLES
    SAMPLES = samples
    bt.POLICIES.update(POLICIES)
    w = (bt.robust_worlds() if robust else bt.WORLDS)[world]
    return bt.run_book(w.book(seed), w.preset, NAMES)


def summarise(world: str, results: Sequence[dict[str, tuple[float, float]]]) -> list[Row]:
    out = []
    for name in NAMES:
        rs = [r[name] for r in results]
        d = [e - s for e, s in rs]
        above = sum(x > bt.EPS for x in d) / len(d)
        below = sum(x < -bt.EPS for x in d) / len(d)
        out.append(
            Row(
                world,
                name,
                len(rs),
                round(statistics.mean(d), 4),
                round(above, 3),
                round(below, 3),
                round(min(d), 3),
                round(statistics.mean(bt.real_points(e, s) for e, s in rs), 3),
                round(above + 0.5 * (1 - above - below), 3),
            )
        )
    return out


def run(
    seeds: int = 300,
    samples: int = SAMPLES,
    worlds: Sequence[str] = ("cal_normal20", "cal_hard24", "cal_normal20_uniform"),
    robust: bool = False,
    processes: int | None = None,
) -> list[Row]:
    rows: list[Row] = []
    for world in worlds:
        jobs = [(world, seed, robust, samples) for seed in range(seeds)]
        if processes == 1:
            results = [_book(j) for j in jobs]
        else:
            with Pool(processes) as pool:
                results = pool.map(_book, jobs)
        rows += summarise(world, results)
    return rows


def markdown(rows: Sequence[Row]) -> str:
    out = [
        "| world | policy | books | margin | P(above) | P(below) | worst | E[points] | E0[points] |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| {r.world} | {r.policy} | {r.books} | {r.margin:+.4f} | {r.above:.3f} | {r.below:.3f} | {r.worst:+.3f} | "
        f"{r.points:.3f} | {r.points0:.3f} |"
        for r in rows
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--robust", action="store_true", help="the main world with each parameter at +/-50 %%")
    ap.add_argument("--worlds", help="comma-separated tournament worlds")
    args = ap.parse_args()
    worlds = args.worlds.split(",") if args.worlds else ["cal_normal20", "cal_hard24", "cal_normal20_uniform"]
    print(markdown(run(args.seeds, args.samples, worlds)), flush=True)
    if args.robust:
        print("\n" + markdown(run(args.seeds, args.samples, list(bt.robust_worlds()), robust=True)))


if __name__ == "__main__":
    main()
