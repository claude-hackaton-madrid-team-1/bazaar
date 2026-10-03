"""Jev questions as `JevFn`s with a masked decision log, and the CLI wiring of the duel player and the maker.

No network: `judge` is replaced by a fake, the team client by a recorder, Postgres by a refusal.
"""

import json
from copy import deepcopy
from types import MappingProxyType

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.jev_journal import JevJournal, question_fn
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.jev import JevUsageError, JudgeResult, Verdict, log_tally, read_log
from tests.test_duel_jev import LIVE, FakeJev

PACK = REPO_ROOT / "questions" / "duels.json"
PROBS = MappingProxyType({"accept": 0.91, "counter": 0.07, "hold": 0.02})


def judged(state, questions, *, api_key, timeout_s):
    (question_id,) = questions
    answer = Verdict("choice", "accept", 0.91, "confidence", 0.75, leaning="accept", probabilities=PROBS, margin=0.84)
    return JudgeResult("jev-1.13.0", 7, MappingProxyType({question_id: answer}))


def test_a_call_writes_one_masked_decision_line_and_returns_its_digest(tmp_path):
    fn = question_fn(PACK, "duel_move", api_key="", timeout_s=3.0, journal=JevJournal(tmp_path), judge_fn=judged)
    advice = fn({"duel": {"our_limit": 104, "note": "tk-abcd-1234-efgh"}})
    assert (advice.verdict, advice.value, dict(advice.probabilities)) == ("accept", 0.91, dict(PROBS))
    (line,) = read_log(tmp_path)
    assert line["stateDigest"] == advice.digest and line["model"] == "jev-1.13.0"
    assert "tk-abcd" not in json.dumps(line)  # only the masked digest of the state is logged
    assert advice.as_dict()["digest"] == advice.digest


def test_a_judge_failure_or_a_broken_log_never_stops_the_tick(tmp_path):
    def broken(*args, **kwargs):
        raise JevUsageError("state is empty")

    advice = question_fn(PACK, "duel_move", api_key="", timeout_s=3.0, judge_fn=broken)({})
    assert advice == JevAdvice("undecided", 0.0, reason="jev error: JevUsageError")
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    lines: list[str] = []
    journal = JevJournal(blocked, log=lines.append)
    advice = question_fn(PACK, "duel_move", api_key="", timeout_s=3.0, journal=journal, judge_fn=judged)({"x": 1})
    assert advice.verdict == "accept" and advice.digest is None and lines
    journal.outcome("abc", "right", "duel_move")  # also dropped, not raised


def test_without_a_key_jev_is_undecided_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "must-not-be-read")
    advice = question_fn(PACK, "duel_move", api_key="", timeout_s=3.0)({"duel": {"our_limit": 104}})
    assert (advice.verdict, advice.reason) == ("undecided", "typesafe_api_key_missing")


def test_outcome_lines_feed_the_per_question_report(tmp_path):
    journal = JevJournal(tmp_path)
    fn = question_fn(PACK, "duel_move", api_key="", timeout_s=3.0, journal=journal, judge_fn=judged)
    first, second = fn({"duel": 1}), fn({"duel": 2})
    journal.outcome(first.digest, "right", "duel_move", "duel 1 deal")
    journal.outcome(second.digest, "wrong", "duel_move", "duel 2 no deal")
    (row,) = log_tally(read_log(tmp_path))
    assert (row.question, row.calls, row.decided, row.right, row.wrong) == ("duel_move", 2, 2, 1, 1)


# ---------------------------------------------------------------- the CLI: `bazaar duel run` and `agent maker`


class DuelClient:
    """The team client for `duel run`: canned /api/duels, every write recorded."""

    def __init__(self, duels):
        self.payload, self.sent = duels, []

    def clock(self):
        return {"tick": 134, "next_tick_in": 40.0, "tick_seconds": 60.0, "t_hours": 2.2}

    def duels(self):
        return {"duels": deepcopy(self.payload)}

    def duel_accept(self, did):
        self.sent.append(("accept", did))
        return {"ok": True}

    def duel_say(self, did, text, price=None, days=None):
        self.sent.append(("say", did, price, days))
        return {"ok": True}


@pytest.fixture
def duel_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    client = DuelClient([{**LIVE, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}])
    asked: list[dict] = []

    def fake_fns(settings, rules, journal, pack, *questions):
        def move(state):
            asked.append(state)
            return JevAdvice("accept", 0.91, dict(PROBS), None, "digest-1")

        return [move, FakeJev("undecided")][: len(questions)]

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(cli, "_jev_fns", fake_fns)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    return cli, client, asked, tmp_path


def decision_rows(tmp_path):
    path = tmp_path / "agents" / "decisions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if "kind" in line]


def test_duel_run_plays_jevs_legal_pick_and_records_its_floats(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)] and len(asked) == 1  # today's move would have countered at 157
    output = " ".join(result.output.split())  # rich wraps long lines
    assert "Jev duel_move" in output and "-> accept 110 (inside our limit) · jev accept (0.91)" in output
    (row,) = decision_rows(tmp_path)
    assert (row["agent"], row["kind"], row["status"], row["dry_run"]) == ("duels", "duel_accept", "done", False)
    assert row["jev"]["verdict"] == "accept" and row["jev"]["probabilities"] == dict(PROBS)
    assert row["inputs"]["our_limit"] == 104 and row["inputs"]["legal_moves"] == ["accept", "counter", "hold"]


def test_duel_run_without_jev_plays_todays_move(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert asked == [] and client.sent[0][:2] == ("say", 95) and client.sent[0][2] > 104
    (row,) = decision_rows(tmp_path)
    assert row["kind"] == "duel_offer" and row["jev"] is None


def test_duel_run_log_only_records_would_moves_and_sends_nothing(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and decision_rows(tmp_path)[0]["dry_run"] is True


def test_a_bug_in_the_jev_layer_never_costs_a_duel_its_move(duel_cli, monkeypatch):
    from bazaar_agent.agents import duel_jev

    cli, client, asked, tmp_path = duel_cli

    def broken(self, *args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(duel_jev.DuelJev, "pick", broken)
    monkeypatch.setattr(duel_jev.DuelOutcomes, "settle", broken)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "duel jev failed (RuntimeError): today's moves this tick" in result.output
    assert client.sent[0][:2] == ("say", 95)  # today's counter still went out


@pytest.mark.parametrize("jev", [False, True])
def test_the_guardrail_stops_a_duel_move_outside_our_limit_at_the_send_site(duel_cli, monkeypatch, jev):
    """Second line of defence: whichever layer chose it, a move worth less than our cost 104 is never sent."""
    from bazaar_agent.agents import duel_jev, duelist

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "issues": ["price", "days"], "your_days_weight": 2.0}]
    outside = duelist.DuelMove("offer", 110, 5, "a policy bug")  # 110 - 2 × 5 = 100 < cost 104
    if jev:
        pick = duel_jev.DuelPick(outside, outside, ("counter",), "jev counter")
        monkeypatch.setattr(duel_jev.DuelJev, "pick", lambda self, duels, *a, **kw: {95: pick})
    else:
        monkeypatch.setattr(duelist, "duel_move", lambda *a, **kw: outside)
    args = ["duel", "run", "--play", "--max-ticks", "1"] + ([] if jev else ["--no-jev"])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert client.sent == [] and "duel_inside_limit" in " ".join(result.output.split())
    (row,) = decision_rows(tmp_path)
    assert row["status"] == "rejected"
