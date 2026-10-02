import pytest

from bazaar_agent.agents.dealer import (
    BidPlan,
    Move,
    Negotiation,
    apply_advice,
    decide,
    latest_dealer_offer,
    words,
)


def neg(start=6, step=1, max_price=10, bids=()):
    return Negotiation(BidPlan(start, step, max_price), list(bids))


def test_opens_with_the_start_bid_when_she_has_no_offer():
    assert decide(neg(), None, None, False) == Move("bid", 6, reason="small distinct step up")


def test_accepts_when_her_ask_meets_our_next_bid():
    assert decide(neg(bids=[6, 7, 8]), 9, 55, False).kind == "accept"


def test_keeps_stepping_while_her_ask_is_above_our_next_bid():
    move = decide(neg(bids=[6, 7]), 10, 55, False)
    assert (move.kind, move.price) == ("bid", 8)


def test_takes_a_final_inside_the_limit_and_walks_from_one_above_it():
    assert decide(neg(bids=[6]), 10, 9, True).kind == "accept"
    assert decide(neg(bids=[6]), 12, 9, True).kind == "walk"


def test_never_repeats_a_price_and_walks_when_the_limit_is_spent():
    n = neg(bids=[6, 8, 10], step=2)
    assert n.next_bid() is None
    assert decide(n, 12, 3, False).kind == "walk"
    assert decide(n, 10, 3, False).kind == "accept"  # her ask came down to our limit


def test_bad_plans_are_refused():
    with pytest.raises(ValueError):
        BidPlan(start=12, step=1, max_price=10)


def test_jev_can_accept_earlier_but_never_above_the_limit():
    n = neg(bids=[6])
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


def test_words_vary_with_each_step():
    assert words(0, 7) != words(1, 8) and "7" in words(0, 7)


class FakeDealerClient:
    """Abuela on a fake clock: the clock stays on a tick for `reads_per_tick` reads, then advances."""

    def __init__(self, asks, reads_per_tick=5):
        self.asks, self.reads_per_tick, self.reads = list(asks), reads_per_tick, 0
        self.sent, self.accepted, self.closed, self.status = [], [], False, "open"

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
        elif 0 < n <= len(self.asks):
            offers = [
                {
                    "id": 500 + n,
                    "maker": "abuela",
                    "status": "open",
                    "give": {"cash": 0, "types": ["card:LAV-03"]},
                    "want": {"cash": self.asks[n - 1], "assets": [], "types": []},
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
        guard=lambda m: "cash_floor" if m.price and m.price >= 8 else None,
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

    client = FakeDealerClient(asks=[6])  # she asks 6 right after our first bid: we accept on tick 2 of 2
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=2,
    )
    assert (client.accepted, client.closed, out.status, out.price) == ([501], False, "deal", 6)


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
