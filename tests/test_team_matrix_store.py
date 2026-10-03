"""The team matrix in Postgres: one matrix per world, replaced whole, read back equal, never raising into a tick."""

import secrets
import time
from contextlib import contextmanager, nullcontext
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from bazaar_agent.pgconn import describe
from bazaar_agent.team_matrix import Cell, Summary, TeamMatrix
from bazaar_agent.team_matrix_store import (
    DELETE_CELLS,
    DELETE_SUMMARY,
    INSERT_CELL,
    INSERT_SUMMARY,
    LOAD_CELLS,
    LOAD_SUMMARY,
    LOCK,
    LOCK_KEY,
    RETRY_EVERY,
    TeamMatrixStore,
)
from tests.test_db import database_url, open_in, schema  # noqa: F401 — pytest fixtures

DIRTY = "LAV-03\x00 for a 9/10 page \ud83d"  # a NUL and half an emoji, as another team's text can carry
CLEAN = "LAV-03 for a 9/10 page ?"


def matrix(tick=400, us="t01", wants="SAL-03 for a 9/10 page", t07=True):
    cells = [
        Cell("t14", "LAT-04", 2, 1, False, None, None, 0.5),
        Cell("t14", "SAL-03", 0, 0, True, 9, 10, 0.4),
    ]
    teams = {"t14": Summary("t14", 3, 30.5, 2, "LAT", None, "top 5", wants, "2x LAT-04", "LAT-04 to t03 at 25")}
    if t07:
        cells.append(Cell("t07", "LAV-09", 1, 0, False, None, None, 2 / 3))  # 15 digits would not read back equal
        teams["t07"] = Summary("t07", 12, None, None, None, "v07", None, "", "", "")
    return TeamMatrix(tick, us, tuple(cells), teams)


def same(loaded, m):
    return loaded.tick == m.tick and loaded.us == m.us and set(loaded.cells) == set(m.cells) and loaded.teams == m.teams


# ---------------------------------------------------------------- without Postgres


def test_a_store_without_postgres_keeps_nothing_and_retries_later():
    lines: list[str] = []
    calls = []

    def down():
        calls.append(1)
        raise OSError("refused")

    store = TeamMatrixStore(down, lines.append)
    assert store.save(matrix()) == 0 and store.load() is None
    assert len(calls) == 1 and lines == ["team matrix: connect failed (OSError); kept in memory only"]
    for _ in range(RETRY_EVERY):
        store.save(matrix())
    assert len(calls) == 2 and len(lines) == 1  # tried again after RETRY_EVERY skipped calls, logged once


def test_no_database_or_an_empty_matrix_never_connects():
    lines: list[str] = []
    assert TeamMatrixStore(None, lines.append).save(matrix()) == 0 and TeamMatrixStore(None).load() is None
    calls = []
    store = TeamMatrixStore(lambda: calls.append(1), lines.append)  # would fail if it were called
    assert store.save(TeamMatrix(400, "t01", (), {})) == 0  # an empty build never wipes the stored matrix
    assert calls == [] and lines == []


TABLE_OF = {
    DELETE_CELLS: INSERT_CELL,
    LOAD_CELLS: INSERT_CELL,
    DELETE_SUMMARY: INSERT_SUMMARY,
    LOAD_SUMMARY: INSERT_SUMMARY,
}


class FakeConn:
    """Just enough of a psycopg connection: keeps the rows the store inserts and answers its selects with them."""

    def __init__(self, fail_on=None):
        self.closed, self.autocommit, self.fail_on = False, False, fail_on
        self.statements: list[str] = []
        self.rows: dict[str, list[tuple]] = {INSERT_CELL: [], INSERT_SUMMARY: []}
        self.result: list[tuple] = []

    @contextmanager
    def transaction(self):
        self.statements.append("begin")
        yield
        self.statements.append("commit")

    def cursor(self):
        return nullcontext(self)

    def executemany(self, query, rows):
        for row in rows:
            self.execute(query, row)

    def execute(self, query, params=()):
        if query == self.fail_on:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        self.statements.append(query)
        table = TABLE_OF.get(query)
        if query in self.rows:
            self.rows[query].append(params)
        elif query in (DELETE_CELLS, DELETE_SUMMARY):
            self.rows[table] = [r for r in self.rows[table] if r[0] != params[0]]
        elif query in (LOAD_CELLS, LOAD_SUMMARY):
            mine = [r[1:] for r in self.rows[table] if r[0] == params[0]]
            self.result = sorted(mine, key=lambda r: (r[0], r[1] if query == LOAD_CELLS else ""))
        return self

    def fetchall(self):
        return self.result

    def close(self):
        self.closed = True


def test_a_save_replaces_the_world_in_one_transaction_and_load_reads_it_back():
    fake = FakeConn()
    store = TeamMatrixStore(lambda: fake, world="real")
    assert store.save(matrix()) == 3 and fake.autocommit
    assert fake.statements[:5] == ["begin", "set local statement_timeout = 1500", LOCK, DELETE_CELLS, DELETE_SUMMARY]
    assert fake.statements[-1] == "commit" and fake.statements.count("begin") == 1
    confidences = {r[2]: r[8] for r in fake.rows[INSERT_CELL]}
    assert confidences["LAV-09"] == Decimal("0.6666666666666666")  # repr digits, not float8's 15
    assert same(store.load(), matrix())
    second = matrix(tick=410, wants=DIRTY, t07=False)
    assert store.save(second) == 2
    loaded = store.load()
    assert loaded.tick == 410 and set(loaded.cells) == set(second.cells) and set(loaded.teams) == {"t14"}
    assert loaded.teams["t14"].wants == CLEAN
    assert loaded.card("LAT-04")["spare"][0]["team"] == "t14"  # the indexes are rebuilt


