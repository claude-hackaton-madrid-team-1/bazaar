"""Saturday's eight Market Tests, simulated: what opening our venue (and when) is worth in points. Offline.

    uv run python -m bazaar_agent.evals.saturday --days 500          # W1a's bench if bazaar_sim is there, else ours

**The day** (tests/fixtures/api/get_api_schedule.anon.json, Saturday opens at h4 = 09:00, game hour = wall hour):
bench sessions at h5, h7, h9, h11, h13, h15, h17 (10 traders) and h16 (the hard test, 12 traders).

**Plans.** `never`: the free stall all day. `11:30`: #71's venue keeper opens our board venue at game hour 6.5;
`09:00`: the same at 4.05 (a guardrail change). The stall is replaced on the spot (RULES.md), so every session after
the opening is matched by our broker: `exact` (#71's keeper today, ties like the stall), `edge`, or `edge+probe`
(the limit probe, its statistics kept across the day as one broker process keeps them).

**Points.** A session's share of the bench points is W1a's reading of RULES.md (`session_points`): 0.5 at the stall,
1.0 at the top-three mean, linear in between, 0.5 × eff/stall below. The rival field is two venues at the stall
(`stall` field: the kit's starter broker or the free stall), one at the clairvoyant oracle and one at the stall
(`strong`), or three at the oracle (`top3`: we are never in the top three unless we match the oracle; the
pessimistic bound, since with only two rivals any session above the stall earns the full point). A board venue
whose broker is down matches nothing in that session (efficiency 0): `--down` is the chance per session.
Round points = 15 × the mean session share (W5's split of market-making's 30: 15 bench, 15 venue; the
venue's organic half is B1's), final points = 0.40 × round points (W5: one Saturday round point is 0.40 final).

Everything above except the schedule is an assumption; every one is a parameter.
"""

from __future__ import annotations

import argparse
import importlib
import json
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from bazaar_agent.agents.bench_edge import ProbeStats
from bazaar_agent.evals import bench as own

SESSIONS: tuple[tuple[float, str], ...] = (
    (5.0, "normal"),
    (7.0, "normal"),
    (9.0, "normal"),
    (11.0, "normal"),
    (13.0, "normal"),
    (15.0, "normal"),
    (16.0, "hard"),
    (17.0, "normal"),
)
# Game hour the venue opens: #71's keeper opens at `venue_open_after_game_hours` = 6.5 (11:30); 09:00 would need
# that value at 4.05 (the first tick after the 150 P grant, the earliest the cash floor allows).
PLANS: dict[str, float | None] = {"09:00": 4.05, "11:30": 6.5, "never": None}
BENCH_ROUND_POINTS = 15.0  # W5: market-making 30 = 15 bench + 15 venue (assumed split)
FINAL_PER_ROUND_POINT = 0.40  # W5: one Saturday round point in final game points
# Bench worlds: (W1a variant, match rule). The default cell is the best guess; the others are its unknowns.
WORLDS: dict[str, tuple[str, str]] = {
    "default/quote": ("default", "quote"),
    "default/limit": ("default", "limit"),
    "shade2/limit": ("shade2", "limit"),
    "tick0/quote": ("tick0", "quote"),
    "tick0_shade2_firm/quote": ("tick0_shade2_firm", "quote"),
    "tick0_shade2_firm/limit": ("tick0_shade2_firm", "limit"),
}
Policy = Literal["exact", "edge", "edge+probe"]
Field = Literal["stall", "strong", "top3"]
TOURNAMENT_NAME: dict[str, str] = {"exact": "exact", "edge": "edge", "edge+probe": "edge_limit"}


def bench_points(efficiency: float, stall: float, top3: float) -> float:
    """W1a's reading of RULES.md (#77 `bazaar_sim.bench.bench_points`), copied so this runs without bazaar_sim."""
    if efficiency <= stall:
        return round(0.5 * efficiency / stall, 4) if stall > 0 else 0.5
    if top3 <= stall:
        return 1.0
    return round(min(1.0, 0.5 + 0.5 * (efficiency - stall) / (top3 - stall)), 4)


