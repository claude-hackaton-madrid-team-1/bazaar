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


def newest_dealer_offer(thread: dict[str, Any], dealer: str) -> dict[str, Any] | None:
    offers = [o for o in thread.get("standing_offers") or [] if o.get("maker") == dealer and o.get("status") == "open"]
    return offers[-1] if offers else None


def latest_dealer_offer(thread: dict[str, Any], dealer: str) -> tuple[int | None, int | None, bool]:
    """(ask, offer id, final) of the dealer's newest open structured offer."""
    o = newest_dealer_offer(thread, dealer)
    if o is None:
        return None, None, False
    cash = (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash")
    return (int(cash) if cash else None), int(o["id"]), bool(o.get("final"))


def requested_item(topic: dict[str, Any]) -> str | None:
    """'LAV-03' or 'sobre_barrio' for a buy topic; None when the topic is not a plain buy."""
    spec = topic.get("buy") if isinstance(topic.get("buy"), dict) else None
    return str(spec.get("card") or spec.get("pack")) if spec and (spec.get("card") or spec.get("pack")) else None


def offer_terms_problem(offer: dict[str, Any], item: str | None) -> str | None:
    """Why accepting this whole dealer offer would not be the trade we asked for (None = it is).

    We accept an offer by id, which binds BOTH sides. A buy must give us the requested item and
    nothing of ours may be in `want` except cash. Words persuade, structure binds.
    """
    give, want = offer.get("give") or {}, offer.get("want") or {}
    if want.get("assets") or want.get("types") or want.get("cards"):
        return "the offer also asks for our assets"
    if give.get("cash"):
        return "the offer gives cash on a buy"
    if item is None:
        return None
    refs = [str(t).split(":", 1)[-1] for t in give.get("types") or []]
    refs += [str(a.get("ref")) for a in give.get("assets") or [] if isinstance(a, dict)]
    if refs != [item]:
        return f"the offer gives {refs or 'nothing'} instead of exactly [{item}]"
    return None


Advisor = Callable[[Negotiation, int | None, bool], str | None]
Guard = Callable[[Move], str | None]  # returns a deny reason, or None when the move is allowed
DealHook = Callable[[int, int, float], None]  # (price, tick, t_hours) once a deal settles


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
    guard: Guard | None = None,
    on_deal: DealHook | None = None,
) -> Outcome:
    """Open one thread and play it out, one move per tick. Returns when it closes or times out."""
    import time

    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

    sleep = sleep or time.sleep

    neg = Negotiation(plan)
    item = requested_item(topic)
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
            if state["status"] == "deal":
                state["price"] = state["price"] or (neg.bids[-1] if neg.bids else None)
                if on_deal is not None and state["price"] is not None:
                    on_deal(int(state["price"]), clock.tick, clock.t_hours)
            return
        if state["accepted"]:
            log(f"tick {clock.tick}: accepted, waiting for settlement")
            return
        ask, offer_id, final = latest_dealer_offer(thread, dealer)
        newest = newest_dealer_offer(thread, dealer)
        problem = offer_terms_problem(newest, item) if newest is not None else None
        if problem:
            log(f"tick {clock.tick}: ignoring offer {offer_id}: {problem}")
            ask, offer_id, final = None, None, False
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
        if guard is not None and move.kind in ("accept", "bid"):
            denied = guard(move)
            if denied:
                log(f"tick {clock.tick}: GUARDRAIL denied {move.kind} {move.price}: {denied} → walk")
                move = Move("walk", reason=f"guardrail: {denied}")
        if move.kind in ("accept", "bid"):
            fresh = Clock.model_validate(client.clock())  # the thread read and Jev may have used the tick
            if fresh.tick != clock.tick or action_budget_s(fresh) <= 0:
                log(f"tick {clock.tick}: tick budget spent before sending, re-deciding next tick")
                return
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
    if state["status"] == "open" and state["accepted"]:
        # Our accept settles on the next tick: wait for it, never close an accepted deal as a timeout.
        run_per_tick(client.clock, on_tick, max_ticks=2, stop=lambda: state["status"] != "open", sleep=sleep)
        if state["status"] == "open":
            state["status"] = "accepted_pending"
    if state["status"] == "open":
        client.close_thread(tid)
        state["status"] = "timeout"
    return Outcome(tid, str(state["status"]), state["price"], tuple(neg.bids), int(state["ticks"]))
