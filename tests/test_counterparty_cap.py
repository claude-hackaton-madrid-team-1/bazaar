"""`max_counterparty_share` (#14): no team may reach more than a share of our team-to-team volume.

Off by default (1.0). On, the check counts what each team settled with us plus every open offer of ours it
could take; an offer anyone may take counts against the team we trade most with. The maker addresses an
offer (`to`) when the public one would break the cap; the taker refuses a board accept from a capped maker.
"""

from copy import deepcopy

import pytest

from bazaar_agent import intel
from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.seller import bid_listing, trade_book
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import ANY_TEAM, Action, Context, Guardrails, TradeBook, check, counterparty_refusal
from tests.agent_fakes import FakePublic, FakeTeam, ask, bid, clock, parts, rows
from tests.test_runtime_tools import cli_env  # noqa: F401 (the CLI on fakes)
from tests.test_strategy import EVENTS

CAP = Guardrails(max_counterparty_share=0.25, counterparty_cap_base=200)
CTX = Context(cash=1000, held={}, tick=1, t_hours=0.1)


def settled(eid, frm, to, ref, price, persona=None):
    item = {"id": 7000 + eid, "ref": ref, "frm": frm, "to": to, "kind": "card"}
    payload = {"items": [item], "price": price, "persona": persona, "parties": [frm, to], "settlement": eid}
    return {"id": 9000 + eid, "tick": 5, "type": "settlement", "payload": payload}


def sell(price, team=ANY_TEAM, kind="sell"):
    return Action(kind, "LAT-03", "common", price, 1.0, counterparty=team)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the rule


def test_off_by_default_and_today_behaviour_holds():
    assert Guardrails().max_counterparty_share == 1.0
    assert check(sell(10_000), CTX, Guardrails()).allowed  # no trade book read, and none needed
    assert counterparty_refusal(None, "t05", 10_000, Guardrails()) is None


def test_on_without_our_volume_fails_closed():
    verdict = check(sell(10), CTX, CAP)
    assert not verdict.allowed and "volume was not read" in str(verdict)


def test_the_base_lets_the_first_trades_through_then_the_share_bites():
    empty = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=TradeBook())
    assert check(sell(50, "t05"), empty, CAP).allowed  # 0.25 × max(50, 200) = 50
    denied = check(sell(51, "t05"), empty, CAP)
    assert not denied.allowed and "counterparty t05: 0 + 51 > max_counterparty_share 0.25" in str(denied)
    grown = TradeBook({"t02": 100, "t03": 100, "t04": 100, "t05": 100})  # 400 settled, 100 each
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=grown)
    assert not check(sell(34, "t05"), ctx, CAP).allowed  # 134 > 0.25 × 434
    assert check(sell(30, "t06"), ctx, CAP).allowed  # a new team: 30 <= 0.25 × 430


def test_open_offers_count_addressed_to_their_team_and_public_ones_to_every_team():
    book = TradeBook({"t05": 20}, addressed={"t06": 25}, public=15)
    assert book.exposure("t05") == 35 and book.exposure("t06") == 40 and book.exposure("t07") == 15
    assert book.exposure(ANY_TEAM) == 40  # the worst case: the team that could take the most
    assert TradeBook(public=15).exposure(ANY_TEAM) == 15
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=book)
    assert check(sell(10, ANY_TEAM), ctx, CAP).allowed  # 40 + 10 <= 50
    assert not check(sell(11, ANY_TEAM), ctx, CAP).allowed
    assert check(sell(35, "t07"), ctx, CAP).allowed  # 15 + 35: addressed to a team with room


def test_dealers_and_non_trades_are_never_capped():
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=TradeBook({"t05": 10_000}))
    assert check(Action("accept_buy", "LAV-02", "common", 12), ctx, CAP).allowed  # a dealer: no counterparty
    # a duel inside our limit (#60's duel_inside_limit needs its terms) is never counterparty-capped
    duel = Action("duel_accept", "duel:1", None, 999, limit=1000, role="buyer", counterparty="t05")
    assert check(duel, ctx, CAP).allowed
    assert not check(Action("accept_buy", "LAV-02", "common", 12, counterparty="t05"), ctx, CAP).allowed


@pytest.mark.parametrize("share", [0.0, 1.5])
def test_the_share_is_a_fraction(share):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Guardrails(max_counterparty_share=share)


# ---------------------------------------------------------------- our volume, from the feed and our offers


def test_settled_volume_counts_team_trades_we_are_party_to():
    events = [
        settled(1, "t01", "t05", "LAT-03", 12),
        settled(2, "t06", "t01", "LAV-09", 70),
        settled(3, "t05", "t01", "LAV-08", 0),  # a swap leg without cash: its book
        settled(4, "abuela", "t01", "LAV-02", 9, persona="abuela"),  # a dealer
        settled(5, "t06", "t07", "MAL-01", 10),  # not ours
    ]
    assert intel.settled_volume(events, "t01", {"LAV-08": 25.0}) == {"t05": 37, "t06": 70}


