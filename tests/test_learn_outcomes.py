"""The outcome learner (N3): tick-driven passes on a worker, fail-open, and one full pass through Postgres."""

import threading

import pytest

from bazaar_agent.learn.outcomes import OutcomeLearner, PassResult
from bazaar_agent.learn.store import LearningStore
from tests.test_db import database_url, schema  # noqa: F401  (fixtures for the Postgres test)
from tests.test_intel import msg, opened, settle
from tests.test_learn_recall import FakeModels

US = "t01"


def learner(run, every=5):
    logged: list[str] = []
    lr = OutcomeLearner(lambda: None, LearningStore(), None, logged.append, every=every)  # type: ignore[arg-type,return-value]
    lr._run = run  # type: ignore[method-assign]
    return lr, logged


def test_a_pass_starts_every_n_ticks_and_never_twice_at_once():
    gate, started = threading.Event(), []

    def run(tick, us):
        started.append(tick)
        gate.wait(2)
        return PassResult(tick)

    lr, _ = learner(run)
    assert lr.maybe_run(10, US) is True
    assert lr.maybe_run(15, US) is False  # the first pass is still running
    gate.set()
    lr.wait(2)
    assert lr.maybe_run(12, US) is False  # not due yet (every 5 ticks)
    assert lr.maybe_run(15, US) is True
    lr.wait(2)
    assert started == [10, 15]
    assert lr.maybe_run(30, None) is False  # no team id yet: nothing to learn about
    lr.close()


def test_a_failing_pass_is_logged_once_and_the_agent_goes_on():
    logged: list[str] = []

    def connect():
        raise OSError("db down")

    lr = OutcomeLearner(connect, LearningStore(), None, logged.append, every=1)  # type: ignore[arg-type]
    for tick in (1, 2, 3):
        lr.maybe_run(tick, US)
        result = lr.wait(2)
        assert result is not None and result.error is not None and "OSError" in result.error
    assert len([line for line in logged if "pass failed" in line]) == 1
    lr.close()


def test_the_taker_starts_a_pass_after_its_tick(tmp_path):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import FakePublic, FakeTeam, clock, parts

    calls: list[tuple[int, str]] = []

    class Spy:
        def maybe_run(self, tick, us):
            calls.append((tick, us))
            return True

    t = Taker(
        FakeTeam(),
        FakePublic(),
        live=False,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        outcome_learner=Spy(),  # type: ignore[arg-type]
        **parts(tmp_path),
    )
    t.on_tick(clock())
    assert calls and calls[0][1] == US


# ---------------------------------------------------------------- Postgres (throwaway schema)


def feed():
    """Our uncommon thread with Abuela (deal at 22) and Chato's uncommon walk, plus another team's fill."""
    return [
        opened(1, 115, US, {"buy": {"card": "LAV-06"}}, tick=64),
        msg(2, 115, US, US, give_cash=15, tick=64),
        msg(3, 115, US, "abuela", want_cash=29, tick=64),
        msg(4, 115, US, US, give_cash=19, tick=65),
        msg(5, 115, US, "abuela", want_cash=24, tick=65),
        msg(6, 115, US, US, give_cash=22, tick=66),
        settle(7, 1, "abuela", US, "LAV-06", 22, tick=67, kind="card"),
        opened(8, 300, "t05", {"buy": {"card": "SAL-06"}}, tick=70),
        msg(9, 300, "t05", "t05", give_cash=17, tick=70),
        msg(10, 300, "t05", "abuela", want_cash=29, tick=70),
        msg(11, 300, "t05", "t05", give_cash=18, tick=71),
        settle(12, 2, "abuela", "t05", "SAL-06", 18, tick=72, kind="card"),
    ]


