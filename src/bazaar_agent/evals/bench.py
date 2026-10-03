"""The Market Test tournament: our bench broker against the stall and the greedy brokers, on synthetic books. Offline.

    uv run python -m bazaar_agent.evals.bench --books 1000            # the efficiency table, markdown
    uv run python -m bazaar_agent.evals.bench --books 1000 --json out.json

No network, no key: every book is drawn in-process from a seeded `random.Random`, so a table is reproducible.

**The bench model** (`InProcessBench`). No real Market Test has been observed yet (the Friday one was after the
capture; the shared feed has no `bench.*` event), so it is #55's generator plus #12's presets and the rules' words:
  - half sellers (cost 20–60, first ask = cost × 1.05–1.30), half buyers (value 40–95, first bid = value × 0.75–0.95);
  - each trader stays `patience` ticks: impatient ones 1–2 (25 % normal, 35 % hard), the others 3–6;
  - a relaxing trader moves its quote linearly toward its limit and keeps `relax_end` of its shade on its last tick;
    a firm one (20 % normal, 35 % hard) never moves;
  - arrivals: `front` (everyone at the first tick), `spread` (uniform over the first three quarters of the session)
    or `uniform` (over the whole session). The arrival pattern is the biggest unknown, so the table reports each;
  - acceptance: `quote` (a match needs `ask ≤ price` and `price + fee ≤ bid`, what #55 and RULES.md say) or `limit`
    (the server checks the hidden limits instead: `cost ≤ price` and `price + fee ≤ value`). Nobody knows which the
    real server does: the morning probe decides it.
  - the fee is `round(price × bps / 10000) + per card`, the simulator's rule (the matcher plans with ceil, the safe
    side), so a plan the server would refuse shows up as a refusal.

**Efficiency** = the gains between the true limits of the matched pairs ÷ the possible gains, #55's
`possible_gains`: the best static pairing of all limits, blind to time. Two clairvoyant bounds show how much any
broker could get in each world: `oracle_quote` (every pair whose quotes ever cross while both are in the book) and
`oracle_limit` (every pair whose limits cross while both are in the book).

**Policies**: `stall` (the free auto venue: best ask against best bid while they cross, every tick), `greedy` (the
starter broker's `bench_plan`, by quote), `exact` (PR #71's matcher: the maximum quoted-surplus matching every tick)
and `edge` (`agents/bench_edge.py`). Each tick a broker reads the book once and sends at most 15 matches.

The bench is a `Bench` protocol, so the same policies can run against another bench (W1a's simulator) by adapter.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from functools import partial
from typing import Any, Literal, Protocol

from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, expiries_in
from bazaar_agent.agents.bench_model import PRIORS, BenchPrior
from bazaar_agent.agents.matcher import BrokerBook, Fee, Match, max_weight_assignment, plan_matches, quotes_from

MAX_SENDS = 15  # BrokerConfig.max_matches_per_tick
Cross = Literal["quote", "limit"]
Arrivals = Literal["front", "spread", "uniform"]


@dataclass(frozen=True)
class BenchSpec:
    traders: int = 10
    ticks: int = 16
    firm_share: float = 0.2
    impatient_share: float = 0.25
    arrivals: Arrivals = "spread"
    relax_end: float = 0.25
    cross: Cross = "quote"
    fee_bps: int = 0
    fee_per_card: int = 0
    seller_markup: tuple[float, float] = (1.05, 1.30)  # first ask = cost × markup
    buyer_shade: tuple[float, float] = (0.75, 0.95)  # first bid = value × shade
    stall_pairs: int | None = None  # pairs the stall crosses per tick (None: every crossing pair)
    show_expiry: bool = False  # bench offers carry `expires_tick` (their last tick in the book)

    def fee(self, price: int) -> int:
        return round(price * self.fee_bps / 10_000) + self.fee_per_card


PRESETS: dict[str, BenchSpec] = {
    "normal": BenchSpec(),
    "hard": BenchSpec(traders=12, firm_share=0.35, impatient_share=0.35),
}


@dataclass(frozen=True)
class Trader:
    id: str
    side: Literal["sell", "buy"]
    limit: int
    shade: float  # how far the first quote sits from the limit, as a share of the limit
    patience: int  # ticks in the book
    firm: bool
    arrive: int
    relax_end: float

    def present(self, tick: int) -> bool:
        return self.arrive <= tick < self.arrive + self.patience

    def quote(self, tick: int) -> int:
        age = tick - self.arrive
        relaxed = 0.0 if self.firm or self.patience == 1 else (1 - self.relax_end) * age / (self.patience - 1)
        shade = self.shade * (1 - relaxed)
        return round(self.limit * (1 + shade)) if self.side == "sell" else round(self.limit * (1 - shade))


def draw_traders(spec: BenchSpec, rng: random.Random, run: int = 1) -> list[Trader]:
    out = []
    last = {"front": 1, "spread": max(1, spec.ticks * 3 // 4), "uniform": spec.ticks}[spec.arrivals]
    for k in range(spec.traders):
        side: Literal["sell", "buy"] = "sell" if k % 2 == 0 else "buy"
        if side == "sell":
            limit, shade = rng.randint(20, 60), rng.uniform(*spec.seller_markup) - 1
        else:
            limit, shade = rng.randint(40, 95), 1 - rng.uniform(*spec.buyer_shade)
        impatient = rng.random() < spec.impatient_share
        patience = rng.randint(1, 2) if impatient else rng.randint(3, 6)
        firm = rng.random() < spec.firm_share
        out.append(Trader(f"b{run}-{k}", side, limit, shade, patience, firm, rng.randrange(last), spec.relax_end))
    return out


class Refused(Exception):
    """The bench refused a match (the server's 400)."""


class Bench(Protocol):
    tick: int

    def book(self) -> dict[str, Any]: ...
    def match(self, sell: str, buy: str, price: int) -> None: ...
    def auto_cross(self) -> None: ...
    def advance(self) -> bool: ...


@dataclass
class InProcessBench:
    spec: BenchSpec
    traders: list[Trader]
    run: int = 1
    tick: int = 0
    pairs: list[tuple[str, str]] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    reads: int = 0
    sends: int = 0
    refused: dict[str, int] = field(default_factory=dict)
    sends_by_tick: dict[int, int] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, Trader]:
        return {t.id: t for t in self.traders}

    def open(self) -> list[Trader]:
        return [t for t in self.traders if t.id not in self.used and t.present(self.tick)]

    def book(self) -> dict[str, Any]:
        """The shape starter_broker.py reads: both sides carry `cash` (0 on the side that does not pay)."""
        self.reads += 1
        offers = []
        for t in self.open():
            q = t.quote(self.tick)
            if t.side == "sell":
                give: dict[str, Any] = {"cash": 0, "assets": [{"kind": "card", "ref": "BENCH"}]}
                offers.append({"id": t.id, "run": self.run, "give": give, "want": {"cash": q}})
            else:
                want: dict[str, Any] = {"cash": 0, "types": ["card:BENCH"]}
                offers.append({"id": t.id, "run": self.run, "give": {"cash": q}, "want": want})
            if self.spec.show_expiry:
                offers[-1]["expires_tick"] = t.arrive + t.patience - 1
        return {
            "offers": [],
            "bench_offers": offers,
            "fee_bps": self.spec.fee_bps,
            "fee_per_card": self.spec.fee_per_card,
        }

    def _refuse(self, reason: str) -> None:
        self.refused[reason] = self.refused.get(reason, 0) + 1
        raise Refused(reason)

    def match(self, sell: str, buy: str, price: int) -> None:
        self.sends += 1
        self.sends_by_tick[self.tick] = self.sends_by_tick.get(self.tick, 0) + 1
        open_now = {t.id: t for t in self.open()}
        s, b = open_now.get(sell), open_now.get(buy)
        if s is None or b is None or s.side != "sell" or b.side != "buy":
            self._refuse("not_open")
        assert s is not None and b is not None
        low, high = (s.quote(self.tick), b.quote(self.tick)) if self.spec.cross == "quote" else (s.limit, b.limit)
        if not low <= price or price + self.spec.fee(price) > high:
            self._refuse("does_not_cross")
        self.pairs.append((sell, buy))
        self.used |= {sell, buy}

    def auto_cross(self) -> None:
        """The free stall (#55 `_auto_bench`): best ask against best bid, by quote, while they cross (or at most
        `stall_pairs` pairs a tick: RULES.md says the auto venue "crosses its best bid and ask every tick")."""
        for _ in range(self.spec.stall_pairs or self.spec.traders):
            open_now = self.open()
            asks = sorted((t for t in open_now if t.side == "sell"), key=lambda t: t.quote(self.tick))
            bids = sorted((t for t in open_now if t.side == "buy"), key=lambda t: -t.quote(self.tick))
            if not asks or not bids:
                return
            ask, bid = asks[0].quote(self.tick), bids[0].quote(self.tick)
            if ask + self.spec.fee(ask) > bid:
                return
            self.pairs.append((asks[0].id, bids[0].id))
            self.used |= {asks[0].id, bids[0].id}

    def advance(self) -> bool:
        self.tick += 1
        return self.tick < self.spec.ticks

    def realised(self) -> int:
        by_id = self.by_id
        return sum(max(0, by_id[b].limit - by_id[s].limit) for s, b in self.pairs)


