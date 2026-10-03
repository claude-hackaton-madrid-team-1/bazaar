"""B14 evidence: the real maker alone, live vs the in-process simulator with no rivals, so its bids lapse unfilled.

Run from the repo root: `PYTHONPATH=. uv run python docs/night/b14_maker_probe.py <label>`. Per run (240 ticks):
bids posted, bid-ticks on the board (our open bids summed over ticks), lapses refunded, the most the ledger booked
in any rolling game hour, and the most cash our open bids really committed at once."""

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Ledger, load_guardrails
from bazaar_agent.sdk import Bazaar, PublicBazaar
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import Clock
from tests.simkit import QUIET, running_sim

US, KEY = "t01", "sim-team1"


def run(tick_s: float, seed: int, ticks: int = 240) -> dict[str, float]:
    tmp = Path(tempfile.mkdtemp())
    rules, strategy = load_guardrails().rules, load_strategy()
    config = replace(QUIET, tick_seconds=tick_s, rivals=0, seed=seed, rivals_enabled=False)
    lines: list[str] = []
    bid_ticks, committed, worst_booked = 0, 0, 0
    with running_sim(config, run_clock=False) as (url, sim):
        team, public = Bazaar(url, KEY, wait_on_tick=False, retries=2), PublicBazaar(url)
        with sim.world.lock:
            sim.world.team(US).cash = 900
        ledger = Ledger(tmp / "ledger.jsonl")
        maker = Maker(
            team,
            public,
            rules=rules,
            params=lambda tick: strategy.params,
            ledger=ledger,
            decisions=DecisionLog(tmp),
            feed=MarketFeed(public.feed_window),
            live=True,
            log=lines.append,
        )
        for _ in range(ticks):
            clock = Clock.model_validate(team.clock())
            maker.on_tick(clock)
            worst_booked = max(worst_booked, ledger.spent_since(clock.t_hours - 1.0))
            with sim.world.lock:
                bids = [
                    o
                    for o in sim.world.state.offers.values()
                    if o.maker == US and o.status == "open" and o.give.cash and o.thread is None
                ]
                bid_ticks += len(bids)
                committed = max(committed, sum(o.give.cash for o in bids))
                sim.world.state.clock.tick_started_at = sim.world.now() - tick_s
            sim.world.advance()
    return {
        "bids_posted": sum("maker: post bid" in line for line in lines),
        "bid_ticks_on_board": bid_ticks,
        "lapses_refunded": sum("lapsed unfilled: refunded" in line for line in lines),
        "worst_hour_booked": worst_booked,
        "most_committed_at_once": committed,
        "cap_denials": sum("max_spend_per_game_hour" in line for line in lines),
    }


if __name__ == "__main__":
    for tick_s in (30.0, 15.0):
        for seed in (1, 2, 3):
            print(json.dumps({"code": sys.argv[1], "tick_s": tick_s, "seed": seed, **run(tick_s, seed)}), flush=True)
