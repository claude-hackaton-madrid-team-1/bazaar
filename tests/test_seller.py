from dataclasses import replace

import pytest
from typer.testing import CliRunner

from bazaar_agent import render
from bazaar_agent.agents import seller
from bazaar_agent.cli import app
from bazaar_agent.guardrails import Action, Context, Guardrails, Ledger, check
from tests.test_render import text
from tests.test_runtime_tools import cli_env  # noqa: F401

ME = {
    "id": "t01",
    "cash": 353,
    "assets": [
        {"id": 15, "kind": "card", "ref": "LAT-09", "rarity": "rare", "your_value": 35.0},
        {"id": 1, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 3.2},
        {"id": 11, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 3.2},
        {"id": 7, "kind": "pack", "ref": "sobre_barrio"},
    ],
}
CTX = Context(cash=353, held={"LAT-09": 1, "SAL-01": 2}, tick=90, t_hours=1.5)
RULES = Guardrails()


class FakeOfferClient:
    def __init__(self):
        self.posted = []

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.posted.append({"give": give, "want": want, "venue": venue, "expires_in_ticks": expires_in_ticks})
        return {"id": 777, "status": "open"}


def test_find_copy_by_asset_id_or_by_the_copy_we_lose_least():
    assert seller.find_copy(ME, "15")["ref"] == "LAT-09"
    assert seller.find_copy(ME, "SAL-01")["id"] == 11
    with pytest.raises(seller.OfferError, match="no card"):
        seller.find_copy(ME, "LAV-09")
    with pytest.raises(seller.OfferError):
        seller.find_copy(ME, "7")  # a pack is not a card


def test_a_sell_listing_is_the_structured_offer_with_our_value():
    listing = seller.sell_listing(ME, "LAT-09", 68)
    assert (listing.give, listing.want, listing.venue) == ({"assets": [15]}, {"cash": 68}, "rastro")
    assert listing.action().your_value == 35.0 and listing.action().kind == "sell"
    with pytest.raises(seller.OfferError, match="outside"):
        seller.sell_listing(ME, "LAT-09", 0)


def test_sell_list_is_a_dry_run_by_default():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 68), CTX, RULES, live=False)
    assert not out.sent and out.verdict.allowed and client.posted == []
    assert out.message.startswith("dry run: would sell asset 15 (LAT-09) for 68 P")


def test_sell_list_live_posts_the_offer():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 68, "t03-venue"), CTX, RULES, live=True, expires_in_ticks=9)
    assert out.sent and out.offer == {"id": 777, "status": "open"}
    assert client.posted == [
        {"give": {"assets": [15]}, "want": {"cash": 68}, "venue": "t03-venue", "expires_in_ticks": 9}
    ]


def test_guardrails_deny_a_sell_below_our_value_even_when_live():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 30), CTX, RULES, live=True)
    assert not out.sent and not out.verdict.allowed and client.posted == []
    assert "your_value 35.0" in out.message


def test_the_kill_switch_stops_listings():
    client = FakeOfferClient()
    paused = Context(cash=353, held={}, tick=90, t_hours=1.5, paused=True)
    out = seller.post(client, seller.sell_listing(ME, "15", 90), paused, RULES, live=True)
    assert not out.sent and client.posted == []


def test_a_live_bid_posts_cash_for_any_copy_and_books_the_commitment(tmp_path):
    client, ledger = FakeOfferClient(), Ledger(tmp_path / "ledger.jsonl")
    out = seller.post(client, seller.bid_listing("LAV-09", "rare", 70), CTX, RULES, live=True, ledger=ledger)
    assert out.sent and client.posted[0]["give"] == {"cash": 70} and client.posted[0]["want"] == {"cards": ["LAV-09"]}
    assert ledger.spent_since(0) == 70


def test_bids_are_guarded_like_buys():
    client = FakeOfferClient()
    held = seller.post(client, seller.bid_listing("LAT-09", "rare", 60), CTX, RULES, live=True)
    too_high = seller.post(client, seller.bid_listing("LAV-09", "rare", 81), CTX, RULES, live=True)
    assert "block_buying_held_cards" in held.message and "max_price_rare" in too_high.message
    assert client.posted == []


def test_offer_sides_read_in_a_few_words():
    assert seller.offer_side({"assets": [{"ref": "LAT-09", "id": 15}], "cash": 0}) == "LAT-09#15"
    assert seller.offer_side({"cash": 70, "cards": ["LAV-09"]}) == "70 P + any LAV-09"
    assert seller.offer_side({"types": ["card:SAL-08"]}) == "any SAL-08"
    assert seller.offer_side(None) == "-"
    offers = [{"id": 5, "status": "open", "venue": "rastro", "give": {"assets": [{"ref": "LAT-09", "id": 15}]}}]
    assert "LAT-09#15" in text(render.offers_table(offers))


