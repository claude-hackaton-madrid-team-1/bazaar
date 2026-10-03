"""Our policies on W1a's simulator bench: the adapter reads answers off the next book; the run needs bazaar_sim."""

import importlib.util

import pytest

from bazaar_agent.evals import bench_w1a as w1a


def book(tick, *offers):
    return {"tick": tick, "offers": [], "bench_offers": list(offers), "fee_bps": 0, "fee_per_card": 0}


def sell(oid, ask):
    return {"id": oid, "give": {"cash": 0, "assets": [{"kind": "card", "ref": "BENCH"}]}, "want": {"cash": ask}}


def buy(oid, bid):
    return {"id": oid, "give": {"cash": bid}, "want": {"cash": 0, "types": ["card:BENCH"]}}


def test_the_edge_policy_returns_crossing_pairs_in_the_contracts_shape():
    policy = w1a.BookPolicy("edge", "normal")
    assert policy(book(0, sell("b1-0", 30), buy("b1-1", 40))) == [("b1-0", "b1-1", 35)]


def test_a_probe_still_in_the_next_book_was_refused_and_a_gone_one_matched():
    policy = w1a.BookPolicy("edge_limit", "normal")
    (probe,) = policy(book(0, sell("b1-0", 62), buy("b1-1", 58)))
    policy(book(0, sell("b1-0", 62), buy("b1-1", 58)))  # still both there: refused
    assert policy.edge.refused == {("b1-0", "b1-1"): [probe[2]]}
    (again,) = policy.pending
    policy(book(0))  # both gone within the tick: matched
    assert (policy.edge.probes.sent, policy.edge.probes.refused, policy.edge.probes.accepted) == (2, 1, 1)


def test_a_probe_gone_after_the_tick_teaches_nothing():
    policy = w1a.BookPolicy("edge_limit", "normal")
    policy(book(0, sell("b1-0", 62), buy("b1-1", 58)))
    policy(book(1))  # they may simply have left
    assert policy.edge.probes.sent == 0


def test_greedy_and_exact_need_no_simulator():
    b = book(0, sell("b1-0", 30), buy("b1-1", 40))
    assert w1a.BookPolicy("greedy", "hard")(b) == [("b1-0", "b1-1", 35)]
    assert w1a.BookPolicy("exact", "hard")(b) == [("b1-0", "b1-1", 35)]


@pytest.mark.skipif(importlib.util.find_spec("bazaar_sim") is None, reason="needs bazaar_sim (#55 + #77)")
def test_the_run_on_the_simulator_bench_scores_every_policy():
    rows = w1a.run(20, presets=("normal",), rules=("quote",))
    by = {r.policy: r for r in rows}
    assert by["stall"].points == 0.5 and by["stall"].tied == 1.0
    assert by["edge"].refused == 0 and by["edge"].mean >= by["stall"].mean - 0.02
    assert "| normal | quote | default | edge |" in w1a.markdown(rows)
