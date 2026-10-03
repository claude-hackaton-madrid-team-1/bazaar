"""The Market Test, pure: presets, synthetic traders, one venue's session, the free stall and an oracle.

No `World` here. The simulator's venues (`broker.py`) run the bench through these functions, and a
broker under test runs it in-process through `simulate()`, thousands of books a second.

What the official texts say (RULES.md "The Market Test", vendor/bazaar-kit/starter_broker.py, the
schedule fixture tests/fixtures/api/get_api_schedule.anon.json):
  - every venue gets the same synthetic book: 10 traders over 16 ticks, 12 in the hard test
    ("firmer and more impatient traders");
  - traders shade their quotes away from a limit they keep hidden; some are patient and some leave
    soon; most relax their quotes as their patience runs out, and the firm ones never do;
  - the score is the share of the possible gains (between the true limits) a venue realises; matching
    as well as the free auto stall earns half the bench points, the mean of the top three the full points.

Issue #12 gives the numbers: impatient traders leave after 1–2 ticks instead of 3–6; the firm share is
0.2 (normal) and 0.35 (hard), the impatient share 0.25 and 0.35.

Assumptions (in no official text; each one is a preset field or a parameter, so both regimes can be run):
  - arrival: traders show up spread over the session (`arrive_spread`, default `ticks − 6`, so a patient
    trader who arrives last still leaves inside it); `arrive_spread=0` is the "whole book at tick 0" regime;
  - relaxing: linear in the trader's age, giving up `relax` ∈ [0.5, 1.0] of its shade by its last tick;
  - the match rule: `quote` (today's simulator: `ask <= price` and `price + fee <= bid` at the current
    quotes) or `limit` (the same at the hidden limits). Which one the real `POST /api/broker/matches`
    applies is unknown: a refused probe in the morning settles it;
  - the free stall charges no fee, and crosses as the starter broker does (`cross_by_quote`);
  - the book does not change inside a tick: reading it twice in one tick shows the same offers.

The contract for a broker under test:

    from bazaar_sim.bench import HARD, NORMAL, make_traders, simulate, stall_policy
    result = simulate(policy, NORMAL, seed=7, rule="quote")   # policy(book) -> [(sell, buy, price), ...]
    result.efficiency, result.stall, result.oracle, result.points(), result.refused, result.max_requests_per_tick

`policy` gets the payload `GET /api/broker/book` returns (`bench_offers` in the real shape; `tick` the game
tick, as the simulator's venues show it, the run starting at `simulate(start_tick=...)`, default 0) and
returns the matches to send. It is called `reads_per_tick` times a tick, each time on the book left by the
matches before; keep state across calls in the policy object (a class with `__call__`). A refused match
costs nothing but is counted by reason in `result.refused`. `result.points(rivals)` is the session's share
of the bench points; `references(traders, ticks, rule)` gives the stall and the oracle once per book.
"""

from __future__ import annotations

import contextlib
import random
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, get_args

from bazaar_sim.errors import SimError, invalid
from bazaar_sim.models import BenchTrader

MatchRule = Literal["quote", "limit"]
MATCH_RULES: tuple[str, ...] = get_args(MatchRule)
BENCH_REF = "BENCH"
MAX_EXACT_SIDE = 16  # the exact matching below is a bitmask DP over the smaller side

Pair = tuple[str, str, int]  # (sell id, buy id, price)
Policy = Callable[[dict[str, Any]], Iterable[Pair]]


@dataclass(frozen=True)
class BenchPreset:
    name: str
    traders: int
    ticks: int = 16
    firm_share: float = 0.2
    impatient_share: float = 0.25
    impatient_life: tuple[int, int] = (1, 2)  # ticks in the book, both ends included
    patient_life: tuple[int, int] = (3, 6)
    arrive_spread: int | None = None  # arrivals uniform on ticks 0..spread; None: ticks − the longest patient life
    relax: tuple[float, float] = (0.5, 1.0)
    cost: tuple[int, int] = (20, 60)  # a seller's hidden limit
    value: tuple[int, int] = (40, 95)  # a buyer's hidden limit
    sell_shade: tuple[float, float] = (1.05, 1.3)  # opening ask = cost × shade
    buy_shade: tuple[float, float] = (0.75, 0.95)  # opening bid = value × shade

    @property
    def spread(self) -> int:
        if self.arrive_spread is not None:
            return max(0, self.arrive_spread)
        return max(0, self.ticks - self.patient_life[1])

    def with_ticks(self, ticks: int) -> BenchPreset:
        return replace(self, ticks=ticks)

    def variant(
        self, *, spread: int | None = None, shade: float = 1.0, relax: tuple[float, float] | None = None
    ) -> BenchPreset:
        """The same preset with other unverified assumptions, for sensitivity runs: arrivals over 0..`spread`,
        every shade `shade` times as wide (2.0: asks 10–60 % over cost, bids 10–50 % under value), another relax."""
        widen = lambda lo, hi, side: (side + (lo - side) * shade, side + (hi - side) * shade)  # noqa: E731
        return replace(
            self,
            arrive_spread=self.arrive_spread if spread is None else spread,
            sell_shade=widen(*self.sell_shade, 1.0),
            buy_shade=widen(*self.buy_shade, 1.0),
            relax=self.relax if relax is None else relax,
        )


