"""B3: duels (and the taker) share ONE accept per tick (`max_accepts_per_tick` = 1, ledger.reserve_accept).

Duels II (Sat h13): up to 6 concurrent duels, 16 ticks, all scheduled together, so they share a deadline.
When several rivals sit strictly inside our limit but short of our target, v1 waits for its endgame
(`left <= duel_endgame_ticks` = 2): two accept ticks for six duels. UNVERIFIED: whether the real server
counts duel accepts against `accepts_per_team_per_tick` (the simulator does not); the guardrail does.
"""

import pytest

from tests.bites.duelkit import FakeDuelServer, duel, patch_cli, run_duels

START, DEADLINE = 120, 136


def six_duels():
    out = []
    for i in range(6):
        role = "seller" if i % 2 == 0 else "buyer"
        price = 102 + i // 2 if role == "seller" else 98 - i // 2  # inside limit 100, short of the 5 % floor
        rival = {"id": 700 + i, "price": price, "tick": START, "days": 0}
        msg = {"tick": START, "from": "Rival Azul", "text": f"{price} P.", "price": price, "days": None}
        out.append(duel(duel=601 + i, role=role, rival="Rival Azul", rival_offer=rival, messages=[msg]))
    return out


def test_six_duels_sharing_a_deadline_all_close(monkeypatch, tmp_path):
    server = FakeDuelServer(six_duels(), range(START, DEADLINE))
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, DEADLINE - START)
    why = f"only {len(server.deals())}/6 deals; accepts sent (tick, duel): {server.accept_log}"
    assert len(server.deals()) == 6, why


def test_endgame_slot_goes_to_the_most_valuable_duel(monkeypatch, tmp_path):
    """Last accept tick, two duels inside the limit: 1 P of surplus (duel 1) vs 4 P (duel 2)."""
    small = duel(duel=1, rival_offer={"id": 1, "price": 101, "tick": 134, "days": 0})
    big = duel(duel=2, rival_offer={"id": 2, "price": 104, "tick": 134, "days": 0})
    server = FakeDuelServer([small, big], [DEADLINE - 1])
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, 1)
    assert server.accept_log == [(DEADLINE - 1, 2)], f"accepted {server.accept_log}: the 4 P duel lost the slot"


def test_a_duel_that_loses_the_slot_still_sends_its_offer(monkeypatch, tmp_path):
    """cli.py: a refused `reserve_accept` `continue`s: that duel says nothing at all that tick."""
    a = duel(duel=1, rival_offer={"id": 1, "price": 102, "tick": 133, "days": 0})
    b = duel(duel=2, rival_offer={"id": 2, "price": 103, "tick": 133, "days": 0})
    server = FakeDuelServer([a, b], [DEADLINE - 2])
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, 1)
    moved = {did for _, did in server.accept_log} | {did for _, did, _, _ in server.said}
    assert moved == {1, 2}, f"a duel made no move this tick: accepts {server.accept_log}, says {server.said}"


def test_v2_planner_closes_all_six(monkeypatch, tmp_path):
    """#86 only: duel_policy = v2 plans the team's accepts across duels (earliest deadline, slowest rival)."""
    pytest.importorskip("bazaar_agent.agents.duel_v2")
    from bazaar_agent.guardrails import Guardrails

    server = FakeDuelServer(six_duels(), range(START, DEADLINE))
    cli = patch_cli(monkeypatch, tmp_path, server, rules=Guardrails(duel_policy="v2"))
    run_duels(cli, DEADLINE - START)
    assert len(server.deals()) == 6, f"v2: {len(server.deals())}/6 deals; accepts {server.accept_log}"
