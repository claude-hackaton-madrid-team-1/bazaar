"""Our own dealer and team threads kept in Postgres from existing reads and acknowledged sends.

The public feed carries every dealer message and its structured offer, but not what only our own thread
responses carry: `closed_reason` (`cooloff` + `until_tick`, `persona_quota`, `sold_out`, ...), the status a
thread ended with, our own words, and which of our tactics sent a message. The taker reads `GET /api/me/threads`
and `GET /api/threads/{id}` every tick anyway: this module keeps those answers, with ZERO extra requests.

`saw()` only buffers (no I/O, never raises); `flush()` runs after the tick's sends and upserts by id in one
bounded transaction. A database error is logged once and Postgres is retried every 5 ticks; the buffer is
bounded, so a long outage drops the oldest answers, never the tick. Dealer words are untrusted data: stored
as text (NUL stripped), never read back into a decision here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from bazaar_agent.db import jsonb_safe

STATEMENT_TIMEOUT_MS = 1500
RETRY_EVERY = 5  # ticks between Postgres retries after a failure
BUFFER_MAX = 200  # threads waiting for a write; the oldest are dropped during a long outage

THREAD_UPSERT = (
    "insert into threads (id, counterpart, kind, topic, venue, status, opened_tick, closed_tick, closed_reason, "
    "until_tick, updated_tick, ours) values (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, true) "
    "on conflict (id) do update set "
    # a thread that ended never goes back to open (another process may flush an older read in the same tick)
    "status = case when threads.status <> 'open' and excluded.status = 'open' then threads.status "
    "else excluded.status end, "
    "closed_reason = coalesce(excluded.closed_reason, threads.closed_reason), "
    "until_tick = coalesce(excluded.until_tick, threads.until_tick), "
    "closed_tick = coalesce(threads.closed_tick, excluded.closed_tick), "
    "opened_tick = least(threads.opened_tick, excluded.opened_tick), "
    "topic = coalesce(excluded.topic, threads.topic), ours = true, "
    "updated_tick = greatest(threads.updated_tick, excluded.updated_tick) "
    "where threads.updated_tick is null or excluded.updated_tick >= threads.updated_tick"  # never roll back
)
MESSAGE_UPSERT = (
    "insert into messages (id, thread_id, sender, tick, text, price, offer, final, ours, tactic) "
    "values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
    "on conflict (id) do update set text = coalesce(excluded.text, messages.text), "
    "final = coalesce(excluded.final, messages.final), "
    "offer = coalesce(excluded.offer, messages.offer), tactic = coalesce(excluded.tactic, messages.tactic)"
)


def _text(value: object) -> str | None:
    """A text column Postgres accepts: no NUL, no lone surrogate."""
    return jsonb_safe(value) if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and abs(value) < 2**31 else None


def _price(offer: Mapping[str, Any] | None) -> int | None:
    """The cash in a structured offer: what the dealer asks (want) or what we bid (give)."""
    if not isinstance(offer, Mapping):
        return None
    for side in ("want", "give"):
        part = offer.get(side)
        if isinstance(part, Mapping) and _int(part.get("cash")):
            return _int(part.get("cash"))
    return None


@dataclass(frozen=True)
class Seen:
    """One thread answer, as read, with the tick we read it and the tactics we know for our messages."""

    thread: Mapping[str, Any]
    us: str
    tick: int
    tactics: Mapping[int, str]


def thread_row(s: Seen) -> tuple[Any, ...] | None:
    t = s.thread
    tid = _int(t.get("id"))
    kind = t.get("kind", "persona")
    counterpart = _text(t.get("with"))
    if tid is None or kind not in ("persona", "team"):
        return None
    if kind == "team":
        participants = (t.get("team"), t.get("with"))
        counterpart = next((p for p in participants if isinstance(p, str) and p != s.us), None)
        if s.us not in participants or counterpart is None or re.fullmatch(r"t[0-9]+", counterpart) is None:
            return None
    ticks = [tk for m in t.get("messages") or [] if isinstance(m, Mapping) and (tk := _int(m.get("tick"))) is not None]
    status = _text(t.get("status")) or "open"
    topic = t.get("topic")
    return (
        tid,
        counterpart,
        kind,
        json.dumps(jsonb_safe(topic), allow_nan=False) if isinstance(topic, Mapping) else None,
        _text(t.get("venue")),
        status,
        min(ticks) if ticks else s.tick,
        s.tick if status != "open" else None,
        _text(t.get("closed_reason")),
        _int(t.get("until_tick")),
        s.tick,
    )


def message_rows(s: Seen) -> list[tuple[Any, ...]]:
    tid = _int(s.thread.get("id"))
    out: list[tuple[Any, ...]] = []
    for m in s.thread.get("messages") or []:
        if not isinstance(m, Mapping) or tid is None:
            continue
        mid = _int(m.get("message")) if _int(m.get("message")) is not None else _int(m.get("id"))
        if mid is None:
            continue
        offer = m.get("offer") if isinstance(m.get("offer"), Mapping) else None
        sender = _text(m.get("sender"))
        final = offer.get("final") if offer is not None and isinstance(offer.get("final"), bool) else None
        out.append(
            (
                mid,
                tid,
                sender,
                _int(m.get("tick")),
                _text(m.get("text")),
                _price(offer),
                json.dumps(jsonb_safe(offer), allow_nan=False) if offer is not None else None,
                final,
                sender == s.us,
                _text(s.tactics.get(mid)) if sender == s.us else None,
            )
        )
    return out


def _storable_messages(s: Seen) -> list[tuple[Any, ...]]:
    """One message at a time, skipping the ones that cannot be stored."""
    out: list[tuple[Any, ...]] = []
    for m in s.thread.get("messages") or []:
        try:
            out += message_rows(Seen({**s.thread, "messages": [m]}, s.us, s.tick, s.tactics))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _rows(seen: list[Seen]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Every row that can be built; a thread whose answer cannot be stored is skipped, not the batch."""
    threads: list[tuple[Any, ...]] = []
    messages: list[tuple[Any, ...]] = []
    for s in seen:
        try:
            row = thread_row(s)
        except (ValueError, TypeError, AttributeError):
            continue
        if row is None:
            continue
        threads.append(row)
        try:  # a message that cannot be stored never costs the thread's own row (status, closed_reason)
            messages += message_rows(s)
        except (ValueError, TypeError, AttributeError):
            messages += _storable_messages(s)
    return threads, sorted(messages, key=lambda m: m[0])


