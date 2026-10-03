"""The guardrail ledger in Postgres: one accepts-per-tick count and one spend cap for every machine.

The taker and maker on Railway, `bazaar-duels`, and the CLI on a laptop all write the same `ledger`
table, so `max_accepts_per_tick` and `max_spend_per_game_hour` hold for the whole team, not per
process. `reserve_accept` counts and inserts under one advisory lock: two processes can never both
take the last accept of a tick. When DATABASE_URL is unreachable at start, `open_ledger` falls back
to the JSONL file (this machine only) and says so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import psycopg

from bazaar_agent.guardrails import Ledger, LedgerStore, is_pack

ACCEPT_LOCK = "bazaar_agent.ledger.accept"
STATEMENT_TIMEOUT_MS = 3000  # a stuck query must not eat the tick


class LedgerUnavailable(RuntimeError):
    """The shared ledger cannot be read or written now. Callers fail closed: no write this tick."""


class PgLedger:
    """`LedgerStore` on the Postgres `ledger` table (created by `bazaar db init`)."""

    def __init__(self, conn: psycopg.Connection, source: str = "") -> None:
        conn.autocommit = True  # each call is its own transaction; reserve_accept opens one explicitly
        conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
        self._conn, self._source = conn, source
        self.where = "postgres ledger table"

    def _one(self, sql: str, args: tuple[object, ...]) -> int:
        try:
            row = self._conn.execute(sql, args).fetchone()  # type: ignore[arg-type]  # fixed SQL above
        except psycopg.Error as e:
            raise LedgerUnavailable(f"ledger read failed ({type(e).__name__})") from None
        return int(row[0] or 0) if row else 0

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None:
        try:
            self._conn.execute(
                "insert into ledger (kind, tick, t_hours, price, item, source) values (%s, %s, %s, %s, %s, %s)",
                (kind, tick, t_hours, int(price), item, self._source),
            )
        except psycopg.Error as e:
            raise LedgerUnavailable(f"ledger write failed ({type(e).__name__})") from None

    def spent_since(self, t_hours: float) -> int:
        return self._one("select sum(price) from ledger where kind = 'spend' and t_hours > %s", (t_hours,))

    def packs_since(self, t_hours: float) -> Counter[str]:
        try:
            rows = self._conn.execute(
                "select item, count(*) from ledger where kind = 'spend' and t_hours > %s and price > 0 group by item",
                (t_hours,),
            ).fetchall()
        except psycopg.Error as e:
            raise LedgerUnavailable(f"ledger read failed ({type(e).__name__})") from None
        return Counter({str(item): int(n) for item, n in rows if is_pack(str(item or ""))})

    def accepts_in_tick(self, tick: int) -> int:
        return self.count_in_tick("accept", tick)

    def accept_items(self, tick: int) -> list[str]:
        try:
            rows = self._conn.execute(
                "select item from ledger where kind = 'accept' and tick = %s order by id", (tick,)
            ).fetchall()
        except psycopg.Error as e:
            raise LedgerUnavailable(f"ledger read failed ({type(e).__name__})") from None
        return [str(item or "") for (item,) in rows]

    def spend_rows(self, prefix: str, t_hours: float) -> list[tuple[str, int, int]]:
        try:
            rows = self._conn.execute(
                "select item, price, tick from ledger where kind = 'spend' and t_hours > %s "
                "and starts_with(item, %s) order by id",
                (t_hours, prefix),
            ).fetchall()
        except psycopg.Error as e:
            raise LedgerUnavailable(f"ledger read failed ({type(e).__name__})") from None
        return [(str(item), int(price or 0), int(tick or 0)) for item, price, tick in rows]

    def count_in_tick(self, kind: str, tick: int) -> int:
        return self._one("select count(*) from ledger where kind = %s and tick = %s", (kind, tick))

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        """True when this process got one of the tick's accepts (and it is recorded); False when spent.

        Atomic twice over: an advisory lock serializes the count-and-insert, and the unique index on
        (tick, slot) refuses a second row for the same slot even from a writer that skipped the lock."""
        try:
            with self._conn.transaction():
                self._conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ACCEPT_LOCK,))
                taken = self._conn.execute(
                    "select count(*) from ledger where kind = 'accept' and tick = %s", (tick,)
                ).fetchone()
                slot = int(taken[0]) + 1 if taken is not None else 1
                if slot > limit:
                    return False
                self._conn.execute(
                    "insert into ledger (kind, tick, t_hours, price, item, source, slot) "
                    "values ('accept', %s, %s, %s, %s, %s, %s)",
                    (tick, t_hours, int(price), item, self._source, slot),
                )
        except psycopg.errors.UniqueViolation:
            return False
        except psycopg.Error as e:
            raise LedgerUnavailable(f"accept reservation failed ({type(e).__name__})") from None
        return True

    def close(self) -> None:
        self._conn.close()


def open_ledger(
    data_dir: Path,
    *,
    source: str,
    connect: Callable[[], psycopg.Connection] | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> LedgerStore:
    """The shared Postgres ledger when DATABASE_URL answers, else this machine's JSONL file."""
    from bazaar_agent import db

    opener = connect or (lambda: db.connect_ready(app=f"bazaar-{source}"))
    try:
        ledger: LedgerStore = PgLedger(opener(), source)
    except Exception as e:  # unreachable, bad URL, schema lock timeout: the file still guards this machine
        log(f"ledger: Postgres unavailable ({type(e).__name__}), using {data_dir / 'ledger.jsonl'} (this machine only)")
        return Ledger(data_dir / "ledger.jsonl")
    log("ledger: shared Postgres table (accepts per tick and spend counted across every machine)")
    return ledger
