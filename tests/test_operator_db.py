"""Real PostgreSQL integration, isolated schemas; no game API or production mutation."""

import threading

import pytest

from bazaar_agent import db
from bazaar_agent.ledger_pg import LedgerUnavailable, PgLedger, trade_lock
from bazaar_agent.runtime.operator import Proposals
from tests.test_db import conn, database_url, open_in, schema  # noqa: F401

pytestmark = pytest.mark.integration


def test_bid_refund_recovers_unique_date_and_rejects_prior_refunds(conn, database_url, schema):  # noqa: F811
    maker = PgLedger(lambda: open_in(database_url, schema), source="maker")
    other = PgLedger(lambda: open_in(database_url, schema), source="taker")
    try:
        for kind in ("listing", "spend"):
            maker.record(kind, 1467, 13.7208, 160, "MAL-11")
        assert maker.refund_bid(1467, 12.6792, 160, "MAL-11") == 13.7208
        assert maker.refund_bid(1467, 13.7208, 160, "MAL-11") is None
        # A second same-price listing is ambiguous: keep the conservative fallback.
        for kind in ("listing", "listing", "spend"):
            maker.record(kind, 1468, 13.73, 160, "MAL-11")
        assert maker.refund_bid(1468, 12.68, 160, "MAL-11") == 12.68
        for kind in ("listing", "spend"):
            maker.record(kind, 1469, 13.74, 160, "MAL-11")
        other.record("spend", 1469, 12.69, -160, "MAL-11")
        assert maker.refund_bid(1469, 13.74, 160, "MAL-11") is None
        # Another source cannot date its refund from this source's positive pair.
        for kind in ("listing", "spend"):
            other.record(kind, 1470, 13.75, 160, "MAL-11")
        assert maker.refund_bid(1470, 12.70, 160, "MAL-11") == 12.70
    finally:
        maker.close()
        other.close()


def test_two_makers_cannot_credit_one_bid_twice(conn, database_url, schema):  # noqa: F811
    ledgers = [PgLedger(lambda: open_in(database_url, schema), source="maker") for _ in range(2)]
    for kind in ("listing", "spend"):
        ledgers[0].record(kind, 1467, 13.7208, 160, "MAL-11")
    barrier, results = threading.Barrier(2), []

    def refund(ledger):
        barrier.wait()
        results.append(ledger.refund_bid(1467, 12.6792, 160, "MAL-11"))

    threads = [threading.Thread(target=refund, args=(ledger,)) for ledger in ledgers]
    try:
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(timeout=5)
        assert results.count(13.7208) == 1 and results.count(None) == 1
        assert ledgers[0].spent_since(13) == 0
        assert conn.execute("select count(*) from ledger where kind='spend' and price<0").fetchone()[0] == 1
    finally:
        for ledger in ledgers:
            ledger.close()


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
