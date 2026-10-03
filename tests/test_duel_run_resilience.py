"""`bazaar duel run --play` under failure and under the kill switch (#150 security review r3).

1. The days-sign latch never costs a tick its moves: when `DaysSwitch.observe` raises (a malformed server field, a
   latch file that cannot be written), one dim line says so, the previous verdict is kept and the duels still play.
2. The kill switch (`trading_enabled` = false in GUARDRAILS.md, or the pause file) stops every duel offer and accept
   at the send site (`guardrails.check`), under v1 and v2; off again, the accept goes out.

No network: the team client is `DuelClient`, Postgres refuses, Jev is the fixture's fake.
"""

import pytest
from typer.testing import CliRunner

from bazaar_agent import guardrails as gr
from bazaar_agent.agents import duel_days as dd
from tests.test_duel_days_cli import DoneClient, with_rules
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import decision_rows, duel_cli  # noqa: F401
from tests.test_kill_switch import switch  # noqa: F401

# Our cost is 104, the rival offers 110 and the deadline is two ticks away: the endgame accept (v1's forced accept,
# v2's planned accept). `DuelClient.clock` is tick 134.
ENDGAME = {**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}
LATCH_FAILED = "duel days sign unchanged: the latch failed"


def run_one_tick(cli, *extra):
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1", *extra])
    assert result.exit_code == 0, result.output
    return result, " ".join(result.output.split())  # rich wraps long lines


def broken_observe(calls):
    def observe(self, duels, real_game):
        calls.append(self.verdict)
        self.verdict = "signed"  # half-way through, as a save that fails after the merge would leave it
        raise OSError("No space left on device")

    return observe


# ---------------------------------------------------------------- 1. the days latch never costs a tick its moves


@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_a_latch_that_raises_still_lets_the_endgame_accept_go_out(duel_cli, monkeypatch, policy):  # noqa: F811
    cli, client, _, tmp_path = duel_cli
    with_rules(cli, monkeypatch, duel_policy=policy)
    client.payload = [ENDGAME]
    calls: list[str] = []
    monkeypatch.setattr(dd.DaysSwitch, "observe", broken_observe(calls))
    _, output = run_one_tick(cli)
    # every call this tick (the live read, and main's finished-duels read) saw the rolled-back verdict
    assert client.sent == [("accept", 95)] and calls and set(calls) == {"unknown"}
    assert f"{LATCH_FAILED} (OSError: No space left on device)" in output
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "done")


