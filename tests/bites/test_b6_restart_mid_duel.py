"""B6: a restart or redeploy in the middle of a duel (any src/** merge redeploys bazaar-duels on Railway,
restartPolicyType ALWAYS restarts it after a crash).

cli.py keeps `first_seen` in memory and `duel_move(d, tick, first_seen[did])` reads the duel's progress from
it. After a restart at tick k, progress is 0 again: our next offer jumps back to the anchor (limit x 1.6 for
a seller), away from the rival and from what we already offered. The payload carries what is needed to
recover: `messages[*].tick` (our "you" messages and the rival's) and `your_offer.price`.
"""

import pytest

from bazaar_agent.agents.duelist import duel_move
from tests.bites.duelkit import FakeDuelServer, duel, patch_cli, run_duels

START, DEADLINE, RESTART = 120, 136, 130


def mid_duel():
    """Tick 130 of a 16-tick duel: we have been conceding (last offer 129 -> 128 P), the rival bids 110."""
    ours = [
        {"tick": t, "from": "you", "text": "x", "price": 160 - 3 * (t - START), "days": None}
        for t in range(START, RESTART)
    ]
    theirs = [
        {"tick": t, "from": "Rival Azul", "text": "y", "price": 100 + t - START, "days": None}
        for t in range(START, RESTART)
    ]
    messages = sorted(ours + theirs, key=lambda m: m["tick"])
    return duel(
        your_offer={"id": 1, "price": ours[-1]["price"], "tick": RESTART - 1, "days": 0},
        rival_offer={"id": 2, "price": 110, "tick": RESTART - 1, "days": 0},
        messages=messages,
        rounds=len(ours),
    )


def test_v1_after_a_restart_never_retreats_from_our_standing_offer():
    d = mid_duel()
    standing = d["your_offer"]["price"]
    move = duel_move(d, RESTART, RESTART)  # what cli.py computes on its first tick after the restart
    continuous = duel_move(d, RESTART, START)  # what it would have computed without the restart
    why = f"after restart: offer {move.price} > our standing {standing} (without restart: {continuous.price})"
    assert move.kind != "offer" or move.price <= standing, why


def test_duel_run_restart_end_to_end(monkeypatch, tmp_path):
    """Two `duel run` processes: ticks 120-129, then a fresh process from tick 130 (a redeploy)."""
    rival = {"id": 2, "price": 103, "tick": START, "days": 0}
    server = FakeDuelServer([duel(rival_offer=rival)], range(START, RESTART))
    cli = patch_cli(monkeypatch, tmp_path, server)
    run_duels(cli, RESTART - START)
    before = [p for t, _, p, _ in server.said if t == RESTART - 1]
    server.ticks, server.i = list(range(RESTART, RESTART + 2)), 0
    run_duels(cli, 2)
    after = [p for t, _, p, _ in server.said if t == RESTART]
    assert before and after and after[0] <= before[0], f"offer at 129: {before}, after the restart at 130: {after}"


def _v2_rival(d, tick):
    """A rival that opens at 100 on the start tick and concedes 3 P a tick up to 110, then stalls."""
    if d["rival_offer"] is None or d["rival_offer"]["tick"] < tick:
        price = min(110, 100 + 3 * (tick - START))
        if d["rival_offer"] is None or d["rival_offer"]["price"] != price:
            d["rival_offer"] = {"id": 700 + tick, "price": price, "tick": tick, "days": 0}
            d["messages"].append(
                {"tick": tick, "from": "Rival Azul", "text": f"{price} P", "price": price, "days": None}
            )


@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_duel_run_restart_changes_nothing(monkeypatch, tmp_path, policy):
    """The same duel played by one process (ticks 120-133) and by two (120-129, redeploy, 130-133): every
    message and accept after the restart must be the same. v2 only on #86."""
    rules = None
    if policy == "v2":
        pytest.importorskip("bazaar_agent.agents.duel_v2")
        from bazaar_agent.guardrails import Guardrails

        rules = Guardrails(duel_policy="v2")

    def play(split):
        server = FakeDuelServer([duel(rival_offer=None, messages=[])], range(START, split), rival=_v2_rival)
        cli = patch_cli(monkeypatch, tmp_path / f"{policy}-{split}", server, rules=rules)
        run_duels(cli, split - START)
        if split < 134:
            server.ticks, server.i = list(range(split, 134)), 0
            run_duels(cli, 134 - split)
        return [x for x in server.said if x[0] >= RESTART], [x for x in server.accept_log if x[0] >= RESTART]

    assert play(RESTART) == play(134), f"{policy}: restart at {RESTART} changed our moves"
