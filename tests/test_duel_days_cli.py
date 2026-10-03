"""r1's proofs against #113 (_night/r1_proof_pr113_days.py), adapted to the fixes: the done read is gated on v2 +
duel_days_auto and runs after the tick's sends, a signed text keeps its cross-check, and the parser is strict."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents import duel_days as dd
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import DuelClient, duel_cli  # noqa: F401


class DoneClient(DuelClient):
    def __init__(self, duels, done):
        super().__init__(duels)
        self.done_payload, self.done_calls, self.calls = done, 0, []

    def clock(self):
        return {"tick": 130, "next_tick_in": 40.0, "tick_seconds": 60.0, "t_hours": 2.2}

    def duels(self, done=False):
        self.calls.append("done" if done else "live")
        if done:
            self.done_calls += 1
            return {"duels": deepcopy(self.done_payload)}
        return {"duels": deepcopy(self.payload)}


def scored_cost_deal():
    """A finished real two-issue deal the game scored as a COST (weight +2, 5 days): 120 - 100 - 2 × 5 = 10."""
    return {**LIVE, "duel": 900, "status": "deal", "issues": ["price", "days"], "role": "seller", "your_limit": 100,
            "your_days_weight": 2.0, "price": 120, "days": 5, "rounds": 0, "decay_per_round": 0.08, "result": 10.0,
            "days_meaning": None}  # fmt: skip


def with_rules(cli, monkeypatch, **update):
    loaded = cli._rules()
    monkeypatch.setattr(cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update=update)))


def test_with_today_s_rules_the_duel_tick_never_reads_the_finished_duels(duel_cli, monkeypatch):  # noqa: F811
    cli, _, _, _ = duel_cli
    client = DoneClient([{**LIVE}], [])
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    assert cli._rules().rules.duel_days_auto is False  # the shipped default
    from bazaar_agent import duel_store

    monkeypatch.setattr(duel_store.DuelStore, "read_finished", lambda self, duels: False)  # main's own read: off
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1", "--no-jev"])
    assert result.exit_code == 0, result.output
    assert client.done_calls == 0


def test_a_signed_text_is_cross_checked_against_a_scored_deal_after_the_sends(duel_cli, monkeypatch):  # noqa: F811
    cli, _, _, tmp_path = duel_cli
    with_rules(cli, monkeypatch, duel_policy="v2", duel_days_auto=True)
    (tmp_path / "duels").mkdir(parents=True, exist_ok=True)
    (tmp_path / "duels" / "days_sign.json").write_text(json.dumps({"verdict": "signed", "duel": 1, "text": "x"}))
    client = DoneClient([{**LIVE}], [scored_cost_deal()])
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1", "--no-jev"])
    assert result.exit_code == 0, result.output
    assert dd.latch(tmp_path).verdict == "conflict"  # the text said signed, the game scored a cost
    assert client.calls[0] == "live" and client.calls[-1] == "done"  # the live read first, the done read last


@pytest.mark.parametrize(
    "text",
    [
        "what the other side gains (+) per delivery day",
        "primas per delivery day; positive = the buyer gains",
        "primas per day: gain (+) for the seller, loss for the buyer",
        "days count against (+) you",
        "primas you do not gain (+) per delivery day: each day costs you",
        "the rival's primas per day, gain (+) or lose (-)",
    ],
)
def test_texts_that_do_not_say_our_weight_is_a_gain_never_latch_signed(text):
    assert dd.evidence({"issues": ["price", "days"], "days_meaning": text}, True) != "signed"


def test_wording_that_merely_contains_a_loss_word_is_not_a_cost():
    for text in ("each day closes the window for you", "the house pays you primas per day"):
        assert dd.evidence({"issues": ["price", "days"], "days_meaning": text}, True) == "unknown"


def test_real_evidence_against_the_sign_overrides_a_hand_set_signed(tmp_path):
    from bazaar_agent import guardrails as gr

    switch = dd.latch(tmp_path)
    switch.observe([scored_cost_deal() | {"days_meaning": None}], True)
    assert switch.verdict == "cost"
    rules = gr.Guardrails(duel_policy="v2", duel_days_signed=True)
    assert not dd.effective_rules(rules, switch).duel_days_signed


def test_a_latch_file_that_is_not_an_object_is_a_conflict(tmp_path):
    (tmp_path / "duels").mkdir()
    (tmp_path / "duels" / "days_sign.json").write_text("[1, 2]")
    assert dd.latch(tmp_path).verdict == "conflict"  # never silently undo a recorded conflict (security P3)


def test_one_done_read_per_tick_when_the_store_already_read_the_finished_duels(duel_cli, monkeypatch):  # noqa: F811
    """r1 on #159: with duel_days_auto on, the store's read and the days latch's read could both fire on a tick
    that is a multiple of 10. The latch now reuses the store's read."""
    from bazaar_agent import duel_store

    cli, _, _, _ = duel_cli
    with_rules(cli, monkeypatch, duel_policy="v2", duel_days_auto=True)
    monkeypatch.setattr(duel_store.DuelStore, "read_finished", lambda self, duels: True)
    client = DoneClient([{**LIVE}], [])
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1", "--no-jev"])
    assert result.exit_code == 0, result.output
    assert client.clock()["tick"] % 10 == 0 and client.done_calls == 1
