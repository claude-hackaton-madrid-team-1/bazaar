import pytest

from bazaar_agent.agents.dealer import (
    BidPlan,
    Move,
    Negotiation,
    apply_advice,
    decide,
    latest_dealer_offer,
    reopen_start,
    settled_price,
    words,
)


def neg(start=6, step=1, max_price=10, bids=(), opened=None):
    """`opened=(ask, bids_sent_before_it)`: her opening ask, already seen and countered."""
    n = Negotiation(BidPlan(start, step, max_price), list(bids))
    if opened is not None:
        n.opening_ask, n.lowest_ask, n.bids_at_opening = opened[0], opened[0], opened[1]
    return n


def test_opens_with_the_start_bid_when_she_has_no_offer():
    assert decide(neg(), None, None, False) == Move("bid", 6, reason="small distinct step up")


def test_accepts_when_her_ask_meets_our_next_bid():
    assert decide(neg(bids=[6, 7, 8], opened=(12, 1)), 9, 55, False).kind == "accept"


def test_keeps_stepping_while_her_ask_is_above_our_next_bid():
    move = decide(neg(bids=[6, 7]), 10, 55, False)
    assert (move.kind, move.price) == ("bid", 8)


def test_takes_a_final_below_her_opening_inside_the_limit_and_walks_from_one_above_it():
    assert decide(neg(bids=[6, 7], opened=(12, 1)), 10, 9, True) == Move("accept", 10, 9, "final within limit")
    above = decide(neg(bids=[6, 7], opened=(12, 1)), 11, 9, True)
    assert above.kind == "walk" and not above.reopen  # above our limit: no lower thread to try


def test_never_repeats_a_price_and_walks_when_the_limit_is_spent():
    n = neg(bids=[6, 8, 10], step=2, opened=(14, 1))
    assert n.next_bid() is None
    assert decide(n, 12, 3, False).kind == "walk"
    assert decide(n, 10, 3, False).kind == "accept"  # her ask came down to our limit


def test_bad_plans_are_refused():
    with pytest.raises(ValueError):
        BidPlan(start=12, step=1, max_price=10)


def test_jev_can_accept_earlier_but_never_above_the_limit():
    n = neg(bids=[6], opened=(12, 0))
    bid = decide(n, 9, 4, False)
    assert apply_advice(bid, "accept", n, 9, 4).kind == "accept"
    assert apply_advice(bid, "accept", n, 11, 4).kind == "bid"
    assert apply_advice(bid, "counter", n, 9, 4) == bid


def test_latest_dealer_offer_reads_structure_not_words():
    thread = {
        "standing_offers": [
            {"id": 1, "maker": "abuela", "status": "expired", "want": {"cash": 12}},
            {"id": 2, "maker": "t01", "status": "open", "give": {"cash": 7}},
            {"id": 3, "maker": "abuela", "status": "open", "want": {"cash": 10}, "final": True},
        ]
    }
    assert latest_dealer_offer(thread, "abuela") == (10, 3, True)
    assert latest_dealer_offer({"standing_offers": []}, "abuela") == (None, None, False)


def test_an_opening_ask_below_our_start_is_countered_not_accepted():
    n = Negotiation(BidPlan(18, 1, 20))
    assert decide(n, 17, 5, False) == Move("bid", 16, reason="counter below her unconceded ask 17")
    n.bids.append(16)
    assert decide(n, 16, 6, False) == Move("accept", 16, 6, "ask meets our next bid")  # she came down
    wide = Negotiation(BidPlan(18, 3, 20))
    assert decide(wide, 17, 5, False) == Move("bid", 14, reason="counter below her unconceded ask 17")
    wide.bids.append(14)
    assert decide(wide, 17, 5, False) == Move("bid", 15, reason="counter below her unconceded ask 17")


def test_a_welcome_offer_is_never_taken_on_the_first_tick():
    assert decide(neg(start=6, max_price=12), 9, 9, False) == Move("bid", 6, reason="small distinct step up")
    assert decide(neg(start=10, step=2, max_price=12), 9, 9, False) == Move(
        "bid", 7, reason="counter below her unconceded ask 9"
    )


