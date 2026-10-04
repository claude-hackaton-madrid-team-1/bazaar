"""Market Test tournament on books calibrated to the real bench, scored with the real points rule.

    uv run python scripts/bench_tournament.py --seeds 300 [--robust] [--replay b120 b137] [--json out.json]
        [--plugin my_policies.py] [--only edge_none,mine] [--worlds cal_normal20]

Plug a strategy family in without touching this file: a .py file with
    POLICIES = {"mine": lambda traders: MyPolicy()}   # MyPolicy()(book) -> [(sell id, buy id, price), ...]
`book` is what `GET /api/broker/book` returns (bench_offers in the real shape, each with `expires_tick` = the
session's end as on the real server, and `tick`); the policy object lives for one session. `traders` is the hidden
truth, for bounds only (`who_oracle`); a real policy must not read it. `--replay` needs DATABASE_URL (read-only).

Why another bench script (`bench_edge_proof.py` stays as it is): Sunday's first recorded book (run b120, session 7,
Postgres `bench_books`) and one live fact change the setting.
  - The server checks QUOTES: the one-shot match probe (BAZAAR_BENCH_MATCH_PROBE) sent sell b120-12 ask 40 × buy
    b120-2 bid 39 @39 at tick 1692 and got 400 bad_match "price must sit between the ask 40 and the bid 39". So only
    pairs crossing by quote can match (`rule="quote"` only), and a broker beats the stall only by WHICH crossing
    traders it matches and WHEN.
  - The real book is not #77's preset: sellers come in two bumps (asks 35–48 or 77–134, none between), buyers open
    as low as 0.6 of a limit, quotes move 1–10 P a tick, a third of traders are firm. `WORLDS` below are fitted to it
    (`--fit` prints real vs simulated summaries).
  - Points: nobody beat the stall in any session so far (bench_points 0.5 for all 18 teams in session 7), so a venue
    alone above the stall is in the top three and gets the full points (0.5 + 0.5·(E−Es)/(T−Es) with T−Es =
    (E−Es)/3 caps at 1.0); below it, b = 0.5·E/Es (Saturday's fit on t03/t13 moves, ±0.004). `real_points`.

Every policy runs on the same books; per world: mean efficiency margin over the stall, P(above), P(below), worst
margin, E[points]. `--robust` reruns the main world with each parameter at ±50 %. `--replay RUN` rebuilds a recorded
real book from `bench_books` (read-only, DATABASE_URL) and replays it under posterior draws of the hidden limits,
lives and relax shares that reproduce every recorded quote path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from multiprocessing import Pool
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bazaar_agent.agents.bench_edge import BenchEdge, edge_plan, expiries_in  # noqa: E402
from bazaar_agent.agents.bench_lookahead import BenchLookahead, LookaheadConfig, LookaheadPrior  # noqa: E402
from bazaar_agent.agents.bench_model import PRIORS, BenchPrior  # noqa: E402
from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from  # noqa: E402
from bazaar_sim import bench  # noqa: E402
from bazaar_sim.models import BenchTrader  # noqa: E402

MAX_SENDS = 15
Pair = tuple[str, str, int]
EPS = 1e-9


# ---------------------------------------------------------------- worlds fitted to the real book


@dataclass(frozen=True)
class World:
    """A bench preset plus the seller mixture the preset cannot draw (uniform costs)."""

    preset: bench.BenchPreset
    cheap: tuple[int, int] | None = (25, 42)  # None: the preset's uniform costs
    dear: tuple[int, int] = (65, 110)
    cheap_share: float = 0.5

    def book(self, seed: int) -> list[BenchTrader]:
        traders = bench.make_book(self.preset, seed)
        if self.cheap is None:
            return traders
        rng = random.Random(seed * 7919 + 1)
        out = []
        for t in traders:
            if t.side == "sell":
                cost = rng.randint(*(self.cheap if rng.random() < self.cheap_share else self.dear))
                t = t.model_copy(update={"limit": cost, "quote": round(cost * t.quote / max(1, t.limit))})
            out.append(t)
        return out


def _cal(name: str, traders: int, firm: float, impatient: float) -> bench.BenchPreset:
    return replace(
        bench.NORMAL,
        name=name,
        traders=traders,
        firm_share=firm,
        impatient_share=impatient,
        arrive_spread=11,
        value=(40, 105),
        sell_shade=(1.05, 1.45),
        buy_shade=(0.6, 0.95),
    )


WORLDS: dict[str, World] = {
    # session 9 is a normal test with 10 traders a side (/api/schedule params traders=10; b120 had 12 a side)
    "cal_normal20": World(_cal("cal_normal20", 20, 0.2, 0.25)),
    "cal_hard24": World(_cal("cal_hard24", 24, 0.35, 0.35)),
    "cal_normal20_uniform": World(_cal("cal_normal20_uniform", 20, 0.2, 0.25), cheap=None),
    # the real books' hint: none of 15 unmatched traders left after one tick (impatient lives 2-3, not 1-2)
    "cal_normal20_life2": World(replace(_cal("cal_normal20_life2", 20, 0.2, 0.25), impatient_life=(2, 3))),
    "old_normal20": World(replace(bench.NORMAL, traders=20), cheap=None),  # #77's preset, twice the traders
}
MAIN = "cal_normal20"
REPLAY_WORLD = {"b120": "cal_hard24"}  # session 7 was the hard test (12 a side); the rest are normal


def robust_worlds() -> dict[str, World]:
    """MAIN with one parameter at ±50 % (shares capped to [0, 1], shade widths scaled around 1)."""
    w = WORLDS[MAIN]
    p = w.preset
    out: dict[str, World] = {}
    for f, tag in ((0.5, "-50%"), (1.5, "+50%")):
        out[f"firm{tag}"] = replace(w, preset=replace(p, firm_share=min(1.0, p.firm_share * f)))
        out[f"impatient{tag}"] = replace(w, preset=replace(p, impatient_share=min(1.0, p.impatient_share * f)))
        out[f"relax{tag}"] = replace(w, preset=replace(p, relax=(min(1.0, 0.5 * f), min(1.0, 1.0 * f))))
        out[f"shade{tag}"] = replace(w, preset=p.variant(shade=f))
        out[f"cheap_share{tag}"] = replace(w, cheap_share=min(1.0, w.cheap_share * f))
        out[f"lives{tag}"] = replace(
            w,
            preset=replace(
                p,
                impatient_life=(1, max(1, round(2 * f))),
                patient_life=(max(2, round(3 * f)), max(2, round(6 * f))),
            ),
        )
    return out


# ---------------------------------------------------------------- points


def zero_below_points(eff: float, stall: float) -> float:
    """The most pessimistic reading (review MED 2): nothing at all below the stall."""
    return 1.0 if eff > stall + EPS else 0.0 if eff < stall - EPS else 0.5


def real_points(eff: float, stall: float) -> float:
    """One session's bench points with the field at the stall: above it alone = full points, below 0.5·E/Es."""
    if eff > stall + EPS:
        return 1.0
    if eff < stall - EPS:
        return 0.5 * eff / stall if stall > 0 else 0.5
    return 0.5