def test_cli_cancel_is_a_dry_run_without_live():
    result = CliRunner().invoke(app, ["sell", "cancel", "12"])
    assert result.exit_code == 0 and "would cancel offer 12" in result.stdout


OUR_OPEN = [
    {"id": 1, "maker": "t01", "status": "open", "give": {"cash": 70}, "want": {"cards": ["LAV-09"]}},
    {"id": 2, "maker": "m3950d43b", "status": "open", "give": {"assets": [{"id": 15, "ref": "LAT-09"}]}},
    {"id": 3, "maker": "t07", "to": "t01", "status": "open", "give": {"cash": 50}, "want": {"cards": ["LAT-09"]}},
    {"id": 4, "maker": "t01", "status": "cancelled", "give": {"cash": 30}, "want": {"cards": ["SAL-09"]}},
]


def test_open_commitments_count_our_open_offers_and_fail_closed_on_unknown_makers():
    c = seller.open_commitments(OUR_OPEN, "t01")
    assert (c.cash, c.wanted, c.listed) == (70, ("LAV-09",), frozenset({15}))  # 3 is to us, 4 is cancelled
    assert seller.offers_in({"offers": OUR_OPEN[:1], "to_me": OUR_OPEN[2:3], "note": "x"}) == [OUR_OPEN[0], OUR_OPEN[2]]
    ctx = seller.committed_context(CTX, c)
    assert (ctx.cash, ctx.held["LAV-09"], ctx.held["LAT-09"]) == (283, 1, 1)


def test_dealer_thread_bids_count_toward_the_hourly_spend_and_pack_caps():
    # A board bid is booked as spend when it is posted; a thread bid only when its deal settles, so the
    # context counts it while it stands: three dealer threads bidding 40 on top of 100 spent is 220 > 150.
    threads = [
        {"id": 10 + n, "maker": "t01", "status": "open", "thread": 80 + n, "give": {"cash": 40}, "want": {"types": [t]}}
        for n, t in enumerate(["card:LAV-08", "card:SAL-02", "pack:sobre_barrio"])
    ]
    c = seller.open_commitments([OUR_OPEN[0], *threads], "t01")
    assert (c.cash, c.thread_cash, c.thread_packs) == (190, 120, 1)
    ctx = seller.committed_context(replace(CTX, cash=600, spent_last_hour=100, packs_last_hour=2), c)
    assert (ctx.spent_last_hour, ctx.packs_last_hour) == (220, 3)  # the board bid's 70 is in the ledger already
    verdict = check(Action("bid", "SAL-03", "common", 10), ctx, RULES)
    assert "spend 220 + 10 > max_spend_per_game_hour 150" in str(verdict)
    assert "3 pack(s) bought this game hour" in str(check(Action("bid", "sobre_barrio", "pack", 18), ctx, RULES))


def test_a_dealer_thread_counts_once_at_its_biggest_open_bid():
    # Thread 101: our bids 7, 8 and 9 are offers 732, 744 and 759. If the old ones still read open, the thread
    # still settles one deal at most: 9 P and one pack at risk, not 24 P and three packs.
    thread = [
        {"id": oid, "maker": "t01", "status": "open", "thread": 101, "give": {"cash": p}, "want": {"types": [t]}}
        for oid, p, t in [(732, 7, "pack:sobre_barrio"), (759, 9, "pack:sobre_barrio"), (744, 8, "pack:sobre_barrio")]
    ]
    c = seller.open_commitments([OUR_OPEN[0], *thread], "t01")
    assert (c.cash, c.thread_cash, c.thread_packs, c.wanted) == (70 + 9, 9, 1, ("LAV-09", "sobre_barrio"))
    same = [{**o, "give": {"cash": 9}} for o in thread]  # a tie: the newest stands for the thread
    assert [o["id"] for o in seller.one_per_thread(same)] == [759]


def test_open_bids_block_a_second_bid_and_count_toward_the_cash_floor():
    client, c = FakeOfferClient(), seller.open_commitments(OUR_OPEN, "t01")
    again = seller.post(client, seller.bid_listing("LAV-09", "rare", 60), CTX, RULES, live=True, commitments=c)
    other = seller.post(client, seller.bid_listing("SAL-09", "rare", 20), CTX, RULES, live=True, commitments=c)
    assert "block_buying_held_cards" in again.message
    assert "cash 283 - 20 < cash_floor 270" in other.message and client.posted == []


