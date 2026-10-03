"""What the taker and the maker share: the tick window, live mode, the feed they rank from, Jev advice.

Tick discipline is the one in `ticks.py`: the loop is driven by `/api/clock`, never by wall-clock
time; each tick gets a deadline (`action_budget_s`) and every send checks it right before it goes.
A move that would land after the deadline is dropped (logged as `expired`), never sent late.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import psycopg

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.market import Venue, venues_from
from bazaar_agent.agents.seller import Commitments, committed_context
from bazaar_agent.decisions import Decision, DecisionLog, Status
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore
from bazaar_agent.guardrails import Context, Guardrails, LedgerStore, context_from
from bazaar_agent.holdings import Holdings, MeRead
from bazaar_agent.ticks import Clock, action_budget_s

DB_RETRY_EVERY = 5  # ticks between Postgres retries once the feed table was unreachable
ARCHIVE_TIMEOUT_MS = 2000  # the taker's feed archive never holds a tick longer than this
# Refusals after which a write may have reached the game anyway: the connection failed after the request
# went out (`network`), or the server answered 2xx with a body that is not JSON (`bad_response`).
MAYBE_LANDED = ("network", "bad_response")
LIVE_ENV = "BAZAAR_LIVE"  # "1" on a Railway service turns its agent live; never read from .env


def live_mode(flag: bool, env: Mapping[str, str] | None = None) -> bool:
    """`--live`, or BAZAAR_LIVE=1 in the process environment (Railway). Anything else is a dry run."""
    return flag or (os.environ if env is None else env).get(LIVE_ENV, "").strip() == "1"


@dataclass(frozen=True)
class TickWindow:
    """The time left to act in one tick. `open()` is checked right before every send."""

    tick: int
    deadline: float  # time.monotonic() value
    now: Callable[[], float] = time.monotonic

    def left(self) -> float:
        return max(0.0, self.deadline - self.now())

    def open(self) -> bool:
        return self.left() > 0


def window_for(clock: Clock, started: float, now: Callable[[], float] = time.monotonic) -> TickWindow:
    return TickWindow(clock.tick, started + action_budget_s(clock), now)


@dataclass(frozen=True)
class JevAdvice:
    """One Jev verdict (`offer_is_worth_accepting`, `duel_move`, ...): advisory only, it never lifts a limit."""

    verdict: str  # a noul's "yes" | "no", a choice's option, or "undecided"
    value: float
    probabilities: Mapping[str, float] | None = None
    reason: str | None = None
    digest: str | None = None  # the decision line's state digest (jev/log.py), for its outcome line later

    @property
    def decided(self) -> bool:
        return self.verdict != "undecided"

    def as_dict(self) -> dict[str, Any]:
        probabilities = dict(self.probabilities) if self.probabilities is not None else None
        written = {"verdict": self.verdict, "value": self.value, "probabilities": probabilities, "reason": self.reason}
        return written if self.digest is None else {**written, "digest": self.digest}


JevFn = Callable[[dict[str, Any]], JevAdvice]


def no_jev(state: dict[str, Any]) -> JevAdvice:
    return JevAdvice("undecided", 0.0, reason="jev off")


def _row_event(row: tuple[Any, ...]) -> Event:
    event_id, tick, kind, actor, payload = row
    return {"id": int(event_id), "tick": tick, "type": kind, "actor": actor, "payload": payload or {}}


class MarketFeed:
    """Feed events for the strategy: the shared `feed_events` table (the monitor writes it) when Postgres
    answers, else this machine's captured JSONL; the public live window is merged in every tick.

    `archive=True` (the taker on Railway) also writes the window it just read into `feed_events`, so the
    shared archive keeps growing while the laptop monitor sleeps: the window holds ~20 ticks, and an event
    that leaves it unarchived is gone for good. Same dedupe-safe insert as the monitor; no extra game call."""

    def __init__(
        self,
        read_window: Callable[[int], list[Event]],
        store: FeedStore | None = None,
        connect: Callable[[], psycopg.Connection] | None = None,
        log: Callable[[str], None] = lambda message: None,
        archive: bool = False,
    ) -> None:
        self._read_window, self._store, self._connect, self._log = read_window, store, connect, log
        self._archive, self._archive_failed = archive, False
        self._unarchived: list[Event] = []  # the last window read, written by `archive_pending()` after the sends
        self._conn: psycopg.Connection | None = None
        self._events: dict[int, Event] = {}
        self._newest_db = 0
        self._loaded_store = False
        self._db_down = False
        self._skip = 0  # reads to skip Postgres after a failure (a connect may take 10 s)

    def _from_db(self) -> bool:
        if self._connect is None:
            return False
        if self._skip > 0:
            self._skip -= 1
            return False
        try:
            if self._conn is None or self._conn.closed:
                self._conn = self._connect()
                self._conn.autocommit = True
            rows = self._conn.execute(
                "select id, tick, type, actor, payload from feed_events where id > %s order by id",
                (self._newest_db,),
            ).fetchall()
        except Exception as e:  # down or unreachable: the JSONL + live window still serve this tick
            if not self._db_down:
                self._log(f"feed: Postgres unavailable ({type(e).__name__}), using the captured JSONL + live window")
            self._db_down, self._conn, self._skip = True, None, DB_RETRY_EVERY
            return False
        self._db_down = False
        for row in rows:
            event = _row_event(row)
            self._events[event["id"]] = event
            self._newest_db = max(self._newest_db, event["id"])
        return True

    def events(self) -> list[Event]:
        from_db = self._from_db()
        if not from_db and self._store is not None and not self._loaded_store:
            for event in self._store.events():
                self._events.setdefault(event["id"], event)
            self._loaded_store = True
        window: list[Event] = []
        try:
            window = self._read_window(DEFAULT_WINDOW)
            for event in window:
                self._events[event["id"]] = event
        except Exception as e:
            self._log(f"feed: live window unavailable ({type(e).__name__}); ranking from what we hold")
        if self._archive and from_db:
            self._unarchived = [e for e in window if isinstance(e.get("id"), int) and e["id"] > self._newest_db]
        return [self._events[i] for i in sorted(self._events)]

    def archive_pending(self) -> None:
        """Write the last window's events Postgres does not hold yet: called after the tick's sends, so the
        archive never delays one (bounded by a statement timeout; a failure only logs)."""
        from bazaar_agent.db import insert_events

        fresh, self._unarchived = self._unarchived, []
        if not fresh or self._conn is None or self._conn.closed:
            return
        try:
            with self._conn.transaction():
                self._conn.execute(f"set local statement_timeout = {ARCHIVE_TIMEOUT_MS}")
                with self._conn.cursor() as cur:
                    stored = insert_events(cur, fresh)["feed_events"]
        except Exception as e:
            if not self._archive_failed:
                self._log(f"feed: archiving the window failed ({type(e).__name__}); trading goes on")
            self._archive_failed = True
            return
        if stored < len(fresh):  # an event Postgres would refuse (malformed, out of range): skipped, not the batch
            self._log(f"feed: archived {stored} of {len(fresh)} new events; {len(fresh) - stored} unstorable skipped")
        if self._archive_failed:
            self._log("feed: archiving the window again")
        self._archive_failed = False


def album_pages(me: Mapping[str, Any]) -> frozenset[str]:
    """The set codes of the pages in `/api/me`: a set released mid-game shows up here first."""
    return frozenset(str(p.get("set")) for p in (me.get("album") or {}).get("pages") or [] if isinstance(p, dict))


class PageWatch:
    """The album pages one agent has seen since it started. The playbook is rebuilt from `/api/me` every
    tick, so a page released mid-game (El Retiro Saturday, Chamberí Sunday) is ranked the first tick it
    shows up, without a restart; this only says so once, in the log."""

    def __init__(self) -> None:
        self.seen: frozenset[str] | None = None

    def new(self, me: Mapping[str, Any]) -> tuple[str, ...]:
        """Pages in this `/api/me` that the agent had not seen: none on its first tick."""
        pages = album_pages(me)
        fresh = () if self.seen is None else tuple(sorted(pages - self.seen))
        self.seen = pages if self.seen is None else self.seen | pages
        return fresh


def new_page_line(tick: int, agent: str, fresh: tuple[str, ...], me: Mapping[str, Any]) -> str:
    return (
        f"tick {tick} {agent}: new page(s) {', '.join(fresh)} in /api/me: ranked on "
        f"{len(album_pages(me))} pages from this tick, no restart"
    )


@dataclass(frozen=True)
class Snapshot:
    """One tick's view, read album first: `/api/me` before anything is decided."""

    clock: Clock
    me: dict[str, Any]
    offers: dict[str, Any]  # GET /api/me/offers
    catalog: dict[str, Any]
    dealers: list[dict[str, Any]]
    venues: list[Venue]
    events: list[Event]
    holdings: MeRead | None = None  # where `me` came from: the shared Postgres snapshot or a live read

    @property
    def us(self) -> str:
        return str(self.me.get("id") or "")

    def with_me(self, read: MeRead) -> Snapshot:
        """The same view with fresher holdings (re-read after a deal)."""
        return replace(self, me=read.me, holdings=read)


