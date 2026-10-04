"""BAZAAR_BENCH_POLICY=lookahead_safe / lookahead_bold (`agents/bench_posterior.py`): the bench pairs most likely to
end the session above the stall, from sampled hidden limits. The stall's own pairs unless the samples say otherwise;
never a pair whose quotes do not cross. No network."""

import importlib.util
import sys

import pytest

from bazaar_agent.agents import bench_posterior as bl
from bazaar_agent.agents.bench_posterior import PosteriorPolicy, PosteriorPrior, Seen, candidate_sets, points, posterior
from bazaar_agent.agents.broker import BrokerConfig, bench_config_from_env, bench_text
from bazaar_agent.config import REPO_ROOT
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, agent
from tests.test_matcher import bench_buy, bench_sell


def offer(oid, side, quote, end=116):
    """A bench offer as the real book shows it: `expires_tick` is the session's end (b120: 1706 = start + 16)."""
    o = bench_sell(oid, quote) if side == "sell" else bench_buy(oid, quote)
    return {**o, "expires_tick": end}


def book(tick, *offers):
    return {"tick": tick, "bench_offers": list(offers)}


# ---------------------------------------------------------------- the environment


@pytest.mark.parametrize("value, policy", [("lookahead_safe", "lookahead_safe"), ("LookAhead_Bold", "lookahead_bold")])
def test_the_posterior_planner_is_a_bench_policy_of_the_environment(value, policy):
    config = bench_config_from_env(BrokerConfig(), {"BAZAAR_BENCH_POLICY": value})
    assert config.bench_policy == policy
    assert bench_text(config).startswith(policy)


def test_today_stays_exact_without_the_variable():
    assert bench_config_from_env(BrokerConfig(), {}).bench_policy == "exact"


# ---------------------------------------------------------------- the planner


def test_a_quiet_book_sends_nothing():
    pol = PosteriorPolicy(samples=8)
    assert pol(book(101, offer("b9-10", "sell", 80), offer("b9-0", "buy", 50))) == []


def test_on_the_last_tick_the_best_bid_takes_the_only_ask_that_crosses():
    """Nothing comes after the last tick: the 80 bid is worth more than the 45 one in every sample."""
    pol = PosteriorPolicy(samples=16)
    plan = pol(
        book(
            115,
            offer("b9-10", "sell", 30),
            offer("b9-11", "sell", 90),
            offer("b9-0", "buy", 80),
            offer("b9-1", "buy", 45),
        )
    )
    assert plan == [("b9-10", "b9-0", 55)]
    assert pol.last_choice["b9"]["deviates"] is False


def test_the_samples_pick_the_set_and_a_small_edge_keeps_the_stalls(monkeypatch):
    bench = book(
        110, offer("b9-10", "sell", 30), offer("b9-11", "sell", 50), offer("b9-0", "buy", 80), offer("b9-1", "buy", 45)
    )
    stall = ((("b9-10",), ("b9-0",)), [("b9-10", "b9-0", 55)])
    both = ((("b9-10", "b9-11"), ("b9-0", "b9-1")), [("b9-10", "b9-1", 37), ("b9-11", "b9-0", 65)])
    for edge, (_, want_plan) in ((0.05, stall), (0.2, both)):
        pol = PosteriorPolicy(samples=4)

        def fake(seen, rel, keys, edge=edge, want=both[0]):
            return [0.5 + (edge if (tuple(sorted(k[0])), tuple(sorted(k[1]))) == want else 0.0) for k in keys]

        monkeypatch.setattr(pol, "score", fake)
        pol.min_edge = 0.1
        assert sorted(pol(bench)) == sorted(want_plan), edge


def test_every_pair_sent_crosses_by_quote():
    """Over whole simulated sessions: the server refuses a pair whose quotes do not cross (t1692, bad_match)."""
    from bazaar_sim import bench

    for seed in range(6):
        pol = PosteriorPolicy(samples=12)
        r = bench.simulate(_stamped(pol), bench.NORMAL.with_ticks(16), seed)
        assert r.refused == {}, (seed, r.refused)


def _stamped(pol):
    def f(b):
        for o in b.get("bench_offers") or []:
            o["expires_tick"] = 16
        return pol(b)

    return f


