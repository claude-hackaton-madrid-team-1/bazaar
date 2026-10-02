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
    assert our_target(50, "seller", 0.0) == 80 and our_target(50, "seller", 1.0) == 52
    assert our_target(50, "buyer", 0.0) == 20 and our_target(50, "buyer", 1.0) == 48


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
    move = duel_move(duel(issues=["price", "days"]), tick=100, started_tick=100)
    assert (move.kind, move.days) == ("offer", 5)


def test_unreadable_or_done_duels_are_left_alone():
    assert duel_move({"done": True}, 1, 0).kind == "hold"
    assert duel_move({"role": "seller", "your_limit": None}, 1, 0).kind == "hold"
