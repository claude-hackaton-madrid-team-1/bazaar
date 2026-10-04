import pytest

from bazaar_agent.agents.dealer import Hold, Move
from bazaar_agent.agents.dealer_sell import (
    AskPlan,
    SellNegotiation,
    SellRefused,
    ask_schedule,
    check_floor,
    copy_to_sell,
    dealer_buys,
    dealer_refusal,
    decide_sell,
    latest_dealer_bid,
    negotiate_sell,
    only_copy,
    page_complete,
    sell_offer_problem,
    sell_topic,
)

ASSET = 171


def neg(start=20, step=2, floor=12, asks=(), opening=None):
    n = SellNegotiation(AskPlan(start, step, floor), list(asks))
    if opening is not None:
        n.opening_bid = n.highest_bid = opening
    return n


def test_opens_with_the_start_ask_and_steps_down_distinct_prices_to_the_floor():
    assert decide_sell(neg(), None, None, False) == Move("bid", 20, reason="small distinct step down")
    assert ask_schedule(AskPlan(20, 3, 12)) == [20, 17, 14, 12]
    assert len(set(ask_schedule(AskPlan(9, 1, 5)))) == 5  # never the same price twice


def test_bad_plans_are_refused():
    for start, step, floor in ((10, 1, 11), (10, 0, 5), (10, 1, 0)):
        with pytest.raises(ValueError):
            AskPlan(start, step, floor)


def test_accepts_a_raised_bid_that_meets_our_next_ask():
    n = neg(asks=[20, 18, 16], opening=10)
    assert decide_sell(n, 14, 7, False) == Move("accept", 14, 7, "her bid meets our ask")


def test_keeps_stepping_down_while_her_bid_is_below_our_next_ask():
    move = decide_sell(neg(asks=[20], opening=10), 11, 7, False)
    assert (move.kind, move.price) == ("bid", 18)


def test_never_closes_at_her_opening_bid_and_counters_above_it():
    move = decide_sell(neg(start=12, step=2, floor=8, asks=[12, 11]), 9, 7, False)  # 9 = her opening bid
    assert (move.kind, move.price) == ("bid", 10)  # our next ask (9) would close at her opening
    held = decide_sell(neg(start=12, step=2, floor=8, asks=[12, 10]), 9, 7, False)
    assert held.kind == "walk" and "held her opening" in held.reason  # no whole price left above it


def test_a_final_bid_is_taken_above_the_floor_and_walked_below_it():
    assert decide_sell(neg(asks=[20, 18], opening=10), 13, 7, True).kind == "accept"
    below = decide_sell(neg(asks=[20, 18], opening=10), 11, 7, True)
    assert below.kind == "walk" and "below our floor" in below.reason


def test_a_final_at_her_opening_bid_is_walked():
    move = decide_sell(neg(asks=[20, 18]), 13, 7, True)  # 13 is both her first and her final bid
    assert move.kind == "walk" and "opening" in move.reason


def test_never_asks_below_the_floor_and_walks_when_spent():
    n = neg(start=14, step=2, floor=12, asks=[14, 12], opening=5)
    assert decide_sell(n, 5, 7, False).kind == "walk"


def test_sell_offers_must_give_cash_only_for_exactly_our_copy():
    ok = {"give": {"cash": 13}, "want": {"assets": [{"id": ASSET, "ref": "LAV-02"}]}}
    assert sell_offer_problem(ok, ASSET) is None
    assert sell_offer_problem({"give": {"cash": 13}, "want": {"assets": [ASSET]}}, ASSET) is None
    assert sell_offer_problem({"give": {"cash": 13}, "want": {"assets": [ASSET, 9]}}, ASSET)
    assert sell_offer_problem({"give": {"cash": 13, "types": ["card:X"]}, "want": {"assets": [ASSET]}}, ASSET)
    assert sell_offer_problem({"give": {"cash": 0}, "want": {"assets": [ASSET]}}, ASSET)
    assert sell_offer_problem({"give": {"cash": 13}, "want": {"cash": 2, "assets": [ASSET]}}, ASSET)
    thread = {"standing_offers": [{"id": 4, "maker": "abuela", "status": "open", **ok, "final": True}]}
    assert latest_dealer_bid(thread, "abuela", ASSET) == (13, 4, True)
    assert latest_dealer_bid(thread, "abuela", 999) == (None, None, False)


