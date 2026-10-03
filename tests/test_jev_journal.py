"""Jev questions as `JevFn`s with a masked decision log, and the CLI wiring of the duel player and the maker.

No network: `judge` is replaced by a fake, the team client by a recorder, Postgres by a refusal.
"""

import json
from copy import deepcopy
from types import MappingProxyType

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli as cli_module
from bazaar_agent.agents.jev_journal import JevJournal, question_fn
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import Ledger
from bazaar_agent.jev import JevUsageError, JudgeResult, Verdict, log_tally, read_log
from tests.test_duel_jev import LIVE, FakeJev

PACK = REPO_ROOT / "questions" / "duels.json"
REAL_LEDGER = cli_module._ledger  # before any fixture replaces it
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

    def duels(self, done=False):
        return {"duels": [] if done else deepcopy(self.payload)}  # `?done=true`: none finished yet

    def duel_accept(self, did):
        self.sent.append(("accept", did))
        return {"ok": True}

    def duel_say(self, did, text, price=None, days=None):
        self.sent.append(("say", did, price, days))
        return {"ok": True}


def use_policy(monkeypatch, cli, policy):
    """`duel run` reads GUARDRAILS.md through `cli._rules`: pin the duel policy a test is about."""
    from dataclasses import replace

    from bazaar_agent.guardrails import load_guardrails

    loaded = load_guardrails()
    rules = loaded.rules.model_copy(update={"duel_policy": policy})
    monkeypatch.setattr(cli, "_rules", lambda: replace(loaded, rules=rules))


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
    # Stands in for the shared ledger a live run needs (which ledger a process may use: tests/test_ledger.py).
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: Ledger(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    use_policy(monkeypatch, cli, "v1")  # these tests are about v1 (GUARDRAILS.md runs v2): v2 tests pin v2
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


class ReserveFails(Ledger):
    """Reads work, the accept reservation hits a dropped connection."""

    def reserve_accept(self, tick, t_hours, price, item, limit):
        from bazaar_agent.ledger_pg import LedgerUnavailable

        raise LedgerUnavailable("accept reservation failed (OperationalError)")


@pytest.mark.parametrize("broken", ["down", "reserve"])
def test_a_ledger_failure_costs_the_duel_its_write_this_tick_never_the_loop(duel_cli, monkeypatch, broken):
    import psycopg

    from bazaar_agent import ticks
    from bazaar_agent.ledger_pg import PgLedger

    cli, client, asked, tmp_path = duel_cli

    def refused():
        raise psycopg.OperationalError("the server closed the connection")

    ledger = PgLedger(refused, "duels") if broken == "down" else ReserveFails(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
    loop_errors = []
    run = ticks.run_per_tick
    monkeypatch.setattr(cli, "run_per_tick", lambda *a, **k: run(*a, **k, on_error=lambda *e: loop_errors.append(e)))
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert loop_errors == [] and client.sent == []  # the tick ended normally and nothing was sent
    assert "no accept this tick (fail closed)" in " ".join(result.output.split())
    (row,) = decision_rows(tmp_path)
    assert row["status"] == "rejected" and row["guardrail"].startswith("ledger unavailable:")


def test_duel_run_play_refuses_without_the_shared_ledger(duel_cli, monkeypatch):
    cli, client, asked, tmp_path = duel_cli
    monkeypatch.setattr(cli, "_ledger", REAL_LEDGER)  # the real choice: DATABASE_URL is the local default here
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 1 and client.sent == []
    assert "refusing to trade: live trading needs the team's shared ledger" in " ".join(result.output.split())
    log_only = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1"])  # sends nothing: the file is fine
    assert log_only.exit_code == 0, log_only.output


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
    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    args = ["duel", "run", "--play", "--max-ticks", "1"] + ([] if jev else ["--no-jev"])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert client.sent == []  # the rival's 110 just arrived: v2 waits, and Jev's "accept" is not a legal move
    output = " ".join(result.output.split())
    assert "silence is free" in output and ("not a legal move" in output) == jev


def test_a_bug_in_the_v2_planner_holds_every_duel(duel_cli, monkeypatch):
    from bazaar_agent.agents import duel_v2

    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    monkeypatch.setattr(duel_v2, "plan_moves", lambda *a, **kw: 1 / 0)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "planner failed (ZeroDivisionError)" in result.output and client.sent == []


def test_under_v2_the_planners_accept_books_the_slot_before_jev_is_asked(duel_cli, monkeypatch):
    """r2 bite X17: the taker claims the team's accept 2 s into the tick; the duel books its accept first."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import duel_jev

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    use_policy(monkeypatch, cli, "v2")
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
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}]
    use_policy(monkeypatch, cli, "v2")

    def down(self, *a):
        raise ConnectionError("ledger down")

    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", down)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "ledger unreadable (ConnectionError)" in result.output and client.sent == []


# ---------------------------------------------------------------- v2: the same safety nets as v1 (GUARDRAILS.md: v2)

PLANNED_ACCEPT = {**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}


@pytest.mark.parametrize(("kind", "jev"), [("offer", False), ("offer", True), ("accept", False), ("accept", True)])
def test_under_v2_the_guardrail_stops_a_duel_move_outside_our_limit_at_the_send_site(duel_cli, monkeypatch, kind, jev):
    """Whatever the planner (or Jev on top of it) chose, a move worth less than our cost 104 is never sent."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import duel_jev, duel_v2, duelist

    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    if kind == "offer":  # 110 - 2 × 5 = 100 < cost 104
        client.payload = [{**LIVE, "issues": ["price", "days"], "your_days_weight": 2.0}]
        outside = duelist.DuelMove("offer", 110, 5, "a planner bug")
    else:  # the rival's standing 100: on the limit is no surplus
        client.payload = [{**PLANNED_ACCEPT, "rival_offer": {"id": 702, "price": 100, "tick": 133, "days": 0}}]
        outside = duelist.DuelMove("accept", 100, None, "a planner bug")
    monkeypatch.setattr(duel_v2, "plan_moves", lambda *a, **kw: {95: outside})
    if jev:
        pick = duel_jev.DuelPick(outside, outside, (kind if kind == "accept" else "counter",), f"jev {kind}")
        monkeypatch.setattr(duel_jev.DuelJev, "pick", lambda self, duels, *a, **kw: {95: pick})
    args = ["duel", "run", "--play", "--max-ticks", "1"] + ([] if jev else ["--no-jev"])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert client.sent == [] and "duel_inside_limit" in " ".join(result.output.split())
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == []  # denied before the slot was booked
    (row,) = decision_rows(tmp_path)
    assert row["status"] == "rejected" and "duel_inside_limit" in row["guardrail"]


@pytest.mark.parametrize("broken", ["down", "reserve"])
def test_under_v2_a_ledger_failure_costs_the_planned_accept_its_write_never_the_loop(duel_cli, monkeypatch, broken):
    """v2 fails closed twice: a ledger it cannot read holds every duel; a reservation that fails drops the accept."""
    import psycopg

    from bazaar_agent import ticks
    from bazaar_agent.ledger_pg import PgLedger

    cli, client, asked, tmp_path = duel_cli
    client.payload = [PLANNED_ACCEPT]
    use_policy(monkeypatch, cli, "v2")

    def refused():
        raise psycopg.OperationalError("the server closed the connection")

    ledger = PgLedger(refused, "duels") if broken == "down" else ReserveFails(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
    loop_errors = []
    run = ticks.run_per_tick
    monkeypatch.setattr(cli, "run_per_tick", lambda *a, **k: run(*a, **k, on_error=lambda *e: loop_errors.append(e)))
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert loop_errors == [] and client.sent == []  # the tick ended normally and nothing was sent
    output = " ".join(result.output.split())
    (row,) = decision_rows(tmp_path)
    if broken == "down":  # the planner's slot read failed: no accept and no offer this tick
        assert "ledger unreadable" in output and "every duel holds" in output
        assert (row["kind"], row["chosen"]) == ("duel_hold", False)  # Jev, asked with no slot, holds it too
    else:  # the planner accepted, the reservation failed: dropped, never sent unbooked
        assert "no accept this tick (fail closed)" in output
        assert (row["kind"], row["status"]) == ("duel_accept", "rejected")
        assert row["guardrail"].startswith("ledger unavailable:")


def test_under_v2_a_planned_accept_that_finds_the_slot_taken_holds_and_sends_nothing(duel_cli, monkeypatch):
    """Taken before the tick: the planner sees no slot and holds; nothing is sent past the team's quota."""
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    client.payload = [PLANNED_ACCEPT]
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    ledger.reserve_accept(134, 2.2, 12, "LAV-02", 1)  # the taker went first
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and ledger.accept_items(134) == ["LAV-02"]
    (row,) = decision_rows(tmp_path)
    assert row["kind"] == "duel_hold" and "accept queued" in row["reason"]


def test_under_v2_the_taker_claiming_the_slot_during_the_re_read_costs_the_accept(duel_cli, monkeypatch):
    """Taken after the planner read the slot (during the S1 re-read): the reservation refuses the accept."""
    from bazaar_agent import guardrails as gr

    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    reads: list[int] = []

    def duels():  # the S1 re-read before the accept: the taker reserves the team's accept meanwhile
        reads.append(1)
        if len(reads) == 2:
            gr.Ledger(tmp_path / "ledger.jsonl").reserve_accept(134, 2.2, 12, "LAV-02", 1)
        return {"duels": deepcopy([PLANNED_ACCEPT])}

    client.duels = duels
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [] and len(reads) == 2
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == ["LAV-02"]
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"], row["guardrail"]) == (
        "duel_accept",
        "rejected",
        "accept slot taken by another process",
    )


def test_under_v2_a_sent_accept_records_the_clean_inspection(duel_cli, monkeypatch):
    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    client.payload = [PLANNED_ACCEPT]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]
    (row,) = decision_rows(tmp_path)
    assert (row["kind"], row["status"]) == ("duel_accept", "done")
    assert row["inputs"]["inspector"]["verdict"] == "clean"


def test_under_v2_one_duel_that_fails_does_not_cost_the_others_their_move(duel_cli, monkeypatch):
    """The planner holds a duel it cannot read (duel_v2.plan_moves) and still plans the others."""
    from bazaar_agent.agents import duel_v2

    cli, client, asked, tmp_path = duel_cli
    use_policy(monkeypatch, cli, "v2")
    client.payload = [{**LIVE, "duel": 94}, PLANNED_ACCEPT]
    real = duel_v2.duel_plan

    def flaky(d, *a, **kw):
        if d.get("duel") == 94:
            raise ValueError("malformed row")
        return real(d, *a, **kw)

    monkeypatch.setattr(duel_v2, "duel_plan", flaky)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]
    rows = {r["move"]["duel"]: r for r in decision_rows(tmp_path)}
    assert rows[94]["kind"] == "duel_hold" and "unreadable duel (ValueError)" in rows[94]["reason"]


