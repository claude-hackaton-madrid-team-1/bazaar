"""The schedule watch: every official event once, then again as its lead time crosses 20, 10 and 3 ticks."""

import json

import pytest

from bazaar_agent.learn.store import LearningStore
from bazaar_agent.news import EVENTS_FILE, NewsSentinel
from bazaar_agent.schedule_watch import ScheduleWatch, events_from_levels, events_from_schedule, lead_ticks
from bazaar_agent.ticks import Clock

# The real /api/schedule and /api/levels shapes (Sat 3 Oct, h4.06), trimmed.
SCHEDULE = {
    "now_hours": 4.058,
    "upcoming": [
        {"at_hours": 5.0, "action": "bench", "note": "The Market Test: every venue gets the same synthetic book",
         "params": {"traders": 10, "ticks": 16}},
        {"at_hours": 5.15, "action": "duels", "note": "Duels I: price only, one round-robin",
         "params": {"name": "Duels I"}},
        {"at_hours": 5.508333333333326, "action": "persona_opens", "note": "Doña Pilar opens for everyone",
         "params": {"persona": "pilar"}},
        {"at_hours": 16.65, "action": "set_release", "note": "Chamberí released", "params": {"set": "CHA"}},
        {"note": "no action"},
    ],
}  # fmt: skip
LEVELS = {
    "levels": [
        {"id": "chato", "state": "active", "open_to_all": True, "opens_to_all_at_hours": 2.633},
        {"id": "pilar", "name": "Doña Pilar", "state": "active", "open_to_all": False, "opens_to_all_at_hours": 5.508},
        {"id": "lupe", "name": "La Lupe", "state": "announced"},
    ]
}


def at(t_hours, tick_seconds=30.0, tick=400):
    return Clock(tick=tick, tick_seconds=tick_seconds, next_tick_in=20.0, t_hours=t_hours, limits={})


def test_every_schedule_action_and_every_level_still_to_open_becomes_an_event():
    events = events_from_schedule(SCHEDULE)
    assert [e.action for e in events] == ["bench", "duels", "persona_opens", "set_release"]
    assert events[2].subject == "pilar" and events[3].subject == "CHA" and events[2].at_hours == 5.508
    levels = events_from_levels(LEVELS)
    assert [(e.event_id, e.at_hours) for e in levels] == [("level:pilar:open", 5.508), ("level:lupe:announced", None)]


def test_lead_time_is_in_ticks_at_todays_pace():
    bench = events_from_schedule(SCHEDULE)[0]
    assert lead_ticks(bench, 4.9, 30.0) == 12  # 0.1 h = 360 s = 12 ticks of 30 s
    assert lead_ticks(bench, 4.9, 15.0) == 24 and lead_ticks(bench, 5.2, 30.0) == 0
    assert lead_ticks(events_from_levels(LEVELS)[1], 4.9, 30.0) is None


def test_each_event_is_said_once_then_again_at_each_threshold():
    stored, lines = [], []
    w = ScheduleWatch(stored.extend, lines.append)
    w.update({"upcoming": SCHEDULE["upcoming"][:1]}, None)
    first = w.on_tick(400, 4.5, 30.0)  # 60 ticks ahead
    assert [lr.detail["lead_ticks"] for lr in first] == [60]
    assert "The Market Test" in first[0].text and "keep the maker and our venue's broker up, no deploy" in first[0].text
    assert w.on_tick(401, 4.6, 30.0) == []  # 48 ticks: no threshold crossed
    assert [lr.detail["lead_ticks"] for lr in w.on_tick(402, 4.84, 30.0)] == [20]
    assert w.on_tick(403, 4.85, 30.0) == []  # 18: still inside the 20 window
    assert [lr.detail["lead_ticks"] for lr in w.on_tick(404, 4.92, 30.0)] == [10]
    assert [lr.detail["lead_ticks"] for lr in w.on_tick(405, 4.98, 30.0)] == [3]
    assert w.on_tick(406, 4.99, 30.0) == []
    assert lines[-1] == "tick 405 schedule: " + stored[-1].text and "(in 3 ticks)" in stored[-1].text
    w.on_tick(407, 5.0, 30.0)  # lead 0: it is happening, dropped
    assert w.events == {} and w.on_tick(408, 5.01, 30.0) == []


def test_one_row_per_event_in_the_store_refreshed_with_the_lead_time():
    store = LearningStore(None)
    w = ScheduleWatch(store.record, lambda line: None)
    w.update(SCHEDULE, LEVELS)
    w.on_tick(400, 4.5, 30.0)
    w.on_tick(402, 4.84, 30.0)
    rows = [lr for lr in store.memory.values() if lr.detail.get("action") == "bench"]
    assert len(rows) == 1 and rows[0].detail["lead_ticks"] == 20 and rows[0].kind == "schedule"
    assert len(store.memory) == 5  # 4 schedule events + La Lupe announced (Pilar's opening is on the schedule)


def test_a_store_that_raises_says_nothing_and_tries_again_next_tick():
    lines: list[str] = []

    def boom(rows):
        raise RuntimeError("db")

    w = ScheduleWatch(boom, lines.append)
    w.update(SCHEDULE, None)
    with pytest.raises(RuntimeError):  # the sentinel around it catches and logs once
        w.on_tick(400, 4.5, 30.0)
    assert lines == [] and w._said == {}


class Public:
    def __init__(self):
        self.calls = []

    def call(self, method, path):
        self.calls.append(path)
        return {"/api/schedule": SCHEDULE, "/api/levels": LEVELS}.get(path, {})


def test_the_sentinel_writes_upcoming_events_with_lead_times(tmp_path):
    stored, lines = [], []
    s = NewsSentinel(Public(), stored.extend, lines.append, tmp_path)
    s.on_tick(400, [], {}, at(4.9))
    body = json.loads((tmp_path / EVENTS_FILE).read_text())
    assert [(u["action"], u["lead_ticks"]) for u in body["upcoming"][:2]] == [("bench", 12), ("duels", 30)]
    assert any("schedule: The Market Test: every venue gets the same synthetic book at game hour 5 (in 12 ticks)" in x
               for x in lines)  # fmt: skip
    assert s.on_tick(401, [], {}, None) == []  # no clock: no lead times, never raises
