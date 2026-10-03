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
    def __enter__(self) -> Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


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
        return RunSummary(ours, {"dealer": 2}, 1)

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
    cli = duel_cli[0]
    recorder = Recorder()
    monkeypatch.setattr(cli, "_tick_evals", lambda agent, every, log: recorder if agent == "duels" else None)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert len(recorder.ticks) == 1 and "evals every 6 ticks" in output
    # its first tick reads finished duels; that bookkeeping failing never cuts the tick short
    assert "/api/duels?done=true failed (TypeError)" in output


def test_the_cli_builds_each_agents_evals(monkeypatch: pytest.MonkeyPatch) -> None:
    from bazaar_agent import cli

    evals = cli._tick_evals("maker", 6, lambda m: None)
    assert isinstance(evals, TickEvals) and (evals.agent, evals.every_ticks, evals.targets) == (
        "maker",
        6,
        frozenset({"market_test"}),
    )
