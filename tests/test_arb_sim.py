"""Arbitrage and duplicate buys end to end: our Taker, live, against the simulator served in this process over
real HTTP (tests/simkit). Never the real game. The sim's clock is turned by hand: one `advance()` per tick."""

import tempfile
from pathlib import Path

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from bazaar_agent.sdk import Bazaar, PublicBazaar
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import Clock
from bazaar_sim import broker, market
from tests.simkit import QUIET, running_sim

US = "t01"


def make_taker(url, **rules):
    team, public = Bazaar(url, "sim-team1", wait_on_tick=False, retries=0), PublicBazaar(url)
    tmp = Path(tempfile.mkdtemp())
    lines: list[str] = []
    params = load_strategy().params
    taker = Taker(
        team,
        public,
        rules=Guardrails(cash_floor=0, **rules),  # cash_floor 0: the sim starts every team at 400 P
        params=lambda tick: params,
        ledger=Ledger(tmp / "ledger.jsonl"),
        decisions=DecisionLog(tmp),
        feed=MarketFeed(lambda n: public.feed_window(n)),
        live=True,
        log=lines.append,
        config=TakerConfig(max_dealer_threads=0, duel_grace_s=0.0),
    )
    return taker, team, lines


def tick(taker, team, world):
    taker.on_tick(Clock.model_validate(team.clock()))
    world.advance()


def zero_fee_venue(w, owner="t04"):
    w.team(owner).unlocked.append("chato")
    return broker.open_venue(w, owner, {"name": "Zero", "fee_bps": 0, "rules": {"mechanism": "board"}})["venue"]


def shared_card(w, *teams):
    refs = set.intersection(*({a.ref for a in w.holdings(t) if a.kind == "card"} for t in teams))
    ref = sorted(refs)[0]
    return {t: next(a for a in w.holdings(t) if a.kind == "card" and a.ref == ref) for t in teams}


def test_buy_on_el_rastro_then_sell_the_same_copy_into_a_bid_on_a_zero_fee_venue():
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        vid = zero_fee_venue(w)
        card = next(a for a in w.holdings("t02") if a.kind == "card")
        market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 6}})
        market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 16}, "want": {"cards": [card.ref]}})
        taker, team, _ = make_taker(url, arb_enabled=True)
        cash0 = w.team(US).cash
        tick(taker, team, w)
        assert w.asset(card.id).owner == US
        tick(taker, team, w)
        assert w.asset(card.id).owner == "t03"  # the exact copy bought went out
        assert w.team(US).cash - cash0 > 0  # 16 − (6 + fee): +9 in the sim (it rounds the fee; we round up)


def test_switches_off_nothing_moves():
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        vid = zero_fee_venue(w)
        card = next(a for a in w.holdings("t02") if a.kind == "card")
        market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 1}})
        market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 30}, "want": {"cards": [card.ref]}})
        taker, team, _ = make_taker(url)
        cash0 = w.team(US).cash
        for _ in range(2):
            tick(taker, team, w)
        assert w.team(US).cash == cash0 and w.asset(card.id).owner == "t02"


def test_a_vanished_exit_leaves_the_card_with_us_for_the_maker():
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        vid = zero_fee_venue(w)
        card = next(a for a in w.holdings("t02") if a.kind == "card")
        market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 6}})
        bid = market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 16}, "want": {"cards": [card.ref]}})
        taker, team, lines = make_taker(url, arb_enabled=True)
        tick(taker, team, w)
        market.cancel(w, "t03", bid.id)
        tick(taker, team, w)
        assert w.asset(card.id).owner == US and taker.exits == {}
        assert any("is gone or moved" in line for line in lines)


def test_two_bids_one_card_each_and_a_resting_team_is_skipped():
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        vid = zero_fee_venue(w)
        copies = shared_card(w, "t02", "t06")
        ref = copies["t02"].ref
        market.offer_from_input(
            w, "t02", {"venue": "rastro", "give": {"assets": [copies["t02"].id]}, "want": {"cash": 6}}
        )
        b3 = market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 16}, "want": {"cards": [ref]}})
        b5 = market.offer_from_input(w, "t05", {"venue": vid, "give": {"cash": 14}, "want": {"cards": [ref]}})
        taker, team, _ = make_taker(url, arb_enabled=True)
        tick(taker, team, w)
        tick(taker, team, w)
        assert w.state.offers[b3.id].status == "settled" and w.state.offers[b5.id].status == "open"  # the best bid
        # t03 (our last exit buyer, resting) now asks less than t06: the taker skips t03 and buys t06's copy
        market.offer_from_input(
            w, "t03", {"venue": "rastro", "give": {"assets": [copies["t02"].id]}, "want": {"cash": 5}}
        )
        market.offer_from_input(
            w, "t06", {"venue": "rastro", "give": {"assets": [copies["t06"].id]}, "want": {"cash": 7}}
        )
        tick(taker, team, w)
        tick(taker, team, w)
        assert w.asset(copies["t06"].id).owner == "t05" and w.asset(copies["t02"].id).owner == "t03"


def test_a_duplicate_worth_its_price_is_bought_only_with_the_switch_on():
    with running_sim(QUIET, run_clock=False) as (url, sim):
        w = sim.world
        ours = {a.ref for a in w.holdings(US) if a.kind == "card"}
        team = Bazaar(url, "sim-team1", wait_on_tick=False, retries=0)
        candidates = [a for a in w.holdings("t02") if a.kind == "card" and a.ref in ours]
        card = max(candidates, key=lambda a: team.value(a.ref)["your_value"])
        value = team.value(card.ref)["your_value"]
        market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 1}})
        off, team, _ = make_taker(url)
        tick(off, team, w)
        assert w.asset(card.id).owner == "t02"
        on, team, lines = make_taker(url, dup_buy_enabled=True, dup_min_surplus=1.0)
        tick(on, team, w)
        assert w.asset(card.id).owner == US
        assert any(f"worth {value:g}" in line for line in lines)  # our value of one more copy = the sim's /me/value
