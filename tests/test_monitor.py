import json
from pathlib import Path

from bazaar_agent import monitor as mon
from bazaar_agent.ticks import Clock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"


def body(name):
    return json.loads((FIXTURES / name).read_text())["body"]


DEALERS = {
    "personas": [
        {"id": "abuela", "name": "Abuela Carmen", "status": "active", "level": 1, "menu": {"a": 1}, "traits": {}},
    ]
}


def test_first_sync_is_a_baseline_then_new_dealers_status_menu_and_levels_alert():
    before = mon.dealer_snapshots(DEALERS)
    assert mon.detect_changes(1, {}, before, [], []) == []  # baseline
    after_payload = {
        "personas": [
            {"id": "abuela", "name": "Abuela Carmen", "status": "active", "level": 1, "menu": {"a": 2}},
            {"id": "rata", "name": "El Rata", "status": "announced", "level": None},
        ]
    }
    alerts = mon.detect_changes(
        5, before, mon.dealer_snapshots(after_payload), [], [{"name": "L2", "status": "active"}]
    )
    kinds = sorted((a.kind, a.subject) for a in alerts)
    assert kinds == [("level_announced", "L2"), ("menu_change", "abuela"), ("new_dealer", "rata")]
    went_live = {"personas": [{"id": "rata", "name": "El Rata", "status": "active", "level": 2}]}
    alerts = mon.detect_changes(9, mon.dealer_snapshots(after_payload), mon.dealer_snapshots(went_live), [], [])
    assert {a.kind for a in alerts if a.subject == "rata"} == {"status_change", "level_change"}


def test_teams_come_from_feed_actors_parties_and_joins():
    events = [
        {"id": 1, "type": "team.joined", "actor": "", "payload": {"team": "t07", "name": "Team 7", "level": 1}},
        {"id": 2, "type": "settlement", "payload": {"parties": ["abuela", "t05"]}},
        {"id": 3, "type": "offer.listed", "actor": "t06", "payload": {}},
    ]
    teams = mon.team_snapshots(events)
    assert sorted(teams) == ["t05", "t06", "t07"] and teams["t07"].name == "Team 7"


def test_announcements_and_level_events_in_the_feed_alert(tmp_path):
    events = [
        {"id": 9, "tick": 40, "type": "announcement", "actor": "calendar", "payload": {"text": "El Rata is coming"}},
        {"id": 10, "tick": 40, "type": "thread.message", "payload": {}},
    ]
    alerts = mon.event_alerts(events)
    assert [(a.kind, a.detail) for a in alerts] == [("feed:announcement", "El Rata is coming")]
    path = tmp_path / "alerts.jsonl"
    mon.append_alerts(path, alerts)
    assert mon.read_alerts(path, 5)[0]["detail"] == "El Rata is coming"


def test_a_missing_level_is_not_a_level_change():
    known = mon.team_snapshots([{"id": 1, "type": "team.joined", "payload": {"team": "t12", "level": 1}}])
    later = mon.team_snapshots([{"id": 2, "type": "offer.listed", "actor": "t12", "payload": {}}])
    assert mon.detect_changes(9, known, later, [], []) == []


def test_a_level_going_active_is_reported_by_id():
    base = mon.dealer_snapshots(DEALERS)
    announced = [{"id": "chato", "name": "El Chato", "state": "announced", "teaser": "If I like you."}]
    active = [{"id": "chato", "name": "El Chato", "state": "active", "how": "deal with him"}]
    [alert] = mon.detect_changes(80, base, base, announced, active)
    assert (alert.kind, alert.subject, alert.detail) == (
        "level_state_change",
        "chato",
        "announced → active: deal with him",
    )


def test_a_tick_becomes_the_web_stream_hello_me_clock_then_the_feed():
    clock = Clock.model_validate(body("get_api_clock.anon.json"))
    me = body("get_api_me.team.json")
    feed = body("get_api_feed_limit_20.anon.json")["events"][:3]
    events = mon.web_events(clock, feed, me)
    assert [e["type"] for e in events] == ["agent.hello", "agent.me", "clock"] + [e["type"] for e in feed]
    assert events[3:] == feed
    assert events[0]["payload"] == {"team": "t01", "name": "Team 1"}
    assert events[1]["payload"] == me
    assert events[2]["tick"] == 31 and events[2]["t"] == clock.t_hours
    assert events[2]["payload"] == {"day": "fri", "tick_seconds": 60.0}
    assert all(set(e) == {"id", "tick", "t", "type", "scope", "actor", "payload"} for e in events)


def test_made_up_stream_ids_are_negative_and_unique_across_ticks():
    me = body("get_api_me.team.json")
    ids = [e["id"] for tick in (1, 2, 3) for e in mon.web_events(Clock(tick=tick), [], me)]
    assert all(i < 0 for i in ids) and len(ids) == len(set(ids))


def test_without_a_team_key_the_stream_has_no_hello_and_no_me():
    feed = body("get_api_feed_limit_20.anon.json")["events"][:2]
    events = mon.web_events(Clock(tick=5), feed, None)
    assert [e["type"] for e in events] == ["clock"] + [e["type"] for e in feed]
