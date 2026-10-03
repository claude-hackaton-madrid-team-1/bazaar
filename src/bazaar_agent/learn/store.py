"""Where learnings live and how agents recall them: the shared `learnings` table, with this process's
memory as the always-on fallback.

Every write is an idempotent upsert on the learning's dedupe key, so two processes (or a restart)
reading the same feed write one row. `recall()` merges Postgres and memory and returns only what is
still in force at the tick asked. Nothing here raises into a tick: a database error is logged once,
Postgres is retried at most once per tick, and memory keeps serving recall meanwhile.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable
from typing import Any

import psycopg

from bazaar_agent.db import jsonb_safe
from bazaar_agent.learn.model import Learning

STATEMENT_TIMEOUT_MS = 1500  # a recall or a write must never eat the tick
RETRY_EVERY = 5  # ticks between Postgres retries once it failed (a connect may take seconds)
MEMORY_MAX = 5000
SCOPES = {"dealer": "trader", "team": "trader", "venue": "market", "organiser": "market"}
COLUMNS = "subject_kind, subject, kind, created_tick, until_tick, team, evidence, confidence, claim, source, stats"


def _row(learning: Learning) -> tuple[Any, ...]:
    return (
        SCOPES[learning.subject_kind],
        learning.subject_kind,
        learning.subject,
        learning.kind,
        learning.tick,
        learning.until_tick,
        learning.team,
        list(learning.evidence),
        len(learning.evidence),
        learning.confidence,
        learning.text,
        learning.source,
        json.dumps(jsonb_safe(learning.detail), default=str, ensure_ascii=False, allow_nan=False),
        learning.key(),
    )


UPSERT = (
    "insert into learnings (scope, subject_kind, subject, kind, created_tick, until_tick, team, evidence, support_n, "
    "confidence, claim, source, stats, dedupe_key, updated_at) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now()) "
    "on conflict (dedupe_key) do update set claim = excluded.claim, stats = excluded.stats, "
    "evidence = excluded.evidence, support_n = excluded.support_n, "
    "confidence = greatest(learnings.confidence, excluded.confidence), "
    "created_tick = greatest(learnings.created_tick, excluded.created_tick), updated_at = now()"
)


def _from_row(row: tuple[Any, ...]) -> Learning | None:
    subject_kind, subject, kind, tick, until, team, evidence, confidence, claim, source, stats = row
    try:
        return Learning(
            subject_kind=subject_kind,
            subject=subject,
            kind=kind,
            tick=tick or 0,
            until_tick=until,
            team=team,
            evidence=tuple(evidence or ()),
            confidence=float(confidence if confidence is not None else 0.0),
            text=claim or "-",
            source=source or "rules",
            detail=stats if isinstance(stats, dict) else {},
        )
    except ValueError:  # a row another writer wrote in an older shape: skipped, never fatal
        return None


def matches(
    learning: Learning,
    *,
    subject: str | None,
    kinds: Collection[str] | None,
    subject_kind: str | None,
    tick: int | None,
    team: str | None,
) -> bool:
    """`team`: the learnings that bind this team or everyone (None = every learning)."""
    return (
        (subject is None or learning.subject == subject)
        and (kinds is None or learning.kind in kinds)
        and (subject_kind is None or learning.subject_kind == subject_kind)
        and (tick is None or learning.active(tick))
        and (team is None or learning.team in (None, team))
    )


class LearningStore:
    """`record()` and `recall()` over Postgres (when `connect` answers) plus this process's memory."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None = None,
        log: Callable[[str], None] = lambda message: None,
        init_schema: Callable[[psycopg.Connection], object] | None = None,
    ) -> None:
        self._connect, self._log, self._init = connect, log, init_schema
        self._conn: psycopg.Connection | None = None
        self._down = False
        self._tick: int | None = None
        self._tried_tick: int | None = None
        self._init_tried = False
        self._disabled = False  # the table lacks a column we need: memory only for this process
        self.memory: dict[str, Learning] = {}

    # ---------------------------------------------------------------- connection (DecisionLog's pattern)

    def begin_tick(self, tick: int) -> None:
        """While Postgres is down, try it again at most once per tick (a connect may take seconds)."""
        self._tick = tick

    @property
    def where(self) -> str:
        return "postgres learnings + memory" if self._db() is not None else "memory only"

    def _db(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or self._disabled:
            return None
        tick, tried = self._tick, self._tried_tick
        if self._down and tick is not None and tried is not None and tick - tried < RETRY_EVERY:
            return None
        self._tried_tick = self._tick
        try:
            conn = self._connect()
            self._init_once(conn)
            conn.autocommit = True
        except Exception as e:
            if not self._down:
                self._log(f"learnings: Postgres unavailable ({type(e).__name__}); memory only")
            self._down = True
            return None
        if self._down:
            self._log("learnings: Postgres back")
        self._conn, self._down = conn, False
        return conn

    def open(self) -> str:
        """Connect (and apply the schema) now, at process start, so no tick ever waits on it."""
        return self.where

    def _init_once(self, conn: psycopg.Connection) -> None:
        """The learnings columns (idempotent `init_schema`), tried once per process: on failure the table is
        used as it is, and a missing column only sends writes to memory."""
        if self._init is None or self._init_tried:
            return
        self._init_tried = True
        try:
            self._init(conn)
        except Exception as e:
            self._log(f"learnings: schema init failed ({type(e).__name__}); using the table as it is")
            conn.rollback()

    def _failed(self, what: str, error: Exception) -> None:
        if isinstance(error, ValueError):  # a bad value in this batch: the connection is fine, skip the batch
            self._log(f"learnings: {what} skipped a batch ({type(error).__name__}); memory keeps it")
            return
        if isinstance(error, psycopg.errors.UndefinedColumn | psycopg.errors.UndefinedTable):
            self._disabled = True  # run `bazaar db init`; retrying every tick would only log the same error
            self._log(f"learnings: the learnings table is not migrated ({type(error).__name__}); memory only")
        else:
            self._log(f"learnings: {what} failed in Postgres ({type(error).__name__}); memory meanwhile")
        if self._conn is not None:
            self._conn.close()
        self._conn, self._down, self._tried_tick = None, True, self._tick

    # ---------------------------------------------------------------- write

    def remember(self, learnings: Iterable[Learning]) -> dict[str, Learning]:
        """Into this process's memory only (no I/O), trimmed to the newest MEMORY_MAX facts."""
        batch = {lr.key(): lr for lr in learnings}
        self.memory.update(batch)
        if len(self.memory) > MEMORY_MAX:
            self.memory = dict(sorted(self.memory.items(), key=lambda kv: kv[1].tick)[-MEMORY_MAX:])
        return batch

    def record(self, learnings: Iterable[Learning]) -> int:
        """Remember and upsert; returns how many were given (memory always takes them)."""
        batch = self.remember(learnings)
        if not batch:
            return 0
        conn = self._db()
        if conn is not None:
            try:
                with conn.transaction():
                    conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    with conn.cursor() as cur:
                        cur.executemany(UPSERT, [_row(lr) for lr in sorted(batch.values(), key=Learning.key)])
            except (psycopg.Error, ValueError) as e:  # ValueError: a value jsonb refuses (NaN, an encoding)
                self._failed("upsert", e)
        return len(batch)

    # ---------------------------------------------------------------- read

    def recall(
        self,
        subject: str | None = None,
        kinds: Collection[str] | None = None,
        tick: int | None = None,
        *,
        subject_kind: str | None = None,
        team: str | None = None,
        limit: int = 50,
        use_db: bool = True,
    ) -> list[Learning]:
        """The learnings in force at `tick` (all ticks when None), newest first, deduped by key.
        `use_db=False`: memory only, no I/O (what a tick reads before its sends)."""
        found = {k: lr for k, lr in self.memory.items()}
        for lr in self._recall_db(subject, kinds, tick, subject_kind, team, limit) if use_db else ():
            found.setdefault(lr.key(), lr)
        hits = [
            lr
            for lr in found.values()
            if matches(lr, subject=subject, kinds=kinds, subject_kind=subject_kind, tick=tick, team=team)
        ]
        return sorted(hits, key=lambda lr: (-lr.tick, lr.key()))[:limit]

    def _recall_db(
        self,
        subject: str | None,
        kinds: Collection[str] | None,
        tick: int | None,
        subject_kind: str | None,
        team: str | None,
        limit: int,
    ) -> list[Learning]:
        conn = self._db()
        if conn is None:
            return []
        query = (
            f"select {COLUMNS} from learnings where dedupe_key is not null and superseded_by is null "
            "and (%(subject)s::text is null or subject = %(subject)s) "
            "and (%(kinds)s::text[] is null or kind = any(%(kinds)s)) "
            "and (%(sk)s::text is null or subject_kind = %(sk)s) "
            "and (%(tick)s::int is null or until_tick is null or until_tick > %(tick)s) "
            "and (%(team)s::text is null or team is null or team = %(team)s) "
            "order by created_tick desc nulls last, id desc limit %(limit)s"
        )
        params = {
            "subject": subject,
            "kinds": sorted(kinds) if kinds is not None else None,
            "sk": subject_kind,
            "tick": tick,
            "team": team,
            "limit": limit,
        }
        try:
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                rows = conn.execute(query, params).fetchall()  # type: ignore[arg-type]
        except psycopg.Error as e:
            self._failed("recall", e)
            return []
        return [lr for row in rows if (lr := _from_row(row)) is not None]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
