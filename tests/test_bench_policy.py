"""BAZAAR_BENCH_POLICY: the broker matches the Market Test exactly (today) unless it says `edge`, and the edge's own
pairs go out only past its guard margin. The proof script runs on the simulator's bench. No network."""

import importlib.util
import sys

import pytest

from bazaar_agent.agents import bench_edge as be
from bazaar_agent.agents.bench_edge import BenchEdge, EdgeConfig, edge_plan
from bazaar_agent.agents.bench_model import PRIORS
from bazaar_agent.agents.broker import BrokerConfig, bench_config_from_env, bench_text
from bazaar_agent.agents.matcher import MAX_SIDE, BrokerBook, Fee, quotes_from
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.ticks import Clock
from tests.agent_fakes import clock, rows
from tests.test_broker_agent import FakeBroker, FakeTeam, agent
from tests.test_matcher import bench_buy, bench_sell, book_buy, book_sell
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
    assert len(lines) == 1 and "IGNORED BAZAAR_BENCH_POLICY (54 chars" in lines[0] and "edgy" not in lines[0]


@pytest.mark.parametrize(
    ("value", "margin"),
    [
        (None, 10.0),
        ("", 10.0),
        ("5", 5.0),
        (" 2.5 ", 2.5),
        ("0", 0.0),
        (" NONE ", float("-inf")),
        ("-inf", float("-inf")),
        ("-Infinity", float("-inf")),
        ("inf", float("inf")),
    ],
)
def test_the_guard_margin_comes_from_the_environment(value, margin):
    env = {"BAZAAR_BENCH_POLICY": "edge"} | ({} if value is None else {"BAZAAR_BENCH_GUARD_MARGIN": value})
    lines: list[str] = []
    config = bench_config_from_env(BrokerConfig(), env, lines.append)
    assert config.bench_policy == "edge" and config.bench_guard_margin == margin and lines == []


@pytest.mark.parametrize("value", ["nan", "ten", "1e", "10 P", "tk-" + "s" * 40])
def test_a_margin_that_is_not_a_number_is_ignored_by_its_length_and_ten_stays(value):
    lines: list[str] = []
    config = bench_config_from_env(BrokerConfig(), {"BAZAAR_BENCH_GUARD_MARGIN": value}, lines.append)
    assert config.bench_guard_margin == 10.0
    assert len(lines) == 1 and f"IGNORED BAZAAR_BENCH_GUARD_MARGIN ({len(value)} chars" in lines[0]
    assert value.lower() not in lines[0]


def test_84s_other_switches_are_reported_as_not_wired():
    lines: list[str] = []
    env = {"BAZAAR_BENCH_POLICY": "edge", "BAZAAR_BENCH_CROSS": "limit", "BAZAAR_BENCH_PRESET": "hard"}
    config = bench_config_from_env(BrokerConfig(), env, lines.append)
    assert config == BrokerConfig(bench_policy="edge")
    assert [line.split(":")[1].strip() for line in lines] == [
        "IGNORED BAZAAR_BENCH_CROSS",
        "IGNORED BAZAAR_BENCH_PRESET",
    ]


def test_the_bench_text_names_the_policy_and_its_guard():
    assert bench_text(BrokerConfig()) == "exact"
    assert bench_text(BrokerConfig(bench_policy="edge")) == "edge (guard margin 10 P)"
    assert bench_text(BrokerConfig(bench_policy="edge", bench_guard_margin=2.5)) == "edge (guard margin 2.5 P)"
    assert bench_text(BrokerConfig(bench_policy="edge", bench_guard_margin=float("-inf"))) == "edge (unguarded, as #84)"


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


def bench_broker(tmp_path, policy, lines=None, low_bid=45, margin=10.0):
    broker = FakeBroker(bench=two_way_bench(low_bid))
    a = agent(tmp_path, broker, lines=lines, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy=policy, bench_guard_margin=margin)
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


def test_the_edge_keeps_our_offers_out_and_the_public_matches_take_the_slots_left(tmp_path):
    broker = FakeBroker(
        offers=[
            book_sell(1, "LAV-03", 20, "mUs"),  # ours (by id), and so is the pseudonym's bid
            book_buy(2, "LAV-03", 40, "mThem"),
            book_buy(3, "LAV-03", 35, "mUs"),
            book_sell(4, "LAV-03", 25, "mOther"),
            book_sell(5, "SAL-01", 10, "mA"),
            book_buy(6, "SAL-01", 30, "mB"),
        ],
        bench=two_way_bench(45),
    )
    team = FakeTeam(offers=[{"id": 1, "maker": "t01", "status": "open"}])
    a = agent(tmp_path, broker, team, live=True, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy="edge", max_matches_per_tick=3)
    a.on_tick(clock())
    assert broker.sent == [("b7-11", "b7-0", 65), ("b7-10", "b7-1", 37), (5, 6, 20)]  # the cap binds: 2 bench + 1


def test_a_failing_edge_falls_back_to_todays_matching_for_the_tick(tmp_path):
    lines: list[str] = []
    broker = FakeBroker(
        offers=[book_sell(5, "SAL-01", 10, "mA"), book_buy(6, "SAL-01", 30, "mB")],
        bench=[bench_sell("b7-10", 10**400), bench_buy("b7-0", 80)],  # a first quote no float can hold
    )
    a = agent(tmp_path, broker, live=True, allow_venue_open=True, lines=lines)
    a.config = BrokerConfig(bench_policy="edge")
    a.on_tick(clock(tick=10))
    assert broker.sent == [(5, 6, 20)]
    broker.bench = [bench_sell("b7-10", 30), bench_buy("b7-0", 80)]  # now it crosses: its band overflows
    a.on_tick(clock(tick=11))
    assert broker.sent[-1] == ("b7-10", "b7-0", 55)  # today's matching sent it
    failed = [line for line in lines if "bench edge failed (OverflowError); exact matching this tick" in line]
    assert len(failed) == 1
    broker.bench = [bench_sell("b7-11", 40), bench_buy("b7-1", 70)]  # the models restarted: no poisoned trader left
    a.on_tick(clock(tick=12))
    assert broker.sent[-1] == ("b7-11", "b7-1", 55) and len([x for x in lines if "bench edge failed" in x]) == 1


