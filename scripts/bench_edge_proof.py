"""Market Test proof: the free stall, our exact broker and the bench edge on the same seeded books.

    uv run python scripts/bench_edge_proof.py --seeds 1000 [--json out.json]

Every row runs `bazaar_sim.bench.simulate` (#77's realistic bench: staggered arrivals, firm and impatient traders,
relaxing quotes, the quote rule) once per seed and policy, on the same book:
  - stall: `bazaar_sim.bench.stall_policy`, what the free auto stall and the kit's starter broker do;
  - exact: `matcher.plan_matches`, the broker's matching today (`BAZAAR_BENCH_POLICY` unset or `exact`);
  - edge: `bench_edge.edge_plan`, the broker's matching with `BAZAAR_BENCH_POLICY=edge` (PR #84's edge, ported, behind
    its guard: the exact plan unless the edge's pairs beat it by `EdgeConfig.guard_margin` estimated primas);
  - unguarded: the same edge with no guard (PR #84 as it was), to show what the guard costs and saves.

Variants (each an unverified assumption, so both sides of it are run): `default` (the preset as #77 draws it),
`x2` (twice the traders: the real bench's ids suggest ten a side), `tick0` (the whole book at once), `shade2` (quotes
twice as far from the limits), `firm` (nobody relaxes). Per row: mean and p50 efficiency, the share of books where the
policy realises less / more than the stall, the worst book (policy − stall), and the mean session points against two
stall-level rivals (`BenchResult.points`, 0.5 at the stall).
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

from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, edge_plan, expiries_in  # noqa: E402
from bazaar_agent.agents.bench_model import PRIORS  # noqa: E402
from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from  # noqa: E402
from bazaar_sim import bench  # noqa: E402

MAX_SENDS = 15  # BrokerConfig.max_matches_per_tick
POLICIES = ("stall", "exact", "edge", "unguarded")
VARIANTS: dict[str, Callable[[bench.BenchPreset], bench.BenchPreset]] = {
    "default": lambda p: p,
    "x2": lambda p: replace(p, traders=2 * p.traders),
    "tick0": lambda p: p.variant(spread=0),
    "shade2": lambda p: p.variant(shade=2.0),
    "firm": lambda p: p.variant(relax=(0.0, 0.0)),
}

Pair = tuple[str, str, int]


def exact_policy(book: dict[str, Any]) -> list[Pair]:
    parsed = BrokerBook.model_validate(book)
    fee = Fee(parsed.fee_bps, parsed.fee_per_card)
    return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan_matches(quotes_from(parsed).quotes, fee, MAX_SENDS)]


class EdgePolicy:
    """The broker's edge path on the simulator's book: one `BenchEdge` per session, as one bench run in the broker."""

    def __init__(self, preset: str, config: EdgeConfig | None = None) -> None:
        self.edge = BenchEdge(PRIORS[preset], config or EdgeConfig())

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        parsed = BrokerBook.model_validate(book)
        quotes = [q for q in quotes_from(parsed).quotes if q.bench]
        tick = int(book.get("tick") or 0)
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        plan = edge_plan(self.edge, quotes, fee, tick, MAX_SENDS, expiries_in(parsed.bench_offers, tick))
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan.matches]


def _policy(name: str, preset: str) -> Callable[[dict[str, Any]], list[Pair]]:
    if name == "stall":
        return bench.stall_policy
    if name == "exact":
        return exact_policy
    if name == "unguarded":
        return EdgePolicy(preset, EdgeConfig(guard_margin=float("-inf")))
    return EdgePolicy(preset)


def one_book(args: tuple[str, str, int]) -> dict[str, Any]:
    """Every policy on one seeded book: (efficiency, stall, points, refused) per policy."""
    preset_name, variant, seed = args
    p = VARIANTS[variant](bench.preset(preset_name))
    traders = bench.make_book(p, seed)
    out: dict[str, Any] = {}
    for name in POLICIES:
        r = bench.simulate(_policy(name, preset_name), p, seed, traders=traders)
        out[name] = (r.efficiency, r.stall, r.points(), sum(r.refused.values()), r.realised, r.stall_realised)
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
    points: float
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
                sum(b[name][3] for b in books),
            )
        )
    return rows


def markdown(rows: list[Row]) -> str:
    cols = ("preset", "variant", "policy", "books", "mean", "p50", "stall mean", "< stall", "> stall", "worst Δ")
    lines = ["| " + " | ".join((*cols, "points", "refused")) + " |", "|" + "---|" * 12]
    for r in rows:
        lines.append(
            f"| {r.preset} | {r.variant} | {r.policy} | {r.books} | {r.mean:.4f} | {r.p50:.4f} | {r.stall_mean:.4f} | "
            f"{r.below_stall:.1%} | {r.above_stall:.1%} | {r.worst:+.4f} | {r.points:.3f} | {r.refused} |"
        )
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
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in rows], indent=1))


if __name__ == "__main__":
    main()
