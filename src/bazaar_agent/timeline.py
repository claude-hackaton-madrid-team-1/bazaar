"""The organisers' timeline: every scheduled event in game hours and on the Madrid wall clock.

`GET /api/schedule` dates its events in game hours (`at_hours`), and `GET /api/clock` defines them as
"game hours since the opening; the clock stops overnight". A game hour therefore passes only while the
doors are open, at the wall clock's pace whatever the tick length (each tick adds its `tick_seconds`),
and a late opening shifts every later event. Friday opened at about 20:21 instead of 19:00 and froze at
tick 159 = game hour 2.65 (60 s ticks, `t_hours` = tick / 60), so unless the organisers jump the clock,
Saturday 09:00 is game hour 2.65, not the 4.0 that the schedule's `day_opens` event names.

So every event gets one wall time per anchor (a known game hour at a known wall time):
- `resume`: the clock resumes where it froze, at the next opening (a closed clock's `t_hours`).
- `jump`: the organisers align the clock, so the next `day_opens` event's `at_hours` falls on its `wall`.
- `live`: doors open, the clock's `t_hours` is now.

An event that falls on or after a day's closing waits for the next opening (no tick runs at a closing; the
last closing excepted); one before the anchor is `overdue` (it fires at the jump or never); one past the
last opening the clock knows is `never`. The calendar actions (`day_opens`, `day_closes`) carry their own
`wall` and keep it: the doors follow the wall clock.
Session lengths: a Market Test lasts its `ticks`; a duel session runs `rounds × 2 × (teams − 1)` duels per
team (practice: 306 duels for 18 teams), `max_concurrent` at a time, each up to `duel_ticks`, so at most
`⌈duels / max_concurrent⌉ × duel_ticks` ticks (earlier deals free a slot sooner).

Read-only: files in, text or JSON out. Nothing here holds a key or writes anywhere.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

FIXTURES = Path("tests/fixtures/api")
SCHEDULE_FIXTURE = FIXTURES / "get_api_schedule.anon.json"
CLOCK_FIXTURE = FIXTURES / "get_api_clock.anon.json"
TEAMS = 18  # Friday's feed: t01-t18 (team.joined + level.unlocked)
FRIDAY_FREEZE_T = 2.65  # tick 159 at 22:59:47 Madrid (feed_events.received_at), 60 s ticks


@dataclass(frozen=True)
class Day:
    name: str
    opens: datetime
    closes: datetime
    tick_seconds: float

    @property
    def hours(self) -> float:
        return (self.closes - self.opens).total_seconds() / 3600.0


@dataclass(frozen=True)
class Event:
    at_hours: float
    action: str
    note: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    wall: datetime | None = None  # the organisers' own wall time, on the calendar actions

    @property
    def name(self) -> str:
        return str(self.params.get("name") or self.note.split(": ")[0] or self.action)


@dataclass(frozen=True)
class Anchor:
    name: str  # resume | jump | live
    t_hours: float
    wall: datetime


@dataclass(frozen=True)
class Slot:
    """Where one event lands under one anchor."""

    status: str  # scheduled | past | overdue | never
    start: datetime | None = None
    end: datetime | None = None
    day: str | None = None


@dataclass(frozen=True)
class Row:
    event: Event
    duration_ticks: int
    slots: Mapping[str, Slot]


def body(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """A fixture wraps the response as {"status", "body", ...}; the API returns the body itself."""
    inner = doc.get("body")
    return inner if isinstance(inner, Mapping) and "status" in doc else doc


def load(path: Path) -> Mapping[str, Any]:
    return body(json.loads(path.read_text(encoding="utf-8")))


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def parse_days(clock: Mapping[str, Any]) -> list[Day]:
    days = []
    for d in body(clock).get("days") or []:
        opens, closes = _dt(d.get("opens")), _dt(d.get("closes"))
        if opens and closes:
            days.append(Day(str(d.get("day") or d.get("name")), opens, closes, float(d.get("tick_seconds") or 60.0)))
    return sorted(days, key=lambda d: d.opens)


def parse_events(schedule: Mapping[str, Any]) -> list[Event]:
    events = [
        Event(
            at_hours=float(e["at_hours"]),
            action=str(e.get("action", "")),
            note=str(e.get("note", "")),
            params=dict(e.get("params") or {}),
            wall=_dt(e.get("wall")),
        )
        for e in body(schedule).get("upcoming") or []
    ]
    return sorted(events, key=lambda e: e.at_hours)


def day_at(wall: datetime, days: Sequence[Day]) -> Day | None:
    return next((d for d in days if d.opens <= wall < d.closes), None)


def wall_at(t_hours: float, anchor: Anchor, days: Sequence[Day]) -> datetime | None:
    """The wall time of game hour `t_hours`: walk the open hours forward from the anchor.

    None before the anchor or past the last opening. A game hour that falls exactly on a closing waits
    for the next opening: no tick runs at the closing time.
    """
    remaining = (t_hours - anchor.t_hours) * 3600.0
    if remaining < -1e-6:
        return None
    cursor = anchor.wall
    for d in days:
        if d.closes <= cursor:
            continue
        start = max(cursor, d.opens)
        available = (d.closes - start).total_seconds()
        if remaining < available or (d is days[-1] and remaining - available < 1e-6):
            return start + timedelta(seconds=max(0.0, remaining))
        remaining -= available
        cursor = d.closes
    return None


def team_duels(params: Mapping[str, Any], teams: int = TEAMS) -> int:
    """Duels per team in a session: every rival twice per round, once as seller and once as buyer."""
    return int(params.get("rounds") or 1) * 2 * (teams - 1)


def duration_ticks(event: Event, teams: int = TEAMS) -> int:
    """A session's length in ticks (an upper bound for duels); 0 for an instant action."""
    p = event.params
    if event.action == "bench":
        return int(p.get("ticks") or 0)
    if event.action == "duels":
        waves = math.ceil(team_duels(p, teams) / max(1, int(p.get("max_concurrent") or 1)))
        return waves * int(p.get("duel_ticks") or 0)
    return 0


