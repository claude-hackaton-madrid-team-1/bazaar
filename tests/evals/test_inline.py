"""Evals inside the agents (evals/inline.py): cadence, never doubled, fail open, each agent its own targets."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from typer.testing import CliRunner

from bazaar_agent.evals import inline
from bazaar_agent.evals.inline import TARGETS_BY_AGENT, TickEvals
from bazaar_agent.evals.run import RunSummary
from tests import test_agents_market, test_jev_journal

# The CLI fixtures of the agent and duel suites: fakes for the game, no database.
agent_cli, duel_cli = test_agents_market.agent_cli, test_jev_journal.duel_cli


class Conn:
    """A connection whose advisory lock is free (`locked=False`: another process of the kind holds it)."""

    def __init__(self, free: bool = True) -> None:
        self.free, self.autocommit, self.sql = free, False, []

    def __enter__(self) -> Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> Conn:
        self.sql.append(query)
        return self

    def fetchone(self) -> tuple[bool]:
        return (self.free,)


def make(
    agent: str = "taker",
    every: int = 3,
    start: Callable[[Callable[[], None]], Any] | None = None,
    connect: Callable[[], Any] = Conn,
) -> tuple[TickEvals, list[str]]:
    logs: list[str] = []
    evals = TickEvals(agent, every, connect, lambda conn: "t01", lambda: None, logs.append, start or (lambda w: w()))
    return evals, logs


@pytest.fixture
def passes(monkeypatch: pytest.MonkeyPatch) -> list[frozenset[str]]:
    seen: list[frozenset[str]] = []

    def fake_run_once(conn: Any, ours: str | None, **kw: Any) -> RunSummary:
        seen.append(frozenset(kw["targets"]))
        return RunSummary(ours, {"dealer": 2}, 1, notes=("our team id is unknown",) if ours is None else ())

    monkeypatch.setattr(inline, "run_once", fake_run_once)
    return seen


def test_a_pass_starts_every_n_ticks_counted_from_the_first_tick(passes: list[frozenset[str]]) -> None:
    evals, logs = make(every=3)
    started = [tick for tick in (100, 101, 102, 103, 104, 106, 107, 109) if evals.after_tick(tick)]
    assert started == [103, 106, 109]  # none at boot: a short CLI run never scores
    assert passes == [frozenset({"dealer", "trade"})] * 3
    assert logs[0] == "evals (taker): dealer 2 · 1 new/changed · phoenix off: 0 annotated"


def test_a_pass_still_running_is_never_doubled(passes: list[frozenset[str]]) -> None:
    pending: list[Callable[[], None]] = []
    evals, _ = make(every=2, start=pending.append)
    assert [evals.after_tick(t) for t in (10, 12, 14)] == [False, True, False]  # 14: the 12 pass still runs
    pending.pop()()  # it finishes
    assert evals.after_tick(16) is True and len(pending) == 1


def test_a_failing_pass_is_logged_and_the_next_one_still_runs() -> None:
    def down() -> Any:
        raise psycopg.OperationalError("no route to host")

    evals, logs = make(every=2, connect=down)
    evals.after_tick(1)
    assert evals.after_tick(3) is True  # the agent's tick never sees the error
    assert logs == ["evals (taker): pass failed (OperationalError); next one in 2 ticks"]
    assert evals.after_tick(5) is True


def test_a_thread_that_cannot_start_costs_only_that_pass(passes: list[frozenset[str]]) -> None:
    def refuse(work: Callable[[], None]) -> None:
        raise RuntimeError("can't start new thread")

    evals, logs = make(every=1, start=refuse)
    evals.after_tick(1)
    assert evals.after_tick(2) is False and "could not start a pass (RuntimeError)" in logs[0]


@pytest.mark.parametrize(("agent", "every"), [("taker", 0), ("monitor", 3)])
def test_off_or_an_agent_without_targets_never_scores(agent: str, every: int, passes: list[frozenset[str]]) -> None:
    evals, _ = make(agent=agent, every=every)
    assert not any(evals.after_tick(t) for t in range(1, 20)) and passes == []


def test_each_agent_scores_only_its_own_targets() -> None:
    assert {
        "duels": frozenset({"duel"}),
        "taker": frozenset({"dealer", "trade"}),
        "maker": frozenset({"market_test"}),
    } == TARGETS_BY_AGENT
    every = [t for targets in TARGETS_BY_AGENT.values() for t in targets]
    assert len(every) == len(set(every)) == 4  # each target scored by exactly one agent


def test_the_default_start_runs_the_pass_on_a_daemon_thread(passes: list[frozenset[str]]) -> None:
    done = threading.Event()
    evals = TickEvals("maker", 1, Conn, lambda conn: None, lambda: None, lambda m: done.set())
    evals.after_tick(1)
    assert evals.after_tick(2) is True
    assert done.wait(5) and passes == [frozenset({"market_test"})]


class Recorder:
    def __init__(self) -> None:
        self.ticks: list[int] = []

    def after_tick(self, tick: int) -> bool:
        self.ticks.append(tick)
        return False


def test_the_taker_calls_its_evals_after_each_tick(agent_cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    team, cli = agent_cli
    made: dict[str, Any] = {}

    def fake(agent: str, every: int, log: Any) -> Recorder:
        made.update(agent=agent, every=every, recorder=Recorder())
        return made["recorder"]

    monkeypatch.setattr(cli, "_tick_evals", fake)
    args = ["agent", "taker", "--max-ticks", "1", "--no-jev", "--threads", "0", "--evals-every", "4"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert (made["agent"], made["every"], len(made["recorder"].ticks)) == ("taker", 4, 1)
    assert "evals every 4 ticks" in result.output and team.sent == []


def test_the_duel_player_calls_its_evals_after_each_tick(duel_cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cli, client = duel_cli[0], duel_cli[1]
    live_only = client.duels

    def duels(done: bool = False) -> dict[str, Any]:
        if done:
            raise RuntimeError("finished-duel read failed")  # any error after the sends
        return dict(live_only())

    monkeypatch.setattr(client, "duels", duels)
    recorder = Recorder()
    monkeypatch.setattr(cli, "_tick_evals", lambda agent, every, log: recorder if agent == "duels" else None)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert len(recorder.ticks) == 1 and "evals every - ticks" in output  # log only: a dry run does not score
    # its first tick reads finished duels; that bookkeeping failing never cuts the tick short
    assert "/api/duels?done=true failed (RuntimeError)" in output


def test_the_cli_builds_each_agents_evals(monkeypatch: pytest.MonkeyPatch) -> None:
    from bazaar_agent import cli

    evals = cli._tick_evals("maker", 6, lambda m: None)
    assert isinstance(evals, TickEvals) and (evals.agent, evals.every_ticks, evals.targets) == (
        "maker",
        6,
        frozenset({"market_test"}),
    )


def test_against_the_simulator_no_score_reaches_the_real_phoenix(monkeypatch: pytest.MonkeyPatch) -> None:
    from bazaar_agent import cli
    from bazaar_agent.config import Settings
    from bazaar_agent.evals import cli as evals_cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(simulated=True))
    monkeypatch.setattr(evals_cli, "load_settings", lambda: Settings(simulated=True))
    logs: list[str] = []
    evals = cli._tick_evals("duels", 6, logs.append)
    assert evals._annotator() is None and evals._annotator() is None
    assert logs == ["evals (duels): simulator, scores stay in Postgres only"]  # said once
    assert evals_cli._annotator(True) is None


def test_a_notes_line_is_logged_once_and_a_tick_going_back_restarts_the_count(passes: list[frozenset[str]]) -> None:
    logs: list[str] = []
    evals = TickEvals("taker", 2, Conn, lambda conn: None, lambda: None, logs.append, lambda w: w())
    for tick in (10, 12, 14):
        evals.after_tick(tick)
    assert logs.count("evals (taker): our team id is unknown") == 1 and len(passes) == 2
    assert evals.after_tick(3) is False and evals.after_tick(5) is True  # a simulator restarted at tick 0


def test_a_hung_pass_never_holds_the_tick_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    release = threading.Event()

    def hang(conn: Any, ours: str | None, **kw: Any) -> RunSummary:
        release.wait(10)
        return RunSummary(ours, {}, 0)

    monkeypatch.setattr(inline, "run_once", hang)
    evals = TickEvals("duels", 1, Conn, lambda conn: "t01", lambda: None, lambda m: None)  # the real daemon thread
    evals.after_tick(1)
    started = time.perf_counter()
    assert evals.after_tick(2) is True
    assert [evals.after_tick(t) for t in range(3, 10)] == [False] * 7  # still running: skipped, not queued
    assert time.perf_counter() - started < 0.5
    release.set()


def test_another_process_of_the_kind_holding_the_lock_skips_the_pass(passes: list[frozenset[str]]) -> None:
    conn = Conn(free=False)
    evals, logs = make(every=1, connect=lambda: conn)
    for tick in (1, 2, 3):
        evals.after_tick(tick)
    assert passes == [] and logs == ["evals (taker): another taker process is scoring; skipped 1x"]
    assert conn.autocommit is True and "set statement_timeout = 30000" in conn.sql
    assert "set idle_session_timeout = '2min'" in conn.sql  # a frozen holder loses its lock
    assert any("pg_try_advisory_lock" in q for q in conn.sql)


def test_simulated_traces_go_to_their_own_phoenix_project() -> None:
    from bazaar_agent import telemetry as tm

    assert tm.tracing_config({"BAZAAR_SIM": "local"}).project == "bazaar-sim"
    assert tm.tracing_config({"BAZAAR_SIM": "1", "PHOENIX_PROJECT": "team1"}).project == "team1-sim"
    assert tm.tracing_config({"BAZAAR_SIM": "0"}).project == "bazaar"
    assert tm.tracing_config({}).project == "bazaar"


def test_a_stuck_lock_stays_visible_every_tenth_skip(passes: list[frozenset[str]]) -> None:
    evals, logs = make(every=1, connect=lambda: Conn(free=False))
    for tick in range(1, 23):
        evals.after_tick(tick)
    assert logs == [f"evals (taker): another taker process is scoring; skipped {n}x" for n in (1, 11, 21)]


@pytest.mark.parametrize(
    ("asked", "trading", "every"), [(None, True, 6), (None, False, 0), (2, False, 2), (0, True, 0)]
)
def test_only_a_process_that_trades_scores_by_default(asked: int | None, trading: bool, every: int) -> None:
    from bazaar_agent.cli import evals_default

    assert evals_default(asked, trading) == every


def test_an_empty_bazaar_sim_in_the_environment_does_not_hide_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from bazaar_agent import telemetry as tm

    env_file = tmp_path / ".env"
    env_file.write_text("BAZAAR_SIM=1\n", encoding="utf-8")
    monkeypatch.setenv("BAZAAR_ENV_FILE", str(env_file))
    monkeypatch.setenv("BAZAAR_SIM", "")  # load_settings reads it as unset: the simulator
    monkeypatch.delenv("PHOENIX_PROJECT", raising=False)
    assert tm.tracing_config().project == "bazaar-sim"
