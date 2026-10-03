"""B14 evidence: the real taker and maker live vs the in-process simulator (r2's chaos setup).

Run from the repo root: `PYTHONPATH=. uv run python docs/night/b14_chaos.py <label> <rivals> <seed>...`.
Per run: what we really paid (settlements, fee included), what the ledger booked, the worst rolling game hour of
each, and how many buys the spend cap denied."""

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Ledger, load_guardrails
from bazaar_agent.sdk import Bazaar, PublicBazaar
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import Clock
from tests.simkit import QUIET, running_sim

US, KEY = "t01", "sim-team1"


def worst_hour(rows):
    """rows: (t_hours, amount). The most in any rolling game hour."""
    return max((sum(a for h, a in rows if t - 1.0 < h <= t) for t, _ in rows), default=0)


def run(ticks, tick_s, seed, rivals, start_cash=900):
    tmp = Path(tempfile.mkdtemp())
    rules = load_guardrails().rules
    strategy = load_strategy()
    config = replace(QUIET, tick_seconds=tick_s, rivals=rivals, seed=seed, rivals_enabled=rivals > 0)
    lines: list[str] = []
    paid: list[tuple[float, int]] = []
    with running_sim(config, run_clock=False) as (url, sim):
        team, public = Bazaar(url, KEY, wait_on_tick=False, retries=2), PublicBazaar(url)
        with sim.world.lock:
            sim.world.team(US).cash = start_cash
        ledger = Ledger(tmp / "ledger.jsonl")
        common = dict(
            rules=rules,
            params=lambda tick: strategy.params,
            ledger=ledger,
            decisions=DecisionLog(tmp),
            feed=MarketFeed(public.feed_window),
            live=True,
            log=lines.append,
        )
        taker = Taker(team, public, config=TakerConfig(max_dealer_threads=3), sleep=lambda s: None, **common)
        maker = Maker(team, public, **common)
        seen = 0
        for _ in range(ticks):
            clock = Clock.model_validate(team.clock())
            taker.on_tick(clock)
            maker.on_tick(clock)
            with sim.world.lock:
                sim.world.state.clock.tick_started_at = sim.world.now() - tick_s
            sim.world.advance()
            with sim.world.lock:
                events = [e.model_dump() if hasattr(e, "model_dump") else vars(e) for e in sim.world.state.events]
            for e in events:
                if int(e["id"]) <= seen or e.get("type") != "settlement":
                    continue
                p = e.get("payload") or {}
                parties = p.get("parties") or []
                if US in parties and any(i.get("to") == US for i in p.get("items") or []):
                    fee = int(p.get("fee") or 0) if parties[1:2] == [US] else 0
                    paid.append((float(e.get("t") or 0.0), int(p.get("price") or 0) + fee))
            seen = max([seen, *(int(e["id"]) for e in events)])
        booked = [(float(e["t_hours"]), int(e["price"])) for e in ledger.entries() if e.get("kind") == "spend"]
    return {
        "paid": sum(a for _, a in paid),
        "booked": sum(a for _, a in booked),
        "worst_hour_paid": worst_hour(paid),
        "worst_hour_booked": worst_hour(booked),
        "cap_denials": sum("max_spend_per_game_hour" in line for line in lines),
        "bids_posted": sum(" post bid " in f" {line} " and "skip" not in line for line in lines),
    }


if __name__ == "__main__":
    label, rivals, seeds = sys.argv[1], int(sys.argv[2]), [int(s) for s in sys.argv[3:]]
    for tick_s in (30.0, 15.0):
        for seed in seeds:
            row = {"code": label, "rivals": rivals, "tick_s": tick_s, "seed": seed, **run(240, tick_s, seed, rivals)}
            print(json.dumps(row), flush=True)