def read_snapshot(
    team: Any,
    public: Any,
    feed: MarketFeed,
    clock: Clock,
    holdings: Holdings | None = None,
    clock_read_at: float | None = None,
) -> Snapshot:
    """Team reads (`me`, our offers) with the key; everything public without it. With `holdings`, /me
    comes from the shared Postgres snapshot while it is provably current (`holdings.py`), else live."""
    read = holdings.me(clock, clock_read_at=clock_read_at) if holdings is not None else None
    me = read.me if read is not None else team.me()
    offers = team.my_offers()
    personas = public.dealers()
    catalog = public.catalog()
    if holdings is not None:
        holdings.observe_catalog(clock.tick, catalog)
    return Snapshot(
        clock=clock,
        me=me,
        offers=offers,
        catalog=catalog,
        dealers=[d for d in personas.get("personas") or personas.get("dealers") or [] if isinstance(d, dict)],
        venues=venues_from(public.venues()),
        events=feed.events(),
        holdings=read,
    )


def guard_context(snap: Snapshot, ledger: LedgerStore, rules: Guardrails, commitments: Commitments) -> Context:
    """The live guardrail context: /me, the shared ledger, and what our open offers already promise."""
    base = context_from(snap.me, snap.clock.tick, snap.clock.t_hours, ledger, rules)
    return committed_context(base, commitments)