def test_a_gain_that_is_not_a_number_keeps_the_exact_plan():
    huge = 15 * 10**307  # both estimated limits overflow to inf: their difference is nan
    picked = plan_for([bench_sell("b7-10", huge), bench_buy("b7-0", huge)])
    assert not picked.edge and picked.gain != picked.gain


def test_the_edge_plans_on_at_most_max_side_quotes_a_side(monkeypatch):
    seen: list[tuple[int, int]] = []
    assign = be._assign
    monkeypatch.setattr(be, "_assign", lambda table: seen.append((len(table), len(table[0]))) or assign(table))
    bench = [bench_sell(f"b7-{k}", 20 + k % 30) for k in range(150)]
    bench += [bench_buy(f"b7-{200 + k}", 40 + k % 30) for k in range(150)]
    assert len(plan_for(bench).matches) == 15
    assert seen and all(rows_ <= MAX_SIDE and cols <= MAX_SIDE for rows_, cols in seen)


def test_the_keeper_says_which_bench_policy_its_broker_runs(tmp_path, monkeypatch):
    from tests.test_venue_keeper import ours, snap, window

    monkeypatch.setenv("BAZAAR_BENCH_POLICY", "edge")
    monkeypatch.delenv("BAZAAR_BENCH_GUARD_MARGIN", raising=False)
    lines: list[str] = []
    k = keeper(tmp_path, Team(), lines=lines)
    assert lines == ["venue keeper: broker bench edge (guard margin 10 P)"]  # at start: what the runbooks look for
    k._key = lambda venue: "bk-test"  # type: ignore[method-assign]  # a venue listed as ours, its key in hand
    k.on_tick(Clock(tick=400, tick_seconds=60.0, next_tick_in=40.0, t_hours=6.6), snap(venues=(ours(),)), window())
    assert any("broker on for" in line and "bench edge (guard margin 10 P)" in line for line in lines)
    monkeypatch.setenv("BAZAAR_BENCH_GUARD_MARGIN", "none")
    unguarded: list[str] = []
    keeper(tmp_path, Team(), lines=unguarded)
    assert unguarded == ["venue keeper: broker bench edge (unguarded, as #84)"]
    monkeypatch.delenv("BAZAAR_BENCH_POLICY")
    quiet: list[str] = []
    keeper(tmp_path, Team(), lines=quiet)
    assert quiet == []  # exact (today) says nothing at start


@pytest.mark.parametrize(("margin", "edge_sent"), [(10.0, False), (5.0, True), (float("-inf"), True)])
def test_the_brokers_guard_margin_decides_a_close_call(tmp_path, margin, edge_sent):
    a, broker = bench_broker(tmp_path, "edge", low_bid=44, margin=margin)  # the edge is 9.45 estimated P better
    a.on_tick(clock())
    decisions = rows(tmp_path)
    if edge_sent:
        assert [d["move"]["sell"] for d in decisions] == ["b7-11", "b7-10"] and {d["reason"] for d in decisions} == {
            EDGE
        }
    else:
        assert [d["move"] for d in decisions] == [{"sell": "b7-10", "buy": "b7-0", "price": 55}]


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


def test_the_regret_table_names_the_policy_whose_worst_regret_is_smallest():
    def row(policy, points, rivals, steep, harsh):
        return proof.Row("normal", "x2", policy, 1, 0, 0, 0, 0, 0, 0, points, rivals, steep, harsh, 0)

    rows_ = [
        row("stall", 0.5, 0.5, 0.5, 0.5),
        row("exact", 0.5, 0.5, 0.5, 0.5),
        row("edge20", 0.5, 0.5, 0.5, 0.5),
        row("edge", 0.55, 0.54, 0.53, 0.50),
        row("edge5", 0.58, 0.56, 0.55, 0.49),
        row("unguarded", 0.60, 0.58, 0.56, 0.45),
    ]
    table = proof.regrets(rows_)
    assert table["unguarded"] == (0.05, 0.0125, 0.5475)  # best but for the harsh reading, where exact wins by 0.05
    assert table["edge5"][0] == 0.02 and min(table.values())[0] == 0.02
    assert proof.regret_markdown(rows_).splitlines()[2].startswith("| edge5 | 0.020 |")


def test_an_unguarded_edge_on_an_empty_bench_says_nothing(tmp_path):
    lines: list[str] = []
    broker = FakeBroker(offers=[book_sell(5, "SAL-01", 10, "mA"), book_buy(6, "SAL-01", 30, "mB")])
    a = agent(tmp_path, broker, lines=lines, allow_venue_open=True)
    a.config = BrokerConfig(bench_policy="edge", bench_guard_margin=float("-inf"))
    a.on_tick(clock())
    assert [d["move"] for d in rows(tmp_path)] == [{"sell": 5, "buy": 6, "price": 20}]
    assert not any("bench edge over exact" in line for line in lines)


def test_an_exported_bench_switch_never_reaches_the_suite(monkeypatch, tmp_path):
    import os

    assert all(os.environ.get(n) is None for n in ("BAZAAR_BENCH_POLICY", "BAZAAR_BENCH_GUARD_MARGIN"))
    assert keeper(tmp_path, Team()).broker_config == BrokerConfig(pace_s=0.0)
