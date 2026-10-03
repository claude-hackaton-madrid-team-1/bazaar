"""Relisting one copy after its ask lapsed unsold: step the price down, never the same price twice in a row.

Organisers' Day-2 hint: "quality over volume: the same price twice is not a move; dealers notice spam". Sat 3
Oct (tick 160+): the maker listed 110 asks and sold 3, the same copy at the same price up to 16 times (SAL-01
#11 at 10). An ask lives 20 ticks on the server, lapses silently, and the maker posted it again unchanged.

Per copy (`AskTrail`, read back from our live decision rows, so a restart keeps it):
  - first listing: the strategy's price;
  - an open ask stands at its price (it moves only down, when the strategy's target falls below it);
  - a strategy target above every ask the copy ever had (a chaser appeared): listed afresh at the target;
  - a relist after a lapse: previous − max(1, round(`relist_step_share` × previous)), and down to the median
    of the venue's recent fills of that card (else its rarity; 3+ fills, 2+ sellers, none ours) when that
    median lies between the floor and the stepped price; never above the strategy's target, never below the floor;
  - the floor: the highest of what selling the copy costs us (`ask_floor` of the target's value, which
    includes a page bonus), its `your_value` in /me × `sell_min_value_ratio`, and `relist_min_price_share` × the
    copy's first ask. /api/me/value is the value of one MORE copy (a buy cap): there is no official sell value;
  - a rest of `relist_cooldown_ticks`: once the copy lapsed `relist_max_lapses` times since its last rest, or when
    the price cannot step down any more (it sits at the floor and would repeat). After a rest the copy may
    be listed once more at the floor: a rest stands between the two posts.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from bazaar_agent.intel import Print

ROUNDING = 1e-9
MARKET_TICKS = 120  # the fills the market anchor reads: the last 120 ticks (1 h at Saturday's 30 s ticks)
MIN_PRINTS, MIN_SELLERS = 3, 2  # the market anchor needs this many fills, from this many sellers
MEMORY_TICKS = 720  # how far back a copy's posts are read (a restart keeps this much history)


@dataclass(frozen=True)
class AskTrail:
    """One copy's live asks as our decision rows remember them, oldest first."""

    asset_id: int
    prices: tuple[int, ...] = ()  # every ask posted for it inside the memory window
    since_rest: int = 0  # asks posted since its last rest (all of them when it never rested)
    rest_until: int | None = None  # the end of its latest rest

    @property
    def first(self) -> int | None:
        return self.prices[0] if self.prices else None

    @property
    def last(self) -> int | None:
        return self.prices[-1] if self.prices else None


@dataclass(frozen=True)
class Relist:
    price: int | None  # the ask to stand or post now; None: the copy rests (no post)
    why: str
    rest_until: int | None = None  # a NEW rest starts now and ends at this tick
    floor: int = 0  # a relist's floor: no later pick (Jev's quick sale) may go under it


def step_down(previous: int, share: float) -> int:
    return previous - max(1, round(previous * share))


def relist_floor(trail: AskTrail, cost_floor: int, min_share: float) -> int:
    """The lowest relist price: what selling costs us, and `min_share` × the copy's first ask."""
    first = trail.first
    share_floor = math.ceil(first * min_share - ROUNDING) if first is not None else 0
    return max(cost_floor, share_floor)


def relist_price(
    target: int,
    trail: AskTrail,
    *,
    open_price: int | None,
    cost_floor: int,
    median: float | None,
    tick: int,
    step_share: float,
    min_share: float,
    max_lapses: int,
    cooldown_ticks: int,
) -> Relist:
    """Where one copy's ask should stand this tick (see the module docstring)."""
    if not trail.prices:
        return Relist(target, "first listing")
    if target > max(trail.prices):  # a new need (a chaser appeared): the strategy's price, like a first listing
        return Relist(target, f"target {target} above every past ask: listed afresh")
    if open_price is not None:
        return Relist(min(target, open_price), "open ask stands")
    previous = trail.prices[-1]
    if trail.rest_until is not None and tick < trail.rest_until:
        return Relist(None, f"resting until tick {trail.rest_until}")
    floor = relist_floor(trail, cost_floor, min_share)
    if trail.since_rest >= max_lapses:
        return _rest(tick, cooldown_ticks, f"lapsed {trail.since_rest} times unsold at {previous}")
    stepped = step_down(previous, step_share)
    why = f"lapsed unsold at {previous}: step to {stepped}"
    anchor = math.ceil(median - ROUNDING) if median is not None else None
    if anchor is not None and floor <= anchor < stepped:
        stepped, why = anchor, f"lapsed unsold at {previous}: market median {median:g}"
    price = max(floor, min(target, stepped))
    if price == previous and trail.since_rest > 0:
        return _rest(tick, cooldown_ticks, f"at its floor {floor}: {previous} would repeat")
    held = "" if price == stepped else f", held at {'floor' if price == floor else 'target'} {price}"
    return Relist(price, why + held, floor=floor)


def _rest(tick: int, cooldown_ticks: int, why: str) -> Relist:
    until = tick + cooldown_ticks
    return Relist(None, f"{why}: rest until tick {until}", until)


def market_median(
    prints: Iterable[Print], venue: str, ref: str, rarity: str, rarities: dict[str, str], tick: int, us: str
) -> float | None:
    """The median price of the venue's single-card fills in the last MARKET_TICKS ticks, of `ref`, else of its
    rarity: only with MIN_PRINTS fills from MIN_SELLERS sellers, and never our own, so one staged fill (a rival
    selling to a friend at our break-even) cannot set it."""
    recent = [
        p
        for p in prints
        if p.venue == venue
        and p.kind == "card"
        and p.items == 1
        and p.tick > tick - MARKET_TICKS
        and us not in (p.buyer, p.seller)
    ]
    for same in ([p for p in recent if p.ref == ref], [p for p in recent if rarities.get(p.ref) == rarity]):
        if len(same) >= MIN_PRINTS and len({p.seller for p in same}) >= MIN_SELLERS:
            return float(statistics.median(p.price for p in same))
    return None
