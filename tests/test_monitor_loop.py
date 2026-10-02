"""`MonitorLoop` with a fake Postgres and fake clients: what each tick and each stream burst writes,
and how refusals and a failed write leave the loop running. No network, no database."""

from bazaar_agent import db
from bazaar_agent.agents.monitoring import MonitorLoop, Options
from bazaar_agent.feed import FeedStore
from bazaar_agent.monitor import Watcher
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import Clock
from tests.test_telemetry_cli import FEED, FakePublic


class FakeConnection:
    closed = False

    def close(self):
        self.closed = True


class Recorder:
    """Stands in for the `db` module's writers: records every call, fails on demand."""

    def __init__(self, monkeypatch, fail=False):
        self.calls: list[tuple] = []
        self.fail = fail
        for name in ("load_events", "upsert_traders", "save_snapshot", "insert_alerts", "load_history"):
            monkeypatch.setattr(db, name, self._record(name))
        monkeypatch.setattr(db, "connect_ready", lambda app: FakeConnection())

    def _record(self, name):
        def call(cx, *args):
            if self.fail:
                raise RuntimeError("disk full")
            self.calls.append((name, *args))
            return name != "load_history" or args[0] is not None

        return call

    def names(self):
        return [c[0] for c in self.calls]


class Team:
    def __init__(self, me=None, refuse=False):
        self.answer, self.refuse = me or {"id": "t01", "cash": 312, "level": 2, "score": {"score": 11.3}}, refuse

    def me(self):
        if self.refuse:
            raise BazaarError("rate_limited", "slow down", 429)
        return self.answer


class Refusing(FakePublic):
    def feed_window(self, limit):
        raise BazaarError("rate_limited", "slow down", 429)

    def dealers(self):
        raise BazaarError("rate_limited", "slow down", 429)


def make(tmp_path, public=None, team=None, ours="t01", **options):
    said: list[str] = []
    watcher = Watcher(FeedStore(tmp_path / "feed"), ours)
    loop = MonitorLoop(public or FakePublic(), team, watcher, tmp_path, Options(**options), said.append)
    return loop, said


def clock(tick):
    return Clock(tick=tick, next_tick_in=30)


def test_a_tick_writes_events_traders_snapshot_alerts_and_history(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    loop, said = make(tmp_path, team=Team())
    loop.on_tick(clock(120))
    assert rec.names() == ["load_events", "upsert_traders", "save_snapshot", "insert_alerts", "load_history"]
    assert [e["id"] for e in rec.calls[0][1]] == [41, 42]
    assert rec.calls[-1][-1] == "t01"  # the history rebuild tags our threads
    assert "cash 312 lvl 2 score 11.3" in said[-1]


def test_a_stream_burst_writes_its_events_and_alerts_at_once(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    loop, said = make(tmp_path)
    loop.on_tick(clock(120))
    rec.calls.clear()
    news = {"id": 50, "tick": 120, "type": "level.activated", "actor": "admin", "payload": {"name": "El Rata"}}
    loop.on_stream([news])
    assert rec.names() == ["load_events", "insert_alerts"]
    assert any(line.startswith("[bold red]ALERT[/bold red] tick 120 feed:level.activated") for line in said)
    loop.on_stream([news])  # delivered again (the poll, a replay): nothing is written twice
    assert rec.names() == ["load_events", "insert_alerts", "load_events"] and rec.calls[-1][1] == []


def test_a_failed_write_is_reported_and_the_loop_keeps_going(tmp_path, monkeypatch):
    Recorder(monkeypatch, fail=True)
    loop, said = make(tmp_path)
    loop.on_tick(clock(120))
    assert any("tick 120: DB write failed (RuntimeError: disk full)" in line for line in said)
    loop.on_stream([{"id": 60, "tick": 120, "type": "thread.message", "actor": "t05", "payload": {}}])
    assert any("stream: DB write failed" in line for line in said)
    assert (tmp_path / "feed" / "feed.jsonl").read_text().count('"id":60') == 1  # JSONL kept it anyway


def test_refused_reads_keep_the_last_view_and_the_tick_finishes(tmp_path):
    loop, said = make(tmp_path, public=Refusing(), team=Team(refuse=True), db_enabled=False)
    loop.on_tick(clock(120))
    assert said[:3] == [
        "tick 120: feed refused rate_limited",
        "tick 120: dealers/levels refused rate_limited",
        "tick 120: /me refused rate_limited",
    ]
    assert "tick 120: +0 events" in said[-1] and "0 dealers" in said[-1]


def test_me_corrects_a_stale_team_id_and_tags_it_us(tmp_path):
    loop, said = make(tmp_path, team=Team(me={"id": "t01"}), ours="t09", db_enabled=False)
    loop.watcher.from_stream([{"id": 1, "tick": 1, "type": "offer.listed", "actor": "t01", "payload": {}}])
    loop.on_tick(clock(120))
    assert loop.watcher.ours == "t01" and loop.watcher.teams["t01"].status == "us"
    assert (tmp_path / "team_id").read_text().strip() == "t01"
    assert any("our team id is t01 (was t09)" in line for line in said)


def test_stream_notes_are_printed_as_text_never_as_markup(tmp_path):
    from bazaar_agent.stream import Note

    loop, said = make(tmp_path, db_enabled=False)
    loop.on_stream([Note("stream reader failed (ValueError: [red]boom[/red])")])
    assert said[0].endswith(r"stream reader failed (ValueError: \[red]boom\[/red])")


def test_notifications_strip_quotes_from_what_the_feed_controls(tmp_path, monkeypatch):
    from bazaar_agent.agents import monitoring
    from bazaar_agent.monitor import Alert

    sent: list[list[str]] = []
    monkeypatch.setattr(monitoring.subprocess, "run", lambda args, **kwargs: sent.append(args))
    loop, _ = make(tmp_path, notify=True, db_enabled=False)
    loop.raise_alerts([Alert(1, "feed:announcement", 'adm"in\\', "x")], "stream")
    assert sent == [["osascript", "-e", 'display notification "adm\'in: feed:announcement" with title "Bazaar"']]


def test_feed_window_from_the_fake_server_is_the_poll(tmp_path):
    loop, _ = make(tmp_path, db_enabled=False)
    loop.on_tick(clock(120))
    assert sorted(e["id"] for e in loop.watcher.store.events()) == [e["id"] for e in FEED]


def test_a_failing_stream_burst_is_reported_and_the_monitor_keeps_running(tmp_path, monkeypatch):
    loop, said = make(tmp_path, db_enabled=False)

    def broken(events):
        raise OSError("disk full")

    monkeypatch.setattr(loop.watcher, "from_stream", broken)
    loop.on_stream([{"id": 70, "tick": 120, "type": "thread.message", "actor": "t05", "payload": {}}])
    assert "stream burst failed (OSError: disk full)" in said[-1]
    loop.on_tick(clock(120))  # the next tick still runs
    assert "tick 120: +2 events" in said[-1]
