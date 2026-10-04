"""PM1: configured partners get durable public supply, without overriding live demand."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from bazaar_agent.agents.market import best_venue, venues_from
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.strategy import load_strategy
from tests.agent_fakes import CHEAP, OURS, RASTRO, FakePublic, bid, clock, our_ask
from tests.test_counter_bids import Team
from tests.test_maker import maker, posted
from tests.test_market_routing import listed
from tests.test_strategy import EVENTS, PARAMS

PARTNERS = (
    {**CHEAP, "venue": "v15", "owner": "t15"},
    {**CHEAP, "venue": "v28", "owner": "t18"},
)
OWNERS = ("t15", "t18")


def configured(tmp_path, team, venues=(RASTRO, *PARTNERS, OURS), events=EVENTS):
    agent, lines = maker(tmp_path, team, FakePublic(venues=venues), live=True)
    agent.params = lambda tick: PARAMS.model_copy(update={"preferred_sell_venue_owners": "t15,t18"})
    agent.feed = MarketFeed(lambda n: events)
    return agent, lines


def test_config_defaults_disabled_and_deployed_owner_ids_are_validated():
    assert PARAMS.preferred_sell_venue_owners == "none"
    assert load_strategy().params.preferred_sell_venue_owners == "t04,t10,t15,t18"
    with pytest.raises(ValidationError):
        type(PARAMS)(**{**PARAMS.model_dump(), "preferred_sell_venue_owners": "v15,t18"})


def test_new_public_asks_are_split_stably_across_requested_markets_without_extra_reads(tmp_path):
    class NoBoard(FakePublic):
        def board(self, venue="rastro"):
            raise AssertionError("no extra board requests")

    team = Team()
    agent, _ = configured(tmp_path, team)
    agent.public = NoBoard(venues=(RASTRO, *PARTNERS, OURS))
    agent.on_tick(clock(tick_seconds=15))
    asks = {p[1]["assets"][0]: p[3] for p in posted(team) if p[1].get("assets")}
    assert asks == {4: "v15", 5: "v28"}
    assert {p[3] for p in posted(team) if p[1].get("cash")} == {"rastro"}  # bids unchanged


@pytest.mark.parametrize("key", [4, 5])
def test_crossing_demand_wins_and_partner_list_order_does_not_change_distribution(key):
    venues = venues_from({"venues": [RASTRO, *reversed(PARTNERS), OURS]})
    kwargs = {"preferred_owners": OWNERS, "spread_key": key}
    assert best_venue(venues, "t01", 10, **kwargs).id == ("v15" if key == 4 else "v28")
    assert best_venue(venues, "t01", 10, demand={"rastro": 12}, **kwargs).id == "rastro"
    assert best_venue(venues, "t01", 10, demand={"rastro": 12, "v15": 13}, **kwargs).id == "v15"


@pytest.mark.parametrize("bad", ["closed", "owned", "fee", "pending_fee", "absent"])
def test_unavailable_or_nonzero_fee_partners_fall_back_to_existing_route(bad):
    partners = deepcopy(PARTNERS)
    for row in partners:
        if bad == "closed":
            row["status"] = "closed"
        elif bad == "owned":
            row["owner"] = "t01"
        elif bad == "fee":
            row["fee_per_card"] = 1
        elif bad == "pending_fee":
            row["pending_fee"] = {"fee_bps": 100, "fee_per_card": 1, "effective_tick": 101}
    venues = venues_from({"venues": [RASTRO, *(partners if bad != "absent" else []), OURS]}, 100)
    assert best_venue(venues, "t01", 10, preferred_owners=OWNERS, spread_key=4).id == "rastro"


def test_addressed_offers_cannot_choose_the_recipient_owner():
    venues = venues_from({"venues": [*PARTNERS, OURS]})
    assert best_venue(venues, "t01", 10, to="t15", preferred_owners=OWNERS).id == "v28"
    assert best_venue(venues, "t01", 10, to="t18", preferred_owners=OWNERS).id == "v15"


def test_existing_ask_migrates_once_then_remains_on_its_preferred_venue(tmp_path):
    class StandingTeam(Team):
        def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
            result = super().list_offer(give, want, venue, to, expires_in_ticks)
            if give.get("assets"):
                asset = next(a for a in self._me["assets"] if a["id"] == give["assets"][0])
                self.offers.append(our_ask(result["id"], asset["id"], asset["ref"], want["cash"], venue=venue))
            return result

    team = StandingTeam(offers=[our_ask(800, 4, "LAT-03", 10)])
    agent, _ = configured(tmp_path, team)
    agent.on_tick(clock(tick_seconds=15))
    copies = [p for p in posted(team) if p[1].get("assets") == [4]]
    assert copies == [("list_offer", {"assets": [4]}, {"cash": 10}, "v15", None)]
    assert team.sent.index(("cancel", 800)) < team.sent.index(copies[0])
    team.sent.clear()
    team.now = clock(tick=101, tick_seconds=15)
    agent.on_tick(team.now)
    assert not any(p[1].get("assets") == [4] for p in posted(team))
    assert not any(p[0] == "cancel" for p in team.sent)


@pytest.mark.parametrize("protected", ["hands_off", "addressed", "current_demand"])
def test_partner_preference_never_migrates_hands_off_addressed_or_better_current_demand(tmp_path, protected):
    offer = our_ask(800, 4, "LAT-03", 10)
    if protected == "addressed":
        offer["to"] = "t18"
    events = [*EVENTS]
    if protected == "current_demand":
        events.append(listed(bid(901, "LAT-03", 14, maker="t04")))
    team = Team(offers=[offer])
    agent, _ = configured(tmp_path, team, events=events)
    if protected == "hands_off":
        agent.ledger.record("listing", 99, 1.4, 10, "hands-off:800")
    agent.on_tick(clock(tick_seconds=15))
    assert ("cancel", 800) not in team.sent
    assert not any(p[1].get("assets") == [4] for p in posted(team))


@pytest.mark.parametrize("failure", ["unknown_cancel", "refused_cancel", "no_slot", "expired_tick", "fresh_promise"])
def test_preference_relocation_reuses_cancel_and_publication_safety(tmp_path, failure):
    from bazaar_agent.agents import publication
    from bazaar_agent.sdk import BazaarError

    class Refusing(Team):
        def cancel(self, offer_id):
            if failure in ("unknown_cancel", "refused_cancel"):
                self.sent.append(("cancel", offer_id))
                raise BazaarError("network_error" if failure == "unknown_cancel" else "offer_not_open", "test", 0)
            body = super().cancel(offer_id)
            if failure == "fresh_promise":
                publication.reserve(agent.ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 11})
            return body

    team = Refusing(offers=[our_ask(800, 4, "LAT-03", 10)])
    agent, _ = configured(tmp_path, team)
    if failure == "no_slot":
        for _ in range(12):
            agent.ledger.record("listing", 100, 1.5, 1, "other")
    agent.on_tick(clock(tick_seconds=15, next_tick_in=0 if failure == "expired_tick" else 12))
    assert not any(p[1].get("assets") == [4] for p in posted(team))
    if failure in ("no_slot", "expired_tick"):
        assert ("cancel", 800) not in team.sent


def test_existing_preferred_ask_does_not_rotate_when_other_partner_appears(tmp_path):
    # Asset4 maps to v15 for new publication, but its valid existing v28 ask stays there.
    team = Team(offers=[our_ask(800, 4, "LAT-03", 10, venue="v28")])
    agent, _ = configured(tmp_path, team)
    agent.on_tick(clock(tick_seconds=15))
    assert ("cancel", 800) not in team.sent
    assert not any(p[1].get("assets") == [4] for p in posted(team))


def test_better_crossing_demand_can_move_an_ask_out_of_a_preferred_market(tmp_path):
    team = Team(offers=[our_ask(800, 4, "LAT-03", 10, venue="v15")])
    agent, _ = configured(tmp_path, team, events=[*EVENTS, listed(bid(901, "LAT-03", 14, maker="t04"))])
    agent.on_tick(clock(tick_seconds=15))
    assert ("cancel", 800) in team.sent
    assert [(p[2], p[3]) for p in posted(team) if p[1].get("assets") == [4]] == [({"cash": 10}, "rastro")]


def test_live_configuration_distributes_three_eligible_copies_across_all_three_alliance_markets(tmp_path):
    from tests.test_maker_supply import RULES, inventory

    me, catalog = inventory()
    refs = {"LAT-01", "LAT-02", "LAT-03"}
    me["assets"] = [a for a in me["assets"] if a["ref"] in refs]
    me["album"]["pages"] = [p for p in me["album"]["pages"] if p["set"] == "LAT"]
    catalog["sets"] = [s for s in catalog["sets"] if s["id"] == "LAT"]
    catalog["sets"][0]["cards"] = [c for c in catalog["sets"][0]["cards"] if c["id"] in refs]
    gacela = {**CHEAP, "venue": "v05", "owner": "t04"}
    team = Team(me=me)
    agent, _ = maker(
        tmp_path,
        team,
        FakePublic(venues=(RASTRO, gacela, *PARTNERS, OURS), catalog=catalog, dealers=[], events=[]),
        live=True,
        **RULES.model_dump(),
    )
    agent.params = lambda tick: load_strategy().params
    agent.on_tick(clock(tick_seconds=15))
    asks = [p for p in posted(team) if p[1].get("assets")]
    assert len(asks) == 3
    assert {p[3] for p in asks} == {"v05", "v15", "v28"}
    values = {a["id"]: a["your_value"] for a in me["assets"]}
    assert all(p[2]["cash"] > values[p[1]["assets"][0]] for p in asks)


def test_rival_avoidance_never_drops_a_requested_partner_market():
    venues = venues_from({"venues": [RASTRO, *PARTNERS, OURS]})
    # t15 is a podium rival but a configured partner: still used; t18 stays usable too
    assert best_venue(venues, "t01", 10, preferred_owners=OWNERS, spread_key=4, avoid={"t15"}).id == "v15"
    # without the partner setting the same rival venue is skipped
    assert best_venue(venues, "t01", 10, avoid={"t15", "t18"}).id == "rastro"
