from bazaar_agent import monitor as mon

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
