"""N14a, L3-L5 readiness: any unlocked dealer is planned the same way, trickster bait never closes a deal and is
never bid into, and a vault's legendary is never bought (no cap). Fakes only, no network."""

from copy import deepcopy

import pytest

from bazaar_agent.agents.dealer import BidPlan, Negotiation
from bazaar_agent.agents.desk import Conversation, meet_the_ask, plan_conversation
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_strategy import ME

ITEM = "LAV-08"


def conv(final_max=None):
    plan = BidPlan(18, 1, 26, final_max)
    return Conversation("trileros", ITEM, "uncommon", 40, "r", Negotiation(plan, [18]), 300, TICK)


def offer(give, want, final=True, maker="trileros", oid=9):
    return {"id": oid, "maker": maker, "status": "open", "final": final, "give": give, "want": want}


BAIT = {
    "another card": offer({"types": ["card:LAV-02"]}, {"cash": 5}),
    "a lower rarity, any card": offer({"types": ["card:uncommon"]}, {"cash": 5}),
    "our assets wanted too": offer({"types": [f"card:{ITEM}"]}, {"cash": 5, "assets": [{"id": 2, "ref": "LAV-06"}]}),
    "cash given on a buy": offer({"types": [f"card:{ITEM}"], "cash": 3}, {"cash": 5}),
    "two cards for one": offer({"types": [f"card:{ITEM}", "card:LAV-02"]}, {"cash": 5}),
    "an asset of another card": offer({"assets": [{"id": 77, "ref": "LAV-01"}]}, {"cash": 5}),
}


@pytest.mark.parametrize("bait", list(BAIT), ids=list(BAIT))
def test_trickster_bait_is_ignored_never_accepted_never_walked_from_never_met(bait):
    c = conv(final_max=29)
    dm = plan_conversation(c, {"status": "open", "standing_offers": [BAIT[bait]]}, 14)
    assert dm.ignored and dm.move.kind == "bid" and dm.move.price == 19  # our ladder goes on as if no offer
    assert (dm.ask, dm.final, dm.offer_id) == (None, False, None)
    assert meet_the_ask(dm).move.kind == "wait"  # the bait's price is never bid into


def test_a_bait_from_someone_else_in_the_thread_is_not_the_dealers_offer():
    c = conv()
    posing = offer({"types": [f"card:{ITEM}"]}, {"cash": 5}, maker="t07")
    assert plan_conversation(c, {"status": "open", "standing_offers": [posing]}, 14).ask is None


def test_the_real_final_behind_a_newer_bait_is_not_taken_on_the_bait_s_terms():
    c = conv(final_max=29)
    real = offer({"types": [f"card:{ITEM}"]}, {"cash": 28}, oid=8)
    bait = offer({"types": ["card:LAV-02"]}, {"cash": 5}, oid=9)
    dm = plan_conversation(c, {"status": "open", "standing_offers": [real, bait]}, 14)
    assert dm.move.kind == "bid" and dm.offer_id is None  # the newest offer is the bait: nothing is accepted


# ---------------------------------------------------------------- a new dealer level, through the taker

COLECCIONISTA = {
    "id": "coleccionista",
    "status": "active",
    "level": 3,
    "menu": {"sells": [{"rarity": "uncommon", "sets": "released", "list_price": 28}]},
}
BANCO = {
    "id": "banquero",
    "status": "active",
    "level": 5,
    "menu": {"sells": [{"rarity": "legendary", "sets": "released", "list_price": 400, "per_team_per_hour": 1}]},
}


def taker(tmp_path, team, dealers, lift=0.0):
    kw = {**parts(tmp_path, dealer_final_lift=lift), "feed": MarketFeed(lambda n: [])}
    return Taker(
        team,
        FakePublic(dealers=dealers, events=[]),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        **kw,
    )


def test_an_unknown_level_3_dealer_is_planned_like_any_other_and_never_above_the_cap(tmp_path):
    team = FakeTeam(me={**deepcopy(ME), "unlocked": ["coleccionista"]})
    taker(tmp_path / "above", team, [COLECCIONISTA]).on_tick(clock())
    assert team.sent == []  # it lists uncommons at 28, above our cap 26: skipped like Chato, as today
    cheaper = deepcopy(COLECCIONISTA)
    cheaper["menu"]["sells"][0]["list_price"] = 25
    taker(tmp_path, team, [cheaper]).on_tick(clock())
    assert ("open_thread", "coleccionista", {"buy": {"card": ITEM}}) in team.sent
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["changed_by"] == [] and row["inputs"]["final_max"] is None  # the lift off: as today
    assert all(s[2] <= 26 for s in team.sent if s[0] == "say")


def test_with_the_lift_an_unknown_dealer_without_fills_gets_no_lifted_final(tmp_path):
    team = FakeTeam(me={**deepcopy(ME), "unlocked": ["coleccionista"]})
    cheaper = deepcopy(COLECCIONISTA)
    cheaper["menu"]["sells"][0]["list_price"] = 25
    taker(tmp_path, team, [cheaper], lift=0.15).on_tick(clock())
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert row["inputs"]["final_max"] is None and row["inputs"]["changed_by"] == []  # no price history: as today


def test_a_vault_legendary_is_never_bought_without_a_cap(tmp_path):
    team = FakeTeam(me={**deepcopy(ME), "unlocked": ["banquero"]})
    taker(tmp_path, team, [BANCO], lift=0.25).on_tick(clock())
    assert not [s for s in team.sent if s[0] in ("open_thread", "say", "accept")]
