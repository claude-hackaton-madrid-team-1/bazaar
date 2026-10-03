"""Human approvals: big card trades need one (`human_approval_above`), the board fails closed, requests do not spam."""

import psycopg
import pytest

from bazaar_agent import approvals
from bazaar_agent import guardrails as gr
from tests.test_db import conn, database_url, schema  # noqa: F401 — pytest fixtures (throwaway schema)

pytestmark = pytest.mark.human_approval

RULES = gr.Guardrails(cash_floor=0, max_spend_per_game_hour=1000, human_approval_above=60)
BUY = gr.Action("accept_buy", "LAV-09", "rare", 70, counterparty="t02")
SELL = gr.Action("sell", "LAT-09", "rare", 68, your_value=35.0, counterparty=gr.ANY_TEAM)


def book(*rows: approvals.Approval) -> approvals.ApprovalBook:
    return approvals.ApprovalBook({(a.card, a.side): a for a in rows})


def ctx(approved=None, tick=10, t_hours=1.0):
    return gr.Context(cash=500, held={}, tick=tick, t_hours=t_hours, breakers=frozenset(), approvals=approved)


@pytest.fixture
def asked():
    """This process's approval board, with no database (fails closed) and the requests kept in a list."""
    writes: list[dict] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    approvals._BOARD.pop("board", None)
    if old is not None:
        approvals.install(old)


def test_a_big_buy_without_an_approval_is_refused_and_asked_for(asked):
    verdict = gr.check(BUY, ctx(book()), RULES)
    assert verdict.violations == ("needs human approval: LAV-09 buy 70",)
    assert not verdict.halted
    (row,) = asked
    assert row["card"] == "LAV-09" and row["side"] == "buy" and row["price"] == 70
    assert row["counterparty"] == "t02" and row["hour"] == 1 and row["tick"] == 10


def test_an_approval_covers_its_card_side_and_price_only(asked):
    ok = book(approvals.Approval("LAV-09", "buy", 75, None, until_tick=20))
    assert gr.check(BUY, ctx(ok), RULES).allowed
    assert not gr.check(gr.Action("accept_buy", "LAV-09", "rare", 76, counterparty="t02"), ctx(ok), RULES).allowed
    assert not gr.check(gr.Action("accept_buy", "LAV-10", "rare", 70, counterparty="t02"), ctx(ok), RULES).allowed
    assert not gr.check(gr.Action("sell", "LAV-09", "rare", 70, your_value=1.0), ctx(ok), RULES).allowed


def test_an_expired_approval_approves_nothing(asked):
    old = book(approvals.Approval("LAV-09", "buy", 75, None, until_tick=10))
    assert not gr.check(BUY, ctx(old, tick=10), RULES).allowed
    assert gr.check(BUY, ctx(old, tick=9), RULES).allowed


def test_a_big_sell_needs_an_approval_down_to_its_min(asked):
    assert gr.check(SELL, ctx(book()), RULES).violations == ("needs human approval: LAT-09 sell 68",)
    assert gr.check(SELL, ctx(book(approvals.Approval("LAT-09", "sell", None, 60, 20))), RULES).allowed
    assert not gr.check(SELL, ctx(book(approvals.Approval("LAT-09", "sell", None, 70, 20))), RULES).allowed


def test_trades_under_the_threshold_duels_and_packs_need_none(asked):
    assert gr.check(gr.Action("accept_buy", "LAV-09", "rare", 59, counterparty="t02"), ctx(book()), RULES).allowed
    duel = gr.Action("duel_accept", "7", price=95, limit=50, role="seller")
    assert gr.check(duel, ctx(book()), RULES).allowed
    pack = gr.Action("buy", "sobre_barrio", "pack", 60)
    assert gr.check(pack, ctx(book()), RULES.model_copy(update={"max_price_pack": 80})).allowed
    assert gr.check(BUY, ctx(book()), RULES.model_copy(update={"human_approval_above": 0})).allowed
    assert asked == []


def test_a_swap_counts_the_copy_it_gives(asked):
    swap_bid = gr.Action("bid", "SAL-10", "rare", 20, counterparty="t03", gives_value=45.0, scope="team_swap")
    assert gr.check(swap_bid, ctx(book()), RULES).violations == ("needs human approval: SAL-10 buy 65",)