# ---------------------------------------------------------------- policies (each sees only the book)


def exact_policy(book: dict[str, Any]) -> list[Pair]:
    parsed = BrokerBook.model_validate(book)
    fee = Fee(parsed.fee_bps, parsed.fee_per_card)
    return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan_matches(quotes_from(parsed).quotes, fee, MAX_SENDS)]


class EdgePolicy:
    """The broker's live edge path (broker.py: BenchEdge(PRIORS["normal"]), edge_plan with the book's expiries)."""

    def __init__(self, margin: float | None, prior: BenchPrior | None = None) -> None:
        self.edge = BenchEdge(prior or PRIORS["normal"])
        self.margin = margin

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        parsed = BrokerBook.model_validate(book)
        quotes = [q for q in quotes_from(parsed).quotes if q.bench]
        tick = int(book.get("tick") or 0)
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        plan = edge_plan(self.edge, quotes, fee, tick, MAX_SENDS, expiries_in(parsed.bench_offers, tick), self.margin)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan.matches]


class LookaheadPolicy:
    """`agents/bench_lookahead.py` as the broker runs it (BAZAAR_BENCH_POLICY=lookahead): the exact plan, replaced by
    another matching of crossing pairs when the rollouts expect more true gains."""

    def __init__(
        self, per_side: int, samples: int = 128, seed: int = 0, prior: LookaheadPrior | None = None, slack: float = 0.0
    ) -> None:
        prior = replace(prior or LookaheadPrior(), per_side=per_side)
        self.planner = BenchLookahead(LookaheadConfig(samples=samples, prior=prior, slack=slack), seed=seed)
        self.log: list[str] = []

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        parsed = BrokerBook.model_validate(book)
        bench_q = [q for q in quotes_from(parsed).quotes if q.bench]
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        exact = plan_matches(bench_q, fee, MAX_SENDS)
        expires = max(expiries_in(parsed.bench_offers, int(book["tick"])).values(), default=None)
        plan = self.planner.plan(bench_q, fee, int(book["tick"]), exact, expires, self.log.append)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan]


