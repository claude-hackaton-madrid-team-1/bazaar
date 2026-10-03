"""Negotiate with a dealer (Abuela first), one move per tick, inside a hard price limit.

`decide()` holds every rule (its only side effects are on the negotiation: her asks, and our waits);
`negotiate()` runs it against the live thread. Rules learned from the feed (see `bazaar curves`): the
dealer only moves when we move, the same price twice earns nothing, small steps earn small steps, and
a `final` offer is take-it-or-walk. The ladder score is the share of the dealer's range we capture, and
a deal at her opening price scores nothing and unlocks nothing (RULES.md "Dealers"), so we NEVER close at
her opening ask, final or not: we take an ask only once she came down from it, and we counter below an
ask she has not lowered. When she holds it and no whole price is left below it (or her final is her
opening), we walk (`Move.reopen`) and the caller may open a new thread with a lower first bid
(`reopen_start`): her opening may anchor on our first bid.
Words persuade, structure binds: we read only the structured offers, never the dealer's text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from bazaar_agent.agents.words import WordsFn, WordsRequest

MoveKind = Literal["accept", "bid", "walk", "wait"]

KIND_WORDS = (
    "¡Buenas, {n}! Me haría mucha ilusión completar mi página. ¿Le parece bien {p} primas?",
    "Qué puesto tan bonito tiene. ¿Podríamos dejarlo en {p}?",
    "Gracias por su paciencia, {n}. Subo a {p}, ¿trato hecho?",
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
    reopen: bool = False  # a walk because she held her opening ask: try a new thread, lower (`reopen_start`)
    rest: bool = False  # a walk because she stopped answering: leave this item with her for a while


@dataclass
class Negotiation:
    plan: BidPlan
    bids: list[int] = field(default_factory=list)
    opening_ask: int | None = None  # the dealer's first structured ask we saw
    lowest_ask: int | None = None
    bids_at_opening: int = 0  # bids we had sent when her opening ask appeared; later ones are counters
    awaiting_reply: bool = False  # the thread's last message is ours: her answer to our last bid is not in yet
    waits: int = 0  # ticks we waited for her answer to our latest bid (`patient`)
    waited_after: int = 0  # how many bids we had sent when that count started

    def next_bid(self) -> int | None:
        """A strictly higher price than our last bid, capped at the limit; None when spent."""
        if not self.bids:
            return self.plan.start
        nxt = min(self.plan.max_price, self.bids[-1] + self.plan.step)
        return nxt if nxt > self.bids[-1] else None

    def see_ask(self, ask: int | None) -> None:
        """Note the dealer's standing ask. Idempotent: seeing the same ask twice changes nothing."""
        if ask is None:
            return
        if self.opening_ask is None:
            self.opening_ask, self.bids_at_opening = ask, len(self.bids)
        self.lowest_ask = ask if self.lowest_ask is None else min(self.lowest_ask, ask)

    @property
    def came_down(self) -> bool:
        """She lowered her ask below her opening at least once."""
        return self.opening_ask is not None and self.lowest_ask is not None and self.lowest_ask < self.opening_ask

    def bid_cap(self) -> int | None:
        """The highest bid that can never close at her opening price: one below it until she came down,
        then her lowest ask (a bid there closes below her opening). None before she named a price."""
        if self.opening_ask is None or self.lowest_ask is None:
            return None
        return self.lowest_ask if self.came_down else self.opening_ask - 1

    def may_take(self, ask: int) -> bool:
        """Her ask may be taken: it is below her opening ask, so the deal captures part of her range. At her
        opening price it would score nothing on the ladder and count nothing toward the next level."""
        return self.opening_ask is not None and ask < self.opening_ask


def counter_below(neg: Negotiation, ask: int) -> Move:
    """Her ask is inside what we would pay, but she has not come down from her opening yet: bid strictly
    below it (a bid at her ask would close at her opening price), and never above `bid_cap` (she may have
    raised her ask). The dealer matches our step, so we never step past it. When no whole price is left
    between our last bid and her ask, she has held her opening: walk, and let the caller reopen lower
    (taking it would score nothing)."""
    last = neg.bids[-1] if neg.bids else 0
    cap = neg.bid_cap()
    price = max(1, last + 1, ask - neg.plan.step)
    price = price if cap is None else min(price, cap)
    if price >= ask or price <= last:
        return held_walk(neg, f"she held her opening ask {ask}: no counter left below it")
    return Move("bid", price, reason=f"counter below her unconceded ask {ask}")


