"""The public leaderboard's history in Postgres `leaderboard_snapshots`: one row per team per sentinel window.

The rank watch (`rank_watch.py`) keeps its snapshots in memory; this keeps them across restarts and lets
teammates read the board's history in DataGrip. `save()` runs inside the news sentinel, after the taker's
sends; `load()` runs once at process start. Nothing here raises into a tick: a database error is logged once,
Postgres is retried every `RETRY_EVERY` calls, and the in-memory watch goes on without it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import psycopg

from bazaar_agent.rank_watch import Standing

STATEMENT_TIMEOUT_MS = 1500
RETRY_EVERY = 5  # saves skipped after a failure before Postgres is tried again
COLUMNS = "tick, team, rank, score, negotiating, market, level, pages, deals, venue"
UPSERT = (
    f"insert into leaderboard_snapshots (world, {COLUMNS}) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "on conflict (world, tick, team) do update set rank = excluded.rank, score = excluded.score, "
    "negotiating = excluded.negotiating, market = excluded.market, level = excluded.level, "
    "pages = excluded.pages, deals = excluded.deals, venue = excluded.venue, read_at = now()"
)
LOAD = (
    f"select {COLUMNS} from leaderboard_snapshots where world = %(world)s and tick >= "
    "(select coalesce(max(tick), 0) - %(back)s from leaderboard_snapshots where world = %(world)s) order by tick, team"
)


def _row(world: str, s: Standing) -> tuple[Any, ...]:
    return (world, s.tick, s.team, s.rank, s.score, s.negotiating, s.market, s.level, s.pages, s.deals, s.venue)


class LeaderboardStore:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        log: Callable[[str], None] = lambda message: None,
        world: str = "real",
    ) -> None:
        self._connect, self._log, self.world = connect, log, world
        self._conn: psycopg.Connection | None = None
        self._skip = 0  # saves left to skip before Postgres is tried again
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
        except Exception as e:  # memory goes on without Postgres
            self._fail("connect", e)
            return None
        self._conn = conn
        return conn

    def save(self, rows: Sequence[Standing]) -> int:
        """Upsert one board's rows; returns how many were written (0 without Postgres)."""
        if not rows:
            return 0
        conn = self._db()
        if conn is None:
            return 0
        try:
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                with conn.cursor() as cur:
                    cur.executemany(UPSERT, [_row(self.world, s) for s in rows])
        except Exception as e:  # never into the tick loop
            self._fail("write", e)
            return 0
        if self._failed:
            self._log("leaderboard history: Postgres writes are back")
            self._failed = set()
        return len(rows)

    def load(self, back_ticks: int) -> list[Standing]:
        """The stored rows of this world's last `back_ticks` ticks, oldest first (empty without Postgres)."""
        conn = self._db()
        if conn is None:
            return []
        try:
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                rows = conn.execute(LOAD, {"world": self.world, "back": back_ticks}).fetchall()
        except Exception as e:  # start with an empty history
            self._fail("read", e)
            return []
        return [_standing(r) for r in rows]

    def _fail(self, what: str, error: Exception) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn, self._skip = None, RETRY_EVERY
        if what not in self._failed:
            self._log(f"leaderboard history: {what} failed ({type(error).__name__}); kept in memory only")
        self._failed.add(what)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


def _standing(r: Sequence[Any]) -> Standing:
    tick, team, rank, score, neg, market, level, pages, deals, venue = r
    return Standing(int(tick), str(team), int(rank), float(score or 0), float(neg or 0), float(market or 0),
                    int(level or 0), int(pages or 0), int(deals or 0), venue)  # fmt: skip