def test_candidates_are_the_sets_that_cross_together():
    asks, bids = [("s1", 30), ("s2", 70)], [("b1", 76), ("b2", 40)]
    cands = {(tuple(sorted(a)), tuple(sorted(b))) for a, b in candidate_sets(asks, bids, 64)}
    assert ((), ()) in cands
    assert (("s1", "s2"), ("b1", "b2")) in cands  # 30 x 40 and 70 x 76: the stall's zip makes only one pair
    assert (("s2",), ("b2",)) not in cands  # 70 > 40


def test_the_posterior_reads_firmness_and_lives_off_the_path():
    prior = PosteriorPrior()
    firm = posterior(Seen("b1-10", "sell", 2, [80, 80, 80]), prior, now=6, ticks=16)
    assert {relax for _, _, _, relax in firm} == {0.0}  # a flat path over three ticks is firm
    assert all(limit <= 80 for _, limit, _, _ in firm)
    gone = posterior(Seen("b1-0", "buy", 2, [40, 44, 48]), prior, now=6, ticks=16)
    assert {life for _, _, life, _ in gone} == {3}  # it left unmatched after three ticks
    assert all(limit >= 48 for _, limit, _, _ in gone)
    live = posterior(Seen("b1-1", "buy", 4, [40, 44, 48]), prior, now=6, ticks=16)
    assert min(life for _, _, life, _ in live) >= 3 and max(life for _, _, life, _ in live) > 3


def test_the_points_rule():
    assert points(10, 9) == 1.0 and points(9, 9) == 0.5
    assert points(8, 10) == pytest.approx(0.4) and points(8, 10, below=0.0) == 0.0


def test_a_trader_still_in_the_book_after_our_match_is_open_again():
    pol = PosteriorPolicy(samples=8)
    first = book(115, offer("b9-10", "sell", 30), offer("b9-0", "buy", 80))
    assert pol(first) == [("b9-10", "b9-0", 55)]
    pol.observe("b9", [offer("b9-10", "sell", 30), offer("b9-0", "buy", 80)], 16)
    assert pol.runs["b9"]["b9-10"].matched_at is None


# ---------------------------------------------------------------- the broker


def test_the_broker_sends_the_lookaheads_pairs_and_says_why(tmp_path):
    broker = FakeBroker(
        bench=[{**bench_sell("b7-10", 30), "expires_tick": 115}, {**bench_buy("b7-0", 80), "expires_tick": 115}]
    )
    a = agent(tmp_path, broker, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy="lookahead_safe")
    a.on_tick(clock(tick=114))  # the session's last tick: the stall's pair
    decisions = rows(tmp_path)
    assert [d["move"] for d in decisions] == [{"sell": "b7-10", "buy": "b7-0", "price": 55}]
    assert decisions[0]["reason"].startswith("bench posterior")


def test_a_failing_lookahead_falls_back_to_todays_matching(tmp_path, monkeypatch):
    lines: list[str] = []
    broker = FakeBroker(bench=[bench_sell("b7-10", 30), bench_buy("b7-0", 80)])
    a = agent(tmp_path, broker, live=True, allow_venue_open=True, lines=lines)
    a.config = BrokerConfig(bench_policy="lookahead_safe")

    def boom(self, book):
        raise RuntimeError("model")

    monkeypatch.setattr(bl.PosteriorPolicy, "__call__", boom)
    a.on_tick(clock(tick=10))
    assert broker.sent == [("b7-10", "b7-0", 55)]
    assert any("bench posterior failed (RuntimeError); exact matching this tick" in line for line in lines)


# ---------------------------------------------------------------- the proof script


def test_the_proof_script_runs_and_the_lookahead_beats_the_stall_on_average(capsys):
    spec = importlib.util.spec_from_file_location(
        "bench_posterior_proof", REPO_ROOT / "scripts" / "bench_posterior_proof.py"
    )
    assert spec is not None and spec.loader is not None
    proof = importlib.util.module_from_spec(spec)
    sys.modules["bench_posterior_proof"] = proof
    spec.loader.exec_module(proof)
    rows_ = proof.run(seeds=12, samples=24, worlds=["cal_normal20"], processes=1)
    by = {r.policy: r for r in rows_}
    assert by["exact"].margin == 0 and by["exact"].points == 0.5
    assert by["lookahead_safe"].books == 12
    assert "| cal_normal20 | lookahead_safe |" in proof.markdown(rows_)