class HoldPolicy:
    """The stall's pairs, but a pair crossing by less than `gap` waits while both traders are younger than `age`
    ticks and more than `rest` ticks are left."""

    def __init__(self, gap: int, age: int, rest: int = 2, ticks: int = 16) -> None:
        self.gap, self.age, self.rest, self.ticks = gap, age, rest, ticks
        self.first: dict[str, int] = {}
        self.start: int | None = None

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        tick = int(book["tick"])
        self.start = tick if self.start is None else self.start
        quote = {}
        for o in book["bench_offers"]:
            self.first.setdefault(o["id"], tick)
            quote[o["id"]] = o["want"]["cash"] or o["give"]["cash"]
        left = self.ticks - (tick - self.start)
        out = []
        for s, b, price in bench.stall_policy(book):
            young = max(tick - self.first[s], tick - self.first[b]) < self.age
            if left > self.rest and young and quote[b] - quote[s] < self.gap:
                continue
            out.append((s, b, price))
        return out


class MaxCount:
    """As many quote-crossing pairs as the book allows this tick (the stall's lowest-ask × highest-bid zip stops at
    the first pair that does not cross, so ask 35/73 × bid 76/68 makes one pair where two cross: 35×68 and 73×76),
    the most quoted surplus among those; midpoint price."""

    BIG = 10_000

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        offers = book["bench_offers"]
        sells = [o for o in offers if o["want"]["cash"]]
        buys = [o for o in offers if o["give"]["cash"]]
        if not sells or not buys:
            return []
        w = [
            [
                (self.BIG + b["give"]["cash"] - s["want"]["cash"]) if s["want"]["cash"] <= b["give"]["cash"] else 0
                for b in buys
            ]
            for s in sells
        ]
        _, chosen = bench.best_matching(w)
        mid = lambda i, j: (sells[i]["want"]["cash"] + buys[j]["give"]["cash"]) // 2  # noqa: E731
        return [(sells[i]["id"], buys[j]["id"], mid(i, j)) for i, j in chosen]


