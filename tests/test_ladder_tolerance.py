"""`dealer_ladder_value_tolerance` (Sun 4 Oct): a taker dealer buy may pass the official value by this much while that
dealer's level has an empty ladder slot this round; 0 (the default) is the official-value cap exactly as before.
Saturday: 17 Pícaros RET-09/RET-10 threads walked at "price 50 > official value 49"."""

import pytest

from bazaar_agent import guardrails as gr
from bazaar_agent.agents import taker as taker_module
from bazaar_agent.agents.ladder_probe import LadderSlots, plan_one
from bazaar_agent.official_values import OfficialValues
from tests.agent_fakes import TICK, FakePublic, clock
from tests.test_ladder_probe import RULES as PROBE_RULES
from tests.test_ladder_probe import market, opens
from tests.test_official_value_agents import ValuedTeam, at, her_ask, taker
from tests.test_official_values import Reader

pytestmark = pytest.mark.official_values

RULES = gr.Guardrails(cash_floor=0, venue_bond_reserve=0, max_spend_per_game_hour=1000)
TOLERANT = RULES.model_copy(update={"dealer_ladder_value_tolerance": 2.0})


def ctx(open_dealers=frozenset({"picaros"})):
    values = OfficialValues(Reader({"RET-10": 49.0, "RET-11": 140.0}))
    return gr.Context(cash=400, held={}, tick=7, t_hours=1.0, stops=(), values=values, ladder_open=open_dealers)


def test_zero_keeps_the_official_value_cap_exactly():
    verdict = gr.check(gr.Action("bid", "RET-10", "rare", 50, dealer="picaros"), ctx(), RULES)
    assert verdict.violations == ("price 50 > official value 49 of RET-10 (GET /api/me/value)",)


def test_a_dealer_buy_of_an_open_level_may_pass_the_official_value_by_the_tolerance_only():
    for kind in ("buy", "bid", "accept_buy"):
        assert gr.check(gr.Action(kind, "RET-10", "rare", 51, dealer="picaros"), ctx(), TOLERANT).allowed
    verdict = gr.check(gr.Action("bid", "RET-10", "rare", 52, dealer="picaros"), ctx(), TOLERANT)
    assert verdict.violations == (
        "price 52 > official value 49 + dealer_ladder_value_tolerance 2 of RET-10 (GET /api/me/value)",
    )


def test_no_tolerance_for_a_full_level_a_team_trade_a_board_ask_or_an_epic():
    full = gr.check(gr.Action("bid", "RET-10", "rare", 50, dealer="picaros"), ctx(frozenset()), TOLERANT)
    assert not full.allowed  # the level's three slots are scored: the cap is back
    team = gr.Action("accept_buy", "RET-10", "rare", 50, counterparty="t05", dealer="picaros")
    assert not gr.check(team, ctx(), TOLERANT).allowed
    assert not gr.check(gr.Action("accept_buy", "RET-10", "rare", 50), ctx(), TOLERANT).allowed  # a board ask
    epic = TOLERANT.model_copy(update={"max_price_epic": 240, "off_page_min_surplus": 10})
    assert not gr.check(gr.Action("bid", "RET-11", "epic", 131, dealer="picaros"), ctx(), epic).allowed
    assert gr.check(gr.Action("bid", "RET-11", "epic", 130, dealer="picaros"), ctx(), epic).allowed  # 140 - 10


def test_the_margin_still_counts_and_the_tolerance_never_lifts_the_rarity_cap():
    both = TOLERANT.model_copy(update={"official_value_margin": 1.0})
    assert gr.check(gr.Action("bid", "RET-10", "rare", 50, dealer="picaros"), ctx(), both).allowed  # 49 - 1 + 2
    verdict = gr.check(gr.Action("bid", "RET-10", "rare", 51, dealer="picaros"), ctx(), both)
    assert "official value 49 + dealer_ladder_value_tolerance 1" in str(verdict)
    capped = TOLERANT.model_copy(update={"max_price_rare": 50})
    assert "max_price_rare 50" in str(gr.check(gr.Action("bid", "RET-10", "rare", 51, dealer="picaros"), ctx(), capped))


def test_the_tolerance_is_bounded():
    with pytest.raises(ValueError):
        gr.Guardrails(dealer_ladder_value_tolerance=11)


def test_the_probe_plans_its_top_with_the_same_lift_as_the_guardrails():
    m, rules = market(), PROBE_RULES.model_copy(update={"max_price_common": 20, "dealer_ladder_value_tolerance": 2.0})
    o = opens(m)
    capped = plan_one(m, "abuela", o, rules, 130, lambda ref: 10.0)
    lifted = plan_one(m, "abuela", o, rules, 130, lambda ref: 10.0, LadderSlots({"abuela": 1}, {}))
    assert capped is not None and lifted is not None and (capped.top, lifted.top) == (10, 12)
    full = plan_one(m, "abuela", o, rules, 130, lambda ref: 10.0, LadderSlots({"abuela": 1}, {"abuela": 3}))
    assert full is not None and full.top == 10  # no lift for a full level (plan_probes skips it anyway)


def test_the_taker_bids_over_the_official_value_only_while_the_level_has_an_empty_slot(tmp_path, monkeypatch):
    # tests/test_official_value_agents: with LAV-08 at 18.5, her ask 24 calls for 19, refused by the cap. With a
    # tolerance of 1 and Abuela's level 1 still open this round, 19 goes; with her level full it is refused again.
    monkeypatch.setattr(taker_module, "ladder_deals", lambda events, us: {})
    team = ValuedTeam(values={"LAV-08": 40.0})
    t, _ = taker(tmp_path / "open", team, FakePublic(), live=True, dealers=3, dealer_ladder_value_tolerance=1.0)
    t.on_tick(clock())
    team.values["LAV-08"] = 18.5  # Revalidate a lower value during an existing conversation.
    her_ask(team, 5000, 800, 24)
    t.on_tick(at(team, TICK + 1))
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18), ("say", 5000, 19)]

    monkeypatch.setattr(taker_module, "ladder_deals", lambda events, us: {"abuela": 3})
    full = ValuedTeam(values={"LAV-08": 40.0})
    t, lines = taker(tmp_path / "full", full, FakePublic(), live=True, dealers=3, dealer_ladder_value_tolerance=1.0)
    t.on_tick(clock())
    full.values["LAV-08"] = 18.5
    her_ask(full, 5000, 800, 24)
    t.on_tick(at(full, TICK + 1))
    assert [s for s in full.sent if s[0] == "say"] == [("say", 5000, 18)]
    assert any("price 19 > official value 18.5 of LAV-08" in line for line in lines)