NORMAL = BenchPreset("normal", traders=10, firm_share=0.2, impatient_share=0.25)
HARD = BenchPreset("hard", traders=12, firm_share=0.35, impatient_share=0.35)
# The simulator's first bench: the whole book at tick 0, every trader stays the whole run and never relaxes.
STATIC = BenchPreset(
    "static", traders=10, firm_share=1.0, impatient_share=0.0, patient_life=(1_000, 1_000), arrive_spread=0
)
PRESETS = {p.name: p for p in (NORMAL, HARD, STATIC)}


def preset(name: str) -> BenchPreset:
    if name not in PRESETS:
        raise ValueError(f"unknown bench preset {name!r} (one of {', '.join(PRESETS)})")
    return PRESETS[name]


def make_traders(rng: random.Random, p: BenchPreset, run: int = 1) -> list[BenchTrader]:
    """Sellers on even slots, buyers on odd ones. Every trader draws the same seven numbers whatever the preset,
    so two presets with one seed share their limits and quotes and differ only in patience and firmness."""
    spread = p.spread
    traders = []
    for k in range(p.traders):
        sell = k % 2 == 0
        limit = rng.randint(*(p.cost if sell else p.value))
        shade = rng.uniform(*(p.sell_shade if sell else p.buy_shade))
        u_firm, u_impatient, u_life, u_arrive, u_relax = (rng.random() for _ in range(5))
        lo, hi = p.impatient_life if u_impatient < p.impatient_share else p.patient_life
        relax = 0.0 if u_firm < p.firm_share else round(p.relax[0] + u_relax * (p.relax[1] - p.relax[0]), 3)
        traders.append(
            BenchTrader(
                id=f"b{run}-{k}",
                side="sell" if sell else "buy",
                limit=limit,
                quote=round(limit * shade),
                arrive=min(spread, int(u_arrive * (spread + 1))),
                life=lo + min(hi - lo, int(u_life * (hi - lo + 1))),
                relax=relax,
            )
        )
    return traders


def make_book(p: BenchPreset, seed: int, run: int = 1) -> list[BenchTrader]:
    """The traders of one seeded book: what `simulate(..., seed=seed)` runs on."""
    return make_traders(random.Random(seed), p, run)


# ---------------------------------------------------------------- one trader at one tick


def present(t: BenchTrader, tick: int) -> bool:
    """In the book at this run-relative tick (unless a venue matched it already)."""
    return t.arrive <= tick < t.arrive + t.life


def quote_at(t: BenchTrader, tick: int) -> int:
    """The quote shown at this run-relative tick: the opening quote, relaxed linearly toward the limit with age."""
    if t.relax <= 0 or t.life <= 1:
        return t.quote
    age = min(max(tick - t.arrive, 0), t.life - 1)
    return round(t.limit + (t.quote - t.limit) * (1 - t.relax * age / (t.life - 1)))


def fee_of(price: int, fee_bps: int, fee_per_card: int) -> int:
    return round(price * fee_bps / 10_000) + fee_per_card


def offer(t: BenchTrader, tick: int, run: int) -> dict[str, Any]:
    """A bench offer as the broker book shows it: both sides in full, no limit, no age, no patience."""
    q = quote_at(t, tick)
    card = {"kind": "card", "ref": BENCH_REF}
    if t.side == "sell":
        return {
            "id": t.id,
            "run": run,
            "give": {"cash": 0, "assets": [card], "types": []},
            "want": {"cash": q, "assets": [], "types": []},
        }
    return {
        "id": t.id,
        "run": run,
        "give": {"cash": q, "assets": [], "types": []},
        "want": {"cash": 0, "assets": [], "types": [f"card:{BENCH_REF}"]},
    }


