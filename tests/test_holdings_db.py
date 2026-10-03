"""Holdings against Postgres (local docker or DATABASE_URL), each test in its own throwaway schema.

The freshness rules end to end: a stale tick reads live, a send of ours invalidates, a thread message clouds
the tick, an old row is not trusted, two agents that start a tick together make ONE /me call, two writers in
one tick never move the row backwards, and the MCP tools answer from the stored snapshot with its tick and age.
Skipped when Postgres is unreachable.
"""

import threading
import time
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from bazaar_agent import catalog_db
from bazaar_agent import holdings as hd
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.holdings import REAL, Holdings, Scope, SharedDb, WriteTracker
from tests.agent_fakes import clock
from tests.test_db import database_url, open_in, schema  # noqa: F401  (fixtures)
from tests.test_strategy import CATALOG, ME

pytestmark = pytest.mark.integration

TICK = 100


@pytest.fixture
def opener(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    first = open_in(database_url, schema)
    db.init_schema(first)
    first.close()
    opened = []

    def open_conn():
        conn = open_in(database_url, schema)
        opened.append(conn)
        return conn

    yield open_conn
    for conn in opened:
        conn.close()


class Game:
    """/api/me as the server would answer it: counts calls, can be slow, holdings can change."""

    def __init__(self, tick=TICK, delay=0.0):
        self.payload = deepcopy({**ME, "tick": tick})
        self.calls = 0
        self.delay = delay
        self._lock = threading.Lock()

    def me(self):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return deepcopy(self.payload)


def reader(opener, game, name="taker", **rules):
    return Holdings(game.me, SharedDb(opener), reader=name, rules=Guardrails(**rules), team="t01")


def test_the_second_reader_in_a_tick_answers_from_the_first_ones_snapshot(opener):
    game = Game()
    taker, maker = reader(opener, game, "taker"), reader(opener, game, "maker")
    first = taker.me(clock(tick=TICK))
    second = maker.me(clock(tick=TICK))
    assert (first.source, first.why) == ("live", "no snapshot this tick")
    assert (second.source, second.why, second.read_by, second.tick) == ("db", "fresh", "taker", TICK)
    assert second.me == first.me and second.digest == first.digest and game.calls == 1
    assert maker.counts["db"] == 1 and taker.counts["live"] == 1


def test_the_stored_row_never_holds_the_broker_key(opener):
    game = Game()
    game.payload["starter_broker_key"] = "bk_live_SECRET_000"
    taker = reader(opener, game)
    taker.me(clock(tick=TICK))
    with taker.shared.session() as conn:
        (me,) = conn.execute("select me from me_snapshots where team = 't01'").fetchone()
        assert "starter_broker_key" not in me and "bk_live_SECRET_000" not in str(me)
    assert "starter_broker_key" not in reader(opener, game, "maker").me(clock(tick=TICK)).me


def test_a_simulator_snapshot_never_answers_for_the_real_game(opener):
    game = Game()
    sim = Holdings(game.me, SharedDb(opener), reader="taker", rules=Guardrails(), team="t01",
                   scope=Scope("sim:127.0.0.1:8765", False))  # fmt: skip
    sim.me(clock(tick=TICK))  # sim-team1 is t01 too, at the same tick number
    assert sim.shared.call(lambda conn: None, timeout_s=5, queue_if_stuck=True)[0]
    mcp = reader(opener, game, "mcp")
    real = mcp.me(clock(tick=TICK))
    assert (real.source, real.why, game.calls) == ("live", "no snapshot this tick", 2)
    with mcp.shared.session() as conn:
        worlds = conn.execute("select world from me_snapshots order by world").fetchall()
    assert worlds == [("real",), ("sim:127.0.0.1:8765",)]


def test_a_simulator_in_a_shared_database_writes_no_world_less_table(opener):
    game = Game()
    sim = Holdings(game.me, SharedDb(opener), reader="taker", rules=Guardrails(), team="t01",
                   scope=Scope("sim:127.0.0.1:8765", False))  # fmt: skip
    sim.me(clock(tick=TICK))
    # The live answer precedes its commit; the same worker's session waits for that commit.
    with sim.shared.session() as conn:
        assert conn.execute("select count(*) from snapshots").fetchone() == (0,)
        assert conn.execute("select count(*) from me_snapshots").fetchone() == (1,)


def test_a_tampered_row_is_never_a_decision_input(opener):
    game = Game()
    taker = reader(opener, game)
    taker.me(clock(tick=TICK))
    with taker.shared.session() as conn:
        conn.execute("update me_snapshots set me = jsonb_set(me, '{cash}', '5000')")
    forged = reader(opener, game, "maker").me(clock(tick=TICK))
    assert (forged.source, forged.why, forged.me["cash"]) == ("live", "stored row does not match itself", ME["cash"])


def test_a_lost_bump_is_caught_up_by_the_next_one(opener):
    game = Game()
    shared = SharedDb(opener)
    reader(opener, game).me(clock(tick=TICK))
    tracker = WriteTracker(shared, "taker")
    tracker.missed = True  # a send went out while Postgres was away
    tracker("POST", "/api/offers", "after")
    with shared.session() as conn:
        assert hd.current_epoch(conn, "real") == 2  # the catch-up bump plus this one
    assert not tracker.missed


def test_a_row_from_a_reset_world_never_hides_the_current_tick(opener):
    game = Game(tick=19)
    reader(opener, game).me(clock(tick=19))  # a simulator world that was later reset
    game.payload["tick"] = 1
    reader(opener, game).me(clock(tick=1))  # the new world's tick 1
    found = reader(opener, game, "maker").me(clock(tick=1))
    assert (found.source, found.tick, game.calls) == ("db", 1, 2)


def test_a_snapshot_from_an_older_tick_is_never_used(opener):
    game = Game(tick=TICK)
    reader(opener, game).me(clock(tick=TICK))
    game.payload["tick"] = TICK + 1
    later = reader(opener, game, "maker").me(clock(tick=TICK + 1))
    assert (later.source, later.why, later.tick, game.calls) == ("live", "no snapshot this tick", TICK + 1, 2)


def test_a_send_of_ours_invalidates_every_older_snapshot(opener):
    game = Game()
    shared = SharedDb(opener)
    reader(opener, game).me(clock(tick=TICK))
    WriteTracker(shared, "maker")("POST", "/api/offers", "after")
    again = reader(opener, game, "mcp").me(clock(tick=TICK))
    assert (again.source, again.why, game.calls) == ("live", "a write of ours since it was read", 2)
    assert reader(opener, game, "cli").me(clock(tick=TICK)).source == "db"  # the re-read is current again


def test_a_duel_move_does_not_invalidate_holdings(opener):
    game = Game()
    reader(opener, game).me(clock(tick=TICK))
    WriteTracker(SharedDb(opener), "duels")("POST", "/api/duels/7/messages", "after")
    assert reader(opener, game, "mcp").me(clock(tick=TICK)).source == "db" and game.calls == 1


def test_a_thread_message_this_tick_clouds_every_snapshot_of_the_tick(opener):
    game = Game()
    WriteTracker(SharedDb(opener), "taker")("POST", "/api/threads/12/messages", "after")
    reader(opener, game).me(clock(tick=TICK, next_tick_in=40.0))  # read AFTER the message, same tick
    cloudy = reader(opener, game, "maker").me(clock(tick=TICK, next_tick_in=40.0))
    assert cloudy.source == "live" and game.calls == 2
    assert cloudy.why == "a thread message of ours this tick"


def test_a_thread_message_before_this_tick_does_not_cloud_it(opener):
    game = Game()
    shared = SharedDb(opener)
    WriteTracker(shared, "taker")("POST", "/api/threads/12/messages", "after")
    with shared.session() as conn:  # the message went out 30 s ago, in the previous tick
        conn.execute("update holdings_state set thread_message_at = clock_timestamp() - interval '30 seconds'")
    reader(opener, game).me(clock(tick=TICK, next_tick_in=55.0))  # 5 s into this tick
    assert reader(opener, game, "maker").me(clock(tick=TICK, next_tick_in=55.0)).source == "db"


def test_a_snapshot_older_than_the_max_age_is_read_again(opener):
    game = Game()
    reader(opener, game).me(clock(tick=TICK))
    old = reader(opener, game, "maker", holdings_max_age_s=0.0).me(clock(tick=TICK))
    assert (old.source, old.why, game.calls) == ("live", "older than 0 s", 2)


def test_after_a_deal_the_actor_re_reads_and_everyone_gets_the_new_holdings(opener):
    game = Game()
    taker = reader(opener, game)
    before = taker.me(clock(tick=TICK))
    game.payload["assets"] = game.payload["assets"] + [{"id": 99, "kind": "card", "ref": "LAV-02", "rarity": "common"}]
    game.payload["cash"] = 391
    after = taker.after_deal(clock(tick=TICK), "deal in thread 12")
    assert after.source == "live" and after.why == "after deal in thread 12" and after.digest != before.digest
    seen = reader(opener, game, "maker").me(clock(tick=TICK))
    assert seen.source == "db" and seen.me["cash"] == 391 and seen.digest == after.digest and game.calls == 2


def test_two_agents_starting_a_tick_together_make_one_me_call(opener):
    game = Game(delay=0.3)  # the leader's /me takes 300 ms: the other one waits for it
    answers = {}

    def run(name):
        answers[name] = reader(opener, game, name).me(clock(tick=TICK))

    threads = [threading.Thread(target=run, args=(name,)) for name in ("taker", "maker")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert game.calls == 1
    assert sorted(a.source for a in answers.values()) == ["db", "live"]
    assert len({a.digest for a in answers.values()}) == 1


def test_a_row_found_after_a_lock_wait_is_not_served_once_the_tick_is_ending(opener):
    game = Game(delay=1.2)  # the leader's /me holds the team lock for 1.2 s
    answers = {}

    def lead():
        answers["taker"] = reader(opener, game, "taker").me(clock(tick=TICK, next_tick_in=30.0))

    first = threading.Thread(target=lead)
    first.start()
    time.sleep(0.15)
    late = reader(opener, game, "maker").me(clock(tick=TICK, next_tick_in=1.6))  # 1.6 s left: fine at entry
    first.join(timeout=10)
    assert (late.source, late.why) == ("live", "the tick is about to end") and game.calls == 2


def test_a_pre_release_snapshot_table_is_replaced(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    conn.execute("create table me_snapshots (team text, tick int, epoch bigint, primary key (team, tick))")
    conn.commit()
    db.init_schema(conn)
    columns = conn.execute(
        "select column_name from information_schema.columns where table_schema = current_schema() "
        "and table_name = 'me_snapshots' and column_name = 'world'"
    ).fetchall()
    conn.close()
    assert columns == [("world",)]


def test_a_slow_game_read_is_awaited_not_asked_twice(opener, monkeypatch):
    monkeypatch.setattr(hd, "READ_DEADLINE_S", 0.3)
    game = Game(delay=0.8)  # /me is slower than the database deadline
    started = time.monotonic()
    read = reader(opener, game).me(clock(tick=TICK))
    took = time.monotonic() - started
    assert (read.source, game.calls) == ("live", 1) and 0.7 < took < 1.5


def test_a_database_that_hangs_after_the_game_answered_costs_the_caller_nothing(opener, monkeypatch):
    real_save = hd.save  # production deadlines: the caller is woken by the answer, not by the deadline

    def hung_save(*args, **kwargs):  # the link hangs during the store, after /me answered
        time.sleep(2.0)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(hd, "save", hung_save)
    game = Game()
    started = time.monotonic()
    read = reader(opener, game).me(clock(tick=TICK))
    took = time.monotonic() - started
    assert (read.source, game.calls) == ("live", 1) and took < 1.0  # handed over before the store
    time.sleep(2.2)  # let the worker finish the store before the schema is dropped


def test_a_store_that_lands_late_makes_the_row_look_old_never_new(opener, monkeypatch):
    real_save = hd.save

    def stalled_save(*args, **kwargs):  # the link stalls after /me answered, then the store lands
        time.sleep(1.2)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(hd, "save", stalled_save)
    game = Game()
    reader(opener, game).me(clock(tick=TICK))
    time.sleep(1.4)  # the store has landed
    late = reader(opener, game, "maker", holdings_max_age_s=1.0).me(clock(tick=TICK))
    assert (late.source, late.why) == ("live", "older than 1 s")


def test_an_error_after_the_game_answered_reaches_the_caller_once(opener, monkeypatch):
    def broken(self, raw, clock, epoch, why):
        raise RuntimeError("bad payload")

    monkeypatch.setattr(Holdings, "_as_read", broken)
    game = Game()
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        reader(opener, game).me(clock(tick=TICK))
    assert time.monotonic() - started < 2.0 and game.calls == 1


def test_a_job_whose_caller_gave_up_never_asks_the_game(opener, monkeypatch):
    monkeypatch.setattr(hd, "READ_DEADLINE_S", 0.3)
    real_epoch = hd.epoch_and_time

    def slow_epoch(*args, **kwargs):  # Postgres is slow before the game is asked
        time.sleep(1.0)
        return real_epoch(*args, **kwargs)

    monkeypatch.setattr(hd, "epoch_and_time", slow_epoch)
    game = Game()
    read = reader(opener, game).me(clock(tick=TICK))
    time.sleep(1.2)  # the job wakes up after its caller left
    assert (read.source, read.why, game.calls) == ("live", "postgres too slow", 1)


def test_two_writers_in_one_tick_never_move_the_row_backwards(opener):
    from bazaar_agent.holdings import parse_me, save

    shared = SharedDb(opener)
    newer = deepcopy({**ME, "tick": TICK, "cash": 380})
    with shared.session() as conn:
        assert save(conn, REAL, parse_me(newer), newer, TICK, 5, "taker", datetime.now(UTC))
        old = {**ME, "tick": TICK}
        assert not save(
            conn, REAL, parse_me(old), old, TICK, 4, "maker", datetime.now(UTC)
        )  # read under an older epoch
        row = conn.execute("select epoch, cash, read_by from me_snapshots where team = 't01' and tick = %s", (TICK,))
        assert row.fetchone() == (5, 380, "taker")
        evals = conn.execute("select cash from snapshots where tick = %s", (TICK,)).fetchone()
        assert evals == (380,)  # the evals row follows the winning snapshot, never the older one


def test_the_catalog_is_stored_and_never_rolls_back(opener):
    shared = SharedDb(opener)
    with shared.session() as conn:
        written = catalog_db.save_catalog(conn, CATALOG, 120)
        later = deepcopy(CATALOG)
        later["sets"][0]["cards"][0]["minted"] = 1
        catalog_db.save_catalog(conn, later, 110)  # an older tick: ignored
        rows = catalog_db.read_cards(conn)
        lav = catalog_db.read_cards(conn, set_code="LAV")
    assert written == len(rows) > 0 and all(r["updated_tick"] == 120 for r in rows)
    assert lav and all(r["set"] == "LAV" for r in lav)
    first = CATALOG["sets"][0]["cards"][0]
    assert next(r for r in rows if r["ref"] == first["id"])["minted"] == first.get("minted")


def test_the_mcp_tools_answer_from_the_stored_snapshot_with_its_tick_and_age(opener, tmp_path):
    from bazaar_agent.runtime import backend as be
    from tests.runtime_fakes import Public, Team, settings

    team = Team(me={**ME, "tick": TICK})
    shared = SharedDb(opener)
    stored_by = Holdings(team.me, shared, reader="taker", rules=Guardrails(), team="t01")
    stored_by.me(clock(tick=TICK))  # the taker read /me this tick
    team.reads.clear()
    b = be.Backend(
        settings(tmp_path),
        Guardrails(),
        live=False,
        team=team,
        public=Public(now=clock(tick=TICK)),
        holdings=Holdings(team.me, shared, reader="mcp", rules=Guardrails(), team="t01"),
    )
    status = be.status(b)
    assert status["holdings"]["source"] == "db" and status["holdings"]["tick"] == TICK
    assert status["holdings"]["read_by"] == "taker" and status["holdings"]["age_s"] < 5
    held = be.holdings(b)
    assert held["duplicates"] == {"LAT-03": [3, 4]} and held["packs"] == [{"asset": 6, "pack": "sobre_barrio"}]
    assert held["holdings"]["source"] == "db" and "me" not in team.reads  # no /api/me call at all
    with shared.session() as conn:
        catalog_db.save_catalog(conn, CATALOG, TICK)
    cards = be.cards(b, set_code="LAV")
    assert cards["source"] == "db" and cards["rows"] and all(r["set"] == "LAV" for r in cards["rows"])


def test_the_process_registry_never_shares_a_test_connection():
    assert hd.process_db()._connect is None  # tests/conftest.py: unit tests never reach a real database
