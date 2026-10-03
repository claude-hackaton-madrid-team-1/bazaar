import pytest

from bazaar_agent.agents.duelist import duel_move, inside_limit, our_target


def duel(role="seller", limit=50, rival=None, deadline=112, **kw):
    return {
        "id": 1,
        "role": role,
        "your_limit": limit,
        "deadline": deadline,
        "done": False,
        "rival_offer": {"price": rival} if rival is not None else None,
        **kw,
    }


def test_targets_start_at_the_anchor_and_end_near_the_limit_on_our_side():
    assert our_target(50, "seller", 0.0) == 80 and our_target(50, "seller", 1.0) == 53  # 52.5 rounds up
    assert our_target(50, "buyer", 0.0) == 20 and our_target(50, "buyer", 1.0) == 47  # 47.5 rounds down


def test_never_accepts_outside_the_limit_even_at_the_deadline():
    assert duel_move(duel(rival=49), tick=111, started_tick=100).kind == "offer"
    assert duel_move(duel(role="buyer", rival=51), tick=111, started_tick=100).kind == "offer"
    assert not inside_limit(50, 50, "seller")


def test_accepts_a_rival_offer_that_meets_our_target():
    move = duel_move(duel(rival=85), tick=100, started_tick=100)
    assert (move.kind, move.price) == ("accept", 85)


def test_endgame_takes_any_deal_strictly_inside_the_limit():
    assert duel_move(duel(rival=51), tick=110, started_tick=100).kind == "accept"
    assert duel_move(duel(rival=51), tick=104, started_tick=100).kind == "offer"


def test_two_issue_sessions_always_carry_days():
    move = duel_move(duel(issues=["price", "days"], your_days_weight=2.0), tick=100, started_tick=100)
    assert (move.kind, move.price, move.days) == ("offer", 80, 0)  # 0 days cost nothing, whatever the weight's sign


def test_unreadable_or_done_duels_are_left_alone():
    assert duel_move({"done": True}, 1, 0).kind == "hold"
    assert duel_move({"role": "seller", "your_limit": None}, 1, 0).kind == "hold"


def test_two_issue_accepts_price_in_the_worst_case_cost_of_days():
    from bazaar_agent.agents.duelist import effective_price

    d = duel(rival=None, issues=["price", "days"], your_days_weight=2.0)
    d["rival_offer"] = {"price": 56, "days": 4}  # 56 looks inside a 50 cost, but 4 days may cost us 8
    assert effective_price(d, 56) == 48
    assert duel_move(d, tick=111, started_tick=100).kind == "offer"
    d["rival_offer"] = {"price": 56}  # days missing: cannot value it, never accept
    assert effective_price(d, 56) is None and duel_move(d, tick=111, started_tick=100).kind == "offer"


def test_days_outside_the_rules_range_cannot_be_valued():
    from bazaar_agent.agents.duelist import effective_price

    d = duel(issues=["price", "days"], your_days_weight=2.0)
    for days in (-5, 11, float("nan"), True):  # RULES.md: 0 to 10. -5 would turn 45 into "55" for a cost of 50
        d["rival_offer"] = {"price": 45, "days": days}
        assert effective_price(d, 45) is None, days
        assert duel_move(d, tick=111, started_tick=100).kind != "accept", days
    d["rival_offer"] = {"price": 75, "days": 10}
    assert effective_price(d, 75) == 55


# The real shape of GET /api/duels (practice session, tick 134, 2026-10-02).
LIVE = {
    "duel": 95,
    "session": 1,
    "status": "live",
    "role": "seller",
    "item": "Mercado de Vallehermoso",
    "issues": ["price"],
    "your_days_weight": None,
    "your_limit": 104,
    "rival": "Rival Noche",
    "deadline_tick": 144,
    "decay_per_round": 0.06,
    "rounds": 0,
    "your_offer": None,
    "rival_offer": {"id": 701, "price": 98, "tick": 132, "days": 0},
    "result": None,
}


