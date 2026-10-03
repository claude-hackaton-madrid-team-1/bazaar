"""The full Market Test bench book, recorded every tick for later study (`bench_books`, `bench_books.jsonl`).

The organisers publish no trader limits, arrivals or patience, and `bench.finished` only gives the session's
efficiency. The one source is the book our broker already reads each tick (`GET /api/broker/book` →
`bench_offers`), so this keeps every raw offer, with every field the server sends, at no extra request.

Never into the tick: `record()` appends one JSONL line (a local file) and hands the Postgres rows to a daemon
worker through a bounded queue (a full queue drops the rows, a slow or dead Postgres costs the tick nothing).
Every failure is logged once by its exception class only (never its text: libpq echoes URLs and passwords) and
the capture goes on. Postgres is written for the real game only; a simulator keeps the JSONL beside its own
data dir.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from bazaar_agent.db import jsonb_safe

STATEMENT_TIMEOUT_MS = 1500
QUEUE_SIZE = 8  # ticks waiting for the worker; a full queue drops the newest rows
RETRY_EVERY = 5  # batches skipped after a failed connect before Postgres is tried again
FILE_NAME = "bench_books.jsonl"
COLUMNS = "world, run, tick, offer_id, side, quote, venue, fee_bps, fee_per_card, offer"
INSERT = (
    f"insert into bench_books ({COLUMNS}) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "on conflict (world, run, tick, offer_id) do nothing"
)

Row = tuple[Any, ...]


def run_of(offer_id: object) -> str:
    """The bench run an offer belongs to: "b35" in the id "b35-17"."""
    return str(offer_id).split("-")[0]


def side_and_quote(offer: dict[str, Any]) -> tuple[str, int | None]:
    """A bench seller asks `want.cash`; a bench buyer bids `give.cash` (the kit's starter broker)."""
    want = offer.get("want")
    give = offer.get("give")
    ask = want.get("cash") if isinstance(want, dict) else None
    if ask:
        return "sell", ask if isinstance(ask, int) else None
    bid = give.get("cash") if isinstance(give, dict) else None
    return "buy", bid if isinstance(bid, int) else None


def rows_for(
    world: str, tick: int, offers: Iterable[Any], venue: str | None, fee_bps: int, fee_per_card: int
) -> list[Row]:
    """One row per raw offer that has an id: the whole offer dict stays in `offer`."""
    out: list[Row] = []
    for offer in offers:
        if not isinstance(offer, dict) or offer.get("id") is None:
            continue
        side, quote = side_and_quote(offer)
        out.append(
            (world, run_of(offer["id"]), tick, str(offer["id"]), side, quote, venue, fee_bps, fee_per_card, offer)
        )
    return out


class BenchBooks:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        stats_dir: Path | None,
        log: Callable[[str], None] = lambda message: None,
        *,
        world: str = "real",
        venue: str | None = None,
        inline: bool = False,
    ) -> None:
        self._connect, self.stats_dir, self._log = connect, stats_dir, log
        self.world, self.venue, self._inline = world, venue, inline
        self._queue: queue.Queue[list[Row]] = queue.Queue(QUEUE_SIZE)
        self._worker: threading.Thread | None = None
        self._conn: psycopg.Connection | None = None
        self._skip = 0
        self._failed: set[str] = set()
        self.stored = 0  # rows Postgres accepted (conflicts included), for the tests

    def record(self, tick: int, offers: Sequence[Any], fee_bps: int = 0, fee_per_card: int = 0) -> None:
        """Keep this tick's bench book. An empty book (no Market Test running) writes nothing. Never raises."""
        try:
            if not offers:
                return
            self._append(tick, offers, fee_bps, fee_per_card)
            if self._connect is None:
                return
            rows = rows_for(self.world, tick, offers, self.venue, fee_bps, fee_per_card)
            if rows:
                self._submit(rows)
        except Exception as e:  # noqa: BLE001 — a capture never breaks a tick
            self._fail("record", e)

    def _append(self, tick: int, offers: Sequence[Any], fee_bps: int, fee_per_card: int) -> None:
        if self.stats_dir is None:
            return
        line = {
            "tick": tick,
            "world": self.world,
            "venue": self.venue,
            "fee_bps": fee_bps,
            "fee_per_card": fee_per_card,
        }
        line["offers"] = list(offers)  # type: ignore[assignment]
        try:
            self.stats_dir.mkdir(parents=True, exist_ok=True)
            with (self.stats_dir / FILE_NAME).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=True, default=str) + "\n")  # ASCII: a lone surrogate is fine
        except (OSError, ValueError, TypeError) as e:
            self._fail("file", e)

    def _submit(self, rows: list[Row]) -> None:
        if self._inline:
            self._write(rows)
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="bench-books", daemon=True)
            self._worker.start()
        try:
            self._queue.put_nowait(rows)
        except queue.Full:
            self._fail("queue", RuntimeError("full"))

    def _work(self) -> None:
        while True:
            rows = self._queue.get()
            try:
                self._write(rows)
            except Exception as e:  # noqa: BLE001
                self._fail("worker", e)

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
        except Exception as e:  # noqa: BLE001
            self._skip = RETRY_EVERY
            self._fail("connect", e)
            return None
        self._conn = conn
        return conn

    def _write(self, rows: list[Row]) -> None:
        conn = self._db()
        if conn is None:
            return
        payload = [(*r[:-1], Jsonb(jsonb_safe(r[-1]))) for r in rows]
        try:
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                with conn.cursor() as cur:
                    cur.executemany(INSERT, payload)
        except Exception as e:  # noqa: BLE001
            self._fail("write", e)
            self._conn = None
            return
        self.stored += len(rows)
        if self._failed:
            self._log("bench books: Postgres writes are back")
            self._failed = set()

    def _fail(self, where: str, error: BaseException) -> None:
        key = f"{where}:{type(error).__name__}"
        if key in self._failed:
            return
        self._failed.add(key)
        self._log(f"bench books: {where} failed ({type(error).__name__}); the capture goes on without it")
