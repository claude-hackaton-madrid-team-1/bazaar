"""Our venue end to end on the simulator over real HTTP: the keeper opens a board venue once (the bond and
fee leave the floor intact), the broker plays a Market Test, and the venue realises at least what the free
stall realises on the same book. The clock is turned by hand (`/sim/tick`), so the test takes a second."""

from dataclasses import replace

import httpx
from pydantic import SecretStr

from bazaar_agent import venue as vn
from bazaar_agent.agents.broker import BrokerConfig
from bazaar_agent.agents.runtime import MarketFeed, TickWindow, read_snapshot
from bazaar_agent.agents.venue_keeper import VenueKeeper
from bazaar_agent.config import Settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.sdk import PublicBazaar, team_client
from bazaar_agent.ticks import Clock
from bazaar_sim.bench import cross_by_quote, possible_gains
from tests.simkit import QUIET, running_sim
from tests.test_venue import FakeConn

ADMIN = {"X-Admin-Token": "test-admin-token"}


def realised(traders, pairs):
    limits = {t.id: t.limit for t in traders}
    return sum(limits[b] - limits[s] for s, b in pairs)


def test_the_keeper_opens_a_board_venue_and_its_broker_matches_the_bench_at_least_as_well_as_the_stall(tmp_path):
    config = replace(QUIET, chato_open_ticks=1, bench_first_tick=5, bench_every_ticks=1000, bench_ticks=4)
    with running_sim(config, run_clock=False) as (url, sim):
        settings = Settings(simulated=True, bazaar_url=url, bazaar_key=SecretStr("sim-team1"), data_dir=tmp_path)
        team, public, store, lines = team_client(settings), PublicBazaar(url), {}, []
        rules = Guardrails(
            allow_venue_open=True, cash_floor=100, venue_open_after_game_hours=0.0, pause_file=str(tmp_path / "P")
        )
        keeper = VenueKeeper(
            team,
            settings=settings,
            rules=rules,
            vault=vn.KeyVault(tmp_path, lambda: FakeConn(store)),
            decisions=DecisionLog(tmp_path),
            live=True,
            log=lines.append,
            broker_config=BrokerConfig(pace_s=0.0),
        )
        feed = MarketFeed(public.feed_window)
        assert httpx.post(f"{url}/sim/tick", headers=ADMIN).status_code == 200  # tick 1: level 2 for everyone
        for _ in range(10):
            clock = Clock.model_validate(team.clock())
            snap = read_snapshot(team, public, feed, clock)
            keeper.on_tick(clock, snap, TickWindow(clock.tick, 1e12))
            assert httpx.post(f"{url}/sim/tick", headers=ADMIN).status_code == 200
        me = team.me()
        venue = keeper.opened.venue if keeper.opened else None
        assert venue is not None, lines
        assert me["venue"]["venue"] == venue and me["cash"] == 400 - 270
        assert (
            list(store) == [("", venue)] and len([v for v in sim.world.state.venues.values() if v.owner == "t01"]) == 1
        )
        run = sim.world.state.bench[0]
        assert run.scored
        # The free stall on the same book (#77 moved #55's `_auto_bench` into `bazaar_sim.bench`).
        stall = [[s, b] for s, b, _ in cross_by_quote(run.traders, 0, lambda price: 0)]
        ours = realised(run.traders, run.matched[venue])
        assert ours >= realised(run.traders, stall) and ours > 0
        assert me["score"]["bench_efficiency"] == round(ours / possible_gains(run.traders), 3)
        key = keeper.opened.key.get_secret_value()
        assert key.startswith("simbk-") and not any(key in line for line in lines)
