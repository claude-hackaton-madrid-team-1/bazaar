"""Sell one card TO a dealer, one move per tick, never below our floor.

The mirror of `dealer.negotiate`: we ask, the dealer bids (`give.cash`, `want.assets` = our copy). Our asks
step DOWN from `start` toward `floor`, always a new price (the same price twice earns nothing). A sale is a
dealer deal on the ladder, which scores the share of the dealer's range we capture, so we never close at her
OPENING bid: we take a bid only once she came up from it, and we never ask at or below an opening bid she has
not raised. Words persuade, structure binds: we read only the structured offers, never the dealer's text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.agents.dealer import (
    DEALER_NAMES,
    MAX_WAITS,
    DealHook,
    Guard,
    Hold,
    Inspect,
    KillSwitch,
    Move,
    Outcome,
    Reserve,
    newest_dealer_offer,
    settled_price,
    whole_primas,
    with_name,
)
from bazaar_agent.guardrails import OFF_PAGE_RARITIES

SELL_WORDS = (
    "¡Buenas, {n}! Le traigo un cromo precioso para su puesto. ¿Le parece bien {p} primas?",
    "Está como nuevo, {n}. ¿Lo dejamos en {p}?",
    "Por ser usted, bajo a {p}. ¿Trato hecho?",
    "Gracias por su paciencia, {n}. {p} primas y es suyo.",
    "Seguro que algún nieto lo busca. ¿{p} le parece justo?",
    "Le hago un buen precio: {p}. ¿Cerramos?",
)


class SellRefused(ValueError):
    """The sale is refused before any write: the reason is the message."""


@dataclass(frozen=True)
class AskPlan:
    """Our side of one sale. `floor` is the hard limit: we never ask, nor accept, below it."""

    start: int
    step: int
    floor: int

    def __post_init__(self) -> None:
        if not 1 <= self.floor <= self.start or self.step < 1:
            raise ValueError(f"bad plan: start={self.start} step={self.step} floor={self.floor}")


@dataclass
class SellNegotiation:
    plan: AskPlan
    asks: list[int] = field(default_factory=list)
    opening_bid: int | None = None  # the dealer's first structured bid we saw
    highest_bid: int | None = None
    awaiting_reply: bool = False  # the thread's last message is ours
    waits: int = 0
    waited_after: int = 0
    bids_by_offer: dict[int, int] = field(default_factory=dict)  # her structured bids, by offer id
    hold_offer: int | None = None  # the rising bid we hold our floor ask against
    holds: int = 0  # ticks held on `hold_offer`

    def see_bid(self, bid: int | None, offer_id: int | None = None) -> None:
        if bid is None:
            return
        if offer_id is not None:
            self.bids_by_offer[offer_id] = bid
        if self.opening_bid is None:
            self.opening_bid = bid
        self.highest_bid = bid if self.highest_bid is None else max(self.highest_bid, bid)

    def rising(self, offer_id: int | None) -> bool:
        """Her bid in `offer_id` is above every bid she made before it (offer ids grow): she came up with it.
        A repeat of her bid, or a first bid, is not rising."""
        bid = self.bids_by_offer.get(offer_id) if offer_id is not None else None
        earlier = [b for oid, b in self.bids_by_offer.items() if offer_id is not None and oid < offer_id]
        return bid is not None and bool(earlier) and bid > max(earlier)

    @property
    def came_up(self) -> bool:
        return self.opening_bid is not None and self.highest_bid is not None and self.highest_bid > self.opening_bid

    def next_ask(self) -> int | None:
        """A strictly lower price than our last ask, never below the floor; None when spent."""
        if not self.asks:
            return self.plan.start
        nxt = max(self.plan.floor, self.asks[-1] - self.plan.step)
        return nxt if nxt < self.asks[-1] else None

    def ask_floor(self) -> int | None:
        """The lowest ask that can never close at her opening bid: one above it until she came up, then her
        highest bid. None before she named a price."""
        if self.opening_bid is None or self.highest_bid is None:
            return None
        return self.highest_bid if self.came_up else self.opening_bid + 1

    def may_take(self, bid: int) -> bool:
        """Her bid may be taken: inside our floor and above her opening (a deal at her opening scores nothing)."""
        return self.opening_bid is not None and bid > self.opening_bid and bid >= self.plan.floor


def _patient(neg: SellNegotiation, reason: str) -> Move | None:
    """A one-tick wait for her answer to our latest ask, at most `MAX_WAITS` ticks in a row."""
    if neg.waited_after != len(neg.asks):
        neg.waits, neg.waited_after = 0, len(neg.asks)
    if neg.awaiting_reply and neg.waits < MAX_WAITS:
        neg.waits += 1
        return Move("wait", reason=reason)
    return None


def _hold_at_floor(neg: SellNegotiation, offer_id: int | None) -> Move | None:
    """Our ask sits at our floor and her bid below it is still RISING (she came up with her newest offer): hold,
    at most `MAX_WAITS` ticks per rising offer, rather than walk from a final that may land at or above the floor
    (Sat 3 Oct: Pilar's SAL-10 finals 65, 68, 69, 71 against our walks 2-3 P from the floor). Our floor is already
    our last ask, and re-sending it is spam (Day-2 hint; RULES.md: "repeating the same price earns no concession"),
    so we wait. She repeated her bid, or held one rising bid `MAX_WAITS` ticks: None (walk)."""
    if neg.awaiting_reply or offer_id is None or not neg.rising(offer_id):
        return None
    if neg.hold_offer != offer_id:
        neg.hold_offer, neg.holds = offer_id, 0
    if neg.holds >= MAX_WAITS:
        return None
    neg.holds += 1
    bid = neg.bids_by_offer[offer_id]
    return Move("wait", reason=f"her bid {bid} is still rising: our floor ask {neg.plan.floor} stands")


def _counter_above(neg: SellNegotiation, bid: int) -> Move:
    """Her bid meets our next ask but she has not come up from her opening: ask strictly above it (an ask at
    her bid would close at her opening price) and strictly below our last ask. An opening bid at or above our
    `start` is countered one step above it, never walked from."""
    price = bid + neg.plan.step  # before our first ask (her opening ≥ our start) nothing caps it from above
    if neg.asks:
        price = min(neg.asks[-1] - 1, price)
    if price <= bid or price < neg.plan.floor:
        return _patient(neg, "her answer to our last ask is not in yet") or Move(
            "walk", reason=f"she held her opening bid {bid}: no ask left above it"
        )
    return Move("bid", price, reason=f"counter above her unraised bid {bid}")


TRICKSTER_KINDS = frozenset({"trickster"})  # `/api/dealers` kinds whose "final" is no limit (Los Pícaros: bad faith)


def is_trickster(kind: str | None) -> bool:
    return str(kind or "").strip().lower() in TRICKSTER_KINDS


def decide_sell(
    neg: SellNegotiation,
    bid: int | None,
    offer_id: int | None,
    final: bool,
    final_min: int = 0,
    kind: str = "dealer",
) -> Move:
    """The next move, given the dealer's newest open bid (None when none stands). A "bid" move is OUR ask.
    `final_min`: a FINAL below it walks even above our floor (`dealer_sell_final_min_first_ask_share`).
    `kind` (`/api/dealers`): a trickster's FINAL is no limit, so it reads as an ordinary bid (never taken, nor
    walked from, because it says final)."""
    final = final and not is_trickster(kind)
    neg.see_bid(bid, offer_id)
    if final and bid is not None and bid < final_min:
        return Move("walk", reason=f"her final {bid} is below {final_min} (share of our first ask)")
    if bid is None and neg.asks and neg.opening_bid is None:
        neg.awaiting_reply = True
        return _patient(neg, "waiting for her first bid") or Move("walk", reason="no bid from her", rest=True)
    nxt = neg.next_ask()
    if bid is not None and offer_id is not None:
        if bid >= neg.plan.floor and (final or nxt is None or bid >= nxt):
            if neg.may_take(bid):
                return Move("accept", bid, offer_id, "final above our floor" if final else "her bid meets our ask")
            if final:
                return Move("walk", reason=f"her final {bid} is her opening bid: it scores nothing")
            return _counter_above(neg, bid)
        if final:
            return Move("walk", reason=f"her final {bid} is below our floor {neg.plan.floor}")
    if nxt is None:  # our last ask is the floor
        return (
            _patient(neg, "her answer to our lowest ask is not in yet")
            or _hold_at_floor(neg, offer_id)
            or Move("walk", reason="no lower ask left above our floor")
        )
    low = neg.ask_floor()
    if low is not None and nxt < low:
        last = neg.asks[-1] if neg.asks else neg.plan.start + 1
        if low < last:
            return Move("bid", low, reason=f"kept above her opening bid {neg.opening_bid}")
        if neg.came_up:
            return Move("wait", reason=f"our ask stands at her highest bid {neg.highest_bid}")
        return _patient(neg, "her answer to our last ask is not in yet") or Move(
            "walk", reason=f"she held her opening bid {neg.opening_bid}: no ask left above it"
        )
    return Move("bid", nxt, reason="small distinct step down")


def ask_schedule(plan: AskPlan) -> list[int]:
    """Every ask of the ladder, from `start` down to the floor: the dry run of one sale."""
    neg, out = SellNegotiation(plan), []
    while (price := neg.next_ask()) is not None:
        neg.asks.append(price)
        out.append(price)
    return out


def sell_words(step: int, price: int, dealer: str = "", name: str | None = None) -> str:
    """Kind, varied words for an ask. The structured price is what binds; the text never changes it.
    `name` (the address from `dealer_memory.address_for`) overrides `DEALER_NAMES`."""
    name = DEALER_NAMES.get(dealer, "") if name is None else name
    return with_name(SELL_WORDS[step % len(SELL_WORDS)], price, name)


def sell_topic(asset_id: int) -> dict[str, Any]:
    """The dealer topic for selling one copy (openapi `Topic`: `{"sell": {"assets": [id]}}`)."""
    return {"sell": {"assets": [asset_id]}}


def sell_offer_problem(offer: Mapping[str, Any], asset_id: int) -> str | None:
    """Why accepting this dealer offer would not be the sale we asked for (None = it is): it must give cash
    only and want exactly our one copy, nothing else of ours."""
    give, want = offer.get("give") or {}, offer.get("want") or {}
    if give.get("assets") or give.get("types") or give.get("cards"):
        return "the offer gives items on a sale"
    if whole_primas(give.get("cash")) is None:
        return "the offer gives no valid cash"
    if want.get("cash") or want.get("types") or want.get("cards"):
        return "the offer also wants cash or other cards"
    wanted = [a.get("id") if isinstance(a, dict) else a for a in want.get("assets") or []]
    if wanted != [asset_id]:
        return f"the offer wants {wanted} instead of exactly [{asset_id}]"
    return None


def latest_dealer_bid(thread: dict[str, Any], dealer: str, asset_id: int) -> tuple[int | None, int | None, bool]:
    """(bid, offer id, final) of the dealer's newest open offer for our copy; anything else: no bid."""
    o = newest_dealer_offer(thread, dealer)
    if o is None or not isinstance(o.get("id"), int) or sell_offer_problem(o, asset_id) is not None:
        return None, None, False
    return whole_primas((o.get("give") or {}).get("cash")), int(o["id"]), bool(o.get("final"))


