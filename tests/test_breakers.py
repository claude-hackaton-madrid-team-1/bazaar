"""Circuit breakers: the scope of each write, the check() refusal, the fail-open board, and the Postgres table."""

import threading

import psycopg
import pytest

from bazaar_agent import breakers
from bazaar_agent import guardrails as gr
from bazaar_agent.agents.seller import Swap
from tests.test_db import conn, database_url, schema  # noqa: F401 — pytest fixtures (throwaway schema)

RULES = gr.Guardrails(cash_floor=0)


def ctx(tripped=frozenset(), tick=10):
    return gr.Context(cash=500, held={}, tick=tick, t_hours=1.0, breakers=frozenset(tripped))


@pytest.mark.parametrize(
    ("action", "scope"),
    [
        (gr.Action("duel_accept", "7", price=60, limit=50, role="seller"), "duel_accept"),
        (gr.Action("accept_buy", "LAV-03", "common", 5, counterparty="t02"), "board_accept"),
        (gr.Action("accept_sell", "LAV-03", "common", 50, your_value=1.0), "board_accept"),
        (gr.Action("buy", "LAV-03", "common", 5), "dealer_buy"),
        (gr.Action("bid", "LAV-03", "common", 5), "dealer_buy"),
        (gr.Action("bid", "LAV-03", "common", 5, counterparty=gr.ANY_TEAM), "maker_post"),
        (gr.Action("sell", "LAV-03", "common", 50, your_value=1.0, counterparty=gr.ANY_TEAM), "maker_post"),
        (gr.Action("dealer_sell", "LAV-03", "common", 50, your_value=1.0), "dealer_sell"),
        (gr.Action("cancel", "12"), None),
        (gr.Action("close_thread", "12"), None),
        (gr.Action("duel_offer", "7", price=60, limit=50, role="seller"), None),
    ],
)
def test_each_write_answers_to_one_breaker_and_stepping_back_to_none(action, scope):
    assert gr.breaker_scope(action) == scope


def test_a_swap_answers_to_the_team_swap_breaker():
    swap = Swap(
        asset_id=123,
        venue="rastro",
        to="t02",
        give_ref="LAV-03",
        give_rarity="common",
        want_ref="SAL-01",
        want_rarity="common",
        your_value=4.0,
        worth=9.0,
        give_cash=0,
        want_cash=0,
        notional=10,
    )
    assert {gr.breaker_scope(a) for a in swap.actions()} == {"team_swap"}


def test_a_tripped_scope_refuses_its_writes_and_nothing_else():
    accept = gr.Action("accept_buy", "LAV-03", "common", 5, counterparty="t02")
    assert gr.check(accept, ctx({"board_accept"}), RULES).violations == (
        "circuit breaker board_accept is tripped (`bazaar breaker list`)",
    )
    assert gr.check(accept, ctx({"dealer_buy"}), RULES).allowed
    assert gr.check(gr.Action("cancel", "12"), ctx(set(breakers.SCOPES)), RULES).allowed
    assert not gr.check(accept, ctx({"board_accept"}), RULES).halted  # a refusal, not the kill switch


def test_check_reads_the_process_board_when_the_context_has_none():
    seen = []

    class Board(breakers.BreakerBoard):
        def tripped(self, tick):
            seen.append(tick)
            return frozenset({"dealer_buy"})

    breakers.install(Board(None))
    plain = gr.Context(cash=500, held={}, tick=33, t_hours=1.0)
    assert not gr.check(gr.Action("buy", "LAV-03", "common", 5), plain, RULES).allowed
    assert seen == [33]


class FakeConn:
    def __init__(self, scopes=(), fail=None, block=None):
        self.scopes, self.fail, self.block, self.closed, self.reads = scopes, fail, block, False, 0

    def execute(self, sql, args=None):
        if sql.startswith("select"):
            self.reads += 1
            if self.block is not None:
                self.block.wait(5)
            if self.fail is not None:
                raise self.fail
        return self

    def fetchall(self):
        return [(s,) for s in self.scopes]

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_the_board_reads_once_per_tick():
    fake = FakeConn(scopes=("team_swap",))
    board = breakers.BreakerBoard(lambda: fake, timeout_s=1.0)
    assert board.tripped(5) == {"team_swap"}
    assert board.tripped(5) == {"team_swap"}
    assert fake.reads == 1
    board.tripped(6)
    assert fake.reads == 2


def test_the_board_fails_open_when_postgres_is_down_and_says_so_once_per_tick():
    notes = []

    def down():
        raise psycopg.OperationalError("connection refused")

    board = breakers.BreakerBoard(down, timeout_s=1.0, notify=notes.append)
    assert board.tripped(5) == frozenset()
    assert board.tripped(5) == frozenset()
    assert len(notes) == 1 and "fail open" in notes[0] and "OperationalError" in notes[0]


