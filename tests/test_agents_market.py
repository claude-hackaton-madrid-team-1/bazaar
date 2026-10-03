"""Venues, fees and board shapes as the agents read them; live mode; the feed source; the agent CLI (fakes)."""

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.market import board_offers, our_open_offers, parse_offer, venues_from
from bazaar_agent.agents.runtime import MarketFeed, TickWindow, live_mode, window_for
from bazaar_agent.feed import FeedStore
from tests.agent_fakes import CHEAP, RASTRO, FakePublic, FakeTeam, ask, bid, clock


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


def test_a_bid_that_also_wants_an_asset_or_hides_an_unknown_key_is_never_plain():
    # security-auditor #98 P1: the bid branch never looked at want.assets, so a rival bid for "card:LAT-09"
    # that also wants the id of our rare read as a plain LAT-09 bid. Words persuade, structure binds: any
    # extra structure means it is not the shape we price, so it is skipped, never guessed at.
    plain = bid(2, "LAT-09", 62)
    trap = {**plain, "want": {**plain["want"], "assets": [77]}}
    hidden_want = {**plain, "want": {**plain["want"], "packs": ["sobre_barrio"]}}
    hidden_give = {**plain, "give": {**plain["give"], "debt": 5}}
    ask_extra = {**ask(1, "LAV-02", 10), "want": {"cash": 10, "assets": [], "types": [], "cards": ["LAV-09"]}}
    ask_hidden = {**ask(1, "LAV-02", 10), "want": {"cash": 10, "assets": [], "types": [], "bonus": [1]}}
    assert parse_offer(plain) is not None and parse_offer(plain).side == "bid"
    assert [parse_offer(o) for o in (trap, hidden_want, hidden_give, ask_extra, ask_hidden)] == [None] * 5
    real = {  # an offer.listed payload from Friday's feed: zero cash and empty lists are plain
        "id": 23,
        "maker": "t07",
        "venue": "rastro",
        "give": {"cash": 0, "assets": [{"id": 100, "kind": "card", "ref": "LAT-03", "rarity": "common"}], "types": []},
        "want": {"cash": 10, "assets": [], "types": []},
    }
    assert parse_offer(real) is not None and (parse_offer(real).side, parse_offer(real).price) == ("ask", 10)


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
    assert (
        "taker · DRY RUN: nothing is sent" in result.output
        and "ledger: Postgres on localhost:5433 unavailable" in result.output
    )
    assert "WOULD accept LAV-02 on rastro for 12" in result.output and team.sent == []


def test_a_live_agent_refuses_to_start_without_the_shared_ledger(agent_cli, monkeypatch):
    team, cli = agent_cli
    monkeypatch.setenv("BAZAAR_LIVE", "1")  # how Railway turns an agent live; DATABASE_URL is the local default
    result = CliRunner().invoke(cli.app, ["agent", "taker", "--max-ticks", "1", "--no-jev", "--threads", "0"])
    assert result.exit_code == 1 and team.sent == []
    assert "taker: refusing to trade: live trading needs the team's shared ledger" in " ".join(result.output.split())


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
