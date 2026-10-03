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


@pytest.mark.parametrize("jev", [False, True])
def test_duel_run_under_v2_holds_in_silence_and_jev_cannot_take_the_planners_accept(duel_cli, monkeypatch, jev):
    from dataclasses import replace

    from bazaar_agent.guardrails import load_guardrails

    cli, client, asked, tmp_path = duel_cli
    loaded = load_guardrails()
    monkeypatch.setattr(
        cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update={"duel_policy": "v2"}))
    )
    args = ["duel", "run", "--play", "--max-ticks", "1"] + ([] if jev else ["--no-jev"])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert client.sent == []  # the rival's 110 just arrived: v2 waits, and Jev's "accept" is not a legal move
    output = " ".join(result.output.split())
    assert "silence is free" in output and ("not a legal move" in output) == jev


def test_a_bug_in_the_v2_planner_holds_every_duel(duel_cli, monkeypatch):
    from dataclasses import replace

    from bazaar_agent.agents import duel_v2
    from bazaar_agent.guardrails import load_guardrails

    cli, client, asked, tmp_path = duel_cli
    loaded = load_guardrails()
    monkeypatch.setattr(
        cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update={"duel_policy": "v2"}))
    )
    monkeypatch.setattr(duel_v2, "plan_moves", lambda *a, **kw: 1 / 0)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "planner failed (ZeroDivisionError)" in result.output and client.sent == []


def test_under_v2_the_planners_accept_books_the_slot_before_jev_is_asked(duel_cli, monkeypatch):
    """r2 bite X17: the taker claims the team's accept 2 s into the tick; the duel books its accept first."""
    from dataclasses import replace

    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import duel_jev
    from bazaar_agent.guardrails import load_guardrails

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    loaded = load_guardrails()
    monkeypatch.setattr(
        cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update={"duel_policy": "v2"}))
    )
    order: list[str] = []
    reserve, pick = gr.Ledger.reserve_accept, duel_jev.DuelJev.pick
    monkeypatch.setattr(gr.Ledger, "reserve_accept", lambda self, *a: order.append("reserve") or reserve(self, *a))
    sent_at_jev = lambda self, *a, **kw: order.append(f"jev after {client.sent}") or pick(self, *a, **kw)  # noqa: E731
    monkeypatch.setattr(duel_jev.DuelJev, "pick", sent_at_jev)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert order == ["reserve", "jev after [('accept', 95)]"]  # booked AND sent before Jev (B15 / B7): nothing strands
    assert client.sent == [("accept", 95)]  # once


def test_one_duel_that_fails_does_not_cost_the_others_their_move(duel_cli, monkeypatch):
    """r2 bite B2b: a duel row that makes the policy raise skips that duel only."""
    from bazaar_agent.agents import duelist

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "duel": 94}, {**LIVE, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    real = duelist.duel_move

    def flaky(d, *a, **kw):
        if d.get("duel") == 94:
            raise ValueError("malformed row")
        return real(d, *a, **kw)

    monkeypatch.setattr(duelist, "duel_move", flaky)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "duel 94: skipped this tick (ValueError)" in result.output and client.sent[0][:2] == ("say", 95)


def test_under_v2_a_ledger_outage_holds_every_duel_instead_of_killing_the_tick(duel_cli, monkeypatch):
    """b5 (rehearsal with #62): the planner's slot read and the pre-booking fail closed."""
    from dataclasses import replace

    from bazaar_agent import guardrails as gr
    from bazaar_agent.guardrails import load_guardrails

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    loaded = load_guardrails()
    monkeypatch.setattr(
        cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update={"duel_policy": "v2"}))
    )

    def down(self, *a):
        raise ConnectionError("ledger down")

    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", down)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "ledger unreadable (ConnectionError)" in result.output and client.sent == []


