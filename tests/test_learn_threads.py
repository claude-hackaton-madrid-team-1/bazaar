"""Our dealer threads kept in Postgres from the answers the taker already reads (N12 part 3)."""

import json
from pathlib import Path

import psycopg
import pytest

from bazaar_agent.learn.threads import BUFFER_MAX, Seen, ThreadStore, message_rows, thread_row
from tests.test_db import database_url, open_in, schema  # noqa: F401  (fixtures)

THREAD = json.loads((Path(__file__).parent / "fixtures" / "learn" / "thread_cooloff.json").read_text())
US = "t01"


def test_a_closed_thread_keeps_its_reason_expiry_and_every_message():
    row = thread_row(Seen(THREAD, US, 6, {}))
    assert row is not None
    assert row[:3] == (41, "abuela", "persona") and json.loads(row[3]) == {"buy": {"card": "LAV-03"}}
    assert row[5:] == ("cooloff", 2, 6, "cooloff", 25, 6)  # status, opened, closed at, reason, until, updated
    rows = message_rows(Seen(THREAD, US, 6, {301: "anchor-low"}))
    assert [(m[0], m[2], m[5], m[7], m[8], m[9]) for m in rows] == [
        (301, "t01", 2, None, True, "anchor-low"),
        (302, "abuela", 12, False, False, None),
        (303, "abuela", 11, True, False, None),
    ]
    assert "\x00" not in rows[2][4]  # dealer words stored as clean text


def test_team_threads_and_junk_are_not_kept():
    assert thread_row(Seen({**THREAD, "kind": "team"}, US, 6, {})) is None
    store = ThreadStore(None)
    store.saw({"id": "x"}, US, 6)
    store.saw(THREAD, "", 6)
    store.saw(None, US, 6)  # type: ignore[arg-type]
    assert store.buffer == {}


def test_without_postgres_the_answers_wait_bounded():
    store = ThreadStore(None)
    for i in range(BUFFER_MAX + 50):
        store.saw({**THREAD, "id": i}, US, i)
    assert store.flush(500) == 0 and len(store.buffer) == BUFFER_MAX and min(store.buffer) == 50


def test_a_down_database_is_retried_every_five_ticks():
    tries: list[int] = []

    def down() -> psycopg.Connection:
        tries.append(1)
        raise psycopg.OperationalError("no route")

    lines: list[str] = []
    store = ThreadStore(down, lines.append)
    for tick in range(1, 12):
        store.saw(THREAD, US, tick)
        store.flush(tick)
    assert len(tries) == 3 and lines == [
        "threads: connect failed (OperationalError); trading goes on, the answers wait"
    ]