def possible_gains(traders: Sequence[Trader]) -> int:
    """#55's denominator: the best static pairing of the limits, blind to time and quotes."""
    sells = sorted(t.limit for t in traders if t.side == "sell")
    buys = sorted((t.limit for t in traders if t.side == "buy"), reverse=True)
    return sum(max(0, b - s) for s, b in zip(sells, buys, strict=False))


def oracle(traders: Sequence[Trader], spec: BenchSpec, cross: Cross) -> int:
    """A clairvoyant broker's best: the maximum-weight matching of every pair that can cross at some tick both are
    in the book (each such pair can be sent on its own tick, independently of the others)."""
    sells = [t for t in traders if t.side == "sell"]
    buys = [t for t in traders if t.side == "buy"]

    def crosses(s: Trader, b: Trader) -> bool:
        for tick in range(spec.ticks):
            if not (s.present(tick) and b.present(tick)):
                continue
            low, high = (s.quote(tick), b.quote(tick)) if cross == "quote" else (s.limit, b.limit)
            if low + spec.fee(low) <= high:
                return True
        return False

    weights = [
        [(b.limit - s.limit) * 100 + 1 if b.limit > s.limit and crosses(s, b) else 0 for b in buys] for s in sells
    ]
    return sum(buys[c].limit - sells[r].limit for r, c in max_weight_assignment(weights))