def anchors(clock: Mapping[str, Any], events: Iterable[Event], now: datetime) -> list[Anchor]:
    """`live` while the doors are open; otherwise `resume` (the frozen clock) and `jump` (the calendar)."""
    c = body(clock)
    t = float(c.get("t_hours") or 0.0)
    if c.get("doors", "open") == "open" and not c.get("paused"):
        return [Anchor("live", t, now)]
    opens = _dt(c.get("next_opens"))
    if opens is None:
        return []
    found = [Anchor("resume", t, opens)]
    jump = next((e for e in events if e.action == "day_opens" and e.wall and e.wall >= opens), None)
    if jump is not None and jump.wall is not None:
        found.append(Anchor("jump", jump.at_hours, jump.wall))
    return found


def frozen(clock: Mapping[str, Any], t_hours: float, now: datetime) -> dict[str, Any]:
    """A copy of `clock` with the doors closed at `t_hours` and the next opening after `now`."""
    c = dict(body(clock))
    nxt = next((d.opens for d in parse_days(c) if d.opens > now), None)
    c.update(doors="closed", t_hours=t_hours, next_opens=nxt.isoformat() if nxt else None)
    return c


def slot(event: Event, anchor: Anchor, days: Sequence[Day], ticks: int) -> Slot:
    if event.wall is not None:  # a calendar action: the doors follow the wall clock, not the game hour
        status = "scheduled" if event.wall >= anchor.wall else "past"
        return Slot(status, event.wall, event.wall, str(event.params.get("day") or "") or None)
    if event.at_hours < anchor.t_hours - 1e-6:
        return Slot("overdue")
    start = wall_at(event.at_hours, anchor, days)
    if start is None:
        return Slot("never")
    day = day_at(start, days)
    tick_s = day.tick_seconds if day else 60.0
    end = start + timedelta(seconds=ticks * tick_s)
    if day is not None and end > day.closes:  # the clock stops overnight: the session ends next morning
        end = wall_at(event.at_hours + ticks * tick_s / 3600.0, anchor, days) or end
    return Slot("scheduled", start, end, day.name if day else None)


def timeline(events: Iterable[Event], days: Sequence[Day], found: Sequence[Anchor], teams: int = TEAMS) -> list[Row]:
    rows = []
    for e in events:
        ticks = duration_ticks(e, teams)
        rows.append(Row(e, ticks, {a.name: slot(e, a, days, ticks) for a in found}))
    return rows


def game_hour_at(wall: datetime, anchor: Anchor, days: Sequence[Day]) -> float | None:
    """The inverse of `wall_at`: the game hour reached at `wall` (None before the anchor)."""
    if wall < anchor.wall:
        return None
    seconds, cursor = 0.0, anchor.wall
    for d in days:
        if d.closes <= cursor:
            continue
        start = max(cursor, d.opens)
        if wall <= start:
            break
        seconds += (min(wall, d.closes) - start).total_seconds()
        cursor = d.closes
        if wall <= d.closes:
            break
    return round(anchor.t_hours + seconds / 3600.0, 4)


def _hhmm(value: datetime | None) -> str:
    return value.strftime("%a %H:%M") if value else "-"


def as_dict(rows: Sequence[Row], found: Sequence[Anchor], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "anchors": [{"name": a.name, "t_hours": a.t_hours, "wall": a.wall.isoformat()} for a in found],
        "events": [
            {
                "at_hours": r.event.at_hours,
                "action": r.event.action,
                "name": r.event.name,
                "note": r.event.note,
                "params": dict(r.event.params),
                "duration_ticks": r.duration_ticks,
                "slots": {
                    k: {
                        "status": s.status,
                        "start": s.start.isoformat() if s.start else None,
                        "end": s.end.isoformat() if s.end else None,
                        "day": s.day,
                    }
                    for k, s in r.slots.items()
                },
            }
            for r in rows
        ],
    }


def render(rows: Sequence[Row], found: Sequence[Anchor]) -> list[str]:
    """Plain text lines: one row per event, one start-end column per anchor."""
    names = [a.name for a in found]
    head = "  h      action        " + "".join(f"{n:<22}" for n in names) + "event"
    lines = [head]
    for r in rows:
        cols = ""
        for n in names:
            s = r.slots[n]
            if s.status != "scheduled":
                cols += f"{s.status:<22}"
            elif r.duration_ticks and s.end:
                cols += f"{_hhmm(s.start)}-{s.end.strftime('%H:%M'):<12}"
            else:
                cols += f"{_hhmm(s.start):<22}"
        lines.append(f"{r.event.at_hours:6.2f}  {r.event.action:<13} {cols}{r.event.note}")
    return lines


def with_plays(doc: Mapping[str, Any], plays: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the playbook's steps: each event gets the first play whose `match` keys all equal its own.

    `plays` = {"events": [{"match": {"action": "bench", "at_hours": 16.0}, ...}], ...}; every other
    top-level key (standing plays, gates) is copied as is.
    """
    out = {**doc, **{k: v for k, v in plays.items() if k != "events"}}
    rules = list(plays.get("events") or [])
    events = []
    for e in doc.get("events") or []:
        hit = next((p for p in rules if all(e.get(k) == v for k, v in (p.get("match") or {}).items())), None)
        events.append({**e, "play": {k: v for k, v in hit.items() if k != "match"}} if hit else dict(e))
    out["events"] = events
    return out
