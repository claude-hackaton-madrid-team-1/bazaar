"""Injection recording cannot interrupt moves or classify team threads as dealers."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bazaar_agent import injection_log as il
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Ledger
from tests.agent_fakes import FakePublic, FakeTeam, ask, clock, parts
from tests.test_injection_log import FakeConn
from tests.test_jev_journal import duel_cli  # noqa: F401 - shared CLI fixture
from tests.test_redteam_injection import PAYLOADS
from tests.test_team_desk import thread


@pytest.fixture
def recording_taker(tmp_path):
    conn, team = FakeConn(), FakeTeam()
    recorder = il.InjectionLog(lambda: conn)
    taker = Taker(
        team,
        FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}),
        live=True,
        log=lambda message: None,
        now=lambda: 1000.0,
        sleep=lambda seconds: None,
        config=TakerConfig(max_dealer_threads=0),
        injection_log=recorder,
        **parts(tmp_path),
    )
    return taker, team, recorder, conn


def test_inline_team_thread_and_team_desk_record_one_team_attempt(recording_taker, monkeypatch):
    taker, team, recorder, conn = recording_taker
    team.threads = [thread(messages=[{"message": 91, "sender": "t13", "text": PAYLOADS["role_tag"]}])]
    sources = []
    note_thread = recorder.note_thread

    def note(payload, us, source, tick=None):
        sources.append(source)
        return note_thread(payload, us, source, tick)

    monkeypatch.setattr(recorder, "note_thread", note)
    taker.on_tick(clock())
    assert sources == ["team_thread", "team_thread"]  # _keep, then the team desk's after-send pass
    assert len(conn.rows) == 1
    assert (conn.rows[0][2], conn.rows[0][4], conn.rows[0][6], conn.rows[0][13]) == ("team_thread", 42, 91, il.IGNORED)


@pytest.mark.parametrize("broken_extractor", [False, True])
def test_malformed_thread_recording_cannot_stop_the_taker_move(recording_taker, monkeypatch, broken_extractor):
    taker, team, _, _ = recording_taker
    team.threads = [{"id": 41, "kind": "persona", "with": "unknown", "messages": 5}]
    extracted = []
    from_thread = il.from_thread

    def extract(payload, *args):
        extracted.append(payload["messages"])
        if broken_extractor:
            raise TypeError("malformed thread messages")
        return from_thread(payload, *args)

    monkeypatch.setattr(il, "from_thread", extract)
    taker.on_tick(clock())
    assert extracted == [5]
    assert ("accept", 1) in team.sent


@pytest.mark.parametrize("broken_extractor", [False, True])
def test_malformed_duel_recording_cannot_stop_the_runner(duel_cli, monkeypatch, broken_extractor):  # noqa: F811
    cli, client, _, tmp_path = duel_cli
    client.payload[0]["messages"] = 5
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.where = "postgres test ledger"  # activate the recorder without a real database
    monkeypatch.setattr(cli, "_ledger", lambda *args, **kwargs: ledger)
    recorder = il.InjectionLog(None)
    monkeypatch.setattr(cli, "_injection_log", lambda *args: recorder)
    finished = []
    monkeypatch.setattr(cli, "_tick_evals", lambda *args: SimpleNamespace(after_tick=finished.append))
    extracted = []
    from_duel = il.from_duel

    def extract(payload, *args):
        extracted.append(payload["messages"])
        if broken_extractor:
            raise TypeError("malformed duel messages")
        return from_duel(payload, *args)

    monkeypatch.setattr(il, "from_duel", extract)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert extracted == [5]
    assert client.sent and client.sent[0][:2] == ("say", 95)
    assert finished == [134]  # the final post-send step ran; swallowed tick errors cannot pass this test
