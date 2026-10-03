"""Organic market-making: venue flow off the public feed and the organic score under each reading (#13, #17)."""

from __future__ import annotations

import math

from bazaar_agent.organic import organic_fraction, organic_raw, scenario_table, venue_flows


def listed(maker: str, venue: str | None, *, sell: bool = True, thread: int | None = None) -> dict:
    give = {"assets": [{"id": 1, "ref": "LAV-01"}], "cash": 0} if sell else {"assets": [], "cash": 20}
    offer = {"maker": maker, "venue": venue, "thread": thread, "give": give, "want": {"cash": 20 if sell else 0}}
    return {"type": "offer.listed", "actor": maker, "payload": {"offer": offer, "venue": venue}}


def settled(a: str, b: str, venue: str | None, price: int = 20, fee: int = 2) -> dict:
    return {
        "type": "settlement",
        "payload": {"kind": "trade", "venue": venue, "parties": [a, b], "price": price, "fee": fee},
    }


def test_flows_count_offers_makers_trades_and_distinct_pairs_per_venue():
    events = [
        {"type": "venue.opened", "payload": {"venue": "v01", "owner": "t01"}},
        listed("t06", "rastro"),
        listed("t07", "rastro", sell=False),
        listed("t08", "v01"),
        listed("t06", None, thread=4),  # a thread offer is not venue flow
        settled("t06", "t07", "rastro", 20, 3),
        settled("t07", "t06", "rastro", 30, 3),
        settled("t08", "t09", "v01"),
        settled("t01", "t09", "v01"),  # the owner's own trade never counts as organic
        settled("t06", "t09", None),  # a direct thread deal is no venue's
    ]
    flows = venue_flows(events)
    rastro, ours = flows["rastro"], flows["v01"]
    assert (rastro.listed, rastro.sells, rastro.bids, rastro.makers) == (2, 1, 1, {"t06", "t07"})
    assert (rastro.trades, rastro.volume, rastro.fees, rastro.pairs) == (2, 50, 6, {("t06", "t07"): 2})
    assert rastro.fee_share == 0.12
    assert ours.owner == "t01" and ours.listed == 1 and ours.trades == 1 and ours.pairs == {("t08", "t09"): 1}
    assert set(flows) == {"rastro", "v01"}


def test_the_raw_score_caps_each_pair_and_takes_the_square_root():
    values = {("a", "b"): 30.0, ("c", "d"): 5.0, ("e", "f"): -4.0}
    assert organic_raw(values, sqrt=False) == 35.0
    assert organic_raw(values, sqrt=False, pair_cap=10) == 15.0
    assert organic_raw(values) == math.sqrt(35.0)


def test_one_pair_when_nobody_else_has_any_scores_the_full_component():
    assert organic_fraction(3.0, []) == 1.0
    assert organic_fraction(0.0, [0.0, 0.0]) == 0.0
    assert organic_fraction(1.0, [10.0, 10.0, 10.0]) == 0.1


def test_the_square_root_and_the_cap_compress_a_leaders_lead():
    pairs = lambda n, v=10.0: {(f"a{i}", f"b{i}"): v for i in range(n)}  # noqa: E731
    table = scenario_table(pairs(1), [pairs(10)], pair_cap=10)
    assert table["linear"] == 0.2727 and table["sqrt"] == 0.7208
    big = scenario_table(pairs(1, 40.0), [pairs(4)], pair_cap=10)
    assert big["linear"] == 1.0 and big["linear, capped"] == 0.6