def refusal(s: BenchTrader, b: BenchTrader, price: int, fee: int, tick: int, rule: str) -> str | None:
    """Why a venue may not match this seller to this buyer now (None: it may). Both must be open in its book."""
    if s.side != "sell" or b.side != "buy":
        return "sides"
    if rule == "limit":
        return None if s.limit <= price and price + fee <= b.limit else "limit"
    return None if quote_at(s, tick) <= price and price + fee <= quote_at(b, tick) else "quote"


def gain(s: BenchTrader, b: BenchTrader) -> int:
    """What a matched pair realises for the score: the gap between the true limits."""
    return max(0, b.limit - s.limit)


def possible_gains(traders: Sequence[BenchTrader]) -> int:
    """The book's possible gains: the best pairing at the true limits, ignoring who is there when."""
    sells = sorted(t.limit for t in traders if t.side == "sell")
    buys = sorted((t.limit for t in traders if t.side == "buy"), reverse=True)
    return sum(max(0, b - s) for s, b in zip(sells, buys, strict=False))


def cross_by_quote(open_traders: Sequence[BenchTrader], tick: int, fee: Callable[[int], int]) -> list[Pair]:
    """The free stall and an auto venue: its lowest ask against its highest bid, at the ask, while the bid covers
    ask + fee. Equal quotes keep the book's order (sorted() is stable), as the starter broker says the stall does."""
    asks = sorted((t for t in open_traders if t.side == "sell"), key=lambda t: quote_at(t, tick))
    bids = sorted((t for t in open_traders if t.side == "buy"), key=lambda t: -quote_at(t, tick))
    pairs = []
    for s, b in zip(asks, bids, strict=False):
        ask, bid = quote_at(s, tick), quote_at(b, tick)
        if ask + fee(ask) > bid:
            break
        pairs.append((s.id, b.id, ask))
    return pairs


# ---------------------------------------------------------------- one venue's copy of one run


@dataclass
class BenchSession:
    """One venue's copy of one Market Test: who is in its book now, what it matched, what it was refused."""

    traders: list[BenchTrader]
    run: int = 1
    ticks: int = 16
    fee_bps: int = 0
    fee_per_card: int = 0
    rule: str = "quote"
    tick: int = 0  # run-relative: 0 is the run's first tick
    start_tick: int = 0  # the game tick the run started at: the book's `tick` is start_tick + tick, as the game's
    pairs: list[tuple[str, str, int, int]] = field(default_factory=list)  # (sell, buy, price, tick)
    refused: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if self.rule not in MATCH_RULES:
            raise ValueError(f"unknown match rule {self.rule!r} (one of {', '.join(MATCH_RULES)})")
        self.by_id = {t.id: t for t in self.traders}
        self.used: set[str] = set()

    @property
    def done(self) -> bool:
        return self.tick >= self.ticks

    def fee(self, price: int) -> int:
        return fee_of(price, self.fee_bps, self.fee_per_card)

    def open(self) -> list[BenchTrader]:
        return [t for t in self.traders if t.id not in self.used and present(t, self.tick)]

    def book(self) -> dict[str, Any]:
        """The broker book during this run, in the shape `GET /api/broker/book` has (no public offers)."""
        return {
            "venue": "bench",
            "offers": [],
            "bench_offers": [offer(t, self.tick, self.run) for t in self.open()] if not self.done else [],
            "fee_bps": self.fee_bps,
            "fee_per_card": self.fee_per_card,
            "settlements": [],
            "tick": self.start_tick + self.tick,
        }

    def match(self, sell: str, buy: str, price: int) -> dict[str, Any]:
        """`POST /api/broker/matches` on a bench pair: recorded at once, or refused (and counted) with a SimError."""
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            self.refused["price"] += 1
            raise invalid("price must be a whole number of primas")
        open_now = {t.id: t for t in self.open()} if not self.done else {}
        s, b = open_now.get(sell), open_now.get(buy)
        if s is None or b is None:
            self.refused["gone"] += 1
            raise invalid("sell and buy must be open bench offers of the running test")
        fee = self.fee(price)
        why = refusal(s, b, price, fee, self.tick, self.rule)
        if why is not None:
            self.refused[why] += 1
            raise invalid(
                f"needs ask <= price and price + fee <= bid (fee {fee})"
                if why != "sides"
                else "sell and buy must be open bench offers of the running test"
            )
        self.used.update((sell, buy))
        self.pairs.append((sell, buy, price, self.tick))
        return {"ok": True, "sell": sell, "buy": buy, "price": price, "fee": fee}

    def advance(self) -> None:
        self.tick += 1

    def realised(self) -> int:
        return sum(gain(self.by_id[s], self.by_id[b]) for s, b, _, _ in self.pairs)


