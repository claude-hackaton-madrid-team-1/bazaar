"""move_impact: the score a sale, swap or buy could cost us, replayed on the SAL-07 incident (Sat 3 Oct, tick 947)."""

import pytest

from bazaar_agent import move_impact as mi

# Our real settlements of asset 438 / 1003 (`feed_events`, ticks 320, 948, 958).
BOUGHT_FROM_T02 = {
    "settlement": 408,
    "tick": 320,
    "parties": ["t02", "t01"],
    "venue": "rastro",
    "persona": None,
    "price": 23,
    "items": [{"id": 438, "kind": "card", "ref": "SAL-07", "frm": "t02", "to": "t01"}],
}
SOLD_TO_PILAR = {
    "settlement": 872,
    "tick": 948,
    "parties": ["pilar", "t01"],
    "persona": "pilar",
    "price": 29,
    "items": [{"id": 438, "kind": "card", "ref": "SAL-07", "frm": "t01", "to": "pilar"}],
}
REBOUGHT_FROM_ABUELA = {
    "settlement": 881,
    "tick": 958,
    "parties": ["abuela", "t01"],
    "persona": "abuela",
    "price": 21,
    "items": [{"id": 1003, "kind": "card", "ref": "SAL-07", "frm": "abuela", "to": "t01"}],
}
# Our /me score (tick, neg_points, negotiating) from `me_snapshots`, around the two biggest moves.
SERIES = [
    (370, 57.4, 14.98),
    (376, 101.7, 14.98),
    (380, 101.7, 15.65),
    (386, 134.7, 15.65),
    (400, 134.7, 15.64),
    (940, 134.2, 20.75),
    (947, 134.2, 20.75),
    (948, 44.6, 20.75),
    (949, 44.6, 20.75),
    (950, 44.6, 16.48),
    (960, 44.6, 16.74),
]


def points(until: int) -> tuple[mi.ScorePoint, ...]:
    return tuple(mi.ScorePoint(t, n, g) for t, n, g in SERIES if t <= until)


def me_at_947() -> dict:
    """/me at tick 947 (the SAL copies that matter): one SAL-07, Salamanca complete."""
    return {
        "id": "t01",
        "assets": [
            {"id": 1, "kind": "card", "ref": "SAL-03", "rarity": "common", "your_value": 1.3},
            {"id": 675, "kind": "card", "ref": "SAL-03", "rarity": "common", "your_value": 1.3},
            {"id": 438, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 118.6},
            {"id": 544, "kind": "card", "ref": "SAL-10", "rarity": "rare", "your_value": 177.1},
        ],
        "album": {"pages": [{"set": "SAL", "have": 10, "of": 10, "complete": True}]},
    }


def test_origins_follow_each_copy_through_the_tape():
    known = mi.origins([BOUGHT_FROM_T02], "t01")
    assert known[438] == mi.Origin("team", "t02", 23, 320)
    after = mi.origins([BOUGHT_FROM_T02, SOLD_TO_PILAR, REBOUGHT_FROM_ABUELA], "t01")
    assert 438 not in after  # it left us
    assert after[1003] == mi.Origin("dealer", "abuela", 21, 958)


def test_a_copy_no_settlement_brought_is_starting_stock_or_a_pack():
    known = mi.origins([BOUGHT_FROM_T02], "t01")
    assert mi.origin_of(1, known, "t01").kind == "start"  # t01 was dealt ids 1-15
    assert mi.origin_of(16, known, "t01").kind == "pack"
    assert mi.origin_of(438, None, "t01") == mi.UNKNOWN  # the tape was not read


def test_the_slope_before_the_incident_is_the_fallback_and_after_it_the_measured_loss():
    before = mi.slope(points(947), 0.053)
    assert (before.loss, before.loss_events) == (0.053, 0)
    assert before.gain == pytest.approx(0.67 / 44.3, abs=1e-4)  # 376: +44.3 neg_points, board +0.67 at 380
    after = mi.slope(points(960), 0.053)
    assert after.loss == pytest.approx(4.27 / 89.6, abs=1e-4)  # 948: -89.6, board -4.27 at 950
    assert after.loss_events == 1


def test_incident_replay_selling_sal07_to_pilar_at_29_costs_about_4_7():
    facts = mi.Facts("t01", mi.origins([BOUGHT_FROM_T02], "t01"), points(947))
    impact = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-07", "uncommon", 29, None, facts, 0.053, 0.05)
    assert impact.score == pytest.approx(-4.7, abs=0.5)
    assert impact.score == pytest.approx((29 - 118.6) * 0.053 + 0.05)
    assert impact.neg_points == pytest.approx(-89.6)
    assert (impact.origin.kind, impact.origin.frm, impact.asset) == ("team", "t02", 438)
    assert impact.breaks_page and not impact.team_trade
    assert "bought from t02 for 23" in impact.reason and "BREAKS a complete page" in impact.reason