def test_a_swap_counts_its_book_and_the_volume_of_an_action_can_differ_from_its_price():
    swap_leg = settled(1, "t05", "t01", "LAV-08", 0)
    assert intel.settled_volume(
        [swap_leg], "t01", intel.book_values({"sets": [{"cards": [{"id": "LAV-08", "book": 25}]}]})
    ) == {"t05": 25}
    offer = {
        "id": 9,
        "maker": "t01",
        "to": "t05",
        "give": {"assets": [{"id": 3, "ref": "LAT-09"}]},
        "want": {"cards": ["LAV-09"]},
    }
    assert trade_book([offer], "t01", {}, {"LAT-09": 70.0, "LAV-09": 70.0}).addressed == {"t05": 140}
    unknown = {**bid(10, "LAV-02", 12), "maker": "m77"}  # not addressed to us: ours, whatever the maker says
    assert trade_book([unknown], "t01", {}).public == 12
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=TradeBook({"t05": 40}))
    accept = Action("accept_buy", "LAV-02", "common", 12, counterparty="t05")
    assert not check(accept, ctx, CAP).allowed  # 40 + 12 > 50
    assert check(Action("accept_buy", "LAV-02", "common", 12, counterparty="t05", volume=10), ctx, CAP).allowed


def test_trade_book_reads_our_open_offers():
    offers = [
        bid(1, "LAV-09", 60),  # ours, public
        {**bid(2, "LAV-08", 30), "to": "t05"},  # ours, addressed
        bid(3, "LAV-02", 12, thread=40) | {"to": "abuela"},  # a dealer thread: not a team trade
        {**ask(4, "MAL-01", 9, maker="t07"), "to": "t01"},  # theirs, addressed to us
        {**bid(5, "LAV-01", 9), "status": "cancelled"},
    ]
    book = trade_book(offers, "t01", {"t06": 70})
    assert (book.settled, book.addressed, book.public) == ({"t06": 70}, {"t05": 30}, 60)


def test_a_listing_names_its_counterparty():
    assert bid_listing("LAV-09", "rare", 60).action().counterparty == ANY_TEAM
    listing = bid_listing("LAV-09", "rare", 60, to="t05")
    assert listing.action().counterparty == "t05" and "to t05" in listing.describe()


# ---------------------------------------------------------------- the maker and the taker


class Recording(FakeTeam):
    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.sent.append(("list_offer", give, want, venue, to))
        return {"id": next(self._ids), "status": "open"}

    def accept(self, offer_id, assets=None):
        raise AssertionError("the maker never accepts")


def with_feed(tmp_path, events, **rules):
    kw = parts(tmp_path, **rules)
    kw["feed"] = MarketFeed(lambda n: deepcopy(events))
    return kw


def test_maker_off_posts_every_target_for_anyone(tmp_path):
    team = Recording()
    Maker(team, FakePublic(), live=True, log=lambda m: None, now=lambda: 1000.0, **parts(tmp_path)).on_tick(clock())
    assert {(s[2].get("cash") or s[1].get("cash"), s[4]) for s in team.sent} == {(65, None), (68, None), (10, None)}


def test_maker_addresses_an_offer_the_public_cannot_take_and_drops_what_no_team_may(tmp_path):
    # We settled 90 with t09. Cap: 0.5 × max(volume, 200) = 100 per team.
    events = EVENTS + [settled(1, "t01", "t09", "SAL-01", 90)]
    team, lines = Recording(), []
    kw = with_feed(tmp_path, events, max_counterparty_share=0.5, counterparty_cap_base=200)
    Maker(team, FakePublic(events=events), live=True, log=lines.append, now=lambda: 1000.0, **kw).on_tick(clock())
    posts = {(s[2].get("cards", [None])[0] or s[1].get("assets", [None])[0], s[4]) for s in team.sent}
    # the LAV-09 bid (65): t09, its only holder, would reach 155: never posted, public or addressed
    assert not any(ref == "LAV-09" for ref, _ in posts)
    assert any("counterparty any team (public offer): 90 + 65 > max_counterparty_share 0.5" in line for line in lines)
    # the LAT-09 ask (68, asset 5): public worst case t09 90 + 68 > 100; addressed to t07 (0 + 68) passes
    assert (5, "t07") in posts
    # the LAT-03 ask (10, asset 4): public, t09 90 + 10 = 100 fits
    assert (4, None) in posts
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "post_ask" and r["inputs"].get("to")]
    assert row["inputs"]["to"] == "t07" and "addressed to t07" in row["reason"]


def taker(tmp_path, team, public, events, **rules):
    kw = with_feed(tmp_path, events, **rules)
    lines: list[str] = []
    t = Taker(
        team,
        public,
        live=True,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **kw,
    )
    return t, lines