MAX_WAITS = 2  # the feed: every answered bid was answered within 0-1 tick; ~2 % of first bids never were


def patient(neg: Negotiation, waiting: bool, reason: str) -> Move | None:
    """A one-tick wait for her answer while `waiting` (her answer may be a "Deal!"), at most `MAX_WAITS`
    ticks in a row; None when we should act now."""
    if neg.waited_after != len(neg.bids):  # a new bid went out since: a fresh wait for her answer to it
        neg.waits, neg.waited_after = 0, len(neg.bids)
    if waiting and neg.waits < MAX_WAITS:
        neg.waits += 1
        return Move("wait", reason=reason)
    return None


def held_walk(neg: Negotiation, reason: str) -> Move:
    """She held her opening ask: walk and reopen lower. Unless her answer to our last bid is not in yet (it
    may be a "Deal!" at that bid): then wait for it, at most `MAX_WAITS` ticks."""
    wait = patient(neg, neg.awaiting_reply, "her answer to our last bid is not in yet")
    return wait or Move("walk", reason=reason, reopen=True)


def reopen_start(neg: Negotiation) -> int | None:
    """The first bid for a new thread after she held her opening ask: as far below our first bid as her
    opening sat above it (at least one step), since her opening may anchor on our first bid. None when
    there is nothing lower to try."""
    if neg.opening_ask is None:
        return None
    first = neg.bids[0] if neg.bids else neg.plan.start
    lower = max(1, first - max(neg.plan.step, neg.opening_ask - first))
    return lower if lower < first else None


def meet_ask(neg: Negotiation, ask: int | None) -> Move:
    """Our accept slot went elsewhere this tick: bid exactly her ask instead (a new, higher price inside our
    max), so the dealer can accept OUR offer; going silent would freeze the thread. Only for an ask we may
    take (`may_take`): her opening price never. Otherwise wait."""
    last = neg.bids[-1] if neg.bids else 0
    if ask is not None and neg.may_take(ask) and last < ask <= neg.plan.max_price:
        return Move("bid", ask, reason="accept slot used: meet her ask")
    return Move("wait", reason="accept slot used this tick")


def decide(neg: Negotiation, ask: int | None, offer_id: int | None, final: bool) -> Move:
    """The next move, given the dealer's latest open offer (None when it has none standing)."""
    neg.see_ask(ask)
    if ask is None and neg.bids and neg.opening_ask is None:  # a second bid before her first ask is blind
        wait = patient(neg, True, "waiting for her first ask")
        return wait or Move("walk", reason=f"no ask from her after {MAX_WAITS} ticks", rest=True)
    nxt = neg.next_bid()
    if ask is not None and offer_id is not None:
        if ask <= neg.plan.max_price and (final or nxt is None or ask <= nxt):
            if neg.may_take(ask):
                return Move("accept", ask, offer_id, "final within limit" if final else "ask meets our next bid")
            if final:
                return Move("walk", reason=f"her final {ask} is her opening price: it scores nothing", reopen=True)
            return counter_below(neg, ask)
        if final:
            return Move("walk", reason=f"final {ask} above our limit {neg.plan.max_price}")
    if nxt is None:  # our max is bid: her answer to it may still be a "Deal!"
        wait = patient(neg, neg.awaiting_reply, "her answer to our max bid is not in yet")
        return wait or Move("walk", reason="no higher bid left inside our limit")
    cap = neg.bid_cap()
    if cap is not None and nxt > cap:  # e.g. no ask of hers stands this tick: never bid up to her opening
        if cap > (neg.bids[-1] if neg.bids else 0):
            return Move("bid", cap, reason=f"capped below her opening ask {neg.opening_ask}")
        if neg.came_down:  # cap is her lowest ask, and our bid already meets it
            return Move("wait", reason=f"our bid stands at her lowest ask {neg.lowest_ask}")
        return held_walk(neg, f"she held her opening ask {neg.opening_ask}: no bid left below it")
    return Move("bid", nxt, reason="small distinct step up")


def bid_schedule(plan: BidPlan) -> list[int]:
    """Every bid of the ladder, from `start` to the hard max: the dry run of one negotiation (live, a bid
    after the first waits for her answer and stays below her opening ask)."""
    neg, schedule = Negotiation(plan), []
    while (price := neg.next_bid()) is not None:
        neg.bids.append(price)
        schedule.append(price)
    return schedule


