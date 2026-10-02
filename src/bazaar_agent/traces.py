"""The span model: what a human sees in Phoenix for each thing our runtime does.

- `negotiation` (AGENT): one trace per dealer thread. Each tick is a `tick N` child (CHAIN) whose
  events tell the tick's story in order: `message` (every new line in the thread, both sides),
  `dealer_offer`, `jev_verdict`, `guardrail`, `our_move`, `log`, and `exception` on a refusal. The
  tick's input is what the dealer said, its output what we did; the root's output is the transcript.
- `duel` (AGENT): one trace per duel id, a `duel tick N` child for every tick it is live.
- `thread.view` (CHAIN): one span per `bazaar thread <id>`, the whole conversation as events.

Tick spans land in Phoenix as each tick ends; a root lands when its negotiation or duel ends.
Every hook is wrapped by `never_raise`: a telemetry bug can never change or stop a trade.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.dealer import BidPlan, Move, Observer, Outcome, latest_dealer_offer
from bazaar_agent.conversation import Line, Thread, header, lines, topic_ref


def message_event(line: Line) -> dict[str, object]:
    return {
        "message.id": line.id,
        "tick": line.tick,
        "sender": line.sender,
        "text": line.text,
        "price": line.price,
        "offer_id": line.offer_id,
        "offer_status": line.offer_status,
        "final": line.final,
    }


class NegotiationTrace(Observer):
    """`dealer.Observer` for one negotiation: tick spans under the root, an event for every step."""

    def __init__(self, root: Span, dealer: str) -> None:
        self._root, self._dealer = root, dealer
        self._seen: set[tuple[object, ...]] = set()
        self._transcript: list[str] = []
        self._refusals = 0

    @tm.never_raise
    def opened(self, thread_id: int) -> None:
        tm.set_attributes(self._root, {"bazaar.thread.id": thread_id})

    def wrap_tick(self, on_tick: Callable[[Any], None]) -> Callable[[Any], None]:
        def traced(clock: Any) -> None:
            values = {
                "bazaar.tick": clock.tick,
                "bazaar.t_hours": clock.t_hours,
                "bazaar.next_tick_in": clock.next_tick_in,
            }
            with tm.span(f"tick {clock.tick}", tm.CHAIN, values):
                on_tick(clock)

        return traced

    @tm.never_raise
    def thread_read(self, thread: dict[str, Any]) -> None:
        current = trace.get_current_span()
        view = Thread.model_validate(thread)
        fresh = [line for line in lines(view) if line.key not in self._seen]
        for line in fresh:
            self._seen.add(line.key)
            self._transcript.append(line.transcript())
            tm.add_event(current, "message", message_event(line))
        heard = "\n".join(line.transcript() for line in fresh if line.sender == self._dealer)
        tm.set_attributes(current, {"bazaar.thread.status": view.status, tm.INPUT: heard or None})
        if view.item:
            tm.set_attributes(self._root, {"bazaar.item.name": view.item})
        ask, offer_id, final = latest_dealer_offer(thread, self._dealer)
        if offer_id is not None:
            tm.add_event(current, "dealer_offer", {"ask": ask, "final": final, "offer_id": offer_id})
        if view.status != "open":
            tm.add_event(current, "thread_closed", {"status": view.status, "closed_reason": view.closed_reason})

    @tm.never_raise
    def guardrail(self, move: Move, denied: str | None) -> None:
        violations = denied.split("; ") if denied else []
        tm.event(
            "guardrail", {"allowed": denied is None, "violations": violations, "move": move.kind, "price": move.price}
        )

    @tm.never_raise
    def move(self, move: Move, said: str | None) -> None:
        current = trace.get_current_span()
        values = {
            "kind": move.kind,
            "price": move.price,
            "offer_id": move.offer_id,
            "reason": move.reason,
            "words": said,
        }
        tm.add_event(current, "our_move", values)
        summary = f"{move.kind} {move.price if move.price is not None else ''} ({move.reason})"
        tm.set_attributes(
            current, {"bazaar.move.kind": move.kind, "bazaar.move.price": move.price, tm.OUTPUT: said or summary}
        )

    @tm.never_raise
    def refused(self, error: Exception) -> None:
        self._refusals += 1
        tm.record_failure(trace.get_current_span(), error)
        tm.set_attributes(self._root, {"bazaar.refusals": self._refusals})

    @tm.never_raise
    def finished(self, outcome: Outcome) -> None:
        tm.set_attributes(
            self._root,
            {
                "bazaar.outcome": outcome.status,
                "bazaar.price": outcome.price,
                "bazaar.ticks": outcome.ticks,
                "bazaar.bids": list(outcome.bids),
                tm.OUTPUT: "\n".join([*self._transcript, f"→ {outcome.status} price {outcome.price}"]),
            },
        )
        if outcome.status == "deal":
            self._root.set_status(Status(StatusCode.OK))


@contextmanager
def trace_negotiation(dealer: str, topic: dict[str, Any], plan: BidPlan) -> Iterator[NegotiationTrace | None]:
    """The root `negotiation` span around `negotiate(..., observer=...)`. Yields None when tracing is off."""
    if not tm.enabled():
        yield None
        return
    ref = topic_ref(topic)
    values = {
        "bazaar.dealer": dealer,
        "bazaar.item": ref,
        "bazaar.plan.start": plan.start,
        "bazaar.plan.step": plan.step,
        "bazaar.plan.max": plan.max_price,
        tm.INPUT: tm.as_json({"dealer": dealer, "topic": topic, "plan": plan.__dict__}),
        tm.INPUT_MIME: tm.JSON_MIME,
        tm.TAGS: [f"dealer:{dealer}", f"item:{ref}"],
    }
    with tm.span("negotiation", tm.AGENT, values, root=True) as root:
        yield NegotiationTrace(root, dealer)


class DuelTraces:
    """One trace per duel across ticks: a `duel` root per id, a `duel tick N` child per tick it is live.

    Call `seen` for every live duel each tick, the other hooks as things happen, and `end_tick`
    after the last duel of the tick. `close` ends whatever is still open (Ctrl-C, max ticks).
    """

    def __init__(self) -> None:
        self._roots: dict[int, Span] = {}
        self._ticks: dict[int, Span] = {}
        self._last: dict[int, Mapping[str, Any]] = {}

    @tm.never_raise
    def seen(self, duel: Mapping[str, Any], tick: int, move: Any) -> None:
        tracer = tm.tracer()
        did = duel.get("id")
        if tracer is None or not isinstance(did, int):
            return
        if did not in self._roots:
            values = {
                tm.KIND: tm.AGENT,
                "bazaar.duel.id": did,
                "bazaar.duel.role": duel.get("role"),
                "bazaar.duel.limit": duel.get("your_limit"),
                "bazaar.duel.issues": duel.get("issues"),
                "bazaar.duel.first_tick": tick,
                tm.TAGS: [f"duel:{did}", f"role:{duel.get('role')}"],
            }
            self._roots[did] = tracer.start_span("duel", context=Context(), attributes=tm.attributes(values))
        child = tracer.start_span(
            f"duel tick {tick}",
            context=trace.set_span_in_context(self._roots[did]),
            attributes=tm.attributes(
                {tm.KIND: tm.CHAIN, "bazaar.tick": tick, "bazaar.duel.deadline": duel.get("deadline")}
            ),
        )
        self._ticks[did], self._last[did] = child, duel
        tm.add_event(child, "rival_offer", {"offer": duel.get("rival_offer")})
        values = {"kind": move.kind, "price": move.price, "days": move.days, "reason": move.reason}
        tm.add_event(child, "our_move", values)

    @tm.never_raise
    def guardrail(self, duel_id: int, allowed: bool, violations: Iterable[str]) -> None:
        if duel_id in self._ticks:
            tm.add_event(self._ticks[duel_id], "guardrail", {"allowed": allowed, "violations": list(violations)})

    @tm.never_raise
    def sent(self, duel_id: int, move: Any, text: str | None) -> None:
        if duel_id in self._ticks:
            values = {"kind": move.kind, "price": move.price, "days": move.days, "words": text}
            tm.add_event(self._ticks[duel_id], "move_sent", values)

    @tm.never_raise
    def refused(self, duel_id: int, error: Exception) -> None:
        if duel_id in self._ticks:
            tm.record_failure(self._ticks[duel_id], error)

    @tm.never_raise
    def end_tick(self, live_ids: Iterable[object]) -> None:
        self._end_children()
        live = set(live_ids)
        for did in [d for d in self._roots if d not in live or self._last[d].get("done")]:
            self._finish(did, "done" if self._last[did].get("done") else "left /api/duels")

    @tm.never_raise
    def close(self, reason: str = "stopped") -> None:
        self._end_children()
        for did in list(self._roots):
            self._finish(did, reason)

    def _end_children(self) -> None:
        for child in self._ticks.values():
            child.end()
        self._ticks.clear()

    def _finish(self, duel_id: int, outcome: str) -> None:
        root = self._roots.pop(duel_id)
        last = self._last.pop(duel_id, {})
        tm.set_attributes(
            root, {"bazaar.outcome": outcome, tm.OUTPUT: tm.as_json(last), "bazaar.duel.done": bool(last.get("done"))}
        )
        root.end()

    @staticmethod
    @tm.never_raise
    def read_failed(tick: int, error: Exception) -> None:
        with tm.span("duels.read", tm.CHAIN, {"bazaar.tick": tick}, root=True) as current:
            tm.record_failure(current, error)


@tm.never_raise
def trace_thread(view: Thread) -> None:
    """One `thread.view` span with the whole conversation as `message` events (`bazaar thread <id>`)."""
    meta = header(view)
    transcript = "\n".join(line.transcript() for line in lines(view))
    values = {
        "bazaar.thread.id": view.id,
        "bazaar.thread.with": view.with_,
        "bazaar.thread.status": view.status,
        "bazaar.thread.closed_reason": view.closed_reason,
        "bazaar.item": meta["ref"],
        "bazaar.item.name": view.item,
        "bazaar.messages": len(view.messages),
        tm.INPUT: tm.as_json(meta),
        tm.INPUT_MIME: tm.JSON_MIME,
        tm.OUTPUT: transcript,
        tm.TAGS: [f"thread:{view.id}", f"with:{view.with_}", f"status:{view.status}"],
    }
    with tm.span("thread.view", tm.CHAIN, values, root=True) as current:
        for line in lines(view):
            tm.add_event(current, "message", message_event(line))
        for offer in view.standing_offers:
            values = {"offer_id": offer.id, "maker": offer.maker, "price": offer.price, "final": offer.final}
            tm.add_event(current, "standing_offer", values)


def per_tick(name: str, on_tick: Callable[[Any], None]) -> Callable[[Any], None]:
    """Each call of a loop's `on_tick` as its own trace `<name> <tick>`, so a loop that runs for hours
    shows up in Phoenix tick by tick instead of as one span that lands only when it stops."""
    if not tm.enabled():
        return on_tick

    def traced(clock: Any) -> None:
        values = {"bazaar.tick": clock.tick, "bazaar.t_hours": clock.t_hours, "bazaar.loop": name}
        with tm.span(f"{name} {clock.tick}", tm.CHAIN, values, root=True):
            on_tick(clock)

    return traced


@tm.never_raise
def feed_capture(result: Any) -> None:
    """The capture's numbers on the current span (`feed.capture` or a monitor tick)."""
    tm.set_attributes(
        trace.get_current_span(),
        {
            "bazaar.feed.fetched": result.fetched,
            "bazaar.feed.new": result.new,
            "bazaar.feed.newest_id": result.newest_id,
            "bazaar.feed.gap_possible": result.gap_possible,
        },
    )