class ThreadStore:
    """Buffers observed threads and acknowledged sent words; writes them after the sends."""

    def __init__(
        self, connect: Callable[[], psycopg.Connection] | None, log: Callable[[str], None] = lambda message: None
    ) -> None:
        self._connect, self._log = connect, log
        self._conn: psycopg.Connection | None = None
        self._down_at: int | None = None
        self._failed: set[str] = set()
        self.buffer: dict[int, Seen] = {}  # thread id -> the newest answer read

    def saw(self, thread: Mapping[str, Any], us: str, tick: int, tactics: Mapping[int, str] | None = None) -> None:
        """Keep one answer (no I/O, never raises). The newest read of a thread wins."""
        try:
            tid, at = _int(thread.get("id")), _int(tick)
            if tid is None or at is None or not us:
                return
            body = dict(thread)
            prior = self.buffer.get(tid)
            if prior is not None and prior.us == us:
                # A pre-send snapshot later in the tick must not erase an acknowledged message.
                messages = {}
                for m in [*(prior.thread.get("messages") or []), *(body.get("messages") or [])]:
                    if isinstance(m, Mapping) and (mid := _int(m.get("message", m.get("id")))) is not None:
                        messages[mid] = m
                body["messages"] = list(messages.values())[-200:]
            self.buffer[tid] = Seen(body, us, at, dict(tactics or {}))
            if len(self.buffer) > BUFFER_MAX:
                for old in sorted(self.buffer, key=lambda i: self.buffer[i].tick)[: len(self.buffer) - BUFFER_MAX]:
                    del self.buffer[old]
        except Exception as e:
            self._fail("buffer", e)

    def sent(
        self,
        tid: int,
        counterpart: str,
        us: str,
        tick: int,
        message_id: int,
        text: str,
        offer: dict[str, Any],
        venue: str = "rastro",
    ) -> None:
        """Buffer only an SDK-acknowledged outgoing message (no game request or database I/O)."""
        if (
            type(message_id) is not int
            or message_id < 0
            or type(tid) is not int
            or tid < 0
            or not isinstance(text, str)
            or not isinstance(venue, str)
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", venue) is None
            or counterpart == us
            or re.fullmatch(r"t[0-9]+", counterpart) is None
            or re.fullmatch(r"t[0-9]+", us) is None
        ):
            return
        self.saw(
            {
                "id": tid,
                "kind": "team",
                "team": us,
                "with": counterpart,
                "venue": venue,
                "status": "open",
                "topic": {"trade": "cards"},
                "messages": [{"id": message_id, "sender": us, "tick": tick, "text": text, "offer": offer}],
            },
            us,
            tick,
        )

    def _db(self, tick: int) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or (self._down_at is not None and tick - self._down_at < RETRY_EVERY):
            return None
        try:
            conn = self._connect()
            conn.autocommit = True
        except Exception as e:
            self._down_at = tick
            self._fail("connect", e)
            return None
        self._conn, self._down_at = conn, None
        return conn

    def flush(self, tick: int) -> int:
        """Upsert the buffered threads and their messages (after the sends). Returns the threads written."""
        if not self.buffer:
            return 0
        conn: psycopg.Connection | None = None
        try:
            conn = self._db(tick)
            if conn is None:
                return 0
            threads, messages = _rows([self.buffer[i] for i in sorted(self.buffer)])
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                with conn.cursor() as cur:
                    if threads:
                        cur.executemany(THREAD_UPSERT, threads)
                    if messages:
                        cur.executemany(MESSAGE_UPSERT, messages)
        except psycopg.OperationalError as e:  # the server went away: keep the answers, retry in 5 ticks
            self._fail("write", e)
            if self._conn is not None:
                self._conn.close()
            self._conn, self._down_at = None, tick
            return 0
        except (psycopg.Error, ValueError) as e:  # the data itself: write thread by thread, drop only the bad ones
            self._fail("write", e)
            return self._write_each(conn, tick) if conn is not None else 0
        except Exception as e:  # never into the tick loop
            self._fail("write", e)
            return 0
        self.buffer = {}
        if self._failed:
            self._log("threads: Postgres writes are back")
            self._failed = set()
        return len(threads)

    def open(self) -> None:
        """Connect at process start, so the first write never pays a connect inside a tick."""
        self._db(0)

    def _write_each(self, conn: psycopg.Connection, tick: int) -> int:
        """After a batch the server refused: each thread in its own transaction. A bad thread is logged and
        dropped; a connection problem (a lock, a cancelled statement) stops here and keeps the rest buffered."""
        written = 0
        for tid in sorted(self.buffer):
            threads, messages = _rows([self.buffer[tid]])
            try:
                with conn.transaction():
                    conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    with conn.cursor() as cur:
                        if threads:
                            cur.executemany(THREAD_UPSERT, threads)
                        if messages:
                            cur.executemany(MESSAGE_UPSERT, messages)
            except (psycopg.DataError, psycopg.IntegrityError, ValueError) as e:  # this thread's own data
                self._fail(f"thread {tid}", e, dropped=True)
            except psycopg.Error as e:  # the database, not the thread: stop, keep the rest, retry in 5 ticks
                self._fail("connection", e)
                conn.close()
                self._conn, self._down_at = None, tick
                return written
            else:
                written += len(threads)
            del self.buffer[tid]
        return written

    def _fail(self, what: str, error: Exception, dropped: bool = False) -> None:
        if what not in self._failed:
            fate = "dropped" if dropped else "the answers wait"
            self._log(f"threads: {what} failed ({type(error).__name__}); trading goes on, {fate}")
        self._failed.add(what)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
