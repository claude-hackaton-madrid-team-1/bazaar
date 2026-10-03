"""B4: the endgame accept is tick-relative (`deadline_tick - tick <= duel_endgame_ticks` = 2). If the loop
misses ticks (an on_tick that overran: Jev 3 s + words + a 10 s Postgres connect on a 15 s Sunday tick; a
redeploy; a 429 on /api/duels), the endgame window is skipped and an inside-limit rival offer is lost.

`run_per_tick` handles only the tick it reads: a tick that passed while we worked is never played.
UNVERIFIED on the real server: whether an accept on D-1 settles (the simulator settles it on D).
"""

import json

import pytest

from tests.bites.duelkit import FakeDuelServer, duel, patch_cli, run_duels

DEADLINE = 136
RIVAL_IN = {"id": 1, "price": 102, "tick": 125, "days": 0}  # inside a seller's limit of 100, short of target


@pytest.mark.parametrize(
    "ticks,last_settle,label",
    [
        ([*range(120, 134), 136], DEADLINE - 1, "two ticks skipped (D-2, D-1), D-1 settles as in the sim"),
        ([*range(120, 134), 135], DEADLINE - 2, "one tick skipped (D-2), D-1 does NOT settle (unverified)"),
    ],
)
@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_an_inside_limit_offer_is_taken_despite_skipped_ticks(monkeypatch, tmp_path, ticks, last_settle, label, policy):
    rules = None
    if policy == "v2":  # #86 only
        pytest.importorskip("bazaar_agent.agents.duel_v2")
        from bazaar_agent.guardrails import Guardrails

        rules = Guardrails(duel_policy="v2")
    msg = {"tick": 125, "from": "Rival Azul", "text": "102 P.", "price": 102, "days": None}
    d = duel(rival_offer=RIVAL_IN, messages=[msg])
    server = FakeDuelServer([d], ticks, last_settle=lambda d: last_settle)
    cli = patch_cli(monkeypatch, tmp_path, server, rules=rules)
    run_duels(cli, len(ticks))
    log = [json.loads(x) for x in (tmp_path / "duels" / "duels.jsonl").read_text().splitlines()]
    reasons = [(r["tick"], r["move"]["kind"], r["move"]["reason"]) for r in log if "move" in r][-2:]
    assert server.deals(), f"{policy} {label}: no deal; accepts {server.accept_log}, last moves {reasons}"
