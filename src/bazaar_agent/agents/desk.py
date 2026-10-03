"""The dealer desk: several dealer conversations at once, one move per tick each, none blocking the loop.

`negotiate()` plays ONE thread to the end inside its own tick loop. The taker cannot wait on one
dealer, so the desk keeps each conversation's state between ticks and asks `dealer.decide()` for that
tick's move only. Same rules: one thread per dealer, the hard max never moves, a final offer is
take-it-or-walk, an offer whose structure is not the plain buy we asked for is ignored, and a thread
that runs `dealer_max_ticks_per_thread` ticks without a deal is closed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from bazaar_agent.agents.dealer import (
    BidPlan,
    Move,
    Negotiation,
    decide,
    latest_dealer_offer,
    meet_ask,
    newest_dealer_offer,
    offer_terms_problem,
    settled_price,
)
from bazaar_agent.strategy import Move as StrategyMove

ACCEPT_SETTLE_TICKS = 2  # an accept settles on the next tick; still open after this many, it did not land


@dataclass
class Conversation:
    """One live dealer thread the desk owns (mutable: it lives across ticks)."""

    dealer: str
    item: str
    rarity: str
    value: float  # worth to us (strategy): never pay above the plan's max, which sits below it
    reason: str
    neg: Negotiation
    thread_id: int
    opened_tick: int
    ticks: int = 0
    accepted_tick: int | None = None
    accepted_price: int | None = None
    reopened: bool = False  # this thread already is the lower reopen after she held her opening ask

    @property
    def topic(self) -> dict[str, dict[str, str]]:
        return topic_for(self.item)


def topic_for(item: str) -> dict[str, dict[str, str]]:
    return {"buy": {"card": item}} if "-" in item else {"buy": {"pack": item}}


@dataclass(frozen=True)
class DeskMove:
    """This tick's move for one conversation, with what it was decided from."""

    conv: Conversation
    move: Move
    ask: int | None = None
    final: bool = False
    status: str = "open"
    ignored: str | None = None  # why the dealer's offer was ignored (structure is not our buy)
    offer_id: int | None = None  # the dealer's standing offer we would accept

    @property
    def surplus(self) -> float:
        return self.conv.value - (self.ask or 0)


def plan_conversation(conv: Conversation, thread: dict[str, object], max_ticks: int, tick: int) -> DeskMove:
    """The one move for this tick. `wait` when the thread is closed or our accept is settling."""
    status = str(thread.get("status") or "open")
    if status != "open":
        return DeskMove(conv, Move("wait", reason=f"thread {status}"), status=status)
    if conv.accepted_tick is not None and tick - conv.accepted_tick < ACCEPT_SETTLE_TICKS:
        return DeskMove(conv, Move("wait", reason="accepted, waiting for settlement"))
    # An accept that never settled (the thread is still open) must not block this dealer forever. Its price
    # goes too: a later deal may be one of our higher bids, and `deal_price` reads what settled.
    conv.accepted_tick, conv.accepted_price = None, None
    ask, offer_id, final = latest_dealer_offer(thread, conv.dealer)
    newest = newest_dealer_offer(thread, conv.dealer)
    problem = offer_terms_problem(newest, conv.item) if newest is not None else None
    if problem:
        ask, offer_id, final = None, None, False
    if conv.ticks >= max_ticks:
        walk = Move("walk", reason=f"{max_ticks} ticks without a deal")
        return DeskMove(conv, walk, ask, final, ignored=problem, offer_id=offer_id)
    return DeskMove(conv, decide(conv.neg, ask, offer_id, final), ask, final, ignored=problem, offer_id=offer_id)


def meet_the_ask(dm: DeskMove) -> DeskMove:
    """Our accept slot went elsewhere this tick: offer exactly her ask instead (`dealer.meet_ask`: a new,
    higher price inside our max, never her opening price), so the dealer can accept OUR offer; else wait."""
    move = meet_ask(dm.conv.neg, dm.ask)
    return DeskMove(dm.conv, move, dm.ask, dm.final, offer_id=None if move.kind == "bid" else dm.offer_id)


def deal_price(conv: Conversation, thread: dict[str, object]) -> int | None:
    """What a settled deal cost: the thread's settled offer, else the most we may have agreed (our accept,
    or our last bid, which the dealer may have taken): over-counts the spend, never under-counts it."""
    settled = settled_price(thread)
    if settled is not None:
        return settled
    known = [p for p in (conv.accepted_price, conv.neg.bids[-1] if conv.neg.bids else None) if p is not None]
    return max(known) if known else None


@dataclass(frozen=True)
class Opening:
    """A dealer buy the desk may open now (from the strategy's ranked moves)."""

    dealer: str
    item: str
    rarity: str
    value: float
    plan: BidPlan
    reason: str
    move: StrategyMove = field(compare=False)


def openings(moves: Iterable[StrategyMove], busy_dealers: set[str], busy_items: set[str], room: int) -> list[Opening]:
    """The best dealer buys whose dealer is free (one thread per dealer) and item is not in a thread yet."""
    out: list[Opening] = []
    taken = set(busy_dealers)
    for mv in moves:
        if len(out) >= room:
            break
        if mv.ladder is None or not mv.command or mv.source in taken or mv.ref in busy_items:
            continue
        if mv.guardrail.startswith("denied"):
            continue
        start, top, step = mv.ladder
        out.append(Opening(mv.source, mv.ref, mv.rarity, mv.value, BidPlan(start, step, top), mv.reason, mv))
        taken.add(mv.source)
    return out
