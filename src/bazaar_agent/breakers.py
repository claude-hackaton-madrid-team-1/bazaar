"""Circuit breakers: per-scope write stops in Postgres (`guard_breakers`), shared by every process.

A tripped scope makes `guardrails.check()` refuse every write of that scope (a duel accept, a team swap,
a dealer buy, a board accept, a maker post, a dealer sell). A human trips and resets them with
`bazaar breaker trip|reset`; the live watchdog (`watchdog.py`) trips them on a bad trade and never resets a
human's trip. A trip may carry `until_tick`: it lapses by itself at that tick.

Reads fail OPEN: `BreakerBoard.tripped` reads once per tick per process on a worker thread, waits at most
`breaker_read_timeout_s`, and a failed or slow read answers "nothing tripped". Postgres down must never stop
trading: the shared ledger already fails closed for every write that spends.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg

log = logging.getLogger(__name__)

SCOPES: tuple[str, ...] = ("duel_accept", "team_swap", "dealer_buy", "board_accept", "maker_post", "dealer_sell")
NOTHING: frozenset[str] = frozenset()
STATEMENT_TIMEOUT_MS = 1000
CONNECT_TIMEOUT_S = 2
RETRY_EVERY_S = 15.0  # after a failed read, skip Postgres this long: an outage never becomes a connect storm

DDL = (
    "create table if not exists guard_breakers (scope text primary key, tripped bool not null default false, "
    "reason text, tick int, at timestamptz not null default now(), until_tick int, source text)"
)
READ = "select scope from guard_breakers where tripped and (until_tick is null or until_tick > %s)"


class BreakerError(ValueError):
    """An unknown scope or an empty reason: nothing was written."""


def known_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise BreakerError(f"unknown breaker scope {scope!r}: use one of {', '.join(SCOPES)}")
    return scope


@dataclass(frozen=True)
class BreakerRow:
    scope: str
    tripped: bool
    reason: str
    tick: int | None
    until_tick: int | None
    source: str
    at: str

    def active(self, tick: int | None) -> bool:
        if not self.tripped:
            return False
        return self.until_tick is None or tick is None or self.until_tick > tick


class TickBoard[T]:
    """One Postgres read per tick per process, on a worker thread with a deadline.

    `connect` opens a connection (None: no database, every read answers `fallback`). A failed or slow read answers
    `fallback` for the rest of that tick and skips Postgres for `RETRY_EVERY_S`; a read still running from an earlier
    tick is never doubled. A missing table answers `no_table`. The subclass picks what `fallback` means: the breakers
    fail open (nothing tripped), the human approvals fail closed (no approval readable).
    """

    name = "board"
    fallback_note = ""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        timeout_s: float = 1.0,
        notify: Callable[[str], None] = log.warning,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect, self._timeout_s, self._notify, self._now = connect, timeout_s, notify, now
        self._down_until = 0.0
        self._conn: psycopg.Connection | None = None
        self._tick: int | None = None
        self._value: T = self.fallback()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._failed_tick: int | None = None

    def fallback(self) -> T:
        raise NotImplementedError

    def no_table(self) -> T:
        return self.fallback()

    def query(self, conn: psycopg.Connection, tick: int) -> T:
        raise NotImplementedError

    def read(self, tick: int) -> T:
        if self._connect is None:
            return self.fallback()
        with self._lock:
            if self._tick == tick:
                return self._value
            if self._worker is not None and self._worker.is_alive():
                return self._fail(tick, "an earlier read is still running")
            if self._now() < self._down_until:
                return self._fail(tick, f"Postgres failed less than {RETRY_EVERY_S:g} s ago", backoff=False)
            box: dict[str, Any] = {}
            worker = threading.Thread(target=self._read, args=(tick, box), name=f"{self.name}-read", daemon=True)
            self._worker = worker
            worker.start()
            worker.join(self._timeout_s)
            if worker.is_alive():
                return self._fail(tick, f"no answer in {self._timeout_s:g} s")
            if "error" in box:
                return self._fail(tick, str(box["error"]))
            self._tick, self._value = tick, box["value"]
            return self._value

    def _fail(self, tick: int, why: str, backoff: bool = True) -> T:
        # The failure is this tick's answer (one try per tick), and the next tries wait out the backoff.
        self._tick, self._value = tick, self.fallback()
        if backoff:
            self._down_until = self._now() + RETRY_EVERY_S
        if self._failed_tick != tick:  # once per tick, never a flood
            self._failed_tick = tick
            self._notify(f"{self.name}: read failed at tick {tick} ({why}); {self.fallback_note}")
        return self._value

    def _read(self, tick: int, box: dict[str, Any]) -> None:
        try:
            if self._conn is None or self._conn.closed:
                assert self._connect is not None
                self._conn = self._connect()
                self._conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                self._conn.commit()
            box["value"] = self.query(self._conn, tick)
            self._conn.commit()
        except psycopg.errors.UndefinedTable:
            self._rollback()
            box["value"] = self.no_table()
        except Exception as e:  # noqa: BLE001 — any failure answers the fallback; the type is enough for the log
            self._drop()
            box["error"] = type(e).__name__

    def _rollback(self) -> None:
        try:
            if self._conn is not None:
                self._conn.rollback()
        except psycopg.Error:
            self._drop()

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


class BreakerBoard(TickBoard[frozenset[str]]):
    """The tripped scopes, read once per tick; fail open (a failed read: nothing tripped)."""

    name = "breakers"
    fallback_note = "no breaker applies (fail open)"

    def fallback(self) -> frozenset[str]:
        return NOTHING  # also no breaker was ever tripped on a database without the table

    def query(self, conn: psycopg.Connection, tick: int) -> frozenset[str]:
        return frozenset(str(r[0]) for r in conn.execute(READ, (tick,)).fetchall())

    def tripped(self, tick: int) -> frozenset[str]:
        return self.read(tick)


_BOARD: dict[str, BreakerBoard] = {}


def _default_connect() -> psycopg.Connection:
    from bazaar_agent import pgconn

    return pgconn.connect(app="bazaar-breakers", connect_timeout_s=CONNECT_TIMEOUT_S)


def board(timeout_s: float = 1.0) -> BreakerBoard:
    """This process's board (one per process, DATABASE_URL). Tests install their own with `install`."""
    if "board" not in _BOARD:
        _BOARD["board"] = BreakerBoard(_default_connect, timeout_s)
    return _BOARD["board"]


