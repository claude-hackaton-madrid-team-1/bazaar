"""The win-rate bench policy (B1) on hand-built Market Test books: what it observes and what it may send."""

from __future__ import annotations

from bazaar_agent.agents.bench_winrate import NORMAL_PRIOR, WinRatePolicy, _cross, _matchings
from bazaar_agent.agents.matcher import Fee


def offer(oid: str, side: str, quote: int) -> dict:
    if side == "sell":
        return {"id": oid, "give": {"cash": 0, "assets": [{"kind": "card", "ref": "BENCH"}]}, "want": {"cash": quote}}
    return {"id": oid, "give": {"cash": quote}, "want": {"cash": 0, "types": ["card:BENCH"]}}


def book(tick: int, *offers: dict, fee_bps: int = 0) -> dict:
    return {"tick": tick, "bench_offers": list(offers), "offers": [], "fee_bps": fee_bps, "fee_per_card": 0}


def test_no_crossing_pair_sends_nothing():
    policy = WinRatePolicy(seed=1)
    assert policy(book(30, offer("b1-0", "sell", 60), offer("b1-1", "buy", 40))) == []


def test_every_sent_pair_crosses_at_the_quotes_with_the_fee():
    for seed in range(20):
        policy = WinRatePolicy(seed=seed, samples=8)
        b = book(
            30,
            offer("b1-0", "sell", 30),
            offer("b1-2", "sell", 45),
            offer("b1-1", "buy", 50),
            offer("b1-3", "buy", 32),
            fee_bps=500,
        )
        sent = policy(b)
        quotes = {"b1-0": 30, "b1-2": 45, "b1-1": 50, "b1-3": 32}
        for s, by, price in sent:
            fee = Fee(500).of(price)
            assert quotes[s] <= price and price + fee <= quotes[by]
        assert len({i for s, by, _ in sent for i in (s, by)}) == 2 * len(sent)


def test_the_stall_plan_stops_at_the_first_pair_that_does_not_cross():
    asks, bids = [(30, "s1"), (45, "s2")], [(50, "b1"), (32, "b2")]
    assert _cross(asks, bids, Fee()) == [("s1", "b1")]  # 45 > 32: the stall stops, two pairs were possible
    matchings = _matchings([("s1", "b1"), ("s1", "b2"), ("s2", "b1")], cap=10)
    assert [("s1", "b2"), ("s2", "b1")] in matchings and [] in matchings and len(matchings[0]) == 2


def test_it_remembers_who_left_resets_on_a_new_run_and_forgets_a_refused_match():
    policy = WinRatePolicy(seed=3, samples=8)
    policy(book(30, offer("b1-0", "sell", 60), offer("b1-1", "buy", 40)))
    policy(book(31, offer("b1-1", "buy", 41)))
    assert policy.seen["b1-0"].gone and not policy.seen["b1-1"].gone
    assert policy.seen["b1-1"].quotes == {0: 40, 1: 41}
    policy.ours = [("b1-1", "b1-9")]
    policy(book(32, offer("b1-1", "buy", 42)))
    assert policy.ours == []  # b1-1 is still in the book: the match was refused
    policy(book(120, offer("b2-0", "sell", 30)))
    assert policy.run == 2 and policy.start == 120 and list(policy.seen) == ["b2-0"]


def test_with_the_zero_loss_curve_and_a_large_edge_it_plays_the_stall():
    policy = WinRatePolicy(NORMAL_PRIOR, seed=4, samples=16, min_edge=1.01, loss_curve="zero")
    sent = policy(book(30, offer("b1-0", "sell", 30), offer("b1-1", "buy", 50)))
    assert [(s, b) for s, b, _ in sent] == [("b1-0", "b1-1")] and policy.deviations == 0


def test_the_run_comes_from_the_offers_run_field_and_a_refusal_can_be_reported():
    policy = WinRatePolicy(seed=5, samples=8)
    first = offer("x-0", "sell", 30) | {"run": 7}
    policy(book(10, first, offer("x-1", "buy", 50) | {"run": 7}))
    assert policy.run == 7 and policy.start == 10
    policy.ours = [("x-0", "x-1")]
    policy.refused("x-0", "x-1")
    assert policy.ours == []


def test_begin_pins_the_runs_first_tick_and_malformed_offers_are_skipped():
    policy = WinRatePolicy(seed=6, samples=8)
    policy.begin(3, 100)
    bad = {"id": "b3-5", "give": {"cash": "lots"}, "want": {}}
    policy(book(102, offer("b3-0", "sell", 30), bad, {"nothing": True}))
    assert policy.start == 100 and policy.start_known and policy.seen["b3-0"].arrive == 2 and policy.skipped == 2


def test_the_shadow_stall_breaks_equal_quotes_in_book_order():
    from bazaar_agent.agents.bench_winrate import _stall_step, _Trader

    late_low_slot = _Trader("b1-2", 2, "sell", 30, 40, 3, 5, 0.0)
    early_high_slot = _Trader("b1-4", 4, "sell", 30, 40, 1, 5, 0.0)
    buyer = _Trader("b1-1", 1, "buy", 60, 45, 0, 9, 0.0)
    assert _stall_step([early_high_slot, late_low_slot, buyer], set(), 3, Fee()) == [("b1-2", "b1-1")]


def test_plain_ids_keep_one_session_an_older_run_never_wins_and_whole_float_quotes_count():
    policy = WinRatePolicy(seed=7, samples=8)
    plain = [{"id": "12", "give": {"cash": 0}, "want": {"cash": 30.0}}, {"id": "13", "give": {"cash": 50}}]
    policy(book(10, *plain))
    policy(book(11, *plain))
    assert policy.run == 0 and policy.start == 10 and policy.seen["12"].quotes == {0: 30, 1: 30}
    policy.begin(3, 100)
    policy(book(101, offer("b2-0", "sell", 30), offer("b3-1", "buy", 50)))
    assert policy.run == 3 and policy.start == 100 and set(policy.seen) == {"b3-1"}
