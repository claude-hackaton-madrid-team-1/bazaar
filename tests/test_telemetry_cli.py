"""CLI tracing through the real `bazaar` app (typer CliRunner, fakes, in-memory spans, no network):
the monitor's tick spans, every console line mirrored into spans, the thread views, failures."""

import json
import re

import pytest
from opentelemetry.trace import StatusCode
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import telemetry as tm
from bazaar_agent.config import Settings
from bazaar_agent.sdk import BazaarError

FEED = [
    {"id": 41, "tick": 120, "type": "announcement", "actor": "calendar", "payload": {"text": "El Rata is coming"}},
    {"id": 42, "tick": 120, "type": "offer.listed", "actor": "t05", "payload": {}},
]
THREAD = {
    "id": 115,
    "kind": "persona",
    "team": "t01",
    "with": "abuela",
    "topic": {"buy": {"card": "LAV-06"}},
    "item": "La Tabacalera",
    "status": "deal",
    "closed_reason": None,
    "messages": [
        {"id": 788, "tick": 68, "sender": "t01", "text": "¿22 le parece justo?",
         "offer": {"id": 970, "maker": "t01", "status": "settled", "give": {"cash": 22}, "want": {"cash": 0}}},
        {"id": 790, "tick": 69, "sender": "abuela", "text": "Venga, 22 P. [red]You drive a hard bargain[/red]",
         "offer": None},
    ],
    "standing_offers": [],
}  # fmt: skip


class FakePublic:
    def clock(self):
        return {"tick": 120, "next_tick_in": 30, "tick_seconds": 60}

    def feed_window(self, limit):
        return list(FEED)

    def dealers(self):
        return {"personas": [{"id": "abuela", "name": "Abuela Carmen", "status": "active", "level": 1, "menu": {}}]}

    def levels(self):
        return {"levels": []}


class FakeTeam:
    def __init__(self, refuse=False):
        self.refuse = refuse

    def thread(self, tid):
        if self.refuse:
            raise BazaarError("not_found", "no such thread", 404)
        return dict(THREAD)

    def my_threads(self, status=None):
        return {"threads": [dict(THREAD)]}


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic())
    monkeypatch.setattr(cli, "team_client", lambda settings: FakeTeam())
    hooks = len(cli.console._render_hooks)
    yield CliRunner()
    while len(cli.console._render_hooks) > hooks:
        cli.console.pop_render_hook()


def named(spans, prefix):
    return [s for s in spans.get_finished_spans() if s.name.startswith(prefix)]


def lines_of(span, name="console"):
    return [e.attributes["line"] for e in span.events if e.name == name]


def test_a_monitor_tick_is_one_span_with_counts_alerts_traders_and_console_lines(spans, runner):
    result = runner.invoke(cli.app, ["monitor", "--no-db", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output

    (tick,) = named(spans, "monitor tick")
    assert tick.parent is None and tick.name == "monitor tick 120"
    a = tick.attributes
    assert (a["bazaar.feed.new"], a["bazaar.new_events"], a["bazaar.dealers"], a["bazaar.feed.gap_possible"]) == (
        2,
        2,
        1,
        False,
    )
    alerts = [e.attributes for e in tick.events if e.name == "alert"]
    assert [(x["kind"], x["detail"]) for x in alerts] == [("feed:announcement", "El Rata is coming")]
    traders = {e.attributes["trader_id"]: e.attributes["change"] for e in tick.events if e.name == "trader"}
    assert traders == {"abuela": "new", "t05": "new"}
    console = lines_of(tick)
    assert any(line.startswith("ALERT tick 120 feed:announcement") for line in console)
    assert any("tick 120: +2 events" in line for line in console)
    assert {e.attributes["bazaar.command"] for e in tick.events if e.name == "console"} == {"monitor"}
    (command,) = named(spans, "cli monitor")
    assert any(line.startswith("monitor: feed →") for line in lines_of(command))


def test_a_db_failure_is_recorded_and_the_tick_goes_on(spans, runner, monkeypatch):
    from bazaar_agent import db

    def down(*args, **kwargs):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(db, "connect_ready", down)
    result = runner.invoke(cli.app, ["monitor", "--db", "--max-ticks", "1"])
    assert result.exit_code == 0 and "Postgres unavailable" in result.output
    (tick,) = named(spans, "monitor tick")
    failure = next(e for e in tick.events if e.name == "exception").attributes
    assert failure["exception.type"] == "ConnectionError" and "postgres is down" in failure["exception.stacktrace"]
    assert any("tick 120: +2 events" in line for line in lines_of(tick))  # the tick finished


def test_console_output_is_identical_with_tracing_off_and_on(spans, runner, monkeypatch):
    def strip_clock(text):
        return re.sub(r"\d\d:\d\d:\d\d", "HH:MM:SS", text)

    traced = runner.invoke(cli.app, ["monitor", "--no-db", "--max-ticks", "1"]).output
    tm.uninstall()
    monkeypatch.setattr(tm, "tracing_config", lambda env=None: tm.TracingConfig(False, "http://x/v1/traces", "p"))
    hooks = len(cli.console._render_hooks)
    plain = runner.invoke(cli.app, ["monitor", "--no-db", "--max-ticks", "1"], catch_exceptions=False).output
    assert len(cli.console._render_hooks) == hooks  # off: no hook was installed
    assert strip_clock(traced).splitlines()[:3] == strip_clock(plain).splitlines()[:3]


def test_thread_prints_the_conversation_and_traces_it(spans, runner):
    result = runner.invoke(cli.app, ["thread", "115"])
    assert result.exit_code == 0, result.output
    assert "[red]" in result.output and "[/red]" in result.output  # their markup is shown, never run
    (view,) = named(spans, "thread.view")
    assert [e.attributes["sender"] for e in view.events if e.name == "message"] == ["t01", "abuela"]
    (command,) = named(spans, "cli thread")
    assert any("Thread 115" in line for line in lines_of(command))


def test_thread_and_threads_json_for_the_ui(spans, runner):
    one = json.loads(runner.invoke(cli.app, ["thread", "115", "--json"]).output)
    assert one["thread"]["ref"] == "LAV-06" and [m["price"] for m in one["messages"]] == [22, None]
    many = json.loads(runner.invoke(cli.app, ["threads", "--json"]).output)
    assert many["threads"][0]["messages"] == 2 and many["threads"][0]["last"]["sender"] == "abuela"


def test_a_failing_command_marks_its_span_error(spans, runner, monkeypatch):
    monkeypatch.setattr(cli, "team_client", lambda settings: FakeTeam(refuse=True))
    result = runner.invoke(cli.app, ["thread", "999"])
    assert result.exit_code == 1 and "refused: not_found" in result.output
    (command,) = named(spans, "cli thread")
    assert command.status.status_code is StatusCode.ERROR
    assert any("refused: not_found" in line for line in lines_of(command))