def install(b: BreakerBoard) -> BreakerBoard | None:
    """Replace this process's board; returns the old one (for a test to put back)."""
    old = _BOARD.get("board")
    _BOARD["board"] = b
    return old


def ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(DDL)
    conn.commit()


def rows(conn: psycopg.Connection) -> list[BreakerRow]:
    ensure_table(conn)
    out = conn.execute(
        "select scope, tripped, coalesce(reason, ''), tick, until_tick, coalesce(source, ''), at::text "
        "from guard_breakers order by scope"
    ).fetchall()
    conn.commit()
    return [BreakerRow(str(r[0]), bool(r[1]), str(r[2]), r[3], r[4], str(r[5]), str(r[6])) for r in out]


def trip(
    conn: psycopg.Connection,
    scope: str,
    reason: str,
    tick: int,
    *,
    until_tick: int | None = None,
    source: str = "manual",
) -> bool:
    """Trip `scope`; True when it was not active before (a new trip: the caller logs it once). An active trip
    is never shortened: a timed trip does not replace an open-ended one, and a later `until_tick` wins."""
    known_scope(scope)
    if not reason.strip():
        raise BreakerError("a trip needs a reason")
    ensure_table(conn)
    before = conn.execute(
        "select tripped, until_tick from guard_breakers where scope = %s for update", (scope,)
    ).fetchone()
    was_active = bool(before and before[0] and (before[1] is None or before[1] > tick))
    if was_active and before is not None:
        old_until = before[1]
        if old_until is None or (until_tick is not None and until_tick <= old_until):
            conn.commit()
            return False
    conn.execute(
        "insert into guard_breakers (scope, tripped, reason, tick, at, until_tick, source) "
        "values (%s, true, %s, %s, now(), %s, %s) on conflict (scope) do update set tripped = true, "
        "reason = excluded.reason, tick = excluded.tick, at = now(), until_tick = excluded.until_tick, "
        "source = excluded.source",
        (scope, reason[:500], tick, until_tick, source),
    )
    conn.commit()
    return not was_active


def reset(conn: psycopg.Connection, scope: str, tick: int, *, source: str = "manual") -> bool:
    """Reset `scope`; True when it was tripped."""
    known_scope(scope)
    ensure_table(conn)
    row = conn.execute(
        "update guard_breakers set tripped = false, tick = %s, at = now(), until_tick = null, source = %s "
        "where scope = %s and tripped returning scope",
        (tick, source, scope),
    ).fetchone()
    conn.commit()
    return row is not None


def record(conn: psycopg.Connection, kind: str, scope: str, reason: str, tick: int, **extra: Any) -> None:
    """Make a trip or reset visible: a `decisions` row (agent `guard`) and, for a trip, a `guard_trip` learning.
    Best effort: a failed write is logged, never raised (the breaker itself is already set)."""
    from bazaar_agent.decisions import scrubbed

    inputs = {"scope": scope, "reason": reason, **extra}
    try:
        conn.execute(
            "insert into decisions (tick, candidates, policy_checks, status, reason, agent, kind, dry_run) "
            "values (%s, %s::jsonb, %s::jsonb, 'done', %s, 'guard', %s, false)",
            (tick, json.dumps(scrubbed(inputs), default=str), json.dumps({"breaker": scope}), scrubbed(reason), kind),
        )
        if kind == "breaker_trip":
            conn.execute(
                "insert into learnings (scope, subject_kind, subject, kind, created_tick, until_tick, support_n, "
                "confidence, claim, source, stats, dedupe_key, updated_at) values ('market', 'team', %s, "
                "'guard_trip', %s, %s, 1, 1, %s, 'watchdog', %s::jsonb, %s, now()) on conflict (dedupe_key) do nothing",
                (
                    scope,
                    tick,
                    extra.get("until_tick"),
                    scrubbed(f"breaker {scope} tripped: {reason}"),
                    json.dumps(scrubbed(inputs), default=str),
                    f"guard_trip:{scope}:{tick}",
                ),
            )
        conn.commit()
    except psycopg.Error as e:
        log.warning("breakers: could not record %s %s (%s)", kind, scope, type(e).__name__)
        with contextlib.suppress(psycopg.Error):
            conn.rollback()


def trip_and_record(
    conn: psycopg.Connection,
    scope: str,
    reason: str,
    tick: int,
    *,
    until_tick: int | None = None,
    source: str = "manual",
) -> bool:
    """`trip` + a WARN log line + `record` when the trip is new. True when it was new."""
    new = trip(conn, scope, reason, tick, until_tick=until_tick, source=source)
    if new:
        until = f" until tick {until_tick}" if until_tick is not None else ""
        log.warning("BREAKER TRIPPED %s%s by %s at tick %d: %s", scope, until, source, tick, reason)
        record(conn, "breaker_trip", scope, reason, tick, until_tick=until_tick, source=source)
    return new