def test_a_dealer_sale_of_a_copy_not_bought_from_a_team_moves_no_neg_points():
    facts = mi.Facts("t01", {}, ())
    impact = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-03", "common", 5, None, facts, 0.053, 0.05, asset=1)
    assert (impact.neg_points, impact.score, impact.origin.kind) == (0.0, 0.05, "start")
    assert not impact.breaks_page  # a duplicate


def test_a_sale_to_a_team_is_a_team_trade_whatever_the_copy_came_from():
    facts = mi.Facts("t01", {}, ())
    impact = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-10", "rare", 100, "t05", facts, 0.053, 0.05)
    assert impact.team_trade and impact.neg_points == pytest.approx(100 - 177.1)
    assert impact.score == pytest.approx((100 - 177.1) * 0.053)  # no dealer ladder on a team trade


def test_unread_facts_price_the_copy_as_bought_from_a_team():
    impact = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-10", "rare", 90, None, None, 0.053, 0.05)
    assert impact.assumed and impact.origin == mi.UNKNOWN
    assert impact.score == pytest.approx((90 - 177.1) * 0.053 + 0.05)
    above = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-10", "rare", 180, None, None, 0.053, 0.05)
    assert above.score is not None and above.score > 0


def test_facts_of_another_team_are_not_ours():
    facts = mi.Facts("t09", {438: mi.Origin("dealer", "abuela", 5, 1)}, ())
    impact = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-07", "uncommon", 29, None, facts, 0.053, 0.05)
    assert impact.assumed  # t09's tape says nothing about our copy


def test_without_an_asset_the_copy_that_costs_most_is_priced():
    me = me_at_947()
    me["assets"].append({"id": 900, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 4.0})
    facts = mi.Facts("t01", mi.origins([BOUGHT_FROM_T02], "t01"), ())
    worst = mi.sell_impact(mi.our_cards(me), "SAL-07", "uncommon", 10, None, facts, 0.053, 0.05)
    assert worst.asset == 438 and worst.origin.kind == "team"
    named = mi.sell_impact(mi.our_cards(me), "SAL-07", "uncommon", 10, None, facts, 0.053, 0.05, asset=900)
    assert named.origin.kind == "pack" and named.neg_points == 0.0


def test_a_copy_with_no_value_cannot_be_estimated():
    me = {"id": "t01", "assets": [{"id": 5, "kind": "card", "ref": "LAV-01", "rarity": "common"}]}
    impact = mi.sell_impact(mi.our_cards(me), "LAV-01", "common", 9, None, None, 0.053, 0.05)
    assert impact.score is None and "cannot be estimated" in impact.reason
    caller = mi.sell_impact(mi.our_cards(me), "LAV-01", "common", 9, None, None, 0.053, 0.05, value=3.0)
    assert caller.score == pytest.approx((9 - 3.0) * 0.053 + 0.05)


def test_a_buy_from_a_team_gains_and_a_dealer_buy_adds_only_the_ladder():
    slope = mi.Slope(0.05, 0.02)
    team = mi.estimate("buy", "LAV-10", 60, 218.0, "t05", slope, 0.05)
    assert team.neg_points == pytest.approx(158.0) and team.score == pytest.approx(158.0 * 0.02)
    dealer = mi.estimate("buy", "LAV-10", 60, 218.0, "picaros", slope, 0.05)
    assert (dealer.neg_points, dealer.score) == (0.0, 0.05)


def test_our_cards_tolerates_a_hostile_me():
    cards = mi.our_cards({"id": "t01\n", "assets": [{"id": "x"}, None, {"id": 3, "your_value": True}], "album": []})
    assert cards.team is None and cards.copies == (mi.Copy(3, "None", None, None),)
    assert cards.complete == frozenset()


def test_as_state_is_what_a_decider_reads():
    facts = mi.Facts("t01", mi.origins([BOUGHT_FROM_T02], "t01"), ())
    state = mi.sell_impact(mi.our_cards(me_at_947()), "SAL-07", "uncommon", 29, None, facts, 0.053, 0.05).as_state()
    assert state["score_delta"] == pytest.approx(-4.699, abs=1e-3)
    assert (state["copy_origin"], state["copy_from"], state["breaks_complete_page"]) == ("team", "t02", True)
