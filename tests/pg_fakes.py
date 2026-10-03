"""A psycopg-shaped Postgres on SQLite, for the ledger's SQL offline: one database file shared by every
"process", and a switch that takes the server down (open refused, open connections broken) and back."""

import contextlib
import sqlite3

import psycopg

LEDGER = """
create table if not exists ledger (
  id integer primary key autoincrement, kind text not null check (kind in ('spend','accept','listing')),
  tick int not null, t_hours real not null, price int not null default 0,
  item text not null default '', source text, slot int);
create unique index if not exists ledger_accept_slot on ledger (tick, slot) where kind = 'accept';
"""


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakePostgres:
    """The shared server. `connect()` is what `PgLedger` calls; `up = False` is an outage."""

    def __init__(self, path):
        self.path, self.up, self.opens = str(path), True, 0
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.executescript(LEDGER)

    def connect(self):
        self.opens += 1
        if not self.up:
            raise psycopg.OperationalError("connection refused (fake outage)")
        return FakeConnection(self)

    def rows(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            return db.execute("select kind, tick, item, source, slot from ledger order by id").fetchall()


class FakeConnection:
    def __init__(self, server):
        self._server, self._db = server, sqlite3.connect(server.path, isolation_level=None, timeout=5)
        self._db.create_function("starts_with", 2, lambda text, prefix: int(str(text).startswith(str(prefix))))
        self.autocommit, self.closed, self.broken = False, False, False

    def execute(self, sql, args=()):
        if self.closed:
            raise psycopg.OperationalError("the connection is closed")
        if not self._server.up:
            self.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly (fake outage)")
        if sql.startswith("set ") or "pg_advisory_xact_lock" in sql:
            return _Rows([(None,)])
        try:
            return _Rows(self._db.execute(sql.replace("%s", "?"), tuple(args)).fetchall())
        except sqlite3.IntegrityError as e:
            raise psycopg.errors.UniqueViolation(str(e)) from None

    @contextlib.contextmanager
    def transaction(self):
        self._db.execute("begin immediate")  # one writer at a time, like the advisory lock
        try:
            yield
        except BaseException:
            self._db.execute("rollback")
            raise
        self._db.execute("commit")

    def close(self):
        if not self.closed:
            self.closed = True
            self._db.close()