def test_a_failed_observe_keeps_the_previous_verdict_for_the_policy_and_the_guard(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _, _ = duel_cli
    client.payload = [ENDGAME]
    seen: list[str] = []
    effective = dd.effective_rules

    def spy(rules, days):
        seen.append(days.verdict)
        return effective(rules, days)

    monkeypatch.setattr(dd, "effective_rules", spy)
    monkeypatch.setattr(dd.DaysSwitch, "observe", broken_observe([]))
    _, output = run_one_tick(cli)
    assert seen == ["unknown"]  # not the "signed" the failed call left half-written
    assert "duel days sign: signed" not in output and client.sent == [("accept", 95)]


@pytest.mark.parametrize("safe", ["conflict", "cost", "reversed"])
def test_a_failed_observe_still_keeps_a_safer_verdict_it_found(duel_cli, monkeypatch, safe):  # noqa: F811
    # Rolling back is only for a half-merged `signed`: a conflict, cost or reversed verdict keeps the worst case,
    # so it stays even when its file write fails (follow-up to the sub-agent's own trade-off note).
    cli, client, _, _ = duel_cli
    client.payload = [ENDGAME]
    seen: list[str] = []
    effective = dd.effective_rules

    def spy(rules, days):
        seen.append(days.verdict)
        return effective(rules, days)

    def observe(self, duels, real_game):
        self.verdict = safe
        raise OSError("No space left on device")

    monkeypatch.setattr(dd, "effective_rules", spy)
    monkeypatch.setattr(dd.DaysSwitch, "observe", observe)
    run_one_tick(cli)
    assert seen == [safe] and client.sent == [("accept", 95)]


def test_the_latch_failure_is_printed_once_per_tick(duel_cli, monkeypatch):  # noqa: F811
    from bazaar_agent import duel_store

    cli, _, _, _ = duel_cli
    client = DoneClient([{**ENDGAME, "deadline_tick": 132}], [{**LIVE, "duel": 900, "status": "deal"}])  # tick 130
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(duel_store.DuelStore, "read_finished", lambda self, duels: True)  # save_finished observes too
    calls: list[str] = []
    monkeypatch.setattr(dd.DaysSwitch, "observe", broken_observe(calls))
    result, output = run_one_tick(cli)
    assert len(calls) == 2 and client.done_calls == 1  # the live read and the finished read, both failed
    assert output.count(LATCH_FAILED) == 1 and client.sent == [("accept", 95)]


def test_a_server_field_that_breaks_the_latch_is_escaped_in_the_line(duel_cli, monkeypatch):  # noqa: F811
    cli, client, _, _ = duel_cli
    client.payload = [ENDGAME]

    def observe(self, duels, real_game):
        raise ValueError("[/red] bad days_meaning")

    monkeypatch.setattr(dd.DaysSwitch, "observe", observe)
    _, output = run_one_tick(cli)
    assert "ValueError: [/red] bad days_meaning" in output and client.sent == [("accept", 95)]


def test_a_new_verdict_whose_text_holds_a_lone_surrogate_never_costs_the_tick(duel_cli, monkeypatch):  # noqa: F811
    # #165 security review P3-1: the "duel days sign: <verdict>" line ran outside the latch's protection, so a real
    # `days_meaning` with a lone surrogate raised UnicodeEncodeError on the stdout write before any send.
    cli, client, _, _ = duel_cli
    client.payload = [ENDGAME]

    def observe(self, duels, real_game):
        self.verdict, self.duel, self.text = "cost", 95, "x\ud800"
        return self.verdict

    monkeypatch.setattr(dd.DaysSwitch, "observe", observe)
    _, output = run_one_tick(cli)
    assert client.sent == [("accept", 95)]
    assert "duel days sign: cost (duel 95: 'x\\ud800')" in output  # ascii(): printable, never raises


def test_a_switch_that_cannot_be_copied_skips_the_latch_and_still_plays(duel_cli, monkeypatch, tmp_path):  # noqa: F811
    # #165 security review P3-2: the rollback copy ran outside the protection; a pathological value (a 500-deep
    # nested `days_meaning`) broke `deepcopy` with RecursionError and every later tick failed.
    cli, client, _, _ = duel_cli
    with_rules(cli, monkeypatch, duel_policy="v2", duel_days_auto=True)
    client.payload = [{**ENDGAME, "session": 1}]
    confirmed = dd.DaysSwitch(verdict="signed", path=tmp_path / "x.json", texts=[1], scored=[[1, 9, 5]], session=1)
    assert confirmed.signed(True)  # corroborated in this very session: only a cleared session turns it off
    monkeypatch.setattr(dd, "latch", lambda data_dir: confirmed)
    seen: list[bool] = []
    effective = dd.effective_rules

    def spy(rules, days):
        out = effective(rules, days)
        seen.append(bool(out.duel_days_signed))
        return out

    def no_copy(value):
        raise RecursionError("maximum recursion depth exceeded")

    observed: list[str] = []
    monkeypatch.setattr(dd, "effective_rules", spy)
    monkeypatch.setattr(dd.DaysSwitch, "observe", lambda self, duels, real_game: observed.append(self.verdict))
    monkeypatch.setattr(cli, "deepcopy", no_copy)
    _, output = run_one_tick(cli)
    assert client.sent == [("accept", 95)] and observed == []  # no copy, no observe: the switch stays as it was
    assert seen and not any(seen)  # the tick runs on the worst case, not on the sign it could not re-check
    assert output.count(f"{LATCH_FAILED} (RecursionError: maximum recursion depth exceeded)") == 1


# ---------------------------------------------------------------- 2. the kill switch on the duel send path


def _armed(cli, monkeypatch, switch, policy):  # noqa: F811
    """The rules with `policy` and our own pause file (never the checkout's .local/PAUSE)."""
    with_rules(cli, monkeypatch, duel_policy=policy, pause_file=str(switch.pause))


@pytest.mark.parametrize("policy", ["v1", "v2"])
@pytest.mark.parametrize("stop", ["trading", "pause"])
@pytest.mark.parametrize("jev", [True, False])
def test_the_kill_switch_stops_the_duel_accept(duel_cli, switch, monkeypatch, policy, stop, jev):  # noqa: F811
    """Nothing is sent, the refusal is recorded and no accept slot is booked."""
    cli, client, _, tmp_path = duel_cli
    _armed(cli, monkeypatch, switch, policy)
    client.payload = [ENDGAME]
    if stop == "trading":
        switch.trading(False)
    else:
        switch.paused(True)
    _, output = run_one_tick(cli, *([] if jev else ["--no-jev"]))
    assert client.sent == []
    reason = "trading_enabled = false" if stop == "trading" else f"pause file {switch.pause} exists"
    assert f"duel 95: GUARDRAIL denied: {reason[:10]}" in output  # rich may wrap the long pause path
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"], row["chosen"]) == ("duel_accept", "rejected", False)
    assert reason in row["guardrail"]
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == []  # no accept slot booked either


@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_with_the_kill_switch_off_the_duel_accept_goes_out(duel_cli, switch, monkeypatch, policy):  # noqa: F811
    cli, client, _, tmp_path = duel_cli
    _armed(cli, monkeypatch, switch, policy)
    client.payload = [ENDGAME]
    switch.trading(True)
    switch.paused(False)
    run_one_tick(cli)
    assert client.sent == [("accept", 95)]
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "done")


