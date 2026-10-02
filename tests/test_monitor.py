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


# ---------------------------------------------------------------- us vs the competition

OURS = "t01"
OUR_LEVEL = {"id": 4416, "tick": 98, "type": "level.unlocked", "actor": "", "payload": {"team": OURS, "level": 2}}
THEIR_LEVEL = {"id": 4417, "tick": 98, "type": "level.unlocked", "actor": "", "payload": {"team": "t06", "level": 2}}
OUR_VENUE = {"id": 4536, "tick": 100, "type": "venue.opened", "actor": "", "payload": {"owner": OURS, "name": "Ours"}}


def test_our_trader_row_is_tagged_us_and_never_alerts():
    events = [{"id": 1, "type": "offer.listed", "actor": OURS, "payload": {}}, THEIR_LEVEL]
    teams = mon.team_snapshots(events, ours=OURS)
    assert (teams[OURS].status, teams["t06"].status) == ("us", "active")
    before = mon.team_snapshots([THEIR_LEVEL], ours=OURS)
    alerts = mon.detect_changes(5, before, teams, [], [], ours=OURS)
    assert alerts == []  # our own first appearance is not a new competitor


def test_our_own_actions_never_raise_feed_alerts():
    alerts = mon.event_alerts([OUR_LEVEL, OUR_VENUE, THEIR_LEVEL], ours=OURS)
    assert [(a.kind, a.tick) for a in alerts] == [("feed:level.unlocked", 98)]
    assert len(mon.event_alerts([OUR_LEVEL, OUR_VENUE, THEIR_LEVEL])) == 3  # unknown id: nothing is hidden


# ---------------------------------------------------------------- one pipeline: stream + poll


def ev(i, kind="thread.message", actor="t05", tick=106, **payload):
    return {"id": i, "tick": tick, "type": kind, "scope": "public", "actor": actor, "payload": payload}


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def watcher(tmp_path, ours=OURS):
    from bazaar_agent.feed import FeedStore

    clock = FakeClock()
    w = mon.Watcher(FeedStore(tmp_path), ours, FeedStore(tmp_path, "team_events.jsonl"), now=clock)
    return w, clock


def stored_ids(path):
    return [json.loads(line)["id"] for line in path.read_text().splitlines()]


def test_stream_and_poll_dedupe_by_id_and_the_poll_measures_the_lead(tmp_path):
    w, clock = watcher(tmp_path)
    w.from_poll([ev(1)], 500)  # baseline
    streamed = w.from_stream([ev(5), ev(6)])
    assert [e["id"] for e in streamed.fresh] == [5, 6]
    assert w.from_stream([ev(6)]).fresh == []  # a repeat (reconnect replay) is a no-op
    clock.t += 42.0  # the next tick's poll
    result, polled, lead = w.from_poll([ev(1), ev(4), ev(5), ev(6), ev(7)], 500)
    assert [e["id"] for e in polled.fresh] == [4, 7]  # only what the stream missed
    assert (lead.streamed, lead.poll_only, lead.median_s, lead.max_s) == (2, 2, 42.0, 42.0)
    assert "stream ahead on 2 events by median 42.0 s" in lead.describe()
    assert (result.new, result.newest_id) == (2, 7)
    assert sorted(stored_ids(tmp_path / "feed.jsonl")) == [1, 4, 5, 6, 7]  # each event exactly once


def test_an_announcement_alerts_once_from_the_stream_never_again_from_the_poll(tmp_path):
    w, _ = watcher(tmp_path)
    w.from_poll([ev(1)], 500)
    news = ev(2, "announcement", actor="admin", text="El Rata is coming")
    assert [a.kind for a in w.from_stream([news]).alerts] == ["feed:announcement"]
    _, polled, _ = w.from_poll([ev(1), news], 500)
    assert polled.alerts == []


def test_a_new_team_alerts_from_the_stream_after_the_baseline_but_never_us(tmp_path):
    w, _ = watcher(tmp_path)
    before = w.from_stream([ev(1, actor="t09")])  # before the first poll: still the baseline
    assert before.alerts == []
    _, first, _ = w.from_poll([ev(1, actor="t09"), ev(2, actor="t05")], 500)
    assert first.alerts == [] and sorted(w.teams) == ["t05", "t09"]
    later = w.from_stream([ev(3, actor="t12", tick=110), ev(4, actor=OURS, tick=110)])
    assert [(a.kind, a.subject, a.tick) for a in later.alerts] == [("new_team", "t12", 110)]
    assert w.teams[OURS].status == "us"


def test_tick_events_are_skipped_and_team_scoped_events_stay_out_of_the_public_feed(tmp_path):
    w, _ = watcher(tmp_path)
    private = ev(9, "gift.given", actor="abuela", team=OURS) | {"scope": "team:t01"}
    got = w.from_stream([ev(8, "tick", actor=""), private, ev(10)])
    assert w.stream_only_skipped == 1
    assert ([e["id"] for e in got.fresh], [e["id"] for e in got.private]) == ([10], [9])
    assert stored_ids(tmp_path / "feed.jsonl") == [10]
    assert stored_ids(tmp_path / "team_events.jsonl") == [9]


def test_events_the_stream_delivered_do_not_hide_a_gap_in_the_poll(tmp_path):
    w, _ = watcher(tmp_path)
    w.from_poll([ev(1), ev(2)], 2)
    w.from_stream([ev(50)])  # after a long stream outage, the stream is back at id 50
    result, _, _ = w.from_poll([ev(40), ev(50)], 2)  # a full window that starts after our last poll
    assert result.gap_possible
