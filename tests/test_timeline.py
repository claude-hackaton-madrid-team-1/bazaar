"""The organisers' timeline on the wall clock: game hours pass only while the doors are open."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent import timeline as tl
from bazaar_agent.cli import app

NIGHT = datetime.fromisoformat("2026-10-03T03:30:00+02:00")


def at(text: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-0{text}+02:00")


@pytest.fixture(scope="module")
def friday() -> tuple[list[tl.Event], list[tl.Day], dict]:
    clock = dict(tl.load(tl.CLOCK_FIXTURE))
    return tl.parse_events(tl.load(tl.SCHEDULE_FIXTURE)), tl.parse_days(clock), clock


def rows_by(rows: list[tl.Row], action: str, at_hours: float) -> tl.Row:
    return next(r for r in rows if r.event.action == action and r.event.at_hours == at_hours)


@pytest.fixture(scope="module")
def saturday(friday) -> tuple[list[tl.Row], list[tl.Anchor]]:
    events, days, clock = friday
    found = tl.anchors(tl.frozen(clock, tl.FRIDAY_FREEZE_T, NIGHT), events, NIGHT)
    return tl.timeline(events, days, found), found


def test_fixture_wrapper_and_raw_body_both_load(friday):
    events, days, clock = friday
    assert tl.body({"status": 200, "body": {"x": 1}}) == {"x": 1}
    assert tl.body({"x": 1}) == {"x": 1}
    assert [d.name for d in days] == ["fri", "sat", "sun"]
    assert [d.tick_seconds for d in days] == [60.0, 30.0, 15.0]
    assert days[1].hours == 14.0
    assert events == sorted(events, key=lambda e: e.at_hours)


def test_a_closed_clock_gives_the_resume_and_the_jump_anchor(saturday):
    _, found = saturday
    assert [(a.name, a.t_hours, a.wall) for a in found] == [
        ("resume", 2.65, at("3T09:00:00")),
        ("jump", 4.0, at("3T09:00:00")),
    ]


def test_resume_shifts_saturday_by_the_friday_late_start(saturday):
    rows, _ = saturday
    resume = {(r.event.action, r.event.at_hours): r.slots["resume"] for r in rows}
    assert resume[("bench", 3.0)].start == at("3T09:21:00")  # never fired on Friday
    assert resume[("round", 4.0)].start == at("3T10:21:00")
    assert resume[("grant_all", 4.05)].start == at("3T10:24:00")
    assert resume[("duels", 6.5)].start == at("3T12:51:00")
    assert resume[("duels", 13.0)].start == at("3T19:21:00")
    assert resume[("bench", 16.0)].start == at("3T22:21:00")
    assert resume[("bench", 17.0)].start == at("4T09:21:00")  # past Saturday's 23:00 closing
    assert resume[("end_round", 24.0)].status == "never"  # Sunday ends at game hour 22.65


def test_jump_keeps_the_published_saturday(saturday):
    rows, _ = saturday
    jump = {(r.event.action, r.event.at_hours): r.slots["jump"] for r in rows}
    assert jump[("bench", 3.0)].status == "overdue"
    assert jump[("bench", 5.0)].start == at("3T10:00:00")
    assert jump[("duels", 6.5)].start == at("3T11:30:00")
    assert jump[("bench", 17.0)].start == at("3T22:00:00")
    assert jump[("round", 18.0)].start == at("4T09:00:00")  # no tick at the closing: next opening
    assert jump[("end_round", 24.0)].start == at("4T15:00:00")  # the last closing still counts


def test_calendar_actions_keep_their_own_wall(saturday):
    rows, _ = saturday
    closes_fri = rows_by(rows, "day_closes", 4.0)
    assert {s.status for s in closes_fri.slots.values()} == {"past"}
    opens_sat = rows_by(rows, "day_opens", 4.0)
    assert {s.start for s in opens_sat.slots.values()} == {at("3T09:00:00")}


def test_duel_sessions_last_waves_times_duel_ticks(friday, saturday):
    events, _, _ = friday
    practice = next(e for e in events if e.params.get("practice"))
    assert tl.team_duels(practice.params) == 34  # 306 duels in the feed = 18 × 34 / 2
    assert tl.duration_ticks(practice) == 6 * 12
    rows, _ = saturday
    duels_1 = rows_by(rows, "duels", 6.5)
    assert duels_1.duration_ticks == 12 * 16  # 34 duels, 3 at a time
    assert duels_1.slots["jump"].end == at("3T13:06:00")  # 192 ticks of 30 s
    duels_2 = rows_by(rows, "duels", 13.0)
    assert tl.team_duels(duels_2.event.params) == 68 and duels_2.duration_ticks == 12 * 16
    assert rows_by(rows, "bench", 5.0).slots["jump"].end == at("3T10:08:00")


def test_a_session_cut_by_the_closing_ends_next_morning(friday):
    _, days, _ = friday
    anchor = tl.Anchor("live", 10.0, at("3T22:55:00"))
    row = tl.timeline([tl.Event(10.0, "bench", params={"ticks": 16})], days, [anchor])[0]
    slot = row.slots["live"]
    # 10 ticks of 30 s before 23:00, the other 6 at Sunday's 15 s
    assert slot.start == at("3T22:55:00") and slot.end == at("4T09:01:30")
    lines = tl.render([row], [anchor])
    assert "Sat 22:55-Sun 09:01" in lines[1]


def test_wall_at_and_game_hour_at_are_inverse(friday):
    _, days, _ = friday
    anchor = tl.Anchor("resume", 2.65, at("3T09:00:00"))
    for h in (2.65, 3.0, 9.4, 16.64, 17.0, 20.0):
        wall = tl.wall_at(h, anchor, days)
        assert wall is not None and tl.game_hour_at(wall, anchor, days) == pytest.approx(h, abs=1e-4)
    assert tl.wall_at(2.0, anchor, days) is None
    assert tl.game_hour_at(at("3T08:00:00"), anchor, days) is None


def test_an_open_clock_anchors_now(friday):
    events, days, clock = friday  # the fixture: Friday, doors open, game hour 0.5167
    now = at("2T20:52:00")
    found = tl.anchors(clock, events, now)
    assert [(a.name, a.t_hours) for a in found] == [("live", 0.5167)]
    practice = tl.timeline(events, days, found)[0].slots["live"]
    assert practice.start is not None and practice.end is not None
    assert abs((practice.start - at("2T22:21:00")).total_seconds()) < 1  # it fired at 22:20-22:21
    # 72 ticks: ~39 of 60 s on Friday, the other ~33 at Saturday's 30 s
    assert practice.end.strftime("%a %H:%M") == "Sat 09:16"


def test_frozen_picks_the_next_opening(friday):
    _, _, clock = friday
    late = tl.frozen(clock, 9.0, at("3T23:30:00"))
    assert late["doors"] == "closed" and late["next_opens"] == "2026-10-04T09:00:00+02:00"


def test_cli_prints_both_columns_and_json():
    runner = CliRunner()
    args = ["timeline", "--frozen-at", "2.65", "--at", "2026-10-03T03:30:00+02:00"]
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert "resume: h2.65 = Sat 09:00" in out.output and "Sat 12:51-14:27" in out.output
    data = json.loads(runner.invoke(app, [*args, "--json"]).stdout)
    assert [a["name"] for a in data["anchors"]] == ["resume", "jump"]
    duels_2 = next(e for e in data["events"] if e["name"] == "Duels II")
    assert duels_2["slots"]["resume"]["start"] == "2026-10-03T19:21:00+02:00"


def test_plays_attach_by_match_and_keep_the_rest():
    doc = {
        "anchors": [],
        "events": [
            {"action": "bench", "at_hours": 16.0, "name": "The hard Market Test"},
            {"action": "bench", "at_hours": 5.0, "name": "The Market Test"},
            {"action": "grant_all", "at_hours": 4.05, "name": "El Retiro has arrived"},
        ],
    }
    plays = {
        "gates": [{"id": "G1"}],
        "events": [
            {"match": {"action": "bench", "at_hours": 16.0}, "do": "hard preset"},
            {"match": {"action": "bench"}, "do": "normal preset"},
        ],
    }
    out = tl.with_plays(doc, plays)
    assert out["gates"] == [{"id": "G1"}]
    assert [e.get("play") for e in out["events"]] == [{"do": "hard preset"}, {"do": "normal preset"}, None]


def test_the_committed_schedule_is_the_generated_one():
    """docs/night/saturday-schedule.json = the CLI on the fixtures + the plays file (regenerate on change)."""
    args = ["timeline", "--frozen-at", "2.65", "--at", "2026-10-03T08:55:00+02:00"]
    args += ["--plays", "docs/night/saturday-plays.json", "--json"]
    out = CliRunner().invoke(app, args)
    assert out.exit_code == 0, out.output
    committed = json.loads(Path("docs/night/saturday-schedule.json").read_text(encoding="utf-8"))
    assert json.loads(out.stdout) == committed
    saturday = [e for e in committed["events"] if e["at_hours"] < 18]
    assert len(saturday) == 17
    assert all("play" in e for e in saturday)


def test_cli_reads_an_offset_free_time_as_madrid():
    out = CliRunner().invoke(app, ["timeline", "--frozen-at", "2.65", "--at", "2026-10-03T08:55"])
    assert out.exit_code == 0, out.output
    assert "resume: h2.65 = Sat 09:00" in out.output


def test_cli_from_api_reads_the_two_public_bodies(monkeypatch):
    """`--from-api` makes two keyless GETs; the bodies come unwrapped, a closed clock gives both columns."""
    clock = tl.frozen(tl.load(tl.CLOCK_FIXTURE), 2.65, NIGHT)
    schedule = dict(tl.load(tl.SCHEDULE_FIXTURE))
    calls: list[str] = []

    class Public:
        def clock(self) -> dict:
            calls.append("clock")
            return clock

        def schedule(self) -> dict:
            calls.append("schedule")
            return schedule

    monkeypatch.setattr("bazaar_agent.cli.public_client", lambda settings: Public())
    out = CliRunner().invoke(app, ["timeline", "--from-api", "--at", "2026-10-03T08:55:00+02:00", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.stdout)
    assert sorted(calls) == ["clock", "schedule"] and data["source"] == "api"
    assert [(a["name"], a["t_hours"]) for a in data["anchors"]] == [("resume", 2.65), ("jump", 4.0)]