def run_stall(
    traders: list[BenchTrader], ticks: int, *, fee_bps: int = 0, fee_per_card: int = 0, run: int = 1
) -> BenchSession:
    """The free auto stall on this book: every tick it crosses by quote before anyone else reads the book."""
    session = BenchSession(traders, run=run, ticks=ticks, fee_bps=fee_bps, fee_per_card=fee_per_card)
    while not session.done:
        for s, b, price in cross_by_quote(session.open(), session.tick, session.fee):
            session.match(s, b, price)
        session.advance()
    return session


def stall_policy(book: dict[str, Any]) -> list[Pair]:
    """The stall as a broker policy (what the kit's starter broker sends): the lowest ask against the highest bid
    while the bid covers ask + the venue's fee, at the ask, from the book alone."""
    fee = lambda p: fee_of(p, int(book.get("fee_bps") or 0), int(book.get("fee_per_card") or 0))  # noqa: E731
    asks = sorted((o for o in book.get("bench_offers") or [] if o["want"]["cash"]), key=lambda o: o["want"]["cash"])
    bids = sorted((o for o in book.get("bench_offers") or [] if o["give"]["cash"]), key=lambda o: -o["give"]["cash"])
    plan = []
    for s, b in zip(asks, bids, strict=False):
        ask, bid = s["want"]["cash"], b["give"]["cash"]
        if ask + fee(ask) > bid:
            break
        plan.append((s["id"], b["id"], ask))
    return plan


# ---------------------------------------------------------------- the oracle: the best any broker could do


def first_cross(
    s: BenchTrader, b: BenchTrader, ticks: int, rule: str, fee: Callable[[int], int]
) -> tuple[int, int] | None:
    """(tick, price) of the first tick both are in the book and the pair may be matched, or None if never."""
    for k in range(max(s.arrive, b.arrive), min(s.arrive + s.life, b.arrive + b.life, ticks)):
        price = s.limit if rule == "limit" else quote_at(s, k)
        if refusal(s, b, price, fee(price), k, rule) is None:
            return k, price
    return None


def best_matching(weights: list[list[int]]) -> tuple[int, list[tuple[int, int]]]:
    """Exact maximum-weight bipartite matching (rows × columns, weight 0 = no edge) by a DP over the subsets of
    the smaller side. Returns (total, [(row, column), ...])."""
    if not weights or not weights[0]:
        return 0, []
    flip = len(weights[0]) > len(weights)
    w = [list(col) for col in zip(*weights, strict=True)] if flip else weights
    rows, cols = len(w), len(w[0])
    if cols > MAX_EXACT_SIDE:
        raise ValueError(f"best_matching takes at most {MAX_EXACT_SIDE} on the smaller side, got {cols}")
    best: dict[int, tuple[int, list[tuple[int, int]]]] = {0: (0, [])}
    for r in range(rows):
        nxt = dict(best)
        for mask, (total, chosen) in best.items():
            for c in range(cols):
                if w[r][c] > 0 and not mask >> c & 1:
                    cand = (total + w[r][c], [*chosen, (r, c)])
                    if cand[0] > nxt.get(mask | 1 << c, (-1, []))[0]:
                        nxt[mask | 1 << c] = cand
        best = nxt
    total, chosen = max(best.values(), key=lambda v: v[0])
    return total, [(c, r) for r, c in chosen] if flip else chosen


def oracle_schedule(
    traders: list[BenchTrader], ticks: int, rule: str, *, fee_bps: int = 0, fee_per_card: int = 0
) -> list[tuple[str, str, int, int]]:
    """What a broker knowing every limit, arrival, departure and future quote matches: the maximum-gain set of
    pairs that can each be matched at some tick, each at its first such tick. [(sell, buy, price, tick)]."""
    fee = lambda p: fee_of(p, fee_bps, fee_per_card)  # noqa: E731
    sells = [t for t in traders if t.side == "sell"]
    buys = [t for t in traders if t.side == "buy"]
    cross = [[first_cross(s, b, ticks, rule, fee) for b in buys] for s in sells]
    weights = [[gain(s, b) if cross[i][j] else 0 for j, b in enumerate(buys)] for i, s in enumerate(sells)]
    _, chosen = best_matching(weights)
    out = []
    for i, j in chosen:
        k_price = cross[i][j]
        assert k_price is not None
        out.append((sells[i].id, buys[j].id, k_price[1], k_price[0]))
    return sorted(out, key=lambda m: (m[3], m[0]))


