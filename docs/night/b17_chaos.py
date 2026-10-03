"""B17 evidence: the real taker live vs the in-process simulator, restarted every N ticks.

Run from the repo root: `PYTHONPATH=. uv run python docs/night/b17_chaos.py b17`. Counts the dealer deals of
ours never booked in the ledger, and orphan thread-ticks (our open dealer threads that no live Taker owns)."""

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

US, KEY, TICK_S = "t01", "sim-team1", 30.0


def run(ticks, restart_every, seed, start_cash=900, maker=False):
    tmp = Path(tempfile.mkdtemp())
    rules = load_guardrails().rules
    strategy = load_strategy()
    config = replace(QUIET, tick_seconds=TICK_S, rivals=6, seed=seed, rivals_enabled=True)
    out = {"dealer_deals": 0, "dealer_paid": 0, "unbooked": 0, "unbooked_p": 0, "orphan_thread_ticks": 0, "restarts": 0}
    with running_sim(config, run_clock=False) as (url, sim):
        team, public = Bazaar(url, KEY, wait_on_tick=False, retries=2), PublicBazaar(url)
        with sim.world.lock:
            sim.world.team(US).cash = start_cash
        ledger = Ledger(tmp / "ledger.jsonl")

        def build():
            common = dict(
                rules=rules,
                params=lambda tick: strategy.params,
                ledger=ledger,
                decisions=DecisionLog(tmp),
                feed=MarketFeed(public.feed_window),
                live=True,
                log=lambda m: None,
            )
            t = Taker(team, public, config=TakerConfig(max_dealer_threads=3), sleep=lambda s: None, **common)
            return t, (Maker(team, public, **common) if maker else None)

        taker, mk = build()
        seen = 0
        deals = []
        for n in range(ticks):
            if restart_every and n and n % restart_every == 0:
                taker, mk = build()
                out["restarts"] += 1
            clock = Clock.model_validate(team.clock())
            taker.on_tick(clock)
            if mk:
                mk.on_tick(clock)
            with sim.world.lock:
                owned = {c.thread_id for c in taker.convs.values()}
                for th in sim.world.state.threads.values():
                    if th.team == US and th.kind == "persona" and th.status == "open" and th.id not in owned:
                        out["orphan_thread_ticks"] += 1
                sim.world.state.clock.tick_started_at = sim.world.now() - TICK_S
            sim.world.advance()
            with sim.world.lock:
                events = [e.model_dump() if hasattr(e, "model_dump") else vars(e) for e in sim.world.state.events]
            for e in events:
                if int(e["id"]) <= seen:
                    continue
                p = e.get("payload") or {}
                ours = US in (p.get("parties") or []) and any(i.get("to") == US for i in p.get("items") or [])
                if e.get("type") == "settlement" and p.get("persona") and ours:
                    deals.append((int(p.get("price") or 0), str(p["items"][0].get("ref"))))
            seen = max([seen, *(int(e["id"]) for e in events)])
        rows = [e for e in ledger.entries() if e.get("kind") == "spend" and int(e["price"]) > 0]
        spends = [(int(e["price"]), str(e["item"])) for e in rows]
        for d in deals:
            out["dealer_deals"] += 1
            out["dealer_paid"] += d[0]
            if d in spends:
                spends.remove(d)
            else:
                out["unbooked"] += 1
                out["unbooked_p"] += d[0]
    return out


if __name__ == "__main__":
    label = sys.argv[1]
    for every in (0, 3, 5, 10):
        tot = {}
        for seed in (1, 2, 3, 4, 5):
            r = run(240, every, seed)
            for k, v in r.items():
                tot[k] = tot.get(k, 0) + v
        print(json.dumps({"code": label, "restart_every": every, **tot}))
