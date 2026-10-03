"""The per-tick request budget (RULES.md: 5 req/s per key, bursts of 20) and the loops measured against it.

Each loop runs one tick on fakes behind a counting proxy (`CallTally`): a loop that starts making more
calls per tick than its `LoopBudget` declares fails here. No network, no database.
"""

from copy import deepcopy

import psycopg
import pytest
from typer.testing import CliRunner

from bazaar_agent import db
from bazaar_agent import rate_budget as rb
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.monitoring import MonitorLoop, Options
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.feed import FeedStore
from bazaar_agent.guardrails import Ledger
from bazaar_agent.monitor import Watcher
from bazaar_agent.ticks import run_per_tick
from tests.agent_fakes import CHEAP, RASTRO, TICK, FakePublic, FakeTeam, ask, bid, clock, our_ask, parts
from tests.test_dealer import FakeDealerClient
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import DuelClient, use_policy
from tests.test_strategy import EVENTS

# ---------------------------------------------------------------- the budget table and its verdicts


@pytest.mark.parametrize("tick_seconds", [rb.SATURDAY_TICK_S, rb.SUNDAY_TICK_S])
def test_a_steady_tick_of_every_service_fits_the_key_today(tick_seconds):
    plan = rb.steady_plan()
    assert rb.check(plan, tick_seconds).ok
    assert rb.budget_table(tick_seconds, plan).rps("team") <= rb.RATE_PER_KEY / 2  # half the key left over


@pytest.mark.parametrize("tick_seconds", [rb.SATURDAY_TICK_S, rb.SUNDAY_TICK_S])
def test_every_loop_at_its_ceiling_stays_under_5_per_second_but_bursts_past_20(tick_seconds):
    plan = rb.saturday_plan()
    table = rb.budget_table(tick_seconds, plan)
    assert table.total("team") == 71 and table.rps("team") <= rb.RATE_PER_KEY
    verdict = rb.check(plan, tick_seconds)
    assert not verdict.ok and all("tick boundary" in p for p in verdict.problems)  # sustained is fine
    assert rb.check(plan, tick_seconds, offsets=rb.PROPOSED_STAGGER).ok  # a stagger absorbs it


def test_sunday_has_no_room_for_extra_dealer_processes_on_top_of_the_ceiling():
    plan = rb.saturday_plan(dealer_children=3)
    assert rb.budget_table(rb.SATURDAY_TICK_S, plan).rps("team") <= rb.RATE_PER_KEY
    problems = rb.check(plan, rb.SUNDAY_TICK_S, offsets=rb.PROPOSED_STAGGER).problems
    assert any("sustained 5.73 req/s" in p for p in problems)


def test_a_second_laptop_running_taker_and_maker_breaks_the_boundary_burst():
    two = rb.with_copies(rb.steady_plan(), {"taker": 2, "maker": 2})
    assert rb.burst(two).refused > 0
    assert rb.check(two, rb.SUNDAY_TICK_S, offsets=rb.PROPOSED_STAGGER).ok


def test_the_broker_key_is_its_own_bucket_unless_it_shares_the_teams():
    plan = rb.steady_plan()
    assert rb.burst(plan, "broker").refused == 0
    shared = rb.check(plan, rb.SATURDAY_TICK_S, broker_shares_team_bucket=True)
    assert not shared.ok and "team+broker" in shared.problems[0]


def test_the_token_bucket_refuses_only_what_the_refill_cannot_cover():
    one = [rb.LoopBudget("x", team=30, team_at_boundary=30)]
    assert rb.burst(one, latency_s=0.0) == rb.BurstResult(30, 10, 0.0, 30, 10)  # 20 tokens, no refill time
    assert rb.burst(one, latency_s=0.2).refused == 0  # 5/s refill matches one call per 0.2 s
    later = rb.burst(one + [rb.LoopBudget("y", team=20, team_at_boundary=20)], latency_s=0.0, offsets={"y": 4.0})
    assert later.refused == 10  # y starts once the bucket refilled to 20


def test_the_table_multiplies_copies_and_prints_a_total_row():
    table = rb.budget_table(15.0, [rb.duels(3).times(2), rb.evals()])
    assert [(r.name, r.team, r.public) for r in table.rows] == [("duels", 12, 0), ("evals", 0, 1)]
    assert rb.describe(table)[-1][:4] == ("total", "", "12", "0.80")


