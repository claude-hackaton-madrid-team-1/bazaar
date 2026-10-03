import json
import random
from pathlib import Path

import pytest

from bazaar_agent.agents.dealer_sell import (
    MIRROR,
    AskPlan,
    Sale,
    ask_plan_for,
    decide_sell,
    mirrored_conversation,
    sell_floor,
)
from bazaar_agent.ladder import floor_table, from_rows
from bazaar_agent.ladder_replay import backtest, draw, fit, play

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "dealer_threads.json"


def sale(start=16, step=1, floor=12, asks=()):
    s = Sale(AskPlan(start, step, floor))
    for a in asks:
        s.record(a)
    return s


def test_opens_with_the_start_ask_and_comes_down_one_distinct_step_at_a_time():
    s = sale()
    move = decide_sell(s, None, None, False)
    assert (move.kind, move.price, move.reason) == ("bid", 16, "small distinct step down")
    s.record(16)
    assert decide_sell(s, 12, 1, False).price == 15  # her bid 12 stands; we come down to 15


def test_never_asks_below_the_floor_and_walks_when_nothing_is_left():
    s = sale(start=14, floor=13, asks=(14, 13))
    move = decide_sell(s, 11, 7, False)
    assert move.kind == "walk" and "no lower ask left above our floor" in move.reason


def test_a_final_bid_below_our_floor_walks_and_one_inside_it_is_taken():
    assert decide_sell(sale(asks=(16,)), 11, 9, True).kind == "walk"
    taken = decide_sell(sale(asks=(16,)), 13, 9, True)
    assert (taken.kind, taken.price) == ("accept", 13)


def test_never_takes_its_opening_bid_until_it_raised_it():
    s = sale(start=20, step=2, floor=10, asks=(20,))
    first = decide_sell(s, 18, 1, False)  # its opening bid meets our next ask (18): counter above it instead
    assert (first.kind, first.price) == ("bid", 19)
    s.record(19)
    assert decide_sell(s, 19, 2, False).kind == "accept"  # it came up to our 19: a negotiated sale
    no_room = sale(asks=(16, 15, 14))
    assert decide_sell(no_room, 13, 3, False).kind == "accept"  # no whole price left between 14 and 13


def test_plans_and_floors():
    assert sell_floor(13.2, 1.0) == 14 and sell_floor(None, 1.0) == 1
    with pytest.raises(ValueError):
        AskPlan(10, 1, 12)  # the floor above the start
    assert AskPlan(16, 1, 12).mirrored().start == MIRROR - 16


@pytest.fixture(scope="module")
def real():
    return from_rows(json.loads(FIXTURE.read_text())["rows"])


def test_an_ask_plan_from_abuelas_uncommon_sales(real):
    row = next(r for r in floor_table(real) if (r.dealer, r.price_class, r.opening) == ("abuela", "sell", 12))
    plan = ask_plan_for(row, 1).plan
    assert plan is not None and (MIRROR - plan.start, MIRROR - plan.max_price) == (16, 13)  # never its opening 12
    floored = ask_plan_for(row, 14).plan
    assert floored is not None and MIRROR - floored.max_price == 14  # never below our own floor
    assert ask_plan_for(row, 99).plan is None  # our floor above what it ever paid


def test_sales_replay_through_the_buy_machinery_without_repeats(real):
    mirrored = [mirrored_conversation(c) for c in real if c.side == "sell"]
    model = fit(mirrored, "abuela", "sell", MIRROR - 12)
    plan = AskPlan(16, 1, 12).mirrored()
    summary = backtest(plan, model, runs=500, seed=1)
    assert summary.repeated == 0 and summary.deal_rate == 1.0 and summary.mean_share >= 0.8
    rng = random.Random(2)
    for _ in range(200):
        r = play(plan, draw(model, rng))
        assert r.price is None or MIRROR - r.price >= 12  # never sold below the floor


class FakeBuyingDealer:
    """A dealer that buys asset 437: opens at `opening`, raises 1 per ask we send, takes an ask <= limit."""

    def __init__(self, opening=12, limit=14, want=437, final_at=None):
        self.opening, self.limit, self.want, self.final_at = opening, limit, want, final_at
        self.asks, self.accepted, self.closed, self.status = [], [], False, "open"
        self.reads = 0

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.reads // 3, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer, topic):
        assert topic == {"sell": {"assets": [437]}}
        return {"id": 91}

    def thread(self, tid):
        if self.accepted:
            self.status = "deal"
        bid = min(self.limit, self.opening + len(self.asks))
        final = self.final_at is not None and len(self.asks) >= self.final_at
        offer = {
            "id": 700 + len(self.asks),
            "maker": "chato",
            "status": "open",
            "final": final,
            "give": {"cash": bid},
            "want": {"assets": [{"id": self.want}]},
        }
        return {"status": self.status, "standing_offers": [] if self.status != "open" else [offer]}

    def say(self, tid, text, price):
        self.asks.append(price)
        if price <= self.limit:
            self.status = "deal"

    def accept(self, offer_id):
        self.accepted.append(offer_id)

    def close_thread(self, tid):
        self.closed = True


def sell(client, plan=None, **kw):
    from bazaar_agent.agents.dealer_sell import negotiate_sell

    plan = plan or AskPlan(16, 1, 12)
    return negotiate_sell(client, "chato", 437, plan, log=lambda _: None, sleep=lambda _: None, **kw)


def test_a_sale_closes_at_the_dealers_limit():
    client = FakeBuyingDealer(opening=12, limit=14)
    out = sell(client)
    # after our 15 its bid rose to 14, which meets our next ask: we take its 14, its limit, a round sooner
    assert (out.status, out.price, client.asks, len(client.accepted)) == ("deal", 14, [16, 15], 1)


def test_a_guardrail_denial_walks():
    client = FakeBuyingDealer()
    out = sell(client, guard=lambda move: "sell price below your_value")
    assert (out.status, client.asks, client.closed) == ("walked", [], True)


def test_an_offer_for_another_asset_is_never_accepted():
    client = FakeBuyingDealer(opening=15, limit=12, want=999)  # a bid of 15 that wants a different card
    out = sell(client, plan=AskPlan(16, 1, 14))
    assert client.accepted == [] and out.status in ("walked", "timeout")


def test_a_final_bid_below_our_floor_walks():
    client = FakeBuyingDealer(opening=10, limit=11, final_at=2)
    out = sell(client)
    assert out.status == "walked" and client.accepted == [] and min(client.asks) >= 12


def test_reasons_speak_in_real_prices():
    move = decide_sell(sale(asks=(16, 15, 14)), 13, 3, False)
    assert move.reason == "no room left between our ask 14 and its bid 13"