@pytest.mark.integration
def test_one_pass_writes_lessons_behaviours_and_embeddings(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from bazaar_agent.learn.outcomes import learn_once
    from tests.test_db import open_in

    def connect():
        return open_in(database_url, schema)

    with connect() as conn:
        db.init_schema(conn)
        db.load_events(conn, feed())
        vectors = db.pgvector_version(conn) is not None
    store = LearningStore(connect, init_schema=db.init_schema)
    first = learn_once(connect, store, FakeModels(), US, 80)
    assert first.error is None and first.lessons == 1 and first.behaviours == 5
    assert ("abuela", "card:uncommon") in first.curves
    (lesson,) = [lr for lr in first.learned if lr.kind == "lesson"]
    assert lesson.detail["fill"] == 22 and lesson.detail["market_floor"] == 18
    again = learn_once(connect, store, FakeModels(), US, 85)
    assert again.error is None and again.embedded == 0  # same claims: nothing embedded again
    with connect() as conn:
        rows = conn.execute(
            "select kind, source, count(*) from learnings group by kind, source order by kind"
        ).fetchall()
        moves = conn.execute("select count(*), count(distinct dedupe_key) from trader_behaviors").fetchone()
        embedded = conn.execute("select count(*) from learnings where embedded_hash = md5(claim)").fetchone()
    assert rows == [("behaviour", "outcome", 1), ("lesson", "outcome", 1)]
    assert moves == (5, 5)  # the second pass added no duplicate move
    assert embedded is not None and embedded[0] == (2 if vectors else 0)
    store.close()


@pytest.mark.integration
def test_the_cli_runs_a_pass_and_answers_a_query(database_url, schema, monkeypatch, capsys):  # noqa: F811
    import json

    from bazaar_agent import db
    from bazaar_agent.learn import lessons_cli
    from tests.test_db import open_in

    def connect():
        return open_in(database_url, schema)

    with connect() as conn:
        db.init_schema(conn)
        db.load_events(conn, feed())

    class Models(FakeModels):
        status = "ready (fake)"

        def load(self):
            return True

    monkeypatch.setattr(lessons_cli, "LocalModels", lambda **kw: Models())
    common = dict(subject=None, limit=3, min_score=0.0, init_schema=db.init_schema)
    lessons_cli.show(
        connect, US, 90, lessons=True, query="abuela uncommon LAV-06 ask 29", save=False, as_json=True, **common
    )
    out = json.loads(capsys.readouterr().out)
    assert out["pass"]["lessons"] == 1 and out["pass"]["where"] == "memory only"
    assert out["query"]["status"] == "ok" and "LAV-06" in out["query"]["hits"][0]["text"]
    lessons_cli.show(connect, US, 90, lessons=True, query=None, save=True, as_json=False, **common)
    assert "1 lessons" in capsys.readouterr().out
    lessons_cli.show(connect, US, 90, lessons=False, query="abuela LAV-06", save=False, as_json=False, **common)
    printed = capsys.readouterr().out
    assert "recall ok" in printed and "LAV-06" in printed  # the shared table now holds the saved lessons
    with pytest.raises(SystemExit):
        lessons_cli.show(None, US, 90, lessons=True, query=None, save=False, as_json=False, **common)


@pytest.mark.integration
def test_a_dry_run_pass_writes_nothing_to_postgres(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from bazaar_agent.learn.outcomes import learn_once
    from tests.test_db import open_in

    def connect():
        return open_in(database_url, schema)

    with connect() as conn:
        db.init_schema(conn)
        db.load_events(conn, feed())
    result = learn_once(connect, LearningStore(), None, US, 80, save_moves=False)
    assert result.lessons == 1 and result.behaviours == 5
    with connect() as conn:
        assert conn.execute(
            "select (select count(*) from learnings) + (select count(*) from trader_behaviors)"
        ).fetchone() == (0,)


def test_the_clock_going_back_restarts_the_schedule():
    lr, _ = learner(lambda tick, us: PassResult(tick))
    assert lr.maybe_run(100, US) is True
    lr.wait(2)
    assert lr.maybe_run(3, US) is True  # a simulator reset: tick 3 < 100
    lr.wait(2)
    lr.close()


@pytest.mark.integration
def test_later_passes_read_only_new_events_and_insert_only_new_moves(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from bazaar_agent.learn.outcomes import PassState, learn_once
    from tests.test_db import open_in

    def connect():
        return open_in(database_url, schema)

    events = feed()
    with connect() as conn:
        db.init_schema(conn)
        db.load_events(conn, events[:7])
    state, store = PassState(), LearningStore(connect)
    first = learn_once(connect, store, None, US, 70, state=state)
    assert state.last_id == 7 and len(state.events) == 7 and first.lessons == 1
    assert len(state.recorded) == len(first.learned)
    with connect() as conn:
        db.load_events(conn, events[7:])
    second = learn_once(connect, store, None, US, 75, state=state)
    assert state.last_id == 12 and len(state.events) == 12 and second.behaviours == 5
    assert len(state.moves) == 5
    with connect() as conn:
        assert conn.execute("select count(*) from trader_behaviors").fetchone() == (5,)
    store.close()
