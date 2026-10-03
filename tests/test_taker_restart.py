"""B17 (bite X3): the taker's dealer threads that no Conversation drives are watched, booked once, and closed when
quiet; a thread the taker never drove (a live `bazaar dealer buy`) is never touched.

The flipped bite tests live in `tests/bites/test_bite_a1_restart_orphans.py`; these pin the edges, among them
r1's three: a deal after the new process's first tick, two overlapping takers, and a deal behind many walks.
"""

import json

import pytest

from bazaar_agent.decisions import THREAD_CLOSED, Decision, DecisionLog
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)

DEALER_THREAD = {"id": 40, "with": "abuela", "team": "t01", "status": "open"}


def _ticks(t, team, first, n):
    for k in range(n):
        t.on_tick(at(team, first + k))


def _owned(tmp_path, tid=40, tick=TICK - 10, price=20):
    """The process before this one drove thread `tid` (its decisions log says so)."""
    DecisionLog(tmp_path).decide(_bid_row(tid, tick, price))


def _old_process(tmp_path, team):
    """Process 1 opens thread 5000 with abuela for LAV-08 and bids 18; the server lists it open."""
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK}]
    return old, ledger


def test_a_thread_the_taker_never_drove_is_never_touched(tmp_path):
    """`bazaar dealer buy --live` on a laptop: not in the taker's decisions, so never read or closed, even
    when it goes quiet (a kill switch held on that laptop)."""
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 8)
    assert "thread 40" not in team.reads and ("close_thread", 40) not in team.sent


def test_a_watched_thread_with_fresh_bids_is_left_alone(tmp_path):
    """The old taker still drives it (a redeploy overlaps two containers): it bids every tick."""
    _owned(tmp_path)
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    for k in range(6):
        team.offers = [thread_bid(77 + k, 40, "LAV-08", 15 + k) | {"created_tick": TICK + k}]
        t.on_tick(at(team, TICK + k))
    assert ("close_thread", 40) not in team.sent


def test_a_watched_thread_without_a_bid_is_closed_only_after_it_stayed_quiet(tmp_path):
    _owned(tmp_path)
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 3)
    assert ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 3))
    assert team.sent.count(("close_thread", 40)) == 1


def test_quiet_ticks_under_the_kill_switch_do_not_count(tmp_path, monkeypatch):
    _owned(tmp_path)
    stops = [("trading_enabled = false",)]
    monkeypatch.setattr("bazaar_agent.agents.taker.kill_switch", lambda rules, path=None: stops[0])
    monkeypatch.setattr("bazaar_agent.guardrails.kill_switch", lambda rules, path=None: stops[0])
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 6)
    assert ("close_thread", 40) not in team.sent
    stops[0] = ()
    _ticks(t, team, TICK + 6, 3)
    assert ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 9))
    assert ("close_thread", 40) in team.sent


def test_a_dry_run_watches_nothing(tmp_path):
    _owned(tmp_path)
    team = FakeTeam(threads=[DEALER_THREAD], offers=[thread_bid(77, 40, "LAV-08", 20)])
    t, _, _ = make_taker(tmp_path, team, FakePublic(), live=False)
    _ticks(t, team, TICK, 5)
    assert "thread 40" not in team.reads and team.sent == []


def test_r1_a_deal_after_the_new_process_first_tick_is_booked(tmp_path):
    """At the restart our bid is fresh (the old process bid a moment ago): watched, not closed; the dealer
    takes it a tick later, the thread leaves the open list, and the new process books it."""
    team = FakeTeam()
    _, ledger = _old_process(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK + 1))
    assert ledger.spent_since(0) == 0 and ("close_thread", 5000) not in team.sent
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    _ticks(new, team, TICK + 2, 2)
    assert ledger.spent_since(0) == 18


def test_r1_two_overlapping_takers_book_the_deal_once(tmp_path):
    """A redeploy overlaps the old container (which drives the thread) and the new one (which watches it)."""
    team = FakeTeam()
    old, ledger = _old_process(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK + 1))
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    old.on_tick(at(team, TICK + 2))
    new.on_tick(at(team, TICK + 2))
    new.on_tick(at(team, TICK + 3))
    assert ledger.spent_since(0) == 18


def test_r1_a_deal_behind_many_walked_threads_is_booked_first(tmp_path):
    """16 threads the old process walked from (never wrapped up), and the newest one dealt: newest first, it
    is booked on the first tick, and the rest are wrapped up a few per tick."""
    log = DecisionLog(tmp_path)
    team = FakeTeam()
    for tid in range(10, 26):
        log.decide(_bid_row(tid, TICK - 30 + tid - 10, 12))
        team.thread_payloads[tid] = {"id": tid, "status": "closed", "closed_reason": "walked", "with": "abuela"}
    log.decide(_bid_row(5000, TICK - 2, 18))
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=200)
    t.on_tick(at(team, TICK))
    assert ledger.spent_since(0) == 18
    _ticks(t, team, TICK + 1, 6)
    assert not set(t._watch) & {*range(10, 26), 5000} and ledger.spent_since(0) == 18


def test_a_thread_this_process_walked_that_dealt_at_the_boundary_is_booked(tmp_path):
    team = FakeTeam()
    old, ledger = _old_process(tmp_path, team)
    conv = old.convs["abuela"]
    old.convs.pop("abuela")
    old._watch_walked(conv, TICK)  # as `_desk_send` does after a walk
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    old.on_tick(at(team, TICK + 1))
    assert ledger.spent_since(0) == 18


