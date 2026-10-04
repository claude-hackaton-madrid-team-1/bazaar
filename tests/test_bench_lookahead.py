"""BAZAAR_BENCH_POLICY=lookahead: the exact plan unless rollouts over the fitted bench model favour another matching of
pairs whose quotes cross. It only ever sends crossing pairs (the server refuses the rest), falls back to exact on any
error, and stays off by default. No network."""

from __future__ import annotations

import random

from bazaar_agent.agents.bench_lookahead import BenchLookahead, LookaheadConfig
from bazaar_agent.agents.broker import BrokerConfig, bench_config_from_env, bench_text
from bazaar_agent.agents.matcher import BrokerBook, Fee, feasible, plan_matches, quotes_from
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, agent
from tests.test_matcher import bench_buy, bench_sell
from tests.test_venue_keeper import Team, keeper

EXACT = "maximum-surplus matching (exact), midpoint price"
LOOKAHEAD = "bench lookahead: crossing matching with the most true surplus in rollouts, midpoint price"


def quotes(bench):
    return quotes_from(BrokerBook.model_validate({"bench_offers": bench})).quotes


def pairs(matches):
    return sorted((str(m.sell.id), str(m.buy.id)) for m in matches)


def test_off_by_default_and_from_the_environment(tmp_path, monkeypatch):
    assert BrokerConfig().bench_policy == "exact"
    assert bench_config_from_env(BrokerConfig(), {"BAZAAR_BENCH_POLICY": "LookAhead"}).bench_policy == "lookahead"
    assert bench_text(BrokerConfig(bench_policy="lookahead")).startswith("lookahead")
    monkeypatch.setenv("BAZAAR_BENCH_POLICY", "lookahead")
    lines: list[str] = []
    assert keeper(tmp_path, Team(), lines=lines).broker_config.bench_policy == "lookahead"
    assert any("broker bench lookahead" in line for line in lines)


def test_one_option_is_the_exact_plan():
    qs = quotes([bench_sell("b1-0", 30), bench_buy("b1-1", 60)])
    exact = plan_matches(qs, Fee(), 15)
    assert pairs(BenchLookahead().plan(qs, Fee(), 100, exact, expires=116)) == pairs(exact)


def test_it_only_ever_sends_pairs_whose_quotes_cross():
    """Random books, read tick after tick: every pair the lookahead sends crosses by quote, no trader twice."""
    rng = random.Random(3)
    fee = Fee()
    for book_no in range(25):
        planner = BenchLookahead(LookaheadConfig(samples=8), seed=book_no)
        offers = {}
        for tick in range(16):
            for k in range(rng.randint(0, 2)):
                oid = f"b9-{tick}{k}"
                offers[oid] = (
                    bench_sell(oid, rng.randint(30, 120)) if rng.random() < 0.5 else bench_buy(oid, rng.randint(25, 95))
                )
            qs = quotes(list(offers.values()))
            exact = plan_matches(qs, fee, 15)
            plan = planner.plan(qs, fee, 200 + tick, exact, expires=216)
            used = [x for m in plan for x in (m.sell.id, m.buy.id)]
            assert len(used) == len(set(used))
            for m in plan:
                assert feasible(m.sell, m.buy, fee) and m.sell.price <= m.price <= m.buy.price
                offers.pop(str(m.sell.id)), offers.pop(str(m.buy.id))


def test_session_8_holds_the_cheap_seller_for_the_bidders_about_to_arrive():
    """Run b137 (session 8, recorded in bench_books; synthetic quotes) as our book showed it up to tick 1783: two cheap
    sellers (27, 32) arrive with four low bidders while four sellers sit far above. The exact plan crosses both; the
    lookahead keeps one cheap seller for the higher bids still to come (the book had 10 buyers a side, 8 seen). A
    small expected edge: 4 of seeds 0-4 make this call."""
    paths = {  # id: (side, first tick, quotes) as recorded, after our exact matches left the book
        "b137-14": ("sell", 1775, [71, 65]),
        "b137-10": ("sell", 1776, [107, 104, 100, 96, 93]),
        "b137-13": ("sell", 1776, [129, 123, 118, 112, 106, 101]),
        "b137-11": ("sell", 1777, [115, 111, 106, 101]),
        "b137-15": ("sell", 1778, [100, 90]),
        "b137-4": ("buy", 1778, [42, 54]),
        "b137-17": ("sell", 1779, [54]),  # matched by us with b137-0 at 1779
        "b137-0": ("buy", 1779, [90]),
        "b137-1": ("buy", 1780, [50, 55, 60]),
        "b137-3": ("buy", 1780, [55, 58, 62, 65]),
        "b137-5": ("buy", 1782, [46, 46]),
        "b137-8": ("buy", 1782, [30, 32]),
        "b137-7": ("buy", 1783, [36]),
        "b137-12": ("sell", 1783, [32]),
        "b137-18": ("sell", 1783, [27]),
    }
    planner, fee = BenchLookahead(seed=0), Fee()
    plan = []
    for tick in range(1775, 1784):
        book = []
        for oid, (side, first, qs) in paths.items():
            if first <= tick < first + len(qs):
                q = qs[tick - first]
                book.append(bench_sell(oid, q) if side == "sell" else bench_buy(oid, q))
        qs_now = quotes(book)
        exact = plan_matches(qs_now, fee, 15)
        plan = planner.plan(qs_now, fee, tick, exact, expires=1790)
    assert pairs(exact) == [("b137-12", "b137-5"), ("b137-18", "b137-3")]
    assert pairs(plan) == [("b137-18", "b137-3")]


def test_the_broker_sends_the_lookahead_plan_and_labels_it(tmp_path, monkeypatch):
    broker = FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 90), bench_sell("b7-2", 40)])
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy="lookahead", pace_s=0.0)
    qs = {str(q.id): q for q in quotes(broker.bench)}
    from bazaar_agent.agents.matcher import Match

    monkeypatch.setattr(a.lookahead, "plan", lambda *args, **kw: [Match(qs["b7-2"], qs["b7-1"], 65, 0)])
    a.on_tick(clock(tick=5))
    assert broker.sent == [("b7-2", "b7-1", 65)]
    assert [d["reason"] for d in rows(tmp_path) if "reason" in d][0] == LOOKAHEAD


def test_a_failing_lookahead_sends_the_exact_plan(tmp_path, monkeypatch):
    lines: list[str] = []
    broker = FakeBroker(bench=[bench_sell("b7-0", 30), bench_buy("b7-1", 90)])
    a = agent(tmp_path, broker, live=True, lines=lines, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy="lookahead", pace_s=0.0)

    def boom(*args, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(a.lookahead, "plan", boom)
    a.on_tick(clock(tick=5))
    assert broker.sent == [("b7-0", "b7-1", 60)]
    assert [d["reason"] for d in rows(tmp_path) if "reason" in d][0] == EXACT
    assert any("bench lookahead failed (RuntimeError); exact matching this tick" in line for line in lines)