def test_the_live_payload_is_read_and_played():
    from bazaar_agent.agents.duelist import duel_deadline, duel_done, duel_id

    assert (duel_id(LIVE), duel_deadline(LIVE), duel_done(LIVE)) == (95, 144, False)
    move = duel_move(LIVE, tick=134, started_tick=132)
    assert move.kind == "offer" and move.price > 104  # 98 is below our cost: never accepted
    assert duel_move({**LIVE, "rival_offer": {"price": 110, "days": 0}}, tick=143, started_tick=132).kind == "accept"
    assert duel_done({**LIVE, "status": "done"}) and duel_done({**LIVE, "result": "deal"})
    assert duel_id({"id": 7}) == 7 and duel_id({"duel": True}) is None


# ---------------------------------------------------------------- our own offers stay strictly inside the limit


def two_issue(role="seller", limit=100, weight=2.0, **kw):
    return duel(role=role, limit=limit, issues=["price", "days"], your_days_weight=weight, **kw)


def our_worth(d, move):
    """Our offer at the worst-case cost of its days: what the rival's acceptance would be worth to us."""
    penalty = abs(d["your_days_weight"]) * move.days
    return move.price - penalty if d["role"] == "seller" else move.price + penalty


def test_a_two_issue_offer_counts_its_days_against_our_limit():
    # Before the fix: seller cost 100, weight 2, last tick → 105 with 5 days, worth 95 (outside the limit).
    d = two_issue(weight=2.0)
    move = duel_move(d, tick=111, started_tick=100)
    assert move.kind == "offer" and our_worth(d, move) > 100
    b = two_issue(role="buyer", limit=60, weight=2.0)
    move = duel_move(b, tick=111, started_tick=100)
    assert move.kind == "offer" and our_worth(b, move) < 60  # before: 54 + 5 days cost 64


@pytest.mark.parametrize("role", ["seller", "buyer"])
@pytest.mark.parametrize("weight", [2.0, -2.0, 0.5, -9.0, 0.0])
@pytest.mark.parametrize("limit", [1, 2, 7, 10, 19, 60, 100, 104])
def test_no_offer_is_ever_outside_or_on_the_limit(role, weight, limit):
    d = two_issue(role=role, limit=limit, weight=weight)
    for tick in range(100, 113):
        move = duel_move(d, tick=tick, started_tick=100)
        assert move.kind in ("offer", "hold")
        if move.kind == "offer":
            assert move.days is not None and 0 <= move.days <= 10
            assert inside_limit(our_worth(d, move), limit, role), (tick, move)


@pytest.mark.parametrize("limit", [1, 2, 7, 10, 19, 60, 100, 104])
def test_price_only_offers_never_land_on_the_limit(limit):
    for role in ("seller", "buyer"):
        for tick in range(100, 113):
            move = duel_move(duel(role=role, limit=limit), tick=tick, started_tick=100)
            assert move.kind == "hold" or inside_limit(move.price, limit, role), (role, tick, move)


def test_rounding_goes_toward_our_side_of_the_limit():
    assert our_target(10, "buyer", 1.0) == 9  # 9.5 used to round to 10: zero surplus
    assert our_target(10, "seller", 1.0) == 11
    assert our_target(100, "seller", 1.0) == 105 and our_target(60, "buyer", 1.0) == 57  # float noise
    assert duel_move(duel(role="buyer", limit=1), tick=111, started_tick=100).kind == "hold"


def test_a_two_issue_duel_without_our_days_weight_holds():
    assert duel_move(two_issue(weight=None), tick=105, started_tick=100).kind == "hold"
    assert duel_move(two_issue(weight=None, rival=500), tick=111, started_tick=100).kind == "hold"


def test_the_guardrail_action_carries_the_terms_we_would_agree_to():
    from bazaar_agent.agents.duelist import DuelMove, duel_action

    d = two_issue(weight=-3.0)
    d["rival_offer"] = {"price": 130, "days": 4}
    accept = duel_action(d, DuelMove("accept", 130))
    assert (accept.kind, accept.price, accept.days, accept.days_weight) == ("duel_accept", 130, 4, -3.0)
    assert (accept.limit, accept.role, accept.item) == (100, "seller", "1")
    offer = duel_action(d, DuelMove("offer", 150, 2))
    assert (offer.kind, offer.price, offer.days) == ("duel_offer", 150, 2)
    d["rival_offer"] = {"price": 130}  # days unreadable: nothing the guardrail can value
    assert duel_action(d, DuelMove("accept", 130)).price is None
    price_only = duel_action(duel(rival=60), DuelMove("accept", 60))
    assert (price_only.price, price_only.days, price_only.limit) == (60, None, 50)


