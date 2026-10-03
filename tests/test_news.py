"""The news sentinel: Radio Rastro and the schedule are logged and stored once each; signals stay off."""

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.news import (
    EVENTS_FILE,
    NewsSentinel,
    active_signals,
    items_from_api,
    items_from_feed,
    items_from_schedule,
    learning_of,
    load_market_events,
    parse_events,
    set_names,
)
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts

CATALOG = {"sets": [{"id": "SAL", "name": "Salamanca"}, {"id": "RET", "name": "El Retiro"}, {"id": "LAV"}]}
NAMES = set_names(CATALOG)
# The real schedule's two Pilar patches (GET /api/schedule, Sat 3 Oct 10:54), plus a lever that is not one.
SCHEDULE = {
    "now_hours": 4.058,
    "upcoming": [
        {"at_hours": 5.0, "action": "bench", "note": "The Market Test", "params": {"traders": 10}},
        {
            "at_hours": 9.15,
            "action": "persona_patch",
            "note": "Salamanca fever: Doña Pilar pays 25 % over book for Salamanca until 17:30",
            "params": {"id": "pilar"},
        },
        {"at_hours": 11.15, "action": "persona_patch", "note": "The fever breaks", "params": {"id": "pilar"}},
    ],
}
NEWS = {
    "news": [
        {
            "id": 3,
            "at_hours": 4.2,
            "tick": 340,
            "source": "radio",
            "headline": "Word is Chato pays 10 % over book for El Retiro",
            "body": "Heard it at the bar.",
        },
        {"id": 1, "at_hours": 3.68, "tick": 283, "source": "boletin", "headline": "Radio Rastro is on the air"},
    ]
}
FEED_NEWS = {
    "id": 17596,
    "tick": 331,
    "t": 4.0833,
    "type": "news.posted",
    "payload": {"id": 2, "source": "radio", "headline": "Atleti win 2-1", "body": "Car horns on Gran Vía."},
}


READS = ["/api/news", "/api/schedule", "/api/levels", "/api/leaderboard"]


class Public:
    def __init__(self, news=NEWS, schedule=SCHEDULE, fail=False, levels=None, leaderboard=None):
        self.fail, self.calls = fail, []
        self.answers = {
            "/api/news": news,
            "/api/schedule": schedule,
            "/api/levels": levels or {"levels": []},
            "/api/leaderboard": leaderboard or {"teams": []},
        }

    def call(self, method, path):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError("down")
        return self.answers[path]


def sentinel(tmp_path, public):
    stored, lines = [], []
    return NewsSentinel(public, stored.extend, lines.append, tmp_path), stored, lines


def test_the_feed_and_the_api_carry_the_same_items():
    assert [i.news_id for i in items_from_feed([FEED_NEWS, {"type": "tick"}])] == ["news:2"]
    api = items_from_api(NEWS)
    assert [(i.news_id, i.source, i.official) for i in api] == [
        ("news:3", "radio", False),
        ("news:1", "boletin", False),
    ]


def test_the_schedule_fever_is_an_official_window_closed_by_the_fever_breaking():
    items = items_from_schedule(SCHEDULE, 300)
    assert [i.persona for i in items] == ["pilar", "pilar"] and all(i.official for i in items)
    (fever,) = parse_events(items, NAMES)
    assert (fever.set_code, fever.pct, fever.start_hours, fever.end_hours) == ("SAL", 25.0, 9.15, 11.15)
    assert fever.until_wall == "17:30" and fever.persona == "pilar" and fever.official


def test_a_rumour_is_parsed_but_never_official_and_an_unknown_set_is_no_event():
    events = parse_events(items_from_api(NEWS), NAMES)
    assert [(e.set_code, e.pct, e.official) for e in events] == [("RET", 10.0, False)]
    vague = items_from_api(
        {"news": [{"id": 9, "headline": "Prices are going up for Narnia, 30 % over book for Narnia"}]}
    )
    assert parse_events(vague, NAMES) == []
    under = items_from_api({"news": [{"id": 8, "headline": "Abuela sells 15% under book for LAV."}]})
    assert [(e.set_code, e.pct) for e in parse_events(under, NAMES)] == [("LAV", -15.0)]


def test_signals_are_off_by_default_and_on_only_for_official_moves_in_their_window():
    events = parse_events([*items_from_schedule(SCHEDULE, 0), *items_from_api(NEWS)], NAMES)
    assert Guardrails().news_signals_enabled is False
    assert active_signals(Guardrails(), events, 10.0) == []
    on = Guardrails(news_signals_enabled=True)
    assert [e.set_code for e in active_signals(on, events, 10.0)] == ["SAL"]  # the rumour (RET) never
    assert active_signals(on, events, 9.0) == [] and active_signals(on, events, 11.15) == []


