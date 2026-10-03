"""The supply map in Postgres and on disk: the card scan the agents read back, and the per-card table.

A scan is a list of `GET /api/cards/{id}` bodies. It lives in `supply_assets` (shared by every process
and laptop) and in `.local/supply/scan.jsonl` (this machine; the previous scan is kept beside it for the
diff). Writes never move a row back to an older tick; reads never block a tick (any error: no scan).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import psycopg

from bazaar_agent.supply import SupplyMap, valid_scan

SCAN_FILE = "scan.jsonl"
PREVIOUS_SCAN_FILE = "scan.prev.jsonl"
RELOAD_EVERY = 30  # ticks between re-reads of the stored scan (it changes when someone rescans)
DB_BACKOFF = 10  # reloads to wait before asking Postgres again after it failed (a connect can take 10 s)


def read_scan_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn last line: the rest of the scan still counts
    return valid_scan(rows)


def write_scan_file(folder: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """The new scan replaces the latest; the latest becomes the previous (for `scan_diff`). The new file is
    written in full first, so a failed write leaves the latest scan in place."""
    folder.mkdir(parents=True, exist_ok=True)
    latest = folder / SCAN_FILE
    tmp = folder / f"{SCAN_FILE}.tmp"
    tmp.write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in valid_scan(rows)), encoding="utf-8")
    if latest.is_file():
        latest.replace(folder / PREVIOUS_SCAN_FILE)
    tmp.replace(latest)
    return latest


def save_scan(conn: psycopg.Connection, rows: Iterable[Mapping[str, Any]], tick: int) -> int:
    data = [
        (int(r["id"]), str(r["ref"]), str(r["kind"]), json.dumps(r, allow_nan=False), tick)
        for r in sorted(valid_scan(rows), key=lambda r: int(r["id"]))
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into supply_assets (id, ref, kind, scanned, scanned_tick) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do update set ref = excluded.ref, kind = excluded.kind, scanned = excluded.scanned, "
            "scanned_tick = excluded.scanned_tick "
            "where supply_assets.scanned_tick is null or excluded.scanned_tick >= supply_assets.scanned_tick",
            data,
        )
    conn.commit()
    return len(data)


def load_scan(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("select scanned from supply_assets where scanned is not null order by id").fetchall()
    return valid_scan(r[0] for r in rows)


def save_map(conn: psycopg.Connection, sm: SupplyMap, tick: int) -> int:
    cards = [
        (
            c.ref,
            c.set_code,
            c.rarity,
            c.page,
            c.minted,
            c.print_run,
            c.ours,
            json.dumps(dict(c.holders)),
            c.others,
            c.unplaced,
            tick,
        )
        for c in sorted(sm.cards.values(), key=lambda c: c.ref)
    ]
    sets = [
        (
            s.set_code,
            s.released,
            s.pages_possible,
            json.dumps(list(s.bottleneck)),
            s.our_have,
            s.page_cards,
            sum(sm.packs_opened.values()),
            tick,
        )
        for s in sorted(sm.sets.values(), key=lambda s: s.set_code)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into supply_cards (ref, set_code, rarity, page, minted, print_run, ours, holders, others, "
            "unplaced, updated_tick) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (ref) do "
            "update set set_code = excluded.set_code, rarity = excluded.rarity, page = excluded.page, "
            "minted = excluded.minted, print_run = excluded.print_run, ours = excluded.ours, "
            "holders = excluded.holders, others = excluded.others, unplaced = excluded.unplaced, "
            "updated_tick = excluded.updated_tick "
            "where supply_cards.updated_tick is null or excluded.updated_tick >= supply_cards.updated_tick",
            cards,
        )
        cur.executemany(
            "insert into supply_sets (set_code, released, pages_possible, bottleneck, our_have, page_cards, "
            "packs_opened, updated_tick) values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (set_code) do "
            "update set released = excluded.released, pages_possible = excluded.pages_possible, "
            "bottleneck = excluded.bottleneck, our_have = excluded.our_have, page_cards = excluded.page_cards, "
            "packs_opened = excluded.packs_opened, updated_tick = excluded.updated_tick "
            "where supply_sets.updated_tick is null or excluded.updated_tick >= supply_sets.updated_tick",
            sets,
        )
    conn.commit()
    return len(cards)


class ScanStore:
    """The latest stored scan for the agents: Postgres when it answers, else this machine's file. Re-read
    every RELOAD_EVERY ticks; an error keeps the last rows (or none) and never ends a tick, and a failed
    Postgres is asked again only after DB_BACKOFF reloads."""

    def __init__(
        self,
        folder: Path,
        connect: Callable[[], psycopg.Connection] | None = None,
        log: Callable[[str], None] = lambda message: None,
        every: int = RELOAD_EVERY,
    ) -> None:
        self.folder, self._connect, self._log, self.every = folder, connect, log, every
        self._rows: list[dict[str, Any]] = []
        self._read_tick: int | None = None
        self._db_after: int | None = None  # the tick from which a failed Postgres is asked again

    def rows(self, tick: int) -> list[dict[str, Any]]:
        if self._read_tick is not None and tick - self._read_tick < self.every:
            return self._rows
        self._read_tick = tick
        rows = self._from_db(tick)
        if rows is None:
            rows = read_scan_file(self.folder / SCAN_FILE)
        if rows:  # an empty answer (an outage, no file) never drops the scan we already hold
            self._rows = rows
        return self._rows

    def _from_db(self, tick: int) -> list[dict[str, Any]] | None:
        if self._connect is None or (self._db_after is not None and tick < self._db_after):
            return None
        try:
            with self._connect() as conn:
                return load_scan(conn)
        except psycopg.errors.UndefinedTable:  # nobody stored a scan yet: ask again at the next reload
            return None
        except Exception as e:  # down or unreachable: the last rows (or the file) serve until the back-off ends
            self._db_after = tick + self.every * DB_BACKOFF
            self._log(
                f"supply: stored scan unavailable in Postgres ({type(e).__name__}); retry at tick {self._db_after}"
            )
            return None