def see_bids(neg: SellNegotiation, thread: dict[str, Any], dealer: str, asset_id: int) -> None:
    """Note every bid she made in this thread (her opening may have lapsed) and whether we spoke last."""
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict)]
    if all(isinstance(m.get("id"), int) for m in messages):
        messages = sorted(messages, key=lambda m: int(m["id"]))
    for m in messages:
        o = m.get("offer")
        if isinstance(o, dict) and o.get("maker") == dealer and sell_offer_problem(o, asset_id) is None:
            oid = o.get("id")
            neg.see_bid(whole_primas((o.get("give") or {}).get("cash")), oid if isinstance(oid, int) else None)
    senders = [m.get("sender") for m in messages if m.get("sender")]
    neg.awaiting_reply = bool(senders) and senders[-1] != dealer


def dealer_buys(dealer: Mapping[str, Any], rarity: str | None, set_code: str | None = None) -> bool:
    """The dealer's menu (`GET /api/dealers`) buys this rarity in this set. Any kind of persona counts (Doña
    Pilar is a "collector"); a line's `rarity` may be one or a list, its `sets` "released" (any) or set codes."""
    for line in (dealer.get("menu") or {}).get("buys") or []:
        if not isinstance(line, dict):
            continue
        raw, sets = line.get("rarity"), line.get("sets")
        rarities: list[Any] = raw if isinstance(raw, list) else [raw]
        scope: list[Any] | None = (
            sets if isinstance(sets, list) else None if sets in (None, "released", "all") else [sets]
        )
        if rarity is not None and rarity in rarities and (scope is None or set_code in scope):
            return True
    return False