def test_a_failed_write_is_logged_once_closes_the_connection_and_comes_back():
    lines: list[str] = []
    broken = FakeConn(fail_on=INSERT_SUMMARY)  # fails mid-transaction, after the cells went in
    fakes = [broken, FakeConn()]
    store = TeamMatrixStore(lambda: fakes.pop(0), lines.append)
    assert store.save(matrix()) == 0 and store.load() is None  # the load is one of the skipped calls
    assert broken.closed and lines == ["team matrix: write failed (OperationalError); kept in memory only"]
    for _ in range(RETRY_EVERY - 1):
        assert store.save(matrix()) == 0
    assert store.save(matrix()) == 3 and lines[-1] == "team matrix: Postgres writes are back"
    assert fakes == []


def test_a_nan_is_stored_as_null_and_rows_without_summaries_still_load():
    fake = FakeConn()
    m = TeamMatrix(400, "t01", (Cell("t14", "LAT-04", 2, 1, False, None, None, float("nan")),), {})
    store = TeamMatrixStore(lambda: fake)
    assert store.save(m) == 1 and fake.rows[INSERT_CELL][0][8] is None
    loaded = store.load()
    assert loaded.tick == 400 and loaded.us == "" and loaded.cells[0].confidence == 0.0 and loaded.teams == {}


# ---------------------------------------------------------------- local Postgres


@pytest.mark.integration
def test_each_world_keeps_its_own_matrix_and_a_save_replaces_it(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    conn.autocommit = True  # never left idle in a transaction: the schema's teardown must not wait on us
    stores = []
    try:
        db.init_schema(conn)
        real = TeamMatrixStore(lambda: open_in(database_url, schema), world="real")
        sim = TeamMatrixStore(lambda: open_in(database_url, schema), world="sim:127.0.0.1:8765")
        stores = [real, sim]
        sim_matrix = matrix(tick=12, us="t01", t07=False)
        assert real.save(matrix()) == 3 and sim.save(sim_matrix) == 2
        assert same(real.load(), matrix()) and same(sim.load(), sim_matrix)
        # the next window: t07 left the matrix, and a dirty text came in
        second = matrix(tick=410, wants=DIRTY, t07=False)
        assert real.save(second) == 2
        loaded = real.load()
        assert loaded.tick == 410 and set(loaded.cells) == set(second.cells) and set(loaded.teams) == {"t14"}
        assert loaded.teams["t14"].wants == CLEAN
        count = "select count(*) from {} where world = %s"
        for table, n in (("team_matrix", 2), ("team_matrix_summary", 1)):  # no stale rows
            assert conn.execute(sql.SQL(count).format(sql.Identifier(table)), ("real",)).fetchone()[0] == n
        assert same(sim.load(), sim_matrix)  # the other world is untouched
        stores.append(TeamMatrixStore(lambda: open_in(database_url, schema), world="sim:127.0.0.1:9999"))
        assert stores[-1].load() is None  # a world with no matrix yet
    finally:
        for store in stores:
            store.close()
        conn.close()


@pytest.mark.integration
def test_a_save_waits_at_most_the_statement_timeout_behind_another_writer(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    conn.autocommit = True
    lines: list[str] = []
    store = TeamMatrixStore(lambda: open_in(database_url, schema), lines.append)
    try:
        db.init_schema(conn)
        with conn.transaction():
            conn.execute(LOCK, (LOCK_KEY + "real",))  # another process is saving this world
            started = time.monotonic()
            assert store.save(matrix()) == 0
            waited = time.monotonic() - started
        assert 1.0 < waited < 5.0, waited
        assert lines == ["team matrix: write failed (QueryCanceled); kept in memory only"]
        for _ in range(RETRY_EVERY):
            store.save(matrix())
        assert store.save(matrix()) == 3 and lines[-1] == "team matrix: Postgres writes are back"
    finally:
        store.close()
        conn.close()


@pytest.mark.integration
def test_the_schema_grants_the_matrix_to_every_role_that_reads_the_feed(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    if not describe(database_url).is_local_default:
        pytest.skip("roles are cluster-wide: only on the local docker Postgres")
    role = f"bazaar_pytest_ro_{secrets.token_hex(4)}"
    conn = open_in(database_url, schema)
    conn.autocommit = True
    created = False
    can = "select has_table_privilege(%s, %s, 'SELECT')"
    try:
        db.init_schema(conn)
        conn.execute(sql.SQL("create role {}").format(sql.Identifier(role)))
        created = True
        conn.execute(sql.SQL("grant select on feed_events to {}").format(sql.Identifier(role)))
        assert conn.execute(can, (role, "team_matrix")).fetchone()[0] is False
        db.init_schema(conn)
        db.init_schema(conn)  # a re-run finds nothing missing
        for table in ("team_matrix", "team_matrix_summary"):
            assert conn.execute(can, (role, table)).fetchone()[0] is True
        assert conn.execute(can, (role, "venue_broker_keys")).fetchone()[0] is False  # only the matrix
    finally:
        if created:
            conn.execute(sql.SQL("drop owned by {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))
        conn.close()
