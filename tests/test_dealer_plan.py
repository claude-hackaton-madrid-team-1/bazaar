"""N14a: the per-dealer plan built from recall (policy, curve, lift), on Chato's real Friday numbers."""

from dataclasses import replace

from bazaar_agent.agents.dealer_plan import final_reach, patience_ladder, plan_dealer_buy
from bazaar_agent.guardrails import parse_guardrails
from bazaar_agent.learn.curves import CurveStats
from bazaar_agent.learn.evolve import Ladder, LadderPolicy
from bazaar_agent.strategy import Move

OFF = parse_guardrails("- `dealer_final_lift` = 0 — x").rules
LIFT = parse_guardrails("- `dealer_final_lift` = 0.15 — x").rules
SURPLUS = 8.0
# Chato's uncommons, Friday ticks 99-159 (feed): fills 28-32, opening 33, a final after ~6 bids.
FILLS = (28, 28, 29, 29, 31, 32)
CURVE = CurveStats("chato", "card:uncommon", 13, FILLS, (33,) * 11, 3, 6.0, 1.0, None, (187, 228, 253))


def chato_move(value=45.0, ladder=(26, 26, 1)):
    cmd = "uv run bazaar dealer buy LAV-08 --start 26 --max 26 --dealer chato"
    return Move(
        "buy",
        "dealer_floor",
        "LAV-08",
        "uncommon",
        value,
        28.0,
        value - 28,
        0.4,
        10.0,
        "chato",
        ("chato",),
        "buy",
        ladder[1],
        "worth 45; chato fills 28",
        cmd,
        ladder=ladder,
    )


def skip_policy(tick=150):
    reason = "skip: 0 of 6 chato card:uncommon conversations closed at or under the cap 26"
    return LadderPolicy("chato", "card:uncommon", None, reason, FILLS, 13, 6.0, tick)


def test_lift_off_and_no_policy_leave_the_move_as_today():
    plan = plan_dealer_buy(chato_move(), None, CURVE, OFF, SURPLUS)
    assert plan.move == chato_move() and plan.final_max is None and plan.notes == ()


def test_a_learned_policy_replaces_the_ladder_and_says_which_learning_did_it():
    abuela = replace(chato_move(ladder=(17, 26, 3)), source="abuela")
    policy = LadderPolicy("abuela", "card:uncommon", Ladder(17, 1, 24), "best replayed share", FILLS, 60, 5.0, 140)
    plan = plan_dealer_buy(abuela, policy, None, OFF, SURPLUS)
    assert plan.move is not None and plan.move.ladder == (17, 24, 1) and plan.move.limit == 24
    assert "--start 17 --max 24" in plan.move.command
    assert plan.changed_by == ["learning policy abuela card:uncommon @t140: ladder 17→26 step 3 → 17→24 step 1"]


def test_with_the_lift_off_a_learned_skip_still_skips():
    plan = plan_dealer_buy(chato_move(), skip_policy(), CURVE, OFF, SURPLUS)
    assert plan.move is None and plan.skip is not None and plan.skip.startswith("skip: 0 of 6")


def test_final_reach_is_the_lifted_cap_inside_our_value_and_only_above_the_top():
    assert final_reach("uncommon", 45.0, 26, LIFT, SURPLUS) == 29
    assert final_reach("uncommon", 35.0, 26, LIFT, SURPLUS) == 27  # value 35 - surplus 8
    assert final_reach("uncommon", 33.0, 26, LIFT, SURPLUS) is None  # nothing above our top
    assert final_reach("uncommon", 45.0, 26, OFF, SURPLUS) is None
    assert final_reach("pack", 45.0, 20, LIFT, SURPLUS) is None  # packs keep their cap
    assert final_reach("epic", 200.0, 0, LIFT, SURPLUS) is None


def test_the_lift_turns_a_chato_skip_into_the_patience_play():
    plan = plan_dealer_buy(chato_move(), skip_policy(), CURVE, LIFT, SURPLUS)
    assert plan.move is not None and plan.final_max == 29
    # 6 bids of patience + 2: start 26 - 7 = 19, step 1, our bids never above 26
    assert plan.move.ladder == (19, 26, 1) and plan.move.limit == 26
    assert plan.changed_by == [
        "learning policy chato card:uncommon @t150: skip lifted: 4 of 6 fills at or under the final cap 29",
        "curve chato card:uncommon (final after ~6 bids, opens 33): ladder 26→26 step 1 → 19→26 step 1",
        "dealer_final_lift 0.15: take a final up to 29 (our bids stay at or under 26)",
    ]
    assert "a final up to 29" in plan.move.reason


def test_a_skip_stays_when_too_few_fills_sit_under_the_lifted_cap():
    rares = (82, 89, 90, 90, 91, 91, 93, 93)
    policy = LadderPolicy("chato", "card:rare", None, "skip: 0 of 8", rares, 15, 5.0, 150)
    low = parse_guardrails("- `dealer_final_lift` = 0.05 — x").rules  # rare final cap 84: 1 of 8 fills
    mv = replace(chato_move(value=150.0, ladder=(80, 80, 1)), ref="LAV-09", rarity="rare")
    assert plan_dealer_buy(mv, policy, None, low, SURPLUS).move is None
    assert plan_dealer_buy(mv, policy, None, LIFT, SURPLUS).final_max == 92


def test_patience_ladder_never_raises_the_start_nor_drops_under_an_ignored_bid_or_40pct_of_the_opening():
    assert patience_ladder((26, 26, 1), 6.0, 33.0, None) == (19, 26, 1)
    assert patience_ladder((12, 26, 2), 6.0, 33.0, None) == (12, 26, 1)  # already low: kept, step 1
    assert patience_ladder((26, 26, 1), 20.0, 33.0, None) == (14, 26, 1)  # 40 % of the opening 33
    assert patience_ladder((26, 26, 1), 20.0, None, 21) == (22, 26, 1)  # it ignored a first bid of 21
    assert patience_ladder((26, 26, 1), None, None, None) == (20, 26, 1)  # default patience 5


def test_without_a_curve_the_policy_patience_is_used():
    plan = plan_dealer_buy(chato_move(), skip_policy(), None, LIFT, SURPLUS)
    assert plan.move is not None and plan.move.ladder == (19, 26, 1)
    assert plan.changed_by[1].startswith("learning policy chato card:uncommon @t150 (final after ~6 bids)")