# ---------------------------------------------------------------- the loops, measured


def counted_loop(read_clock, on_tick):
    """One tick through the real loop driver: its clock read counts too."""
    run_per_tick(read_clock, on_tick, max_ticks=1, sleep=lambda s: None)


def test_the_taker_stays_inside_its_budget_on_a_busy_tick(tmp_path):
    tally = rb.CallTally()
    team = FakeTeam(offers=[bid(77, "LAV-02", 19)])
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}, venues=(RASTRO, CHEAP), events=EVENTS)
    kw = parts(tmp_path)
    counted_public = tally.wrap(public, "public")
    kw["feed"] = MarketFeed(counted_public.feed_window)
    taker = Taker(
        tally.wrap(team, "team"),
        counted_public,
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        **kw,
    )
    counted_loop(tally.wrap(team, "team").clock, taker.on_tick)
    assert [s[0] for s in team.sent] == ["open_thread", "accept", "cancel", "say"]  # one dealer to talk to
    budget = rb.taker(dealer_threads=1, venues=2)
    assert tally.total("team") == 10 and budget.team == 11  # + a clock re-read after a lost reservation
    assert tally.total("public") <= budget.public
    assert tally.total("team") <= rb.taker().team


def test_the_maker_cancels_every_stale_offer_in_one_tick_inside_its_ceiling(tmp_path):
    tally = rb.CallTally()
    stale = [our_ask(100 + i, 900 + i, "LAV-01", 30) for i in range(28)]  # assets we no longer target
    team = FakeTeam(offers=stale)
    maker = Maker(
        tally.wrap(team, "team"),
        tally.wrap(FakePublic(), "public"),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        **parts(tmp_path),
    )
    counted_loop(tally.wrap(team, "team").clock, maker.on_tick)
    cancels = sum(1 for s in team.sent if s[0] == "cancel")
    assert cancels == 28  # nothing caps the cancels in a tick: the ceiling counts all 30 slots
    assert tally.total("team") <= rb.maker().team
    assert tally.total("public") <= rb.maker().public


class Duels(DuelClient):
    def duels(self, done=False):
        return {"duels": [] if done else deepcopy(self.payload)}


@pytest.fixture
def duel_client(monkeypatch, tmp_path):
    from bazaar_agent import cli
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    live = [
        {**deepcopy(LIVE), "duel": 95 + i, "rival_offer": {"id": 700 + i, "price": 160, "tick": 133, "days": 0}}
        for i in range(3)
    ]
    tally = rb.CallTally()
    client = Duels(live)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: tally.wrap(client, "team"))
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    # Stands in for the shared ledger a live run needs (as tests/test_jev_journal.py's duel_cli does).
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: Ledger(tmp_path / "ledger.jsonl"))
    return cli, client, tally


def test_the_duel_player_moves_three_duels_inside_its_budget(duel_client, monkeypatch):
    cli, client, tally = duel_client
    use_policy(monkeypatch, cli, "v1")  # v1 moves every live duel each tick: the ceiling
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0 and "failed" not in result.output, result.output
    assert len(client.sent) == 3  # one move per live duel
    assert tally.total("team") == rb.duels(3).team == 6  # clock, /api/duels, ?done=true, three moves
    assert tally.calls[("team", "clock")] == 1 and tally.calls[("team", "duels")] == 2


def test_the_live_duel_policy_stays_inside_the_same_budget(duel_client):
    cli, client, tally = duel_client  # GUARDRAILS.md as committed (duel_policy v2 holds a conceding rival)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0 and "failed" not in result.output, result.output
    assert len(client.sent) <= 3 and tally.total("team") <= rb.duels(3).team


def test_the_monitor_reads_the_key_once_per_tick(tmp_path, monkeypatch):
    from tests.test_telemetry_cli import FakePublic as MonitorPublic

    monkeypatch.setattr(db, "connect_ready", lambda app: (_ for _ in ()).throw(RuntimeError("no db")))
    tally = rb.CallTally()
    public = tally.wrap(MonitorPublic(), "public")
    team = tally.wrap(type("Team", (), {"me": lambda self: {"id": "t01", "cash": 300}})(), "team")
    watcher = Watcher(FeedStore(tmp_path / "feed"), "t01")
    loop = MonitorLoop(public, team, watcher, tmp_path, Options(False, False, 5, False), lambda line: None)
    counted_loop(public.clock, loop.on_tick)
    assert tally.total("team") == 1 <= rb.monitor().team
    assert tally.total("public") == rb.monitor().public  # clock, feed, dealers, levels


