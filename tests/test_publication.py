"""Lost write responses retain inventory/cash across workers and process restarts."""

from bazaar_agent.agents import publication as p
from bazaar_agent.agents.seller import open_commitments
from bazaar_agent.guardrails import Ledger
from tests.agent_fakes import ME, bid, our_ask


def test_unknown_asset_remains_promised_after_restart_and_absent_offer(tmp_path):
    path = tmp_path / "ledger.jsonl"
    p.reserve(Ledger(path), 100, 1.5, "t01", {"assets": [4]}, {"cash": 10})
    rows = p.with_pending(Ledger(path), ME, [], "t01", 200, 3.0)
    assert open_commitments(rows, "t01").listed == {4}
    assert open_commitments(rows, "t01").listed_refs == ("LAT-03",)


def test_observed_exact_offer_replaces_unknown_promise_without_double_counting(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    p.reserve(ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 10})
    actual = our_ask(8, 4, "LAT-03", 10, created=100)
    rows = p.with_pending(ledger, ME, [actual], "t01", 101, 1.6)
    assert rows == [actual]
    assert p.with_pending(ledger, ME, [], "t01", 102, 1.7) == []


def test_cash_only_unknown_resolves_only_unique_exact_fresh_offer(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    p.reserve(ledger, 100, 1.5, "t01", {"cash": 10}, {"cards": ["LAV-02"]})
    assert open_commitments(p.with_pending(ledger, ME, [], "t01", 101, 1.6), "t01").cash == 10
    stale = bid(8, "LAV-02", 10, created=99)
    assert len(p.with_pending(ledger, ME, [stale], "t01", 101, 1.6)) == 2
    actual = bid(9, "LAV-02", 10, created=100)
    assert p.with_pending(ledger, ME, [actual], "t01", 101, 1.6) == [actual]


def test_one_observed_offer_does_not_release_two_unknown_writes(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for _ in range(2):
        p.reserve(ledger, 100, 1.5, "t01", {"cash": 10}, {"cards": ["LAV-02"]})
    rows = p.with_pending(ledger, ME, [bid(9, "LAV-02", 10, created=100)], "t01", 101, 1.6)
    assert len(rows) == 2
    assert open_commitments(rows, "t01").cash == 20


def test_confirmed_refusal_or_departed_asset_releases_promise(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    token = p.reserve(ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 10})
    p.release(ledger, token, 100, 1.5)
    assert p.with_pending(ledger, ME, [], "t01", 101, 1.6) == []
    p.reserve(ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 10})
    me = {**ME, "assets": [a for a in ME["assets"] if a["id"] != 4]}
    assert p.with_pending(ledger, me, [], "t01", 101, 1.6) == []


def test_worlds_do_not_share_promises(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.world = "simulator"
    p.reserve(ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 10})
    ledger.world = "real"
    assert p.with_pending(ledger, ME, [], "t01", 101, 1.6) == []


def test_addressed_promise_keeps_its_counterparty(tmp_path):
    from bazaar_agent.agents.seller import trade_book

    ledger = Ledger(tmp_path / "ledger.jsonl")
    p.reserve(ledger, 100, 1.5, "t01", {"assets": [4]}, {"cash": 10}, to="t07")
    rows = p.with_pending(ledger, ME, [], "t01", 101, 1.6)
    commitments = trade_book(rows, "t01", {})
    assert commitments.addressed == {"t07": 10}
    assert commitments.public == 0
