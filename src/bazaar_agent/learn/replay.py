"""Replay real dealer conversations under a ladder: would it have closed, and at what price?

Each real thread brackets its own secret limit L from its structure: a bid the dealer countered is below L
(`low`); a price the dealer accepted or offered is at or above it (`high`: the fill, else its last ask or
final). The replay is pessimistic for every ladder alike: the dealer accepts our bid only once it reaches
`high`, and after `patience` bids (the thread's own bids before its final; else at least as many as the
team made, and the class median) it names its final, which we take only at or under our walk point.

Share = (opening ask − price) / (opening ask − the class's lowest fill), as the evals score a deal; no deal
scores 0. It is a model, not the dealer: `old` and `new` are judged by the same rules on the same threads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import mean

from bazaar_agent.evals.dealers import price_class
from bazaar_agent.intel import DealerThread
from bazaar_agent.learn.evolve import DEFAULT_PATIENCE, Ladder


@dataclass(frozen=True)
class Replayed:
    thread: int
    price: int | None  # None: no deal
    bids: int
    share: float


def _bracket(t: DealerThread) -> tuple[int, int] | None:
    """(low, high): the dealer refused `low` and would take `high`. None without a dealer price."""
    if not t.dealer_prices:
        return None
    if t.fill_price is not None:
        high = t.fill_price
    else:
        high = min(p for p in (t.final_price, t.dealer_prices[-1]) if p is not None)
    countered = [b for b in t.team_prices if b < high]
    if t.fill_price is not None and t.team_prices and t.team_prices[-1] == t.fill_price:
        countered = [b for b in t.team_prices[:-1] if b < high]
    low = max(countered, default=0)
    return low, high


def replay_thread(
    t: DealerThread, ladder: Ladder, floor: int, patience: float, final_max: int | None = None
) -> Replayed | None:
    """`final_max` (N14a): the most we take for the dealer's final; None = the walk point, as before."""
    bracket = _bracket(t)
    opening = t.opening_ask
    if bracket is None or opening is None or opening <= floor:
        return None
    _, high = bracket
    own = len(t.team_prices) if t.final_price is not None else max(len(t.team_prices), round(patience))
    rounds = max(1, own)
    bids = ladder.bids(rounds)
    for n, bid in enumerate(bids, start=1):
        if bid >= high:
            return Replayed(t.thread, bid, n, _share(opening, bid, floor))
    final = t.final_price if t.final_price is not None else high
    if final <= (ladder.walk if final_max is None else max(ladder.walk, final_max)):
        return Replayed(t.thread, final, rounds, _share(opening, final, floor))
    return Replayed(t.thread, None, rounds, 0.0)


def _share(opening: int, price: int, floor: int) -> float:
    return round(min(1.0, max(0.0, (opening - price) / (opening - floor))), 4)


@dataclass(frozen=True)
class Comparison:
    dealer: str
    price_class: str
    threads: int
    old: Ladder
    new: Ladder | None
    old_deals: int
    new_deals: int
    old_share: float
    new_share: float
    old_price: float | None  # mean price of the deals
    new_price: float | None
    real_share: float  # what the teams actually got on those threads

    def as_dict(self) -> dict[str, object]:
        return {
            "threads": self.threads,
            "old": str(self.old),
            "new": str(self.new) if self.new else "skip",
            "old_deals": self.old_deals,
            "new_deals": self.new_deals,
            "old_share": self.old_share,
            "new_share": self.new_share,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "real_share": self.real_share,
        }


def compare(
    threads: Iterable[DealerThread], dealer: str, cls: str, old: Ladder, new: Ladder | None, floor: int, patience: float
) -> Comparison | None:
    """Both ladders on every real conversation of (dealer, class). `new` None = skip: no deal, no cost."""
    pool = [t for t in threads if t.dealer == dealer and price_class(t.item) == cls and t.side == "buy"]
    olds = [r for t in pool if (r := replay_thread(t, old, floor, patience)) is not None]
    if not olds:
        return None
    news = [r for t in pool if new is not None and (r := replay_thread(t, new, floor, patience)) is not None]
    real = [
        _share(t.opening_ask, t.fill_price, floor) if t.fill_price is not None and t.opening_ask else 0.0
        for t in pool
        if t.opening_ask is not None and t.opening_ask > floor and _bracket(t) is not None
    ]

    def price(rs: list[Replayed]) -> float | None:
        paid = [r.price for r in rs if r.price is not None]
        return round(mean(paid), 2) if paid else None

    return Comparison(
        dealer,
        cls,
        len(olds),
        old,
        new,
        sum(r.price is not None for r in olds),
        sum(r.price is not None for r in news),
        round(mean(r.share for r in olds), 3),
        round(mean(r.share for r in news), 3) if news else 0.0,
        price(olds),
        price(news),
        round(mean(real), 3) if real else 0.0,
    )


def today_ladder(fills: tuple[int, ...], cap: int | None, max_ticks: int) -> Ladder | None:
    """The ladder the strategy builds today without learning (`strategy.bid_range` + `ladder_step`): from the
    lowest fill to the highest (capped), climbing to reach it in `max_ticks` bids. Value is not modelled."""
    if not fills:
        return None
    top = fills[-1] if cap is None else min(cap, fills[-1])
    start = min(fills[0], top)
    step = max(1, -(-(top - start) // max(1, max_ticks - 1)))
    return Ladder(start, step, top)


def patience_of(curve_patience: float | None) -> float:
    return curve_patience or DEFAULT_PATIENCE


def replay_all(
    threads: list[DealerThread],
    policies: Mapping[tuple[str, str], tuple[Ladder | None, Ladder | None, int, float]],
) -> dict[tuple[str, str], Comparison]:
    """`policies`: (dealer, class) → (old ladder, new ladder or None, class floor, patience)."""
    out = {}
    for (dealer, cls), (old, new, floor, patience) in policies.items():
        if old is None:
            continue
        found = compare(threads, dealer, cls, old, new, floor, patience)
        if found is not None:
            out[(dealer, cls)] = found
    return out