def test_a_dealer_negotiation_makes_at_most_five_keyed_calls_a_tick():
    tally = rb.CallTally()
    fake = FakeDealerClient(asks=[12, 10, 9], reads_per_tick=2)  # the loop clock and the fresh one
    out = negotiate(
        tally.wrap(fake, "team"),
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda line: None,
        sleep=lambda s: None,
    )
    assert out.ticks >= 3
    assert tally.total("team") <= rb.dealer_child().team * out.ticks


def test_the_proxy_counts_every_call_and_passes_attributes_through():
    tally = rb.CallTally()
    team = FakeTeam(now=clock(tick=TICK))
    counted = tally.wrap(team, "team")
    counted.me()
    counted.me()
    counted.now = clock(tick=TICK + 1)
    assert tally.calls[("team", "me")] == 2 and team.now.tick == TICK + 1 and counted.reads == ["me", "me"]


def test_the_budget_command_prints_the_table_and_the_verdict_offline():
    from bazaar_agent import cli

    out = CliRunner().invoke(cli.app, ["budget", "--ceiling", "--tick-seconds", "15"])
    assert out.exit_code == 0, out.output
    assert "64 team-key calls → 71 requests" in out.output
    staggered = CliRunner().invoke(cli.app, ["budget", "--ceiling", "--tick-seconds", "15", "--stagger"])
    assert "fits the key" in staggered.output


def test_operator_tools_on_top_of_a_sunday_ceiling_break_the_key_but_not_on_saturday():
    from bazaar_agent import rate_budget as rb

    sunday = rb.saturday_plan() + [rb.operator(0.5, rb.SUNDAY_TICK_S)]
    assert rb.budget_table(rb.SUNDAY_TICK_S, sunday).rps("team") == pytest.approx(79 / 15)
    assert not rb.check(sunday, rb.SUNDAY_TICK_S, offsets=rb.PROPOSED_STAGGER).ok
    saturday = rb.saturday_plan() + [rb.operator(1.0, rb.SATURDAY_TICK_S)]
    assert rb.check(saturday, rb.SATURDAY_TICK_S, offsets=rb.PROPOSED_STAGGER).ok


def test_the_stagger_needs_slow_calls_and_a_shared_broker_bucket_breaks_sunday():
    from bazaar_agent import rate_budget as rb

    plan = rb.saturday_plan()
    runs = {lat: rb.burst(plan, offsets=rb.PROPOSED_STAGGER, latency_s=lat, retries=2) for lat in (0.15, 0.10, 0.05)}
    assert {lat: (r.refused, r.failed) for lat, r in runs.items()} == {0.15: (0, 0), 0.10: (2, 0), 0.05: (10, 0)}
    shared = rb.check(plan, rb.SUNDAY_TICK_S, broker_shares_team_bucket=True)
    assert not shared.ok and "5.87 req/s" in shared.problems[0]
    assert rb.flatten().team == 32


def test_the_sdk_re_sends_a_refused_call_which_spreads_the_edge_but_can_still_lose_it():
    """r2 bite X6: `team_client()` re-sends a 429 twice (0.25 s × attempt), GETs and POSTs alike. A call
    lost after its last retry may be the tick's one accept (r2 X20: the reserved slot is then wasted)."""
    ceiling = rb.burst(rb.saturday_plan(), retries=rb.SDK_RETRIES)
    assert (ceiling.calls, ceiling.sent, ceiling.refused, ceiling.failed) == (64, 71, 7, 0)
    crowded = rb.burst(rb.saturday_plan(dealer_children=3), retries=rb.SDK_RETRIES)
    assert (crowded.calls, crowded.sent, crowded.failed) == (79, 110, 2)
    two = rb.burst(rb.with_copies(rb.steady_plan(), {"taker": 2, "maker": 2}), retries=rb.SDK_RETRIES)
    assert two.failed == 1
    staggered = rb.burst(rb.saturday_plan(dealer_children=3), offsets=rb.PROPOSED_STAGGER, retries=rb.SDK_RETRIES)
    assert staggered.failed == 0
