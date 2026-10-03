"""The bench edge: only crossing pairs by default, one use per trader, holds and the endgame, the limit probe."""

import itertools
import random

import pytest

from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, ProbeStats
from bazaar_agent.agents.bench_model import PRIORS, BenchPrior, TraderModel
from bazaar_agent.agents.matcher import Fee, Quote


def ask(oid, price, run="b3"):
    return Quote(f"{run}-{oid}", "sell", f"bench:{run}", price, f"{run}-{oid}")


def bid(oid, price, run="b3"):
    return Quote(f"{run}-{oid}", "buy", f"bench:{run}", price, f"{run}-{oid}")


NO_FEE = Fee()


def plan(edge, quotes, tick=0, fee=NO_FEE, **kw):
    edge.observe(quotes, tick)
    return edge.plan(quotes, fee, tick, **kw)


def test_quote_mode_only_crosses_pairs_whose_quotes_cross_and_uses_each_trader_once():
    rng = random.Random(4)
    for _ in range(300):
        fee = Fee(rng.choice([0, 100, 500]), rng.choice([0, 1, 2]))
        quotes = [ask(i, rng.randint(20, 80)) for i in range(0, 8, 2)]
        quotes += [bid(i, rng.randint(20, 90)) for i in range(1, 9, 2)]
        out = plan(BenchEdge(PRIORS["normal"]), quotes, fee=fee)
        ids = [str(q.id) for m in out for q in (m.sell, m.buy)]
        assert len(ids) == len(set(ids))
        for m in out:
            assert m.sell.price <= m.price and m.price + fee.of(m.price) <= m.buy.price and m.fee == fee.of(m.price)


def test_it_crosses_as_many_pairs_as_the_quotes_allow():
    quotes = [ask(0, 30), ask(2, 50), bid(1, 60), bid(3, 40)]
    out = plan(BenchEdge(PRIORS["normal"]), quotes)
    assert {(m.sell.id, m.buy.id) for m in out} == {("b3-0", "b3-3"), ("b3-2", "b3-1")}


def test_two_runs_never_mix():
    out = plan(BenchEdge(PRIORS["normal"]), [ask(0, 30, "b3"), bid(1, 60, "b4")])
    assert out == []


def test_a_hold_threshold_waits_for_fresh_traders_and_crosses_old_or_endgame_ones():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(hold_below=0.3))
    fresh = [ask(0, 30), bid(1, 60)]
    assert plan(edge, fresh, tick=0) == []  # hazard 0.125 at age 1: hold
    assert plan(edge, fresh, tick=3) != []  # age 4: hazard 1/3
    late = BenchEdge(PRIORS["normal"], EdgeConfig(hold_below=0.3))
    late.observe(fresh, 0)
    assert late.plan(fresh, Fee(), 0, session_ticks={"b3": 1}) != []  # a one-tick session: its last tick


def test_the_cap_keeps_the_most_urgent_pairs():
    edge = BenchEdge(PRIORS["normal"])
    edge.observe([ask(0, 30), bid(1, 60)], 0)
    quotes = [ask(0, 30), bid(1, 60), ask(2, 30), bid(3, 60)]
    out = plan(edge, quotes, tick=3, limit=1)  # b3-0/b3-1 are 4 ticks old, the others fresh
    assert [(m.sell.id, m.buy.id) for m in out] == [("b3-0", "b3-1")]


def test_quote_mode_never_proposes_a_non_crossing_pair():
    assert plan(BenchEdge(PRIORS["normal"]), [ask(0, 62), bid(1, 58)]) == []


def test_the_limit_probe_proposes_a_close_non_crossing_pair_inside_both_bands():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit"))
    (m,) = plan(edge, [ask(0, 62), bid(1, 58)])
    s, b = edge.models["b3-0"], edge.models["b3-1"]
    assert s.band()[0] <= m.price <= b.band()[1]
    assert m.price > m.buy.price or m.price < m.sell.price  # outside the quotes: a probe


def test_a_refused_probe_is_repriced_then_dropped():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit", tries_per_pair=2, min_accept=0.0))
    quotes = [ask(0, 62), bid(1, 58)]
    (first,) = plan(edge, quotes)
    edge.note_sent(first, accepted=False)
    (second,) = plan(edge, quotes)
    assert second.price != first.price
    edge.note_sent(second, accepted=False)
    assert plan(edge, quotes) == []
    assert edge.refused == {("b3-0", "b3-1"): [first.price, second.price]}


def test_probing_stops_once_all_refusals_have_become_unlikely_and_one_acceptance_keeps_it_on():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit", give_up_below=0.001, give_up_after=4))
    k = 0
    while edge.probing:
        (m,) = plan(edge, [ask(10 + 2 * k, 62), bid(11 + 2 * k, 58)])
        edge.note_sent(m, accepted=False)
        k += 1
    assert 4 <= k < 20 and edge.probes.refused == k and edge.probes.all_refused < 0.001
    assert plan(edge, [ask(90, 62), bid(91, 58)]) == []
    kept = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit", give_up_below=0.001, give_up_after=4))
    (m,) = plan(kept, [ask(0, 62), bid(1, 58)])
    kept.note_sent(m, accepted=True)
    for k in range(12):
        (m,) = plan(kept, [ask(10 + 2 * k, 62), bid(11 + 2 * k, 58)])
        kept.note_sent(m, accepted=False)
    assert kept.probing and kept.probes.accepted == 1


def test_probe_stats_multiply_the_predicted_refusal_chances():
    stats = ProbeStats()
    stats.record(False, 0.5)
    stats.record(False, 0.4)
    stats.record(True, 0.3)
    assert (stats.sent, stats.refused, stats.accepted) == (3, 2, 1)
    assert stats.all_refused == pytest.approx(0.5 * 0.6)


