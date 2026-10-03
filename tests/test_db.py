"""Integration tests against DATABASE_URL (local docker or the shared Railway DB), in throwaway schemas.

Skipped when Postgres is unreachable. Every test gets its own schema, `bazaar_pytest_<random>`, so
several teammates can run the suite at once against one shared database; it is dropped afterwards,
and real tables (which share ids with the synthetic events) are never touched.
"""

import secrets
import threading

import psycopg
import pytest
from psycopg import sql

from bazaar_agent.config import load_settings
from bazaar_agent.monitor import Alert, TraderSnapshot
from tests.test_intel import EVENTS

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url():
    from bazaar_agent import db

    url = load_settings().database_url.get_secret_value()
    try:
        db.connect(url, app="bazaar-pytest").close()
    except (psycopg.OperationalError, db.DatabaseUrlError):
        pytest.skip("Postgres not reachable (uv run bazaar db up, or set DATABASE_URL)")
    return url


@pytest.fixture
def schema(database_url):
    from bazaar_agent import db

    name = sql.Identifier(f"bazaar_pytest_{secrets.token_hex(4)}")
    with db.connect(database_url, app="bazaar-pytest") as admin:
        admin.execute(sql.SQL("create schema {}").format(name))
    yield name
    with db.connect(database_url, app="bazaar-pytest") as admin:
        admin.execute(sql.SQL("drop schema if exists {} cascade").format(name))


def open_in(database_url, schema):
    """A connection whose tables all resolve to the test schema (`public` only for the vector type)."""
    from bazaar_agent import db

    connection = db.connect(database_url, app="bazaar-pytest")
    connection.execute(sql.SQL("set search_path to {}, public").format(schema))
    connection.commit()
    return connection


@pytest.fixture
def conn(database_url, schema):
    from bazaar_agent import db

    connection = open_in(database_url, schema)
    db.init_schema(connection)
    yield connection
    connection.close()


def test_load_feed_is_idempotent_and_fills_intel_tables(conn):
    from bazaar_agent import db

    first = db.load_feed(conn, EVENTS)
    second = db.load_feed(conn, EVENTS)
    assert first == second == {"feed_events": len(EVENTS), "tape": 2, "dealer_curves": 3}
    with conn.cursor() as cur:
        cur.execute("select bids, asks, final_ask, fill_price from dealer_curves where thread_id = 10")
        assert cur.fetchone() == ([12, 15], [24, 20], 20, 20)


def test_schema_reruns_cleanly_and_embeddings_follow_pgvector(conn):
    from bazaar_agent import db

    vector = db.init_schema(conn)
    assert db.init_schema(conn) is vector  # idempotent
    tables = dict(db.table_counts(conn))
    assert {"feed_events", "traders", "alerts", "dealer_curves", "messages", "learnings"} <= set(tables)
    rows = conn.execute(
        "select table_name from information_schema.columns where table_schema = current_schema() "
        "and column_name = 'embedding' order by 1"
    ).fetchall()
    assert [r[0] for r in rows] == (["learnings", "messages", "trader_behaviors"] if vector else [])