def test_lav03_replay_walks_from_her_opening_ask_and_reopens_lower():
    # The real LAV-03 thread 99: we bid 6, her opening ask was 7 and we took it. It settled at her opening
    # price, which scores nothing on the ladder and unlocks nothing. Now: walk, and reopen from 5.
    n = Negotiation(BidPlan(6, 1, 9), [6])
    assert decide(n, 7, 9, False) == Move(
        "walk", reason="she held her opening ask 7: no counter left below it", reopen=True
    )
    assert reopen_start(n) == 5
    spent = neg(bids=[8], max_price=8)  # spent at our max and her opening ask meets it: still never taken
    assert decide(spent, 8, 9, False).kind == "walk" and reopen_start(spent) == 7


def test_a_final_offer_at_her_opening_price_is_walked_and_reopened_lower():
    n = neg(start=8)
    assert decide(n, 9, 1, True) == Move(
        "walk", reason="her final 9 is her opening price: it scores nothing", reopen=True
    )
    assert reopen_start(n) == 7  # one step under our planned first bid
    assert decide(neg(start=8), 11, 1, True).kind == "walk"
    assert reopen_start(Negotiation(BidPlan(1, 1, 5))) is None  # nothing lower than 1 to try


def test_settled_price_reads_the_deal_from_the_thread_messages():
    # The real thread 101: our bids are offers 720, 732, 744 (cancelled when replaced) and 759 at 9 (settled).
    msgs = [
        {"offer": {"id": i, "status": s, "give": {"cash": p}}}
        for i, s, p in [(720, "cancelled", 6), (759, "settled", 9)]
    ]
    assert settled_price({"messages": [*msgs, {"offer": None}]}) == 9
    assert settled_price({"messages": [{"offer": {"status": "settled", "want": {"cash": 7}}}]}) == 7  # her ask
    assert settled_price({"messages": []}) is None


def test_jev_accept_on_her_opening_ask_is_overridden():
    n = neg()
    bid = decide(n, 9, 4, False)
    assert bid.kind == "bid"
    assert apply_advice(bid, "accept", n, 9, 4) == bid  # not countered, she has not come down
    n.bids.append(6)
    assert apply_advice(decide(n, 9, 4, False), "accept", n, 9, 4) == decide(n, 9, 4, False)  # countered only
    assert apply_advice(decide(n, 8, 5, False), "accept", n, 8, 5).kind == "accept"  # countered and conceded


def test_words_vary_with_each_step():
    assert words(0, 7) != words(1, 8) and "7" in words(0, 7)


class FakeDealerClient:
    """Abuela on a fake clock: the clock stays on a tick for `reads_per_tick` reads, then advances."""

    def __init__(self, asks, reads_per_tick=5, welcome=None):
        self.asks, self.reads_per_tick, self.reads = list(asks), reads_per_tick, 0
        self.sent, self.accepted, self.closed, self.status = [], [], False, "open"
        self.welcome = welcome  # an ask she posts as soon as the thread opens, before any bid

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.reads // self.reads_per_tick, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer, topic):
        return {"id": 85}

    def thread(self, tid):
        n = len(self.sent)
        offers = []
        if self.accepted:
            self.status = "deal"
        elif 0 < n <= len(self.asks) or (n == 0 and self.welcome is not None):
            offers = [
                {
                    "id": 500 + n,
                    "maker": "abuela",
                    "status": "open",
                    "give": {"cash": 0, "types": ["card:LAV-03"]},
                    "want": {"cash": self.asks[n - 1] if n else self.welcome, "assets": [], "types": []},
                }
            ]
        return {"status": self.status, "standing_offers": offers}

    def say(self, tid, text, price):
        self.sent.append(price)

    def accept(self, offer_id):
        self.accepted.append(offer_id)

    def close_thread(self, tid):
        self.closed = True


def test_negotiate_sends_one_message_per_tick_even_when_the_clock_is_read_many_times():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
    )
    assert client.sent == [6, 7, 8]  # one distinct bid per tick, never a repeat on the same tick
    assert (out.status, out.price, client.accepted, client.closed) == ("deal", 9, [503], False)