def test_only_copy_of_a_page_card_is_refused_and_the_cheapest_duplicate_is_picked():
    one = {"assets": [{"id": 1, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 3}]}
    with pytest.raises(SellRefused, match="never sell the last one"):
        copy_to_sell(one, "MAL-02")
    two = {
        "assets": [
            {**one["assets"][0]},
            {"id": 2, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 1.5},
        ]
    }
    assert copy_to_sell(two, "MAL-02")["id"] == 2
    with pytest.raises(SellRefused, match="hold no card"):
        copy_to_sell(two, "SAL-01")
    blind = {"assets": [{"id": i, "kind": "card", "ref": "X-1", "rarity": "common"} for i in (1, 2)]}
    with pytest.raises(SellRefused, match="your_value"):
        copy_to_sell(blind, "X-1")


def test_an_incomplete_pages_single_copy_may_be_sold_under_protect_complete_pages_only():
    from bazaar_agent.guardrails import Guardrails

    album = {"pages": [{"set": "RET", "complete": False}, {"set": "SAL", "complete": True}]}
    me = {
        "album": album,
        "assets": [
            {"id": 1, "kind": "card", "ref": "RET-08", "rarity": "uncommon", "your_value": 17.5},
            {"id": 2, "kind": "card", "ref": "SAL-10", "rarity": "rare", "your_value": 177.0},
        ],
    }
    on = Guardrails(protect_complete_pages_only=True)
    assert page_complete(me, "RET-08", on) is False and page_complete(me, "SAL-10", on) is True
    assert copy_to_sell(me, "RET-08", page_complete=page_complete(me, "RET-08", on))["id"] == 1
    with pytest.raises(SellRefused, match="never sell the last one"):  # a complete page keeps its copy
        copy_to_sell(me, "SAL-10", page_complete=page_complete(me, "SAL-10", on))
    with pytest.raises(SellRefused, match="never sell the last one"):  # listed by an ask of ours: none free
        copy_to_sell(me, "RET-08", frozenset({1}), page_complete=False)
    # fail closed: rule off, album unread or a set off the album protects as before
    assert page_complete(me, "RET-08", Guardrails(protect_complete_pages_only=False)) is None
    assert page_complete({"assets": me["assets"]}, "RET-08", on) is None
    assert page_complete(me, "CHA-02", on) is None
    assert only_copy("RET-08", "uncommon", 1) and only_copy("RET-08", "uncommon", 1, True)
    assert not only_copy("RET-08", "uncommon", 1, False) and only_copy("RET-08", "uncommon", 0, False)


def test_floor_must_cover_the_copys_your_value():
    check_floor(5, 4.2)
    with pytest.raises(SellRefused, match="your_value"):
        check_floor(4, 4.2)


def test_the_menu_says_which_rarities_a_dealer_buys():
    abuela = {"menu": {"buys": [{"rarity": "common"}, {"rarity": "uncommon"}]}}
    assert dealer_buys(abuela, "common") and not dealer_buys(abuela, "rare") and not dealer_buys({}, "common")
    assert sell_topic(ASSET) == {"sell": {"assets": [ASSET]}}


