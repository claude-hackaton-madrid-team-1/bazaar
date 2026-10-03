"""Prove our venue on a simulator: the maker opens it on its own, then its broker plays the Market Test.

One process, real HTTP, nothing reaches the real game: `bazaar_sim` serves on a free local port (in-memory
store, the real per-key rate limits), and the maker runs LIVE against it exactly as on Railway (album
first, the venue keeper before its own offers, the broker inside its tick window). Our agent's memory
(decisions, ledger, the broker-key vault) goes to the local `bazaar_sim` database, never `railway`.

For each Market Test session it prints the share of the possible gains (between the hidden limits) that
our board venue realised, and what the free auto stall realises on the same book: `bazaar_sim.bench.run_stall`,
the stall replayed tick by tick on the session's traders (arrivals, departures and relaxing quotes included).

The committed `venue_open_after_game_hours` (6.5) is a game hour of the real calendar; a simulator hour
lasts an hour of ticks, so `--open-after-hours` sets the opening hour for this run only (the rest of
GUARDRAILS.md is the committed file).

    uv run python scripts/sim_market_test.py --sessions 2
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import SecretStr

from bazaar_sim.app import Sim, create_app, server_config
from bazaar_sim.auth import Gate
from bazaar_sim.bench import possible_gains, run_stall
from bazaar_sim.models import BenchRun
from bazaar_sim.store import MemoryStore
from bazaar_sim.world import SimConfig, World

LOCAL_SIM_DB = "postgresql://bazaar:bazaar@localhost:5433/bazaar_sim"
SIM_KEY = "sim-team1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serve(config: SimConfig) -> Iterator[tuple[str, Sim]]:
    sim = Sim(world=World.create(config), store=MemoryStore(), gate=Gate.from_rates(5.0, 20.0), admin_token=None)
    port = free_port()
    server = uvicorn.Server(server_config(create_app(sim), "127.0.0.1", port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", sim
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def rules_for_run(open_after_hours: float) -> Any:
    from bazaar_agent.guardrails import GUARDRAILS_FILE, parse_guardrails

    text = GUARDRAILS_FILE.read_text(encoding="utf-8")
    text = re.sub(
        r"^- `venue_open_after_game_hours` = [0-9.]+",
        f"- `venue_open_after_game_hours` = {open_after_hours}",
        text,
        flags=re.M,
    )
    return parse_guardrails(text).rules


def build_maker(url: str, data_dir: Path, rules: Any, log: Any) -> Any:
    from bazaar_agent import db
    from bazaar_agent import venue as vn
    from bazaar_agent.agents.maker import Maker
    from bazaar_agent.agents.runtime import MarketFeed
    from bazaar_agent.agents.status import StatusHub
    from bazaar_agent.agents.venue_keeper import VenueKeeper
    from bazaar_agent.config import Settings
    from bazaar_agent.decisions import DecisionLog
    from bazaar_agent.feed import FeedStore
    from bazaar_agent.ledger_pg import open_ledger
    from bazaar_agent.sdk import PublicBazaar, team_client
    from bazaar_agent.strategy import load_strategy

    settings = Settings(
        simulated=True,
        bazaar_url=url,
        bazaar_key=SecretStr(SIM_KEY),
        data_dir=data_dir,
        database_url=SecretStr(os.environ["BAZAAR_SIM_DATABASE_URL"]),
    )
    team, public = team_client(settings), PublicBazaar(url)

    def connect() -> Any:
        return db.connect(app="bazaar-sim-market-test")

    decisions = DecisionLog(data_dir, connect, log)
    hub = StatusHub("maker", True, target=settings.target)
    keeper = VenueKeeper(
        team,
        settings=settings,
        rules=rules,
        vault=vn.KeyVault.from_settings(settings, connect),
        decisions=decisions,
        live=True,
        log=log,
        hub=hub,
        stats_dir=data_dir / "agents",
    )
    params = load_strategy().params
    maker = Maker(
        team,
        public,
        rules=rules,
        params=lambda tick: params,
        ledger=open_ledger(data_dir, source="sim-market-test", log=log),
        decisions=decisions,
        feed=MarketFeed(public.feed_window, FeedStore(data_dir / "feed"), connect, log),
        live=True,
        log=log,
        hub=hub,
        market=keeper,
    )
    return maker, keeper, team, hub, decisions


def realised(traders: list[Any], pairs: list[list[str]]) -> int:
    limits = {t.id: t.limit for t in traders}
    return sum(max(0, limits[b] - limits[s]) for s, b in pairs)


def stall_on(run: BenchRun) -> int:
    """The free auto stall on this session's book, replayed tick by tick (`bazaar_sim.bench.run_stall`)."""
    return run_stall(run.traders, run.end_tick - run.start_tick + 1, run=run.run).realised()