def test_negotiate_times_out_by_ticks_and_closes_the_thread():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[30] * 20)
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert (out.status, len(client.sent), client.closed) == ("timeout", 3, True)


def test_a_guardrail_denial_turns_the_move_into_a_walk_and_deals_are_reported():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        guard=lambda m, _tid: "cash_floor" if m.price and m.price >= 8 else None,
    )
    assert (client.sent, out.status, client.closed) == ([6, 7], "walked", True)

    deals = []
    client = FakeDealerClient(asks=[12, 10, 9])
    negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        on_deal=lambda price, tick, t: deals.append(price),
    )
    assert deals == [9]


class LiveDealerClient(FakeDealerClient):
    def me(self):
        return {"cash": 400, "assets": []}

    def my_offers(self):  # #72: `dealer buy` counts our open offers too
        return {"offers": []}


@pytest.fixture
def live_dealer_buy(monkeypatch, tmp_path):
    from bazaar_agent import cli
    from bazaar_agent.config import Settings

    client = LiveDealerClient(asks=[12, 10, 9])
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(cli, "_rarity_of", lambda item: "common")
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    return cli, client


def dealer_buy(cli, ledger, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
    args = ["dealer", "buy", "LAV-03", "--max", "10", "--start", "6", "--live"]
    result = CliRunner().invoke(cli.app, args)
    return result, " ".join(result.output.split())


def test_live_dealer_buy_with_the_ledger_down_exits_cleanly_before_opening(live_dealer_buy, monkeypatch):
    import psycopg

    from bazaar_agent.ledger_pg import PgLedger

    cli, client = live_dealer_buy

    def refused():
        raise psycopg.OperationalError("down")

    result, output = dealer_buy(cli, PgLedger(refused, "dealer-buy"), monkeypatch)
    assert result.exit_code == 1 and not isinstance(result.exception, psycopg.Error | RuntimeError)
    assert "refusing to trade: ledger read failed" in output and "(fail closed)" in output
    assert client.sent == [] and client.reads == 1  # read the clock, never opened a thread


def test_a_ledger_failure_inside_the_guard_walks_instead_of_accepting(live_dealer_buy, monkeypatch, tmp_path):
    from bazaar_agent.guardrails import Ledger
    from bazaar_agent.ledger_pg import LedgerUnavailable

    class ReserveFails(Ledger):
        def reserve_accept(self, *args, **kw):
            raise LedgerUnavailable("accept reservation failed (OperationalError)")

    cli, client = live_dealer_buy
    result, output = dealer_buy(cli, ReserveFails(tmp_path / "ledger.jsonl"), monkeypatch)
    assert result.exit_code == 0, result.output
    assert client.sent == [6, 7, 8] and client.accepted == [] and client.closed  # her 9 was not taken
    assert "accept reservation failed (OperationalError); no write without the shared ledger" in output
    assert "walked" in output


def test_offer_terms_must_be_exactly_the_requested_item_for_cash_only():
    from bazaar_agent.agents.dealer import offer_terms_problem, requested_item

    real = {
        "give": {"cash": 0, "assets": [], "types": ["card:LAV-06"]},
        "want": {"cash": 29, "assets": [], "types": []},
    }
    assert offer_terms_problem(real, "LAV-06") is None
    assert "instead of exactly" in offer_terms_problem(real, "LAV-05")
    sneaky = {"give": {"types": ["card:LAV-06"]}, "want": {"cash": 9, "assets": [{"id": 3, "ref": "LAV-01"}]}}
    assert "our assets" in offer_terms_problem(sneaky, "LAV-06")
    swapped = {"give": {"types": ["card:LAV-01"]}, "want": {"cash": 9}}
    assert offer_terms_problem(swapped, "LAV-06")
    assert requested_item({"buy": {"pack": "sobre_barrio"}}) == "sobre_barrio"
    assert requested_item({"sell": {"assets": [1]}}) is None


def test_a_mismatched_offer_is_never_accepted():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 9, 9])
    client.thread = lambda tid, c=client: {  # she quotes 9 but for a different card
        "status": "open",
        "standing_offers": [
            {"id": 777, "maker": "abuela", "status": "open", "give": {"types": ["card:LAV-01"]}, "want": {"cash": 9}}
        ],
    }
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert client.accepted == [] and out.status == "timeout"


