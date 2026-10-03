"""Our bench policies on the simulator's Market Test (`bazaar_sim.bench`, W1a's PR #77 on top of #55).

    uv run python -m bazaar_agent.evals.bench_w1a --seeds 1000      # needs bazaar_sim (#55 + #77 merged)

The same policies as `evals/bench.py`, on a bench someone else wrote: W1a's presets, its stall, its oracle, its
refusal rules and its session points (0.5 at the stall, 1.0 at the top-three mean). A policy follows W1a's contract:
`policy(book) -> [(sell, buy, price)]`, called `reads_per_tick` times a tick on the book its earlier matches left.
It hears no answers, so the edge reads them off the next book: a pair still in it was refused, a pair gone from it
was matched.

Points are against a field of two stall-level venues (rivals running the kit's starter broker or the free stall):
any session above the stall earns the full point, a tie 0.5, below the stall less. So the table reports the share
of sessions won, tied and lost against the stall next to the efficiency.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, expiries_in
from bazaar_agent.agents.bench_model import PRIORS
from bazaar_agent.agents.matcher import BrokerBook, Fee, Match, plan_matches, quotes_from
from bazaar_agent.evals.bench import MAX_SENDS, greedy_plan

POLICIES = ("stall", "greedy", "exact", "edge", "edge_limit")
READS = {"edge_limit": 3}  # book reads per tick; 1 for the others
# W1a's sensitivity corners: (arrival spread, shade ×, relax); None keeps the preset's value.
VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "shade2": {"shade": 2.0},
    "shade2_firm": {"shade": 2.0, "relax": (0.0, 0.0)},
    "tick0": {"spread": 0},
    "tick0_shade2_firm": {"spread": 0, "shade": 2.0, "relax": (0.0, 0.0)},
}

Pair = tuple[str, str, int]


def _bench() -> Any:
    return importlib.import_module("bazaar_sim.bench")


@dataclass
class BookPolicy:
    """One of our policies as a W1a policy: a callable on the broker book, fresh for every session."""

    name: str
    preset: str
    edge: BenchEdge = field(init=False)
    pending: list[Match] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.edge = BenchEdge(PRIORS[self.preset], EdgeConfig(cross="limit" if self.name == "edge_limit" else "quote"))

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        if self.name == "stall":
            return list(_bench().stall_policy(book))
        if self.name == "greedy":
            return greedy_plan(book)
        parsed = BrokerBook.model_validate(book)
        quotes = quotes_from(parsed).quotes
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        if self.name == "exact":
            return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan_matches(quotes, fee, MAX_SENDS)]
        tick = int(book.get("tick") or 0)
        self._answers({str(q.id) for q in quotes})
        self.edge.observe(quotes, tick, expiries_in(parsed.bench_offers, tick))
        self.pending = self.edge.plan(quotes, fee, tick, limit=MAX_SENDS)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in self.pending]

    def _answers(self, in_book: set[str]) -> None:
        """What became of the last plan: both offers still here = refused, both gone = matched, else unknown."""
        for m in self.pending:
            here = (str(m.sell.id) in in_book, str(m.buy.id) in in_book)
            if all(here):
                self.edge.note_sent(m, accepted=False)
            elif not any(here):
                self.edge.note_sent(m, accepted=True)
        self.pending = []


@dataclass
class W1aRow:
    preset: str
    rule: str
    variant: str
    policy: str
    seeds: int
    p10: float
    p50: float
    mean: float
    stall_mean: float
    oracle_mean: float
    points: float  # mean session points against two stall-level rivals
    won: float  # share of sessions strictly above the stall
    tied: float
    lost: float
    refused: int
    max_requests_per_tick: int


def run(
    seeds: int,
    presets: Sequence[str] = ("normal", "hard"),
    rules: Sequence[str] = ("quote", "limit"),
    variants: Sequence[str] = ("default",),
    names: Sequence[str] = POLICIES,
) -> list[W1aRow]:
    bench = _bench()
    rows = []
    for preset_name in presets:
        for variant in variants:
            preset = bench.preset(preset_name).variant(**VARIANTS[variant])
            for rule in rules:
                for name in names:
                    results = [
                        bench.simulate(
                            BookPolicy(name, preset_name), preset, seed, rule=rule, reads_per_tick=READS.get(name, 1)
                        )
                        for seed in range(seeds)
                    ]
                    rows.append(_row(preset_name, rule, variant, name, results))
    return rows


def _row(preset: str, rule: str, variant: str, name: str, results: Sequence[Any]) -> W1aRow:
    eff = sorted(r.efficiency for r in results)
    n = len(results)
    won = sum(r.efficiency > r.stall + 1e-9 for r in results) / n
    lost = sum(r.efficiency < r.stall - 1e-9 for r in results) / n
    return W1aRow(
        preset,
        rule,
        variant,
        name,
        n,
        round(eff[n // 10], 3),
        round(statistics.median(eff), 3),
        round(statistics.fmean(eff), 3),
        round(statistics.fmean(r.stall for r in results), 3),
        round(statistics.fmean(r.oracle for r in results), 3),
        round(statistics.fmean(r.points() for r in results), 3),
        round(won, 3),
        round(1 - won - lost, 3),
        round(lost, 3),
        sum(sum(r.refused.values()) for r in results),
        max(r.max_requests_per_tick for r in results),
    )


def markdown(rows: Sequence[W1aRow]) -> str:
    head = (
        "| preset | rule | variant | policy | p10 | p50 | mean | stall mean | oracle mean | points | won | tied | lost "
        "| refused | max req/tick |"
    )
    lines = [head, "|" + "---|" * 15]
    for r in rows:
        lines.append(
            f"| {r.preset} | {r.rule} | {r.variant} | {r.policy} | {r.p10:.3f} | {r.p50:.3f} | {r.mean:.3f} | "
            f"{r.stall_mean:.3f} | {r.oracle_mean:.3f} | {r.points:.3f} | {r.won:.0%} | {r.tied:.0%} | {r.lost:.0%} | "
            f"{r.refused} | {r.max_requests_per_tick} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=1000)
    parser.add_argument("--presets", nargs="+", default=["normal", "hard"])
    parser.add_argument("--rules", nargs="+", default=["quote", "limit"])
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS))
    parser.add_argument("--policies", nargs="+", default=list(POLICIES))
    parser.add_argument("--json", help="also write the rows to this file")
    args = parser.parse_args(argv)
    rows = run(args.seeds, args.presets, args.rules, args.variants, args.policies)
    print(markdown(rows))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in rows], handle, indent=2)


if __name__ == "__main__":
    main()