def test_a_crossing_refusal_teaches_nothing():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit"))
    (m,) = plan(edge, [ask(0, 30), bid(1, 60)])
    edge.note_sent(m, accepted=False)
    assert edge.refused == {} and edge.probes.sent == 0


@pytest.mark.parametrize("seed", range(25))
def test_best_price_matches_a_brute_force_posterior(seed):
    rng = random.Random(seed)
    prior = BenchPrior().widened()
    s, b = TraderModel("s", "sell", 0, prior), TraderModel("b", "buy", 0, prior)
    s.observe(0, rng.randint(40, 70))
    b.observe(0, rng.randint(30, 60))
    fee = Fee(rng.choice([0, 300]), rng.choice([0, 1]))
    tried = sorted(rng.sample(range(30, 80), rng.randint(0, 3)))
    price, chance = BenchEdge._best_price(s, b, fee, tried)
    # brute force: a fine grid over (cost, value), uniform on the bands, minus what the refusals rule out
    (c0, c1), (v0, v1) = s.band(), b.band()
    grid = [
        (c0 + (c1 - c0) * (i + 0.5) / 120, v0 + (v1 - v0) * (j + 0.5) / 120) for i in range(120) for j in range(120)
    ]

    def accepts(c, v, p):
        return c <= p and v >= p + fee.of(p)

    left = [(c, v) for c, v in grid if not any(accepts(c, v, p) for p in tried)]
    if not left:
        assert chance == 0.0
        return
    brute = sum(accepts(c, v, price) for c, v in left) / len(left)
    assert chance == pytest.approx(brute, abs=0.03)
    best_brute = max(
        sum(accepts(c, v, p) for c, v in left) / len(left) for p in range(int(c0), int(v1) + 2) if p not in tried
    )
    assert chance == pytest.approx(best_brute, abs=0.03)


def test_every_pair_the_edge_plans_is_feasible_in_both_modes_for_random_sessions():
    for cross, seed in itertools.product(("quote", "limit"), range(30)):
        rng = random.Random(seed)
        edge = BenchEdge(PRIORS["hard"], EdgeConfig(cross=cross))
        for tick in range(6):
            quotes = [ask(i, rng.randint(25, 80)) for i in range(0, 12, 2)]
            quotes += [bid(i, rng.randint(30, 90)) for i in range(1, 13, 2)]
            out = plan(edge, quotes, tick=tick)
            ids = [str(q.id) for m in out for q in (m.sell, m.buy)]
            assert len(ids) == len(set(ids))
            for m in out:
                crossing = m.sell.price <= m.price and m.price + m.fee <= m.buy.price
                assert crossing or cross == "limit"
                edge.note_sent(m, accepted=crossing)


def test_a_pair_whose_offers_say_when_they_leave_is_held_until_the_first_of_them_leaves():
    from bazaar_agent.agents.bench_edge import expiries_in

    edge = BenchEdge(PRIORS["normal"])
    quotes = [ask(0, 30), bid(1, 60)]
    offers = [{"id": "b3-0", "expires_tick": 2}, {"id": "b3-1", "expires_tick": 5}, {"id": "b3-9"}]
    assert expiries_in(offers, 0) == {"b3-0": 2, "b3-1": 5}
    starts = {"b3": 0}
    edge.observe(quotes, 0, expiries_in(offers, 0))
    assert edge.plan(quotes, NO_FEE, 0, session_starts=starts) == []  # held: b3-0 stays until tick 2
    assert edge.plan(quotes, NO_FEE, 0) != []  # but never when the run's start is a guess
    edge.observe(quotes, 1, expiries_in(offers, 1))  # one tick early: the expiry's meaning is a guess
    assert [(m.sell.id, m.buy.id) for m in edge.plan(quotes, NO_FEE, 1, session_starts=starts)] == [("b3-0", "b3-1")]


def test_a_crossing_pair_in_one_run_is_never_displaced_by_a_probe_in_another():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit"))
    edge.observe([ask(0, 30, "b4"), bid(1, 60, "b4")], 0)  # older: b4's pair is the more urgent one
    quotes = [ask(0, 62), bid(1, 58), ask(0, 30, "b4"), bid(1, 60, "b4")]
    (m,) = plan(edge, quotes, tick=3, limit=1)
    assert m.sell.id == "b4-0"


def test_a_finished_run_is_forgotten():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit"))
    (m,) = plan(edge, [ask(0, 62), bid(1, 58), ask(0, 30, "b4")])
    edge.note_sent(m, accepted=False)
    edge.forget("b3")
    assert set(edge.models) == {"b4-0"} and edge.refused == {} and "b3" not in edge.first_tick


def test_with_hold_known_off_a_known_expiry_does_not_delay_a_match():
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(hold_known=False))
    edge.observe([ask(0, 30), bid(1, 60)], 0, {"b3-0": 4, "b3-1": 4})
    assert len(edge.plan([ask(0, 30), bid(1, 60)], NO_FEE, 0)) == 1


def test_a_pair_that_crosses_when_the_fee_is_rounded_down_is_never_probed():
    fee = Fee(150, 0)  # at 50: ceil 0.75 = 1, so 51 > 50 does not cross; floored, 50 ≤ 50 does
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(cross="limit", min_accept=0.0))
    assert plan(edge, [ask(0, 50), bid(1, 50)], fee=fee) == []
    assert len(plan(edge, [ask(2, 52), bid(3, 50)], fee=fee)) == 1  # a real gap is still probed
