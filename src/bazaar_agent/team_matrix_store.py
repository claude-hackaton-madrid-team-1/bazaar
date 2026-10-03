"""The team matrix in Postgres (`team_matrix`, `team_matrix_summary`): the newest matrix of each world.

The taker builds the matrix in its news-sentinel window (`team_matrix.py`); this keeps the newest one across
restarts and lets teammates read it in DataGrip. `save()` replaces the world's rows in one transaction, so a reader
never sees half of two matrices; `load()` runs once at process start. Nothing here raises into a tick: a database
error is logged once, Postgres is retried every `RETRY_EVERY` calls, and the in-memory matrix goes on without it
(the fail-soft pattern of `leaderboard_store.py`). `world`: "real" or "sim:<host:port>", as `me_snapshots`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import psycopg

from bazaar_agent.db import jsonb_safe
from bazaar_agent.team_matrix import Cell, Summary, TeamMatrix

STATEMENT_TIMEOUT_MS = 1500
RETRY_EVERY = 5  # calls skipped after a failure before Postgres is tried again
CELL_COLUMNS = "team, card, holds, spare, missing_for_page, page_have, page_of, confidence, tick"
SUMMARY_COLUMNS = "team, rank, score, trend, top_set, venue, rival, rival_why, wants, has_for_us, last_trades, us, tick"
# One save per world at a time (the Railway taker and a laptop's may share the database): a second writer waits,
# bounded by the statement timeout, then deletes the first one's rows instead of colliding with them.
LOCK = "select pg_advisory_xact_lock(hashtext(%s))"
LOCK_KEY = "bazaar_agent.team_matrix:"
DELETE_CELLS = "delete from team_matrix where world = %s"
DELETE_SUMMARY = "delete from team_matrix_summary where world = %s"
INSERT_CELL = f"insert into team_matrix (world, {CELL_COLUMNS}) values ({', '.join(['%s'] * 10)})"
INSERT_SUMMARY = f"insert into team_matrix_summary (world, {SUMMARY_COLUMNS}) values ({', '.join(['%s'] * 14)})"
LOAD_SUMMARY = f"select {SUMMARY_COLUMNS} from team_matrix_summary where world = %s order by team"
LOAD_CELLS = f"select {CELL_COLUMNS} from team_matrix where world = %s order by team, card"


def _text(value: object) -> str | None:
    """A text column Postgres accepts: no NUL, no lone surrogate (as `learn/threads.py`)."""
    return jsonb_safe(value) if isinstance(value, str) else None


def _num(value: float | None) -> Decimal | None:
    """A numeric that reads back as the same float: psycopg sends a float as float8, and Postgres's float8 → numeric
    cast keeps only 15 digits. NaN and infinities are stored as NULL."""
    if value is None or not math.isfinite(value):
        return None
    return Decimal(repr(float(value)))


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _cell_row(world: str, tick: int, c: Cell) -> tuple[Any, ...]:
    return (world, _text(c.team), _text(c.card), c.holds, c.spare, bool(c.missing_for_page), c.page_have, c.page_of,
            _num(c.confidence), tick)  # fmt: skip


def _summary_row(world: str, m: TeamMatrix, s: Summary) -> tuple[Any, ...]:
    return (world, _text(s.team), s.rank, _num(s.score), s.trend, _text(s.top_set), _text(s.venue),
            s.rival is not None, _text(s.rival), _text(s.wants), _text(s.has_for_us), _text(s.last_trades),
            _text(m.us), m.tick)  # fmt: skip


class TeamMatrixStore:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        log: Callable[[str], None] = lambda message: None,
        world: str = "real",
    ) -> None:
        self._connect, self._log, self.world = connect, log, world
        self._conn: psycopg.Connection | None = None
        self._skip = 0  # calls left to skip before Postgres is tried again
        self._failed: set[str] = set()

    def _db(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None:
            return None
        if self._skip > 0:
            self._skip -= 1
            return None
        try:
            conn = self._connect()
            conn.autocommit = True
        except Exception as e:  # noqa: BLE001 — memory goes on without Postgres
            self._fail("connect", e)
            return None
        self._conn = conn
        return conn

    def save(self, m: TeamMatrix) -> int:
        """Replace this world's matrix with `m` in one transaction; returns the cells written (0 without Postgres or
        on a failure). An empty matrix (no cell, no team) writes nothing: it never wipes the stored one."""
        if not m.cells and not m.teams:
            return 0
        conn = self._db()
        if conn is None:
            return 0
        try:
            cells = [_cell_row(self.world, m.tick, c) for c in m.cells]
            summaries = [_summary_row(self.world, m, s) for s in m.teams.values()]
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.execute(LOCK, (LOCK_KEY + self.world,))
                conn.execute(DELETE_CELLS, (self.world,))
                conn.execute(DELETE_SUMMARY, (self.world,))
                with conn.cursor() as cur:
                    cur.executemany(INSERT_CELL, cells)
                    cur.executemany(INSERT_SUMMARY, summaries)
        except Exception as e:  # noqa: BLE001 — never into the tick loop
            self._fail("write", e)
            return 0
        if self._failed:
            self._log("team matrix: Postgres writes are back")
            self._failed = set()
        return len(cells)

    def load(self) -> TeamMatrix | None:
        """This world's stored matrix (None when there is none, without Postgres, or on any error)."""
        conn = self._db()
        if conn is None:
            return None
        try:
            with conn.transaction():
                conn.execute("set transaction isolation level repeatable read")  # both reads see one save
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                summaries = conn.execute(LOAD_SUMMARY, (self.world,)).fetchall()
                cells = conn.execute(LOAD_CELLS, (self.world,)).fetchall()
            return _matrix(summaries, cells)
        except Exception as e:  # noqa: BLE001 — start without a stored matrix
            self._fail("read", e)
            return None

    def _fail(self, what: str, error: Exception) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn, self._skip = None, RETRY_EVERY
        if what not in self._failed:
            self._log(f"team matrix: {what} failed ({type(error).__name__}); kept in memory only")
        self._failed.add(what)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


