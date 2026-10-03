"""B2: payload shapes the real server could send that the practice payloads did not show.

Two kinds of assertion per shape:
  * SAFETY (test_safety_*): `duel_move` never raises and never offers/accepts outside our worst-case limit.
    A failure here is FAIL-COSTLY (points lost or a crashed tick).
  * LIVENESS (test_liveness_*): when a move strictly inside our limit exists, the player makes one.
    A failure here is FAIL-SAFE but scores 0 for that duel (holds forever).
"""

import pytest

from bazaar_agent.agents.duelist import duel_move
from tests.bites.duelkit import FakeDuelServer, duel, patch_cli, run_duels, strictly_inside, worst_case

START, DEADLINE = 120, 136
ENDGAME = DEADLINE - 2  # v1 accepts anything strictly inside the limit here
RIVAL_IN = {"id": 1, "price": 104, "tick": 133, "days": 0}  # strictly inside a seller's limit of 100

SHAPES = {
    "limit_float": duel(your_limit=100.0, rival_offer=RIVAL_IN),
    "limit_string": duel(your_limit="100", rival_offer=RIVAL_IN),
    "deadline_missing": {k: v for k, v in duel(rival_offer=RIVAL_IN).items() if k != "deadline_tick"},
    "status_paused": duel(status="paused", rival_offer=RIVAL_IN),
    "status_settling": duel(status="settling", rival_offer=RIVAL_IN),
    "role_capitalised": duel(role="Seller", rival_offer=RIVAL_IN),
    # A two-issue session whose payload lost `issues`: the rival asks for 10 days, our weight is 3/day.
    "issues_missing_rival_days": {
        k: v for k, v in duel(your_days_weight=-3.0, rival_offer={**RIVAL_IN, "days": 10}).items() if k != "issues"
    },
    "two_issue_weight_null": duel(issues=["price", "days"], your_days_weight=None, rival_offer=RIVAL_IN),
    "two_issue_rival_days_null": duel(
        issues=["price", "days"], your_days_weight=-2.0, rival_offer={**RIVAL_IN, "days": None}
    ),
    "issues_not_a_list": duel(issues=2, rival_offer=RIVAL_IN),
}


def _limit(d):
    try:
        return float(d["your_limit"])
    except (TypeError, ValueError):
        return 100.0


@pytest.mark.parametrize("tick", [START + 2, ENDGAME])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_safety_never_raises_and_never_crosses_the_limit(shape, tick):
    d = SHAPES[shape]
    move = duel_move(d, tick, START)  # raising here kills the WHOLE tick for every duel (no per-duel try)
    role = "buyer" if str(d.get("role")).lower() == "buyer" else "seller"
    if move.kind == "accept":
        days = (d.get("rival_offer") or {}).get("days")
        value = worst_case(d, move.price, days)
        why = f"{shape}: accepts {move.price} with {days} days, worth {value} vs limit {_limit(d)}"
        assert value is not None and strictly_inside(value, _limit(d), role), why
    if move.kind == "offer":
        value = worst_case(d, move.price, move.days)
        why = f"{shape}: offers {move.price} with {move.days} days (unvalued or outside the limit)"
        assert value is not None and strictly_inside(value, _limit(d), role), why
        if isinstance(d.get("your_days_weight"), int | float):
            assert move.days is not None, f"{shape}: the session has days, a price without days is missing_days"


@pytest.mark.parametrize(
    "shape",
    ["limit_float", "deadline_missing", "two_issue_weight_null"],
)
def test_liveness_an_inside_limit_move_is_made(shape):
    """At the endgame (or, without a deadline, long after the start) with the rival strictly inside our
    limit, accepting is the move. For `two_issue_weight_null` an offer at days 0 is also fine: 0 days cost
    nothing under either sign of the weight."""
    d = SHAPES[shape]
    tick = ENDGAME if "deadline_tick" in d else START + 40
    move = duel_move(d, tick, START)
    ok_offer = move.kind == "offer" and move.days == 0 and shape == "two_issue_weight_null"
    assert move.kind == "accept" or ok_offer, f"{shape}: {move} (the duel scores 0)"


def test_duel_run_and_its_guardrail_never_accept_unvalued_days(monkeypatch, tmp_path):
    """End to end (on #60/#86 also through guardrails.duel_inside_limit): `issues` missing, rival asks 10 days."""
    d = SHAPES["issues_missing_rival_days"]
    server = FakeDuelServer([d], [ENDGAME])
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, 1)
    assert not server.accept_log, f"accepted 104 P with 10 days at |w|=3: worth 74 < limit 100 ({server.accept_log})"


def test_one_malformed_duel_does_not_cost_the_others_their_move(monkeypatch, tmp_path):
    """Blast radius: a row that makes `duel_move` raise must not stop the endgame accept of a healthy duel."""
    bad = duel(duel=1, issues=2, rival_offer=RIVAL_IN)
    good = duel(duel=2, rival_offer=RIVAL_IN)
    server = FakeDuelServer([bad, good], [ENDGAME])
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, 1)
    assert (ENDGAME, 2) in server.accept_log, f"healthy duel 2 lost its endgame accept: {server.accept_log}"
