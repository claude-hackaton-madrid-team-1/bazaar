"""N14a: a dealer's final offer is its limit; `final_max` lets us take it above our bid ceiling."""

import pytest

from bazaar_agent.agents.dealer import BidPlan, Move, Negotiation, decide
from bazaar_agent.agents.desk import Conversation, DeskMove, meet_the_ask, plan_conversation

TICK = 40


def neg(start=18, step=1, top=26, final_max=None, bids=()):
    # Chato opened at 33 (Friday's feed): a final below his opening may be taken (`Negotiation.may_take`)
    return Negotiation(BidPlan(start, step, top, final_max), list(bids), opening_ask=33, lowest_ask=33)


def test_without_final_max_a_final_above_the_top_walks_as_today():
    assert decide(neg(bids=[18, 19]), 29, 7, True).kind == "walk"
    assert decide(neg(bids=[18, 19]), 26, 7, True).kind == "accept"
    assert BidPlan(18, 1, 26).final_cap == 26


def test_a_final_up_to_final_max_is_taken_but_never_a_plain_ask_above_the_top():
    n = neg(final_max=29, bids=[18, 19, 20])
    assert decide(n, 29, 7, True) == Move("accept", 29, 7, "final within limit")
    assert decide(n, 30, 7, True).kind == "walk"
    assert decide(n, 29, 7, False) == Move("bid", 21, reason="small distinct step up")  # not final: keep bidding
    spent = neg(start=24, top=26, final_max=29, bids=[24, 25, 26])
    assert decide(spent, 28, 7, False).kind == "walk"  # no final yet and nothing left to bid: as today


def test_final_max_below_the_top_is_refused():
    with pytest.raises(ValueError):
        BidPlan(18, 1, 26, final_max=25)


def conv(final_max=None):
    plan = BidPlan(18, 1, 26, final_max)
    return Conversation(
        "chato", "LAV-08", "uncommon", 45, "r", Negotiation(plan, [18, 19], opening_ask=33, lowest_ask=33), 187, TICK
    )


def final_offer(price, ref="LAV-08"):
    return {
        "id": 9,
        "maker": "chato",
        "status": "open",
        "final": True,
        "give": {"types": [f"card:{ref}"]},
        "want": {"cash": price},
    }


def test_the_desk_accepts_a_final_inside_final_max():
    dm = plan_conversation(conv(29), {"status": "open", "standing_offers": [final_offer(29)]}, 14, TICK)
    assert (dm.move.kind, dm.move.price, dm.final) == ("accept", 29, True)
    assert (
        plan_conversation(conv(), {"status": "open", "standing_offers": [final_offer(29)]}, 14, TICK).move.kind
        == "walk"
    )


def test_meet_the_ask_meets_a_final_inside_final_max_but_no_plain_ask_above_the_top():
    c = conv(29)
    final = DeskMove(c, Move("accept", 29, 9, "final within limit"), 29, True, offer_id=9)
    assert meet_the_ask(final).move == Move("bid", 29, reason="accept slot used: meet her final")
    plain = DeskMove(c, Move("accept", 28, 9), 28, False, offer_id=9)
    assert meet_the_ask(plain).move.kind == "wait"
    assert meet_the_ask(DeskMove(conv(), Move("accept", 29, 9), 29, True, offer_id=9)).move.kind == "wait"


def test_a_lifted_final_needs_lift_after_bids_but_a_final_inside_the_top_never_does():
    early = Negotiation(BidPlan(18, 1, 26, 29, lift_after=4), [18], opening_ask=33, lowest_ask=33)
    assert decide(early, 29, 7, True).kind == "walk"
    assert "a lifted final needs 4" in decide(early, 29, 7, True).reason
    assert decide(early, 25, 7, True).kind == "accept"  # inside our top: as today
    late = Negotiation(BidPlan(18, 1, 26, 29, lift_after=4), [18, 19, 20, 21], opening_ask=33, lowest_ask=33)
    assert decide(late, 29, 7, True).kind == "accept"
    c = Conversation("chato", "LAV-08", "uncommon", 45, "r", early, 187, TICK)
    assert meet_the_ask(DeskMove(c, Move("accept", 29, 9), 29, True, offer_id=9)).move.kind == "wait"
