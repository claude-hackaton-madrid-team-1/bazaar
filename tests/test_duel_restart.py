"""A redeploy restarts `duel run` mid-tick: its FIRST tick must act on the live duels at once.

No warm-up, no waiting for the next tick boundary (`run_per_tick` handles the tick it reads first), and no memory of
the previous process (a fresh data dir). The sleep is made to fail: any wait before the first move breaks the test.
"""

from __future__ import annotations

from functools import partial

import pytest
from typer.testing import CliRunner

from bazaar_agent import ticks
from tests.test_bluff_wiring import duel_cli  # noqa: F401 - the shared `duel run` fixture (fakes, no network)
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import use_policy


@pytest.fixture
def no_wait(monkeypatch):
    """`duel run`'s tick loop with a sleep that fails: `run_per_tick` binds `time.sleep` as a default argument, so
    the loop itself is swapped for one carrying this sleep."""
    from bazaar_agent import cli

    def refuse(seconds):
        raise AssertionError(f"duel run waited {seconds:.1f} s before its first move")

    monkeypatch.setattr(cli, "run_per_tick", partial(ticks.run_per_tick, sleep=refuse))


@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_a_restarted_duel_runner_accepts_on_its_first_tick(duel_cli, no_wait, monkeypatch, policy):  # noqa: F811
    cli, client, tmp_path = duel_cli
    use_policy(monkeypatch, cli, policy)
    # The fake clock is tick 134 with 40 s left; the deadline is next tick and the rival is inside our limit.
    client.payload = [{**LIVE, "deadline_tick": 135, "rival_offer": {"id": 703, "price": 120, "tick": 133, "days": 0}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)], result.output
    assert "tick 134: 1 live duel(s) logged" in result.output


def test_a_restarted_duel_runner_offers_on_its_first_tick(duel_cli, no_wait):  # noqa: F811
    cli, client, tmp_path = duel_cli
    # Far from the deadline, the rival below our limit: the first tick still sends our offer (no warm-up tick).
    client.payload = [{**LIVE, "deadline_tick": 160, "rival_offer": {"id": 704, "price": 90, "tick": 133, "days": 0}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert [s[:2] for s in client.sent] == [("say", 95)], result.output
