"""The Market Test tournament harness: the bench model, its acceptance rules, the bounds and the policies."""

import json
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from bazaar_agent.evals import bench as ev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "bazaar-kit"))
import starter_broker  # noqa: E402


def trader(tid, side, limit, shade=0.2, patience=4, firm=False, arrive=0, relax_end=0.25):
    return ev.Trader(tid, side, limit, shade, patience, firm, arrive, relax_end)


def test_greedy_plan_is_the_starter_brokers_bench_plan():
    rng = random.Random(1)
    for _ in range(200):
        spec = replace(ev.PRESETS["hard"], arrivals="front")
        bench = ev.InProcessBench(spec, ev.draw_traders(spec, rng, run=rng.randint(1, 30)))
        book = bench.book()
        assert ev.greedy_plan(book) == starter_broker.bench_plan(book)


def test_a_relaxing_quote_walks_toward_the_limit_and_a_firm_one_stays():
    s = trader("b1-0", "sell", 40, shade=0.25, patience=5)
    asks = [s.quote(t) for t in range(5)]
    assert asks[0] == 50 and asks == sorted(asks, reverse=True) and asks[-1] >= 40
    b = trader("b1-1", "buy", 80, shade=0.25, patience=5, firm=True)
    assert {b.quote(t) for t in range(5)} == {60}
    assert not trader("x", "sell", 40, patience=2, arrive=3).present(5)


def test_the_bench_refuses_what_its_rule_refuses():
    traders = [trader("b1-0", "sell", 40, shade=0.25), trader("b1-1", "buy", 48, shade=0.2)]  # ask 50, bid 38
    quote = ev.InProcessBench(ev.BenchSpec(cross="quote"), traders)
    with pytest.raises(ev.Refused):
        quote.match("b1-0", "b1-1", 44)
    limit = ev.InProcessBench(ev.BenchSpec(cross="limit"), traders)
    with pytest.raises(ev.Refused):
        limit.match("b1-0", "b1-1", 39)  # under the seller's cost
    limit.match("b1-0", "b1-1", 44)
    assert limit.realised() == 8
    with pytest.raises(ev.Refused):
        limit.match("b1-0", "b1-1", 44)  # both are gone now
    assert limit.refused == {"does_not_cross": 1, "not_open": 1}


def test_the_bench_fee_uses_the_servers_rounding():
    spec = ev.BenchSpec(fee_bps=250, fee_per_card=1)
    assert spec.fee(30) == 2 and spec.fee(10) == 1  # round(0.75) + 1, round(0.25) + 1


def test_the_stall_crosses_every_pair_or_only_its_quota():
    traders = [trader(f"b1-{i}", "sell", 20) for i in (0, 2)] + [trader(f"b1-{i}", "buy", 90) for i in (1, 3)]
    every = ev.InProcessBench(ev.BenchSpec(), traders)
    every.auto_cross()
    one = ev.InProcessBench(ev.BenchSpec(stall_pairs=1), traders)
    one.auto_cross()
    assert len(every.pairs) == 2 and len(one.pairs) == 1


def test_possible_gains_is_the_simulators_static_optimum():
    traders = [trader("a", "sell", 20), trader("b", "sell", 60), trader("c", "buy", 50), trader("d", "buy", 90)]
    assert ev.possible_gains(traders) == (90 - 20) + 0


def test_the_bounds_order_and_cap_every_policy():
    rng = random.Random(7)
    for preset in ("normal", "hard"):
        spec = ev.PRESETS[preset]
        for _ in range(40):
            traders = ev.draw_traders(spec, rng)
            best = ev.possible_gains(traders) or 1
            o_quote, o_limit = ev.oracle(traders, spec, "quote"), ev.oracle(traders, spec, "limit")
            assert o_quote <= o_limit <= best
            for name, make in ev.policies().items():
                if name == "edge_limit":
                    continue  # it proposes pairs the quote rule refuses; its bound is oracle_limit, below
                assert ev.play(spec, traders, make(preset)).efficiency * best <= o_quote + 1e-9
            limit_spec = replace(spec, cross="limit")
            assert ev.play(limit_spec, traders, ev.policies()["edge_limit"](preset)).efficiency * best <= o_limit + 1e-9


def test_crossing_policies_send_nothing_the_quote_rule_refuses():
    rows = ev.tournament(60, scenarios=("base", "wide"), names=["greedy", "exact", "edge"])
    assert all(r.refused == 0 and r.max_sends <= ev.MAX_SENDS for r in rows)


def test_the_edge_is_not_worse_than_the_stall_on_average():
    rows = ev.tournament(200, scenarios=("base", "front"), names=["stall", "edge"])
    by = {(r.preset, r.scenario, r.policy): r for r in rows}
    for preset in ("normal", "hard"):
        for scenario in ("base", "front"):
            assert by[(preset, scenario, "edge")].mean >= by[(preset, scenario, "stall")].mean - 0.005


def test_a_one_pair_stall_falls_behind_every_broker_when_everyone_arrives_at_once():
    rows = ev.tournament(100, scenarios=("front_stall1",), names=["stall", "greedy", "edge"])
    by = {(r.preset, r.policy): r.mean for r in rows}
    for preset in ("normal", "hard"):
        assert by[(preset, "edge")] > by[(preset, "stall")] + 0.05
        assert by[(preset, "greedy")] > by[(preset, "stall")] + 0.05


def test_the_limit_probe_pays_only_where_the_server_checks_limits():
    rows = ev.tournament(150, presets=("normal",), scenarios=("wide", "wide_limit"), names=["stall", "edge_limit"])
    by = {(r.scenario, r.policy): r for r in rows}
    assert by[("wide_limit", "edge_limit")].mean > by[("wide_limit", "stall")].mean + 0.03
    assert by[("wide", "edge_limit")].refused > 0  # the price of probing a quote-only server
    assert by[("wide", "edge_limit")].mean >= by[("wide", "stall")].mean - 0.01


def test_the_prescient_bound_beats_the_stall():
    rows = ev.tournament(100, presets=("hard",), scenarios=("base",), names=["stall", "prescient"])
    assert rows[1].mean > rows[0].mean


def test_markdown_has_one_line_per_preset_and_scenario():
    rows = ev.tournament(5, scenarios=("base", "limit"), names=["stall", "edge"])
    text = ev.markdown(rows, "mean")
    assert text.splitlines()[0] == "| preset | scenario | stall | edge |"
    assert len(text.splitlines()) == 2 + 4


def test_main_prints_the_tables_and_writes_json(tmp_path, capsys):
    out = tmp_path / "rows.json"
    ev.main(["--books", "3", "--presets", "normal", "--scenarios", "base", "--json", str(out)])
    printed = capsys.readouterr().out
    assert "Efficiency p50" in printed and "Refused matches" in printed
    rows = json.loads(out.read_text())
    assert {r["policy"] for r in rows} >= {"stall", "greedy", "exact", "edge", "oracle_quote", "prescient"}
