"""The taker's watchdog hook: after the tick's sends, live only, behind `live_watchdog_enabled`, never fatal."""

from __future__ import annotations

from tests.agent_fakes import FakePublic, FakeTeam, ask, clock
from tests.test_taker import taker


class SpyWatchdog:
    def __init__(self, team: FakeTeam, fail: bool = False) -> None:
        self.team, self.fail = team, fail
        self.calls: list[tuple[int, list[tuple]]] = []

    def tick(self, tick, rules) -> None:
        self.calls.append((tick, list(self.team.sent)))  # what was already sent when the watchdog ran
        if self.fail:
            raise RuntimeError("watchdog bug")


def _board():
    return FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})


def test_the_watchdog_runs_after_the_ticks_sends(tmp_path):
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, _board(), live=True, live_watchdog_enabled=True)
    spy = t.watchdog = SpyWatchdog(team)
    t.on_tick(clock())
    assert team.sent == [("accept", 1)]
    assert spy.calls == [(clock().tick, [("accept", 1)])]


def test_a_watchdog_error_never_breaks_the_tick(tmp_path):
    team = FakeTeam()
    t, lines, _ = taker(tmp_path, team, _board(), live=True, live_watchdog_enabled=True)
    t.watchdog = SpyWatchdog(team, fail=True)
    t.on_tick(clock())  # must not raise
    assert team.sent == [("accept", 1)]
    assert any("watchdog" in line and "RuntimeError" in line for line in lines)


def test_disabled_or_dry_run_never_calls_the_watchdog(tmp_path):
    team = FakeTeam()
    off, _, _ = taker(tmp_path / "off", team, _board(), live=True, live_watchdog_enabled=False)
    spy = off.watchdog = SpyWatchdog(team)
    off.on_tick(clock())
    assert spy.calls == []

    dry_team = FakeTeam()
    dry, _, _ = taker(tmp_path / "dry", dry_team, _board(), live=False, live_watchdog_enabled=True)
    dry_spy = dry.watchdog = SpyWatchdog(dry_team)
    dry.on_tick(clock())
    assert dry_spy.calls == []


def test_the_default_watchdog_has_no_database_in_unit_tests(tmp_path):
    # The JSONL decision log has no Postgres: the real watchdog is built but has nothing to read.
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, _board(), live=True, live_watchdog_enabled=True)
    t.on_tick(clock())
    assert team.sent == [("accept", 1)]