@pytest.mark.integration
def test_threads_and_messages_round_trip_and_never_roll_back(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    with open_in(database_url, schema) as conn:
        db.init_schema(conn)
    store = ThreadStore(lambda: open_in(database_url, schema))
    open_view = {
        **THREAD,
        "status": "open",
        "closed_reason": None,
        "until_tick": None,
        "messages": THREAD["messages"][:2],
    }
    store.saw(open_view, US, 4)
    assert store.flush(4) == 1
    store.saw(THREAD, US, 6, {301: "anchor-low"})
    assert store.flush(6) == 1
    store.saw(open_view, US, 5)  # a lagging read (another process): never rolls the thread back
    store.flush(5)
    with open_in(database_url, schema) as conn:
        thread = conn.execute(
            "select status, closed_reason, until_tick, opened_tick, closed_tick, ours from threads"
        ).fetchall()
        messages = conn.execute("select id, final, ours, tactic, price from messages order by id").fetchall()
    assert thread == [("cooloff", "cooloff", 25, 2, 6, True)]
    assert messages == [(301, None, True, "anchor-low", 2), (302, False, False, None, 12), (303, True, False, None, 11)]
    store.close()


def test_the_taker_keeps_what_it_read_with_no_extra_request(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts

    def run(store):  # type: ignore[no-untyped-def]
        team = FakeTeam(threads=[{**THREAD, "id": 77, "with": "chato", "status": "open"}])  # a thread we hold
        t = Taker(
            team,
            FakePublic(),
            live=True,
            log=lambda line: None,
            now=lambda: 1000.0,
            sleep=lambda s: None,
            config=TakerConfig(max_dealer_threads=3),
            thread_store=store,
            **parts(tmp_path / str(id(store))),
        )
        t.on_tick(clock())
        team.thread_payloads[5000] = {**THREAD, "id": 5000}
        team.now = clock(tick=TICK + 1)
        t.on_tick(team.now)
        return team.reads

    class Spy(ThreadStore):
        flushed: list[list[int]] = []

        def flush(self, tick: int) -> int:
            self.flushed.append(sorted(self.buffer))
            self.buffer = {}
            return 0

    spy = Spy(None)
    assert run(spy) == run(None)  # the same requests with and without the store
    # each tick: the listing, and the dealer thread the desk read (it opens 5000 and reads it in the same tick)
    assert spy.flushed == [[77, 5000], [77, 5000]]


def test_a_bad_message_never_costs_its_thread_row():
    from bazaar_agent.learn.threads import _rows

    nan = {"message": 304, "tick": 5, "sender": "abuela", "offer": {"want": {"cash": 1}, "x": float("nan")}}
    bad = {**THREAD, "messages": [*THREAD["messages"], nan]}
    threads, messages = _rows([Seen(bad, US, 6, {})])
    assert [t[0] for t in threads] == [41] and [m[0] for m in messages] == [301, 302, 303]


def test_a_walked_thread_is_kept_as_walked(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts

    class Spy(ThreadStore):
        def flush(self, tick: int) -> int:
            return 0

    store = Spy(None)
    team = FakeTeam()
    t = Taker(team, FakePublic(), live=True, log=lambda line: None, now=lambda: 1000.0, sleep=lambda s: None,
              config=TakerConfig(max_dealer_threads=3), thread_store=store, **parts(tmp_path))  # fmt: skip
    t.on_tick(clock())  # opens thread 5000
    for tick in range(TICK + 1, TICK + 20):  # past dealer_max_ticks_per_thread: the desk walks
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    assert ("close_thread", 5000) in team.sent
    kept = store.buffer[5000].thread  # the close answer's status (the fake says "closed"), never left "open"
    assert kept["status"] == "closed" and kept["closed_reason"] == "walked"


@pytest.mark.integration
def test_a_write_cancelled_by_a_lock_keeps_the_answers_and_writes_later(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    with open_in(database_url, schema) as conn:
        db.init_schema(conn)
    lines: list[str] = []
    store = ThreadStore(lambda: open_in(database_url, schema), lines.append)
    store.saw(THREAD, US, 6)
    with open_in(database_url, schema) as locker:
        locker.execute("lock table threads in access exclusive mode")  # held until this block ends
        assert store.flush(6) == 0 and 41 in store.buffer
    assert store.flush(7) == 0  # the cancelled statement dropped the connection: retried 5 ticks later
    assert store.flush(11) == 1 and store.buffer == {}
    assert lines[0].startswith("threads: write failed (") and lines[-1] == "threads: Postgres writes are back"
    store.close()


@pytest.mark.integration
def test_an_ended_thread_never_goes_back_to_open_and_a_bad_thread_costs_only_itself(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    with open_in(database_url, schema) as conn:
        db.init_schema(conn)
    lines: list[str] = []
    store = ThreadStore(lambda: open_in(database_url, schema), lines.append)
    store.open()
    store.saw(THREAD, US, 20)
    store.flush(20)
    store.saw({**THREAD, "status": "open", "closed_reason": None}, US, 20)  # an older read flushed in the same tick
    store.saw({**THREAD, "id": 42, "with": "abuela\x00"}, US, 20)
    store.flush(20)
    with open_in(database_url, schema) as conn:
        rows = conn.execute("select id, status, closed_reason, counterpart from threads order by id").fetchall()
    assert rows == [(41, "cooloff", "cooloff", "abuela"), (42, "cooloff", "cooloff", "abuela")]  # 41 stays ended
    store.close()


@pytest.mark.integration
def test_a_thread_the_server_refuses_costs_only_itself(database_url, schema, monkeypatch):  # noqa: F811
    from bazaar_agent import db
    from bazaar_agent.learn import threads as threads_module

    with open_in(database_url, schema) as conn:
        db.init_schema(conn)
    real = threads_module.thread_row

    def bad_topic(s):  # type: ignore[no-untyped-def]
        row = real(s)
        return (*row[:3], "{not json", *row[4:]) if row is not None and row[0] == 42 else row

    monkeypatch.setattr(threads_module, "thread_row", bad_topic)
    lines: list[str] = []
    store = ThreadStore(lambda: open_in(database_url, schema), lines.append)
    store.saw(THREAD, US, 6)
    store.saw({**THREAD, "id": 42}, US, 6)
    assert store.flush(6) == 1 and store.buffer == {}  # 41 written thread by thread, 42 logged and dropped
    with open_in(database_url, schema) as conn:
        assert [r[0] for r in conn.execute("select id from threads").fetchall()] == [41]
    assert any(line.startswith("threads: thread 42 failed (") for line in lines)
    store.close()