def dealer_refusal(dealer_id: str, personas: list[Any], me: Mapping[str, Any], copy: Mapping[str, Any]) -> str | None:
    """Why this dealer cannot buy this copy from us now (None = it can): not in play, not unlocked for us
    (`/api/me` `unlocked`), or its menu does not buy that rarity in that set."""
    found = next((p for p in personas if isinstance(p, dict) and p.get("id") == dealer_id), None)
    if found is None:
        return f"{dealer_id} is not among the dealers (GET /api/dealers)"
    if found.get("status", "active") != "active":
        return f"{dealer_id} is {found.get('status')}, not active yet"
    unlocked = me.get("unlocked")
    if isinstance(unlocked, list) and dealer_id not in unlocked:
        return f"{dealer_id} is not unlocked for us yet (/api/me unlocked: {unlocked})"
    rarity, code = copy.get("rarity"), copy.get("set") or str(copy.get("ref") or "").split("-", 1)[0]
    if not dealer_buys(found, rarity, code):
        return f"{dealer_id} does not buy {rarity} cards of {code} (GET /api/dealers menu.buys)"
    return None


def copy_to_sell(
    me: Mapping[str, Any],
    ref: str,
    listed: frozenset[int] = frozenset(),
    unnamed: int = 0,
    page_complete: bool | None = None,
) -> dict[str, Any]:
    """The copy of `ref` we lose least by selling, from /api/me, among the copies none of our open offers
    gives (`listed` asset ids; `unnamed`: listed assets whose card we cannot tell, counted against every card).
    Refused: no free copy, no `your_value` (no floor), or the last uncommitted copy of a page card (selling it
    while an ask of ours fills would open a hole in the album) unless its page is known incomplete
    (`page_complete` False: `page_complete()` under `protect_complete_pages_only`)."""
    cards = [a for a in me.get("assets") or [] if isinstance(a, dict) and a.get("kind", "card") == "card"]
    copies = [a for a in cards if a.get("ref") == ref and isinstance(a.get("id"), int)]
    if not copies:
        raise SellRefused(f"we hold no card {ref!r} (check `uv run bazaar status`)")
    free = [a for a in copies if a["id"] not in listed]
    if only_copy(ref, copies[0].get("rarity"), len(free) - unnamed, page_complete):
        raise SellRefused(
            f"{ref}: {len(free) - unnamed} free copies (not on our offers); never sell the last one of a page card"
        )
    valued = [a for a in free if isinstance(a.get("your_value"), int | float) and not isinstance(a["your_value"], bool)]
    if not valued:
        raise SellRefused(f"no free copy of {ref} has a your_value in /api/me: not pricing it blind")
    return min(valued, key=lambda a: (float(a["your_value"]), -int(a["id"])))


