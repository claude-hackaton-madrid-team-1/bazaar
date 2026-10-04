"""Ladder slots (Sun 4 Oct): the probe skips a level whose three slots this round are scored, plans the emptiest
level first, and says once per game hour why an empty level gets no probe (Saturday ended with an empty Chato L2
slot and all three Banco L5 slots empty; Pilar and Banco sell us no card)."""

from copy import deepcopy

from bazaar_agent.agents import taker as taker_module
from bazaar_agent.agents.ladder_probe import (
    LadderSlots,
    dealer_levels,
    opening_asks,
    plan_probes,
    probe_state,
    sells_us_cards,
    unfillable,
)
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import build_market
from bazaar_agent.ticks import Clock
from tests.agent_fakes import TICK, clock, rows
from tests.test_intel import settle
from tests.test_ladder_probe import ABUELA14, ValueTeam, gate, probe_rows, taker
from tests.test_strategy import CATALOG, EVENTS, ME

RULES = Guardrails()
# The live menus of Saturday's L2, L3 and L5 dealers (`traders`), trimmed to what the planner reads.
CHATO = {
    "id": "chato",
    "status": "active",
    "level": 2,
    "menu": {
        "sells": [
            {"pack": "sobre_plata", "list_price": 150, "opening_ask": 188},
            {"rarity": "common", "sets": "released", "list_price": 10, "opening_ask": 14},
            {"rarity": "rare", "sets": "released", "list_price": 77},
        ]
    },
}
PILAR = {"id": "pilar", "status": "active", "level": 3, "menu": {"sells": [{"pack": "sobre_oro", "list_price": 420}]}}
BANCO = {
    "id": "banco",
    "status": "active",
    "level": 5,
    "menu": {"sells": [{"pack": "sobre_oro", "list_price": 420}, {"rarity": "legendary", "list_price": 585}]},
}
CHATO_FILL = settle(40, 40, "chato", "t03", "LAV-02", 8, tick=6, kind="card", persona="chato")


def two_dealer_market():
    me = {**deepcopy(ME), "unlocked": ["abuela", "chato"]}
    events = [*EVENTS, CHATO_FILL]
    dealers = [ABUELA14, CHATO]
    m = build_market(me, CATALOG, events, dealers)
    return m, opening_asks(m, events, dealers)


# ---------------------------------------------------------------- the choice (pure)


def test_slots_count_scored_deals_per_level_and_rank_the_emptiest_higher_level_first():
    levels = dealer_levels([ABUELA14, CHATO, PILAR, BANCO, {"id": "x", "level": None}])
    assert levels == {"abuela": 1, "chato": 2, "pilar": 3, "banco": 5}
    slots = LadderSlots(levels, {"abuela": 3, "chato": 1, "pilar": 2})
    assert slots.full("abuela") and not slots.full("chato") and not slots.full("unknown")
    assert (slots.empty(1), slots.empty(2), slots.empty(5)) == (0, 2, 3)
    assert sorted(["abuela", "chato", "pilar", "banco"], key=slots.rank) == ["banco", "chato", "pilar", "abuela"]
    assert slots.facts() == {"L1": "3/3", "L2": "1/3", "L3": "2/3", "L5": "0/3"}


def test_a_full_level_is_not_probed_and_the_emptiest_level_is_planned_first():
    m, opens = two_dealer_market()
    levels = {"abuela": 1, "chato": 2}
    value = lambda ref: 12.0  # noqa: E731
    both = plan_probes(m, opens, RULES, 130, value_of=value, slots=LadderSlots(levels, {}))
    assert [p.dealer for p in both] == ["chato", "abuela"]  # both empty: the higher level first
    chato_scored = plan_probes(m, opens, RULES, 130, value_of=value, slots=LadderSlots(levels, {"chato": 2}))
    assert [p.dealer for p in chato_scored] == ["abuela", "chato"]  # 0 of 3 before 2 of 3
    abuela_full = plan_probes(m, opens, RULES, 130, value_of=value, slots=LadderSlots(levels, {"abuela": 3}))
    assert [p.dealer for p in abuela_full] == ["chato"]
    assert [p.dealer for p in plan_probes(m, opens, RULES, 130, value_of=value)] == ["abuela", "chato"]  # as before


