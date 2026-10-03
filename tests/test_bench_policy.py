"""BAZAAR_BENCH_POLICY: the broker matches the Market Test exactly (today) unless it says `edge`, and the edge's own
pairs go out only past its guard margin. The proof script runs on the simulator's bench. No network."""

import importlib.util
import sys

import pytest

from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, edge_plan
from bazaar_agent.agents.bench_model import PRIORS
from bazaar_agent.agents.broker import BrokerConfig, bench_config_from_env
from bazaar_agent.agents.matcher import BrokerBook, Fee, quotes_from
from bazaar_agent.config import REPO_ROOT
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, agent
from tests.test_matcher import bench_buy, bench_sell
from tests.test_venue_keeper import Team, keeper

_spec = importlib.util.spec_from_file_location("bench_edge_proof", REPO_ROOT / "scripts" / "bench_edge_proof.py")
assert _spec is not None and _spec.loader is not None
proof = importlib.util.module_from_spec(_spec)
sys.modules["bench_edge_proof"] = proof  # dataclasses look their module up while the class is built
_spec.loader.exec_module(proof)

EDGE = "bench edge: maximum estimated true surplus (limit bands from quotes), midpoint price"
EXACT = "maximum-surplus matching (exact), midpoint price"


def two_way_bench(low_bid=45):
    """By quote one pair (30 × 80); by the limits the asks and bids are shaded from, two pairs are worth more:
    est. 80.3 against 69.6 with a 45 bid (10.6 over exact), 79.1 with a 44 bid (9.45: under the margin)."""
    return [bench_sell("b7-10", 30), bench_sell("b7-11", 50), bench_buy("b7-0", 80), bench_buy("b7-1", low_bid)]


def plan_for(bench, config=None):
    quotes = quotes_from(BrokerBook.model_validate({"bench_offers": bench})).quotes
    return edge_plan(BenchEdge(PRIORS["normal"], config or EdgeConfig()), quotes, Fee(), 100, 15)


def pairs(plan):
    return [(m.sell.id, m.buy.id, m.price) for m in plan.matches]


# ---------------------------------------------------------------- the guard


def test_the_edge_sends_its_own_pairs_only_when_they_beat_the_exact_plan_by_the_margin():
    picked = plan_for(two_way_bench(45))
    assert picked.edge and round(picked.gain, 1) == 10.6
    assert pairs(picked) == [("b7-11", "b7-0", 65), ("b7-10", "b7-1", 37)]
    kept = plan_for(two_way_bench(44))
    assert not kept.edge and round(kept.gain, 2) == 9.45
    assert pairs(kept) == [("b7-10", "b7-0", 55)]  # the exact plan: the stall's traders


def test_the_guard_margin_is_a_setting():
    assert plan_for(two_way_bench(44), EdgeConfig(guard_margin=5.0)).edge
    assert not plan_for(two_way_bench(45), EdgeConfig(guard_margin=20.0)).edge


def test_the_edge_never_sends_fewer_pairs_than_the_exact_plan(monkeypatch):
    edge = BenchEdge(PRIORS["normal"], EdgeConfig(guard_margin=float("-inf")))
    monkeypatch.setattr(edge, "plan", lambda *a, **k: [])
    quotes = quotes_from(BrokerBook.model_validate({"bench_offers": two_way_bench()})).quotes
    picked = edge_plan(edge, quotes, Fee(), 100, 15)
    assert not picked.edge and len(picked.matches) == 1


# ---------------------------------------------------------------- the switch


@pytest.mark.parametrize(
    ("value", "policy"),
    [(None, "exact"), ("", "exact"), ("edge", "edge"), (" EDGE ", "edge"), ("Exact", "exact")],
)
def test_bench_policy_comes_from_the_environment(value, policy):
    env = {} if value is None else {"BAZAAR_BENCH_POLICY": value}
    assert bench_config_from_env(BrokerConfig(pace_s=0.2), env).bench_policy == policy
    assert bench_config_from_env(BrokerConfig(pace_s=0.2), env).pace_s == 0.2


