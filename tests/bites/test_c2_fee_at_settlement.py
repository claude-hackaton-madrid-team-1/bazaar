"""Bite hunt C2: the venue fee at settlement when a fee change is announced (`venue.fee_announced`,
`effective_tick`) and an accept made at tick T settles at T+1 = effective_tick.

Offline: the in-process simulator on a hand-turned clock, and the taker on fakes. A test FAILS when the
bug it names exists on the code under test.
"""

from __future__ import annotations

import math

import pytest

from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.taker import Taker, TakerConfig, ask_candidates
from bazaar_agent.guardrails import Guardrails
from tests.agent_fakes import RASTRO, TICK, FakePublic, FakeTeam, ask, clock, parts
from tests.bites.strictness import STRICT
from tests.test_strategy import PARAMS
from tests.test_strategy import market as strategy_market

market = pytest.importorskip("bazaar_sim.market")  # branches cut before the simulator landed skip
manual_world = pytest.importorskip("tests.simkit").manual_world

US, THEM = "t01", "t02"
OLD = {"fee_bps": 0, "fee_per_card": 0}
NEW = {"fee_bps": 1000, "fee_per_card": 5}  # the RULES cap: 10 % and 5 P per card


def rival_venue(**extra):
    """A rival team venue at 0 fee that announced the capped fee, effective next tick."""
    return {
        "venue": "v07",
        "owner": "t07",
        **OLD,
        "status": "open",
        "trades": 3,
        "rules": {"mechanism": "board"},
        "pending_fee": {**NEW, "effective_tick": TICK + 1},
        **extra,
    }


# ---------------------------------------------------------------- the simulator: which fee settles?


def test_sim_charges_the_accept_time_fee_when_the_change_takes_effect_on_the_settlement_tick():
    """Documents the simulator's answer (expected PASS): `World.advance()` runs `market.settle_due` BEFORE
    `market.venue_tick` (which applies `pending_fee`), so an accept at T settles at T+1 with the OLD fee.
    The real server's ordering is NOT verified by this; it is the sim's model."""
    m = manual_world()
    w = m.world
    rastro = w.state.venues["rastro"]  # 500 bps + 1 P
    old_fee = market.venue_fee(w, "rastro", 12, 1)  # round(0.6) + 1 = 2 (equal to the agents' ceil here)
    rastro.pending_fee = {**NEW, "effective_tick": w.tick + 1}
    card = next(a.id for a in w.holdings(THEM) if a.kind == "card")
    offer = market.offer_from_input(w, THEM, {"venue": "rastro", "give": {"assets": [card]}, "want": {"cash": 12}})
    cash = w.team(US).cash
    market.accept(w, US, offer.id, {})
    m.step()  # tick T+1: settle_due, then venue_tick flips the fee
    paid = cash - w.team(US).cash
    settlement = [e for e in w.state.events if e.type == "settlement"][-1].payload
    print(f"sim: paid {paid} = 12 + fee {settlement['fee']} (old fee {old_fee}); fee now {rastro.fee_bps} bps")
    assert rastro.fee_bps == NEW["fee_bps"]  # the change did take effect on that very tick
    assert paid == 12 + old_fee


@pytest.mark.xfail(strict=STRICT, reason="BITE X8b: the sim rounds fees half-to-even, the tape ceils")
def test_sim_fee_rounding_matches_the_tape_the_agents_are_calibrated_on():
    """agents/market.py: El Rastro charged 5 on a 65 P trade (tape, 2026-10-02): ceil(3.25 + 1) = 5.
    The simulator uses round(): round(3.25) + 1 = 4, and round(0.5) = 0 (banker's) on a 10 P trade."""
    w = manual_world().world
    sim = {p: market.venue_fee(w, "rastro", p, 1) for p in (10, 12, 65, 70)}
    real = {p: math.ceil(p * 500 / 10_000 + 1 - 1e-9) for p in (10, 12, 65, 70)}
    print("sim fee:", sim, "tape/agents fee:", real)
    assert sim == real


# ---------------------------------------------------------------- the taker: does it see the pending fee?


def test_venues_from_keeps_the_announced_fee():
    (v,) = [v for v in venues_from({"venues": [rival_venue()]}) if v.id == "v07"]
    print("Venue:", v)
    assert v.fee(10) >= 6, "Venue drops `pending_fee`: fee(10) is today's 0, not the 6 that applies from T+1"


def test_taker_prices_an_ask_with_the_fee_in_force_when_it_settles():
    """A common LAV-02 ask at 10 on a venue at 0 fee whose capped fee (10 % + 5 P) is effective at T+1.
    If the server charges the fee in force at settlement: 10 + 6 = 16 > max_price_common 12."""
    venues = {v.id: v for v in venues_from({"venues": [RASTRO, rival_venue()]})}
    from bazaar_agent.agents.market import board_offers

    offers = board_offers({"offers": [ask(1, "LAV-02", 10, venue="v07")]}, "v07", US)
    cands = ask_candidates(strategy_market(), offers, venues, PARAMS, set(), {})
    print("candidates:", [(c.offer.id, c.fee, c.total, c.surplus) for c in cands])
    cap = Guardrails().max_price_common
    why = f"taker totals {[c.total for c in cands]} use today's fee; at settlement it is 16 > max_price_common {cap}"
    assert all(c.total >= 16 for c in cands) or not cands, why


def test_taker_dry_run_would_accept_past_max_price_common_after_fees(tmp_path):
    """End to end on the taker (dry run): the guardrail sees ask + today's fee (10 <= 12) and approves."""
    team = FakeTeam()
    public = FakePublic(boards={"v07": [ask(1, "LAV-02", 10, venue="v07")]}, venues=(RASTRO, rival_venue()))
    lines: list[str] = []
    t = Taker(
        team,
        public,
        live=False,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **parts(tmp_path),
    )
    t.on_tick(clock())
    would = [line for line in lines if "WOULD accept LAV-02" in line]
    print(would)
    settled_cost = 10 + math.ceil(10 * NEW["fee_bps"] / 10_000 + NEW["fee_per_card"])
    why = f"approved {would[0] if would else None!r}; settles at {settled_cost} P > max_price_common"
    assert not would or settled_cost <= Guardrails().max_price_common, why
