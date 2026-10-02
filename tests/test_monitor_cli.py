"""`bazaar monitor` with the live stream, and the us-vs-competition views, through the real CLI.

Fakes only (no network): `FakeStream` emits what a real connection would, `TwoTicks` serves tick 120
and then tick 121 a moment later, so the stream's events are handled BETWEEN the two ticks.
"""

import json

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent.config import Settings
from bazaar_agent.stream import Note
from tests.test_intel import EVENTS
from tests.test_telemetry_cli import FEED, FakePublic, FakeStream, named

WIDE = {"COLUMNS": "250"}  # one console line per printed line
ANNOUNCE = {
    "id": 43,
    "tick": 120,
    "type": "announcement",
    "scope": "public",
    "actor": "admin",
    "payload": {"text": "El Chato opens to all"},
}
OUR_LISTING = {
    "id": 44,
    "tick": 120,
    "type": "offer.listed",
    "scope": "public",
    "actor": "t01",
    "payload": {"venue": "rastro", "offer": {"id": 900, "maker": "t01"}},
}
TICK = {"id": 45, "tick": 121, "type": "tick", "scope": "public", "actor": "", "payload": {"tick": 121}}
BOARD = [
    {"id": 123, "maker": "p-ours", "give": {"assets": [{"ref": "LAT-05"}]}, "want": {"cash": 10}},
    {"id": 124, "maker": "p-zz", "give": {"cash": 30}, "want": {"types": ["card:LAV-09"]}},
]  # fmt: skip


class TwoTicks(FakePublic):
    """Tick 120, then tick 121 0.5 s later; the window at 121 holds what the stream already delivered."""

    def __init__(self):
        self.reads = 0

    def clock(self):
        self.reads += 1
        return {"tick": 120 if self.reads == 1 else 121, "next_tick_in": 0.2, "tick_seconds": 60}

    def feed_window(self, limit):
        return list(FEED) + ([ANNOUNCE, OUR_LISTING] if self.reads > 1 else [])

    def board(self, venue):
        return {"offers": BOARD}


@pytest.fixture
def run(monkeypatch, tmp_path):
    public = TwoTicks()
    streams: list[FakeStream] = []

    def open_stream(settings, emit):
        streams.append(FakeStream(emit, [Note("stream live (scope team:t01, tick 120)"), ANNOUNCE, OUR_LISTING, TICK]))
        return streams[-1]

    def invoke(*args, team="t01"):
        monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path, team_id=team))
        monkeypatch.setattr(cli, "public_client", lambda settings: public)
        monkeypatch.setattr(cli, "open_stream", open_stream)
        monkeypatch.setattr(cli, "_events", lambda live: list(EVENTS))
        result = CliRunner().invoke(cli.app, list(args), env=WIDE)
        return result, result.output.splitlines()

    invoke.streams, invoke.data_dir = streams, tmp_path
    return invoke


def test_stream_events_are_handled_between_ticks_and_alert_once(run, spans):
    result, lines = run("monitor", "--no-db", "--max-ticks", "2", "--show-events")
    assert result.exit_code == 0, result.output
    order = [next(i for i, line in enumerate(lines) if marker in line) for marker in (
        "tick 120: +2 events by poll",
        "stream live (scope team:t01",
        "stream #43 tick 120 announcement admin",
        "stream #44 tick 120 offer.listed t01 (us)",
        "ALERT tick 120 feed:announcement admin: El Chato opens to all (via stream",  # after its burst
        "tick 121: +0 events by poll · stream live: +2 live, stream ahead on 2 events by median",
    )]  # fmt: skip
    assert order == sorted(order)  # the stream's events land between tick 120 and tick 121
    assert not any("ALERT" in line and "t01" in line for line in lines)  # our own listing is not news
    stored = [json.loads(x) for x in (run.data_dir / "alerts.jsonl").read_text().splitlines()]
    assert [a["detail"] for a in stored] == ["El Rata is coming", "El Chato opens to all"]  # 41 by poll, 43 once
    assert run.streams[0].nudges == 2 and run.streams[0].state == "stopped"
    (burst,) = named(spans, "monitor stream")
    assert (burst.attributes["bazaar.stream.new"], burst.attributes["bazaar.stream.received"]) == (2, 3)
    tick_121 = next(s for s in named(spans, "monitor tick") if s.name == "monitor tick 121")
    assert (tick_121.attributes["bazaar.stream.confirmed"], tick_121.attributes["bazaar.stream.poll_only"]) == (2, 0)


def test_no_stream_means_poll_only(run):
    result, lines = run("monitor", "--no-db", "--max-ticks", "1", "--no-stream")
    assert result.exit_code == 0 and run.streams == []
    assert any("stream off: poll only" in line for line in lines)
    assert any("tick 120: +2 events (id 42)" in line for line in lines)


def test_teams_shows_the_competition_without_us_and_us_apart(run):
    result, lines = run("teams", team="t06")
    assert result.exit_code == 0, result.output
    us_table = next(i for i, line in enumerate(lines) if "Us · t06 (not counted as competition)" in line)
    competition = "\n".join(lines[:us_table])
    assert "t05" in competition and "t06" not in competition
    assert "t06 (us)" in "\n".join(lines[us_table:])
    mixed, mixed_lines = run("teams", "--include-us", team="t06")
    assert "t06 (us)" in mixed.output and "Us ·" not in mixed.output


def test_curves_filter_ours_and_theirs_and_tag_ours(run):
    _, every = run("curves", "--threads", "5", team="t06")
    assert any("LAV-07" in line and " 1 " in line for line in every)  # the 'ours' column counts our thread
    ours, _ = run("curves", "--ours", team="t06")
    assert "our threads" in ours.output and "LAV-07" in ours.output and "sobre_barrio" not in ours.output
    theirs, _ = run("curves", "--theirs", team="t06")
    assert "sobre_barrio" in theirs.output and "LAV-07" not in theirs.output
    both, _ = run("curves", "--ours", "--theirs", team="t06")
    assert both.exit_code == 1 and "pick one of" in both.output


def test_curves_ours_needs_our_team_id(run):
    result, _ = run("curves", "--ours", team=None)
    assert result.exit_code == 1 and "need our team id" in result.output


def test_book_shows_our_offers_apart(run):
    result, lines = run("book", team="t06")
    assert result.exit_code == 0, result.output
    ours_at = next(i for i, line in enumerate(lines) if "Our offers" in line)
    assert "LAV-09" in "\n".join(lines[:ours_at]) and "LAT-05" not in "\n".join(lines[:ours_at])
    assert "t06 (us)" in "\n".join(lines[ours_at:])
