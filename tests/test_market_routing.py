"""MM1: actual market demand, eligible counterparties and net proceeds choose the route."""

from copy import deepcopy

import pytest

from bazaar_agent.agents.market import best_venue, venues_from
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import TakerConfig
from tests.agent_fakes import CHEAP, OURS, RASTRO, FakePublic, bid, clock, rows
from tests.test_counter_bids import Team
from tests.test_maker import maker, posted
from tests.test_taker import taker
from tests.test_taker_addressed import Team as TakerTeam

T10 = {**CHEAP, "venue": "v07", "owner": "t10", "trades": 1}


def listed(offer):
    return {"id": offer["id"], "tick": 95, "type": "offer.listed", "actor": offer["maker"], "payload": {"offer": offer}}


def test_addressed_offer_chooses_lowest_fee_but_never_either_partys_venue():
    venues = venues_from({"venues": [RASTRO, T10, OURS]})
    assert best_venue(venues, "t01", 20, to="t04").id == "v07"
    assert best_venue(venues, "t01", 20, to="t10").id == "rastro"
    assert best_venue(venues_from({"venues": [T10, OURS]}), "t01", 20, to="t10") is None
    pending = {**T10, "pending_fee": {"fee_bps": 1000, "fee_per_card": 5, "effective_tick": 101}}
    assert best_venue(venues_from({"venues": [RASTRO, pending]}, 100), "t01", 20, to="t04").id == "rastro"


def test_maker_routes_one_public_copy_to_t10_for_card_specific_demand_without_board_reads(tmp_path):
    class NoBoard(FakePublic):
        def board(self, venue="rastro"):
            raise AssertionError("maker must not add per-market board reads")

    team = Team()
    m, _ = maker(tmp_path, team, NoBoard(venues=(RASTRO, T10, OURS)), live=True)
    events = [listed(bid(901, "LAT-03", 12, venue="v07", maker="t04"))]
    m.feed = MarketFeed(lambda n: events)
    m.on_tick(clock(tick_seconds=15))
    copies = [p for p in posted(team) if p[1].get("assets") == [4]]
    assert len(copies) == 1 and copies[0][3] == "v07"
    row = next(r for r in rows(tmp_path) if r.get("kind") == "post_ask" and r["inputs"]["ref"] == "LAT-03")
    assert row["inputs"]["venue"] == "v07" and row["inputs"]["price"] >= 10


@pytest.mark.parametrize(
    "bad", ["expired", "cancelled", "ours", "other_address", "unknown_expiry", "not_profitable", "claimed"]
)
def test_unavailable_or_unprofitable_demand_does_not_pull_a_public_ask(tmp_path, bad):
    offer = bid(901, "LAT-03", 12, venue="v07", maker="t04")
    if bad == "expired":
        offer["expires_tick"] = 100
    elif bad == "ours":
        offer["maker"] = "t01"
    elif bad == "other_address":
        offer["to"] = "t09"
    elif bad == "unknown_expiry":
        offer.pop("expires_tick")
    elif bad == "not_profitable":
        offer["give"]["cash"] = 5
    events = [listed(offer)]
    if bad == "cancelled":
        events.append({"id": 902, "tick": 99, "type": "offer.cancelled", "payload": {"offer": 901}})
    team = Team(offers=[{**offer, "status": "accepted"}] if bad == "claimed" else [])
    m, _ = maker(tmp_path, team, FakePublic(venues=(RASTRO, T10)), live=True)
    m.feed = MarketFeed(lambda n: events)
    m.on_tick(clock())
    assert not any(p[1].get("assets") == [4] and p[3] == "v07" for p in posted(team))


def test_maker_addressed_counter_can_use_t10_but_cannot_address_its_owner_there(tmp_path):
    for recipient, expected in (("t04", "v07"), ("t10", "rastro")):
        offer = {**bid(901, "LAT-03", 4, maker=recipient), "to": "t01"}
        team = Team(offers=[offer])
        m, _ = maker(tmp_path / recipient, team, FakePublic(venues=(RASTRO, T10)), live=True)
        m.on_tick(clock())
        counters = [p for p in posted(team) if p[4] == recipient and p[1].get("assets") == [4]]
        assert len(counters) == 1 and counters[0][3] == expected