def test_an_approval_never_loosens_another_cap(asked):
    ok = book(approvals.Approval("LAV-09", "buy", 200, None, until_tick=20))
    over = gr.Action("accept_buy", "LAV-09", "rare", 90, counterparty="t02")
    assert gr.check(over, ctx(ok), RULES).violations == ("price 90 > max_price_rare 80",)
    assert asked == []  # a trade another rule refuses is never put to a human


def test_unreadable_approvals_fail_closed(asked):
    plain = gr.Context(cash=500, held={}, tick=10, t_hours=1.0, breakers=frozenset())
    assert gr.check(BUY, plain, RULES).violations == ("needs human approval: LAV-09 buy 70 (approvals unreadable)",)

    def down():
        raise psycopg.OperationalError("connection refused")

    notes: list[str] = []
    board = approvals.ApprovalBoard(down, timeout_s=1.0, notify=notes.append, write=lambda row: None)
    assert board.read(5) is None
    assert len(notes) == 1 and "fail closed" in notes[0]


def test_a_missing_table_is_an_empty_book_not_an_outage():
    class NoTable:
        closed = False

        def execute(self, *a):
            raise psycopg.errors.UndefinedTable("no table")

        def commit(self):
            pass

        def rollback(self):
            pass

    board = approvals.ApprovalBoard(lambda: NoTable(), timeout_s=1.0)  # type: ignore[arg-type,return-value]
    assert board.read(5) == approvals.EMPTY


def test_one_request_per_card_and_side_per_game_hour(asked):
    for tick in (10, 11, 12):
        gr.check(BUY, ctx(book(), tick=tick, t_hours=1.2), RULES)
    gr.check(SELL, ctx(book(), t_hours=1.5), RULES)
    gr.check(BUY, ctx(book(), tick=130, t_hours=2.1), RULES)
    assert [(r["card"], r["side"], r["hour"]) for r in asked] == [
        ("LAV-09", "buy", 1),
        ("LAT-09", "sell", 1),
        ("LAV-09", "buy", 2),
    ]


def test_approve_revoke_and_the_board_on_postgres(conn):  # noqa: F811
    from bazaar_agent import db

    db.init_schema(conn)
    approvals.approve(conn, "lav-09", "buy", 75, 50, "omar", "page bonus")
    approvals.approve(conn, "LAT-09", "sell", 60, 30, "omar", "")
    board = approvals.ApprovalBoard(lambda: conn, timeout_s=2.0)
    read = board.read(20)
    assert read is not None and read.covers("LAV-09", "buy", 75, 20) and read.covers("LAT-09", "sell", 61, 20)
    assert [a.card for a in approvals.active(conn, 40)] == ["LAV-09"]  # LAT-09 lapsed at 30
    assert approvals.revoke(conn, "LAV-09", "buy") and not approvals.revoke(conn, "LAV-09", "buy")
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(conn, "LAV-09; drop table x", "buy", 75, 50, "omar", "")


def test_a_request_is_one_row_per_hour_across_processes(conn):  # noqa: F811
    from bazaar_agent import db

    db.init_schema(conn)
    need = {"card": "LAV-09", "side": "buy", "price": 70, "tick": 10, "hour": 1, "counterparty": "t02"}
    assert approvals.record_need(conn, need)
    assert not approvals.record_need(conn, {**need, "tick": 11})  # another process, same hour
    assert approvals.record_need(conn, {**need, "tick": 130, "hour": 2})
    assert [(r["card"], r["tick"]) for r in approvals.pending(conn, 0)] == [("LAV-09", 130)]


def test_the_cli_approves_lists_and_revokes(conn, monkeypatch):  # noqa: F811
    from rich.console import Console

    from bazaar_agent import approval_cli, db

    db.init_schema(conn)
    monkeypatch.setattr(approval_cli, "console", Console(width=200))

    class Keep:
        def __enter__(self):
            return conn

        def __exit__(self, *exc):
            return False

    approvals.record_need(conn, {"card": "SAL-10", "side": "buy", "price": 72, "tick": 100, "hour": 3})
    approval_cli.approve_cmd("LAV-09", "buy", 75, 240, "page bonus", "omar", 100, connect=Keep)  # type: ignore[arg-type]
    approval_cli.list_cmd(101, connect=Keep)  # type: ignore[arg-type]
    approval_cli.revoke_cmd("LAV-09", "buy", "omar", 102, connect=Keep)  # type: ignore[arg-type]
    kinds = conn.execute("select kind from decisions where agent = 'guard' order by id").fetchall()
    assert [k[0] for k in kinds] == ["approval_needed", "approval_granted", "approval_revoked"]