def _book_order(monkeypatch, client):
    """The order of the duel loop's ledger reads and reservations, its accepts sent and its `DuelJev.pick` calls."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import duel_jev

    order: list[str] = []
    reserve, pick, accept = gr.Ledger.reserve_accept, duel_jev.DuelJev.pick, client.duel_accept
    count = gr.Ledger.accepts_in_tick
    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", lambda self, *a: order.append("count") or count(self, *a))
    monkeypatch.setattr(gr.Ledger, "reserve_accept", lambda self, *a: order.append("reserve") or reserve(self, *a))
    monkeypatch.setattr(duel_jev.DuelJev, "pick", lambda self, *a, **kw: order.append("jev") or pick(self, *a, **kw))
    monkeypatch.setattr(client, "duel_accept", lambda did: order.append("accept") or accept(did))
    return order


def test_a_forced_endgame_accept_is_booked_and_sent_before_jev_is_asked(duel_cli, monkeypatch):
    """r2 bite X17 (B15): the taker claims the team's accept 2 s into the tick, Jev may take 3 s or more. In
    the endgame an inside-limit offer is the only legal move: it is booked and sent before Jev, once."""
    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    order = _book_order(monkeypatch, client)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    # one ledger read (the guard's accept count) before the booking: v1 skips v2's slot read (one round trip);
    # the second "count" is the file ledger's own count inside reserve_accept, under its lock
    assert order == ["count", "reserve", "count", "accept", "jev"] and client.sent == [("accept", 95)] and asked == []
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "done")
    assert row["inputs"]["legal_moves"] == ["accept"] and "jev not asked" in row["reason"]  # as pick() wrote it


def test_with_one_accept_for_two_forced_duels_the_nearest_deadline_goes_first(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    rival = {"id": 702, "price": 110, "tick": 133, "days": 0}
    client.payload = [
        {**LIVE, "deadline_tick": 136, "rival_offer": rival},
        {**LIVE, "duel": 96, "deadline_tick": 134, "rival_offer": rival},
    ]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 96)]  # 96 ends this tick; 95 still has two ticks to accept


def test_a_forced_accept_that_finds_the_slot_taken_holds_and_sends_nothing(duel_cli):
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    gr.Ledger(tmp_path / "ledger.jsonl").reserve_accept(134, 2.2, 12, "LAV-02", 1)  # the taker went first
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == []
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "rejected")


def test_an_accept_jev_may_still_overrule_is_booked_after_jev(duel_cli, monkeypatch):
    """Outside the endgame Jev may turn today's accept into a counter or a hold, and a booked slot cannot be
    given back: that accept still books after Jev, as before."""
    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "rival_offer": {"id": 702, "price": 170, "tick": 133, "days": 0}}]  # meets 166
    order = _book_order(monkeypatch, client)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert order == ["jev", "count", "reserve", "count", "accept"] and client.sent == [("accept", 95)]
    assert len(asked) == 1


def test_the_taker_claiming_the_accept_while_jev_thinks_no_longer_costs_the_deadline_deal(duel_cli, monkeypatch):
    """r2 bite X17 end to end: while Jev thinks about another duel, the taker reserves the team's accept (its
    2 s grace ran out). The deadline duel already holds the slot and its accept went out."""
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    deadline = {**LIVE, "deadline_tick": 134, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}
    open_duel = {**LIVE, "duel": 96}  # rival at 98, below our cost: counter or hold, Jev is asked
    client.payload = [open_duel, deadline]
    taker: list[bool] = []

    def slow_fns(settings, rules, journal, pack, *questions):
        def move(state):
            taker.append(gr.Ledger(tmp_path / "ledger.jsonl").reserve_accept(134, 2.2, 12, "LAV-02", 1))
            return JevAdvice("hold", 0.9, dict(PROBS), None, "digest-1")

        return [move, FakeJev("undecided")][: len(questions)]

    monkeypatch.setattr(cli, "_jev_fns", slow_fns)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)] and taker == [False]
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == ["duel:95"]


def test_without_jev_a_forced_accept_goes_first_and_its_row_has_no_jev_context(duel_cli):
    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    (row,) = decision_rows(tmp_path)
    assert client.sent == [("accept", 95)] and row["jev"] is None and "jev not asked" not in row["reason"]


def test_a_ledger_outage_fails_a_forced_accept_closed(duel_cli, monkeypatch):
    """v1's forced pass reads the ledger before Jev: an outage skips that duel this tick, nothing is sent."""
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]

    def down(self, *args):
        raise ConnectionError("ledger down")

    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", down)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "duel 95: skipped this tick (ConnectionError)" in result.output and client.sent == []
