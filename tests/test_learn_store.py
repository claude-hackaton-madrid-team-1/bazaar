"""Learnings storage and recall (N12): memory always, Postgres when it answers, never raising into a tick."""

import contextlib

import psycopg
import pytest

from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.reader import FeedReader, GameHour, from_refusal
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import Clock
from tests.test_db import database_url, schema  # noqa: F401  (fixtures for the Postgres test)
from tests.test_learn_reader import FEED, HOUR, US

CLOCK = Clock(tick=173, t_hours=4.1, tick_seconds=60.0)


def learned():
    return FeedReader(US).read(FEED, HOUR)


# ---------------------------------------------------------------- memory only


def test_recall_filters_by_subject_kind_tick_and_team():
    store = LearningStore()
    assert store.record(learned()) == len({lr.key() for lr in learned()})
    assert [lr.subject for lr in store.recall("chato", {"cooloff"}, 180, team=US)] == ["chato"]
    assert store.recall("chato", {"cooloff"}, 193, team=US) == []  # expired AT until_tick
    assert {lr.team for lr in store.recall(kinds={"cooloff"}, tick=180, team=US)} == {US}  # t05's is not ours
    fees = store.recall(kinds={"fee_change"}, subject_kind="venue")
    assert [lr.tick for lr in fees] == sorted((lr.tick for lr in fees), reverse=True) and len(fees) >= 3
    assert len(store.recall(limit=2)) == 2 and store.where == "memory only"


def test_the_same_fact_twice_is_one_learning():
    store = LearningStore()
    store.record(learned())
    store.record(FeedReader(US).read(FEED, HOUR))  # another process (or a restart) reads the same feed
    assert len(store.memory) == len({lr.key() for lr in learned()})


def test_an_unreachable_database_logs_once_and_memory_still_answers():
    lines: list[str] = []

    def down() -> psycopg.Connection:
        raise psycopg.OperationalError("no route")

    store = LearningStore(down, lines.append)
    for tick in (1, 1, 2):
        store.begin_tick(tick)
        store.record(learned())
    assert lines == ["learnings: Postgres unavailable (OperationalError); memory only"]
    assert store.recall("chato", {"cooloff"}, 180, team=US)


class BrokenConn:
    """A connection whose every statement fails (the server went away mid-tick)."""

    closed = False
    autocommit = False

    @contextlib.contextmanager
    def transaction(self):
        raise psycopg.OperationalError("server closed the connection")
        yield

    def close(self) -> None:
        self.closed = True


def test_a_failing_statement_falls_back_to_memory_for_this_tick():
    lines: list[str] = []
    store = LearningStore(lambda: BrokenConn(), lines.append)  # type: ignore[arg-type,return-value]
    store.begin_tick(5)
    store.record(learned())
    assert store.recall("chato", {"cooloff"}, 180, team=US)
    assert lines[0] == "learnings: upsert failed in Postgres (OperationalError); memory only this tick"


# ---------------------------------------------------------------- the live learner (what the taker calls)


def test_the_live_learner_reads_new_events_and_recalls_our_blockers():
    learner = LiveLearner(LearningStore())
    blocks = learner.blocks(FEED, US, Clock(tick=180, t_hours=4.2, tick_seconds=60.0))
    assert blocks.stops("chato") is not None and blocks.stops("abuela", "sobre_barrio") is not None
    assert learner.flush() > 0 and learner.pending == []


def test_the_live_learner_learns_a_refusal_and_a_closed_thread():
    lines: list[str] = []
    learner = LiveLearner(LearningStore(), lines.append)
    error = BazaarError("cooloff", "abuela is not dealing with you until tick 190", 403, {"until_tick": 190})
    assert learner.refused("abuela", error, US, CLOCK, "LAV-03").until_tick == 190
    thread = {"id": 9, "with": "chato", "status": "closed", "closed_reason": "persona_quota", "topic": {}}
    assert learner.thread_closed(thread, US, CLOCK).kind == "quota"
    assert lines == ["learned: abuela cooloff with us until T190", "learned: chato persona quota with us until T227"]
    assert learner.blocks([], US, CLOCK).stops("abuela") is not None


def test_the_live_learner_fails_open():
    class Exploding(LearningStore):
        def recall(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        def record(self, learnings):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    lines: list[str] = []
    learner = LiveLearner(Exploding(), lines.append)
    assert not learner.blocks(FEED, US, CLOCK) and not learner.blocks(FEED, US, CLOCK)
    assert learner.flush() == 0
    assert learner.refused("abuela", object(), US, CLOCK, None) is None  # not a BazaarError: nothing learned
    assert lines == [
        "learnings: recall failed (RuntimeError: boom); trading as before",
        "learnings: write failed (RuntimeError: boom); trading as before",
    ]


# ---------------------------------------------------------------- Postgres (throwaway schema)


@pytest.mark.integration
def test_learnings_round_trip_through_postgres(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from tests.test_db import open_in

    writer = LearningStore(lambda: open_in(database_url, schema), init_schema=db.init_schema)
    writer.record(learned())
    writer.record(learned())  # idempotent upsert on the dedupe key
    reader = LearningStore(lambda: open_in(database_url, schema))  # another process: nothing in memory
    assert reader.where == "postgres learnings + memory"
    cooloff = reader.recall("chato", {"cooloff"}, 180, team=US)
    assert [(lr.until_tick, lr.evidence, lr.team) for lr in cooloff] == [(193, (20006,), US)]
    assert reader.recall("chato", {"cooloff"}, 193, team=US) == []
    duel = reader.recall("duels", {"behaviour"})
    assert duel and duel[0].detail["aggregate"] == "duels"
    with open_in(database_url, schema) as conn:
        rows = conn.execute("select count(*), count(distinct dedupe_key), min(scope) from learnings").fetchone()
    assert rows is not None and rows[0] == rows[1] == len({lr.key() for lr in learned()})
    refusal = from_refusal("abuela", "persona_quota", "", {}, US, GameHour(173, 4.1), "LAV-03")
    writer.record([refusal])
    assert reader.recall("abuela", {"quota"}, 200, team=US)[0].key() == refusal.key()
    writer.close()
    reader.close()
