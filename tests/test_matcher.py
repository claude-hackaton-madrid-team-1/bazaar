"""The broker's matcher: exact maximum-surplus matching, anti-wash, never ours, prices inside the quotes."""

import itertools
import random

import pytest

from bazaar_agent.agents.matcher import (
    BrokerBook,
    Fee,
    Quote,
    best_matches,
    feasible,
    match_price,
    max_weight_assignment,
    plan_matches,
    quotes_from,
)


def sell(oid, ask, maker, item="card:LAV-03"):
    return Quote(oid, "sell", item, ask, maker)


def buy(oid, bid, maker, item="card:LAV-03"):
    return Quote(oid, "buy", item, bid, maker)


def book_sell(oid, ref, ask, maker):
    """A public sell as GET /api/broker/book shows it: one card for cash, maker as a pseudonym."""
    return {
        "id": oid,
        "maker": maker,
        "status": "open",
        "thread": None,
        "give": {"cash": 0, "assets": [{"id": 900 + oid, "kind": "card", "ref": ref}], "types": []},
        "want": {"cash": ask, "assets": [], "types": []},
    }


def book_buy(oid, ref, bid, maker):
    return {
        "id": oid,
        "maker": maker,
        "status": "open",
        "thread": None,
        "give": {"cash": bid, "assets": [], "types": []},
        "want": {"cash": 0, "assets": [], "types": [f"card:{ref}"]},
    }


def bench_sell(oid, ask):
    """A bench seller (starter_broker.py: `want.cash`), as the simulator also shows it."""
    return {"id": oid, "give": {"assets": [{"kind": "card", "ref": "BENCH"}]}, "want": {"cash": ask}}


def bench_buy(oid, bid):
    return {"id": oid, "give": {"cash": bid}, "want": {"types": ["card:BENCH"]}}


def greedy_by_price(sells, buys, fee):
    """The starter broker's rule: lowest ask against highest bid, stop at the first pair that does not cross."""
    out = []
    for s, b in zip(sorted(sells, key=lambda q: q.price), sorted(buys, key=lambda q: -q.price), strict=False):
        if not feasible(s, b, fee):
            break
        out.append((s, b))
    return out


def brute_force(sells, buys, fee):
    """(best surplus, most pairs at that surplus) over every matching: the ground truth for small books."""
    best = (0, 0)
    for k in range(min(len(sells), len(buys)) + 1):
        for chosen in itertools.combinations(sells, k):
            for partners in itertools.permutations(buys, k):
                if all(feasible(s, b, fee) for s, b in zip(chosen, partners, strict=True)):
                    surplus = sum(b.price - s.price for s, b in zip(chosen, partners, strict=True))
                    best = max(best, (surplus, k))
    return best


def test_greedy_by_price_is_suboptimal_with_a_fee_and_the_exact_matcher_is_not():
    fee = Fee(per_card=2)
    sells, buys = [sell(1, 10, "a"), sell(2, 20, "b")], [buy(3, 30, "c"), buy(4, 21, "d")]
    greedy = greedy_by_price(sells, buys, fee)
    assert sum(b.price - s.price for s, b in greedy) == 20  # 10↔30, then 20 + 2 > 21 stops it
    exact = best_matches(sells, buys, fee)
    assert sum(m.surplus for m in exact) == 21 and len(exact) == 2  # 10↔21 and 20↔30
    assert {(m.sell.id, m.buy.id) for m in exact} == {(1, 4), (2, 3)}


def test_greedy_by_price_is_suboptimal_when_one_maker_is_on_both_sides():
    sells = [sell(1, 10, "x"), sell(2, 15, "a")]
    buys = [buy(3, 40, "x"), buy(4, 20, "b")]  # x's own ask and bid are the best quotes
    assert greedy_by_price(sells, buys, Fee()) == []  # x × x is a wash, so the starter rule stops at once
    exact = best_matches(sells, buys, Fee())
    assert {(m.sell.id, m.buy.id) for m in exact} == {(1, 4), (2, 3)}
    assert sum(m.surplus for m in exact) == 35


@pytest.mark.parametrize("seed", range(300))
def test_exact_matching_equals_brute_force_on_random_small_books(seed):
    rng = random.Random(seed)
    makers = ["a", "b", "c", "d"]
    sells = [sell(i, rng.randint(5, 40), rng.choice(makers)) for i in range(rng.randint(0, 4))]
    buys = [buy(10 + i, rng.randint(5, 50), rng.choice(makers)) for i in range(rng.randint(0, 4))]
    fee = Fee(bps=rng.choice([0, 300, 1000]), per_card=rng.choice([0, 1, 5]))
    plan = best_matches(sells, buys, fee)
    assert (sum(m.surplus for m in plan), len(plan)) == brute_force(sells, buys, fee)
    assert len({m.sell.id for m in plan}) == len({m.buy.id for m in plan}) == len(plan)
    for m in plan:
        assert m.sell.maker != m.buy.maker
        assert m.sell.price <= m.price and m.price + fee.of(m.price) <= m.buy.price


def test_the_assignment_solver_matches_brute_force_on_random_weights():
    rng = random.Random(7)
    for _ in range(300):
        n, m = rng.randint(1, 5), rng.randint(1, 5)
        weights = [[rng.choice([0, 0, rng.randint(1, 30)]) for _ in range(m)] for _ in range(n)]
        pairs = max_weight_assignment(weights)
        best = max(
            sum(weights[r][c] for r, c in zip(rows, cols, strict=True) if weights[r][c] > 0)
            for k in range(min(n, m) + 1)
            for rows in itertools.combinations(range(n), k)
            for cols in itertools.permutations(range(m), k)
        )
        assert sum(weights[r][c] for r, c in pairs) == best
        assert all(weights[r][c] > 0 for r, c in pairs)