def _matrix(summaries: Sequence[Sequence[Any]], cells: Sequence[Sequence[Any]]) -> TeamMatrix | None:
    """The stored rows as a matrix: its tick is the newest summary row's (the cells' without a summary)."""
    if not summaries and not cells:
        return None
    newest = max(summaries, key=lambda r: int(r[-1]), default=None)
    tick = int(newest[-1]) if newest is not None else max(int(r[-1]) for r in cells)
    us = str(newest[11] or "") if newest is not None else ""
    teams = {str(r[0]): _summary(r) for r in summaries}
    return TeamMatrix(tick, us, tuple(_cell(r) for r in cells), teams)


def _cell(r: Sequence[Any]) -> Cell:
    team, card, holds, spare, missing, have, of, confidence, _tick = r
    return Cell(str(team), str(card), int(holds), int(spare), bool(missing), _int(have), _int(of),
                float(confidence) if confidence is not None else 0.0)  # fmt: skip


def _summary(r: Sequence[Any]) -> Summary:
    team, rank, score, trend, top_set, venue, rival, why, wants, has_for_us, last_trades, _us, _tick = r
    return Summary(str(team), _int(rank), float(score) if score is not None else None, _int(trend), top_set, venue,
                   (why or "rival") if rival else None, wants or "", has_for_us or "", last_trades or "")  # fmt: skip


class LatestMatrix:
    """The latest stored matrix, for a process that does not build it (the maker): reloaded at most every
    `every` ticks, called after the tick's sends; keeps the last good one through a failed read. Never raises."""

    def __init__(self, store: TeamMatrixStore, every: int = 10) -> None:
        self.store, self.every = store, every
        self.matrix: TeamMatrix | None = None
        self._at: int | None = None

    def refresh(self, tick: int) -> None:
        if self._at is not None and tick - self._at < self.every:
            return
        self._at = tick
        try:
            self.matrix = self.store.load() or self.matrix
        except Exception:  # noqa: BLE001 — advice only: keep the last good matrix
            return
