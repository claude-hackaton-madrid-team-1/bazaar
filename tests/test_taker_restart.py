"""B17 (bite X3): dealer threads from before a restart are wrapped up, adopted or closed, never hijacked.

The flipped bite tests live in `tests/bites/test_bite_a1_restart_orphans.py`; these pin the edges: a thread a
live `bazaar dealer buy` drives is never touched, a deal the old process already booked is not booked twice,
the kill switch holds the close, a dry run adopts nothing, and what the decisions log remembers.
"""

import json

import pytest

from bazaar_agent.decisions import THREAD_CLOSED, Decision, DecisionLog
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)

DEALER_THREAD = {"id": 40, "with": "abuela", "team": "t01", "status": "open"}


def _ticks(t, team, first, n):
    for k in range(n):
        t.on_tick(at(team, first + k))


def test_a_thread_a_live_dealer_buy_drives_is_never_adopted_or_closed(tmp_path):
    """`bazaar dealer buy --live` on a laptop bids every tick in its own thread: the taker leaves it alone."""
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    for k in range(6):
        team.offers = [thread_bid(77 + k, 40, "LAV-08", 15 + k) | {"created_tick": TICK + k}]  # a new bid each tick
        t.on_tick(at(team, TICK + k))
    assert "abuela" not in t.convs
    assert "thread 40" not in team.reads and ("close_thread", 40) not in team.sent
    assert not any(s[0] in ("open_thread", "say") and s[1] in ("abuela", 40) for s in team.sent)


def test_an_orphan_without_a_bid_is_closed_only_after_it_stayed_quiet(tmp_path):
    """No bid of ours stands (the old one expired): closed after `orphan_after_ticks`, not on first sight
    (a `dealer buy` that just opened its thread bids within the tick)."""
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 3)
    assert ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 3))
    assert team.sent.count(("close_thread", 40)) == 1


def test_a_bid_in_between_resets_the_quiet_count(tmp_path):
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 2)
    team.offers = [thread_bid(77, 40, "LAV-08", 15) | {"created_tick": TICK + 2}]
    _ticks(t, team, TICK + 2, 3)  # fresh while created_tick > tick - 3: nobody touches it
    assert "thread 40" not in team.reads and ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 5))  # stale now: adopted, read, walked (its whole plan is the old bid)
    assert "thread 40" in team.reads and ("close_thread", 40) in team.sent


def test_the_kill_switch_holds_the_orphan_close(tmp_path, monkeypatch):
    stop = ("trading_enabled = false",)
    monkeypatch.setattr("bazaar_agent.guardrails.kill_switch", lambda rules, path=None: stop)
    monkeypatch.setattr("bazaar_agent.agents.taker.kill_switch", lambda rules, path=None: stop)
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 6)
    assert ("close_thread", 40) not in team.sent


def test_quiet_ticks_under_the_kill_switch_do_not_count(tmp_path, monkeypatch):
    """A held `dealer buy` sends nothing either: when the switch goes off, its thread is not closed at once."""
    stops = [("trading_enabled = false",)]
    monkeypatch.setattr("bazaar_agent.agents.taker.kill_switch", lambda rules, path=None: stops[0])
    monkeypatch.setattr("bazaar_agent.guardrails.kill_switch", lambda rules, path=None: stops[0])
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 6)
    stops[0] = ()
    _ticks(t, team, TICK + 6, 3)
    assert ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 9))
    assert ("close_thread", 40) in team.sent


def test_a_refused_thread_read_does_not_stop_the_taker(tmp_path):
    """The restart wrap-up skips a thread whose read is refused and tries it again next tick, a few times."""
    from bazaar_agent.sdk import BazaarError

    log = DecisionLog(tmp_path)
    log.decide(_bid_row(10, TICK - 5, 12))
    team = FakeTeam()
    real = team.thread

    def refused(tid):
        if tid == 10:
            team.reads.append("thread 10")
            raise BazaarError("not_found", "no such thread", 404)
        return real(tid)

    team.thread = refused
    t, lines, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 8)
    assert team.reads.count("thread 10") == 5  # RESTART_TICKS, then given up
    assert any(s[0] == "open_thread" for s in team.sent)  # the taker trades on meanwhile


def test_a_dry_run_adopts_and_closes_nothing(tmp_path):
    team = FakeTeam(threads=[DEALER_THREAD], offers=[thread_bid(77, 40, "LAV-08", 20)])
    t, _, _ = make_taker(tmp_path, team, FakePublic(), live=False)
    _ticks(t, team, TICK, 5)
    assert not t.convs and "thread 40" not in team.reads and team.sent == []


def test_an_adopted_thread_that_deals_is_booked_once(tmp_path):
    """Adopted with its old bid as the whole plan; the thread read shows the dealer took it (the open list
    was read a moment earlier): `_finished` books it and writes the THREAD_CLOSED row, and the next restart
    does not book it again."""
    team = FakeTeam()
    dealer_took_our_bid(team, 40, 77, "LAV-08", 20)
    team.threads, team.offers = [DEALER_THREAD], [thread_bid(77, 40, "LAV-08", 20)]  # the listing lags
    t, _, ledger = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))
    assert ledger.spent_since(0) == 20 and "abuela" not in t.convs
    team.threads, team.offers = [], []
    again, _, _ = make_taker(tmp_path, team, FakePublic())  # one more restart
    _ticks(again, team, TICK + 2, 2)
    assert ledger.spent_since(0) == 20


def test_a_deal_the_old_process_booked_is_not_booked_again_after_a_restart(tmp_path):
    team = FakeTeam()
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))  # opens 5000 with abuela, bids 18
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    old.on_tick(at(team, TICK + 1))  # the old process sees the deal and books it
    assert ledger.spent_since(0) == 18
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(new, team, TICK + 2, 3)
    assert ledger.spent_since(0) == 18


def test_a_walked_thread_that_ended_without_a_deal_books_nothing_after_a_restart(tmp_path):
    team = FakeTeam()
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))
    team.thread_payloads[5000] = {"id": 5000, "status": "closed", "closed_reason": "idle", "with": "abuela"}
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    before = len(team.reads)
    _ticks(new, team, TICK + 1, 2)
    assert ledger.spent_since(0) == 0
    assert team.reads[before:].count("thread 5000") == 1  # wrapped up once
    again, _, _ = make_taker(tmp_path, team, FakePublic())
    before = len(team.reads)
    _ticks(again, team, TICK + 3, 2)
    assert "thread 5000" not in team.reads[before:]  # remembered as closed


def test_restart_reads_are_bounded_per_tick(tmp_path):
    """Many unwrapped threads in the log: at most `max_dealer_threads` reads per tick, the rest next tick."""
    log = DecisionLog(tmp_path)
    for tid in range(10, 17):
        log.decide(_bid_row(tid, TICK - 5, 12))
    team = FakeTeam()
    for tid in range(10, 17):
        team.thread_payloads[tid] = {"id": tid, "status": "closed", "closed_reason": "idle", "with": "abuela"}
    t, _, _ = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)  # no new threads in the way
    t.on_tick(at(team, TICK))
    assert sum(r.startswith("thread 1") for r in team.reads) == 3
    _ticks(t, team, TICK + 1, 3)
    assert sorted(r for r in team.reads if r.startswith("thread 1")) == [f"thread {tid}" for tid in range(10, 17)]


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
    conn.close()
    log.close()
