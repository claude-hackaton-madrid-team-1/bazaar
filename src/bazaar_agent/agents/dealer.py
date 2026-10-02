"""Negotiate with a dealer (Abuela first), one move per tick, inside a hard price limit.

`decide()` is pure and holds every rule; `negotiate()` runs it against the live thread.
Rules learned from the feed (see `bazaar curves`): the dealer only moves when we move, the same
price twice earns nothing, small steps earn small steps, and a `final` offer is take-it-or-walk.
Words persuade, structure binds: we read only the structured offers, never the dealer's text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

MoveKind = Literal["accept", "bid", "walk", "wait"]

KIND_WORDS = (
    "¡Buenas, Carmen! Me haría mucha ilusión completar mi página. ¿Le parece bien {p} primas?",
    "Qué puesto tan bonito tiene. ¿Podríamos dejarlo en {p}?",
    "Gracias por su paciencia, Carmen. Subo a {p}, ¿trato hecho?",
    "Es usted un encanto. {p} primas y me lo llevo con mucho cariño.",
    "Mi abuela también vendía en el Rastro. ¿{p} le parece justo?",
    "Le prometo cuidarlo mucho. ¿Cerramos en {p}?",
)


@dataclass(frozen=True)
class BidPlan:
    """Our side of one conversation. `max_price` is the hard limit: never pay above it."""

    start: int
    step: int
    max_price: int

    def __post_init__(self) -> None:
        if not 1 <= self.start <= self.max_price or self.step < 1:
            raise ValueError(f"bad plan: start={self.start} step={self.step} max={self.max_price}")


@dataclass(frozen=True)
class Move:
    kind: MoveKind
    price: int | None = None
    offer_id: int | None = None
    reason: str = ""


@dataclass
class Negotiation:
    plan: BidPlan
    bids: list[int] = field(default_factory=list)

    def next_bid(self) -> int | None:
        """A strictly higher price than our last bid, capped at the limit; None when spent."""
        if not self.bids:
            return self.plan.start
        nxt = min(self.plan.max_price, self.bids[-1] + self.plan.step)
        return nxt if nxt > self.bids[-1] else None


def decide(neg: Negotiation, ask: int | None, offer_id: int | None, final: bool) -> Move:
    """The next move, given the dealer's latest open offer (None when it has none standing)."""
    nxt = neg.next_bid()
    if ask is not None and offer_id is not None:
        if ask <= neg.plan.max_price and (final or nxt is None or ask <= nxt):
            return Move("accept", ask, offer_id, "final within limit" if final else "ask meets our next bid")
        if final:
            return Move("walk", reason=f"final {ask} above our limit {neg.plan.max_price}")
    if nxt is None:
        return Move("walk", reason="no higher bid left inside our limit")
    return Move("bid", nxt, reason="small distinct step up")


def words(step: int, price: int) -> str:
    return KIND_WORDS[step % len(KIND_WORDS)].format(p=price)


def latest_dealer_offer(thread: dict[str, Any], dealer: str) -> tuple[int | None, int | None, bool]:
    """(ask, offer id, final) of the dealer's newest open structured offer."""
    offers = [o for o in thread.get("standing_offers") or [] if o.get("maker") == dealer and o.get("status") == "open"]
    if not offers:
        return None, None, False
    o = offers[-1]
    cash = (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash")
    return (int(cash) if cash else None), int(o["id"]), bool(o.get("final"))


Advisor = Callable[[Negotiation, int | None, bool], str | None]


def apply_advice(move: Move, advice: str | None, neg: Negotiation, ask: int | None, offer_id: int | None) -> Move:
    """Jev may make us accept earlier (still inside the limit) or keep bidding; it never lifts the limit."""
    ready = advice == "accept" and move.kind == "bid" and ask is not None and offer_id is not None
    if ready and ask is not None and ask <= neg.plan.max_price:
        return Move("accept", ask, offer_id, "jev: accept (inside limit)")
    return move


@dataclass(frozen=True)
class Outcome:
    thread: int | None
    status: str
    price: int | None
    bids: tuple[int, ...]
    ticks: int


def negotiate(
    client: Any,
    dealer: str,
    topic: dict[str, Any],
    plan: BidPlan,
    *,
    log: Callable[[str], None],
    advisor: Advisor | None = None,
    max_ticks: int = 14,
    sleep: Callable[[float], None] | None = None,
) -> Outcome:
    """Open one thread and play it out, one move per tick. Returns when it closes or times out."""
    import time

    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

    sleep = sleep or time.sleep

    neg = Negotiation(plan)
    opened = client.open_thread(dealer, topic=topic)
    tid = int(opened["id"])
    log(f"thread {tid} opened with {dealer}: {topic} · plan {plan}")
    state: dict[str, Any] = {"status": "open", "price": None, "ticks": 0, "accepted": False}

    def on_tick(clock: Clock) -> None:
        if state["status"] != "open":
            return
        state["ticks"] += 1
        thread = client.thread(tid)
        state["status"] = thread.get("status", "open")
        if state["status"] != "open":
            log(f"tick {clock.tick}: thread {state['status']} ({thread.get('closed_reason') or '-'})")
            return
        if state["accepted"]:
            log(f"tick {clock.tick}: accepted, waiting for settlement")
            return
        ask, offer_id, final = latest_dealer_offer(thread, dealer)
        move = decide(neg, ask, offer_id, final)
        if advisor is not None and action_budget_s(clock) > 4.0:
            move = apply_advice(move, advisor(neg, ask, final), neg, ask, offer_id)
        if action_budget_s(clock) <= 0:
            log(f"tick {clock.tick}: no budget left in this tick, deciding next tick")
            return
        log(
            f"tick {clock.tick}: her ask {ask}{' FINAL' if final else ''} → {move.kind} {move.price or ''} "
            f"({move.reason})"
        )
        try:
            if move.kind == "accept" and move.offer_id is not None:
                client.accept(move.offer_id)
                state["accepted"], state["price"] = True, move.price
            elif move.kind == "bid" and move.price is not None:
                client.say(tid, words(len(neg.bids), move.price), price=move.price)
                neg.bids.append(move.price)
            elif move.kind == "walk":
                client.close_thread(tid)
                state["status"] = "walked"
        except BazaarError as e:
            log(f"tick {clock.tick}: refused {e.code} ({e.message[:80]}), retry next tick")

    run_per_tick(client.clock, on_tick, max_ticks=max_ticks, stop=lambda: state["status"] != "open", sleep=sleep)
    if state["status"] == "open":
        client.close_thread(tid)
        state["status"] = "timeout"
    return Outcome(tid, str(state["status"]), state["price"], tuple(neg.bids), int(state["ticks"]))