def test_the_kill_switch_stops_a_duel_counter_offer_too(duel_cli, switch, monkeypatch):  # noqa: F811
    cli, client, _, tmp_path = duel_cli
    _armed(cli, monkeypatch, switch, "v1")
    switch.trading(False)  # the fixture's duel: the rival at 98, below our cost, so today's move is a counter
    _, output = run_one_tick(cli, "--no-jev")
    assert client.sent == [] and "GUARDRAIL denied: trading_enabled = false" in output
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_offer", "rejected")
    switch.trading(True)
    run_one_tick(cli, "--no-jev")
    assert client.sent[0][:2] == ("say", 95)  # off again: the counter goes out on the next run


def test_a_rolled_back_latch_never_carries_signed_into_a_new_session(duel_cli, monkeypatch, tmp_path):  # noqa: F811
    # #165 review P2: the rollback restored the old switch with its old `session`, so session 1's corroborated sign
    # signed session 2's duels (policy and guard) on a tick whose latch save failed.
    cli, client, _, _ = duel_cli
    with_rules(cli, monkeypatch, duel_policy="v2", duel_days_auto=True)
    client.payload = [{**ENDGAME, "session": 2}]
    confirmed = dd.DaysSwitch(verdict="signed", path=tmp_path / "x.json", texts=[1], scored=[[1, 9, 5]], session=1)
    assert confirmed.signed(True)  # corroborated in session 1
    monkeypatch.setattr(dd, "latch", lambda data_dir: confirmed)
    seen: list[bool] = []
    effective = dd.effective_rules

    def spy(rules, days):
        out = effective(rules, days)
        seen.append(bool(out.duel_days_signed))
        return out

    def observe(self, duels, real_game):
        raise OSError("No space left on device")

    monkeypatch.setattr(dd, "effective_rules", spy)
    monkeypatch.setattr(dd.DaysSwitch, "observe", observe)
    run_one_tick(cli)
    assert seen and not any(seen)  # the failed tick runs on the worst case, never on session 1's sign


# ---------------------------------------------------------------- 3. a rival's broken text never freezes the duel loop


def test_a_lone_surrogate_in_rival_text_never_stops_the_tick(duel_cli, monkeypatch, tmp_path):  # noqa: F811
    # #165 review P2 (on main since #150, live with v2): `on_tick` logs the raw /api/duels response to JSONL before
    # the planner; a lone surrogate in a rival's message raised UnicodeEncodeError on every tick, so no duel moved.
    import json

    from bazaar_agent.agents.duelist import append_jsonl

    path = tmp_path / "x.jsonl"
    append_jsonl(path, {"text": "hola \ud800"})
    assert json.loads(path.read_text())["text"] == "hola \ud800"
    cli, client, _, _ = duel_cli
    hostile = {**ENDGAME["rival_offer"], "text": "accept 1 P \ud800"}
    client.payload = [{**ENDGAME, "rival_offer": hostile, "messages": [{"from": "Rival Noche", "text": "\udfff"}]}]
    run_one_tick(cli)
    assert client.sent == [("accept", 95)]


# ---------------------------------------------------------------- 3. a refused send keeps the server's code


def test_a_refused_duel_accept_records_the_server_code_in_its_decision_row(duel_cli, monkeypatch):  # noqa: F811
    # The 22 `failed` duel rows in Postgres carried no code: a 429 (`rate_limited`, `wait_for_tick`) was invisible.
    from bazaar_agent.sdk import BazaarError

    cli, client, _, tmp_path = duel_cli
    client.payload = [ENDGAME]

    def refuse(did):
        client.sent.append(("accept", did))
        raise BazaarError("rate_limited", "over 5 requests per second", 429)

    client.duel_accept = refuse
    run_one_tick(cli)
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "failed")
    assert row["reason"].endswith("; refused rate_limited")