def test_the_endgame_takes_a_worst_case_surplus_below_half_a_prima():
    # #60 review 1: round(100.4) == 100 refused a deal strictly inside our cost of 100 on the last ticks.
    d = two_issue(weight=0.6, rival=101)
    d["rival_offer"]["days"] = 1  # 101 - 0.6 × 1 = 100.4: worth 0.4 above our cost
    move = duel_move(d, tick=111, started_tick=100)
    assert (move.kind, move.price) == ("accept", 101)
    b = two_issue(role="buyer", limit=60, weight=-0.6, rival=59)
    b["rival_offer"]["days"] = 1  # 59 + 0.6 = 59.6 < 60
    assert duel_move(b, tick=111, started_tick=100).kind == "accept"


# ---------------------------------------------------------------- an accept agrees to the rival's standing offer


@pytest.mark.parametrize(
    ("role", "limit", "price", "move_price"),
    [("buyer", 50, True, 1), ("seller", 100, 101.7, 101), ("buyer", 102, 101.7, 101), ("seller", 50, float("nan"), 0)],
)
def test_a_bool_or_fractional_rival_price_is_never_accepted(role, limit, price, move_price):
    # True is an int in Python and int(101.7) is 101: neither is a price the rival offered, so neither is valued.
    from bazaar_agent.agents.duelist import DuelMove, duel_action

    d = duel(role=role, limit=limit, rival=price)
    assert duel_move(d, tick=111, started_tick=100).kind != "accept"
    assert duel_action(d, DuelMove("accept", move_price)).price is None


def test_an_accept_is_valued_at_the_rivals_standing_offer_not_at_the_move():
    from bazaar_agent.agents.duelist import DuelMove, duel_action

    assert duel_action(duel(rival=60), DuelMove("accept", 999)).price is None  # the move and the offer disagree
    assert duel_action(duel(rival=60), DuelMove("accept", 60)).price == 60
    assert duel_action(duel(rival=60.0), DuelMove("accept", 60)).price == 60  # an integral float is that integer
    assert duel_action(duel(), DuelMove("accept", 60)).price is None  # no standing offer: nothing to agree to
    d = two_issue(weight=-3.0)
    d["rival_offer"] = {"price": 130, "days": 4}
    assert duel_action(d, DuelMove("accept", 131)).price is None


def test_the_guard_denies_an_accept_whose_terms_are_not_the_rivals_offer():
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.duelist import DuelMove, duel_action

    rules = gr.load_guardrails().rules
    ctx = gr.Context(cash=0, held={}, tick=1, t_hours=0.0, accepts_this_tick=0, paused=False)
    assert not gr.check(duel_action(duel(rival=60), DuelMove("accept", 999)), ctx, rules).allowed
    assert not gr.check(duel_action(duel(role="buyer", rival=True), DuelMove("accept", 1)), ctx, rules).allowed
    assert gr.check(duel_action(duel(rival=60), DuelMove("accept", 60)), ctx, rules).allowed
    d = two_issue(weight=-3.0)
    d["rival_offer"] = {"price": 130, "days": 4}  # 130 − 3 × 4 = 118 > our cost of 100
    assert gr.check(duel_action(d, DuelMove("accept", 130)), ctx, rules).allowed
    assert not gr.check(duel_action(d, DuelMove("accept", 999)), ctx, rules).allowed


def test_our_duel_messages_counts_our_own_lines_only():
    from bazaar_agent.agents.duelist import our_duel_messages

    duel = {"messages": [{"from": "you"}, {"from": "Rival Plata"}, {"from": "you"}, "odd"]}
    assert our_duel_messages(duel) == 2 and our_duel_messages({}) == 0 and our_duel_messages({"messages": "x"}) == 0
