"""Integration test against the local Postgres (`bazaar db up`), in a throwaway schema.

Skipped when Postgres is not running. The test schema is dropped afterwards, so real rows
(which share ids with the synthetic events) are never touched.
"""

import pytest

from bazaar_agent.config import load_settings
from tests.test_intel import EVENTS

psycopg = pytest.importorskip("psycopg")
TEST_SCHEMA = "bazaar_pytest"


@pytest.fixture
def conn():
    from bazaar_agent import db

    try:
        connection = db.connect(load_settings().database_url.get_secret_value())
    except psycopg.OperationalError:
        pytest.skip("local Postgres not running (uv run bazaar db up)")
    with connection.cursor() as cur:
        cur.execute(f"drop schema if exists {TEST_SCHEMA} cascade")
        cur.execute(f"create schema {TEST_SCHEMA}")
        cur.execute(f"set search_path to {TEST_SCHEMA}, public")
    connection.commit()
    db.init_schema(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute(f"drop schema if exists {TEST_SCHEMA} cascade")
    connection.commit()
    connection.close()


@pytest.mark.integration
def test_load_feed_is_idempotent_and_fills_intel_tables(conn):
    from bazaar_agent import db

    first = db.load_feed(conn, EVENTS)
    second = db.load_feed(conn, EVENTS)
    assert first == second == {"feed_events": len(EVENTS), "tape": 2, "dealer_curves": 3}
    with conn.cursor() as cur:
        cur.execute("select bids, asks, final_ask, fill_price from dealer_curves where thread_id = 10")
        assert cur.fetchone() == ([12, 15], [24, 20], 20, 20)
