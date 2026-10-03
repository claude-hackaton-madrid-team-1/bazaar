"""Market Test proof: the free stall, our exact broker and the bench edge on the same seeded books.

    uv run python scripts/bench_edge_proof.py --seeds 1000 [--json out.json]

Every row runs `bazaar_sim.bench.simulate` (#77's realistic bench: staggered arrivals, firm and impatient traders,
relaxing quotes, the quote rule) once per seed and policy, on the same book:
  - stall: `bazaar_sim.bench.stall_policy`, what the free auto stall and the kit's starter broker do;
  - exact: `matcher.plan_matches`, the broker's matching today (`BAZAAR_BENCH_POLICY` unset or `exact`);
  - edge: `bench_edge.edge_plan`, the broker's matching with `BAZAAR_BENCH_POLICY=edge` (PR #84's edge, ported, behind
    its guard: the exact plan unless the edge's pairs beat it by BAZAAR_BENCH_GUARD_MARGIN, default 10, estimated P);
  - edge20 / edge5: the same with a guard margin of 20 / 5 P;
  - unguarded: BAZAAR_BENCH_GUARD_MARGIN=none, PR #84 as it was.

Variants (each an unverified assumption, so both sides of it are run): `default` (the preset as #77 draws it),
`x2` (twice the traders: the real bench's ids suggest ten a side, and its stall mean, 0.879, is the real one, 0.878 over
three sessions), `tick0` (the whole book at once), `shade2` (quotes twice as far from the limits), `firm` (nobody
relaxes), and two where the edge's priors are WRONG: `narrow` and `x2narrow` (quotes half as far from the limits as the
edge believes, so it overestimates every gain). Per row: mean and p50 efficiency, the share of books where the
policy realises less / more than the stall, the worst book (policy − stall), and the mean session points under four
readings of RULES.md ("matching as well as the free auto stall earns half the bench points; the full points go to the
mean of the top three"; the curve below the stall is unpublished):
  - points: #77's reading, the field at the stall (`BenchResult.points`): 0.5 at the stall, 1.0 above it (we are then
    above the top-three mean), 0.5 × efficiency / stall below it;
  - rivals: two rival venues 0.03 above the stall, so a small win earns part of the second half only;
  - steep: as `points` above the stall, but below it the half falls to 0 at 0.10 under the stall;
  - harsh: both at once and worse: rivals 0.10 above the stall, and 0 points at 0.05 under it.
The last table is each policy's regret (the best policy's points in a cell minus its own), worst and mean over every
preset, variant and reading: the policy to pick when the reading is unknown is the one whose worst regret is smallest.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from multiprocessing import Pool
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bazaar_agent.agents.bench_edge import DEFAULT_GUARD_MARGIN, BenchEdge, edge_plan, expiries_in  # noqa: E402
from bazaar_agent.agents.bench_model import PRIORS  # noqa: E402
from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from  # noqa: E402
from bazaar_sim import bench  # noqa: E402

MAX_SENDS = 15  # BrokerConfig.max_matches_per_tick
# policy -> its guard margin (None: not the edge)
POLICIES: dict[str, float | None] = {
    "stall": None,
    "exact": None,
    "edge20": 20.0,
    "edge": DEFAULT_GUARD_MARGIN,
    "edge5": 5.0,
    "unguarded": float("-inf"),
}
RIVALS_ABOVE = 0.03  # the `rivals` reading: two rival venues this far above the stall
STEEP_ZERO = 0.10  # the `steep` reading: no points this far below the stall
HARSH = (0.10, 0.05)  # the `harsh` reading: rivals this far above, no points this far below
READINGS = ("points", "pts_rivals", "pts_steep", "pts_harsh")
VARIANTS: dict[str, Callable[[bench.BenchPreset], bench.BenchPreset]] = {
    "default": lambda p: p,
    "x2": lambda p: replace(p, traders=2 * p.traders),
    "tick0": lambda p: p.variant(spread=0),
    "shade2": lambda p: p.variant(shade=2.0),
    "firm": lambda p: p.variant(relax=(0.0, 0.0)),
    "narrow": lambda p: p.variant(shade=0.5),
    "x2narrow": lambda p: replace(p, traders=2 * p.traders).variant(shade=0.5),
}

Pair = tuple[str, str, int]


def exact_policy(book: dict[str, Any]) -> list[Pair]:
    parsed = BrokerBook.model_validate(book)
    fee = Fee(parsed.fee_bps, parsed.fee_per_card)
    return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan_matches(quotes_from(parsed).quotes, fee, MAX_SENDS)]


class EdgePolicy:
    """The broker's edge path on the simulator's book: one `BenchEdge` per session, as one bench run in the broker."""

    def __init__(self, preset: str, margin: float = DEFAULT_GUARD_MARGIN) -> None:
        self.edge = BenchEdge(PRIORS[preset])
        self.margin = margin

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        parsed = BrokerBook.model_validate(book)
        quotes = [q for q in quotes_from(parsed).quotes if q.bench]
        tick = int(book.get("tick") or 0)
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        plan = edge_plan(self.edge, quotes, fee, tick, MAX_SENDS, expiries_in(parsed.bench_offers, tick), self.margin)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan.matches]


def _policy(name: str, preset: str) -> Callable[[dict[str, Any]], list[Pair]]:
    if name == "stall":
        return bench.stall_policy
    if name == "exact":
        return exact_policy
    margin = POLICIES[name]
    return EdgePolicy(preset, DEFAULT_GUARD_MARGIN if margin is None else margin)


def steep_points(efficiency: float, stall: float, above: float, zero: float = STEEP_ZERO) -> float:
    """The `steep` reading: `above` (the points the rivals give) at or above the stall, falling to 0 at `zero` under
    it."""
    if efficiency >= stall:
        return above
    return round(0.5 * max(0.0, 1 - (stall - efficiency) / zero), 4)


def one_book(args: tuple[str, str, int]) -> dict[str, Any]:
    """Every policy on one seeded book: (efficiency, stall, points, refused, realised, stall's realised, rivals points,
    steep points) per policy."""
    preset_name, variant, seed = args
    p = VARIANTS[variant](bench.preset(preset_name))
    traders = bench.make_book(p, seed)
    out: dict[str, Any] = {}
    for name in POLICIES:
        r = bench.simulate(_policy(name, preset_name), p, seed, traders=traders)
        rivals = bench.session_points(r.efficiency, r.stall, [min(1.0, r.stall + RIVALS_ABOVE)] * 2)
        strong = bench.session_points(r.efficiency, r.stall, [min(1.0, r.stall + HARSH[0])] * 2)
        out[name] = (
            r.efficiency,
            r.stall,
            r.points(),
            sum(r.refused.values()),
            r.realised,
            r.stall_realised,
            rivals,
            steep_points(r.efficiency, r.stall, r.points()),
            steep_points(r.efficiency, r.stall, strong, HARSH[1]),
        )
    return out


@dataclass(frozen=True)
class Row:
    preset: str
    variant: str
    policy: str
    books: int
    mean: float
    p50: float
    stall_mean: float
    below_stall: float  # share of books realising less than the stall
    above_stall: float
    worst: float  # min (efficiency − stall) over the books
    points: float  # mean session points, field at the stall (#77's reading)
    pts_rivals: float  # two rivals RIVALS_ABOVE over the stall
    pts_steep: float  # no points STEEP_ZERO under the stall
    pts_harsh: float  # rivals HARSH[0] over the stall and no points HARSH[1] under it
    refused: int


def rows_for(preset: str, variant: str, seeds: int, pool: Any) -> list[Row]:
    books = pool.map(one_book, [(preset, variant, s) for s in range(seeds)], chunksize=50)
    rows = []
    for name in POLICIES:
        eff = [b[name][0] for b in books]
        delta = [b[name][4] - b[name][5] for b in books]  # realised − stall's realised, in primas: no rounding
        gap = [b[name][0] - b[name][1] for b in books]
        rows.append(
            Row(
                preset,
                variant,
                name,
                len(books),
                round(statistics.fmean(eff), 4),
                round(statistics.median(eff), 4),
                round(statistics.fmean(b[name][1] for b in books), 4),
                round(sum(d < 0 for d in delta) / len(books), 4),
                round(sum(d > 0 for d in delta) / len(books), 4),
                round(min(gap), 4),
                round(statistics.fmean(b[name][2] for b in books), 4),
                round(statistics.fmean(b[name][6] for b in books), 4),
                round(statistics.fmean(b[name][7] for b in books), 4),
                round(statistics.fmean(b[name][8] for b in books), 4),
                sum(b[name][3] for b in books),
            )
        )
    return rows


def markdown(rows: list[Row]) -> str:
    cols = ("preset", "variant", "policy", "books", "mean", "p50", "stall mean", "< stall", "> stall", "worst Δ")
    lines = ["| " + " | ".join((*cols, "points", "rivals", "steep", "harsh", "refused")) + " |", "|" + "---|" * 15]
    for r in rows:
        lines.append(
            f"| {r.preset} | {r.variant} | {r.policy} | {r.books} | {r.mean:.4f} | {r.p50:.4f} | {r.stall_mean:.4f} | "
            f"{r.below_stall:.1%} | {r.above_stall:.1%} | {r.worst:+.4f} | {r.points:.3f} | {r.pts_rivals:.3f} | "
            f"{r.pts_steep:.3f} | {r.pts_harsh:.3f} | {r.refused} |"
        )
    return "\n".join(lines)


def regrets(rows: list[Row]) -> dict[str, tuple[float, float, float]]:
    """Policy -> (worst regret, mean regret, mean points) over every (preset, variant, reading) cell."""
    cells: dict[tuple[str, str, str], dict[str, float]] = {}
    for r in rows:
        for reading in READINGS:
            cells.setdefault((r.preset, r.variant, reading), {})[r.policy] = getattr(r, reading)
    out = {}
    for policy in POLICIES:
        regret = [max(cell.values()) - cell[policy] for cell in cells.values() if policy in cell]
        points = [cell[policy] for cell in cells.values() if policy in cell]
        out[policy] = (round(max(regret), 4), round(statistics.fmean(regret), 4), round(statistics.fmean(points), 4))
    return out


def regret_markdown(rows: list[Row]) -> str:
    lines = ["| policy | worst regret | mean regret | mean points |", "|---|---|---|---|"]
    for policy, (worst, mean, points) in sorted(regrets(rows).items(), key=lambda kv: kv[1][0]):
        lines.append(f"| {policy} | {worst:.3f} | {mean:.3f} | {points:.3f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=1000)
    ap.add_argument("--presets", default="normal,hard")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows: list[Row] = []
    with Pool() as pool:
        for preset in args.presets.split(","):
            for variant in args.variants.split(","):
                rows += rows_for(preset, variant, args.seeds, pool)
                print(markdown([r for r in rows if r.preset == preset and r.variant == variant]), flush=True)
    print(markdown(rows))
    print(regret_markdown(rows))
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in rows], indent=1))


if __name__ == "__main__":
    main()
