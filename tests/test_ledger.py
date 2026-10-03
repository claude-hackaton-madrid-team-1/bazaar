"""The shared guardrail ledger: one accept per slot per tick across processes (file lock, Postgres advisory
lock + unique slot), refunds, reconnecting after a drop, which ledger a live or dry process may use, and the
decision log (Postgres or JSONL)."""

import threading

import psycopg
import pytest

from bazaar_agent.decisions import Decision, DecisionLog
from bazaar_agent.guardrails import Ledger, refund_row
from bazaar_agent.ledger_pg import FallbackLedger, LedgerNotShared, LedgerUnavailable, PgLedger, open_ledger
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)


def race(reserve, n=8):
    """n callers try to take the tick's only accept at the same moment; how many got it."""
    start, results = threading.Barrier(n), []

    def one(i):
        start.wait()
        results.append(reserve(i))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_the_file_ledger_gives_a_ticks_accept_to_exactly_one_process(tmp_path):
    ledgers = [Ledger(tmp_path / "ledger.jsonl") for _ in range(8)]  # one per "process"
    results = race(lambda i: ledgers[i].reserve_accept(100, 1.5, 10, f"LAV-0{i}", 1))
    assert results.count(True) == 1 and ledgers[0].accepts_in_tick(100) == 1
    assert ledgers[0].reserve_accept(101, 1.6, 0, "duel:7", 1)  # a new tick, a new slot
    assert ledgers[0].accept_items(101) == ["duel:7"]


def test_a_refund_is_a_negative_spend(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 10, 1.0, 65, "LAV-09")
    ledger.record("spend", 12, 1.1, -65, "LAV-09")
    ledger.record("listing", 12, 1.1, 65, "LAV-09")
    assert ledger.spent_since(0) == 0 and ledger.count_in_tick("listing", 12) == 1


RAILWAY = "postgresql://postgres:Never-Printed-pw@shuttle.proxy.rlwy.net:41234/railway"
GAME = "https://bazaar.causaprima.ai"
SIM = "http://localhost:8800"


def refused():
    raise psycopg.OperationalError("connection refused")


def test_a_refund_across_a_pace_change_is_never_dated_after_its_spend(tmp_path):
    # Friday: a 40 P bid posted at tick 230 (60 s ticks). Ten more 60 s ticks, then the pace drops to 30 s;
    # cancelled at tick 250. Back-dating 20 ticks at the CURRENT 30 s would date the refund 5 min after the
    # spend, and for those 5 min the hour's spend would read -40 (the cap 40 looser).
    t_spend = 230 / 60
    t_cancel = t_spend + (10 * 60 + 10 * 30) / 3600
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 230, t_spend, 40, "LAV-09")
    row = refund_row(40, "LAV-09", 230, 250, t_cancel, max_tick_seconds=60.0)
    assert row[1] == 230 and row[2] <= t_spend and row[3] == -40
    ledger.record(*row)
    # Every 30 s tick for the next two game hours: the hour's spend never reads below zero.
    for n in range(0, 240):
        assert ledger.spent_since(t_cancel + n * 30 / 3600 - 1.0) >= 0


def test_a_refund_with_an_unknown_created_tick_never_loosens_the_cap(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 90, 1.5, 40, "LAV-09")
    ledger.record(*refund_row(40, "LAV-09", None, 100, 1.6, max_tick_seconds=60.0))
    assert ledger.spent_since(1.6 - 1.0) == 40  # no refund inside the window: over-counts, never under-counts
    assert refund_row(40, "LAV-09", 105, 100, 1.6, 60.0)[1:3] == (100, pytest.approx(1.6 - 1 / 60))  # future: now


def test_a_dry_run_falls_back_to_the_file_when_postgres_is_down(tmp_path):
    lines = []
    ledger = open_ledger(
        tmp_path, source="taker", database_url=RAILWAY, game_url=GAME, connect=refused, log=lines.append
    )
    assert isinstance(ledger, FallbackLedger) and ledger.where == "file ledger.jsonl (this machine only)"
    assert lines == [
        "ledger: Postgres on shuttle.proxy.rlwy.net:41234 unavailable (OperationalError); "
        "using file ledger.jsonl (this machine only) until it answers"
    ]
    ledger.record("spend", 10, 1.0, 17, "sobre_barrio")
    assert ledger.spent_since(0) == 17 and (tmp_path / "ledger.jsonl").is_file()


def test_live_refuses_a_ledger_that_is_not_shared(tmp_path):
    from bazaar_agent.config import DEFAULT_DATABASE_URL

    local = [
        DEFAULT_DATABASE_URL,
        "postgresql://me:pw@127.0.0.1:5432/bazaar",
        "postgresql://me:pw@host.docker.internal:5433/bazaar",
        "postgresql://me:pw@db.localhost/bazaar",
        "postgresql://me:pw@192.168.1.20:5432/bazaar",
        "postgresql://me:pw@[::1]:5432/bazaar",
        "not a url",
    ]
    for url in local:
        with pytest.raises(LedgerNotShared, match="live trading needs the team's shared ledger") as refusal:
            open_ledger(tmp_path, source="duels", live=True, database_url=url, game_url=GAME, connect=refused)
        assert "pw" not in str(refusal.value)
    # dry, the same URLs are fine: a local count is allowed when nothing is sent
    assert open_ledger(tmp_path, source="duels", database_url=DEFAULT_DATABASE_URL, game_url=GAME, connect=refused)


