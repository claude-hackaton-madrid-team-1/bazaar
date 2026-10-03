"""The sentinel's consumers: rank history in Postgres, the taker's schedule guard, rival moves in Jev's state."""

import pytest

from bazaar_agent.agents.taker import Taker, TakerConfig, offer_state
from bazaar_agent.leaderboard_store import RETRY_EVERY, LeaderboardStore
from bazaar_agent.news import NewsSentinel
from bazaar_agent.rank_watch import RankWatch, Standing, standings
from bazaar_agent.schedule_watch import crossing, ladder_ticks
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts, rows
from tests.test_db import database_url, open_in, schema  # noqa: F401 — pytest fixtures

BENCH = {"event_id": "bench:1.6", "action": "bench", "note": "The Market Test", "at_hours": 1.6, "subject": None}
SOON = {**BENCH, "event_id": "bench:1.55", "at_hours": 1.55}  # 3 ticks after the fixture clock (t 1.5 h, 60 s)


def test_a_ladder_runs_one_tick_per_distinct_bid_up_to_the_thread_limit():
    assert ladder_ticks((18, 26, 1), 14) == 9 and ladder_ticks((10, 60, 1), 14) == 14
    assert ladder_ticks((17, 26, 3), 14) == 4 and ladder_ticks(None, 14) == 14


def test_crossing_finds_a_bench_or_duels_starting_inside_the_ladder_only():
    duels = {**BENCH, "event_id": "duels:1.7", "action": "duels", "at_hours": 1.7}
    fever = {**BENCH, "event_id": "persona_patch:1.55", "action": "persona_patch", "at_hours": 1.55}
    rows = [duels, fever, BENCH]
    # t 1.5 h at 60 s ticks: the bench is 6 ticks away, the duels 12
    assert crossing(rows, 1.5, 60.0, 9)["event_id"] == "bench:1.6"
    assert crossing(rows, 1.5, 60.0, 5) is None  # the bench starts after our last bid
    assert crossing([duels], 1.5, 60.0, 12)["lead_ticks"] == 12
    assert crossing([BENCH], 1.61, 60.0, 9) is None  # already started: not ahead of us


def guarded_taker(tmp_path, team, upcoming, **config):
    lines: list[str] = []
    news = NewsSentinel(FakePublic(), lambda rows: None, lines.append, tmp_path)
    news.upcoming = upcoming
    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3, **config),
        news=news,
        **parts(tmp_path),
    )
    return t, lines


def test_no_dealer_ladder_opens_across_a_market_test(tmp_path):
    team = FakeTeam()
    t, _ = guarded_taker(tmp_path, team, [SOON])  # 3 ticks away: LAV-08's ladder (18→22) runs 5, LAV-02's 2
    t.on_tick(clock())
    assert [s[2] for s in team.sent if s[0] == "open_thread"] == [{"buy": {"card": "LAV-02"}}]
    t.on_tick(clock())  # the same event: no second skip row
    found = [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
    assert len(found) == 1 and found[0]["reason"] == "The Market Test starts in 3 ticks"


def test_the_guard_lets_a_ladder_open_when_the_event_is_far_or_the_guard_is_off(tmp_path):
    team = FakeTeam()
    t, _ = guarded_taker(tmp_path / "far", team, [BENCH])  # 6 ticks away: LAV-08's 5 bids end before it
    t.on_tick(clock())
    assert [s[2] for s in team.sent if s[0] == "open_thread"] == [{"buy": {"card": "LAV-08"}}]
    team2 = FakeTeam()
    t2, _ = guarded_taker(tmp_path / "off", team2, [SOON], schedule_guard=False)
    t2.on_tick(clock())
    assert [s[2] for s in team2.sent if s[0] == "open_thread"] == [{"buy": {"card": "LAV-08"}}]


def test_jevs_offer_state_carries_the_latest_rival_moves(tmp_path):
    t, _ = guarded_taker(tmp_path, FakeTeam(), [])
    order = ["t02", "t03", "t04", "t05", "t14", "t01"]
    t.news.ranks.us = "t01"
    t.news.ranks.observe(_board(400, order), [], 400)
    t.news.ranks.observe(_board(410, ["t14", *order[:4], "t01"], {"t14": 11.9}), [], 410)
    assert t._rival_moves() == [t.news.ranks.latest[0].text] and "t14 +4 ranks" in t._rival_moves()[0]
    assert offer_state.__defaults__ == ((),)  # optional: callers without it are unchanged


def _board(tick, order, market=None):
    return {
        "snapshot_tick": tick,
        "teams": [
            {"team": t, "rank": i + 1, "score": 30.0 - i, "negotiating": 15.0, "market": (market or {}).get(t, 7.5)}
            for i, t in enumerate(order)
        ],
    }


def test_rank_watch_keeps_the_three_newest_moves_newest_first():
    rw = RankWatch(lambda rows: None, lambda line: None, window_ticks=20)
    order = ["t02", "t03", "t04", "t05", "t06", "t07", "t14", "t15"]
    rw.observe(_board(400, order), [], 400)
    rw.observe(_board(410, ["t14", "t15", *order[:6]]), [], 410)  # t14 +6, t15 +6
    assert [lr.subject for lr in rw.latest] == ["t15", "t14"]


def test_a_store_without_postgres_keeps_nothing_and_retries_later():
    lines: list[str] = []
    calls = []

    def down():
        calls.append(1)
        raise OSError("refused")

    store = LeaderboardStore(down, lines.append)
    rows = standings(_board(400, ["t01", "t02"]))
    assert store.save(rows) == 0 and store.load(60) == []
    assert len(calls) == 1 and lines == ["leaderboard history: connect failed (OSError); kept in memory only"]
    for _ in range(RETRY_EVERY):
        store.save(rows)
    assert len(calls) == 2  # tried again after RETRY_EVERY skipped saves


@pytest.mark.integration
def test_boards_survive_a_restart_per_world(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    conn.autocommit = True  # never left idle in a transaction: the schema's teardown must not wait on us
    stores = []
    try:
        db.init_schema(conn)
        real = LeaderboardStore(lambda: open_in(database_url, schema), world="real")
        sim = LeaderboardStore(lambda: open_in(database_url, schema), world="sim:127.0.0.1:8765")
        stores = [real, sim]
        order = ["t02", "t03", "t04", "t05", "t14", "t01"]
        assert real.save(standings(_board(400, order))) == 6
        assert real.save(standings(_board(400, order))) == 6  # idempotent upsert
        sim.save(standings(_board(400, ["t14", *order[:4], "t01"])))
        loaded = real.load(60)
        assert len(loaded) == 6 and {s.tick for s in loaded} == {400} and isinstance(loaded[0], Standing)
        # a restarted process: the stored board is the base, the next board's climb is caught
        found: list = []
        rw = RankWatch(found.extend, lambda line: None, us="t01", save=real.save)
        assert rw.seed(real.load(60)) == 1
        rw.observe(_board(410, ["t14", "t01", "t02", "t03", "t04", "t05"]), [], 410)
        assert [lr.subject for lr in found] == ["t14"]
        assert {s.tick for s in real.load(60)} == {400, 410}
        assert conn.execute("select count(*) from leaderboard_snapshots").fetchone()[0] == 18  # real 400, 410; sim
    finally:
        for store in stores:
            store.close()
        conn.close()