def test_an_accept_on_the_last_tick_waits_for_settlement_instead_of_timing_out():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 6])  # she opens at 9, we counter 7, she comes down to 6 on tick 3 of 3
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert (client.accepted, client.closed, out.status, out.price) == ([502], False, "deal", 6)


def test_a_broken_observer_never_changes_or_breaks_the_negotiation():
    from bazaar_agent.agents.dealer import Observer, negotiate

    class Exploding(Observer):
        def __getattribute__(self, name):
            if name in ("opened", "thread_read", "guardrail", "move", "refused", "finished"):

                def boom(*_a):
                    raise RuntimeError("tracing backend down")

                return boom
            return object.__getattribute__(self, name)

    logs: list[str] = []
    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=logs.append,
        sleep=lambda _: None,
        observer=Exploding(),
    )
    assert (client.sent, out.status, out.price) == ([6, 7, 8], "deal", 9)
    assert sum("tracing hook" in line for line in logs) == 1  # warned once, not every tick


def test_words_address_the_dealer_we_are_talking_to():
    texts = [words(step, 20, "chato") for step in range(6)]
    assert not any("Carmen" in t for t in texts)
    assert any("Chato" in t for t in texts)
    assert any("Carmen" in words(step, 9, "abuela") for step in range(6))
    assert all("Carmen" not in words(step, 9, "nuevo") for step in range(6))  # unknown dealer: neutral


def test_negotiate_counters_a_welcome_offer_instead_of_taking_it_on_the_first_tick():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[6], welcome=7)  # her welcome 7 is below our start 8
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(8, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
    )
    assert client.sent == [6]  # countered below her welcome, never accepted it with zero bids
    assert (client.accepted, out.status, out.price) == ([501], "deal", 6)


def test_negotiate_overrides_a_jev_accept_on_her_opening_ask():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 8])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        advisor=lambda neg, ask, final: "accept",
    )
    assert client.sent == [6, 7]  # Jev said accept at her opening 9: we countered 7 instead
    assert (client.accepted, out.status, out.price) == ([502], "deal", 8)


def test_a_busy_accept_slot_bids_her_ask_instead_of_going_silent():
    from bazaar_agent.agents.dealer import negotiate

    class TakesOurBid(FakeDealerClient):
        def say(self, tid, text, price):
            standing = self.asks[len(self.sent) - 1] if self.sent else None
            super().say(tid, text, price)
            if standing is not None and price >= standing:  # our bid meets her ask: she takes it
                self.status = "deal"

    client = TakesOurBid(asks=[12, 10, 9])
    reserved: list[int] = []

    def reserve(move, clock):
        reserved.append(clock.tick)
        return False  # the duel player holds the team's accept this tick

    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        reserve=reserve,
    )
    assert client.sent == [6, 7, 8, 9] and client.accepted == [] and len(reserved) == 1  # bid her 9 that tick
    assert (client.closed, out.status, out.price) == (False, "deal", 9)


def test_a_busy_accept_slot_never_bids_her_opening_ask():
    from bazaar_agent.agents.dealer import meet_ask

    n = neg(bids=[8], opened=(12, 1))
    assert meet_ask(n, 9) == Move("bid", 9, reason="accept slot used: meet her ask")
    assert meet_ask(n, 12).kind == "wait" and meet_ask(n, 11).kind == "wait"  # her opening / above our max
    assert meet_ask(neg(bids=[8]), 9).kind == "wait"  # no opening seen: we cannot tell, so never


def test_negotiate_replays_lav03_and_walks_from_her_opening_ask_with_a_lower_reopen():
    from bazaar_agent.agents.dealer import negotiate

    logs: list[str] = []
    client = FakeDealerClient(asks=[7])  # we bid 6, then her non-final opening ask is 7
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 9),
        log=logs.append,
        sleep=lambda _: None,
    )
    assert client.sent == [6] and client.accepted == [] and client.closed
    assert (out.status, out.price, out.reopen_start) == ("walked", None, 5)
    assert any("→ walk  (she held her opening ask 7: no counter left below it)" in line for line in logs)