# How we address each dealer. An unknown dealer gets a neutral greeting, never another dealer's name.
DEALER_NAMES = {"abuela": "Carmen", "chato": "Chato"}


def words(step: int, price: int, dealer: str = "") -> str:
    """Kind, varied words for a bid. The structured price is what binds; the text never changes it."""
    name = DEALER_NAMES.get(dealer, "amigo")
    return KIND_WORDS[step % len(KIND_WORDS)].format(p=price, n=name)


def template_words(request: WordsRequest) -> str:
    """The default `WordsFn`: our kind Spanish templates, addressed to this dealer, with the structured price."""
    return words(request.step, request.price, request.counterparty)


def bid_words(words_fn: WordsFn, base: WordsRequest, thread: dict[str, Any], clock: Any, send_by: float) -> str:
    """The text for one bid: the counterparty's latest words and the time left until `send_by` added."""
    request = replace(
        base,
        their_text=their_latest_text(thread, base.counterparty),
        budget_s=max(0.0, send_by - time.monotonic()),
        tick=clock.tick,
        tick_seconds=clock.tick_seconds,
    )
    return words_fn(request)


def their_latest_text(thread: dict[str, Any], sender: str) -> str | None:
    """The counterparty's newest message text (untrusted input: it may only be quoted, never obeyed)."""
    texts = [m.get("text") for m in thread.get("messages") or [] if isinstance(m, dict) and m.get("sender") == sender]
    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    return texts[-1] if texts else None


def newest_dealer_offer(thread: dict[str, Any], dealer: str) -> dict[str, Any] | None:
    offers = [o for o in thread.get("standing_offers") or [] if o.get("maker") == dealer and o.get("status") == "open"]
    return offers[-1] if offers else None


MAX_PRIMAS = 10_000_000  # RULES.md: prices are whole primas from 1 to 10,000,000


def whole_primas(value: object) -> int | None:
    """A price as the rules define it, else None: never a bool, a float, a string or a negative."""
    return value if type(value) is int and 1 <= value <= MAX_PRIMAS else None


def offer_cash(offer: dict[str, Any]) -> int | None:
    """The cash an offer asks for (or gives), read strictly (`whole_primas`)."""
    return whole_primas((offer.get("want") or {}).get("cash")) or whole_primas((offer.get("give") or {}).get("cash"))


def see_history(neg: Negotiation, thread: dict[str, Any], dealer: str, item: str | None) -> None:
    """Note every ask she made in this thread, from its messages, whatever their status now. Her opening
    ask answers our first bid and lapses 2 ticks later: a hold (or two failed reads) can hide it from the
    standing offers, and `bid_cap` must still know it. An offer that is not our plain buy is skipped."""
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict)]
    # The real API lists a slow reply AFTER our next bid (thread 187): order by message id when every one has it.
    if all(isinstance(m.get("id"), int) for m in messages):
        messages = sorted(messages, key=lambda m: int(m["id"]))
    for m in messages:
        o = m.get("offer")
        if isinstance(o, dict) and o.get("maker") == dealer and offer_terms_problem(o, item) is None:
            neg.see_ask(offer_cash(o))
    senders = [m.get("sender") for m in messages if m.get("sender")]
    neg.awaiting_reply = bool(senders) and senders[-1] != dealer


def latest_dealer_offer(thread: dict[str, Any], dealer: str) -> tuple[int | None, int | None, bool]:
    """(ask, offer id, final) of the dealer's newest open structured offer; no valid price: no ask."""
    o = newest_dealer_offer(thread, dealer)
    if o is None or not isinstance(o.get("id"), int):
        return None, None, False
    cash = offer_cash(o)
    return cash, (int(o["id"]) if cash is not None else None), bool(o.get("final"))


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
Guard = Callable[[Move, int], str | None]  # (move, our thread id) → a deny reason, or None when allowed
Reserve = Callable[[Move, Any], bool]  # (accept, the clock it is sent on) → True when the team's accept slot is ours
DealHook = Callable[[int, int, float], None]  # (price, tick, t_hours) once a deal settles
KillSwitch = Callable[[], Sequence[str]]  # why every write is refused right now (empty: off)


