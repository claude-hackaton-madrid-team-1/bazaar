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


def test_duel_run_refuses_an_accept_when_the_rival_moved_its_offer_after_our_read(duel_cli):
    """S1: the accept binds the offer standing when it lands, so the duel is read again first."""
    cli, client, asked, tmp_path = duel_cli
    reads = []
    lowered = [{**LIVE, "rival_offer": {"id": 703, "price": 105, "tick": 134, "days": 0}}]

    def duels():
        reads.append(1)
        return {"duels": deepcopy(client.payload if len(reads) == 1 else lowered)}

    client.duels = duels
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and len(reads) == 2
    assert "INSPECTOR block" in " ".join(result.output.split())
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "rejected")
    (finding,) = row["inputs"]["inspector"]["findings"]
    assert finding == "the rival's offer moved against us: we priced 110 (days 0), it is 105 (days 0) now"


def test_duel_run_records_the_clean_inspection_on_a_sent_accept(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    (row,) = decision_rows(tmp_path)
    assert row["status"] == "done" and row["inputs"]["inspector"]["verdict"] == "clean"


def test_duel_run_drops_an_accept_whose_re_read_took_the_rest_of_the_tick(duel_cli, monkeypatch):
    """Review P2: the SDK may retry a slow read; an accept after the tick's deadline is dropped, never sent late."""
    import time as real_time

    cli, client, asked, tmp_path = duel_cli
    late = {"by": 0.0}
    payload = client.payload

    def duels():
        if client.sent == [] and late["by"] == 0.0 and getattr(duels, "calls", 0) == 1:
            late["by"] = 10_000.0  # the gate's re-read: the clock jumps past the tick
        duels.calls = getattr(duels, "calls", 0) + 1
        return {"duels": deepcopy(payload)}

    client.duels = duels
    clock = real_time.monotonic  # the real one, captured before the patch
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock() + late["by"])
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and "the re-read took the rest of tick" in " ".join(result.output.split())
    (row,) = decision_rows(tmp_path)
    assert row["status"] == "expired" and row["inputs"]["inspector"]["verdict"] == "clean"


def test_duel_run_re_reads_once_per_tick_and_a_failed_re_read_fails_every_accept(duel_cli):
    """Security audit P2: a refused re-read is never retried per duel inside the tick (no 429 burst)."""
    from bazaar_agent.sdk import BazaarError

    cli, client, asked, tmp_path = duel_cli
    two = [{**d, "duel": n} for n in (95, 96) for d in client.payload]
    calls = []

    def duels():
        calls.append(1)
        if len(calls) > 1:
            raise BazaarError("rate_limited", "slow down", 429)
        return {"duels": deepcopy(two)}

    client.duels = duels
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and len(calls) == 2  # the tick's read, then ONE re-read for both accepts
    rows = decision_rows(tmp_path)
    assert [r["status"] for r in rows] == ["rejected", "rejected"]
    assert all("could not be read again" in r["inputs"]["inspector"]["findings"][0] for r in rows)


def test_each_duel_accept_re_reads_so_a_rival_that_moved_after_an_earlier_accept_is_caught(duel_cli):
    """Review r2 P1: a successful re-read is never reused for a later accept of the same tick."""
    cli, client, asked, tmp_path = duel_cli
    a, b = ({**d, "duel": n} for n in (95, 96) for d in client.payload)
    calls = []

    def duels():
        calls.append(1)
        if len(calls) == 2:  # duel 95's re-read: its rival moved against us
            return {"duels": deepcopy([{**a, "rival_offer": {**a["rival_offer"], "price": 105}}, b])}
        if len(calls) == 3:  # duel 96's re-read: its rival dropped below our limit in the meantime
            return {"duels": deepcopy([a, {**b, "rival_offer": {**b["rival_offer"], "price": 90}}])}
        return {"duels": deepcopy([a, b])}

    client.duels = duels
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and len(calls) == 3
    findings = [r["inputs"]["inspector"]["findings"][0] for r in decision_rows(tmp_path)]
    assert "moved against us" in findings[0] and "90 is not inside our limit" in findings[1]
