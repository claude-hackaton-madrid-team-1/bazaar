"""W8 end-to-end in the local simulator (#55): our Taker, live against an in-process sim over real HTTP.

Never the real game: the sim runs on 127.0.0.1 in this process (tests/simkit.running_sim). Run it from a
checkout of the #55 branch with this branch's src first on the path:

    cd <bazaar-sim checkout> && PYTHONPATH=<this repo>/src uv run --no-sync python <this file> [ASK BID] [vanish]

A rival (t02) lists a card on El Rastro; another rival (t03) bids for the same card on a 0 bps venue owned by
t04. Tick 1: the taker buys the ask (arbitrage). Tick 2: it sells into the bid with the exact copy.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))  # the #55 checkout: tests.simkit
from tests.simkit import QUIET, running_sim  # noqa: E402
from bazaar_sim import broker, market  # noqa: E402

from bazaar_agent.agents.runtime import MarketFeed  # noqa: E402
from bazaar_agent.agents.taker import Taker, TakerConfig  # noqa: E402
from bazaar_agent.decisions import DecisionLog  # noqa: E402
from bazaar_agent.guardrails import Guardrails, Ledger  # noqa: E402
from bazaar_agent.sdk import Bazaar, PublicBazaar  # noqa: E402
from bazaar_agent.strategy import load_strategy  # noqa: E402
from bazaar_agent.ticks import Clock  # noqa: E402

US, SELLER, BUYER, OWNER = "t01", "t02", "t03", "t04"
ASK, BID = int(sys.argv[1]) if len(sys.argv) > 1 else 6, int(sys.argv[2]) if len(sys.argv) > 2 else 16
with running_sim(QUIET, run_clock=False) as (url, sim):
    w = sim.world
    w.team(OWNER).unlocked.append("chato")
    vid = broker.open_venue(w, OWNER, {"name": "Zero", "fee_bps": 0, "rules": {"mechanism": "board"}})["venue"]
    card = next(a for a in w.holdings(SELLER) if a.kind == "card")
    ask = market.offer_from_input(w, SELLER, {"venue": "rastro", "give": {"assets": [card.id]}, "want": {"cash": ASK}})
    bid = market.offer_from_input(w, BUYER, {"venue": vid, "give": {"cash": BID}, "want": {"cards": [card.ref]}})
    print(f"setup: {card.ref} (asset {card.id}) ask {ASK} on rastro by {SELLER}, bid {BID} on {vid} by {BUYER}")
    team, public = Bazaar(url, "sim-team1", wait_on_tick=False, retries=0), PublicBazaar(url)
    # cash_floor=0: a harness setting for the sim's 400 P start, never a GUARDRAILS.md value
    rules = Guardrails(arb_enabled=True, arb_min_net_spread=3, arb_max_inventory_p=60, cash_floor=0)
    tmp = Path(tempfile.mkdtemp())
    ledger = Ledger(tmp / "ledger.jsonl")
    lines: list[str] = []
    params = load_strategy().params
    taker = Taker(team, public, rules=rules, params=lambda t: params, ledger=ledger, decisions=DecisionLog(tmp),
                  feed=MarketFeed(lambda n: public.feed_window(n)), live=True, log=lines.append,
                  config=TakerConfig(max_dealer_threads=0, duel_grace_s=0.0))
    cash0 = w.team(US).cash
    held0 = w.held_counts(US).get(card.ref, 0)
    for step in range(3):
        clock = Clock.model_validate(team.clock())
        taker.on_tick(clock)
        w.advance()
        if step == 0 and "vanish" in sys.argv:
            market.cancel(w, BUYER, bid.id) if hasattr(market, "cancel") else None
            print("  (the exit bid was cancelled by its maker)")
        print(f"after tick {clock.tick}: cash {w.team(US).cash} (Δ {w.team(US).cash - cash0:+d}), "
              f"{card.ref} held {w.held_counts(US).get(card.ref, 0)} (was {held0}), asset {card.id} owner {w.asset(card.id).owner}")
    for line in lines:
        if "taker:" in line and ("accept" in line or "arbitrage" in line or "exit" in line):
            print("  ", line[:220])
    print("ledger:", ledger.entries())