def test_an_asset_already_listed_is_not_listed_twice():
    client = FakeOfferClient()
    c = seller.open_commitments(OUR_OPEN, "t01")
    out = seller.post(client, seller.sell_listing(ME, "15", 90), CTX, RULES, live=True, commitments=c)
    assert not out.sent and "already in one of our open offers" in out.message and client.posted == []


def test_a_copy_without_your_value_is_never_listed():
    me = {"assets": [{"id": 9, "kind": "card", "ref": "LAT-09", "your_value": None}]}
    with pytest.raises(seller.OfferError, match="no your_value"):
        seller.sell_listing(me, "LAT-09", 90)
    both = {"assets": [*me["assets"], {"id": 15, "kind": "card", "ref": "LAT-09", "your_value": 35.0}]}
    assert seller.sell_listing(both, "LAT-09", 90).asset_id == 15  # a priced copy is preferred


@pytest.mark.parametrize("mode", ["list", "bid", "swap"])
@pytest.mark.parametrize("status", [400, 408, 503])
def test_cli_unknown_publication_keeps_cash_or_asset_reserved(
    cli_env,  # noqa: F811
    tmp_path,
    monkeypatch,
    mode,
    status,  # noqa: F811
):  # noqa: F811
    from bazaar_agent import cli
    from bazaar_agent.agents import publication
    from bazaar_agent.sdk import BazaarError
    from tests.runtime_fakes import with_spare

    with_spare(cli_env)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    before = []

    def refused(*args, **kwargs):
        before.extend(ledger.publication_rows())
        raise BazaarError("upstream_timeout", "no confirmed outcome", status)

    monkeypatch.setattr(cli_env, "list_offer", refused)
    args = {
        "list": ["list", "LAT-03", "--price", "5"],
        "bid": ["bid", "LAV-08", "--price", "10"],
        "swap": ["swap", "LAT-03", "--for", "LAV-08", "--to", "t05", "--give-cash", "5"],
    }[mode]
    result = CliRunner().invoke(cli.app, ["sell", *args, "--live"])
    assert result.exit_code == 1, result.output
    assert any(kind == "publication_pending" for kind, _ in before), result.output
    pending = publication.with_pending(ledger, cli_env.me(), [], "t01", 100, 1.5)
    assert bool(pending) is (status != 400)
    if pending:
        assert pending[0]["give"].get("cash") == (10 if mode == "bid" else 5 if mode == "swap" else None)
        if mode != "bid":
            assert pending[0]["give"]["assets"][0]["id"] == 41


def test_cli_never_reuses_an_unknown_asset_promise(cli_env, tmp_path):  # noqa: F811
    from bazaar_agent import cli
    from bazaar_agent.agents import publication
    from tests.runtime_fakes import with_spare

    with_spare(cli_env)
    publication.reserve(Ledger(tmp_path / "ledger.jsonl"), 100, 1.5, "t01", {"assets": [41]}, {"cash": 5})
    result = CliRunner().invoke(cli.app, ["sell", "list", "41", "--price", "5", "--live"])
    assert result.exit_code == 1 and "already in one of our open offers" in result.output.replace("\n", " ")
    assert cli_env.sent == []


def test_cli_rechecks_selected_copy_value_inside_publication_lock(cli_env, monkeypatch):  # noqa: F811
    from bazaar_agent import cli
    from tests.runtime_fakes import with_spare

    with_spare(cli_env)
    stale = cli_env.me()
    monkeypatch.setattr(cli, "_team_me", lambda: (cli_env, stale))
    next(a for a in cli_env._me["assets"] if a["id"] == 41)["your_value"] = 118.6
    result = CliRunner().invoke(cli.app, ["sell", "list", "41", "--price", "29", "--live"])
    assert result.exit_code == 1 and "118.6" in result.output
    assert cli_env.sent == []


def test_cli_own_venue_and_busy_mutex_refuse_before_publication(cli_env, tmp_path, monkeypatch):  # noqa: F811
    from bazaar_agent import cli
    from bazaar_agent.ledger_pg import trade_lock
    from tests.agent_fakes import RASTRO
    from tests.runtime_fakes import Public, with_spare

    with_spare(cli_env)
    command = ["sell", "list", "41", "--price", "5", "--live"]
    with trade_lock(Ledger(tmp_path / "ledger.jsonl")):
        busy = CliRunner().invoke(cli.app, command)
    assert busy.exit_code == 1 and "publication lock" in busy.output
    monkeypatch.setattr(cli, "public_client", lambda settings: Public(venues=({**RASTRO, "owner": "t01"},)))
    own = CliRunner().invoke(cli.app, command)
    assert own.exit_code == 1 and "owned by us" in own.output
    assert cli_env.sent == []