class FakeBuyingDealer:
    """A dealer that buys our copy: it bids `bids[n]` after our n-th ask, and accepts an ask at or below
    `limit` ("Deal!" settles at once). The clock stays on a tick for `reads_per_tick` reads."""

    def __init__(self, bids, limit=0, reads_per_tick=5, final_after=None):
        self.bids, self.limit, self.reads_per_tick, self.reads = list(bids), limit, reads_per_tick, 0
        self.sent, self.sent_ticks, self.accepted, self.closed = [], [], [], False
        self.topic, self.final_after = None, final_after

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.reads // self.reads_per_tick, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer, topic):
        self.topic = topic
        return {"id": 85}

    def _offer(self, i, cash, status="open"):
        final = self.final_after is not None and i >= self.final_after
        want = {"assets": [{"id": ASSET}]}
        return {
            "id": 500 + i,
            "maker": "abuela",
            "status": status,
            "give": {"cash": cash},
            "want": want,
            "final": final,
        }

    def thread(self, tid):
        n = len(self.sent)
        if self.accepted or (self.sent and self.sent[-1] <= self.limit):
            price = self.sent[-1] if not self.accepted else self.bids[n - 1]
            msg = {"sender": "abuela", "offer": {**self._offer(n, price, "settled")}}
            return {"status": "deal", "messages": [msg], "standing_offers": []}
        offers = [self._offer(n, self.bids[n - 1])] if 0 < n <= len(self.bids) else []
        messages = [{"sender": "t01"}, *({"sender": "abuela", "offer": o} for o in offers)]
        return {"status": "open", "messages": messages, "standing_offers": offers}

    def say(self, tid, text, price):
        self.sent.append(price)
        self.sent_ticks.append(100 + self.reads // self.reads_per_tick)

    def accept(self, offer_id):
        self.accepted.append(offer_id)

    def close_thread(self, tid):
        self.closed = True
        return {"status": "closed"}


PLAN = AskPlan(20, 2, 12)


def run(client, plan=PLAN, **kw):
    return negotiate_sell(client, "abuela", ASSET, plan, log=lambda _: None, sleep=lambda _: None, **kw)


def test_negotiate_sends_one_distinct_ask_per_tick_and_accepts_a_raised_bid():
    client = FakeBuyingDealer(bids=[10, 12, 15])
    deals = []
    out = run(client, on_deal=lambda price, tick, t: deals.append(price))
    assert client.topic == {"sell": {"assets": [ASSET]}}
    assert client.sent == [20, 18, 16] and len(set(client.sent_ticks)) == 3  # one message per tick
    assert (out.status, out.price, client.accepted, deals) == ("deal", 15, [503], [15])


def test_negotiate_closes_when_the_dealer_accepts_our_ask():
    out = run(FakeBuyingDealer(bids=[10, 12], limit=18))
    assert (out.status, out.price, out.bids) == ("deal", 18, (20, 18))


def test_negotiate_walks_from_a_final_below_the_floor():
    client = FakeBuyingDealer(bids=[5, 6, 7], final_after=3)
    out = run(client)
    assert (out.status, client.accepted, client.closed) == ("walked", [], True)
    assert min(client.sent) >= 12


def test_a_guardrail_denial_walks_and_a_hold_sends_nothing():
    client = FakeBuyingDealer(bids=[10, 12, 15])
    out = run(client, guard=lambda m, _tid: "denied" if m.kind == "accept" else None)
    assert (out.status, client.accepted, client.closed) == ("walked", [], True)

    def hold(move, tid):
        raise Hold("ledger down")

    client = FakeBuyingDealer(bids=[10])
    out = run(client, guard=hold, max_ticks=3)
    assert (client.sent, out.status, client.closed) == ([], "timeout", True)


def test_the_inspector_and_a_taken_accept_slot_block_the_accept_without_walking():
    client = FakeBuyingDealer(bids=[10, 12, 15])
    out = run(client, inspect=lambda thread, move: "block: swapped", max_ticks=4)
    assert client.accepted == [] and out.status == "timeout"
    client = FakeBuyingDealer(bids=[10, 12, 15])
    out = run(client, reserve=lambda move, clock: False, max_ticks=4)
    assert client.accepted == [] and out.status == "timeout"


def test_the_kill_switch_opens_nothing():
    client = FakeBuyingDealer(bids=[10])
    out = run(client, kill_switch=lambda: ["PAUSE"])
    assert (out.status, client.topic, client.sent) == ("held", None, [])


def test_every_move_is_reported_to_the_decision_hook():
    rows = []
    run(FakeBuyingDealer(bids=[10, 12, 15]), on_move=lambda m, tick, outcome: rows.append((m.kind, m.price, outcome)))
    assert rows == [("bid", 20, "sent"), ("bid", 18, "sent"), ("bid", 16, "sent"), ("accept", 15, "sent")]


def test_the_accept_gate_reads_a_dealers_bid_on_a_sale():
    from bazaar_agent.agents.accept_gate import dealer_gate
    from bazaar_agent.agents.inspector import CardIndex

    offer = {"id": 4, "maker": "abuela", "status": "open", "give": {"cash": 13}, "want": {"assets": [{"id": ASSET}]}}
    thread = {"standing_offers": [offer], "messages": [{"id": 1, "sender": "abuela", "offer": offer, "text": "13"}]}
    cards = CardIndex.from_catalog({})
    assert dealer_gate(thread, "abuela", 4, 13, sell_topic(ASSET), cards).allowed
    assert not dealer_gate(thread, "abuela", 4, 15, sell_topic(ASSET), cards).allowed
    swapped = {**offer, "want": {"assets": [{"id": 9}]}}
    other = {"standing_offers": [swapped], "messages": [{"id": 1, "sender": "abuela", "offer": swapped}]}
    assert not dealer_gate(other, "abuela", 4, 13, sell_topic(ASSET), cards).allowed


ME = {
    "id": "t01",
    "cash": 300,
    "assets": [
        {"id": 7, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 4.0},
        {"id": 8, "kind": "card", "ref": "MAL-02", "rarity": "common", "your_value": 2.0},
        {"id": 9, "kind": "card", "ref": "SAL-10", "rarity": "rare", "your_value": 30.0},
    ],
}
OFFERS: list = []
ABUELA = {"id": "abuela", "menu": {"buys": [{"rarity": "common"}, {"rarity": "uncommon"}]}}


@pytest.fixture
def sell_cli(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from bazaar_agent import cli
    from bazaar_agent.config import Settings

    class Public:
        def dealers(self):
            return {"personas": [ABUELA]}

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))

    class Client:
        def my_offers(self):
            return {"offers": OFFERS}

    monkeypatch.setattr(cli, "_team_me", lambda: (Client(), ME))
    monkeypatch.setattr(cli, "public_client", lambda settings: Public())
    return lambda *args: CliRunner().invoke(cli.app, ["dealer", "sell", *args])


