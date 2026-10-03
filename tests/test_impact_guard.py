"""guardrails.check(): `max_score_loss_per_move` refuses a sale that could cost us score, unless a human approved it."""

from dataclasses import replace

import pytest

from bazaar_agent import approvals, impact_board
from bazaar_agent import guardrails as gr
from bazaar_agent import move_impact as mi
from bazaar_agent.agents.seller import Swap
from tests.test_move_impact import BOUGHT_FROM_T02, me_at_947, points

pytestmark = pytest.mark.score_impact

RULES = gr.Guardrails(max_score_loss_per_move=0.2, sell_min_value_ratio=0.0)  # the sell floor off: this rule alone


@pytest.fixture
def asked():
    writes: list[dict] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    if old is None:
        approvals._BOARD.pop("board", None)
    else:
        approvals.install(old)


def ctx(facts: mi.Facts | None = None, book: approvals.ApprovalBook | None = approvals.EMPTY, **kw) -> gr.Context:
    me = me_at_947()
    base = gr.Context(cash=100, held={"SAL-07": 1}, tick=947, t_hours=9.2, breakers=frozenset(), approvals=book)
    return replace(base, cards=mi.our_cards(me), impact=facts, **kw)


INCIDENT = mi.Facts("t01", mi.origins([BOUGHT_FROM_T02], "t01"), points(947))
SALE = gr.Action("dealer_sell", "SAL-07", "uncommon", 29, scope="dealer_sell", asset=438)


def test_the_incident_sale_is_refused_and_a_human_is_asked(asked):
    verdict = gr.check(SALE, ctx(INCIDENT), RULES)
    assert not verdict.allowed
    (why,) = verdict.violations
    assert why.startswith("score impact -4.70 < -0.2")
    assert "to a dealer" in why and "?" not in why
    assert "bought from t02 for 23" in why and why.endswith("needs human approval: SAL-07 sell 29")
    assert asked[0]["card"] == "SAL-07" and asked[0]["side"] == "sell" and asked[0]["score_impact"] == -4.7


def test_the_caller_value_is_not_needed_the_guard_reads_me():
    assert SALE.your_value is None  # the incident's real hazard: a sale priced without the copy's value
    assert not gr.check(SALE, ctx(INCIDENT), RULES).allowed


def test_a_human_approval_of_that_card_at_that_price_lets_it_through(asked):
    book = approvals.ApprovalBook({("SAL-07", "sell"): approvals.Approval("SAL-07", "sell", None, 29, 1000)})
    assert gr.check(SALE, ctx(INCIDENT, book), RULES).allowed
    lower = approvals.ApprovalBook({("SAL-07", "sell"): approvals.Approval("SAL-07", "sell", None, 40, 1000)})
    assert not gr.check(SALE, ctx(INCIDENT, lower), RULES).allowed  # approved down to 40, not 29


def test_unreadable_approvals_hold_instead_of_walking(asked):
    verdict = gr.check(SALE, ctx(INCIDENT, book=None), RULES)
    assert verdict.violations[0].endswith(approvals.UNREAD)


def test_unread_facts_fail_closed_at_the_worst_case(asked):
    assert impact_board.board().read(947) is None  # the suite's empty board: Postgres unread
    below = gr.Action("dealer_sell", "SAL-10", "rare", 90, scope="dealer_sell")
    assert not gr.check(below, ctx(None), RULES).allowed  # priced as bought from a team
    above = gr.Action("dealer_sell", "SAL-10", "rare", 180, scope="dealer_sell")
    assert gr.check(above, ctx(None), RULES).allowed


def test_a_dealer_sale_of_a_duplicate_from_our_starting_stock_passes(asked):
    sale = gr.Action("dealer_sell", "SAL-03", "common", 1, scope="dealer_sell", asset=1)
    assert gr.check(sale, ctx(INCIDENT), RULES).allowed


def test_a_board_ask_to_anyone_is_a_team_trade(asked):
    ask = gr.Action("sell", "SAL-10", "rare", 150, counterparty=gr.ANY_TEAM, asset=544)
    verdict = gr.check(ask, ctx(INCIDENT), RULES)
    assert not verdict.allowed and "a team trade at private values" in verdict.violations[0]


def test_a_bid_from_a_board_pseudonym_counts_as_a_team(asked):
    take = gr.Action("accept_sell", "SAL-10", "rare", 150, counterparty="m1a2b3c4", asset=544)
    assert "a team trade" in gr.check(take, ctx(INCIDENT), RULES).violations[0]


def test_off_rankings_and_packs_are_not_checked(asked):
    assert gr.check(SALE, ctx(INCIDENT), RULES.model_copy(update={"max_score_loss_per_move": 0.0})).allowed
    assert gr.check(SALE, ctx(INCIDENT, ranking=True), RULES).allowed
    pack = gr.Action("sell", "sobre_barrio", "pack", 1, counterparty=gr.ANY_TEAM)
    assert gr.check(pack, ctx(INCIDENT), RULES).allowed
    assert asked == []


def test_a_swap_gives_its_own_copy_to_the_guard():
    swap = Swap(438, "SAL-07", "uncommon", 118.6, "LAV-10", "rare", 30.0, "rastro", "t05")
    sale = next(a for a in swap.actions() if a.kind == "sell")
    assert (sale.asset, sale.counterparty, sale.scope) == (438, "t05", "team_swap")


def test_context_from_reads_our_cards_from_me(tmp_path):
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    c = gr.context_from(me_at_947(), 947, 9.2, ledger, RULES)
    assert c.cards is not None and c.cards.team == "t01" and c.cards.complete == frozenset({"SAL"})
    assert c.cards.copy(438) == mi.Copy(438, "SAL-07", "uncommon", 118.6)


class FakeConn:
    """`impact_board.read_facts`'s three queries, answered from the incident's rows."""

    def __init__(self, us=("real", "t01")):
        self.us, self.sql = us, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return self

    def fetchone(self):
        return (958,) if "max(tick)" in self.sql[-1][0] else self.us

    def fetchall(self):
        sql = self.sql[-1][0]
        if "me_snapshots" in sql:
            return [(t, n, g) for t, n, g in [(940, 134.2, 20.75), (948, 44.6, 20.75), (950, 44.6, 16.48)]]
        return [(BOUGHT_FROM_T02,), ("not a dict",)]


def test_read_facts_reads_our_snapshots_and_settlements():
    conn = FakeConn()
    facts = impact_board.read_facts(conn, 960)  # type: ignore[arg-type]
    assert facts is not None and facts.team == "t01" and facts.origins[438].frm == "t02"
    assert facts.slope(0.053).loss_events == 1 and facts.slope(0.053).loss == 0.053  # 0.048 measured, floored
    assert (facts.tick, facts.tape_tick, facts.tape_current) == (960, 958, True)
    assert conn.sql[1][1] == ("real", "t01", 960 - impact_board.WINDOW_TICKS)
    assert impact_board.read_facts(FakeConn(us=None), 960) is None  # type: ignore[arg-type]