@tm.never_raise
def trader_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    """A `trader` event on the current span for every new or changed trader snapshot."""
    for trader_id, snap in after.items():
        old = before.get(trader_id)
        if old == snap:
            continue
        values = {"trader_id": trader_id, "change": "new" if old is None else "changed"}
        values |= {"kind": snap.kind, "name": snap.name, "status": snap.status, "level": snap.level}
        tm.event("trader", values)


@tm.never_raise
def alert_events(alerts: Iterable[Any]) -> None:
    for a in alerts:
        tm.event("alert", {"tick": a.tick, "kind": a.kind, "subject": a.subject, "detail": a.detail})


@tm.never_raise
def monitor_summary(new_events: int, dealers: int, teams: int, levels: int, me: Mapping[str, Any] | None) -> None:
    """The monitor tick's counts and our /me numbers on the current span."""
    score = (me or {}).get("score") or {}
    tm.set_attributes(
        trace.get_current_span(),
        {
            "bazaar.new_events": new_events,
            "bazaar.dealers": dealers,
            "bazaar.teams": teams,
            "bazaar.levels": levels,
            "bazaar.cash": (me or {}).get("cash"),
            "bazaar.level": (me or {}).get("level"),
            "bazaar.score": score.get("score"),
            "bazaar.rank": score.get("rank"),
        },
    )
