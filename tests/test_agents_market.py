"""Venues, fees and board shapes as the agents read them; live mode; the feed source; the agent CLI (fakes)."""

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.market import board_offers, our_open_offers, parse_offer, venues_from
from bazaar_agent.agents.runtime import MarketFeed, TickWindow, live_mode, window_for
from bazaar_agent.feed import FeedStore
from tests.agent_fakes import CHEAP, RASTRO, FakePublic, FakeTeam, ask, bid, clock, our_ask


@pytest.mark.parametrize(("price", "fee"), [(12, 2), (65, 5), (70, 5), (3, 2), (20, 2)])
def test_el_rastro_fees_match_the_tape(price, fee):
    (rastro,) = venues_from({"venues": [RASTRO]})
    assert rastro.fee(price) == fee  # settlements 2026-10-02: 12 P paid 2, 65 P paid 5, 70 P paid 5


def test_a_zero_fee_venue_charges_nothing_and_venues_parse_their_mechanism():
    rastro, cheap = venues_from({"venues": [RASTRO, CHEAP, {"no": "venue"}]})
    assert cheap.fee(500) == 0 and (rastro.mechanism, cheap.mechanism, cheap.owner) == ("board", "board", "t12")


def test_only_plain_one_card_shapes_are_read():
    assert parse_offer(ask(1, "LAV-02", 10)).side == "ask"
    assert parse_offer(bid(2, "LAV-09", 70)).side == "bid" and parse_offer(bid(2, "LAV-09", 70)).ref == "LAV-09"
    two_cards = ask(3, "LAV-02", 10)
    two_cards["give"]["assets"].append({"id": 5, "kind": "card", "ref": "LAV-03"})
    swap = {**ask(4, "LAV-02", 10), "want": {"cash": 0, "types": ["card:LAV-08"]}}
    cash_and_card = {**ask(5, "LAV-02", 10), "give": {"cash": 5, "assets": [{"id": 9, "ref": "LAV-02"}]}}
    assert parse_offer(two_cards) is None and parse_offer(swap) is None and parse_offer(cash_and_card) is None
    assert parse_offer({"id": "x"}) is None


def test_board_offers_keep_only_open_offers_for_anyone_or_for_us():
    offers = [
        ask(1, "LAV-02", 10),
        ask(2, "LAV-02", 10, to="t05"),
        ask(3, "LAV-02", 10, to="t01"),
        {**ask(4, "LAV-02", 10), "status": "filled"},
    ]
    assert [o.id for o in board_offers({"offers": offers}, "rastro", "t01")] == [1, 3]


def test_our_open_offers_leave_dealer_thread_offers_and_offers_to_us_to_others():
    response = {
        "offers": [bid(1, "LAV-09", 65), bid(2, "LAV-08", 20, thread=5000), {**ask(3, "LAV-02", 9), "to": "t01"}],
        "incoming": "not a list",
    }
    mine, total = our_open_offers(response, "t01")
    assert [o.id for o in mine] == [1] and total == 2  # the thread bid counts toward the 30, but is the desk's


def test_live_needs_the_flag_or_bazaar_live_1_in_the_environment():
    assert not live_mode(False, {}) and not live_mode(False, {"BAZAAR_LIVE": "0"})
    assert live_mode(True, {}) and live_mode(False, {"BAZAAR_LIVE": "1"})


def test_the_tick_window_closes_at_the_action_budget():
    now = [100.0]
    window = window_for(clock(next_tick_in=10.0), 100.0, lambda: now[0])
    assert window.left() == 8.0 and window.open()
    now[0] = 108.5
    assert not window.open() and TickWindow(1, 0.0).left() == 0.0


def test_the_feed_merges_captured_and_live_events_and_survives_a_dead_window(tmp_path):
    store = FeedStore(tmp_path)
    store.add([{"id": 1, "tick": 1, "type": "x"}])
    lines = []

    def dead(limit):
        raise OSError("down")

    feed = MarketFeed(lambda n: [{"id": 2, "tick": 2, "type": "y"}], store)
    assert [e["id"] for e in feed.events()] == [1, 2]
    assert [e["id"] for e in MarketFeed(dead, store, log=lines.append).events()] == [1]
    assert "live window unavailable" in lines[0]


def test_the_feed_backs_off_postgres_after_a_failure():
    attempts = []

    def down():
        attempts.append(1)
        raise OSError("no db")

    feed = MarketFeed(lambda n: [], None, down)
    for _ in range(6):
        feed.events()
    assert len(attempts) == 1  # then skipped for the next 5 reads