def test_a_deal_the_old_process_booked_is_not_booked_again_after_a_restart(tmp_path):
    team = FakeTeam()
    old, ledger = _old_process(tmp_path, team)
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    old.on_tick(at(team, TICK + 1))
    assert ledger.spent_since(0) == 18
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(new, team, TICK + 2, 3)
    assert ledger.spent_since(0) == 18


def test_a_thread_that_ended_without_a_deal_books_nothing_and_is_read_once(tmp_path):
    team = FakeTeam()
    _old_process(tmp_path, team)
    team.threads, team.offers = [], []
    team.thread_payloads[5000] = {"id": 5000, "status": "closed", "closed_reason": "idle", "with": "abuela"}
    new, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    before = len(team.reads)
    _ticks(new, team, TICK + 1, 2)
    assert ledger.spent_since(0) == 0 and team.reads[before:].count("thread 5000") == 1
    again, _, _ = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    before = len(team.reads)
    _ticks(again, team, TICK + 3, 2)
    assert "thread 5000" not in team.reads[before:]  # remembered as wrapped up


def test_watched_reads_are_bounded_per_tick(tmp_path):
    log = DecisionLog(tmp_path)
    team = FakeTeam()
    for tid in range(10, 17):
        log.decide(_bid_row(tid, TICK - 5, 12))
        team.thread_payloads[tid] = {"id": tid, "status": "closed", "closed_reason": "idle", "with": "abuela"}
    t, _, _ = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    t.on_tick(at(team, TICK))
    assert sum(r.startswith("thread 1") for r in team.reads) == 3
    _ticks(t, team, TICK + 1, 3)
    assert sorted(r for r in team.reads if r.startswith("thread 1")) == [f"thread {tid}" for tid in range(10, 17)]


def test_a_refused_read_is_retried_then_given_up_and_the_taker_trades_on(tmp_path):
    DecisionLog(tmp_path).decide(_bid_row(10, TICK - 5, 12))
    team = FakeTeam()
    real = team.thread

    def refused(tid):
        if tid == 10:
            team.reads.append("thread 10")
            raise BazaarError("not_found", "no such thread", 404)
        return real(tid)

    team.thread = refused
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 8)
    assert team.reads.count("thread 10") == 5  # RESTART_TICKS refusals, then given up
    assert any(s[0] == "open_thread" for s in team.sent)


def test_decide_once_writes_one_closing_row_per_thread(tmp_path):
    log, other = DecisionLog(tmp_path), DecisionLog(tmp_path)
    closing = _bid_row(7, 100, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={})
    assert log.decide_once(closing) is not None
    assert other.decide_once(closing) is None
    assert log.decide_once(_bid_row(8, 100, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={}))


def _bid_row(tid, tick, price, **extra):
    base = {
        "agent": "taker",
        "tick": tick,
        "kind": "dealer_bid",
        "inputs": {"dealer": "abuela", "thread": tid, "item": "LAV-08"},
        "reason": "small distinct step up",
        "guardrail": "allowed",
        "chosen": True,
        "status": "approved",
        "dry_run": False,
        "thread_id": tid,
        "move": {"kind": "bid", "price": price},
    }
    return Decision(**{**base, **extra})


def test_thread_trails_remember_live_threads_only(tmp_path):
    log = DecisionLog(tmp_path)
    log.decide(_bid_row(1, 100, 10))
    did = log.decide(_bid_row(1, 101, 12))
    log.settle(did, "done")  # an update row: skipped
    log.decide(_bid_row(2, 100, 30, dry_run=True))  # a dry run never had a thread
    log.decide(_bid_row(3, 100, 30, agent="maker"))
    log.decide(_bid_row(4, 50, 30))  # before the lookback
    log.decide(_bid_row(5, 102, 14, chosen=False, status="expired"))  # not sent: no price
    log.decide(_bid_row(6, 103, 9))
    log.decide(_bid_row(6, 104, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={}))
    with (tmp_path / "agents" / "decisions.jsonl").open("a") as f:
        f.write("not json\n")
    trails = log.thread_trails("taker", 90)
    assert sorted(trails) == [1, 5, 6]
    assert (trails[1].item, trails[1].last_tick, trails[1].top_price, trails[1].closed) == ("LAV-08", 101, 12, False)
    assert trails[5].top_price is None
    assert trails[6].closed and trails[6].top_price == 9


def test_thread_trails_read_jsonl_rows_written_while_postgres_was_down(tmp_path):
    def down():
        raise OSError("no route")

    log = DecisionLog(tmp_path, down)
    log.decide(_bid_row(7, 100, 11))
    assert json.loads((tmp_path / "agents" / "decisions.jsonl").read_text().splitlines()[0])["thread_id"] == 7
    assert log.thread_trails("taker", 0)[7].top_price == 11


@pytest.mark.integration
def test_thread_trails_read_postgres(database_url, schema, tmp_path):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    log = DecisionLog(tmp_path, lambda: open_in(database_url, schema))
    log.decide(_bid_row(8, 100, 11))
    log.decide(_bid_row(8, 101, 13))
    log.decide(_bid_row(9, 100, 20, dry_run=True))
    log.decide(_bid_row(8, 102, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={}))
    trails = log.thread_trails("taker", 0)
    assert sorted(trails) == [8]
    assert (trails[8].item, trails[8].top_price, trails[8].closed) == ("LAV-08", 13, True)
    again = _bid_row(8, 103, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={})
    assert log.decide_once(again) is None  # the partial unique index: one closing row per thread
    conn.close()
    log.close()