def test_cli_dry_run_picks_the_cheapest_duplicate_and_prints_the_asks(sell_cli):
    result = sell_cli("MAL-02", "--start", "12", "--min", "6", "--step", "2")
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())  # rich wraps long lines
    assert "'assets': [8]" in out and "[12, 10, 8, 6]" in out


def test_cli_refuses_a_floor_under_your_value_an_only_copy_and_a_dealer_that_does_not_buy(sell_cli):
    low = sell_cli("MAL-02", "--start", "12", "--min", "1")
    assert low.exit_code == 1 and "your_value" in low.output
    only = sell_cli("SAL-10", "--start", "90", "--min", "40")
    assert only.exit_code == 1 and "never sell the last one" in " ".join(only.output.split())
    upside = sell_cli("MAL-02", "--start", "5", "--min", "6")
    assert upside.exit_code == 1 and "bad plan" in upside.output
    nobody = sell_cli("MAL-02", "--start", "12", "--min", "6", "--dealer", "chato")
    assert nobody.exit_code == 1 and "chato is not among the dealers" in " ".join(nobody.output.split())


PILAR = {
    "id": "pilar",
    "kind": "collector",
    "status": "active",
    "menu": {
        "buys": [
            {"rarity": "rare", "sets": ["SAL", "RET"]},
            {"rarity": "uncommon", "sets": "released"},
        ]
    },
}


def test_a_collector_with_set_scoped_lines_counts_as_a_buyer():
    assert dealer_buys(PILAR, "rare", "SAL") and not dealer_buys(PILAR, "rare", "MAL")
    assert dealer_buys(PILAR, "uncommon", "MAL") and not dealer_buys(PILAR, "common", "SAL")
    assert dealer_buys({"menu": {"buys": [{"rarity": ["rare", "epic"], "sets": "SAL"}]}}, "epic", "SAL")


def test_dealer_refusal_needs_an_active_unlocked_buyer():
    rare = {"id": 9, "ref": "SAL-10", "rarity": "rare", "set": "SAL"}
    me = {"unlocked": ["abuela", "chato", "pilar"]}
    assert dealer_refusal("pilar", [PILAR], me, rare) is None
    assert "not unlocked" in dealer_refusal("pilar", [PILAR], {"unlocked": ["abuela"]}, rare)
    assert "announced" in dealer_refusal("pilar", [{**PILAR, "status": "announced"}], me, rare)
    assert "not among" in dealer_refusal("vault", [PILAR], me, rare)
    assert "does not buy" in dealer_refusal("pilar", [PILAR], me, {**rare, "ref": "MAL-10", "set": "MAL"})


def test_cli_refuses_the_last_copy_not_already_listed_by_the_maker(sell_cli, monkeypatch):
    ask = {
        "id": 50,
        "maker": "t01",
        "status": "open",
        "give": {"assets": [{"id": 8, "ref": "MAL-02"}]},
        "want": {"cash": 9},
    }
    monkeypatch.setattr(f"{__name__}.OFFERS", [ask])
    result = sell_cli("MAL-02", "--start", "12", "--min", "6")
    assert result.exit_code == 1 and "never sell the last one" in " ".join(result.output.split())


