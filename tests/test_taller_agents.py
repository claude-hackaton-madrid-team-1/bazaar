"""El Taller in the agents (TL1): the taker converts at most one triple a tick, the maker keeps spare commons."""

from copy import deepcopy

from bazaar_agent.agents.maker import Target, taller_stock
from bazaar_agent.agents.market import OpenOffer
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.taller import TALLER_KIND
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, rows
from tests.test_strategy import ME
from tests.test_taker import taker

SPARES = [{"id": 100 + i, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2} for i in range(5)]


def me_with_spares():
    me = deepcopy(ME)
    me["assets"] = me["assets"] + deepcopy(SPARES)  # LAT-03 x7: six spares
    return me


def tallers(team):
    return [s for s in team.sent if s[0] == "taller"]


def test_the_taker_converts_one_triple_of_free_spares_per_tick(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    sent = tallers(team)
    assert len(sent) == 1  # six spares, one conversion this tick
    assert len(sent[0][1]) == 3
    assert ledger.count_since(TALLER_KIND, 0.0) == 1
    kinds = [r.get("kind") for r in rows(tmp_path)]
    assert TALLER_KIND in kinds and "taller_pulled" in kinds
    assert team.reads.count("me") >= 2  # album first: planned on a fresh /me, read again after the pull


def test_the_taker_dry_run_records_the_plan_and_sends_nothing(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=False, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert ledger.count_since(TALLER_KIND, 0.0) == 0
    assert any(r.get("kind") == TALLER_KIND for r in rows(tmp_path))


def test_the_taker_waits_while_a_duel_is_near_its_deadline(tmp_path):
    team = FakeTeam(me=me_with_spares())
    team.live_duels = [{"duel": 7, "status": "live", "deadline_tick": TICK + 2}]
    t, lines, _ = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert any("El Taller waits" in line for line in lines)


def test_the_taker_stops_at_the_hourly_cap(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True, max_taller_per_game_hour=2)
    for _ in range(2):
        ledger.record(TALLER_KIND, TICK - 1, 1.4, 0, "LAT-03,LAT-03,LAT-03")
    t.on_tick(clock())
    assert tallers(team) == []
    assert "duels" not in team.reads  # the cap is read before any extra request


def test_the_taker_sends_nothing_with_the_switch_off_or_the_kill_switch_on(tmp_path):
    pause = tmp_path / "PAUSE"
    pause.touch()  # the kill switch: the live file's trading_enabled, or the pause file
    for rules in ({"taller_enabled": False}, {"taller_enabled": True, "pause_file": str(pause)}):
        team = FakeTeam(me=me_with_spares())
        t, _, _ = taker(tmp_path / str(len(rules)), team, FakePublic(), live=True, **rules)
        t.on_tick(clock())
        assert tallers(team) == []


def test_the_taker_never_feeds_a_card_held_once_or_twice(tmp_path):
    team = FakeTeam()  # ME: LAT-03 x2, one spare only
    t, _, _ = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    t.on_tick(clock())
    assert tallers(team) == []
    assert "duels" not in team.reads  # no plan: no request at all


def target(side, ref, rarity, asset_id):
    return Target(side, ref, rarity, 10, asset_id, 1.0, 1.0, "test")


def offer(asset_id, ref="LAT-03"):
    return OpenOffer(1, "ask", ref, 10, "rastro", asset_id, 140, 90)


def test_the_maker_posts_no_new_ask_for_a_spare_common_while_the_taller_is_on():
    targets = [
        target("ask", "LAT-03", "common", 3),  # new: El Taller stock
        target("ask", "LAT-04", "common", 9),  # already asked: kept, so it is never cancelled
        target("ask", "LAT-09", "uncommon", 5),
        target("bid", "LAV-08", "uncommon", None),
    ]
    kept = taller_stock(targets, [offer(9, "LAT-04")], Guardrails(taller_enabled=True))
    assert [t.ref for t in kept] == ["LAT-04", "LAT-09", "LAV-08"]
    assert taller_stock(targets, [], Guardrails(taller_enabled=False)) == targets


def test_the_taker_waits_while_a_spare_copy_may_be_in_a_settling_accept(tmp_path):
    team = FakeTeam(me=me_with_spares())
    t, lines, ledger = taker(tmp_path, team, FakePublic(), live=True, taller_enabled=True)
    ledger.record("accept", TICK - 1, 1.49, 6, "LAT-03")  # e.g. a bid for LAT-03 we accepted last tick
    t.on_tick(clock())
    assert tallers(team) == []
    assert any("still settling" in line for line in lines)