def session_points(ours: float, stall: float, rivals: Sequence[float]) -> float:
    top = sorted([ours, *rivals], reverse=True)[:3]
    return bench_points(ours, stall, sum(top) / len(top))


@dataclass(frozen=True)
class Session:
    """One Market Test as a venue sees it: our efficiency, the stall's and the oracle's on the same book."""

    ours: float
    stall: float
    oracle: float


Runner = Callable[[str, str, str, int, Policy, ProbeStats], Session]  # (preset, variant, rule, seed, policy, probes)


def w1a_runner() -> Runner:
    """Sessions on W1a's bench (`bazaar_sim.bench`, #77) through the W1b adapter."""
    bench = importlib.import_module("bazaar_sim.bench")
    from bazaar_agent.evals.bench_w1a import READS, VARIANTS, BookPolicy

    def run(preset: str, variant: str, rule: str, seed: int, policy: Policy, probes: ProbeStats) -> Session:
        name = TOURNAMENT_NAME[policy]
        p = bench.preset(preset).variant(**VARIANTS[variant])
        policy_ = BookPolicy(name, preset, None, probes)
        r = bench.simulate(policy_, p, seed, rule=rule, reads_per_tick=READS.get(name, 1))
        return Session(r.efficiency, r.stall, r.oracle)

    return run


# Our own bench's closest scenario to each W1a variant (arrivals spread, #55 shading, relax to 25 %).
OWN_VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "shade2": {"seller_markup": (1.10, 1.60), "buyer_shade": (0.50, 0.90)},
    "tick0": {"arrivals": "front"},
    "tick0_shade2_firm": {
        "arrivals": "front",
        "seller_markup": (1.10, 1.60),
        "buyer_shade": (0.50, 0.90),
        "firm_share": 1.0,
    },
}


def own_runner() -> Runner:
    """Sessions on our in-process bench (`evals/bench.py`), when bazaar_sim is not installed."""

    def run(preset: str, variant: str, rule: str, seed: int, policy: Policy, probes: ProbeStats) -> Session:
        spec = replace(own.PRESETS[preset], cross=rule, **OWN_VARIANTS[variant])  # type: ignore[arg-type]
        traders = own.draw_traders(spec, random.Random(f"sat:{preset}:{variant}:{seed}"))
        edge = own._shared(own.policies()[TOURNAMENT_NAME[policy]](preset), probes)
        best = own.possible_gains(traders) or 1
        ours = own.play(spec, traders, edge).efficiency
        stall = own.play(spec, traders, own.Stall()).efficiency
        return Session(ours, stall, own.oracle(traders, spec, rule) / best)  # type: ignore[arg-type]

    return run


def default_runner() -> tuple[str, Runner]:
    try:
        return "W1a bench (#77)", w1a_runner()
    except ModuleNotFoundError:
        return "W1b in-process bench", own_runner()


@dataclass
class DayRow:
    world: str
    field: str
    policy: str
    plan: str
    days: int
    final_mean: float  # final game points from the bench on Saturday
    final_p10: float
    final_p90: float
    delta_mean: float  # vs `never` (the free stall all day), final points
    delta_p10: float
    p_worse: float  # share of days the plan scores below `never`
    sessions_ours: int  # sessions matched by our broker under this plan


def _final(shares: Sequence[float]) -> float:
    return BENCH_ROUND_POINTS * statistics.fmean(shares) * FINAL_PER_ROUND_POINT


