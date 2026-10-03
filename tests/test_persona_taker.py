"""The taker under the persona model: the hourly deal budget skips a dealer, and the flag turns it all off."""

from __future__ import annotations

from typing import Any

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_intel import settle
from tests.test_strategy import ME

US = str(ME.get("id") or "t01")
ABUELA = {
    "id": "abuela",
    "status": "active",
    "level": 1,
    "traits": {"patience": 0.85, "generosity": 0.8, "shrewdness": 0.2, "memory": 0.15, "strictness": 0.1},
    "menu": {
        "sells": [{"rarity": "uncommon", "sets": "released", "list_price": 25}],
        "buys": [{"rarity": "uncommon", "sets": "released"}],
        "deals_per_team_per_hour": 2,
    },
    "unlock": {"always": True},
    "open_to_all": True,
}
# Two deals of ours with Abuela inside the last game hour: her budget of 2 per hour is used.
EVENTS = [
    settle(1, 1, "abuela", US, "LAV-01", 9, tick=TICK - 10, kind="card"),
    settle(2, 2, "abuela", US, "LAV-02", 9, tick=TICK - 5, kind="card"),
]


def taker(tmp_path: Any, team: FakeTeam, **rules: Any) -> Taker:
    kw = {**parts(tmp_path, **rules), "feed": MarketFeed(lambda n: list(EVENTS))}
    return Taker(
        team,
        FakePublic(dealers=[ABUELA], events=EVENTS),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        **kw,
    )


def test_a_used_hourly_budget_opens_no_thread_and_says_why_once(tmp_path: Any) -> None:
    team = FakeTeam(me={**ME, "unlocked": ["abuela"]})
    t = taker(tmp_path, team)
    t.on_tick(clock())
    team.now = clock(tick=TICK + 1)
    t.on_tick(team.now)
    assert not [s for s in team.sent if s[0] == "open_thread"]
    skips = [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
    assert len(skips) == 1 and "persona budget: 2 deals per hour with abuela used" in skips[0]["reason"]
    assert t.personas.personas["abuela"].deals_per_team_per_hour == 2


def test_the_flag_off_is_todays_taker(tmp_path: Any) -> None:
    team = FakeTeam(me={**ME, "unlocked": ["abuela"]})
    taker(tmp_path, team, persona_model_enabled=False).on_tick(clock())
    assert [s for s in team.sent if s[0] == "open_thread"]
    assert not [r for r in rows(tmp_path) if r.get("kind") == "dealer_skip"]
