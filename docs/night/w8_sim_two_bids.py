"""W8 in the local simulator (#55): two bids for one card, then two new sellers, one of them resting.

Run like docs/night/w8_sim_e2e.py (from a #55 checkout, this repo's src first on PYTHONPATH). Expected: tick 0 buys
t02's ask, tick 1 sells it into t03's 16 (the best bid); at tick 2 t03 (our last exit buyer, resting under
arb_party_cooldown_ticks) asks 5 and t06 asks 7: the taker skips t03 and buys t06's copy, then sells it into t05's 14.
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from tests.simkit import QUIET, running_sim
from bazaar_sim import broker, market
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from bazaar_agent.sdk import Bazaar, PublicBazaar
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import Clock
with running_sim(QUIET, run_clock=False) as (url, sim):
    w = sim.world
    w.team("t04").unlocked.append("chato")
    vid = broker.open_venue(w, "t04", {"name": "Zero", "fee_bps": 0, "rules": {"mechanism": "board"}})["venue"]
    def refs(team):
        return {a.ref for a in w.holdings(team) if a.kind == "card"}
    shared = sorted(refs("t02") & refs("t06"))
    card = next(a for a in w.holdings("t02") if a.kind == "card" and a.ref == shared[0])
    market.offer_from_input(w, "t02", {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": 6}})
    b3 = market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 16}, "want": {"cards": [card.ref]}})
    b5 = market.offer_from_input(w, "t05", {"venue": vid, "give": {"cash": 14}, "want": {"cards": [card.ref]}})
    print(f"tick 0 board: t02 asks {card.ref} 6 on rastro; t03 bids 16 and t05 bids 14 on {vid}")
    team, public = Bazaar(url, "sim-team1", wait_on_tick=False, retries=0), PublicBazaar(url)
    rules = Guardrails(arb_enabled=True, cash_floor=0)  # cash_floor=0: harness setting for the sim's 400 P start
    tmp = Path(tempfile.mkdtemp()); lines = []
    params = load_strategy().params
    t = Taker(team, public, rules=rules, params=lambda x: params, ledger=Ledger(tmp / "l.jsonl"), decisions=DecisionLog(tmp),
              feed=MarketFeed(lambda n: public.feed_window(n)), live=True, log=lines.append,
              config=TakerConfig(max_dealer_threads=0, duel_grace_s=0.0))
    cash0 = w.team("t01").cash
    for step in range(5):
        if step == 2:  # two new sellers of the same card
            for seller, price in (("t03", 5), ("t06", 7)):  # t03 (our last exit buyer, resting) and t06 (fresh)
                c = next(a for a in w.holdings(seller) if a.kind == "card" and a.ref == card.ref)
                market.offer_from_input(w, seller, {"venue": "rastro", "give": {"assets": [c.id]}, "want": {"cash": price}})
                print(f"tick {w.tick} board: {seller} asks {card.ref} {price} on rastro")
        clock = Clock.model_validate(team.clock())
        t.on_tick(clock)
        w.advance()
        print(f"after tick {clock.tick}: cash Δ {w.team('t01').cash - cash0:+d}; t03 bid {w.state.offers[b3.id].status}, t05 bid {w.state.offers[b5.id].status}")
    for l in lines:
        if "accept " in l or "exit" in l:
            print("  ", l[:200])