def apply_advice(move: Move, advice: str | None, neg: Negotiation, ask: int | None, offer_id: int | None) -> Move:
    """Jev may make us accept earlier (still inside the limit) or keep bidding; it never lifts the limit
    and never takes her opening ask (`Negotiation.may_take`)."""
    ready = advice == "accept" and move.kind == "bid" and ask is not None and offer_id is not None
    if ready and ask is not None and neg.may_take(ask) and ask <= neg.plan.max_price:
        return Move("accept", ask, offer_id, "jev: accept (inside limit)")
    return move


@dataclass(frozen=True)
class Outcome:
    thread: int | None
    status: str
    price: int | None
    bids: tuple[int, ...]
    ticks: int
    reopen_start: int | None = None  # walked because she held her opening ask: a lower first bid to try


def settled_price(thread: dict[str, Any]) -> int | None:
    """The price of the thread's settled (or accepted) offer, read from its messages: what the deal cost,
    whoever accepted (a dealer that takes our bid settles it at our price)."""
    for m in reversed(thread.get("messages") or []):
        o = m.get("offer") if isinstance(m, dict) else None
        if isinstance(o, dict) and o.get("status") in ("settled", "accepted"):
            return offer_cash(o)
    return None


class Observer:
    """Watches one negotiation and never changes a move. This base does nothing (tracing off);
    `traces.NegotiationTrace` overrides the hooks to send every step to Phoenix."""

    def opened(self, thread_id: int) -> None:
        """The thread exists."""

    def wrap_tick(self, on_tick: Callable[[Any], None]) -> Callable[[Any], None]:
        return on_tick

    def thread_read(self, thread: dict[str, Any]) -> None:
        """The thread as read this tick: messages, standing offers, status."""

    def guardrail(self, move: Move, denied: str | None) -> None:
        """A guard verdict on a move (denied is None when allowed)."""

    def move(self, move: Move, said: str | None) -> None:
        """The move we are about to send, and the words with it."""

    def refused(self, error: Exception) -> None:
        """The server refused our move."""

    def finished(self, outcome: Outcome) -> None:
        """The negotiation ended."""


