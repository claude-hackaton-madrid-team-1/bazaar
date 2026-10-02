"""Duel scorer on our real practice duels (`/api/duels?done=true`, 2026-10-02)."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent.evals.duels import Closure, score_duel


def test_a_deal_scores_its_pie_share_bound_times_the_value_kept_after_decay(done_duels: dict[int, Any]) -> None:
    o = score_duel(done_duels[85])  # seller, cost 109, sold at 138 after 7 rounds; API result 18.8
    assert o is not None
    assert (o.target, o.subject, o.tick) == ("duel", "duel:85", 156)
    assert o.surplus == 18.8  # the API's result: (138 - 109) * 0.94 ** 7
    assert o.details["surplus_before_decay"] == 29
    assert o.details["pie_lower_bound"] == 29 and o.details["share_upper_bound"] == 1.0
    assert o.score == pytest.approx(0.94**7, abs=1e-4)
    assert o.label == "good"
    assert "7 rounds" in o.explanation and "at most 100%" in o.explanation


def test_long_haggling_costs_the_score_even_on_a_good_price(done_duels: dict[int, Any]) -> None:
    quick, slow = score_duel(done_duels[267]), score_duel(done_duels[202])  # 1 round vs 9 rounds
    assert quick is not None and slow is not None
    assert (quick.score, quick.label) == (0.94, "good")
    assert slow.score == pytest.approx(0.94**9, abs=1e-4) and slow.label == "ok"


def test_a_buyer_deal_measures_surplus_below_our_value(done_duels: dict[int, Any]) -> None:
    o = score_duel(done_duels[274])  # buyer, value 148, bought at 103
    assert o is not None
    assert o.details["surplus_before_decay"] == 45 and o.surplus == 29.2


def test_no_deal_with_the_rival_inside_our_limit_is_money_left_on_the_table(done_duels: dict[int, Any]) -> None:
    o = score_duel(done_duels[5])  # seller cost 47; the rival bid up to 71; we only logged
    assert o is not None
    assert (o.score, o.label, o.details["missed_surplus"], o.details["played"]) == (0.0, "bad", 24, False)
    assert "24 P left on the table" in o.explanation and "log only" in o.explanation


def test_no_deal_without_a_zone_of_agreement_is_a_correct_walk(done_duels: dict[int, Any]) -> None:
    walked = score_duel(done_duels[147])  # seller cost 80; the rival never bid above 73
    silent = score_duel(done_duels[23])  # the rival never offered
    assert walked is not None and silent is not None
    assert (walked.score, walked.label) == (0.0, "ok")
    assert "never inside our limit 80" in walked.explanation
    assert "made no offer" in silent.explanation


def test_a_live_duel_is_not_scored_until_it_closes(done_duels: dict[int, Any]) -> None:
    assert score_duel(done_duels[125]) is None  # still `live` when the doors closed


def test_the_feed_closure_settles_a_duel_the_runner_last_saw_live(done_duels: dict[int, Any]) -> None:
    last_seen_live = {**done_duels[5], "status": "live", "result": None}
    o = score_duel(last_seen_live, Closure("no_deal", 132))
    assert o is not None and (o.tick, o.label) == (132, "bad")


def test_a_deal_whose_price_has_not_arrived_is_kept_unscored() -> None:
    live = {"duel": 9, "role": "seller", "your_limit": 50, "status": "live", "rounds": 2, "decay_per_round": 0.06}
    o = score_duel(live, Closure("deal", 140))
    assert o is not None and o.score is None and "bazaar duel done" in o.explanation


def test_a_deal_outside_our_limit_scores_zero_and_is_flagged() -> None:
    bad = {"duel": 1, "role": "buyer", "your_limit": 80, "status": "deal", "price": 90, "rounds": 1, "result": -9.4}
    o = score_duel({**bad, "decay_per_round": 0.06})
    assert o is not None
    assert (o.score, o.label, o.details["outside_limit"]) == (0.0, "bad", True)
    assert "OUTSIDE our limit" in o.explanation


def test_a_revealed_better_rival_offer_lowers_the_share() -> None:
    duel = {
        "duel": 2,
        "role": "seller",
        "your_limit": 100,
        "status": "deal",
        "price": 110,
        "rounds": 0,
        "decay_per_round": 0.06,
        "messages": [{"from": "Rival", "price": 120}, {"from": "you", "price": 110}],
    }
    o = score_duel(duel)
    assert o is not None and o.details["pie_lower_bound"] == 20 and o.score == 0.5


def test_two_issue_duels_use_the_api_result_and_value_days_against_us() -> None:
    duel = {
        "duel": 3,
        "role": "buyer",
        "your_limit": 100,
        "issues": ["price", "days"],
        "your_days_weight": 2.0,
        "status": "deal",
        "price": 80,
        "days": 4,
        "rounds": 0,
        "decay_per_round": 0.05,
        "result": 12.0,
        "messages": [{"from": "Rival", "price": 80, "days": 4}],
    }
    o = score_duel(duel)
    assert o is not None
    assert o.details["surplus_before_decay"] == 12.0  # from the API: our day weight is private to it
    assert o.details["rival_best"] == 88.0  # 80 + 2 * 4: days count against us, as the player values them


@pytest.mark.parametrize("payload", [{}, {"duel": 4, "role": "judge", "your_limit": 3, "status": "deal"}])
def test_unreadable_duels_are_skipped(payload: dict[str, Any]) -> None:
    assert score_duel(payload) is None