# ---------------------------------------------------------------- policies: one tick of each


def _send(
    bench: Bench, plan: Sequence[tuple[str, str, int]], answered: Callable[[int, bool], None] | None = None
) -> int:
    """Send the plan (at most MAX_SENDS); `answered(i, accepted)` hears each answer. Returns the sends."""
    for i, (sell, buy, price) in enumerate(plan[:MAX_SENDS]):
        try:
            bench.match(sell, buy, price)
        except Refused:
            if answered is not None:
                answered(i, False)
            continue
        if answered is not None:
            answered(i, True)
    return min(len(plan), MAX_SENDS)


def greedy_plan(book: dict[str, Any]) -> list[tuple[str, str, int]]:
    """starter_broker.bench_plan, verbatim in behaviour (tests/test_bench_eval.py checks it against the kit)."""
    plan: list[tuple[str, str, int]] = []
    runs: dict[str, tuple[list[tuple[int, str]], list[tuple[int, str]]]] = {}
    for o in book.get("bench_offers") or []:
        asks, bids = runs.setdefault(o["id"].split("-")[0], ([], []))
        if (o.get("want") or {}).get("cash"):
            asks.append((o["want"]["cash"], o["id"]))
        else:
            bids.append((o["give"]["cash"], o["id"]))
    for asks, bids in runs.values():
        for (ask, sell), (bid, buy) in zip(
            sorted(asks, key=lambda a: a[0]), sorted(bids, key=lambda b: -b[0]), strict=False
        ):
            if bid < ask:
                break
            plan.append((sell, buy, (ask + bid) // 2))
    return plan


class Policy(Protocol):
    def tick(self, bench: Bench) -> None: ...


class Stall:
    def tick(self, bench: Bench) -> None:
        bench.auto_cross()


class Greedy:
    def tick(self, bench: Bench) -> None:
        _send(bench, greedy_plan(bench.book()))


class Exact:
    """PR #71's broker as it stands: the exact maximum quoted-surplus matching of the book, every tick."""

    def tick(self, bench: Bench) -> None:
        book = BrokerBook.model_validate(bench.book())
        plan = plan_matches(quotes_from(book).quotes, Fee(book.fee_bps, book.fee_per_card), MAX_SENDS)
        _send(bench, [(str(m.sell.id), str(m.buy.id), m.price) for m in plan])


@dataclass
class Edge:
    prior: BenchPrior
    config: EdgeConfig = field(default_factory=EdgeConfig)
    reads_per_tick: int = 1
    edge: BenchEdge = field(init=False)

    def __post_init__(self) -> None:
        self.edge = BenchEdge(self.prior, self.config)

    def tick(self, bench: Bench) -> None:
        """Up to `reads_per_tick` reads, each planning with what the last answers taught; MAX_SENDS in all."""
        sent = 0
        for _ in range(self.reads_per_tick):
            book = BrokerBook.model_validate(bench.book())
            quotes = quotes_from(book).quotes
            self.edge.observe(quotes, bench.tick, expiries_in(book.bench_offers, bench.tick))
            fee = Fee(book.fee_bps, book.fee_per_card)
            plan = self.edge.plan(quotes, fee, bench.tick, limit=MAX_SENDS - sent)
            if not plan:
                return
            answer = partial(self._answered, plan)
            sent += _send(bench, [(str(m.sell.id), str(m.buy.id), m.price) for m in plan], answer)

    def _answered(self, plan: Sequence[Match], i: int, accepted: bool) -> None:
        self.edge.note_sent(plan[i], accepted)


class Prescient:
    """A bound, not a broker: it knows every trader in the book's true limit and last tick (not the future
    arrivals), crosses the maximum true-surplus matching of the crossing pairs, and holds a pair until one of its
    traders is on its last tick. What perfect estimation of limits and departures would buy."""

    def tick(self, bench: Bench) -> None:
        assert isinstance(bench, InProcessBench)
        tick, spec = bench.tick, bench.spec
        open_now = bench.open()
        sells = [t for t in open_now if t.side == "sell"]
        buys = [t for t in open_now if t.side == "buy"]

        def crosses(s: Trader, b: Trader) -> bool:
            low, high = (s.quote(tick), b.quote(tick)) if spec.cross == "quote" else (s.limit, b.limit)
            return low + spec.fee(low) <= high and b.limit >= s.limit

        def last(t: Trader) -> bool:
            return tick in (t.arrive + t.patience - 1, spec.ticks - 1)

        weights = [[(b.limit - s.limit) * 100 + 1 if crosses(s, b) else 0 for b in buys] for s in sells]
        for r, c in max_weight_assignment(weights):
            s, b = sells[r], buys[c]
            if last(s) or last(b):
                low = s.quote(tick) if spec.cross == "quote" else s.limit
                bench.match(s.id, b.id, low)


PolicyFactory = Callable[[str], Policy]


def policies(edge_config: EdgeConfig | None = None) -> dict[str, PolicyFactory]:
    return {
        "stall": lambda preset: Stall(),
        "greedy": lambda preset: Greedy(),
        "exact": lambda preset: Exact(),
        "edge": lambda preset: Edge(PRIORS[preset], edge_config or EdgeConfig()),
        "edge_limit": lambda preset: Edge(PRIORS[preset], replace(edge_config or EdgeConfig(), cross="limit"), 3),
    }


# ---------------------------------------------------------------- the tournament


@dataclass
class Outcome:
    efficiency: float
    refused: int
    max_sends: int
    reads: int


def play(spec: BenchSpec, traders: list[Trader], policy: Policy) -> Outcome:
    bench = InProcessBench(spec, traders)
    while True:
        policy.tick(bench)
        if not bench.advance():
            break
    best = possible_gains(traders) or 1
    return Outcome(
        bench.realised() / best, sum(bench.refused.values()), max(bench.sends_by_tick.values(), default=0), bench.reads
    )


# What-if worlds: each overrides the preset. "base" is the model above; the others move one unknown at a time.
SCENARIOS: dict[str, dict[str, Any]] = {
    "base": {},
    "front": {"arrivals": "front"},
    "uniform": {"arrivals": "uniform"},
    "stall1": {"stall_pairs": 1},
    "front_stall1": {"arrivals": "front", "stall_pairs": 1},
    "wide": {"seller_markup": (1.10, 1.60), "buyer_shade": (0.50, 0.90)},
    "expiry": {"show_expiry": True},
    "limit": {"cross": "limit"},
    "wide_limit": {"cross": "limit", "seller_markup": (1.10, 1.60), "buyer_shade": (0.50, 0.90)},
}
ORACLES = ("prescient", "oracle_quote", "oracle_limit")


@dataclass
class Row:
    preset: str
    scenario: str
    policy: str
    books: int
    p10: float
    p50: float
    mean: float
    refused: int  # matches the bench refused, over every book
    max_sends: int  # most matches sent in one tick
    reads_per_tick: float


def _row(preset: str, scenario: str, spec: BenchSpec, name: str, outcomes: Sequence[Outcome]) -> Row:
    eff = sorted(o.efficiency for o in outcomes)
    return Row(
        preset,
        scenario,
        name,
        len(eff),
        round(eff[len(eff) // 10], 3),
        round(statistics.median(eff), 3),
        round(statistics.fmean(eff), 3),
        sum(o.refused for o in outcomes),
        max(o.max_sends for o in outcomes),
        round(sum(o.reads for o in outcomes) / (len(outcomes) * spec.ticks), 2),
    )


def tournament(
    books: int,
    presets: Sequence[str] = ("normal", "hard"),
    scenarios: Sequence[str] = ("base",),
    seed: int = 2026,
    edge_config: EdgeConfig | None = None,
    names: Sequence[str] | None = None,
) -> list[Row]:
    """Every policy on the same `books` books per preset × scenario, plus the bounds."""
    rows: list[Row] = []
    factories = policies(edge_config)
    chosen = list(names or [*factories, *ORACLES])
    for preset in presets:
        for scenario in scenarios:
            spec = replace(PRESETS[preset], **SCENARIOS[scenario])
            rng = random.Random(f"{seed}:{preset}:{spec.arrivals}")  # the same books wherever the draw allows
            drawn = [draw_traders(spec, rng) for _ in range(books)]
            for name in chosen:
                if name in ORACLES:
                    outs = [_bound(name, spec, t) for t in drawn]
                else:
                    outs = [play(spec, t, factories[name](preset)) for t in drawn]
                rows.append(_row(preset, scenario, spec, name, outs))
    return rows


def _bound(name: str, spec: BenchSpec, traders: list[Trader]) -> Outcome:
    if name == "prescient":
        return play(spec, traders, Prescient())
    rule: Cross = "quote" if name == "oracle_quote" else "limit"
    return Outcome(oracle(traders, spec, rule) / (possible_gains(traders) or 1), 0, 0, 0)


def markdown(rows: Sequence[Row], stat: Literal["p50", "mean", "p10"] = "p50") -> str:
    """One line per preset × scenario, one column per policy."""
    names = list(dict.fromkeys(r.policy for r in rows))
    cells: dict[tuple[str, str], dict[str, Row]] = {}
    for r in rows:
        cells.setdefault((r.preset, r.scenario), {})[r.policy] = r
    lines = [f"| preset | scenario | {' | '.join(names)} |", "|" + "---|" * (len(names) + 2)]
    for (preset, scenario), by_name in cells.items():
        values = [f"{getattr(by_name[n], stat):.3f}" if n in by_name else "" for n in names]
        lines.append(f"| {preset} | {scenario} | {' | '.join(values)} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--books", type=int, default=1000)
    parser.add_argument("--presets", nargs="+", default=["normal", "hard"])
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--json", help="also write the rows to this file")
    args = parser.parse_args(argv)
    rows = tournament(args.books, args.presets, args.scenarios, args.seed)
    for stat in ("p50", "mean", "p10"):
        print(f"\nEfficiency {stat} ({args.books} books per row)\n")
        print(markdown(rows, stat))  # type: ignore[arg-type]
    refused = [r for r in rows if r.refused]
    print(
        "\nRefused matches: "
        + (", ".join(f"{r.preset}/{r.scenario}/{r.policy} {r.refused}" for r in refused) or "none")
    )
    print("Most sends in one tick: " + str(max(r.max_sends for r in rows)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in rows], handle, indent=2)


if __name__ == "__main__":
    main()
