"""B1: in a two-issue duel (price + days), our own offer must stay strictly inside our limit after the
worst-case cost of its days. If the rival accepts our offer, the deal is OUR price and OUR days.

Duels II (Sat h13) and III (Sun) negotiate days 0-10 with a private `your_days_weight` whose sign is
unverified on the real server, so a day is valued at |weight| against us.
"""

import pytest

from bazaar_agent.agents.duel_jev import legal_moves
from bazaar_agent.agents.duelist import duel_move
from tests.bites.duelkit import duel, strictly_inside, worst_case

START, DEADLINE = 120, 136  # Duels II: 16 ticks


def two_issue(role, limit, weight, **kw):
    return duel(
        role=role,
        your_limit=limit,
        issues=["price", "days"],
        your_days_weight=weight,
        days_meaning=None,
        deadline_tick=DEADLINE,
        **kw,
    )


@pytest.mark.parametrize("role", ["seller", "buyer"])
@pytest.mark.parametrize("weight", [-4.0, 4.0, -1.5])
@pytest.mark.parametrize("tick", [START, 128, DEADLINE - 3, DEADLINE - 1])
def test_our_two_issue_offer_is_inside_the_limit_after_its_days(role, weight, tick):
    d = two_issue(role, 50, weight)
    move = duel_move(d, tick, START)
    if move.kind != "offer":
        return
    assert move.days is not None, "a priced two-issue message without days is refused missing_days"
    value = worst_case(d, move.price, move.days)
    assert value is not None and strictly_inside(value, 50, role), (
        f"{role} offers price {move.price} days {move.days}: worth {value} at |w|={abs(weight)}/day, "
        f"outside our limit 50 if the rival accepts"
    )


def test_jev_counter_is_legal_only_inside_the_limit_after_days():
    """The Jev path (production runs with Jev on): `legal_moves` must not offer Jev a counter whose days
    take it outside our limit."""
    d = two_issue("seller", 50, -4.0)
    tick = DEADLINE - 3
    default = duel_move(d, tick, START)
    counter = duel_move({**d, "rival_offer": None}, tick, START, endgame_ticks=0)
    legal = legal_moves(d, tick, default, counter, 2)
    if "counter" in legal:
        c = legal["counter"]
        value = worst_case(d, c.price, c.days)
        why = f"Jev may pick counter {c.price} days {c.days} worth {value} < limit 50"
        assert value is not None and strictly_inside(value, 50, "seller"), why


def test_duel_run_never_sends_a_two_issue_offer_outside_the_limit(monkeypatch, tmp_path):
    """End to end through `bazaar duel run --play` (fake server): what actually goes on the wire."""
    from tests.bites.duelkit import FakeDuelServer, patch_cli, run_duels

    d = two_issue("seller", 50, -4.0, rival_offer={"id": 1, "price": 49, "tick": START, "days": 0})
    server = FakeDuelServer([d], range(START, DEADLINE))
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, DEADLINE - START)
    bad = [
        (t, p, days, worst_case(d, p, days))
        for t, _, p, days in server.said
        if p is not None and not strictly_inside(worst_case(d, p, days) or 0, 50, "seller")
    ]
    assert server.said and not bad, f"offers sent outside limit 50 after days (tick, price, days, worth): {bad}"