def simulate_days(
    days: int,
    worlds: Sequence[str] = tuple(WORLDS),
    fields: Sequence[Field] = ("stall", "strong", "top3"),
    policies: Sequence[Policy] = ("exact", "edge", "edge+probe"),
    down: float = 0.0,
    runner: Runner | None = None,
    seed: int = 2026,
) -> list[DayRow]:
    """Every plan on the same `days` Saturdays (same books, same broker outages) per world, policy and field.

    A plan's broker runs only the sessions after its opening and not down, and only those teach its probe: each
    plan has its own broker process (`ProbeStats`) per day. A session before the opening is the free stall's (0.5
    of the points whatever the field); a session whose broker is down realises nothing (0)."""
    run = runner or default_runner()[1]
    rows: list[DayRow] = []
    for world in worlds:
        variant, rule = WORLDS[world]
        for policy in policies:
            rng = random.Random(f"{seed}:{world}:{policy}:down")
            downs = [[rng.random() < down for _ in SESSIONS] for _ in range(days)]
            shares: dict[str, dict[Field, list[list[float]]]] = {p: {f: [] for f in fields} for p in PLANS}
            for plan, opens in PLANS.items():
                for day in range(days):
                    probes = ProbeStats()  # this plan's broker process for the day
                    day_shares: dict[Field, list[float]] = {f: [] for f in fields}
                    for k, (hour, preset) in enumerate(SESSIONS):
                        ours = opens is not None and opens < hour
                        session = None
                        if ours and not downs[day][k]:
                            session = run(preset, variant, rule, seed * 100_000 + day * 10 + k, policy, probes)
                        for field in fields:
                            day_shares[field].append(_share(session, ours, field))
                    for field in fields:
                        shares[plan][field].append(day_shares[field])
            for field in fields:
                never = [_final(d) for d in shares["never"][field]]
                for plan, opens in PLANS.items():
                    values = [_final(d) for d in shares[plan][field]]
                    deltas = [v - n for v, n in zip(values, never, strict=True)]
                    rows.append(
                        DayRow(
                            world,
                            field,
                            policy,
                            plan,
                            days,
                            round(statistics.fmean(values), 3),
                            round(_q(values, 0.1), 3),
                            round(_q(values, 0.9), 3),
                            round(statistics.fmean(deltas), 3),
                            round(_q(deltas, 0.1), 3),
                            round(sum(d < -1e-9 for d in deltas) / days, 3),
                            sum(opens is not None and opens < hour for hour, _ in SESSIONS),
                        )
                    )
    return rows


def _share(s: Session | None, ours: bool, field: Field) -> float:
    if not ours:
        return 0.5  # the free stall: it matches as well as the stall, whatever the field
    if s is None:
        return 0.0  # our board venue with its broker down realises nothing
    rivals = {"stall": [s.stall, s.stall], "strong": [s.oracle, s.stall], "top3": [s.oracle] * 3}[field]
    return session_points(s.ours, s.stall, rivals)


def _q(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def markdown(rows: Sequence[DayRow]) -> str:
    head = "| world | rivals | policy | plan | final pts (mean) | p10–p90 | Δ vs never (mean) | Δ p10 | days worse |"
    lines = [head, "|" + "---|" * 9]
    for r in rows:
        lines.append(
            f"| {r.world} | {r.field} | {r.policy} | {r.plan} | {r.final_mean:.2f} | "
            f"{r.final_p10:.2f}–{r.final_p90:.2f} | {r.delta_mean:+.2f} | {r.delta_p10:+.2f} | {r.p_worse:.0%} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--worlds", nargs="+", default=list(WORLDS))
    parser.add_argument("--fields", nargs="+", default=["stall", "strong", "top3"])
    parser.add_argument("--policies", nargs="+", default=["exact", "edge", "edge+probe"])
    parser.add_argument("--down", type=float, default=0.0, help="chance per session that our broker is down")
    parser.add_argument("--own", action="store_true", help="our in-process bench even if bazaar_sim is installed")
    parser.add_argument("--json", help="also write the rows to this file")
    args = parser.parse_args(argv)
    name, runner = ("W1b in-process bench", own_runner()) if args.own else default_runner()
    rows = simulate_days(args.days, args.worlds, args.fields, args.policies, args.down, runner)
    print(f"Saturday bench, {args.days} simulated days per row, {name}, broker down {args.down:.0%} per session\n")
    print(markdown(rows))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in rows], handle, indent=2)


if __name__ == "__main__":
    main()
