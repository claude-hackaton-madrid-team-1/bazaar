"""B1's tournament: the win-rate policy against the free stall on W1a's Market Test bench, under both readings of
the points curve below the stall, on the cell its prior assumes and on cells where that prior is wrong.

Needs W1a's bench (PR #77), which is not on this branch's base:
    git checkout origin/night/w1a-bench-sim -- src/bazaar_sim/bench.py src/bazaar_sim/models.py
    uv run python scripts/b1_bench_tournament.py --books 300
(then `git checkout -- src/bazaar_sim` to drop them again). Prints one row per cell and mode.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from bazaar_agent.agents.bench_winrate import HARD_PRIOR, NORMAL_PRIOR, WinRatePolicy

try:
    from bazaar_sim import bench
except ImportError:  # pragma: no cover - a usage error, not a code path
    sys.exit("needs bazaar_sim.bench from PR #77: see this script's docstring")


def points(r: bench.BenchResult, zero_below: bool) -> float:
    """`BenchResult.points()` against two stall-level rivals; with `zero_below`, a loss to the stall scores 0."""
    return 0.0 if zero_below and r.realised < r.stall_realised else r.points()


def cells() -> list[tuple[str, bench.BenchPreset, object]]:
    n, h = bench.NORMAL, bench.HARD
    return [
        ("normal (prior right)", n, NORMAL_PRIOR),
        ("hard (prior right)", h, HARD_PRIOR),
        ("normal, shades 1.5x", n.variant(shade=1.5), NORMAL_PRIOR),
        ("normal, shades 2x", n.variant(shade=2.0), NORMAL_PRIOR),
        ("normal, relax 0.2-0.5", n.variant(relax=(0.2, 0.5)), NORMAL_PRIOR),
        ("normal, all at tick 0", n.variant(spread=0), NORMAL_PRIOR),
        ("hard, shades 2x", h.variant(shade=2.0), HARD_PRIOR),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--books", type=int, default=300)
    ap.add_argument("--first-seed", type=int, default=0)
    ap.add_argument("--modes", default="zero,linear", help="loss_curve values: zero (cautious), linear (aggressive)")
    args = ap.parse_args()
    print(
        "cell | mode | win / tie / loss vs stall | points vs 2 stall-level rivals (linear / zero below stall) | s/book"
    )
    for label, preset, prior in cells():
        for mode in args.modes.split(","):
            wins = ties = losses = 0
            lin, zero = [], []
            start = time.time()
            for seed in range(args.first_seed, args.first_seed + args.books):
                policy = WinRatePolicy(prior, seed=seed, loss_curve=mode)  # type: ignore[arg-type]
                policy.begin(1, 0)  # bench.started: simulate() runs bench run 1 from tick 0
                r = bench.simulate(policy, preset, seed, rule="quote")
                wins += r.realised > r.stall_realised
                ties += r.realised == r.stall_realised
                losses += r.realised < r.stall_realised
                lin.append(points(r, False))
                zero.append(points(r, True))
            n = args.books
            print(
                f"{label} | {mode} | {wins / n:.1%} / {ties / n:.1%} / {losses / n:.1%} | "
                f"{statistics.fmean(lin):.3f} / {statistics.fmean(zero):.3f} | {(time.time() - start) / n:.2f}",
                flush=True,
            )


if __name__ == "__main__":
    main()