def test_two_sessions_can_apply_the_schema_at_once(database_url, schema):
    from bazaar_agent import db

    errors: list[BaseException] = []

    def apply() -> None:
        try:
            with open_in(database_url, schema) as connection:
                db.init_schema(connection)
        except BaseException as e:  # surfaced by the assert below
            errors.append(e)

    workers = [threading.Thread(target=apply) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert errors == []


def test_alerts_from_two_monitors_are_stored_once(conn):
    from bazaar_agent import db

    alerts = [Alert(76, "level_announced", "chato", "El Chato announced"), Alert(76, "feed:announcement", "", "hi")]
    assert db.insert_alerts(conn, alerts) == 2
    assert db.insert_alerts(conn, alerts + alerts) == 0  # the other laptop raises the same ones
    assert db.insert_alerts(conn, [Alert(77, "feed:announcement", "", "hi")]) == 1  # a new tick is news
    assert conn.execute("select count(*) from alerts").fetchone() == (3,)


def test_a_lagging_monitor_never_rolls_traders_back(conn):
    from bazaar_agent import db

    def trader(status, level):
        return TraderSnapshot("chato", "dealer", "El Chato", status, level, "{}", {}, {})

    db.upsert_traders(conn, [trader("active", 2)], tick=80)
    db.upsert_traders(conn, [trader("announced", None)], tick=79)  # the other laptop, one tick behind
    db.upsert_traders(conn, [trader("active", None)], tick=81)  # newer, but without a level
    row = conn.execute("select status, level, first_seen_tick, last_seen_tick, updated_tick from traders").fetchone()
    assert row == ("active", 2, 80, 81, 81)


def test_a_behind_writer_never_shrinks_a_dealer_curve(conn):
    from bazaar_agent import db

    def curve():
        return conn.execute("select bids, asks, fill_price from dealer_curves where thread_id = 10").fetchone()

    behind = [e for e in EVENTS if e["id"] <= 4]  # before Abuela's final ask and the settlement
    db.load_curves(conn, behind)
    assert curve() == ([12, 15], [24], None)
    db.load_curves(conn, EVENTS)  # a fresher writer moves the row forward
    assert curve() == ([12, 15], [24, 20], 20)
    db.load_curves(conn, behind)  # the lagging one never moves it back
    assert curve() == ([12, 15], [24, 20], 20)


def test_a_later_started_capture_leaves_derived_tables_alone(conn):
    from bazaar_agent import db

    assert db.load_history(conn, EVENTS, tick=50)
    profiles = conn.execute("select team, fills from competitor_profiles order by team").fetchall()
    recent_window = [e for e in EVENTS if e["id"] > 6]  # a capture started after thread 10
    assert not db.load_history(conn, recent_window, tick=60)
    assert conn.execute("select team, fills from competitor_profiles order by team").fetchall() == profiles
    assert db.load_feed(conn, recent_window)["dealer_curves"] == 0


def test_monitor_reconnects_after_the_server_drops_its_connection(database_url, schema, conn):
    from bazaar_agent import db
    from bazaar_agent.pgconn import Reconnector

    notes: list[str] = []
    pg = Reconnector(lambda: open_in(database_url, schema), notes.append)
    first = pg.get()
    assert first is not None
    conn.execute("select pg_terminate_backend(%s)", (first.info.backend_pid,))
    conn.commit()
    with pytest.raises(psycopg.OperationalError):
        db.insert_alerts(first, [Alert(1, "k", "s", "lost")])
    pg.drop()  # the monitor's except branch
    second = pg.get()
    assert second is not None and second is not first and not second.closed
    assert db.insert_alerts(second, [Alert(1, "k", "s", "after reconnect")]) == 1
    pg.drop()


def test_check_reports_the_live_server(database_url):
    from bazaar_agent import db

    report = db.check(database_url)
    assert len(report.round_trips_ms) == 3 and report.server_version
    ok, lines = db.run_check(database_url)
    assert ok and any(line.startswith("pgvector") for line in lines)


def test_a_check_against_a_closed_port_fails_fast():
    from bazaar_agent import db

    ok, lines = db.run_check("postgresql://nobody:Never-Printed-pw@127.0.0.1:1/x?connect_timeout=2")
    assert not ok and "Never-Printed-pw" not in "\n".join(lines)


def test_dealer_curves_tag_our_threads_and_an_unaware_writer_keeps_the_tag(conn):
    from bazaar_agent import db

    def tags():
        return conn.execute("select thread_id, ours from dealer_curves order by thread_id").fetchall()

    db.load_curves(conn, EVENTS)  # a writer that does not know our team id (an older monitor)
    assert tags() == [(10, None), (11, None), (12, None)]
    db.load_curves(conn, EVENTS, ours="t06")
    assert tags() == [(10, False), (11, True), (12, True)]
    db.load_curves(conn, EVENTS)  # unaware again: the tag stays
    assert tags() == [(10, False), (11, True), (12, True)]


def test_competitor_profiles_leave_us_out_and_drop_a_stale_row_for_us(conn):
    from bazaar_agent import db
    from bazaar_agent.intel import team_flows

    db.save_competitors(conn, team_flows(EVENTS), tick=50)  # an older monitor stored us as a competitor
    assert [r[0] for r in conn.execute("select team from competitor_profiles order by team")] == ["t05", "t06"]
    db.save_competitors(conn, team_flows(EVENTS), tick=51, ours="t06")
    assert [r[0] for r in conn.execute("select team from competitor_profiles order by team")] == ["t05"]


def test_their_events_keeps_everything_but_our_own_activity(conn):
    from bazaar_agent import db

    db.load_events(conn, EVENTS)
    everything = conn.execute("select count(*) from their_events").fetchone()
    assert everything == (len(EVENTS),)  # no 'us' trader row yet: nothing is hidden
    db.upsert_traders(conn, [TraderSnapshot("t06", "team", "t06", "us", None, "{}", {}, {})], tick=5)
    ids = [r[0] for r in conn.execute("select id from their_events order by id")]
    assert ids == [1, 2, 3, 4, 5, 6]  # thread 11/12, its messages, the sale and the listing are t06's
    assert conn.execute("select count(*) from feed_events").fetchone() == (len(EVENTS),)


def test_ask_rows_read_sent_asks_and_rests_from_postgres(database_url, schema, tmp_path):
    from bazaar_agent import db
    from bazaar_agent.decisions import RELIST_REST, Decision, DecisionLog

    log = DecisionLog(tmp_path, connect=lambda: open_in(database_url, schema))
    conn = open_in(database_url, schema)
    db.init_schema(conn)
    conn.close()

    def row(kind, tick, inputs, dry=False):
        return log.decide(Decision("maker", tick, kind, inputs, "r", "allowed", True, "approved", dry))

    log.settle(row("post_ask", 10, {"asset_id": 11, "price": 10}), "done")
    log.settle(row("post_ask", 11, {"asset_id": 11, "price": 10}), "failed")
    log.settle(row("post_ask", 12, {"asset_id": 11, "price": 9}, dry=True), "done")
    row("post_ask", 13, {"asset_id": 11, "price": 9})  # approved, never sent
    row(RELIST_REST, 15, {"asset_id": 11, "until_tick": 55})
    assert log.ask_rows("maker", 5) == [(10, "post_ask", 11, 10), (15, RELIST_REST, 11, 55)]
    assert not list((tmp_path / "agents").glob("*.jsonl"))  # every row went to Postgres