# ---------------------------------------------------------------- the CLI (fakes, no network)


@pytest.fixture
def agent_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    team = FakeTeam()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}))
    monkeypatch.setattr(cli, "team_client", lambda settings: team)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    monkeypatch.delenv("BAZAAR_LIVE", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    return team, cli


def test_bazaar_agent_taker_is_a_dry_run_by_default(agent_cli):
    team, cli = agent_cli
    result = CliRunner().invoke(cli.app, ["agent", "taker", "--max-ticks", "1", "--no-jev", "--threads", "0"])
    assert result.exit_code == 0, result.output
    assert "taker · DRY RUN: nothing is sent" in result.output and "ledger: Postgres unavailable" in result.output
    assert "WOULD accept LAV-02 on rastro for 12" in result.output and team.sent == []


def test_bazaar_agent_maker_serves_its_status_when_asked(agent_cli):
    import json
    import socket
    import urllib.request

    team, cli = agent_cli
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = str(probe.getsockname()[1])
    result = CliRunner().invoke(cli.app, ["agent", "maker", "--max-ticks", "1", "--port", free, "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert "maker: status on http://127.0.0.1:" in result.output and team.sent == []
    port = int(result.output.split("http://127.0.0.1:")[1].split(" ")[0])
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
        assert json.loads(r.read())["agent"] == "maker"


# ---------------------------------------------------------------- B10: the stagger and the cancel cap, per service


def spy_loop(monkeypatch, cli):
    """Record what each command hands the tick loop, then run one tick of it for real."""
    from bazaar_agent.ticks import run_per_tick

    seen: list[float | None] = []

    def loop(read_clock, on_tick, **kw):
        seen.append(kw.get("start_offset_s"))
        return run_per_tick(read_clock, on_tick, max_ticks=1, sleep=lambda s: None, start_offset_s=0.0)

    monkeypatch.setattr(cli, "run_per_tick", loop)
    return seen


def test_agents_take_a_tick_offset_and_default_to_the_environment(agent_cli, monkeypatch):
    team, cli = agent_cli
    seen = spy_loop(monkeypatch, cli)
    args = ["agent", "taker", "--max-ticks", "1", "--no-jev", "--threads", "0"]
    assert CliRunner().invoke(cli.app, [*args, "--tick-offset", "2"]).exit_code == 0
    assert CliRunner().invoke(cli.app, args).exit_code == 0
    assert (
        CliRunner().invoke(cli.app, ["agent", "maker", "--max-ticks", "1", "--no-jev", "--tick-offset", "4"]).exit_code
        == 0
    )
    assert seen == [2.0, None, 4.0]  # None: run_per_tick reads BAZAAR_TICK_OFFSET_S, unset = 0 (today)
    assert CliRunner().invoke(cli.app, [*args, "--tick-offset", "-1"]).exit_code != 0


def test_the_maker_command_caps_its_cancels_when_asked(agent_cli, monkeypatch):
    team, cli = agent_cli
    spy_loop(monkeypatch, cli)
    team.offers = [our_ask(100 + i, 900 + i, "LAV-01", 30) for i in range(5)]
    capped = CliRunner().invoke(cli.app, ["agent", "maker", "--max-ticks", "1", "--no-jev", "--max-cancels", "2"])
    assert capped.exit_code == 0, capped.output
    assert capped.output.count("max_cancels_per_tick 2 reached") == 3
    uncapped = CliRunner().invoke(cli.app, ["agent", "maker", "--max-ticks", "1", "--no-jev"])
    assert "max_cancels_per_tick" not in uncapped.output and team.sent == []  # dry run either way


def test_the_duel_and_monitor_loops_take_a_tick_offset(agent_cli, monkeypatch):
    team, cli = agent_cli
    seen: list[float | None] = []

    class Reached(Exception):
        pass

    def loop(read_clock, on_tick, **kw):
        seen.append(kw.get("start_offset_s"))
        raise Reached

    class Public(FakePublic):
        def clock(self):
            return {"tick": 1}

    monkeypatch.setattr(cli, "run_per_tick", loop)
    monkeypatch.setattr(cli, "public_client", lambda settings: Public())
    monkeypatch.setattr(cli, "open_stream", lambda settings, emit: None)
    for args in (["duel", "run"], ["monitor", "--no-db", "--no-stream"]):
        for extra, want in ((["--tick-offset", "0.5"], 0.5), ([], None)):
            result = CliRunner().invoke(cli.app, [*args, *extra])
            assert isinstance(result.exception, Reached), (args, result.output)
            assert seen.pop() == want
