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