def listed(offer_id, team):
    return {
        "id": 9500 + offer_id,
        "tick": 6,
        "type": "offer.listed",
        "actor": team,
        "payload": {"offer": {"id": offer_id}},
    }


def test_taker_refuses_a_board_ask_from_a_maker_at_its_cap(tmp_path):
    events = EVENTS + [settled(1, "t14", "t01", "SAL-01", 45), listed(1, "t14")]
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10, maker="m3950")]}, events=events)
    team = FakeTeam()
    t, lines = taker(tmp_path, team, public, events, max_counterparty_share=0.25)
    t.on_tick(clock())
    assert team.sent == []
    # the maker's share counts the ask (10), not ask + fee (12)
    assert any("counterparty t14: 45 + 10 > max_counterparty_share 0.25" in line for line in lines)


def test_taker_accepts_from_a_maker_with_room_and_refuses_an_unknown_one(tmp_path):
    events = EVENTS + [settled(1, "t14", "t01", "SAL-01", 45), listed(2, "t15")]
    public = FakePublic(
        boards={"rastro": [ask(2, "LAV-02", 10, maker="mAAA"), ask(3, "LAV-08", 20, asset=901, maker="mBBB")]},
        events=events,
    )
    team = FakeTeam()
    t, lines = taker(tmp_path, team, public, events, max_counterparty_share=0.25)
    t.on_tick(clock())
    # LAV-08 (the better score) comes from a pseudonym the feed never named: its share is unknown, refused;
    # LAV-02's maker is t15 (from `offer.listed`), with room: accepted
    assert team.sent == [("accept", 2)]
    assert any("counterparty 'mBBB' is not a known team" in line for line in lines)
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert row["inputs"]["maker"] == "t15"


def test_an_unknown_counterparty_fails_closed_only_when_the_cap_is_on():
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=TradeBook())
    assert "not a known team" in str(check(sell(10, "m3950d43b"), ctx, CAP))
    assert check(sell(10, "m3950d43b"), ctx, Guardrails()).allowed


def test_a_live_post_sends_its_to():
    from bazaar_agent.agents.seller import post

    class Client:
        sent: list = []

        def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
            self.sent.append(to)
            return {"id": 1}

    client = Client()
    ctx = Context(cash=1000, held={}, tick=1, t_hours=0.1, trades=TradeBook())
    assert post(client, bid_listing("LAV-09", "rare", 50, to="t05"), ctx, CAP, live=True).sent  # 50 = 0.25 × 200
    assert post(client, bid_listing("LAV-10", "rare", 30), ctx, Guardrails(), live=True).sent
    assert client.sent == ["t05", None]


@pytest.mark.usefixtures("cli_env")
def test_the_cli_sell_commands_take_to_and_read_our_volume_when_the_cap_is_on(monkeypatch):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from bazaar_agent import cli

    monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=CAP))
    out = CliRunner().invoke(cli.app, ["sell", "list", "LAT-03", "--price", "5", "--to", "t09"])
    assert out.exit_code == 0, out.output
    assert "dry run: would sell asset 4 (LAT-03) for 5 P on rastro to t09" in out.output.replace("\n", " ")
    out = CliRunner().invoke(cli.app, ["sell", "bid", "LAV-09", "--price", "60", "--to", "m3950"])
    assert out.exit_code == 1 and "--to takes a team id" in out.output


def test_the_runtime_reads_our_volume_only_when_the_cap_is_on(tmp_path):
    from bazaar_agent.runtime import actions
    from tests.runtime_fakes import backend

    assert actions._base(backend(tmp_path), clock()).ctx.trades is None
    b = backend(tmp_path / "on", rules=CAP)
    reads = []
    real = b.events
    b.events = lambda: reads.append(1) or real()  # type: ignore[method-assign]
    on = actions._base(b, clock())
    actions._base(b, clock())  # the same tick: the feed is not read again
    assert on.ctx.trades == TradeBook({}, {}, 0) and reads == [1]


def test_a_listing_and_a_planned_trade_carry_their_counterparty_not_a_duel_limit():
    # Merged with #60, Action's 6th field is the duel `limit`: a positional counterparty landed there and the
    # cap never saw the team. Action's fields after `your_value` are keyword-only now.
    from bazaar_agent.agents.seller import Listing

    listing = Listing("sell", "LAT-03", "common", 30, "rastro", {}, {}, your_value=1.0, to="t05")
    assert (listing.action().counterparty, listing.action().limit) == ("t05", None)
    assert Listing("bid", "LAT-03", "common", 30, "rastro", {}, {}).action().counterparty == ANY_TEAM
    with pytest.raises(TypeError):
        Action("sell", "LAT-03", "common", 30, 1.0, "t05")  # type: ignore[misc]