def _book_order(monkeypatch, client):
    """The order of the duel loop's ledger reservations, its accepts sent and its `DuelJev.pick` calls."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import duel_jev

    order: list[str] = []
    reserve, pick, accept = gr.Ledger.reserve_accept, duel_jev.DuelJev.pick, client.duel_accept
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
    assert order == ["reserve", "accept", "jev"] and client.sent == [("accept", 95)] and asked == []
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
    assert order == ["jev", "reserve", "accept"] and client.sent == [("accept", 95)] and len(asked) == 1


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


def test_duel_run_forgets_a_failed_re_read_at_every_tick_even_a_repeated_tick_number(duel_cli, monkeypatch):
    """Review r4 P3: `rereads.new_tick()` in duel_run (ticks 134 → 135 → 134 after a world reset). The ticks
    are driven directly, so a regression fails instead of waiting for a tick that never comes."""
    from bazaar_agent.sdk import BazaarError
    from bazaar_agent.ticks import Clock

    cli, client, asked, tmp_path = duel_cli
    calls: list[int] = []

    def duels():
        calls.append(1)
        if len(calls) == 2:  # tick 134's re-read
            raise BazaarError("rate_limited", "slow down", 429)
        return {"duels": deepcopy(client.payload)}

    def three_ticks(read_clock, on_tick, **kwargs):
        for tick in (134, 135, 134):
            on_tick(Clock.model_validate({"tick": tick, "next_tick_in": 40.0, "tick_seconds": 60.0, "t_hours": 2.2}))
        return 3

    client.duels = duels
    monkeypatch.setattr(cli, "run_per_tick", three_ticks)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "3"])
    assert result.exit_code == 0, result.output
    # 134: refused (429); 135: re-read, accepted; 134 again: the old failure is forgotten, re-read, accepted
    assert client.sent == [("accept", 95), ("accept", 95)] and len(calls) == 6


@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_a_forced_or_planned_early_accept_is_refused_when_the_re_read_shows_a_moved_offer(
    duel_cli, monkeypatch, policy
):
    """S1 on #150's early pass: v1's forced endgame accept and v2's planned accept are booked before Jev, so
    the gate must run there too, before the slot, on a fresh read."""
    cli, client, asked, tmp_path = duel_cli
    standing = {**LIVE, "deadline_tick": 136, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}
    moved = {**standing, "rival_offer": {"id": 703, "price": 105, "tick": 134, "days": 0}}
    reads: list[int] = []

    def duels():
        reads.append(1)
        return {"duels": deepcopy([standing] if len(reads) == 1 else [moved])}

    client.duels = duels
    use_policy(monkeypatch, cli, policy)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert not [s for s in client.sent if s[0] == "accept"] and len(reads) >= 2
    rows = [r for r in decision_rows(tmp_path) if r["kind"] == "duel_accept"]
    assert (
        rows and rows[0]["status"] == "rejected" and "moved against us" in rows[0]["inputs"]["inspector"]["findings"][0]
    )


def test_a_lone_surrogate_in_a_rivals_text_never_stops_the_duel_tick(duel_cli, tmp_path):
    """`duel run` logs the raw /api/duels response before it plans: a rival's text with a lone surrogate (an emoji cut
    in half by a JS slice, or on purpose) raised UnicodeEncodeError there on every tick, so no duel moved."""
    from bazaar_agent.agents.duelist import append_jsonl

    path = tmp_path / "x.jsonl"
    append_jsonl(path, {"text": "hola \ud83d"})
    assert json.loads(path.read_text())["text"] == "hola \ud83d"  # ASCII-escaped, same text read back
    cli, client, _, _ = duel_cli
    rival = {"id": 702, "price": 110, "tick": 133, "days": 0, "text": "deal \ud83d"}
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": rival}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1", "--no-jev"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]