def test_an_unknown_bench_policy_is_ignored_loudly_and_today_stays():
    lines: list[str] = []
    config = bench_config_from_env(BrokerConfig(), {"BAZAAR_BENCH_POLICY": "edgy" + "x" * 50}, lines.append)
    assert config.bench_policy == "exact"
    assert len(lines) == 1 and "IGNORED BAZAAR_BENCH_POLICY" in lines[0] and "x" * 30 not in lines[0]


def test_the_keeper_takes_the_policy_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("BAZAAR_BENCH_POLICY", raising=False)
    assert keeper(tmp_path, Team()).broker_config.bench_policy == "exact"
    monkeypatch.setenv("BAZAAR_BENCH_POLICY", "edge")
    assert keeper(tmp_path, Team()).broker_config.bench_policy == "edge"
    monkeypatch.setenv("BAZAAR_BENCH_POLICY", "bogus")
    lines: list[str] = []
    assert keeper(tmp_path, Team(), lines=lines).broker_config.bench_policy == "exact"
    assert any("IGNORED BAZAAR_BENCH_POLICY" in line for line in lines)


# ---------------------------------------------------------------- the broker


def bench_broker(tmp_path, policy, lines=None):
    broker = FakeBroker(bench=two_way_bench(45))
    a = agent(tmp_path, broker, lines=lines, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy=policy)
    return a, broker


def test_by_default_the_broker_matches_the_bench_as_today(tmp_path):
    a, _ = bench_broker(tmp_path, "exact")
    a.on_tick(clock())
    decisions = rows(tmp_path)
    assert [d["move"] for d in decisions] == [{"sell": "b7-10", "buy": "b7-0", "price": 55}]
    assert [d["reason"] for d in decisions] == [EXACT]


def test_with_the_edge_the_broker_sends_the_edges_pairs_and_says_why(tmp_path):
    lines: list[str] = []
    a, _ = bench_broker(tmp_path, "edge", lines)
    a.on_tick(clock())
    decisions = rows(tmp_path)
    assert [d["move"] for d in decisions] == [
        {"sell": "b7-11", "buy": "b7-0", "price": 65},
        {"sell": "b7-10", "buy": "b7-1", "price": 37},
    ]
    assert {d["reason"] for d in decisions} == {EDGE}
    assert any("bench edge over exact by 10.6" in line for line in lines)


def test_a_bench_run_that_leaves_the_book_is_forgotten_by_the_edge(tmp_path):
    a, broker = bench_broker(tmp_path, "edge")
    a.on_tick(clock(tick=10))
    assert set(a.edge.models) == {"b7-10", "b7-11", "b7-0", "b7-1"}
    broker.bench = []
    a.on_tick(clock(tick=11))
    assert a.edge.models == {} and a.edge.first_tick == {}


# ---------------------------------------------------------------- the proof on the simulator's bench


def test_on_the_simulators_books_exact_is_the_stall_and_nothing_is_refused():
    for seed in range(30):
        book = proof.one_book(("normal", "default", seed))
        assert book["exact"][4] == book["stall"][4] == book["exact"][5]  # exact realises what the stall does
        assert all(book[p][3] == 0 for p in proof.POLICIES)  # no policy sends a pair the quote rule refuses


def test_the_proof_table_has_a_row_per_policy():
    rows_ = proof.rows_for("hard", "default", 20, map_pool())
    assert [r.policy for r in rows_] == list(proof.POLICIES)
    assert next(r for r in rows_ if r.policy == "stall").below_stall == 0.0
    assert "| hard | default | edge | 20 |" in proof.markdown(rows_)


def map_pool():
    class Serial:  # the script's Pool, in this process
        def map(self, fn, items, chunksize=1):
            return [fn(i) for i in items]

    return Serial()
