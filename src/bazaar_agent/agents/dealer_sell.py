"""Selling to a dealer: the same negotiation as buying, in a mirrored price space.

Dealers buy too (Abuela commons and uncommons, El Chato uncommons and rares, and very likely the L3
"Collector"), and a sale is a ladder deal like a buy. When a dealer buys, it opens with a low bid,
raises it only when we come down, names a final when its patience runs out, and takes our ask the
moment it reaches its secret limit (the most it pays). That is a buy with every price p replaced by
`MIRROR - p`: its rising bid is a falling ask, our falling asks are rising bids. So `decide_sell`
runs #61's `dealer.decide()` on mirrored prices and mirrors the move back: one set of rules (never
close at the opening price, counter strictly past an unmoved price, take it when no whole price is
left between us, a final is take-it-or-walk) for both sides, never a copy.

`AskPlan.min_price` is the hard floor: never sell below it (GUARDRAILS.md `sell_min_value_ratio` ×
the copy's `your_value`, rounded up, is the least it may be). Pure functions, no network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from bazaar_agent.agents.dealer import BidPlan, Move, Negotiation, decide
from bazaar_agent.ladder import Conversation, FloorRow, PlanChoice, Turn

MIRROR = 10_000  # above any dealer price (RULES.md caps prices at 10,000,000; dealer bids are tens)


@dataclass(frozen=True)
class AskPlan:
    """Our side of one sale: open at `start`, come down `step` per tick, never below `min_price`."""

    start: int
    step: int
    min_price: int

    def __post_init__(self) -> None:
        if not 1 <= self.min_price <= self.start < MIRROR or self.step < 1:
            raise ValueError(f"bad ask plan: start={self.start} step={self.step} min={self.min_price}")

    def mirrored(self) -> BidPlan:
        return BidPlan(MIRROR - self.start, self.step, MIRROR - self.min_price)


def mirror(price: int | None) -> int | None:
    return None if price is None else MIRROR - price


class Sale:
    """One sale's state: a mirrored `Negotiation` that `dealer.decide()` drives."""

    def __init__(self, plan: AskPlan) -> None:
        self.plan = plan
        self.neg = Negotiation(plan.mirrored())

    @property
    def asks(self) -> list[int]:
        return [MIRROR - b for b in self.neg.bids]

    def record(self, ask: int) -> None:
        self.neg.bids.append(MIRROR - ask)


def decide_sell(sale: Sale, bid: int | None, offer_id: int | None, final: bool) -> Move:
    """The next move of a sale, given the dealer's latest standing bid (None when none stands)."""
    move = decide(sale.neg, mirror(bid), offer_id, final)
    return replace(move, price=mirror(move.price), reason=_mirrored_reason(move.reason))


def _mirrored_reason(reason: str) -> str:
    return (
        reason.replace("small distinct step up", "small distinct step down")
        .replace("counter below her unconceded ask", "counter above its unraised bid")
        .replace("ask meets our next bid", "bid meets our next ask")
        .replace("no higher bid left inside our limit", "no lower ask left above our floor")
    )


def sell_floor(your_value: float | None, ratio: float) -> int:
    """The least we may ask for a copy: GUARDRAILS.md sell_min_value_ratio × its your_value, rounded up."""
    return max(1, math.ceil((your_value or 0.0) * ratio))


def ask_plan_for(row: FloorRow, floor_price: int, *, q: float = 0.5, width: int = 2) -> PlanChoice:
    """The mirror of `ladder.plan_for` for a dealer that buys: the floor table's sale rows hold the most
    it paid per conversation, so start = limit + width, come down 1, never below limit − width or our
    own floor. No plan when our floor is above most of what it paid."""
    from bazaar_agent.ladder import MIN_CLOSED

    limit = row.floor(q)
    if limit is None or row.closed < MIN_CLOSED:
        return PlanChoice(row, None, limit, floor_price, f"{row.closed} closed conversations: fewer than {MIN_CLOSED}")
    top = row.floor(0.75) or limit
    if floor_price > top:
        return PlanChoice(row, None, limit, floor_price, f"our floor {floor_price} is above 3 in 4 of its limits")
    bottom = max(floor_price, limit - width)
    start = max(bottom, limit + width)
    if start <= row.opening:
        return PlanChoice(row, None, limit, floor_price, f"limit {limit} is its opening bid {row.opening}: no range")
    plan = AskPlan(start, 1, bottom)
    return PlanChoice(row, plan.mirrored(), limit, floor_price, f"limit p{round(q * 100)} {limit} ± {width}")


def mirrored_conversation(c: Conversation) -> Conversation:
    """A sale as the buy it mirrors (side 'buy', every price MIRROR − p): the ladder's floor table, its
    fitted dealer and its replay then work on sales unchanged."""
    out = Conversation(c.thread, c.team, c.dealer, "buy", c.item, c.opened_tick, fill=mirror(c.fill))
    out.fill_tick = c.fill_tick
    out.turns = [Turn(t.tick, t.dealer, mirror(t.price), t.final) for t in c.turns]
    return out
