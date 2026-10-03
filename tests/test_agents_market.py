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


# B19 (bite X8): an announced fee change (`pending_fee`) is priced in once an accept now could settle under it.
HIKE = {**CHEAP, "pending_fee": {"fee_bps": 1000, "fee_per_card": 5, "effective_tick": 101}}  # the RULES cap


def test_an_announced_fee_effective_by_settlement_is_priced_in():
    (hiked,) = venues_from({"venues": [HIKE]}, tick=100)  # accept at 100 settles at 101 = effective tick
    assert hiked.fee(10) == 6 and hiked.fee(65) == 12 and hiked.fee_bps == 0  # today's fee is still 0
    (hiked,) = venues_from({"venues": [HIKE]}, tick=101)  # the server still shows it pending
    assert hiked.fee(10) == 6


def test_an_announced_fee_effective_after_settlement_is_not_priced_in_yet():
    (later,) = venues_from({"venues": [HIKE]}, tick=98)  # accept at 98 settles at 99, or 100 if it slips
    assert later.fee(10) == 0 and later.pending_fee is None


def test_an_announced_fee_two_ticks_out_is_priced_in():
    """An accept that slips into the next tick settles a tick later (security review of #144)."""
    (hiked,) = venues_from({"venues": [HIKE]}, tick=99)
    assert hiked.fee(10) == 6


def test_an_announced_fee_without_a_tick_is_priced_in():
    """No tick to compare with: assume the change can apply (the conservative side)."""
    (hiked,) = venues_from({"venues": [HIKE]})
    assert hiked.fee(10) == 6
    no_effective = {**CHEAP, "pending_fee": {"fee_bps": 100, "fee_per_card": 0}}
    assert venues_from({"venues": [no_effective]}, tick=100)[0].fee(200) == 2


def test_an_announced_fee_cut_never_lowers_the_fee_before_it_applies():
    cut = {**RASTRO, "pending_fee": {"fee_bps": 0, "fee_per_card": 0, "effective_tick": 101}}
    (rastro,) = venues_from({"venues": [cut]}, tick=100)
    assert rastro.fee(65) == 5  # the higher of today's 5 and the announced 0


def test_an_unreadable_announced_fee_is_priced_at_the_rules_cap_and_none_is_the_old_shape():
    """A rival owns its venue row: an announcement we cannot read is priced at the cap, never ignored."""
    bad = {**CHEAP, "pending_fee": {"fee_bps": "lots", "effective_tick": 101}}
    late = {**CHEAP, "pending_fee": {"fee_bps": 100, "effective_tick": "soon"}}  # effective unknown: priced in
    inf = {**CHEAP, "pending_fee": {"fee_bps": float("inf"), "fee_per_card": 0, "effective_tick": 101}}
    rows = venues_from({"venues": [bad, late, inf, {**CHEAP, "pending_fee": None}]}, 100)
    assert [v.fee(100) for v in rows] == [15, 1, 15, 0]


def test_a_missing_announced_per_card_fee_keeps_todays_and_huge_values_are_capped():
    per_card = {**CHEAP, "fee_per_card": 5, "pending_fee": {"fee_bps": 1000, "effective_tick": 101}}
    huge = {**CHEAP, "pending_fee": {"fee_bps": 10**400, "fee_per_card": 99, "effective_tick": 101}}
    assert [v.fee(65) for v in venues_from({"venues": [per_card, huge]}, 100)] == [12, 12]


def test_a_venue_row_we_cannot_read_is_skipped_not_raised():
    rows = venues_from({"venues": [{**CHEAP, "fee_bps": float("inf")}, {**RASTRO}]}, 100)
    assert [v.id for v in rows] == [RASTRO["venue"]]


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