def report(sim: Sim, venue: str | None) -> list[dict[str, Any]]:
    rows = []
    with sim.world.lock:
        for run in sim.world.state.bench:
            possible = possible_gains(run.traders) or 1
            ours = realised(run.traders, run.matched.get(venue or "", [])) if venue else 0
            stall = stall_on(run)
            rows.append(
                {
                    "session": f"b{run.run}",
                    "ticks": f"{run.start_tick}-{run.end_tick}",
                    "scored": run.scored,
                    "pairs": len(run.matched.get(venue or "", [])),
                    "possible": possible,
                    "ours": ours,
                    "stall": stall,
                    "ours_eff": round(ours / possible, 3),
                    "stall_eff": round(stall / possible, 3),
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=int, default=2, help="Market Test sessions to play")
    parser.add_argument("--tick-seconds", type=float, default=4.0)
    parser.add_argument("--open-after-hours", type=float, default=0.02, help="this run's venue_open_after_game_hours")
    parser.add_argument("--bench-every", type=int, default=20, help="ticks between two sessions")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    os.environ["BAZAAR_SIM"] = "1"  # every connection our code opens goes to the simulator's own database
    os.environ.setdefault("BAZAAR_SIM_DATABASE_URL", LOCAL_SIM_DB)
    from bazaar_agent.agents.runtime import watched_clock
    from bazaar_agent.ticks import run_per_tick

    open_tick = int(args.open_after_hours * 3600 / args.tick_seconds) + 1
    first_bench = open_tick + 4
    config = SimConfig(
        tick_seconds=args.tick_seconds,
        seed=args.seed,
        rivals=4,
        chato_open_ticks=1,  # level 2 for everyone at tick 1: a venue needs it
        duel_first_tick=100_000,
        bench_first_tick=first_bench,
        bench_every_ticks=args.bench_every,
        bench_ticks=16,
    )
    last_tick = first_bench + (args.sessions - 1) * args.bench_every + 16 + 2
    lines: list[str] = []

    def log(line: str) -> None:
        lines.append(line)
        if any(word in line for word in ("venue", "broker", "Market Test", "OPENED")):
            print(line, flush=True)

    rules = rules_for_run(args.open_after_hours)
    with tempfile.TemporaryDirectory(prefix="sim-market-test-") as tmp, serve(config) as (url, sim):
        print(f"simulator {url} · tick {args.tick_seconds:g} s · open after game hour {args.open_after_hours}")
        print(
            f"rules: allow_venue_open {rules.allow_venue_open}, cash_floor {rules.cash_floor} + bond reserve "
            f"{rules.venue_bond_reserve} until open · sessions at ticks {first_bench}, +{args.bench_every}"
        )
        maker, keeper, team, hub, decisions = build_maker(url, Path(tmp), rules, log)
        try:
            handled = run_per_tick(
                watched_clock(team.clock, "maker", log),
                maker.on_tick,
                stop=lambda: sim.world.tick > last_tick,
            )
        finally:
            decisions.close()
        venue = keeper.opened.venue if keeper.opened else None
        me = team.me()
        key = keeper.opened.key.get_secret_value() if keeper.opened else "simbk-none"
        published = "\n".join(hub.replay())
        print(f"\nticks handled {handled} · our venue {venue} · cash now {me['cash']}")
        print(
            f"broker key in any log line: {any(key in line for line in lines)} · "
            f"on the public status: {key in published}"
        )
        print(
            f"sim score: bench_efficiency {me['score'].get('bench_efficiency')} · "
            f"mm_points {me['score'].get('mm_points')}"
        )
        print("\nsession  ticks    scored pairs possible  ours stall  ours_eff stall_eff")
        for r in report(sim, venue):
            print(
                f"{r['session']:<8} {r['ticks']:<8} {str(r['scored']):<6} {r['pairs']:>5} {r['possible']:>8} "
                f"{r['ours']:>5} {r['stall']:>5}  {r['ours_eff']:>8} {r['stall_eff']:>9}"
            )


if __name__ == "__main__":
    main()
