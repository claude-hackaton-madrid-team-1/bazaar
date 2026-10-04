"""Real PostgreSQL integration, isolated schemas; no game API or production mutation."""

import threading

import pytest

from bazaar_agent import db
from bazaar_agent.ledger_pg import LedgerUnavailable, PgLedger, trade_lock
from bazaar_agent.runtime.operator import Proposals
from tests.test_db import conn, database_url, open_in, schema  # noqa: F401

pytestmark = pytest.mark.integration


def test_publication_kind_migration_is_repeatable(conn):  # noqa: F811
    conn.execute("alter table ledger drop constraint ledger_kind_check")
    conn.execute("alter table ledger add constraint ledger_kind_check check (kind in ('spend','accept','listing'))")
    conn.commit()
    db.init_schema(conn)
    db.init_schema(conn)
    for kind in ("publication_pending", "publication_confirm", "publication_release", "operator_say:5"):
        conn.execute("insert into ledger(kind,tick,t_hours,item) values (%s,1,0,%s)", (kind, "test"))
    conn.commit()
    assert conn.execute("select count(*) from ledger").fetchone()[0] == 4


def test_durable_proposal_claim_is_atomic(conn, database_url, schema):  # noqa: F811
    store = Proposals(lambda: open_in(database_url, schema))
    store.create(
        {
            "proposal_id": "a" * 32,
            "world": "real",
            "created_tick": 1,
            "expires_tick": 5,
            "status": "proposed",
            "scope": "test",
        }
    )
    assert store.transition("a" * 32, "proposed", "approved")
    barrier, results = threading.Barrier(2), []

    def claim():
        barrier.wait()
        results.append(store.transition("a" * 32, "approved", "executing"))

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert sorted(results) == [False, True]
    assert store.unresolved("test")
    assert store.get("a" * 32)["status"] == "executing"


def test_publication_mutex_spans_real_database_connections(conn, database_url, schema):  # noqa: F811
    ledger = PgLedger(lambda: open_in(database_url, schema))
    other = PgLedger(lambda: open_in(database_url, schema))
    try:
        with trade_lock(ledger), pytest.raises(LedgerUnavailable), trade_lock(other):
            pytest.fail("second connection entered publication lock")
        with trade_lock(other):
            other.record("publication_pending", 1, 0, item="{}")
        assert ledger.publication_rows() == [("publication_pending", "{}")]
    finally:
        ledger.close()
        other.close()