def test_the_official_game_is_recognised_however_its_url_is_written(tmp_path):
    from bazaar_agent.config import DEFAULT_DATABASE_URL
    from bazaar_agent.ledger_pg import official_game

    for url in (GAME, "https://BAZAAR.causaprima.ai./", "https://bazaar.causaprima.ai:443/api"):
        assert official_game(url), url
        with pytest.raises(LedgerNotShared):
            open_ledger(tmp_path, source="duels", live=True, database_url=DEFAULT_DATABASE_URL, game_url=url)
    assert not official_game(SIM) and not official_game("https://bazaar.causaprima.ai.evil.example")


def test_live_on_a_down_shared_ledger_fails_closed_and_never_counts_on_the_file(tmp_path):
    lines = []
    ledger = open_ledger(
        tmp_path, source="taker", live=True, database_url=RAILWAY, game_url=GAME, connect=refused, log=lines.append
    )
    assert isinstance(ledger, PgLedger) and ledger.where.startswith("postgres ledger table on shuttle.proxy")
    assert "Never-Printed-pw" not in " ".join(lines) and "no game write until it answers (fail closed)" in lines[0]
    with pytest.raises(LedgerUnavailable):
        ledger.accepts_in_tick(100)
    with pytest.raises(LedgerUnavailable):
        ledger.reserve_accept(100, 1.5, 10, "LAV-02", 1)
    assert not (tmp_path / "ledger.jsonl").exists()


def test_live_against_a_simulator_may_keep_the_file(tmp_path):
    from bazaar_agent.config import DEFAULT_DATABASE_URL

    lines = []
    ledger = open_ledger(
        tmp_path, source="taker", live=True, database_url=DEFAULT_DATABASE_URL, game_url=SIM, connect=refused,
        log=lines.append,
    )  # fmt: skip
    assert isinstance(ledger, FallbackLedger) and "localhost is a simulator" in lines[-1]
    assert ledger.reserve_accept(100, 1.5, 10, "LAV-02", 1) and not ledger.reserve_accept(100, 1.5, 9, "x", 1)


def test_a_down_ledger_is_retried_at_most_once_per_window():
    attempts, clock = [], [0.0]

    def down():
        attempts.append(clock[0])
        raise psycopg.OperationalError("down")

    ledger = PgLedger(down, "taker", retry_every_s=15.0, now=lambda: clock[0])
    for step in range(10):
        clock[0] = 2.0 * step
        with pytest.raises(LedgerUnavailable):
            ledger.spent_since(0)
    assert attempts == [0.0, 16.0]


def decision(**extra):
    base = {
        "agent": "taker",
        "tick": 100,
        "kind": "accept_ask",
        "inputs": {"ref": "LAV-02", "total": 12},
        "reason": "worth 20.8",
        "guardrail": "allowed",
        "chosen": True,
        "status": "approved",
        "dry_run": True,
    }
    return Decision(**{**base, **extra})


def test_the_decision_log_writes_jsonl_without_postgres_and_scrubs(tmp_path):
    import json

    log = DecisionLog(tmp_path)
    did = log.decide(decision(reason="team key tk-abcd-efgh-1234 leaked?"))
    log.executed(did, 100, "accept", {"offer": 1}, {"ok": True})
    log.settle(did, "done")
    rows = [json.loads(x) for x in (tmp_path / "agents" / "decisions.jsonl").read_text().splitlines()]
    assert did < 0 and rows[0]["id"] == did and "tk-abcd-efgh-1234" not in json.dumps(rows)
    assert rows[1] == {"id": did, "status": "done", "update": True}
    (execution,) = [json.loads(x) for x in (tmp_path / "agents" / "executions.jsonl").read_text().splitlines()]
    assert execution["sdk_method"] == "accept" and execution["decision_id"] == did


def test_a_down_postgres_is_retried_at_most_once_per_tick(tmp_path):
    attempts = []

    def down():
        attempts.append(1)
        raise psycopg.OperationalError("down")

    log = DecisionLog(tmp_path, down)
    log.begin_tick(100)
    for _ in range(5):
        log.decide(decision())
    log.begin_tick(101)
    log.decide(decision())
    assert len(attempts) == 2


# ---------------------------------------------------------------- Postgres (integration)

pg = pytest.mark.integration