def test_the_board_fails_open_on_a_slow_read_and_never_stacks_workers():
    gate = threading.Event()
    fake = FakeConn(scopes=("team_swap",), block=gate)
    notes = []
    board = breakers.BreakerBoard(lambda: fake, timeout_s=0.05, notify=notes.append)
    assert board.tripped(5) == frozenset()
    assert board.tripped(6) == frozenset()  # the first read still hangs: no second worker
    assert fake.reads == 1
    gate.set()
    assert any("no answer" in n for n in notes) and any("still running" in n for n in notes)


def test_a_missing_table_means_nothing_was_ever_tripped():
    board = breakers.BreakerBoard(lambda: FakeConn(fail=psycopg.errors.UndefinedTable("no table")), timeout_s=1.0)
    assert board.tripped(5) == frozenset()


def test_no_database_means_no_breaker():
    assert breakers.BreakerBoard(None).tripped(5) == frozenset()


def test_an_unknown_scope_or_empty_reason_writes_nothing():
    with pytest.raises(breakers.BreakerError):
        breakers.known_scope("everything")
    with pytest.raises(breakers.BreakerError):
        breakers.trip(FakeConn(), "team_swap", "  ", 5)  # type: ignore[arg-type]


# ------------------------------------------------------------------ Postgres (throwaway schema; skipped without one)


@pytest.mark.integration
def test_trip_reset_and_lapse_in_postgres(conn):  # noqa: F811
    assert breakers.trip(conn, "team_swap", "gave our last LAV-03", 10, source="watchdog")
    assert not breakers.trip(conn, "team_swap", "again", 11, source="watchdog")  # already active
    board = breakers.BreakerBoard(lambda: conn, timeout_s=2.0)
    assert board.tripped(11) == {"team_swap"}
    assert breakers.reset(conn, "team_swap", 12)
    assert not breakers.reset(conn, "team_swap", 12)

    assert breakers.trip(conn, "dealer_buy", "spam", 20, until_tick=40, source="watchdog")
    assert [r.scope for r in breakers.rows(conn) if r.active(39)] == ["dealer_buy"]
    assert [r.scope for r in breakers.rows(conn) if r.active(40)] == []  # lapsed by itself


@pytest.mark.integration
def test_a_timed_watchdog_trip_never_shortens_a_human_trip(conn):  # noqa: F811
    assert breakers.trip(conn, "maker_post", "by hand", 10)
    assert not breakers.trip(conn, "maker_post", "spam", 11, until_tick=31, source="watchdog")
    (row,) = [r for r in breakers.rows(conn) if r.scope == "maker_post"]
    assert row.until_tick is None and row.source == "manual" and row.reason == "by hand"


@pytest.mark.integration
def test_a_new_trip_is_visible_as_a_decision_and_a_learning(conn):  # noqa: F811
    from bazaar_agent import db

    db.init_schema(conn)
    assert breakers.trip_and_record(conn, "duel_accept", "accept outside our limit", 50, source="watchdog")
    assert not breakers.trip_and_record(conn, "duel_accept", "again", 51, source="watchdog")
    decisions = conn.execute("select agent, kind, tick from decisions").fetchall()
    learnings = conn.execute("select kind, subject, created_tick, source from learnings").fetchall()
    assert decisions == [("guard", "breaker_trip", 50)]
    assert learnings == [("guard_trip", "duel_accept", 50, "watchdog")]


@pytest.mark.integration
def test_the_cli_trips_lists_and_resets(conn, capsys, monkeypatch):  # noqa: F811
    from rich.console import Console

    from bazaar_agent import breaker_cli, db

    monkeypatch.setattr(breaker_cli, "console", Console(width=200))

    db.init_schema(conn)

    class Keep:  # the CLI closes what it opens: hand it a wrapper, keep the test's connection
        def __enter__(self):
            return conn

        def __exit__(self, *exc):
            return False

    breaker_cli.trip_cmd("board_accept", "SAL-08 bought above value", 70, connect=Keep)  # type: ignore[arg-type]
    breaker_cli.list_cmd(connect=Keep)  # type: ignore[arg-type]
    breaker_cli.reset_cmd("board_accept", 71, connect=Keep)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "board_accept: TRIPPED at tick 70" in out and "SAL-08 bought above value" in out
    assert "board_accept: reset at tick 71" in out
    kinds = [r[0] for r in conn.execute("select kind from decisions order by id").fetchall()]
    assert kinds == ["breaker_trip", "breaker_reset"]
