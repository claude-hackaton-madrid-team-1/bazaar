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
    # 6 bids of patience + 3 (and at least 9): start 26 - 8 = 18, step 1, our bids never above 26
    assert plan.move.ladder == (18, 26, 1) and plan.move.limit == 26
    assert plan.changed_by == [
        "learning policy chato card:uncommon @t150: skip lifted: 4 of 6 fills at or under the final cap 29",
        "curve chato card:uncommon (final after ~6 bids, opens 33): ladder 26→26 step 1 → 18→26 step 1",
        "dealer_final_lift 0.15: take a final up to 29 after 4 bids (our bids stay at or under 26)",
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
    assert patience_ladder((26, 26, 1), 6.0, 33.0, None) == (18, 26, 1)
    assert patience_ladder((12, 26, 2), 6.0, 33.0, None) == (12, 26, 1)  # already low: kept, step 1
    assert patience_ladder((26, 26, 1), 20.0, 33.0, None) == (14, 26, 1)  # 40 % of the opening 33
    assert patience_ladder((26, 26, 1), 20.0, None, 21) == (22, 26, 1)  # it ignored a first bid of 21
    assert patience_ladder((26, 26, 1), None, None, None) == (18, 26, 1)  # at least 9 bids
    assert patience_ladder((7, 7, 1), None, None, None) == (4, 7, 1)  # no curve: never below half our top (no 1 P)
    assert patience_ladder((26, 26, 1), 20.0, None, None, max_bids=12) == (15, 26, 1)  # the thread's tick limit


def test_without_a_curve_the_policy_patience_is_used():
    plan = plan_dealer_buy(chato_move(), skip_policy(), None, LIFT, SURPLUS)
    assert plan.move is not None and plan.move.ladder == (18, 26, 1)
    assert plan.changed_by[1].startswith("learning policy chato card:uncommon @t150 (final after ~6 bids)")


# ---------------------------------------------------------------- the strategy: a pricier dealer level too


def test_with_the_lift_the_strategy_also_offers_the_pricier_dealer_for_the_ladder():
    from bazaar_agent import strategy
    from tests.test_intel import opened, settle
    from tests.test_strategy import ABUELA, CATALOG, EVENTS, ME, PARAMS

    chato = {
        "id": "chato",
        "status": "active",
        "level": 2,
        "menu": {"sells": [{"rarity": "uncommon", "sets": "released", "list_price": 30}]},
    }
    fills = [
        opened(90, 253, "t03", {"buy": {"card": "LAT-06"}}, tick=8, dealer="chato"),
        settle(91, 9, "chato", "t03", "LAV-06", 28, tick=9, kind="card", persona="chato"),
    ]
    me = {**ME, "unlocked": ["abuela", "chato"]}

    def buys(rules):
        moves, _ = strategy.buy_moves(
            strategy.build_market(me, CATALOG, EVENTS + fills, [ABUELA, chato]), PARAMS, rules
        )
        return [(m.ref, m.source) for m in moves if m.ref == "LAV-08"], moves

    today, _ = buys(OFF)
    assert today == [("LAV-08", "abuela")]  # the cheapest dealer only, as today
    lifted, moves = buys(LIFT)
    assert lifted == [("LAV-08", "abuela"), ("LAV-08", "chato")]
    (alt,) = [m for m in moves if m.source == "chato" and m.ref == "LAV-08"]
    assert "level_ladder" in alt.strategy and alt.limit <= 26  # our bids still never pass the cap


def test_openings_never_open_two_threads_for_one_card_in_the_same_tick():
    from bazaar_agent.agents.desk import openings

    abuela = replace(chato_move(ladder=(18, 22, 1)), source="abuela", score=20.0)
    chato = replace(chato_move(), score=10.0)
    other = replace(chato_move(), ref="SAL-08", score=5.0)
    opened = openings([abuela, chato, other], set(), set(), 3)
    assert [(o.dealer, o.item) for o in opened] == [("abuela", "LAV-08"), ("chato", "SAL-08")]


def test_the_final_is_capped_by_what_we_may_still_commit_and_an_unaffordable_lift_is_skipped():
    rare = replace(chato_move(value=157.0, ladder=(80, 80, 1)), ref="LAV-10", rarity="rare", price=91.0)
    rares = CurveStats("chato", "card:rare", 15, (82, 89, 90, 91, 93), (97,) * 15, 4, 5.0, 1.5, None, (201, 219))
    plan = plan_dealer_buy(rare, None, rares, LIFT, SURPLUS, room=83)  # cash 353 - floor 270
    assert plan.move is None and plan.skip == "cash: what we may still commit is below chato card:rare fills ~91"
    assert plan_dealer_buy(rare, None, rares, LIFT, SURPLUS, room=120).final_max == 92
    uncommon = plan_dealer_buy(chato_move(), skip_policy(), CURVE, LIFT, SURPLUS, room=83)
    assert uncommon.final_max == 29
    tight = plan_dealer_buy(chato_move(), None, CURVE, LIFT, SURPLUS, room=27)  # his fills ~28: out of reach
    assert tight.move is None and tight.skip == "cash: what we may still commit is below chato card:uncommon fills ~28"
    # the reason never carries the room, so the taker's once-per-reason skip row is not repeated every tick
    assert plan_dealer_buy(chato_move(), None, CURVE, LIFT, SURPLUS, room=20).skip == tight.skip
    assert plan_dealer_buy(chato_move(), skip_policy(), CURVE, LIFT, SURPLUS, room=27).skip.startswith("skip: 0 of 6")
    assert plan_dealer_buy(chato_move(), None, CURVE, OFF, SURPLUS, room=0).move == chato_move()  # lift off: today


def test_no_price_history_means_no_lift_for_that_dealer():
    # security audit #158 P1-2: an unknown dealer (an L4 trickster) gets no final above the cap
    unknown = replace(chato_move(), source="trileros")
    plan = plan_dealer_buy(unknown, None, None, LIFT, SURPLUS, room=100)
    assert plan.move == unknown and plan.final_max is None and plan.notes == ()
    empty = CurveStats("trileros", "card:uncommon", 3, (), (40,), 0, None, None, None, (1, 2, 3))
    assert plan_dealer_buy(unknown, None, empty, LIFT, SURPLUS, room=100).final_max is None