def test_a_zero_surplus_pair_is_still_matched_because_true_surplus_is_at_least_the_quoted():
    plan = best_matches([sell(1, 30, "a")], [buy(2, 30, "b")], Fee())
    assert [(m.price, m.surplus) for m in plan] == [(30, 0)]


def test_the_price_is_the_midpoint_lowered_until_the_buyer_can_pay_the_fee():
    assert match_price(20, 40, Fee()) == 30
    assert match_price(20, 40, Fee(per_card=5)) == 30  # 30 + 5 <= 40
    assert match_price(20, 40, Fee(bps=1000, per_card=5)) == 30  # 30 + 3 + 5 = 38 <= 40
    assert match_price(20, 28, Fee(bps=1000, per_card=5)) == 20  # only 20 + 2 + 5 = 27 fits
    assert Fee(bps=300).of(10) == 1  # ceil(0.3): the tape's rounding, never below the simulator's round
    with pytest.raises(ValueError):
        match_price(20, 21, Fee(per_card=2))


def test_items_never_mix_and_bench_runs_never_mix():
    quotes = [
        sell(1, 10, "a", "card:LAV-03"),
        buy(2, 50, "b", "card:LAV-04"),
        sell("b1-0", 10, "b1-0", "bench:b1"),
        buy("b2-1", 50, "b2-1", "bench:b2"),
    ]
    assert plan_matches(quotes, Fee()) == []


def test_quotes_from_reads_public_offers_and_bench_runs_and_skips_odd_shapes():
    book = BrokerBook.model_validate(
        {
            "offers": [
                book_sell(1, "LAV-03", 20, "m1"),
                book_buy(2, "LAV-03", 30, "m2"),
                {**book_buy(3, "LAV-03", 30, "m3"), "want": {"cash": 5, "types": ["card:LAV-03"]}},  # mixed
                {**book_sell(4, "LAV-03", 20, "m4"), "thread": 77},  # a thread's offer, not the board's
                {"id": "junk", "give": "nope"},  # malformed
            ],
            "bench_offers": [bench_sell("b12-0", 30), bench_buy("b12-1", 45)],
            "fee_bps": 0,
            "fee_per_card": 0,
        }
    )
    got = quotes_from(book)
    assert [(q.id, q.side, q.item, q.price) for q in got.quotes] == [
        (1, "sell", "card:LAV-03", 20),
        (2, "buy", "card:LAV-03", 30),
        ("b12-0", "sell", "bench:b12", 30),
        ("b12-1", "buy", "bench:b12", 45),
    ]
    assert got.skipped == 3 and got.ours == 0


def test_never_anything_of_ours_by_offer_id_or_by_the_pseudonym_that_made_it():
    book = BrokerBook.model_validate(
        {
            "offers": [
                book_sell(1, "LAV-03", 20, "mOurs"),  # our open offer (its id is in /api/me/offers)
                book_buy(2, "LAV-03", 40, "mOurs"),  # same pseudonym: ours too, though its id is unknown
                book_buy(3, "LAV-03", 30, "mThem"),
                book_sell(4, "LAV-03", 25, "mOther"),
            ]
        }
    )
    got = quotes_from(book, our_offer_ids=[1])
    assert got.ours == 2 and {q.id for q in got.quotes} == {3, 4}
    plan = plan_matches(got.quotes, Fee())
    assert [(m.sell.id, m.buy.id, m.price) for m in plan] == [(4, 3, 27)]


def test_public_offers_can_be_left_out_and_the_bench_still_matches():
    book = BrokerBook.model_validate(
        {
            "offers": [book_sell(1, "LAV-03", 20, "m1"), book_buy(2, "LAV-03", 30, "m2")],
            "bench_offers": [
                bench_sell("b3-0", 20),
                bench_buy("b3-1", 30),
            ],
        }
    )
    plan = plan_matches(quotes_from(book, public=False).quotes, Fee())
    assert [(m.sell.id, m.buy.id) for m in plan] == [("b3-0", "b3-1")]


def test_bench_comes_first_then_surplus_and_the_limit_cuts_the_tail():
    quotes = [
        sell(1, 10, "a"),
        buy(2, 60, "b"),
        sell("b1-0", 30, "b1-0", "bench:b1"),
        buy("b1-1", 35, "b1-1", "bench:b1"),
        sell(3, 10, "c", "card:LAV-04"),
        buy(4, 20, "d", "card:LAV-04"),
    ]
    plan = plan_matches(quotes, Fee())
    assert [m.sell.id for m in plan] == ["b1-0", 1, 3]
    assert [m.sell.id for m in plan_matches(quotes, Fee(), limit=2)] == ["b1-0", 1]


def test_wash_and_fee_infeasible_pairs_are_never_feasible():
    assert not feasible(sell(1, 10, "a"), buy(2, 50, "a"), Fee())
    assert not feasible(sell(1, 10, "a"), buy(2, 50, "b", "card:OTHER"), Fee())
    assert not feasible(sell(1, 48, "a"), buy(2, 50, "b"), Fee(per_card=3))
    assert feasible(sell(1, 47, "a"), buy(2, 50, "b"), Fee(per_card=3))