@pytest.fixture
def pg_ledgers(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    first = open_in(database_url, schema)
    db.init_schema(first)
    second = open_in(database_url, schema)
    ledgers = [PgLedger(lambda: first, "taker"), PgLedger(lambda: second, "duels")]
    yield ledgers
    for ledger in ledgers:
        ledger.close()


@pg
def test_postgres_gives_a_ticks_accept_to_exactly_one_writer(pg_ledgers):
    taker, duels = pg_ledgers
    results = race(lambda i: (taker, duels)[i % 2].reserve_accept(200, 3.3, 10, f"x{i}", 1), n=2)
    assert sorted(results) == [False, True] and taker.accepts_in_tick(200) == 1
    assert duels.reserve_accept(201, 3.4, 0, "duel:9", 2) and taker.reserve_accept(201, 3.4, 12, "LAV-02", 2)
    assert taker.accept_items(201) == ["duel:9", "LAV-02"] and not taker.reserve_accept(201, 3.4, 9, "x", 2)


@pg
def test_the_unique_slot_refuses_a_second_accept_even_without_the_lock(pg_ledgers):
    taker, _ = pg_ledgers
    assert taker.reserve_accept(300, 4.0, 10, "LAV-02", 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        taker._pg.get().execute(
            "insert into ledger (kind, tick, t_hours, price, item, slot) values ('accept', 300, 4.0, 1, 'x', 1)"
        )


@pg
def test_postgres_spend_packs_listings_and_refunds(pg_ledgers):
    taker, duels = pg_ledgers
    taker.record("spend", 10, 1.0, 17, "sobre_barrio")
    duels.record("spend", 11, 1.2, 65, "LAV-09")
    duels.record("spend", 12, 1.3, -65, "LAV-09")
    taker.record("listing", 12, 1.3, 65, "LAV-09")
    assert taker.spent_since(0.5) == 17 and duels.packs_since(0.5) == {"sobre_barrio": 1}
    assert taker.count_in_tick("listing", 12) == 1


@pg
def test_a_dropped_connection_fails_closed_once_then_reconnects(database_url, schema):  # noqa: F811
    """A Postgres restart or a lost proxy kills the connection: that call sends nothing, the next one works."""
    from bazaar_agent import db

    admin = open_in(database_url, schema)
    db.init_schema(admin)
    opened = []

    def connect():
        opened.append(open_in(database_url, schema))
        return opened[-1]

    ledger = PgLedger(connect, "duels")
    assert ledger.reserve_accept(400, 5.0, 0, "duel:1", 1)
    admin.execute("select pg_terminate_backend(%s)", (opened[0].info.backend_pid,))
    admin.commit()
    with pytest.raises(LedgerUnavailable):
        ledger.accepts_in_tick(400)
    assert ledger.accepts_in_tick(400) == 1 and len(opened) == 2  # reopened, the row is still there
    assert ledger.reserve_accept(401, 5.1, 12, "LAV-02", 1) and ledger.accept_items(401) == ["LAV-02"]
    ledger.close()
    admin.close()


@pg
def test_a_dry_run_moves_back_to_postgres_when_it_answers(database_url, schema, tmp_path):  # noqa: F811
    from bazaar_agent import db

    admin = open_in(database_url, schema)
    db.init_schema(admin)
    up, clock = [False], [0.0]

    def connect():
        if not up[0]:
            raise psycopg.OperationalError("down")
        return open_in(database_url, schema)

    pg_ledger = PgLedger(connect, "taker", retry_every_s=15.0, now=lambda: clock[0])
    ledger = FallbackLedger(pg_ledger, Ledger(tmp_path / "ledger.jsonl"))
    ledger.record("spend", 10, 1.0, 17, "sobre_barrio")  # Postgres down: this machine's file
    up[0], clock[0] = True, 20.0
    ledger.record("spend", 11, 1.1, 9, "LAV-02")  # back: the shared table
    assert ledger.where.startswith("postgres") and ledger.spent_since(0) == 9
    assert admin.execute("select item from ledger").fetchall() == [("LAV-02",)]
    pg_ledger.close()
    admin.close()


@pg
def test_decisions_and_executions_rows_land_in_postgres(database_url, schema, tmp_path):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    log = DecisionLog(tmp_path, lambda: open_in(database_url, schema))
    did = log.decide(decision(dry_run=False, move={"accept": 1, "price": 12}, jev={"verdict": "yes", "value": 0.9}))
    log.executed(did, 100, "accept", {"offer": 1}, {"ok": True})
    log.settle(did, "done")
    row = conn.execute(
        "select agent, kind, status, dry_run, chosen->>'price', jev->>'verdict', candidates->>'ref', "
        "policy_checks->>'allowed' from decisions where id = %s",
        (did,),
    ).fetchone()
    assert row == ("taker", "accept_ask", "done", False, "12", "yes", "LAV-02", "true")
    ex = conn.execute("select sdk_method, request->>'offer', error_code from executions where decision_id = %s", (did,))
    assert ex.fetchone() == ("accept", "1", None)
    conn.close()
    log.close()