def only_copy(ref: str, rarity: Any, sellable: int, page_complete: bool | None = None) -> bool:
    """`sellable` copies of `ref` not committed to an offer of ours: one or none of a page card is never sold.
    `page_complete` False (its page is known incomplete, `page_complete()`): the single free copy may go too, as
    `protect_complete_pages_only` lets every other sale (Sun 4 Oct: Pilar, Chato and Banco buy no common, so
    without it no sale could ever fill their ladder levels); none free is still refused."""
    if page_complete is False:
        return sellable < 1
    return sellable <= 1 and str(rarity or "").lower() not in OFF_PAGE_RARITIES


def page_complete(me: Mapping[str, Any], ref: str, rules: Any) -> bool | None:
    """Whether `ref`'s page is complete in /me's album, under `protect_complete_pages_only` only. None (protect, as
    before) when that rule is off, the album is unread or the set is not one of its pages: fail closed."""
    if not getattr(rules, "protect_complete_pages_only", False):
        return None
    album = me.get("album")
    pages = album.get("pages") if isinstance(album, Mapping) else None
    code = ref.split("-", 1)[0].strip().upper()
    for p in pages if isinstance(pages, list) else []:
        if isinstance(p, Mapping) and str(p.get("set") or "").upper() == code:
            return p.get("complete") is True
    return None