class WhoOracle:
    """A bound, not a policy: knows every limit, never holds; each tick the maximum true-surplus matching among the
    quote-crossing pairs, as many pairs as the stall."""

    def __init__(self, traders: Sequence[BenchTrader]) -> None:
        self.by = {t.id: t for t in traders}

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        offers = book["bench_offers"]
        sells = [o for o in offers if o["want"]["cash"]]
        buys = [o for o in offers if o["give"]["cash"]]
        if not sells or not buys:
            return []
        gain = lambda s, b: self.by[b["id"]].limit - self.by[s["id"]].limit + 1000  # noqa: E731
        w = [[gain(s, b) if s["want"]["cash"] <= b["give"]["cash"] else 0 for b in buys] for s in sells]
        _, chosen = bench.best_matching(w)
        return [(sells[i]["id"], buys[j]["id"], sells[i]["want"]["cash"]) for i, j in chosen]


# the edge's prior refitted to b120 (asks open up to ~1.45 x cost, bids down to ~0.6 x value)
CAL_PRIOR = BenchPrior(seller_markup=(1.05, 1.45), buyer_shade=(0.6, 0.95))

POLICIES: dict[str, Callable[[Sequence[BenchTrader]], Callable[[dict[str, Any]], list[Pair]]]] = {
    "stall": lambda tr: bench.stall_policy,
    "exact": lambda tr: exact_policy,
    "edge20": lambda tr: EdgePolicy(20.0),
    "edge": lambda tr: EdgePolicy(None),  # BAZAAR_BENCH_POLICY=edge, margin unset (10)
    "edge5": lambda tr: EdgePolicy(5.0),
    "edge0": lambda tr: EdgePolicy(0.0),
    "edge_none": lambda tr: EdgePolicy(float("-inf")),  # BAZAAR_BENCH_GUARD_MARGIN=none
    "edgecal_none": lambda tr: EdgePolicy(float("-inf"), CAL_PRIOR),
    "edgecal5": lambda tr: EdgePolicy(5.0, CAL_PRIOR),
    "maxcount": lambda tr: MaxCount(),
    "lookahead": lambda tr: LookaheadPolicy(len(tr) // 2),
    "lookahead_slack1": lambda tr: LookaheadPolicy(len(tr) // 2, slack=1.0),
    "lookahead_slack2": lambda tr: LookaheadPolicy(len(tr) // 2, slack=2.0),
    "lookahead_slack4": lambda tr: LookaheadPolicy(len(tr) // 2, slack=4.0),
    "hold3": lambda tr: HoldPolicy(3, 1),
    "hold6": lambda tr: HoldPolicy(6, 2),
    "who_oracle": lambda tr: WhoOracle(tr),  # bound
}


def stamp(policy: Callable[[dict[str, Any]], list[Pair]], end: int) -> Callable[[dict[str, Any]], list[Pair]]:
    """The real book: every bench offer carries `expires_tick` = the session's end (b120: 1706 on all 24)."""

    def f(book: dict[str, Any]) -> list[Pair]:
        for o in book.get("bench_offers") or []:
            o["expires_tick"] = end
        return policy(book)

    return f


def run_book(traders: list[BenchTrader], p: bench.BenchPreset, names: Sequence[str]) -> dict[str, tuple[float, float]]:
    """{policy: (efficiency, stall's efficiency)} on one book, plus `oracle`: the best any quote-rule broker could do
    knowing every limit, arrival and departure (the headroom over the stall)."""
    out = {}
    for name in names:
        r = bench.simulate(stamp(POLICIES[name](traders), p.ticks), p, 0, traders=traders)
        out[name] = (r.efficiency, r.stall)
    out["oracle"] = (r.oracle, r.stall)
    return out


# ---------------------------------------------------------------- tables


@dataclass(frozen=True)
class Row:
    world: str
    policy: str
    books: int
    eff: float
    stall: float
    margin: float
    above: float
    below: float
    worst: float
    points: float
    points_zero_below: float


def summarise(world: str, results: list[dict[str, tuple[float, float]]], names: Sequence[str]) -> list[Row]:
    rows = []
    for name in names:
        rs = [r[name] for r in results]
        d = [e - s for e, s in rs]
        rows.append(
            Row(
                world,
                name,
                len(rs),
                round(statistics.mean(e for e, _ in rs), 4),
                round(statistics.mean(s for _, s in rs), 4),
                round(statistics.mean(d), 4),
                round(sum(x > EPS for x in d) / len(d), 3),
                round(sum(x < -EPS for x in d) / len(d), 3),
                round(min(d), 3),
                round(statistics.mean(real_points(e, s) for e, s in rs), 3),
                round(statistics.mean(zero_below_points(e, s) for e, s in rs), 3),
            )
        )
    return rows


def markdown(rows: Sequence[Row]) -> str:
    out = [
        "| world | policy | books | eff | stall | margin | P(above) | P(below) | worst | E[points] | E[pts] 0 below |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| {r.world} | {r.policy} | {r.books} | {r.eff:.4f} | {r.stall:.4f} | {r.margin:+.4f} | {r.above:.3f} | "
        f"{r.below:.3f} | {r.worst:+.3f} | {r.points:.3f} | {r.points_zero_below:.3f} |"
        for r in rows
    ]
    return "\n".join(out)


# ---------------------------------------------------------------- the real book, replayed


@dataclass(frozen=True)
class Seen:
    id: str
    side: str
    first: int  # run-relative
    path: tuple[int, ...]  # quotes on consecutive ticks from `first`
    matched: bool  # our broker matched it (its life is censored at its last tick)
    censored_end: bool  # still there at the session's last tick


def load_real(run: str) -> tuple[list[Seen], int]:
    """A recorded book from `bench_books` (read-only) and our broker's matches from `decisions`."""
    import psycopg

    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as conn:
        rows = conn.execute(
            "select offer_id, side, tick, quote from bench_books where run = %s order by offer_id, tick", (run,)
        ).fetchall()
        start = min(r[2] for r in rows)
        last = max(r[2] for r in rows)
        matched = {
            x
            for (c,) in conn.execute(
                "select chosen from decisions where kind = 'broker_match' and status = 'done'"
                " and tick between %s and %s",
                (start, last + 1),
            ).fetchall()
            if isinstance(c, dict)
            for x in (c.get("sell"), c.get("buy"))
            if isinstance(x, str) and x.startswith(f"{run}-")
        }
    paths: dict[str, tuple[str, list[tuple[int, int]]]] = {}
    for oid, side, tick, quote in rows:
        paths.setdefault(oid, (side, []))[1].append((tick - start, quote))
    seen = [
        Seen(oid, side, p[0][0], tuple(q for _, q in p), oid in matched, p[-1][0] >= last - start)
        for oid, (side, p) in paths.items()
    ]
    return seen, start


def posterior_trader(s: Seen, rng: random.Random, world: World, ticks: int) -> BenchTrader:
    """One draw of the hidden trader behind a recorded quote path, by rejection from the world's priors: a limit
    beyond every quote, a life at least as long as seen (exactly as seen when it left unmatched before the end), and a
    relax share that reproduces the path's average step."""
    p = world.preset
    sell = s.side == "sell"
    q0, n = s.path[0], len(s.path)
    step = (s.path[-1] - q0) / (n - 1) if n > 1 else None
    for _ in range(5000):
        if sell:
            lo, hi = p.sell_shade
            limit = round(q0 / rng.uniform(lo, hi))
            ok_limit = limit <= min(s.path)
        else:
            lo, hi = p.buy_shade
            limit = round(q0 / rng.uniform(lo, hi))
            ok_limit = limit >= max(s.path)
        if not ok_limit or limit <= 0:
            continue
        exact_life = not s.matched and not s.censored_end
        if exact_life:
            life = n
        else:
            imp = rng.random() < p.impatient_share
            a, b = p.impatient_life if imp else p.patient_life
            life = rng.randint(a, b)
            if life < n:
                continue
        if step is None or step == 0:
            # a flat path is firm; one tick seen: firm or relaxing per the prior
            relax = 0.0 if step == 0 or rng.random() < p.firm_share else rng.uniform(*p.relax)
        else:
            if life <= 1 or limit == q0:
                continue
            relax = step * (life - 1) / (limit - q0)
            if not 0.0 < relax <= 1.0:
                continue
        life = min(life, ticks)
        return BenchTrader(id=s.id, side=s.side, limit=limit, quote=q0, arrive=s.first, life=life, relax=relax)
    # no draw reproduces the path under these priors: keep the path's own end as the limit
    return BenchTrader(id=s.id, side=s.side, limit=s.path[-1], quote=q0, arrive=s.first, life=n, relax=0.0)


def replay_rows(run: str, draws: int, names: Sequence[str], world: World) -> list[Row]:
    seen, _ = load_real(run)
    ticks = world.preset.ticks
    results = []
    for k in range(draws):
        rng = random.Random(k)
        traders = [posterior_trader(s, rng, world, ticks) for s in seen]
        results.append(run_book(traders, world.preset, names))
    return summarise(f"replay {run}", results, [*names, "oracle"])


# ---------------------------------------------------------------- main


def load_plugins(paths: Sequence[str]) -> None:
    """Add every plugin file's `POLICIES` ({name: factory(traders) -> policy(book) -> [(sell, buy, price)]}) to ours.
    A real policy must ignore `traders` (the hidden truth): only bounds such as `who_oracle` may read it."""
    import importlib.util

    for k, path in enumerate(paths):
        spec = importlib.util.spec_from_file_location(f"bench_plugin_{k}", path)
        assert spec and spec.loader, path
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        POLICIES.update(module.POLICIES)


def _one(args: tuple[str, int, bool, tuple[str, ...]]) -> dict[str, tuple[float, float]]:
    name, seed, robust, names = args
    world = (robust_worlds() if robust else WORLDS)[name]
    return run_book(world.book(seed), world.preset, names)


def run_worlds(worlds: Sequence[str], seeds: int, robust: bool, pool: Any, names: Sequence[str]) -> list[Row]:
    rows: list[Row] = []
    for name in worlds:
        results = pool.map(_one, [(name, s, robust, tuple(names)) for s in range(seeds)])
        rows += summarise(name, results, [*names, "oracle"])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--robust", action="store_true")
    ap.add_argument("--replay", nargs="*", default=[])
    ap.add_argument("--draws", type=int, default=300)
    ap.add_argument("--json")
    ap.add_argument("--plugin", action="append", default=[], help="a .py file defining POLICIES (repeatable)")
    ap.add_argument("--only", help="comma-separated policy names (stall and exact are always run)")
    ap.add_argument("--worlds", help=f"comma-separated worlds (default all: {', '.join(WORLDS)})")
    args = ap.parse_args()
    load_plugins(args.plugin)
    names = list(POLICIES)
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted - set(POLICIES)
        if unknown:
            ap.error(f"unknown policies: {', '.join(sorted(unknown))}")
        names = [n for n in POLICIES if n in wanted or n in ("stall", "exact")]
    worlds = args.worlds.split(",") if args.worlds else list(WORLDS)
    rows: list[Row] = []
    with Pool(initializer=load_plugins, initargs=(args.plugin,)) as pool:
        rows += run_worlds(worlds, args.seeds, False, pool, names)
        print(markdown(rows), flush=True)
        if args.robust:
            rob = run_worlds(list(robust_worlds()), args.seeds, True, pool, names)
            print("\n" + markdown(rob), flush=True)
            rows += rob
    for run in args.replay:
        rep = replay_rows(run, args.draws, names, WORLDS[REPLAY_WORLD.get(run, MAIN)])
        print("\n" + markdown(rep), flush=True)
        rows += rep
    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in rows], indent=1))


if __name__ == "__main__":
    main()
