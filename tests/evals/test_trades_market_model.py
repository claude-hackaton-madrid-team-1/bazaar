"""Team-trade, Market Test and shared-model scorers (no database, no network)."""

from __future__ import annotations

import pytest

from bazaar_agent.evals.market import score_market_test
from bazaar_agent.evals.model import JevCheck, day_of, label_for
from bazaar_agent.evals.trades import Settlement, Valuation, jev_check, score_trade
from tests.evals.conftest import OURS

BUY = Settlement(67, 40, "rastro", OURS, "t06", "LAV-02", (300,), 12, 2)
SELL = Settlement(68, 41, "rastro", "t10", OURS, "LAT-09", (15,), 68, 4)


def test_a_buy_gains_our_value_minus_what_left_our_cash() -> None:
    o = score_trade(BUY, OURS, Valuation(card_value=20.0, cash_change=-14, before_tick=39, after_tick=41))
    assert (o.target, o.subject, o.surplus, o.label) == ("trade", "settlement:67", 6.0, "ok")
    assert o.score == round(6 / 20, 4)
    assert o.details["cash_from"] == "snapshots" and o.details["counterparty"] == "t06"


def test_without_a_clean_cash_change_the_buyer_pays_price_plus_fee() -> None:
    o = score_trade(BUY, OURS, Valuation(card_value=20.0, cash_change=None))
    assert (o.details["cash"], o.surplus) == (14, 6.0)


def test_a_sale_gains_the_cash_over_the_value_we_gave_up() -> None:
    o = score_trade(SELL, OURS, Valuation(card_value=45.5, cash_change=None))
    assert (o.surplus, o.label) == (22.5, "ok")
    assert o.score == round(22.5 / 68, 4)


def test_a_trade_below_our_value_is_a_loss() -> None:
    o = score_trade(SELL, OURS, Valuation(card_value=80.0, cash_change=64))
    assert (o.score, o.label, o.surplus) == (0.0, "bad", -16.0)
    assert "A loss" in o.explanation


def test_a_trade_without_a_snapshot_value_is_kept_unscored() -> None:
    o = score_trade(BUY, OURS, Valuation(card_value=None, cash_change=None))
    assert o.score is None and "no /me snapshot" in o.explanation


@pytest.mark.parametrize(
    ("verdict", "gained", "right"),
    [("yes", 5.0, True), ("yes", -1.0, False), ("no", 5.0, False), ("no", -1.0, True), ("undecided", 5.0, None)],
)
def test_a_decided_jev_verdict_is_right_when_the_settled_trade_agrees(
    verdict: str, gained: float, right: bool | None
) -> None:
    check = jev_check({"verdict": verdict, "value": 0.8}, gained)
    assert check == JevCheck("offer_is_worth_accepting", verdict, right)
    assert check.outcome == ("unknown" if right is None else "right" if right else "wrong")


def test_the_jev_check_rides_on_the_trade_outcome() -> None:
    o = score_trade(BUY, OURS, Valuation(20.0, -14), decision_id=4, jev={"verdict": "yes"})
    assert o.decision_id == 4 and o.jev is not None and o.jev.right is True
    assert jev_check(None, 1.0) is None


def test_the_market_test_waits_for_an_official_efficiency() -> None:
    assert score_market_test({"bench_efficiency": None, "venue": None}, 159, "fri") is None
    assert score_market_test(None, 159, "fri") is None
    o = score_market_test({"bench_efficiency": 0.72, "bench_venue": "v3", "bench_points": 4.1}, 300, "sat")
    assert o is not None
    assert (o.subject, o.score, o.label, o.day) == ("market_test:sat", 0.72, "good", "sat")


def test_labels_and_days() -> None:
    assert [label_for(s) for s in (0.0, 0.29, 0.3, 0.59, 0.6, 1.0)] == ["bad", "bad", "ok", "ok", "good", "good"]
    openings = [(0, "fri"), (241, "sat"), (2000, "sun")]
    assert [day_of(t, openings) for t in (None, 0, 159, 241, 1999, 2500)] == [None, "fri", "fri", "sat", "sat", "sun"]
    assert day_of(5, []) is None
