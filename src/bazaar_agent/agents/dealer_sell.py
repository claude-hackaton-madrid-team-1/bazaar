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
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from bazaar_agent.agents.dealer import BidPlan, Move, Negotiation, Outcome, decide
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
    """decide()'s reason in real prices: every mirrored number back to primas, buy words to sale words."""
    reason = re.sub(r"\d+", lambda m: str(MIRROR - int(m[0])) if int(m[0]) > MIRROR // 2 else m[0], reason)
    return (
        reason.replace("small distinct step up", "small distinct step down")
        .replace("between our", "between our ask")
        .replace(" and her ", " and its bid ")
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


SELL_WORDS = (
    "¡Buenas, {n}! Le traigo un cromo muy bonito. ¿Le parece bien {p} primas?",
    "Gracias por atenderme, {n}. ¿Podríamos dejarlo en {p}?",
    "Es usted muy amable. Bajo a {p}, ¿trato hecho?",
    "Está en perfecto estado, {n}. {p} primas y es suyo.",
    "Mi abuela lo guardaba con cariño. ¿{p} le parece justo?",
    "Por usted, {p}. ¿Cerramos?",
)


def sell_words(step: int, price: int, dealer: str = "") -> str:
    """Kind, varied words for an ask. The structured price is what binds; the text never changes it."""
    from bazaar_agent.agents.dealer import DEALER_NAMES

    return SELL_WORDS[step % len(SELL_WORDS)].format(p=price, n=DEALER_NAMES.get(dealer, "amigo"))


def sell_terms_problem(offer: Mapping[str, Any], asset_id: int) -> str | None:
    """Why accepting this dealer offer would not be the sale we asked for (None = it is): it must want
    exactly our asset and give only cash. Words persuade, structure binds."""
    give, want = offer.get("give") or {}, offer.get("want") or {}
    wanted = [a.get("id") if isinstance(a, dict) else a for a in want.get("assets") or []]
    if wanted != [asset_id] or want.get("types") or want.get("cards") or want.get("cash"):
        return f"the offer wants {wanted or 'nothing'} instead of exactly our asset {asset_id}"
    if give.get("assets") or give.get("types") or give.get("cards"):
        return "the offer gives items on a sale"
    if not give.get("cash"):
        return "the offer pays no cash"
    return None


SellGuard = Callable[[Move], str | None]  # a deny reason for an ask or an accept, or None when allowed


def negotiate_sell(
    client: Any,
    dealer: str,
    asset_id: int,
    plan: AskPlan,
    *,
    log: Callable[[str], None],
    guard: SellGuard | None = None,
    max_ticks: int = 14,
    sleep: Callable[[float], None] | None = None,
    reserve: Callable[[Move, Any], bool] | None = None,
    on_thread: Callable[[dict[str, Any]], None] | None = None,
    on_deal: Callable[[int, int, float], None] | None = None,
) -> Outcome:
    """Open one sale thread and play it out, one move per tick (the mirror of `dealer.negotiate`).
    `guard` sees every ask and accept first (GUARDRAILS.md: sell_min_value_ratio, kill switch); a denied
    move walks. `reserve` claims the team's accept slot on the tick the accept is sent."""
    import time

    from bazaar_agent.agents.dealer import latest_dealer_offer, newest_dealer_offer
    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

    sleep = sleep or time.sleep
    sale = Sale(plan)
    topic = {"sell": {"assets": [asset_id]}}
    tid = int(client.open_thread(dealer, topic=topic)["id"])
    log(f"thread {tid} opened with {dealer}: sell asset {asset_id} · asks {plan.start}→{plan.min_price}")
    state: dict[str, Any] = {"status": "open", "price": None, "ticks": 0, "accepted": False}

    def on_tick(clock: Clock) -> None:
        if state["status"] != "open":
            return
        state["ticks"] += 1
        thread = client.thread(tid)
        if on_thread is not None:
            try:
                on_thread(thread)
            except Exception as e:  # inspection never changes or breaks the sale
                log(f"tick {clock.tick}: offer inspection failed ({type(e).__name__}); sale continues")
        state["status"] = thread.get("status", "open")
        if state["status"] != "open":
            log(f"tick {clock.tick}: thread {state['status']} ({thread.get('closed_reason') or '-'})")
            if state["status"] == "deal" and state["price"] is None and sale.asks:
                state["price"] = sale.asks[-1]  # it took our last ask
            if state["status"] == "deal" and on_deal is not None and state["price"] is not None:
                on_deal(int(state["price"]), clock.tick, clock.t_hours)
            return
        if state["accepted"]:
            log(f"tick {clock.tick}: accepted, waiting for settlement")
            return
        bid, offer_id, final = latest_dealer_offer(thread, dealer)
        newest = newest_dealer_offer(thread, dealer)
        problem = sell_terms_problem(newest, asset_id) if newest is not None else None
        if problem:
            log(f"tick {clock.tick}: ignoring offer {offer_id}: {problem}")
            bid, offer_id, final = None, None, False
        move = decide_sell(sale, bid, offer_id, final)
        log(
            f"tick {clock.tick}: its bid {bid}{' FINAL' if final else ''} → "
            f"{move.kind} {move.price or ''} ({move.reason})"
        )
        if guard is not None and move.kind in ("accept", "bid") and (denied := guard(move)):
            log(f"tick {clock.tick}: GUARDRAIL denied {move.kind} {move.price}: {denied} → walk")
            move = Move("walk", reason=f"guardrail: {denied}")
        if move.kind in ("accept", "bid"):
            fresh = Clock.model_validate(client.clock())
            if fresh.tick != clock.tick or action_budget_s(fresh) <= 0:
                log(f"tick {clock.tick}: tick budget spent before sending, re-deciding next tick")
                return
            if move.kind == "accept" and reserve is not None and not reserve(move, fresh):
                log(f"tick {fresh.tick}: the team's accept slot is taken this tick, trying again next tick")
                return
        try:
            if move.kind == "accept" and move.offer_id is not None:
                client.accept(move.offer_id)
                state["accepted"], state["price"] = True, move.price
            elif move.kind == "bid" and move.price is not None:
                client.say(tid, sell_words(len(sale.asks), move.price, dealer), price=move.price)
                sale.record(move.price)
            elif move.kind == "walk":
                client.close_thread(tid)
                state["status"] = "walked"
        except BazaarError as e:
            log(f"tick {clock.tick}: refused {e.code} ({e.message[:80]}), retry next tick")

    stop = lambda: state["status"] != "open"  # noqa: E731
    run_per_tick(client.clock, on_tick, max_ticks=max_ticks, stop=stop, sleep=sleep)
    if state["status"] == "open" and state["accepted"]:
        run_per_tick(client.clock, on_tick, max_ticks=2, stop=stop, sleep=sleep)
        if state["status"] == "open":
            state["status"] = "accepted_pending"
    if state["status"] == "open":
        client.close_thread(tid)
        state["status"] = "timeout"
    return Outcome(tid, str(state["status"]), state["price"], tuple(sale.asks), int(state["ticks"]))
