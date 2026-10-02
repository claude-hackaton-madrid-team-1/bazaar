"""Where the simulated world lives between restarts: one JSON snapshot, rewritten every tick.

`SIM_DATABASE_URL` picks the store:
- unset or `sqlite:///path` → a SQLite file (default `.local/sim/world.sqlite`);
- `memory` → nothing persists (tests);
- `postgresql://...` → a Postgres database that MUST be named `bazaar_sim` (or `bazaar_sim_*`).
  Anything else, the team's real `railway` database included, is refused before connecting.
The snapshot goes to table `sim.world` (its own schema), never to the agent's tables.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

import psycopg
from psycopg.conninfo import conninfo_to_dict

SIM_DB_PREFIX = "bazaar_sim"
CONNECT_TIMEOUT_S = 10


class StoreRefused(RuntimeError):
    """The configured database is not one the simulator may write to. The message has no password."""


class Store(Protocol):
    def load(self) -> str | None: ...

    def save(self, snapshot: str, tick: int) -> None: ...

    def describe(self) -> str: ...


class MemoryStore:
    def __init__(self) -> None:
        self.snapshot: str | None = None
        self.saves = 0

    def load(self) -> str | None:
        return self.snapshot

    def save(self, snapshot: str, tick: int) -> None:
        self.snapshot = snapshot
        self.saves += 1

    def describe(self) -> str:
        return "memory (nothing persists)"


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as cx:
            cx.execute("create table if not exists world (id integer primary key, tick integer, state text)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=CONNECT_TIMEOUT_S)

    def load(self) -> str | None:
        with self._connect() as cx:
            row = cx.execute("select state from world where id = 1").fetchone()
        return str(row[0]) if row else None

    def save(self, snapshot: str, tick: int) -> None:
        with self._connect() as cx:
            cx.execute(
                "insert into world (id, tick, state) values (1, ?, ?) "
                "on conflict(id) do update set tick = excluded.tick, state = excluded.state",
                (tick, snapshot),
            )

    def describe(self) -> str:
        return f"sqlite {self.path}"


class PostgresStore:
    def __init__(self, url: str) -> None:
        self.url = url
        self.dbname, self.host = database_name(url), conninfo_to_dict(url).get("host") or "localhost"
        with self._connect() as cx:
            cx.execute("create schema if not exists sim")
            cx.execute(
                "create table if not exists sim.world ("
                "id integer primary key, tick integer not null, saved_at timestamptz not null default now(), "
                "state text not null)"
            )

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self.url, connect_timeout=CONNECT_TIMEOUT_S, application_name="bazaar-sim", autocommit=True
        )

    def load(self) -> str | None:
        with self._connect() as cx:
            row = cx.execute("select state from sim.world where id = 1").fetchone()
        return str(row[0]) if row else None

    def save(self, snapshot: str, tick: int) -> None:
        with self._connect() as cx:
            cx.execute(
                "insert into sim.world (id, tick, state) values (1, %s, %s) "
                "on conflict (id) do update set tick = excluded.tick, state = excluded.state, saved_at = now()",
                (tick, snapshot),
            )

    def describe(self) -> str:
        return f"postgres {self.host}/{self.dbname}"


def database_name(url: str) -> str:
    """The database a Postgres URL names (a `?dbname=` override wins, as libpq would make it)."""
    try:
        return str(conninfo_to_dict(url).get("dbname") or "")
    except psycopg.ProgrammingError:
        raise StoreRefused("SIM_DATABASE_URL is not a valid Postgres URL") from None


def check_sim_database(url: str) -> str:
    name = database_name(url)
    if not (name == SIM_DB_PREFIX or name.startswith(SIM_DB_PREFIX + "_")):
        raise StoreRefused(
            f"the simulator writes only to a database named {SIM_DB_PREFIX!r} (got {name or 'none'!r}): "
            "never the team's real `railway` database"
        )
    return name


def open_store(url: str | None, default_path: Path) -> Store:
    if not url:
        return SqliteStore(default_path)
    if url == "memory":
        return MemoryStore()
    if url.startswith("sqlite:///"):
        return SqliteStore(Path(url.removeprefix("sqlite:///")))
    if url.startswith(("postgres://", "postgresql://")):
        check_sim_database(url)
        return PostgresStore(url)
    raise StoreRefused("SIM_DATABASE_URL must be memory, sqlite:///<path> or a postgresql:// URL to bazaar_sim")