# ---------------------------------------------------------------- the in-process harness


def references(
    traders: list[BenchTrader], ticks: int, rule: str, *, fee_bps: int = 0, fee_per_card: int = 0
) -> tuple[int, int, int]:
    """(possible gains, what the free stall realises, what the oracle realises) on one book: what every policy
    run on it is scored against. Compute it once per book when running several policies."""
    by_id = {t.id: t for t in traders}
    oracle = oracle_schedule(traders, ticks, rule, fee_bps=fee_bps, fee_per_card=fee_per_card)
    return (
        possible_gains(traders),
        run_stall(traders, ticks).realised(),
        sum(gain(by_id[s], by_id[b]) for s, b, _, _ in oracle),
    )


@dataclass(frozen=True)
class BenchResult:
    preset: str
    seed: int
    rule: str
    possible: int  # the static optimum at the true limits (the denominator of every efficiency)
    realised: int  # what the policy's matches realise
    stall_realised: int  # what the free stall realises on the same book
    oracle_realised: int  # the best any broker could realise on it under this rule
    matches: int
    refused: dict[str, int]
    reads: int
    posts: int
    max_requests_per_tick: int

    def _share(self, value: int) -> float:
        return round(value / self.possible, 4) if self.possible else 1.0

    @property
    def efficiency(self) -> float:
        return self._share(self.realised)

    @property
    def stall(self) -> float:
        return self._share(self.stall_realised)

    @property
    def oracle(self) -> float:
        return self._share(self.oracle_realised)

    def points(self, rivals: Sequence[float] | None = None) -> float:
        """This session's share of the bench points against `rivals`' efficiencies (default: two stall-level
        venues, what a field running the kit's starter broker or the free stall scores)."""
        field_ = [self.stall, self.stall] if rivals is None else list(rivals)
        return session_points(self.efficiency, self.stall, field_)


def simulate(
    policy: Policy,
    p: BenchPreset = NORMAL,
    seed: int = 0,
    *,
    rule: str = "quote",
    fee_bps: int = 0,
    fee_per_card: int = 0,
    reads_per_tick: int = 1,
    traders: list[BenchTrader] | None = None,
    start_tick: int = 0,
) -> BenchResult:
    """Run one Market Test with `policy` as the venue's broker, and score it against the stall and the oracle."""
    traders = traders if traders is not None else make_book(p, seed)
    session = BenchSession(
        traders, ticks=p.ticks, fee_bps=fee_bps, fee_per_card=fee_per_card, rule=rule, start_tick=start_tick
    )
    reads = posts = peak = 0
    while not session.done:
        in_tick = 0
        for _ in range(max(1, reads_per_tick)):
            reads, in_tick = reads + 1, in_tick + 1
            for sell, buy, price in policy(session.book()):
                posts, in_tick = posts + 1, in_tick + 1
                with contextlib.suppress(SimError):  # a refusal costs nothing; the session counts it by reason
                    session.match(sell, buy, price)
        peak = max(peak, in_tick)
        session.advance()
    possible, stall, oracle = references(traders, p.ticks, rule, fee_bps=fee_bps, fee_per_card=fee_per_card)
    return BenchResult(
        preset=p.name,
        seed=seed,
        rule=rule,
        possible=possible,
        realised=session.realised(),
        stall_realised=stall,
        oracle_realised=oracle,
        matches=len(session.pairs),
        refused=dict(session.refused),
        reads=reads,
        posts=posts,
        max_requests_per_tick=peak,
    )


def session_points(ours: float, stall: float, rivals: Sequence[float]) -> float:
    """Our share of one session's bench points, the top three taken over our venue and the rivals' ones."""
    top = sorted([ours, *rivals], reverse=True)[:3]
    return bench_points(ours, stall, sum(top) / len(top))


def bench_points(efficiency: float, stall: float, top3: float) -> float:
    """One session's share of the bench points (RULES.md): 0.5 for matching as well as the free stall, 1.0 for
    the mean of the top three, linear in between and from 0 to the stall (our reading; the curve is unpublished)."""
    if efficiency <= stall:
        return round(0.5 * efficiency / stall, 4) if stall > 0 else 0.5
    if top3 <= stall:
        return 1.0
    return round(min(1.0, 0.5 + 0.5 * (efficiency - stall) / (top3 - stall)), 4)