def check_floor(floor: int, your_value: float) -> None:
    if floor < your_value:
        raise SellRefused(f"--min {floor} is below the copy's your_value {your_value:g}: never sell under it")


MoveHook = Callable[[Move, int, str], None]  # (move, tick, outcome: sent | refused | denied | held) for the log


def negotiate_sell(
    client: Any,
    dealer: str,
    asset_id: int,
    plan: AskPlan,
    *,
    log: Callable[[str], None],
    max_ticks: int = 14,
    sleep: Callable[[float], None] | None = None,
    guard: Guard | None = None,
    reserve: Reserve | None = None,
    inspect: Inspect | None = None,
    kill_switch: KillSwitch | None = None,
    on_deal: DealHook | None = None,
    on_move: MoveHook | None = None,
    on_thread: Callable[[dict[str, Any]], None] | None = None,
    kind: str = "dealer",
) -> Outcome:
    """Open one sell thread and play it out, one message per tick (`run_per_tick`). Returns when it closes
    or times out (then the thread is closed). The kill switch holds (nothing sent, the tick does not count),
    `guard` may deny an ask or an accept (the move becomes a walk), `Hold` from it or from `reserve` holds the
    tick, `inspect` (S1) may refuse an accept (no accept this tick, never a walk), and `reserve` claims the
    team's accept slot: a taken slot sends nothing this tick. `on_thread` (the inspector's would-flag log) sees
    each tick's thread first; it never changes the move and its failures are logged, not raised. `kind`: the
    dealer's `/api/dealers` kind (a trickster's final is read as an ordinary bid, `decide_sell`)."""
    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

    sleep = sleep or time.sleep
    note = on_move or (lambda move, tick, outcome: None)
    if kill_switch is not None and (stops := kill_switch()):
        log(f"kill switch on: no thread opened with {dealer} ({'; '.join(stops)})")
        return Outcome(None, "held", None, (), 0)
    neg = SellNegotiation(plan)
    tid = int(client.open_thread(dealer, topic=sell_topic(asset_id))["id"])
    log(f"thread {tid} opened with {dealer}: sell asset {asset_id} · plan {plan}")
    state: dict[str, Any] = {"status": "open", "price": None, "ticks": 0, "held": 0, "accepted": False, "clock": None}

    def holding(when: str) -> bool:
        stops = kill_switch() if kill_switch is not None else ()
        if stops:
            log(f"{when}: kill switch on: holding, nothing sent ({'; '.join(stops)})")
            state["held"] += 1
        return bool(stops)

    def ended(thread: dict[str, Any], clock: Clock) -> bool:
        state["status"] = thread.get("status", "open")
        if state["status"] == "open":
            return False
        log(f"tick {clock.tick}: thread {state['status']} ({thread.get('closed_reason') or '-'})")
        if state["status"] == "deal":
            state["price"] = settled_price(thread) or state["price"] or (neg.asks[-1] if neg.asks else None)
            if on_deal is not None and state["price"] is not None:
                on_deal(int(state["price"]), clock.tick, clock.t_hours)
        return True

    def close(outcome: str, clock: Clock) -> str:
        try:
            answer = client.close_thread(tid)
        except BazaarError as e:
            log(f"tick {clock.tick}: close of thread {tid} refused ({e.code})")
            return "open"
        status = answer.get("status") if isinstance(answer, dict) else None
        if status in (None, "closed", "walked", outcome):
            return outcome
        try:  # an ended thread may hide a "Deal!" that landed since our last read: book it
            ended(client.thread(tid), clock)
        except BazaarError as e:
            log(f"thread {tid} unreadable ({e.code}): check it by hand")
        return str(state["status"])

    def on_tick(clock: Clock) -> None:
        state["clock"] = clock
        if state["status"] != "open":
            return
        state["ticks"] += 1
        thread = client.thread(tid)
        if on_thread is not None:
            try:
                on_thread(thread)
            except Exception as e:  # inspection must never change or break the negotiation
                log(f"tick {clock.tick}: offer inspection failed ({type(e).__name__}); negotiation continues")
        if ended(thread, clock) or holding(f"tick {clock.tick}"):
            return
        if state["accepted"]:
            log(f"tick {clock.tick}: accepted, waiting for settlement")
            return
        bid, offer_id, final = latest_dealer_bid(thread, dealer, asset_id)
        see_bids(neg, thread, dealer, asset_id)
        move = decide_sell(neg, bid, offer_id, final, kind=kind)
        if action_budget_s(clock) <= 0:
            log(f"tick {clock.tick}: no budget left in this tick, deciding next tick")
            return
        verb = "ask" if move.kind == "bid" else move.kind
        log(f"tick {clock.tick}: her bid {bid}{' FINAL' if final else ''} → {verb} {move.price or ''} ({move.reason})")
        if inspect is not None and move.kind == "accept" and (refused := inspect(thread, move)):
            log(f"tick {clock.tick}: INSPECTOR refused the accept of offer {move.offer_id}: {refused}")
            note(move, clock.tick, f"denied: inspector: {refused}")
            return
        if guard is not None and move.kind in ("accept", "bid"):
            try:
                denied = guard(move, tid)
            except Hold as e:
                log(f"tick {clock.tick}: HOLD {verb} {move.price}: {e} → nothing sent")
                note(move, clock.tick, "held")
                return
            if denied:
                log(f"tick {clock.tick}: GUARDRAIL denied {verb} {move.price}: {denied} → walk")
                note(move, clock.tick, f"denied: {denied}")
                move = Move("walk", reason=f"guardrail: {denied}")
        if move.kind == "wait":
            return
        if move.kind in ("accept", "bid"):
            fresh = Clock.model_validate(client.clock())
            if fresh.tick != clock.tick or action_budget_s(fresh) <= 0:
                log(f"tick {clock.tick}: tick budget spent before sending, re-deciding next tick")
                return
            if move.kind == "accept" and reserve is not None:
                try:
                    slot = reserve(move, fresh)
                except Hold as e:
                    log(f"tick {clock.tick}: HOLD accept {move.price}: {e} → nothing sent")
                    return
                if not slot:
                    log(f"tick {clock.tick}: the team's accept slot is {'unreadable' if slot is None else 'taken'}")
                    return
        if holding(f"tick {clock.tick}, before sending {verb}"):
            return
        try:
            if move.kind == "accept" and move.offer_id is not None:
                client.accept(move.offer_id)
                state["accepted"], state["price"] = True, move.price
            elif move.kind == "bid" and move.price is not None:
                client.say(tid, sell_words(len(neg.asks), move.price, dealer), price=move.price)
                neg.asks.append(move.price)
            elif move.kind == "walk":
                state["status"] = close("walked", clock)
            note(move, clock.tick, "sent")
        except BazaarError as e:
            note(move, clock.tick, f"refused: {e.code}")
            log(f"tick {clock.tick}: refused {e.code} ({e.message[:80]}), retry next tick")

    run_per_tick(
        client.clock,
        on_tick,
        stop=lambda: state["status"] != "open" or state["ticks"] - state["held"] >= max_ticks,
        sleep=sleep,
    )
    if state["status"] == "open" and state["accepted"]:  # our accept settles on the next tick
        run_per_tick(client.clock, on_tick, max_ticks=2, stop=lambda: state["status"] != "open", sleep=sleep)
        if state["status"] == "open":
            state["status"] = "accepted_pending"
    last: Clock = state["clock"] or Clock(tick=0)
    if state["status"] == "open" and not (kill_switch is not None and kill_switch()):
        state["status"] = close("timeout", last)
        if state["status"] == "open":
            asks = neg.asks[-1] if neg.asks else "-"
            log(f"thread {tid} is still open with our ask {asks} standing: close it by hand")
    elif state["status"] == "open":
        state["status"] = "held"
    return Outcome(tid, str(state["status"]), state["price"], tuple(neg.asks), int(state["ticks"]))
