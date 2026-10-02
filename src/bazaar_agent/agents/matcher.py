"""The broker's matcher: which sells to pair with which buys on our venue, and at what price. Pure.

Payload in (`GET /api/broker/book`: the venue's public offers, makers as pseudonyms, and during the
Market Test the synthetic `bench_offers`), a plan of `(sell, buy, price)` out. No I/O here.

What a match must satisfy (RULES.md "Your own market", `POST /api/broker/matches`):
  - one item: a public sell gives exactly one card for cash, a public buy gives cash for exactly one card
    type, and both name the same card; two bench offers pair only inside one bench run ("b12" in "b12-7");
  - `ask <= price` and `price + fee(price) <= bid`, so a pair is feasible iff `ask + fee(ask) <= bid`
    (the fee grows with the price). `fee(p) = ceil(p × fee_bps / 10000) + fee_per_card`, the rounding the
    tape shows (`agents/market.py`); it is never below the simulator's `round`, so ceil is the safe side;
  - two different makers (no wash trade), and never an offer of ours (we cannot trade on our own venue,
    and an offer id of ours, or the pseudonym that made it, is dropped before anything is planned).

Which pairs: a maximum-weight bipartite matching per item, weight = the quoted surplus `bid − ask`, solved
exactly (Hungarian algorithm, O(n²m), books are small). Ties go to more pairs: every quote shades away from
a hidden limit (bench sellers ask above their cost, buyers bid below their value), so a pair's true surplus
is at least its quoted one and a zero-quote-surplus pair still likely creates value. The weight is the
integer `surplus × (k + 1) + 1` with k = the most pairs the item allows, so surplus always wins and the
count only breaks ties.

Why not the starter broker's greedy (best bid against best ask, stop at the first pair that does not
cross)? With no fee and no maker conflicts it is optimal for one item, because the total surplus is
`Σ matched bids − Σ matched asks` whatever the pairing. A fee breaks it: asks 10 and 20, bids 30 and 21,
2 P per card: greedy pairs 10↔30 (20), then 20↔21 does not cross (20 + 2 > 21) and it stops at 20,
while 10↔21 and 20↔30 realise 21. A maker on both sides of one card breaks it too. Hence the exact solver.

Why the price does not matter for the score: a match moves `bid − ask` of quoted surplus whatever the
price; the price only splits it between the two sides (`(bid − price) + (price − ask)`), and the fee is a
transfer to us that never counts (RULES.md "Scoring"). We use the midpoint, lowered until the buyer can
also pay the fee: the fair split, and the one that keeps both sides inside their quotes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_SIDE = 60  # offers per side and item the exact solver takes (best quotes first): 60³ steps is instant

# ---------------------------------------------------------------- the book (validated at the boundary)


class _Side(BaseModel):
    model_config = ConfigDict(extra="allow")
    cash: int | None = 0
    assets: list[dict[str, Any]] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)


class BookOffer(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | str
    maker: str | None = None
    status: str | None = None
    thread: int | None = None
    give: _Side = Field(default_factory=_Side)
    want: _Side = Field(default_factory=_Side)


class BrokerBook(BaseModel):
    model_config = ConfigDict(extra="allow")
    offers: list[dict[str, Any]] = Field(default_factory=list)
    bench_offers: list[dict[str, Any]] = Field(default_factory=list)
    fee_bps: int = Field(default=0, ge=0)
    fee_per_card: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class Fee:
    bps: int = 0
    per_card: int = 0

    def of(self, price: int) -> int:
        return math.ceil(price * self.bps / 10_000) + self.per_card


@dataclass(frozen=True)
class Quote:
    """One side of a possible match: a sell (its ask) or a buy (its bid) for one item."""

    id: int | str
    side: Literal["sell", "buy"]
    item: str  # "card:LAV-03", or "bench:b12" for every offer of bench run b12
    price: int  # the ask of a sell, the bid of a buy
    maker: str  # a pseudonym; a bench trader is its own maker

    @property
    def bench(self) -> bool:
        return self.item.startswith("bench:")


@dataclass(frozen=True)
class Match:
    sell: Quote
    buy: Quote
    price: int
    fee: int

    @property
    def surplus(self) -> int:
        """The quoted gain the pair realises, whatever the price: `bid − ask`."""
        return self.buy.price - self.sell.price

    @property
    def makers(self) -> frozenset[str]:
        return frozenset((self.sell.maker, self.buy.maker))


def _public_quote(o: BookOffer) -> Quote | None:
    """A plain public sell (one card for cash) or buy (cash for one card type); anything else is skipped."""
    if o.status not in (None, "open") or o.thread is not None or not isinstance(o.id, int) or not o.maker:
        return None
    give_cash, want_cash = o.give.cash or 0, o.want.cash or 0
    if len(o.give.assets) == 1 and not give_cash and want_cash > 0 and not o.want.types and not o.want.assets:
        asset = o.give.assets[0]
        if not asset.get("ref"):
            return None
        return Quote(o.id, "sell", f"{asset.get('kind') or 'card'}:{asset['ref']}", want_cash, o.maker)
    if give_cash > 0 and not o.give.assets and len(o.want.types) == 1 and not want_cash and not o.want.assets:
        wanted = o.want.types[0]
        return Quote(o.id, "buy", wanted if ":" in wanted else f"card:{wanted}", give_cash, o.maker)
    return None


def _bench_quote(o: BookOffer) -> Quote | None:
    """A bench seller asks `want.cash`, a bench buyer bids `give.cash` (starter_broker.py)."""
    if not isinstance(o.id, str) or "-" not in o.id:
        return None
    item = "bench:" + o.id.split("-")[0]
    if (o.want.cash or 0) > 0:
        return Quote(o.id, "sell", item, int(o.want.cash or 0), o.id)
    if (o.give.cash or 0) > 0:
        return Quote(o.id, "buy", item, int(o.give.cash or 0), o.id)
    return None


def _validated(rows: Iterable[object]) -> tuple[list[BookOffer], int]:
    good, bad = [], 0
    for row in rows:
        try:
            good.append(BookOffer.model_validate(row))
        except ValidationError:
            bad += 1
    return good, bad


@dataclass(frozen=True)
class Quotes:
    quotes: list[Quote]
    skipped: int  # malformed rows or shapes the venue cannot cross
    ours: int  # offers dropped because they are ours


def quotes_from(book: BrokerBook, our_offer_ids: Iterable[int] = (), *, public: bool = True) -> Quotes:
    """Every crossable quote in the book, without ours. `public=False` keeps the bench only (used when our
    own offers could not be read: we never risk matching an offer of ours)."""
    offers, bad = _validated(book.offers) if public else ([], 0)
    bench, bad_bench = _validated(book.bench_offers)
    ids = set(our_offer_ids)
    our_makers = {o.maker for o in offers if o.id in ids and o.maker}
    quotes, skipped, ours = [], bad + bad_bench, 0
    for o in offers:
        if o.id in ids or o.maker in our_makers:
            ours += 1
            continue
        q = _public_quote(o)
        skipped += q is None
        if q is not None:
            quotes.append(q)
    for o in bench:
        q = _bench_quote(o)
        skipped += q is None
        if q is not None:
            quotes.append(q)
    return Quotes(quotes, skipped, ours)


# ---------------------------------------------------------------- the exact solver


def max_weight_assignment(weights: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """Maximum-weight matching of rows to columns (weights ≥ 0; 0 = no edge), as (row, col) pairs.

    The Hungarian algorithm (shortest augmenting paths with potentials, O(n²m)) on the rectangular
    assignment with n ≤ m (transposed if needed). Every row is assigned, a 0-weight edge standing for
    "unmatched", so the best assignment is the best matching once its 0-weight pairs are dropped.
    """
    n = len(weights)
    m = len(weights[0]) if n else 0
    if n == 0 or m == 0:
        return []
    if n > m:
        flipped = [[weights[r][c] for r in range(n)] for c in range(m)]
        return [(r, c) for c, r in max_weight_assignment(flipped)]
    inf = math.inf
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)  # p[col] = the row on that column (1-based, 0 = none)
    for row in range(1, n + 1):
        p[0], col0 = row, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while p[col0] != 0:
            used[col0] = True
            r0, delta, col1 = p[col0], inf, 0
            for col in range(1, m + 1):
                if used[col]:
                    continue
                cur = -weights[r0 - 1][col - 1] - u[r0] - v[col]  # minimise the negated weight
                if cur < minv[col]:
                    minv[col], way[col] = cur, col0
                if minv[col] < delta:
                    delta, col1 = minv[col], col
            for col in range(m + 1):
                if used[col]:
                    u[p[col]] += delta
                    v[col] -= delta
                else:
                    minv[col] -= delta
            col0 = col1
        while col0:
            col1 = way[col0]
            p[col0] = p[col1]
            col0 = col1
    return [(p[col] - 1, col - 1) for col in range(1, m + 1) if p[col] and weights[p[col] - 1][col - 1] > 0]


def feasible(sell: Quote, buy: Quote, fee: Fee) -> bool:
    return sell.item == buy.item and sell.maker != buy.maker and sell.price + fee.of(sell.price) <= buy.price


def match_price(ask: int, bid: int, fee: Fee) -> int:
    """The midpoint, lowered until `price + fee(price) <= bid`; never below the ask (feasible pairs only)."""
    for price in range((ask + bid) // 2, ask - 1, -1):
        if price + fee.of(price) <= bid:
            return price
    raise ValueError(f"ask {ask} and bid {bid} do not cross with fee {fee}")


def best_matches(sells: Sequence[Quote], buys: Sequence[Quote], fee: Fee) -> list[Match]:
    """The maximum-surplus set of pairs (most pairs among equals) for one item."""
    sells = sorted(sells, key=lambda q: (q.price, str(q.id)))[:MAX_SIDE]
    buys = sorted(buys, key=lambda q: (-q.price, str(q.id)))[:MAX_SIDE]
    scale = min(len(sells), len(buys)) + 1
    weights = [[(b.price - s.price) * scale + 1 if feasible(s, b, fee) else 0 for b in buys] for s in sells]
    pairs = max_weight_assignment(weights)
    out = []
    for r, c in pairs:
        s, b = sells[r], buys[c]
        price = match_price(s.price, b.price, fee)
        out.append(Match(s, b, price, fee.of(price)))
    return out


def plan_matches(quotes: Iterable[Quote], fee: Fee, limit: int | None = None) -> list[Match]:
    """Best matches for every item, bench first (the Market Test scores them), then by surplus."""
    by_item: dict[str, tuple[list[Quote], list[Quote]]] = defaultdict(lambda: ([], []))
    for q in quotes:
        sells, buys = by_item[q.item]
        (sells if q.side == "sell" else buys).append(q)
    plan = [m for sells, buys in by_item.values() for m in best_matches(sells, buys, fee)]
    plan.sort(key=lambda m: (not m.sell.bench, -m.surplus, str(m.sell.id)))
    return plan if limit is None else plan[:limit]
