"""r1 proof tests for PR #113 @ 784a224 (copy into tests/ on that branch; all 7 FAIL there = bugs confirmed)."""
import json
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents import duel_days as dd
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import DuelClient, duel_cli  # noqa: F401


class DoneClient(DuelClient):
    def __init__(self, duels, done):
        super().__init__(duels)
        self.done_payload, self.done_calls = done, 0

    def clock(self):
        return {"tick": 130, "next_tick_in": 40.0, "tick_seconds": 60.0, "t_hours": 2.2}

    def duels(self, done=False):
        if done:
            self.done_calls += 1
            return {"duels": deepcopy(self.done_payload)}
        return {"duels": deepcopy(self.payload)}


def scored_cost_deal():
    """A finished real two-issue deal where the game scored the days as a COST (weight +2, 5 days)."""
    return {**LIVE, "duel": 900, "status": "deal", "issues": ["price", "days"], "role": "seller",
            "your_limit": 100, "your_days_weight": 2.0, "price": 120, "days": 5, "rounds": 0,
            "decay_per_round": 0.08, "result": 10.0, "days_meaning": None}  # 120-100 - 2*5 = 10: cost


def test_auto_off_still_adds_a_done_GET_on_the_duel_tick(duel_cli, monkeypatch):  # noqa: F811
    cli, _, _, tmp_path = duel_cli
    client = DoneClient([{**LIVE}], [])
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    assert cli._rules().rules.duel_days_auto is False  # the shipped default
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1", "--no-jev"])
    assert result.exit_code == 0, result.output
    assert client.done_calls == 0, "duel_days_auto=false must not add /api/duels?done=true to the duel tick"


def test_a_text_latch_is_never_cross_checked_against_a_scored_deal(duel_cli, monkeypatch):  # noqa: F811
    assert dd.scored_evidence(scored_cost_deal(), True) == "cost"
    cli, _, _, tmp_path = duel_cli
    (tmp_path / "duels").mkdir(parents=True, exist_ok=True)
    (tmp_path / "duels" / "days_sign.json").write_text(json.dumps({"verdict": "signed", "duel": 1, "text": "gain (+)"}))
    client = DoneClient([{**LIVE}], [scored_cost_deal()])
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1", "--no-jev"])
    assert dd.latch(tmp_path).verdict == "conflict", "text says signed, the game scored a cost: must be conflict"


@pytest.mark.parametrize("text", [
    "what the other side gains (+) per delivery day",
    "primas per delivery day; positive = the buyer gains",
    "primas per day: gain (+) for the seller, loss for the buyer",
    "days count against (+) you",
    "primas you do not gain (+) per delivery day: each day costs you",
])
def test_texts_that_do_not_say_our_weight_is_a_gain_must_not_latch_signed(text):
    assert dd.evidence({"issues": ["price", "days"], "days_meaning": text}, True) != "signed"
