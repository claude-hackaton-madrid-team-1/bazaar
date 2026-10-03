"""`duel run` restarted mid-duel (every merge to main redeploys it), against a rival that has not priced.

Live, Sat 3 Oct (duel session 2): after each redeploy the runner restarted its clock at our first offer, not at
our first sight of the duel, and stepped back on its own offers (a seller's ask up, a buyer's bid down: 9 times,
one per restart); a restart inside a tick we had already offered in also sent a second message, which the game
refused (`wait_for_tick`, 8 times in the logs). Synthetic limits and prices, no network.
"""

from __future__ import annotations

from typer.testing import CliRunner

from bazaar_agent.agents.duel_v2 import V2Params, first_offer_wait, plan_moves
from tests.test_bluff_wiring import duel_cli  # noqa: F401 - the shared `duel run` fixture (fakes, no network)
from tests.test_jev_journal import use_policy

TICK = 134  # the fake clock's tick


def silent(ours: list[tuple[int, int]], seen: int, role: str = "seller") -> dict:
    """A live duel the rival has not priced, our offers at (tick, price), first seen at `seen`."""
    return {
        "duel": 41,
        "session": 2,
        "status": "live",
        "role": role,
        "item": "Mercado de Prueba",
        "issues": ["price"],
        "your_days_weight": None,
        "days_meaning": None,
        "your_limit": 100,
        "rival": "Rival Prueba",
        "deadline_tick": seen + 16,
        "decay_per_round": 0.06,
        "rounds": 0,
        "your_offer": {"id": 9, "price": ours[-1][1], "tick": ours[-1][0], "days": 0} if ours else None,
        "rival_offer": None,
        "messages": [{"tick": t, "from": "you", "text": "x", "price": p, "days": None} for t, p in ours],
        "result": None,
        "price": None,
        "days": None,
    }


def history(seen: int, upto: int, params: V2Params) -> list[tuple[int, int]]:
    """Our offers from a runner that never restarted, from `seen` to before `upto`."""
    ours: list[tuple[int, int]] = []
    for tick in range(seen, upto):
        move = plan_moves([silent(ours, seen)], tick, {41: seen}, params)[41]
        if move.kind == "offer" and move.price is not None:
            ours.append((tick, move.price))
    return ours


def run_once(cli, monkeypatch):
    use_policy(monkeypatch, cli, "v2")
    return CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])


def test_a_restarted_runner_plays_on_from_its_last_offer(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _ = duel_cli
    params = V2Params.from_rules(cli._rules().rules)
    seen = TICK - 9
    ours = history(seen, TICK, params)
    assert ours[0][0] == seen + first_offer_wait(params)  # it waited before opening: the trap
    expected = history(seen, TICK + 1, params)[-1]
    client.payload = [silent(ours, seen)]
    result = run_once(cli, monkeypatch)
    assert result.exit_code == 0, result.output
    ((kind, did, price, *_),) = client.sent
    assert (kind, did, price) == ("say", 41, expected[1]) and expected[0] == TICK
    assert price < ours[-1][1]  # a seller only comes down


def test_a_runner_restarted_inside_a_tick_we_already_offered_in_sends_nothing(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _ = duel_cli
    params = V2Params.from_rules(cli._rules().rules)
    seen = TICK - 9
    ours = history(seen, TICK + 1, params)
    assert ours[-1][0] == TICK  # the process we replaced already offered in this tick
    client.payload = [silent(ours, seen)]
    result = run_once(cli, monkeypatch)
    assert result.exit_code == 0, result.output
    assert client.sent == []
    assert "we already offered" in result.output  # (Rich wraps the reason)