@pytest.mark.parametrize("terminal,thread,released", [("closed", 42, True), ("held", 42, False), ("held", None, True)])
def test_live_cli_reserves_before_negotiating_and_releases_only_confirmed_close(
    monkeypatch, tmp_path, terminal, thread, released
):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from bazaar_agent import cli
    from bazaar_agent.agents import dealer_sell, publication
    from bazaar_agent.agents.seller import open_commitments
    from bazaar_agent.config import Settings
    from bazaar_agent.guardrails import Ledger
    from tests.agent_fakes import FakeTeam

    team, ledger = FakeTeam(me=ME), Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "_team_me", lambda: (team, ME))
    monkeypatch.setattr(cli, "_ledger", lambda *a, **kw: ledger)
    monkeypatch.setattr(cli, "public_client", lambda settings: SimpleNamespace(dealers=lambda: {"personas": [ABUELA]}))
    monkeypatch.setattr(cli, "_offer_inspector", lambda *a: {})
    monkeypatch.setattr(cli, "_dealer_kind", lambda *a: "dealer")

    def negotiate(*args, **kw):
        offers = publication.with_pending(ledger, ME, [], "t01", 100, 1.5)
        assert 8 in open_commitments(offers, "t01").listed
        return SimpleNamespace(status=terminal, thread=thread, price=None, bids=(), ticks=1)

    monkeypatch.setattr(dealer_sell, "negotiate_sell", negotiate)
    result = CliRunner().invoke(cli.app, ["dealer", "sell", "MAL-02", "--start", "12", "--min", "6", "--live"])
    assert result.exit_code == 0, result.output
    assert (publication.with_pending(ledger, ME, [], "t01", 101, 1.6) == []) is released


def test_listed_copies_do_not_count_toward_the_only_copy_rule():
    held = {
        "assets": [{"id": i, "kind": "card", "ref": "LAT-04", "rarity": "common", "your_value": 1.3} for i in (4, 5)]
    }
    with pytest.raises(SellRefused, match="never sell the last one"):
        copy_to_sell(held, "LAT-04", listed=frozenset({5}))
    with pytest.raises(SellRefused, match="never sell the last one"):
        copy_to_sell(held, "LAT-04", unnamed=1)
    assert copy_to_sell(held, "LAT-04")["id"] == 5
    three = {"assets": [*held["assets"], {**held["assets"][0], "id": 6}]}
    assert copy_to_sell(three, "LAT-04", listed=frozenset({6}))["id"] in (4, 5)  # never the listed one


def test_only_copy_guard_reads_the_sellable_count():
    from bazaar_agent.agents.dealer_sell import only_copy

    assert only_copy("LAT-04", "common", 1) and not only_copy("LAT-04", "common", 2)
    assert not only_copy("LAT-11", "legendary", 0)


def test_an_opening_bid_at_or_above_our_start_is_countered_above_it_not_walked():
    move = decide_sell(SellNegotiation(AskPlan(10, 1, 6)), 12, 99, False)
    assert (move.kind, move.price) == ("bid", 13)


def test_cli_dry_run_sells_an_incomplete_pages_single_copy_and_keeps_a_complete_ones(sell_cli, monkeypatch):
    import sys

    module = sys.modules[__name__]
    album = {"pages": [{"set": "SAL", "complete": False}]}
    monkeypatch.setattr(module, "ME", {**ME, "album": album})
    from bazaar_agent import cli

    client = type("C", (), {"my_offers": lambda self: {"offers": []}})()
    monkeypatch.setattr(cli, "_team_me", lambda: (client, module.ME))
    ok = sell_cli("SAL-10", "--start", "90", "--min", "40", "--dealer", "abuela")
    out = " ".join(ok.output.split())  # past the only-copy rule: Abuela's menu buys no rare, refused there
    assert ok.exit_code == 1 and "never sell the last one" not in out and "abuela" in out
    monkeypatch.setattr(module, "ME", {**ME, "album": {"pages": [{"set": "SAL", "complete": True}]}})
    kept = sell_cli("SAL-10", "--start", "90", "--min", "40")
    assert kept.exit_code == 1 and "never sell the last one" in " ".join(kept.output.split())
