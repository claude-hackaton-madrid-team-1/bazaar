"""The `sunday` scenario: schedule events at the right ticks, the calibrated dealers and rivals, and that a world
without a scenario is exactly the plain one (RET and CHA unreleased, three dealers, no news)."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from bazaar_sim import catalog, dealers, scenario, threads
from bazaar_sim.app import latency_seconds
from bazaar_sim.errors import SimError
from bazaar_sim.models import Negotiation
from bazaar_sim.world import SimConfig
from tests.simkit import QUIET, manual_world

US = "t01"


@pytest.fixture(autouse=True)
def _plain_catalog_after():
    yield
    catalog.configure()  # a scenario world sets process-wide switches: leave none behind


def sunday(**over: object):
    cfg = replace(scenario.configure(SimConfig(), "sunday"), **{"rivals": 0, **over})  # type: ignore[arg-type]
    return manual_world(cfg)


def events(m, kind: str):
    return [e for e in m.world.state.events if e.type == kind]


# ---------------------------------------------------------------- the schedule as tick events


def test_the_schedule_becomes_tick_events():
    s = scenario.load("sunday")
    at = {(e.action, e.tick) for e in s.events}
    assert ("grant_all", 12) in at and ("bench", 84) in at and ("duels", 480) in at and ("bench", 564) in at
    assert ("persona", 1200) in at and ("set_release", 1) in at and ("round", 1) in at
    assert all(1 <= e.tick <= s.sunday_ticks for e in s.events)


def test_benches_dated_before_the_doors_open_run_one_after_another_from_the_start():
    benches = sorted(e.tick for e in scenario.load("sunday").events if e.action == "bench")
    assert benches[:2] == [2, 2 + scenario.SUNDAY_FIRST_SESSION_GAP]
    assert benches[2] == 84


def test_configure_is_15_second_game_ticks_with_sixteen_rivals_and_everyone_open():
    cfg = scenario.configure(SimConfig(), "sunday")
    assert (cfg.tick_seconds, cfg.game_tick_seconds, cfg.rivals) == (15.0, 15.0, 16)
    assert cfg.chato_open_ticks == cfg.pilar_open_ticks == 0 and cfg.compression() == 1.0
    assert cfg.limits["offers_per_team_per_tick"] == 12 and cfg.latency_p50_ms > 0


def test_a_compressed_replay_keeps_the_game_clock_and_scales_the_per_second_limits(monkeypatch):
    monkeypatch.setenv("SIM_TICK_SECONDS", "2")
    cfg = scenario.configure(SimConfig(), "sunday")
    assert cfg.tick_seconds == 2.0 and cfg.game_tick_seconds == 15.0 and cfg.compression() == 7.5
    assert cfg.latency_p50_ms < 40  # 40 ms of a 15 s tick is 5.3 ms of a 2 s tick
    m = manual_world(replace(cfg, rivals=0))
    m.step(240)
    assert m.world.t_hours == 1.0  # 240 ticks of 15 s of game time, whatever the pace


def test_unknown_scenarios_fail_at_boot():
    with pytest.raises(ValueError, match="unknown scenario"):
        scenario.configure(SimConfig(), "monday")


def test_the_events_fire_on_their_ticks():
    m = sunday()
    w = m.world
    assert "RET" in catalog.released_sets() and "CHA" not in catalog.released_sets()
    cash = w.team(US).cash
    m.step(1)  # tick 1: Chamberí released, Round 3 starts (the ladder restarts)
    assert "CHA" in catalog.released_sets() and w.state.round == 3
    assert [e.payload["set"] for e in events(m, "set.released")] == ["CHA"]
    assert events(m, "round.started")[-1].payload["reset"] is True
    m.step(11)
    assert w.team(US).cash == cash + 150  # tick 12: the Sunday allowance
    m.step(72)
    assert w.state.bench and events(m, "bench.started")  # tick 84: the first full Market Test


def test_the_ladder_restarts_with_the_round():
    m = sunday()
    team = m.world.team(US)
    from bazaar_sim.models import DealRecord

    team.deals.append(DealRecord(dealer="abuela", item="LAV-01", price=8, tick=0, hour=0, share=1.0, negotiated=True))
    from bazaar_sim import scoring

    assert scoring.ladder_points(team) > 0
    m.step(1)
    assert scoring.ladder_points(team) == 0 and team.round_start_deal == 1


def test_the_finale_closes_every_stall():
    m = sunday()
    m.step(1200)
    assert set(m.world.state.disabled_dealers) == {"abuela", "chato", "pilar", "picaros", "banco"}
    with pytest.raises(SimError) as e:
        threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
    assert e.value.code == "dealer_closed"


def test_news_is_posted_at_the_observed_cadence_and_served():
    m = sunday()
    every = scenario.load("sunday").news_every
    m.step(every * 2)
    posted = events(m, "news.posted")
    assert len(posted) == 2 and m.world.state.news[-1]["headline"] == posted[-1].payload["headline"]


def test_the_schedule_view_lists_what_is_still_to_come():
    m = sunday()
    m.step(100)
    from bazaar_sim import views

    schedule = views.schedule_view(m.world)
    upcoming = schedule["upcoming"]
    assert upcoming and all(u["at_hours"] > 16.65 for u in upcoming) and upcoming[0]["action"] == "duels"
    assert schedule["now_hours"] == pytest.approx(16.65 + 100 * 15 / 3600, abs=0.001)
    assert (upcoming[0]["at_hours"] - schedule["now_hours"]) * 3600 == pytest.approx(380 * 15, abs=2)


# ---------------------------------------------------------------- a world without a scenario is the plain one


def test_no_scenario_changes_nothing():
    m = manual_world(QUIET)
    assert m.world.scenario is None
    assert "RET" not in catalog.released_sets() and "CHA" not in catalog.released_sets()
    assert set(catalog.raw_dealers()) == {"abuela", "chato", "pilar"}
    m.step(5)
    assert not m.world.state.news and m.world.state.round == 0 and not events(m, "set.released")
    assert m.world.game_tick_seconds == m.world.config.tick_seconds


def test_the_scenario_dealers_exist_only_in_a_scenario_world():
    sunday()
    assert set(catalog.raw_dealers()) >= {"picaros", "banco"}
    manual_world(QUIET)
    assert "picaros" not in catalog.raw_dealers()


# ---------------------------------------------------------------- dealers


def test_the_scenario_dealers_open_a_thread_and_haggle():
    m = sunday()
    th = threads.open_thread(m.world, US, {"with": "picaros", "topic": {"buy": {"card": "LAV-09"}}})
    assert th.neg is not None and th.neg.opening > th.neg.list_price  # they open above their 63 P list
    th2 = threads.open_thread(m.world, US, {"with": "banco", "topic": {"buy": {"pack": "sobre_oro"}}})
    assert th2.neg is not None and th2.neg.opening == 546  # Don Ernesto's own opening ask


@pytest.mark.parametrize("dealer,ref,rarity", [("banco", "LAV-12", "legendary"), ("picaros", "LAV-11", "epic")])
@pytest.mark.parametrize("by_ref", [True, False])
def test_scenario_dealers_sell_advertised_high_rarities(dealer, ref, rarity, by_ref):
    m = sunday()
    buy = {"card": ref} if by_ref else {"rarity": rarity, "set": "LAV"}
    th = threads.open_thread(m.world, US, {"with": dealer, "topic": {"buy": buy}})
    assert th.neg is not None and th.neg.item == ref and th.neg.rarity == rarity


@pytest.mark.parametrize("ref", ["LAV-11", "LAV-12"])
def test_scenario_banco_buys_advertised_high_rarities(ref):
    m = sunday()
    asset = m.world.mint(ref, US, "test")
    th = threads.open_thread(m.world, US, {"with": "banco", "topic": {"sell": {"assets": [asset.id]}}})
    assert th.neg is not None and th.neg.side == "buy" and th.neg.assets == [asset.id]


def test_scenario_high_rarities_still_obey_dealer_menu_and_release():
    m = sunday()
    for dealer, ref in (("abuela", "LAV-11"), ("banco", "CHA-12")):
        with pytest.raises(SimError):
            threads.open_thread(m.world, US, {"with": dealer, "topic": {"buy": {"card": ref}}})
    asset = m.world.mint("LAV-12", US, "test")
    with pytest.raises(SimError):
        threads.open_thread(m.world, US, {"with": "picaros", "topic": {"sell": {"assets": [asset.id]}}})


def test_plain_simulator_still_refuses_non_page_dealer_topics():
    m = manual_world(QUIET)
    m.world.team(US).unlocked.append("pilar")
    asset = m.world.mint("LAV-11", US, "test")
    with pytest.raises(SimError):
        threads.open_thread(m.world, US, {"with": "pilar", "topic": {"sell": {"assets": [asset.id]}}})


def test_measured_numbers_replace_the_hand_set_ones():
    base = dealers.ABUELA
    cal = dealers.calibrated(
        base,
        {
            "open_mult": {"common": 1.25, "sobre_barrio": 1.15},
            "floor_range": {"common": {"lo": 0.8, "hi": 1.0}, "sobre_barrio": {"lo": 0.73, "hi": 0.96}},
            "steps_to_final": {"median": 5.0},
            "buys": {"opening_bid_over_book": {"p50": 0.5}, "fill_over_book": {"p75": 0.6}},
        },
    )
    assert cal.open_mult["common"] == 1.25 and cal.open_mult["uncommon"] == base.open_mult["uncommon"]
    assert cal.floor_range["common"] == (0.8, 1.0) and cal.floor_range["pack"] == (0.73, 0.96)
    assert (cal.patience, cal.buy_open, cal.buy_ceiling) == (5, 0.5, 0.6)
    assert dealers.calibrated(base, {"open_mult": {"common": "x"}, "steps_to_final": {"median": None}}) == base


def test_the_sunday_data_calibrates_all_five_dealers():
    s = scenario.load("sunday")
    for dealer_id, base in dealers.STYLES.items():
        cal = s.style(base)
        assert cal.dealer == dealer_id and cal.patience >= 2
    assert s.style(dealers.PICAROS).repeats_final == pytest.approx(0.184, abs=0.01)


def test_a_trickster_may_repeat_its_final_instead_of_walking():
    style = replace(dealers.PICAROS, repeats_final=1.0)
    neg = Negotiation(
        side="sell",
        item="LAV-09",
        item_kind="card",
        rarity="rare",
        list_price=63,
        opening=73,
        limit=55,
        ask=60,
        patience=0,
        final=True,
    )
    answer = dealers.reply(style, neg, None, "please", 0.0, random.Random(1), "LAV-09")
    assert answer.kind == "final" and answer.price == 60  # it names the same "last offer" again
    honest = dealers.reply(replace(style, repeats_final=0.0), neg, None, "please", 0.0, random.Random(1), "LAV-09")
    assert honest.kind == "walk"


def test_a_fever_raises_pilars_bid_for_salamanca_only_inside_its_window():
    m = sunday()
    s = m.world.scenario
    assert s.fever_mult(m.world, "pilar", "SAL", "rare") == 1.0
    m.step(300)
    assert s.fever_mult(m.world, "pilar", "SAL", "rare") == 1.25
    assert s.fever_mult(m.world, "pilar", "LAV", "rare") == 1.0 and s.fever_mult(m.world, "chato", "SAL", "rare") == 1.0


# ---------------------------------------------------------------- rivals


def test_the_rivals_are_fitted_to_the_saturday_feed():
    fit = scenario.load("sunday").rival_params()
    assert 0.2 < fit["list_prob"] < 0.6 and fit["max_listings"] >= 5 and fit["listing_ticks"] == 20
    assert 0.01 < fit["cancel_prob"] < 0.05 and fit["take_prob"] < 0.05
    assert fit["ask_band"]["common"][0] < fit["ask_band"]["common"][1] and fit["partners"]


def test_sixteen_rivals_list_at_about_the_real_rate():
    m = sunday(rivals=16)
    m.step(120)
    listed = len([e for e in m.world.state.events if e.type == "offer.listed" and e.tick > 20])
    per_tick = listed / 100
    assert 2.0 < per_tick < 9.0, per_tick  # the feed's mean is 5.8 a tick


# ---------------------------------------------------------------- latency and the Workshop


def test_latency_is_lognormal_around_the_measured_median():
    rng = random.Random(3)
    draws = sorted(latency_seconds(40, 120, rng) * 1000 for _ in range(4001))
    assert 36 < draws[2000] < 44 and 100 < draws[3800] < 145


def test_the_workshop_turns_three_spares_into_one_of_the_next_rarity():
    m = sunday()
    w = m.world
    refs = [a.ref for a in w.holdings(US) if a.kind == "card" and catalog.cards()[a.ref].rarity == "common"]
    ref = refs[0]
    for _ in range(3):
        w.mint(ref, US, "test")
    ids = [a.id for a in w.holdings(US) if a.ref == ref][:3]
    out = scenario.craft(w, US, ids)
    assert out["from"] == "common" and out["to"] == "uncommon"
    assert events(m, "taller.crafted")
    with pytest.raises(SimError):
        scenario.craft(w, US, ids)  # the three are gone


def test_the_workshop_keeps_one_copy_of_each_card():
    m = sunday()
    w = m.world
    held = w.held_counts(US)
    ref = next(c.ref for c in catalog.cards().values() if c.rarity == "common" and not held.get(c.ref))
    ids = [w.mint(ref, US, "test").id for _ in range(3)]  # exactly three copies: giving all three leaves none
    with pytest.raises(SimError) as e:
        scenario.craft(w, US, ids)
    assert e.value.code == "keep_one"


# ---------------------------------------------------------------- over HTTP


def test_the_scenario_routes_and_the_activity_tally_over_http():
    import json
    import urllib.error
    import urllib.request

    from tests.simkit import running_sim

    cfg = replace(scenario.configure(SimConfig(), "sunday"), rivals=0, tick_seconds=30.0, latency_p50_ms=0.0)

    def call(url: str, path: str, body: dict | None = None, key: str | None = "sim-team1"):
        req = urllib.request.Request(
            url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
            headers={"X-Team-Key": key, "Content-Type": "application/json"} if key else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    with running_sim(cfg, run_clock=False) as (url, sim):
        sim.world.advance()  # tick 1: Chamberí out, Round 3
        assert call(url, "/api/news", key=None)[1] == {"news": []}
        _, levels = call(url, "/api/levels", key=None)
        assert {x["id"] for x in levels["levels"]} == {"chato", "pilar", "picaros", "banco", "taller"}
        _, dealers_view = call(url, "/api/dealers", key=None)
        assert {d["id"] for d in dealers_view["personas"]} == {"abuela", "chato", "pilar", "picaros", "banco"}
        status, body = call(url, "/api/taller", {"assets": [1, 2, 3]})
        assert status >= 400 and "error" in body
        status, body = call(url, "/api/threads", {"with": "nobody", "topic": {}})
        assert status == 404
        _, activity = call(url, "/sim/activity", key=None)
        refusals = activity["refusals"]["t01"]
        assert any(k.startswith("POST /api/taller ") for k in refusals) and any("/api/threads" in k for k in refusals)
        status, opened = call(url, "/api/threads", {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
        assert status == 200
        sends = call(url, "/sim/activity", key=None)[1]["sends"]["t01"]
        assert sends == {"1 POST /api/threads": 1}


def test_without_a_scenario_news_is_not_a_route():
    import urllib.error
    import urllib.request

    from tests.simkit import running_sim

    with running_sim(QUIET, run_clock=False) as (url, _):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url + "/api/news", timeout=10)
        assert e.value.code == 404