def accept_limit(clock: Clock, rules: Guardrails) -> int:
    """Accepts per tick for the whole team: the stricter of the clock's limit and GUARDRAILS.md."""
    return min(clock.limits.accepts_per_team_per_tick, rules.max_accepts_per_tick)


@dataclass(frozen=True)
class Refused:
    """What a refusal said (`BazaarError` minus its traceback): the learner reads `until_tick` from `extra`."""

    code: str
    message: str
    extra: dict[str, Any]


class Recorder:
    """Every proposed move: one console line, one `decisions` row, one trace event on the tick span.
    Every live send: one `executions` row with the answer or the refusal code."""

    def __init__(
        self, agent: str, decisions: DecisionLog, live: bool, log: Callable[[str], None], hub: Any = None
    ) -> None:
        self.agent, self.decisions, self.live, self.log = agent, decisions, live, log
        self.hub = hub  # agents.status.StatusHub when the status server runs
        self.last_error: Refused | None = None  # the last refused send: code, message, extra (no traceback)
        self.maybe_landed = False  # the last send failed in a way that may still have reached the game
        self.last_status = 0  # the HTTP status of the last refused send (0: none, or no answer)
        self.last_code: str | None = None  # the last send's refusal code (None: it went through)

    def decide(
        self,
        tick: int,
        kind: str,
        line: str,
        *,
        inputs: dict[str, Any],
        reason: str,
        guardrail: str,
        chosen: bool,
        status: Status,
        jev: JevAdvice | None = None,
        thread_id: int | None = None,
        move: dict[str, Any] | None = None,
    ) -> int:
        self.log(f"tick {tick} {self.agent}: {self._prefix(chosen, status)}{line}")
        decision = Decision(
            agent=self.agent,
            tick=tick,
            kind=kind,
            inputs=inputs,
            reason=reason,
            guardrail=guardrail,
            chosen=chosen,
            status=status,
            dry_run=not self.live,
            jev=jev.as_dict() if jev is not None else None,
            thread_id=thread_id,
            move=move or {},
        )
        tm.event(
            "decision",
            {
                "kind": kind,
                "status": status,
                "chosen": chosen,
                "guardrail": guardrail,
                "dry_run": not self.live,
                "line": line,
                "jev": jev.verdict if jev is not None else None,
            },
        )
        decision_id = self.decisions.decide(decision)
        if self.hub is not None:  # the hub publishes only its allow-listed public view of this row
            sent = "would-send" if not self.live else "sending"
            self.hub.decision(
                {
                    "decision_id": decision_id,
                    "tick": tick,
                    "kind": kind,
                    "line": line,
                    "move": move or {},
                    "inputs": inputs,
                    "strategy": inputs.get("strategy"),
                    "reason": reason,
                    "guardrail": guardrail,
                    "jev": decision.jev,
                    "chosen": chosen,
                    "status": status,
                    "dry_run": not self.live,
                    "thread_id": thread_id,
                    "sent": sent if chosen and status == "approved" else "not sent",
                }
            )
        return decision_id

    def executed(
        self, decision_id: int, tick: int, method: str, request: dict[str, Any], response: Any, code: str | None
    ) -> None:
        """One request sent outside `send` (its call had to run elsewhere): recorded and published the same way."""
        self._executed(decision_id, tick, method, request, response, code)

    def _executed(
        self, decision_id: int, tick: int, method: str, request: dict[str, Any], response: Any, code: str | None
    ) -> None:
        self.decisions.executed(decision_id, tick, method, request, response, code)
        if self.hub is not None:
            self.hub.execution(
                {
                    "decision_id": decision_id,
                    "tick": tick,
                    "method": method,
                    "request": request,
                    "response": response,
                    "error_code": code,
                }
            )

    def _prefix(self, chosen: bool, status: Status) -> str:
        if status == "expired":
            return "DROPPED: "
        if chosen and status == "approved":
            return "" if self.live else "WOULD "
        return ""

    def send(
        self, decision_id: int, tick: int, method: str, request: dict[str, Any], call: Callable[[], Any]
    ) -> dict[str, Any] | None:
        """Send one request; None when the server refused it (logged, recorded, the loop goes on). After a
        None, `maybe_landed` says whether the write may have gone through anyway (see MAYBE_LANDED): a
        spend is then booked as if it did (fail safe: the caps may over-count, never under-count)."""
        from bazaar_agent.sdk import BazaarError

        self.last_error = None
        self.maybe_landed, self.last_code, self.last_status = False, None, 0
        try:
            response = call()
        except BazaarError as e:
            self.maybe_landed, self.last_code = e.code in MAYBE_LANDED, e.code
            # Only the plain fields: the exception's traceback holds the SDK frame with our key header.
            self.last_error = Refused(str(e.code), str(e.message), dict(e.extra) if isinstance(e.extra, dict) else {})
            self.last_status = int(getattr(e, "status", 0) or 0)  # 4xx: refused for sure; 5xx or 0: unknown
            self._executed(decision_id, tick, method, request, None, e.code)
            self.decisions.settle(decision_id, "failed")
            tm.event("refused", {"method": method, "code": e.code, "message": e.message[:200]})
            self.log(f"tick {tick} {self.agent}: {method} refused {e.code} ({e.message[:80]})")
            return None
        body = response if isinstance(response, dict) else {"result": response}
        self._executed(decision_id, tick, method, request, body, None)
        self.decisions.settle(decision_id, "done")
        return body


def watched_clock(
    read: Callable[[], dict[str, Any]], name: str, log: Callable[[str], None], hub: Any = None
) -> Callable[[], dict[str, Any]]:
    """`read_clock` for `run_per_tick` that also tells the status hub, and the log once per change,
    when the game is not ticking (doors closed, paused): the agent waits, it is not stuck."""
    last: list[str] = []

    def read_clock() -> dict[str, Any]:
        payload = read()
        if hub is not None:
            hub.clock(payload)
        live = payload.get("doors", "open") == "open" and not payload.get("paused")
        state = "live" if live else f"doors {payload.get('doors')}" + (", paused" if payload.get("paused") else "")
        if last != [state]:
            if not live:
                log(f"{name}: waiting, {state}; next opening {payload.get('next_opens') or 'unknown'}")
            elif last:
                log(f"{name}: the game is ticking again (tick {payload.get('tick')})")
            last[:] = [state]
        return payload

    return read_clock