def test_pilar_and_banco_sell_us_no_card_so_only_a_dealer_sell_fills_their_level():
    assert sells_us_cards(CHATO, RULES) and sells_us_cards(ABUELA14, RULES)
    assert not sells_us_cards(PILAR, RULES) and not sells_us_cards(BANCO, RULES)  # a pack; legendaries: no cap
    slots = LadderSlots(dealer_levels([ABUELA14, CHATO, PILAR, BANCO]), {"abuela": 3, "pilar": 1})
    gaps = unfillable([ABUELA14, CHATO, PILAR, BANCO], slots, ["abuela", "chato", "pilar"], RULES, ["chato"])
    assert [(lv, d) for lv, d, _ in gaps] == [(3, "pilar"), (5, "banco")]  # L1 full, L2 has its probe
    assert all("only a dealer sell" in why for _, _, why in gaps) and gaps[0][2].startswith("L3 1/3 this round")
    locked = unfillable([CHATO], LadderSlots({"chato": 2}, {}), [], RULES, [])
    assert "not unlocked" in locked[0][2]
    gated = unfillable([CHATO], LadderSlots({"chato": 2}, {}), ["chato"], RULES, [], blocked="the gate said no")
    assert gated[0][2].endswith("chato the gate said no")


def test_the_gate_sees_the_slots_scored_this_round():
    state = probe_state([], 400, 5, 130, 0, {"abuela": 1}, LadderSlots({"abuela": 1, "chato": 2}, {"abuela": 1}))
    assert state["ladder"]["slots_scored_this_round"] == {"L1": "1/3", "L2": "0/3"}


# ---------------------------------------------------------------- the taker


def slot_rows(root):
    return [r for r in rows(root) if r.get("kind") == "ladder_slot"]


def test_the_taker_probes_no_full_level_and_says_why_an_empty_level_gets_no_probe(tmp_path, monkeypatch):
    from tests import test_ladder_probe as lp

    monkeypatch.setattr(lp, "DEALERS", [ABUELA14, PILAR])
    monkeypatch.setattr(taker_module, "ladder_deals", lambda events, us: {"abuela": 3})  # L1 scored this round
    team = ValueTeam(me={**deepcopy(ME), "unlocked": ["abuela", "pilar"]})
    g, asks = gate("yes")
    t, _ = taker(tmp_path, team, g, live=True)
    t.on_tick(clock())
    assert [s for s in team.sent if s[0] == "open_thread"] == [] and probe_rows(tmp_path) == []
    assert asks == []  # nothing to plan: the gate is not even asked
    (row,) = slot_rows(tmp_path)
    assert (row["inputs"]["level"], row["inputs"]["dealer"], row["status"]) == (3, "pilar", "rejected")
    assert "only a dealer sell" in row["reason"]
    team.now = clock(tick=TICK + 1)
    t.on_tick(clock(tick=TICK + 1))
    assert len(slot_rows(tmp_path)) == 1  # once per game hour
    later = Clock(tick=TICK + 2, tick_seconds=60.0, next_tick_in=40.0, t_hours=2.1, limits={})
    team.now = later
    t.on_tick(later)
    assert len(slot_rows(tmp_path)) == 2  # the next game hour says it again


def test_an_empty_level_is_probed_and_a_gate_no_is_named_on_its_slot_row(tmp_path, monkeypatch):
    monkeypatch.setattr(taker_module, "ladder_deals", lambda events, us: {"abuela": 2})
    team = ValueTeam()
    g, asks = gate("yes")
    t, _ = taker(tmp_path / "yes", team, g, live=True)
    t.on_tick(clock())
    assert team.sent[0] == ("open_thread", "abuela", {"buy": {"card": "LAV-02"}})
    assert slot_rows(tmp_path / "yes") == []  # its probe fills it
    assert asks[0]["ladder"]["slots_scored_this_round"] == {"L1": "2/3"}

    no = ValueTeam()
    t, _ = taker(tmp_path / "no", no, gate("no")[0], live=True)
    t.on_tick(clock())
    assert [s for s in no.sent if s[0] == "open_thread"] == []
    (row,) = slot_rows(tmp_path / "no")
    assert row["inputs"]["dealer"] == "abuela" and "gate is not a decided yes" in row["reason"]