def test_taker_sells_on_t10_when_lower_gross_bid_gives_higher_net_proceeds(tmp_path):
    # Rastro12 - ceil(.05*12)-1 =10; T10's11 with zero fees yields11.
    boards = {
        "rastro": [bid(901, "LAT-03", 12, maker="t04")],
        "v07": [bid(902, "LAT-03", 11, venue="v07", maker="t08")],
    }
    team = TakerTeam()
    public = FakePublic(boards=deepcopy(boards), venues=(RASTRO, T10, OURS))
    agent, _, ledger = taker(
        tmp_path, team, public, live=True, config=TakerConfig(max_dealer_threads=0, accept_bids=True)
    )
    actual_bid = {**boards["v07"][0], "maker": "t08"}
    public.boards["v07"][0]["maker"] = "market-alias"
    agent.feed = MarketFeed(lambda n: [listed(actual_bid)])
    agent.on_tick(clock(tick_seconds=15))
    assert team.sent == [("accept", 902, [4])]
    assert ledger.accepts_in_tick(100) == 1
    row = next(r for r in rows(tmp_path) if r.get("kind") == "accept_bid" and r["chosen"])
    assert row["inputs"]["venue"] == "v07"
    assert row["inputs"]["maker"] == "t08"  # exact public offer identity, not venue owner t10


@pytest.mark.parametrize("venue", [{**T10, "status": "closed"}, {**T10, "owner": "t01"}])
def test_public_demand_never_routes_to_our_own_or_inactive_market(venue):
    choices = venues_from({"venues": [RASTRO, venue]})
    assert best_venue(choices, "t01", 10, demand={"v07": 100}).id == "rastro"


def test_public_demand_is_ranked_after_pending_fees(tmp_path):
    high_fee = {**T10, "pending_fee": {"fee_bps": 1000, "fee_per_card": 5, "effective_tick": 101}}
    team = Team()
    m, _ = maker(tmp_path, team, FakePublic(venues=(RASTRO, high_fee)), live=True)
    m.feed = MarketFeed(lambda n: [listed(bid(901, "LAT-03", 14, venue="v07", maker="t04"))])
    m.on_tick(clock())
    # 14 minus the announced7 fee is below our10 ask: lifetime-activity fallback is Rastro.
    assert not any(p[1].get("assets") == [4] and p[3] == "v07" for p in posted(team))


def test_market_routing_cannot_publish_a_copy_already_promised(tmp_path):
    from bazaar_agent.agents import publication

    team = Team()
    m, _ = maker(tmp_path, team, FakePublic(venues=(RASTRO, T10)), live=True)
    m.feed = MarketFeed(lambda n: [listed(bid(901, "LAT-03", 12, venue="v07", maker="t04"))])
    publication.reserve(m.ledger, 99, 1.49, "t01", {"assets": [4]}, {"cash": 10})
    m.on_tick(clock())
    assert not any(4 in p[1].get("assets", []) for p in posted(team))


def test_late_buyer_selection_rechecks_that_buyers_own_venue(tmp_path, monkeypatch):
    from dataclasses import replace

    team = Team()
    m, _ = maker(tmp_path, team, FakePublic(venues=(RASTRO, T10)), live=True)
    m.feed = MarketFeed(lambda n: [listed(bid(901, "LAT-03", 12, venue="v07", maker="t04"))])
    monkeypatch.setattr(
        m, "_address", lambda run, target, venue: replace(target, to="t10") if target.ref == "LAT-03" else target
    )
    m.on_tick(clock())
    ask = next(p for p in posted(team) if p[1].get("assets") == [4])
    assert ask[3:] == ("rastro", "t10")


def test_late_price_change_rechecks_card_specific_demand(tmp_path, monkeypatch):
    from dataclasses import replace

    team = Team()
    m, _ = maker(tmp_path, team, FakePublic(venues=(RASTRO, T10)), live=True)
    m.feed = MarketFeed(lambda n: [listed(bid(901, "LAT-03", 12, venue="v07", maker="t04"))])
    monkeypatch.setattr(
        m,
        "_jev_price",
        lambda run, target, venue: (replace(target, price=15) if target.ref == "LAT-03" else target, None, None),
    )
    m.on_tick(clock())
    ask = next(p for p in posted(team) if p[1].get("assets") == [4])
    assert ask[2] == {"cash": 15} and ask[3] == "rastro"