class _SafeObserver(Observer):
    """Runs every hook of a real observer but swallows its failures: tracing never breaks a deal."""

    def __init__(self, inner: Observer, log: Callable[[str], None]) -> None:
        self._inner, self._log, self._warned = inner, log, False

    def _call(self, name: str, *args: Any) -> None:
        try:
            getattr(self._inner, name)(*args)
        except Exception as e:  # observability must never change the negotiation
            if not self._warned:
                self._log(f"tracing hook {name} failed ({type(e).__name__}); negotiation continues")
                self._warned = True

    def opened(self, thread_id: int) -> None:
        self._call("opened", thread_id)

    def wrap_tick(self, on_tick: Callable[[Any], None]) -> Callable[[Any], None]:
        try:
            return self._inner.wrap_tick(on_tick)
        except Exception:
            return on_tick

    def thread_read(self, thread: dict[str, Any]) -> None:
        self._call("thread_read", thread)

    def guardrail(self, move: Move, denied: str | None) -> None:
        self._call("guardrail", move, denied)

    def move(self, move: Move, said: str | None) -> None:
        self._call("move", move, said)

    def refused(self, error: Exception) -> None:
        self._call("refused", error)

    def finished(self, outcome: Outcome) -> None:
        self._call("finished", outcome)


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
    observer: Observer | None = None,
    words_fn: WordsFn = template_words,
    reserve: Reserve | None = None,
    kill_switch: KillSwitch | None = None,
) -> Outcome:
    """Open one thread and play it out, one move per tick. Returns when it closes or times out.

    `words_fn` writes each bid's text (the templates by default, or the runtime LLM); the price is
    always the structured `price` of the message, set here. `guard(move, thread id)` may deny a bid or an
    accept (the move becomes a walk). `reserve` claims the team's accept slot on the same tick the accept
    is sent; a slot already taken means we bid her ask instead (`meet_ask`), never walk.

    `kill_switch` on means HOLD: reads go on, nothing is sent (no bid, accept, walk or close), the thread
    stays open, and a held tick does not count toward `max_ticks`, so the negotiation resumes where it
    was when the switch goes off. It is read again just before every send. Any other guard denial still
    turns the move into a walk. A walk because she held her opening ask returns `Outcome.reopen_start`.
    """
    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

    sleep = sleep or time.sleep
    obs: Observer = _SafeObserver(observer, log) if observer is not None else Observer()

    neg = Negotiation(plan)
    item = requested_item(topic)
    if kill_switch is not None and (stops := kill_switch()):  # opening a thread is a write too
        log(f"kill switch on: no thread opened with {dealer} ({'; '.join(stops)})")
        return Outcome(None, "held", None, (), 0)
    opened = client.open_thread(dealer, topic=topic)
    tid = int(opened["id"])
    obs.opened(tid)
    log(f"thread {tid} opened with {dealer}: {topic} · plan {plan}")
    state: dict[str, Any] = {
        "status": "open",
        "price": None,
        "ticks": 0,
        "held": 0,
        "accepted": False,
        "reopen": None,
        "clock": None,
    }

    def holding(when: str) -> bool:
        stops = kill_switch() if kill_switch is not None else ()
        if stops:
            log(f"{when}: kill switch on: holding, nothing sent, thread {tid} stays open ({'; '.join(stops)})")
        return bool(stops)

    def hold(when: str) -> bool:
        """Inside a tick: the switch is on, so this tick is held (it does not count toward max_ticks)."""
        if not holding(when):
            return False
        state["held"] += 1
        return True

    def ended(thread: dict[str, Any], clock: Clock) -> bool:
        """Take the thread's status; when it ended, say so and book a deal's price (`on_deal`)."""
        state["status"] = thread.get("status", "open")
        if state["status"] == "open":
            return False
        log(f"tick {clock.tick}: thread {state['status']} ({thread.get('closed_reason') or '-'})")
        if state["status"] == "deal":
            state["price"] = settled_price(thread) or state["price"] or (neg.bids[-1] if neg.bids else None)
            if on_deal is not None and state["price"] is not None:
                on_deal(int(state["price"]), clock.tick, clock.t_hours)
        return True

    def reread(clock: Clock) -> None:
        """One more read of our thread, outside a tick's flow: a deal that landed is booked. Nothing here may
        crash the caller: an unreadable thread or a failed booking is said loudly, with the thread id."""
        try:
            thread = client.thread(tid)
        except BazaarError as e:
            log(f"thread {tid} unreadable ({e.code}): check it by hand, a deal there would be unbooked")
            return
        try:
            ended(thread, clock)
        except Exception as e:  # on_deal's ledger write failed: the deal is done, its spend is not booked
            log(f"thread {tid}: deal at {state['price']} P done but NOT booked ({type(e).__name__}): book it by hand")

    def close(outcome: str, clock: Clock) -> str:
        """Close our thread; the status it ends in. A refused close, or one answered with an ended thread (our
        simulator answers 200 {"status": "deal"}), may hide a "Deal!" that landed since our last read: read
        the thread again and book it. A rate limit sends nothing more now: the thread stays open."""
        try:
            answer = client.close_thread(tid)
        except BazaarError as e:
            log(f"tick {clock.tick}: close of thread {tid} refused ({e.code})")
            if e.code in ("rate_limited", "wait_for_tick", "too_many_requests"):
                return "open"
        else:
            status = answer.get("status") if isinstance(answer, dict) else None
            if status in (None, "closed", "walked", outcome):
                return outcome
            log(f"tick {clock.tick}: close of thread {tid} answered {status}: reading it again")
        reread(clock)
        return str(state["status"])

    def retry_close_next_tick() -> None:
        """One more timeout close, on the NEXT tick (a 429 names it), never the same one. Bounded: no wait
        while the doors are closed or the clock is paused (no overnight block), at most a few reads; held
        (one read, nothing sent) under the kill switch."""
        from bazaar_agent.ticks import seconds_until_next_tick

        try:
            first = Clock.model_validate(client.clock())
            now = first
            for _ in range(5):
                if not now.is_live or now.tick > first.tick:
                    break
                sleep(seconds_until_next_tick(now))
                now = Clock.model_validate(client.clock())
        except BazaarError as e:
            log(f"thread {tid}: clock unreadable ({e.code}), no second close")
            return
        if not now.is_live or now.tick <= first.tick:
            log(f"thread {tid}: no live tick for a second close ({now.doors}, paused={now.paused})")
            return
        if holding(f"tick {now.tick}, before closing thread {tid} again"):
            reread(now)  # a "Deal!" that landed is booked; otherwise the thread stays open, never closed
            if state["status"] == "open":
                state["status"] = "held"
            return
        state["status"] = close("timeout", now)

    def on_tick(clock: Clock) -> None:
        state["clock"] = clock
        if state["status"] != "open":
            return
        state["ticks"] += 1
        thread = client.thread(tid)
        obs.thread_read(thread)
        if ended(thread, clock):
            return
        if hold(f"tick {clock.tick}"):  # a held tick does not count toward max_ticks
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
        see_history(neg, thread, dealer, item)  # her opening ask, even if it lapsed while we held
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
            denied = guard(move, tid)
            obs.guardrail(move, denied)
            if denied and hold(f"tick {clock.tick}"):  # the switch went on mid-tick: hold, never walk
                return
            if denied:
                log(f"tick {clock.tick}: GUARDRAIL denied {move.kind} {move.price}: {denied} → walk")
                move = Move("walk", reason=f"guardrail: {denied}")
        if move.kind == "walk" and hold(f"tick {clock.tick}"):
            return
        send_by = 0.0  # monotonic deadline for the send, set when the clock is re-read
        if move.kind in ("accept", "bid"):
            fresh = Clock.model_validate(client.clock())  # the thread read and Jev may have used the tick
            if fresh.tick != clock.tick or action_budget_s(fresh) <= 0:
                log(f"tick {clock.tick}: tick budget spent before sending, re-deciding next tick")
                return
            send_by = time.monotonic() + action_budget_s(fresh)
            if move.kind == "accept" and hold(f"tick {clock.tick}, before reserving the accept slot"):
                return  # never take the team's accept slot (the duel player's too) while the switch is on
            if move.kind == "accept" and reserve is not None and not reserve(move, fresh):
                move = meet_ask(neg, ask)  # same price the guard allowed: the dealer may accept OUR offer
                log(f"tick {fresh.tick}: the team's accept slot is taken this tick → {move.kind} {move.price or ''}")
                if move.kind != "bid":
                    return
        text = None
        if move.kind == "bid" and move.price is not None:
            text = bid_words(words_fn, WordsRequest(dealer, move.price, len(neg.bids), item), thread, clock, send_by)
            if time.monotonic() > send_by:
                log(f"tick {clock.tick}: the words took the rest of the tick, re-deciding next tick")
                return
        if hold(f"tick {clock.tick}, before sending {move.kind}"):  # it may have gone on while we decided
            return
        obs.move(move, text)
        try:
            if move.kind == "accept" and move.offer_id is not None:
                client.accept(move.offer_id)
                state["accepted"], state["price"] = True, move.price
            elif move.kind == "bid" and move.price is not None and text is not None:
                client.say(tid, text, price=move.price)
                neg.bids.append(move.price)
            elif move.kind == "walk":
                state["status"] = close("walked", clock)
                if state["status"] in ("walked", "closed"):
                    state["reopen"] = reopen_start(neg) if move.reopen else None
        except BazaarError as e:
            obs.refused(e)
            log(f"tick {clock.tick}: refused {e.code} ({e.message[:80]}), retry next tick")

    tick = obs.wrap_tick(on_tick)
    run_per_tick(
        client.clock,
        tick,
        stop=lambda: state["status"] != "open" or state["ticks"] - state["held"] >= max_ticks,
        sleep=sleep,
    )
    if state["status"] == "open" and state["accepted"]:
        # Our accept settles on the next tick: wait for it, never close an accepted deal as a timeout.
        run_per_tick(client.clock, tick, max_ticks=2, stop=lambda: state["status"] != "open", sleep=sleep)
        if state["status"] == "open":
            state["status"] = "accepted_pending"
    last: Clock | None = state["clock"]  # on_deal's tick and game hour: the last tick we handled
    if last is None:
        try:
            last = Clock.model_validate(client.clock())
        except BazaarError:
            last = Clock(tick=0)
    if state["status"] == "open" and holding(f"after {max_ticks} ticks"):
        reread(last)  # a "Deal!" may have landed while we held: book it; the thread stays open otherwise
        state["status"] = "held" if state["status"] == "open" else state["status"]
    elif state["status"] == "open":
        state["status"] = close("timeout", last)
        if state["status"] == "open":  # refused (a rate limit) and still open: one more try on the NEXT tick
            retry_close_next_tick()
        if state["status"] == "open":
            standing = neg.bids[-1] if neg.bids else "-"
            log(f"thread {tid} is still open with our bid {standing} standing: close it by hand")
    outcome = Outcome(tid, str(state["status"]), state["price"], tuple(neg.bids), int(state["ticks"]), state["reopen"])
    obs.finished(outcome)
    return outcome
