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
    newest_dealer_offer,
    offer_terms_problem,
)
from bazaar_agent.strategy import Move as StrategyMove


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
    notes: tuple[str, ...] = ()  # which learnings changed this plan (N14a `changed_by`), logged on every move

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


def plan_conversation(conv: Conversation, thread: dict[str, object], max_ticks: int) -> DeskMove:
    """The one move for this tick. `wait` when the thread is closed or our accept is settling."""
    status = str(thread.get("status") or "open")
    if status != "open":
        return DeskMove(conv, Move("wait", reason=f"thread {status}"), status=status)
    if conv.accepted_tick is not None:
        return DeskMove(conv, Move("wait", reason="accepted, waiting for settlement"))
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
    """Our accept slot went elsewhere this tick: offer exactly her ask instead (inside our max, or her final
    inside `final_max`), so the dealer can accept OUR offer. Only when it is a new, higher price; else wait."""
    last = dm.conv.neg.bids[-1] if dm.conv.neg.bids else 0
    ask, plan = dm.ask, dm.conv.neg.plan
    if ask is not None and last < ask <= (plan.final_cap if dm.final else plan.max_price):
        what = "final" if dm.final and ask > plan.max_price else "ask"
        return DeskMove(dm.conv, Move("bid", ask, reason=f"accept slot used: meet her {what}"), ask, dm.final)
    return DeskMove(dm.conv, Move("wait", reason="accept slot used this tick"), ask, dm.final, offer_id=dm.offer_id)


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
    taken, items = set(busy_dealers), set(busy_items)
    for mv in moves:
        if len(out) >= room:
            break
        if mv.ladder is None or not mv.command or mv.source in taken or mv.ref in items:
            continue
        if mv.guardrail.startswith("denied"):
            continue
        start, top, step = mv.ladder
        out.append(Opening(mv.source, mv.ref, mv.rarity, mv.value, BidPlan(start, step, top), mv.reason, mv))
        taken.add(mv.source)
        items.add(mv.ref)  # two dealers may sell one card (level_ladder): one thread per card
    return out
