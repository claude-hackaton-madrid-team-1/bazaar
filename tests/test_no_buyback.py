"""`no_buyback_ticks`: never buy back a card we sold or swapped away in the window (SAL-07: sold 948, rebought 958)."""

import pytest

from bazaar_agent import guardrails as gr
from bazaar_agent import impact_board
from bazaar_agent import move_impact as mi
from bazaar_agent.agents.seller import Swap
from bazaar_agent.official_values import unread_only
from tests.test_move_impact import BOUGHT_FROM_T02, REBOUGHT_FROM_ABUELA, SOLD_TO_PILAR

pytestmark = pytest.mark.no_buyback

RULES = gr.Guardrails(no_buyback_ticks=480, cash_floor=0)
FACTS = mi.Facts("t01", {}, (), sold=mi.sales([BOUGHT_FROM_T02, SOLD_TO_PILAR, REBOUGHT_FROM_ABUELA], "t01"))
REBUY = gr.Action("buy", "SAL-07", "uncommon", 21)


def ctx(tick: int = 958, facts: mi.Facts | None = FACTS, **kw) -> gr.Context:
    cards = mi.OurCards("t01")
    return gr.Context(cash=500, held={}, tick=tick, t_hours=9.3, breakers=frozenset(), cards=cards, impact=facts, **kw)


def test_our_sales_are_the_copies_that_left_us():
    assert mi.sales([BOUGHT_FROM_T02, SOLD_TO_PILAR, REBOUGHT_FROM_ABUELA], "t01") == {"SAL-07": 948}
    swap = {
        "tick": 50,
        "items": [
            {"id": 1, "ref": "LAV-02", "frm": "t01", "to": "t05"},
            {"id": 2, "ref": "MAL-03", "frm": "t05", "to": "t01"},
        ],
    }
    pack = {"tick": 60, "items": [{"id": 3, "kind": "pack", "ref": "sobre_barrio", "frm": "t01", "to": "abuela"}]}
    assert mi.sales([swap, pack], "t01") == {"LAV-02": 50}  # the copy a swap gives is a sale; a pack is no card


def test_the_sal07_buy_back_is_refused_on_every_buy_path():
    verdict = gr.check(REBUY, ctx(), RULES)
    assert verdict.violations == (
        "no buy-back: we sold SAL-07 at tick 948, 10 ticks ago (no_buyback_ticks 480: buying it back is not "
        "realistic trading)",
    )
    for kind in ("accept_buy", "bid"):
        assert not gr.check(gr.Action(kind, "SAL-07", "uncommon", 21), ctx(), RULES).allowed
    swap_in = next(
        a
        for a in Swap(9, "LAV-02", "common", 2.0, "SAL-07", "uncommon", 30.0, "rastro", "t05").actions()
        if a.kind == "bid"
    )
    assert not gr.check(swap_in, ctx(), RULES).allowed


def test_another_card_a_first_purchase_and_a_sale_pass():
    assert gr.check(gr.Action("buy", "SAL-08", "uncommon", 21), ctx(), RULES).allowed
    assert gr.check(REBUY, ctx(facts=mi.Facts("t01", {}, ())), RULES).allowed  # never sold
    sale = gr.Action("sell", "SAL-07", "uncommon", 40, counterparty=gr.ANY_TEAM)
    assert gr.check(sale, ctx(), RULES).allowed


def test_the_window_ends_after_no_buyback_ticks():
    assert not gr.check(REBUY, ctx(tick=948 + 479), RULES).allowed
    assert gr.check(REBUY, ctx(tick=948 + 480), RULES).allowed


def test_zero_turns_it_off():
    assert gr.check(REBUY, ctx(), RULES.model_copy(update={"no_buyback_ticks": 0})).allowed


def test_unread_sales_hold_a_send_and_skip_a_ranking():
    assert impact_board.board().read(958) is None  # the suite's empty board
    verdict = gr.check(REBUY, ctx(facts=None), RULES)
    assert not verdict.allowed and unread_only(verdict.violations)  # a dealer thread holds, never walks
    assert gr.check(REBUY, ctx(facts=None, ranking=True), RULES).allowed
    other_team = mi.Facts("t09", {}, (), sold={})
    assert unread_only(gr.check(REBUY, ctx(facts=other_team), RULES).violations)


def test_read_facts_carries_our_sales():
    from tests.test_impact_guard import FakeConn

    class Sold(FakeConn):
        def fetchall(self):
            rows = super().fetchall()
            return rows if "me_snapshots" in self.sql[-1][0] else [(BOUGHT_FROM_T02,), (SOLD_TO_PILAR,)]

    facts = impact_board.read_facts(Sold(), 960)  # type: ignore[arg-type]
    assert facts is not None and facts.sold == {"SAL-07": 948} and 438 not in facts.origins