def test_the_sentinel_stores_and_logs_each_item_once_and_reads_the_api_every_ten_ticks(tmp_path):
    public = Public()
    s, stored, lines = sentinel(tmp_path, public)
    fresh = s.on_tick(400, [FEED_NEWS], CATALOG)
    assert len(fresh) == 5 and len(stored) == 5  # 1 feed + 2 news + 2 schedule patches
    assert public.calls == READS
    assert any("news (radio, unverified): Atleti win 2-1" in line for line in lines)
    assert any("news (official): Salamanca fever" in line for line in lines)
    assert s.on_tick(401, [FEED_NEWS], CATALOG) == [] and len(stored) == 5  # dedupe, and no read before 10 ticks
    assert len(public.calls) == 4
    s.on_tick(410, [], CATALOG)
    assert len(public.calls) == 8 and len(stored) == 5
    saved = load_market_events(tmp_path / EVENTS_FILE)
    assert {(e.set_code, e.official) for e in saved} == {("SAL", True), ("RET", False)}


def test_a_failed_read_is_logged_once_and_never_raises(tmp_path):
    public = Public(fail=True)
    s, stored, lines = sentinel(tmp_path, public)
    assert [i.news_id for i in s.on_tick(400, [FEED_NEWS], CATALOG)] == ["news:2"]
    s.on_tick(410, [], CATALOG)
    assert sum("read failed" in line for line in lines) == 1  # said once; /api/schedule not tried after it
    assert public.calls == ["/api/news", "/api/news"]
    assert len(stored) == 1


def test_a_store_that_raises_never_breaks_the_tick(tmp_path):
    def boom(rows):
        raise RuntimeError("db")

    lines: list[str] = []
    s = NewsSentinel(Public(), boom, lines.append, tmp_path)
    assert s.on_tick(400, [], CATALOG) == [] and lines == ["tick 400 news: skipped (RuntimeError)"]


def test_each_item_is_its_own_learnings_row_kept_out_of_the_default_recall():
    a, b = (learning_of(i, 400) for i in items_from_api(NEWS))
    assert a.kind == "news" and a.subject_kind == "organiser" and a.key() != b.key()
    assert a.confidence == 0.5 and learning_of(items_from_schedule(SCHEDULE, 0)[0], 0).confidence == 1.0
    store = LearningStore(None)
    store.record([a, b, a])
    assert len(store.memory) == 2
    assert store.recall(None, ("lesson", "behaviour", "policy"), None) == []


def test_the_taker_runs_the_sentinel_after_its_sends(tmp_path):
    lines: list[str] = []
    public = FakePublic(events=[FEED_NEWS])
    news_public = Public()
    stored: list = []
    t = Taker(
        FakeTeam(),
        public,
        live=False,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        news=NewsSentinel(news_public, stored.extend, lines.append, tmp_path),
        **{**parts(tmp_path), "feed": MarketFeed(lambda n: [dict(FEED_NEWS)])},
    )
    t.on_tick(clock())
    assert any("Atleti win 2-1" in line for line in lines) and news_public.calls == READS
    assert sum(lr.kind == "news" for lr in stored) == 5
    assert sum(lr.kind == "schedule" for lr in stored) == 3  # the bench and both Pilar patches, with lead times
    assert any("schedule: The Market Test at game hour 5" in line for line in lines)


def test_the_cli_sentinel_reads_on_its_own_client_with_a_short_timeout_and_no_retries(tmp_path):
    from types import SimpleNamespace

    from bazaar_agent.cli import _news_sentinel

    settings = SimpleNamespace(bazaar_url="http://127.0.0.1:9", data_dir=tmp_path)
    s = _news_sentinel({"log": lambda line: None}, settings)
    assert (s.public.timeout, s.public.retries, s.public.key) == (2.0, 0, "")
    assert s.path == tmp_path / "agents" / EVENTS_FILE


def _board(tick, order, bonus=0.0):
    teams = [
        {"team": t, "rank": i + 1, "score": 30.0 - i, "negotiating": 15.0, "market": 7.5 + (bonus if t == "t14" else 0)}
        for i, t in enumerate(order)
    ]
    return {"tick": tick, "snapshot_tick": tick, "teams": teams, "venues": []}


def test_the_sentinel_feeds_the_leaderboard_to_the_rank_watch_and_never_flags_us(tmp_path):
    public = Public(leaderboard=_board(400, ["t02", "t03", "t04", "t05", "t14", "t01"]))
    stored, lines = [], []
    s = NewsSentinel(public, stored.extend, lines.append, tmp_path)
    s.on_tick(400, [], CATALOG, None, "t01")
    public.answers["/api/leaderboard"] = _board(410, ["t14", "t01", "t02", "t03", "t04", "t05"], 4.4)
    s.on_tick(410, [], CATALOG, None, "t01")
    moves = [lr for lr in stored if lr.kind == "rival_move"]
    assert [lr.subject for lr in moves] == ["t14"] and "market +4.4" in moves[0].text
    assert any(line.startswith("tick 410 rival move: t14 +4 ranks") for line in lines)
