"""Our CLI, unchanged, against the simulator over real HTTP: one scripted session end to end.

Buy three cards from Abuela with negotiation (`dealer buy --live`), list a duplicate (`sell list
--live`) that a rival team buys, bid for a card (`sell bid --live`), play a duel to a deal (`duel run
--play`), and run the monitor with its live SSE stream. The real game is never reached: the CLI
runs with BAZAAR_SIM=1, the simulator's hardcoded URL is pointed at a local in-process server for the
test, and the key is `sim-team1`.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_sim import catalog, market
from tests.simkit import QUIET, running_sim

US = "t01"
NOWHERE_DB = "postgresql://nobody:nothing@127.0.0.1:9/bazaar_sim_test"  # never reachable, never `railway`


@pytest.fixture
def session(tmp_path, monkeypatch):
    config = replace(QUIET, tick_seconds=0.5, rivals=6, duel_first_tick=4, duel_ticks=12, duel_every_ticks=200)
    with running_sim(config) as (url, sim):
        monkeypatch.setattr("bazaar_agent.config.SIM_URL", url)  # BAZAAR_SIM=1 → this local simulator
        for name in ("BAZAAR_URL", "BAZAAR_KEY", "BAZAAR_SIM_KEY"):
            monkeypatch.delenv(name, raising=False)
        env = {
            "BAZAAR_SIM": "1",
            "BAZAAR_TEAM_ID": US,
            "BAZAAR_DATA_DIR": str(tmp_path),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
            "COLUMNS": "200",
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        yield url, sim, tmp_path


def run(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output + repr(result.exception)
    return result.output


def missing_commons(sim) -> list[str]:
    with sim.world.lock:
        held = sim.world.held_counts(US)
    commons = [c.ref for s in catalog.released_sets() for c in catalog.page_cards(s) if c.rarity == "common"]
    return [r for r in commons if held.get(r, 0) == 0]


def best_duplicate_for_rivals(sim) -> tuple[str, int]:
    """The duplicate most rivals would buy at its book price (they pay up to 1.15 x their value)."""
    w = sim.world
    with w.lock:
        ours = w.held_counts(US)
        rivals = [t for t in w.state.teams.values() if t.bot]
        scored = []
        for ref, n in ours.items():
            if n < 2:
                continue
            price = catalog.cards()[ref].book
            fee = market.venue_fee(w, "rastro", price, 1)
            takers = [
                r
                for r in rivals
                if w.held_counts(r.id).get(ref, 0) == 0
                and catalog.one_more_value(ref, 0, r.affinity) * 1.15 >= price + fee
            ]
            scored.append((len(takers), ref, price))
    count, ref, price = max(scored)
    assert count >= 1, "no rival would buy any duplicate: the seed changed"
    return ref, price


def wait_until(predicate, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_a_full_scripted_session_against_the_simulator(session):
    url, sim, data_dir = session

    out = run("status")
    assert "Team 1" in out or "t01" in out

    bought = []
    for ref in missing_commons(sim)[:3]:
        out = run("dealer", "buy", ref, "--start", "6", "--max", "10", "--live")
        assert "deal" in out.splitlines()[-1], out
        bought.append(ref)
    with sim.world.lock:
        held = sim.world.held_counts(US)
        deals = [d for d in sim.world.team(US).deals if d.dealer == "abuela"]
    assert all(held[r] == 1 for r in bought) and len(deals) == 3
    assert all(d.negotiated and d.price <= 10 for d in deals)
    assert (data_dir / "ledger.jsonl").exists()

    ref, price = best_duplicate_for_rivals(sim)
    out = run("sell", "list", ref, "--price", str(price), "--live")
    assert "sell" in out.lower()
    with sim.world.lock:
        listing = next(o for o in sim.world.state.offers.values() if o.maker == US and o.give.assets)
        asset_id = listing.give.assets[0]

    def rival_owns_it() -> bool:
        with sim.world.lock:
            return (
                sim.world.state.teams[sim.world.asset(asset_id).owner].bot
                if sim.world.asset(asset_id).owner in sim.world.state.teams
                else False
            )

    assert wait_until(rival_owns_it), "no rival bought the listed duplicate"

    target = next(r for r in missing_commons(sim) if r not in bought)
    run("sell", "bid", target, "--price", "4", "--live")
    with sim.world.lock:
        assert any(o.maker == US and o.want.types == [f"card:{target}"] for o in sim.world.state.offers.values())

    run("duel", "run", "--play", "--max-ticks", "17")
    with sim.world.lock:
        mine = [d for d in sim.world.state.duels.values() if d.team == US]
    assert mine and any(d.status == "deal" for d in mine), [
        (d.role, d.status, d.your_offer, d.rival_offer) for d in mine
    ]
    assert (data_dir / "duels" / "duels.jsonl").exists()

    out = run("monitor", "--no-db", "--max-ticks", "2", "--show-events")
    assert "stream on" in out
    assert (data_dir / "feed" / "feed.jsonl").exists()


def test_the_cli_refuses_the_real_key_against_the_simulator(session, monkeypatch):
    monkeypatch.setenv("BAZAAR_SIM_KEY", "tk-real-0042")
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 1
    assert "only a simulator key" in result.output and "tk-real-0042" not in result.output


def test_every_command_names_its_target_and_bazaar_url_fails_fast(session, monkeypatch):
    url, _, _ = session
    out = run("clock")
    assert f"target: SIMULATOR {url}" in out
    status = run("status", "--no-cards")
    assert "SIMULATOR" in status
    monkeypatch.setenv("BAZAAR_URL", "https://bazaar.causaprima.ai")
    result = CliRunner().invoke(cli.app, ["clock"])
    assert result.exit_code == 2 and "BAZAAR_URL is no longer read" in result.output
