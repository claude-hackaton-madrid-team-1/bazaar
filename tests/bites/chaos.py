"""Chaos harness: the real taker and maker, live, over HTTP against the in-process simulator, with faults.

`run_chaos()` serves `bazaar_sim` on a free local port (never the real game), hand-turns its clock (30 s game
ticks, so a game hour is 120 ticks as on Saturday), and every tick runs `Taker.on_tick` then `Maker.on_tick`
with live sends and a shared JSONL ledger. Faults: restart the agents every `restart_every` ticks (a fresh
object, same ledger, as a Railway redeploy). Ground truth is read from the simulator itself: our cash after
every tick, and every settlement where we paid (price + the fee we paid as the accepting side).

The report holds the invariants GUARDRAILS.md promises: cash never below `cash_floor`, and what we really paid
in any rolling game hour never above `max_spend_per_game_hour`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger, load_guardrails
from bazaar_agent.sdk import Bazaar, PublicBazaar
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import Clock
from tests.simkit import QUIET, running_sim

US, KEY = "t01", "sim-team1"
TICK_S = 30.0


@dataclass
class Paid:
    tick: int
    t_hours: float
    amount: int
    what: str


@dataclass
class ChaosReport:
    ticks: int
    restarts: int
    cash: list[tuple[int, int]] = field(default_factory=list)  # (tick, cash after the tick)
    paid: list[Paid] = field(default_factory=list)
    ledger_spend: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def min_cash(self) -> int:
        return min(c for _, c in self.cash) if self.cash else 0

    def worst_hour(self) -> tuple[float, int]:
        """The rolling game hour (by settlement `t_hours`) in which we paid the most: (start, total)."""
        best = (0.0, 0)
        for p in self.paid:
            # the ledger's own window (`spent_since(t - 1.0)`: strictly after): rounded, so 1.0083 - 1.0 is not < 0.0083
            total = sum(q.amount for q in self.paid if 0 <= round(p.t_hours - q.t_hours, 6) < 1.0)
            if total > best[1]:
                best = (p.t_hours, total)
        return best


def _paid_in(events: list[dict[str, Any]]) -> Iterator[Paid]:
    for e in events:
        if e.get("type") != "settlement":
            continue
        p = e.get("payload") or {}
        parties = p.get("parties") or []
        cards_to_us = [i for i in p.get("items") or [] if i.get("to") == US]
        if US not in parties or not cards_to_us:
            continue
        maker, taker = (parties + [None, None])[:2]
        fee = int(p.get("fee") or 0) if taker == US else 0
        refs = ",".join(str(i.get("ref")) for i in cards_to_us)
        yield Paid(int(e.get("tick") or 0), float(e.get("t") or 0.0), int(p.get("price") or 0) + fee, refs)


def run_chaos(
    tmp: Path,
    *,
    ticks: int = 240,
    restart_every: int = 0,
    rivals: int = 6,
    seed: int = 7,
    rules: Guardrails | None = None,
    threads: int = 3,
    start_cash: int | None = None,
    start_hours: float = 0.0,
) -> ChaosReport:
    rules = rules or load_guardrails().rules
    strategy = load_strategy()
    config = replace(QUIET, tick_seconds=TICK_S, rivals=rivals, seed=seed, rivals_enabled=rivals > 0)
    report = ChaosReport(ticks, 0)
    with running_sim(config, run_clock=False) as (url, sim):
        team, public = Bazaar(url, KEY, wait_on_tick=False, retries=2), PublicBazaar(url)
        if start_cash is not None:
            with sim.world.lock:
                sim.world.team(US).cash = start_cash
        if start_hours:
            with sim.world.lock:  # e.g. 6.4: just before a game-hour trigger (the venue at h6.5)
                sim.world.state.clock.t_seconds = start_hours * 3600
        ledger = Ledger(tmp / "ledger.jsonl")
        log = report.log.append

        def build() -> tuple[Taker, Maker]:
            common: dict[str, Any] = {
                "rules": rules,
                "params": lambda tick: strategy.params,
                "ledger": ledger,
                "decisions": DecisionLog(tmp),
                "feed": MarketFeed(public.feed_window),
                "live": True,
                "log": log,
            }
            taker = Taker(team, public, config=TakerConfig(max_dealer_threads=threads), sleep=lambda s: None, **common)
            return taker, Maker(team, public, **common)

        taker, maker = build()
        seen = 0
        for n in range(ticks):
            if restart_every and n and n % restart_every == 0:
                taker, maker = build()
                report.restarts += 1
            clock = Clock.model_validate(team.clock())
            taker.on_tick(clock)
            maker.on_tick(clock)
            with sim.world.lock:
                sim.world.state.clock.tick_started_at = sim.world.now() - TICK_S  # due now
            sim.world.advance()
            with sim.world.lock:
                events = [e.model_dump() if hasattr(e, "model_dump") else vars(e) for e in sim.world.state.events]
                fresh = [e for e in events if int(e["id"]) > seen]
                seen = max([seen, *(int(e["id"]) for e in events)])
                report.cash.append((sim.world.tick, sim.world.team(US).cash))
            report.paid += list(_paid_in(fresh))
        report.ledger_spend = sum(int(e.get("price", 0)) for e in ledger.entries() if e.get("kind") == "spend")
    return report
