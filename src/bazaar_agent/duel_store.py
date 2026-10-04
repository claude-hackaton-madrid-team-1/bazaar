"""Duel states in Postgres (`duels` table): what the evals score, without calling the game API.

`bazaar duel run` writes every live duel each tick and, on a tick where a duel left the live list,
reads `/api/duels?done=true` once and writes the finished ones (status, price, rounds, `result`).
`bazaar duel done` does that read on demand, and `bazaar evals import-duels` loads a duel runner's
JSONL log. A write never moves a duel backwards: a newer tick wins, and a finished payload is never
overwritten by a live one from a lagging writer. Writing never breaks the duel loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg

from bazaar_agent.agents.duelist import duel_deadline, duel_id
from bazaar_agent.db import jsonb_safe
from bazaar_agent.decisions import scrubbed

RETRY_EVERY_TICKS = 5  # after a failed write, skip Postgres for this many ticks (a connect can take 10 s)
STATEMENT_TIMEOUT_MS = 3000

_UPSERT = (
    "insert into duels (duel, session, tick, status, role, item, your_limit, rival, deadline_tick, rounds, "
    "decay_per_round, price, days, result, payload) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
    "on conflict (duel) do update set session = excluded.session, tick = excluded.tick, status = excluded.status, "
    "role = excluded.role, item = excluded.item, your_limit = excluded.your_limit, rival = excluded.rival, "
    "deadline_tick = excluded.deadline_tick, rounds = excluded.rounds, decay_per_round = excluded.decay_per_round, "
    "price = excluded.price, days = excluded.days, result = excluded.result, payload = excluded.payload, "
    "updated_at = now() "
    "where (coalesce(duels.status, 'live') = 'live' and excluded.status <> 'live') "
    "or (excluded.tick >= coalesce(duels.tick, -1) "
    "and (excluded.status <> 'live' or coalesce(duels.status, 'live') = 'live'))"
)


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _row(duel: Mapping[str, Any], tick: int | None) -> tuple[Any, ...] | None:
    did = duel_id(duel)
    if did is None:
        return None
    status = str(duel.get("status") or ("done" if duel.get("done") else "live"))
    seen = tick if tick is not None else duel_deadline(duel)
    result = duel.get("result")
    return (
        did,
        _int(duel.get("session")),
        seen,
        status,
        duel.get("role"),
        duel.get("item"),
        _int(duel.get("your_limit")),
        duel.get("rival"),
        duel_deadline(duel),
        _int(duel.get("rounds")),
        duel.get("decay_per_round") if isinstance(duel.get("decay_per_round"), int | float) else None,
        _int(duel.get("price")),
        _int(duel.get("days")),
        result if isinstance(result, int | float) and not isinstance(result, bool) else None,
        json.dumps(jsonb_safe(scrubbed(dict(duel))), default=str),  # no lone surrogate: Postgres jsonb rejects it
    )


def save_duels(conn: psycopg.Connection, duels: Iterable[Mapping[str, Any]], tick: int | None) -> int:
    """Upsert duel payloads seen at `tick` (None: each duel's deadline, for a finished-duel read)."""
    rows = sorted((r for d in duels if (r := _row(d, tick)) is not None), key=lambda r: r[0])
    with conn.cursor() as cur:
        cur.executemany(_UPSERT, rows)
    conn.commit()
    return len(rows)


def duel_list(response: object) -> list[dict[str, Any]]:
    """The duels in an `/api/duels` response, validated: anything but a list of objects reads as none."""
    duels = response.get("duels") if isinstance(response, Mapping) else None
    return [d for d in duels if isinstance(d, dict)] if isinstance(duels, list) else []


def jsonl_duels(path: Path) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """(tick, duels) for every `/api/duels` response a duel runner logged; other lines are skipped."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            tick = row.get("tick") if isinstance(row, dict) else None
            if isinstance(tick, int) and not isinstance(tick, bool) and isinstance(row.get("response"), dict):
                yield tick, duel_list(row["response"])


class DuelStore:
    """The duel runner's writer: lazy connection, a failure logged once, retried after a few ticks."""

    def __init__(self, connect: Callable[[], psycopg.Connection] | None, log: Callable[[str], None]) -> None:
        self._connect, self._log = connect, log
        self._conn: psycopg.Connection | None = None
        self._skip_until: int | None = None
        self._live: set[int] | None = None  # None until the first tick: a restart catches up once

    def _db(self, tick: int) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or (self._skip_until is not None and tick < self._skip_until):
            return None
        try:
            conn = self._connect()
            conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
            conn.commit()
        except Exception as e:  # the duel loop must go on without Postgres
            self._down(tick, "connect", e)
            return None
        self._conn, self._skip_until = conn, None
        return conn

    def _down(self, tick: int, what: str, error: Exception) -> None:
        if self._skip_until is None:
            self._log(f"duels: Postgres {what} failed ({type(error).__name__}); JSONL only, retrying later")
        if self._conn is not None:
            self._conn.close()
        self._conn, self._skip_until = None, tick + RETRY_EVERY_TICKS

    def save(self, tick: int, duels: Iterable[Mapping[str, Any]], *, finished: bool = False) -> int:
        conn = self._db(tick)
        if conn is None:
            return 0
        try:
            return save_duels(conn, duels, None if finished else tick)
        except Exception as e:
            self._down(tick, "write", e)
            return 0

    def read_finished(self, live: Iterable[Mapping[str, Any]]) -> bool:
        """Whether to read `?done=true` this tick: a duel left the live list since the last tick, or this
        is the runner's first tick (a duel may have finished while it was down)."""
        now = {did for d in live if (did := duel_id(d)) is not None}
        before, self._live = self._live, now
        return before is None or bool(before - now)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
