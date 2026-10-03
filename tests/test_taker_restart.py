"""B17 (bite X3): dealer threads from before a restart are wrapped up, adopted or closed, never hijacked.

The flipped bite tests live in `tests/bites/test_bite_a1_restart_orphans.py`; these pin the edges: only threads
the taker's decisions log knows are touched (a `bazaar dealer buy` thread never is), a deal the old process
already booked is not booked twice, a deal of a process from before the first wrap-up is never booked again,
the kill switch holds the close, a dry run adopts nothing, and what the decisions log remembers.
"""

import json

import pytest

from bazaar_agent.decisions import PROCESS_STARTED, THREAD_CLOSED, Decision, DecisionLog, writer
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid
from tests.test_db import database_url, open_in, schema  # noqa: F401  (pytest fixtures)

DEALER_THREAD = {"id": 40, "with": "abuela", "team": "t01", "status": "open"}


def _ticks(t, team, first, n):
    for k in range(n):
        t.on_tick(at(team, first + k))


def _old_taker_opened(tmp_path, tid=40, tick=TICK - 5, price=None):
    """The decisions log of the taker before the restart: it started, opened thread `tid` (and bid `price`)."""
    log = DecisionLog(tmp_path)
    _started(log, tick - 1)
    log.decide(_bid_row(tid, tick, 0, kind="dealer_opened", chosen=False, status="done", move={}))
    if price is not None:
        log.decide(_bid_row(tid, tick, price))
    return log


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
    """The old taker opened it and sent no bid: closed after `orphan_after_ticks` quiet ticks."""
    _old_taker_opened(tmp_path)
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 3)
    assert ("close_thread", 40) not in team.sent
    t.on_tick(at(team, TICK + 3))
    assert team.sent.count(("close_thread", 40)) == 1


def test_the_old_takers_thread_with_a_fresh_bid_is_adopted_at_once_and_walks_after_her_silence(tmp_path):
    """Its bid is a tick old: adopted on sight (a "Deal!" on it may still come, and must be booked), read every
    tick, and walked only once `MAX_WAITS` ticks passed without her answer."""
    _old_taker_opened(tmp_path, tick=TICK, price=15)
    team = FakeTeam(threads=[DEALER_THREAD])
    team.offers = [thread_bid(77, 40, "LAV-08", 15) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK + 1))
    assert t.convs["abuela"].thread_id == 40 and "thread 40" in team.reads
    assert ("close_thread", 40) not in team.sent
    team.offers = []  # lapsed
    _ticks(t, team, TICK + 2, 3)
    assert team.sent.count(("close_thread", 40)) == 1


def test_the_kill_switch_holds_the_orphan_close(tmp_path, monkeypatch):
    stop = ("trading_enabled = false",)
    monkeypatch.setattr("bazaar_agent.guardrails.kill_switch", lambda rules, path=None: stop)
    monkeypatch.setattr("bazaar_agent.agents.taker.kill_switch", lambda rules, path=None: stop)
    _old_taker_opened(tmp_path)
    team = FakeTeam(threads=[DEALER_THREAD])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 6)
    assert ("close_thread", 40) not in team.sent


def test_quiet_ticks_under_the_kill_switch_do_not_count(tmp_path, monkeypatch):
    """A held `dealer buy` sends nothing either: when the switch goes off, its thread is not closed at once."""
    stops = [("trading_enabled = false",)]
    monkeypatch.setattr("bazaar_agent.agents.taker.kill_switch", lambda rules, path=None: stops[0])
    monkeypatch.setattr("bazaar_agent.guardrails.kill_switch", lambda rules, path=None: stops[0])
    _old_taker_opened(tmp_path)
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
    _started(log, TICK - 6)
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
    assert team.reads.count("thread 10") == 5  # RESTART_TRIES, then given up
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
    _old_taker_opened(tmp_path, price=20)
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
    _started(log, TICK - 6)
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


def _started(log, tick, owner=None):
    inputs = {"owner": owner or log.writer()}
    log.decide(_bid_row(None, tick, 0, kind=PROCESS_STARTED, inputs=inputs, chosen=False, status="done", move={}))


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


# ---------------------------------------------------------------- review of the takeover PR (#140)


def _old_taker_bid_and_died(tmp_path, team):
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))  # opens 5000 with abuela, bids 18
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    return ledger


def test_a_deal_that_lands_after_the_new_process_first_tick_is_booked(tmp_path):
    """The new process starts while the old bid is fresh; her "Deal!" comes a tick later (review P1)."""
    team = FakeTeam()
    ledger = _old_taker_bid_and_died(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK + 1))
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    _ticks(new, team, TICK + 2, 3)
    assert ledger.spent_since(0) == 18


def test_many_walked_threads_never_starve_the_one_that_dealt(tmp_path):
    """Walks are wrapped up, and the wrap-up reads newest first with no overall cap (review P1)."""
    log = DecisionLog(tmp_path)
    _started(log, TICK - 31)
    team = FakeTeam()
    for tid in range(10, 26):  # 16 threads an old process walked without wrapping them up
        log.decide(_bid_row(tid, TICK - 30 + tid - 10, 12))
        team.thread_payloads[tid] = {"id": tid, "status": "closed", "closed_reason": "walked", "with": "abuela"}
    log.decide(_bid_row(99, TICK - 1, 18))  # the thread it drove when it died: abuela took our 18
    dealer_took_our_bid(team, 99, 991, "LAV-08", 18)
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    t.on_tick(at(team, TICK))
    assert "thread 99" in team.reads and ledger.spent_since(0) == 18


def test_a_walk_wraps_its_thread_up(tmp_path):
    team = FakeTeam()
    ledger = _old_taker_bid_and_died(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    team.offers = []  # her answer never came, our bid lapsed: the adopted thread walks
    _ticks(new, team, TICK + 3, 2)
    assert ("close_thread", 5000) in team.sent
    assert DecisionLog(tmp_path).thread_trails("taker", 0)[5000].closed
    assert ledger.spent_since(0) == 0


def test_deals_of_a_process_from_before_the_first_wrap_up_are_not_booked_again(tmp_path):
    """The first deploy onto this code: the old process booked its deals and wrote no THREAD_CLOSED row."""
    log = DecisionLog(tmp_path)
    log.decide(_bid_row(10, TICK - 5, 12))  # no PROCESS_STARTED before it
    team = FakeTeam()
    dealer_took_our_bid(team, 10, 101, "LAV-08", 12)
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    _ticks(t, team, TICK, 2)
    assert "thread 10" not in team.reads and ledger.spent_since(0) == 0


def test_a_thread_the_taker_never_drove_is_never_closed_even_when_quiet(tmp_path):
    """A laptop `dealer buy` paused by its own .local/PAUSE stops bidding; the taker leaves its thread alone."""
    team = FakeTeam(threads=[DEALER_THREAD])
    team.offers = [thread_bid(77, 40, "LAV-08", 20) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))
    team.offers = []
    _ticks(t, team, TICK + 1, 8)
    assert ("close_thread", 40) not in team.sent and "abuela" not in t.convs


def test_an_unreadable_thread_body_does_not_stall_the_taker(tmp_path):
    log = DecisionLog(tmp_path)
    _started(log, TICK - 6)
    log.decide(_bid_row(10, TICK - 5, 12))
    team = FakeTeam()
    team.thread_payloads[10] = {"id": 10, "status": "deal", "with": "abuela", "messages": "not a list"}
    t, lines, _ = make_taker(tmp_path, team, FakePublic())
    _ticks(t, team, TICK, 7)
    assert team.reads.count("thread 10") <= 5
    assert any(s[0] == "open_thread" for s in team.sent)  # the taker trades on


def test_thread_trails_skip_rows_that_are_not_objects_or_have_bad_ticks(tmp_path):
    log = DecisionLog(tmp_path)
    log.decide(_bid_row(1, 100, 10))
    with (tmp_path / "agents" / "decisions.jsonl").open("a") as f:
        f.write("[1, 2]\n")
        f.write(json.dumps({"agent": "taker", "thread_id": 2, "tick": "soon", "kind": "dealer_bid"}) + "\n")
        f.write(
            json.dumps(
                {
                    "agent": "taker",
                    "thread_id": 3,
                    "tick": 100,
                    "inputs": "x",
                    "chosen": True,
                    "move": {"price": 10**15},
                }
            )
            + "\n"
        )
    trails = log.thread_trails("taker", 0)
    assert sorted(trails) == [1, 3] and trails[3].top_price is None
    assert log.first_tick("taker", PROCESS_STARTED, log.writer()) is None
    _started(log, 80, owner="wsomeone-else")
    _started(log, 90)
    assert log.first_tick("taker", PROCESS_STARTED, log.writer()) == 90


# ---------------------------------------------------------------- review round 2 of #140


def _opened_by(log, tid, tick, price, owner):
    inputs = {"dealer": "abuela", "thread": tid, "item": "LAV-08", "owner": owner}
    log.decide(_bid_row(tid, tick, 0, kind="dealer_opened", inputs=inputs, chosen=False, status="done", move={}))
    log.decide(_bid_row(tid, tick, price))


def test_a_thread_another_live_taker_opened_is_never_adopted_or_booked(tmp_path):
    """A laptop taker on the shared Postgres beside Railway's: each touches only its own threads."""
    log = DecisionLog(tmp_path)
    _started(log, TICK - 6, owner="wlaptop")
    _opened_by(log, 40, TICK - 5, 20, owner="wlaptop")  # still open, driven by the other taker
    _opened_by(log, 41, TICK - 5, 18, owner="wlaptop")  # dealt: the other taker books it
    team = FakeTeam(threads=[DEALER_THREAD], offers=[thread_bid(77, 40, "LAV-08", 20)])
    dealer_took_our_bid(team, 41, 78, "LAV-08", 18)
    team.threads = [DEALER_THREAD]
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    _ticks(t, team, TICK, 4)
    assert "abuela" not in t.convs and ("close_thread", 40) not in team.sent
    assert "thread 41" not in team.reads and ledger.spent_since(0) == 0


def test_an_open_thread_older_than_the_lookback_is_still_adopted(tmp_path):
    """A pause longer than `restart_lookback_ticks`, then a redeploy: the open thread is looked up by id."""
    log = DecisionLog(tmp_path)
    _opened_by(log, 40, TICK - 90, 20, owner=log.writer())  # tick 10: outside the 40-tick lookback
    team = FakeTeam(threads=[DEALER_THREAD], offers=[thread_bid(77, 40, "LAV-08", 20)])
    t, lines, _ = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))
    assert any("thread 40 with abuela for LAV-08: adopted" in line for line in lines)
    assert "thread 40" in team.reads and ("close_thread", 40) in team.sent  # stale bid: walks on its next move


def test_a_postgres_blip_at_boot_does_not_end_the_wrap_up(tmp_path):
    """The first read reached only the JSONL: the wrap-up keeps reading until every store answered."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise OSError("no route")

    log = DecisionLog(tmp_path, flaky)
    assert not log.complete or calls["n"] == 0
    log.thread_trails("taker", 0)
    assert not log.complete  # a store is configured and did not answer


# ---------------------------------------------------------------- review round 3 of #140


def _rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "agents" / "decisions.jsonl").read_text().splitlines()]


def test_the_taker_stamps_its_own_threads_and_its_start_with_its_writer(tmp_path):
    team = FakeTeam()
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))  # opens 5000 with abuela
    me = DecisionLog(tmp_path).writer()
    opened = [r for r in _rows(tmp_path) if r.get("kind") == "dealer_opened"]
    started = [r for r in _rows(tmp_path) if r.get("kind") == PROCESS_STARTED]
    assert [(r["thread_id"], r["inputs"]["owner"]) for r in opened] == [(5000, me)]
    assert [r["inputs"]["owner"] for r in started] == [me]


def test_a_laptop_keeps_its_writer_when_its_host_name_changes(tmp_path, monkeypatch):
    """A Mac's host name follows its network: the writer is saved once per data directory (review round 3)."""
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "cafe-wifi.local")
    first = DecisionLog(tmp_path).writer()
    monkeypatch.setattr("socket.gethostname", lambda: "home.local")
    assert DecisionLog(tmp_path).writer() == first
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc-taker")
    assert DecisionLog(tmp_path).writer() == writer() != first  # on Railway: the service, whatever the disk


def test_a_store_that_never_answers_keeps_the_wrap_up_going_until_its_cap(tmp_path):
    """Postgres down at boot: nothing pending in the JSONL is no proof the old threads are done (round 3)."""
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import parts

    def down():
        raise OSError("no route")

    team = FakeTeam()
    kw = parts(tmp_path, max_spend_per_game_hour=0) | {"decisions": DecisionLog(tmp_path, down)}
    config = TakerConfig(max_dealer_threads=3, restart_lookback_ticks=6)
    t = Taker(
        team, FakePublic(), live=True, log=lambda s: None, now=lambda: 1000.0, sleep=lambda s: None, config=config, **kw
    )
    _ticks(t, team, TICK, 3)
    assert not t._restart_checked
    _ticks(t, team, TICK + 3, 3)
    assert t._restart_checked


def test_rate_limited_and_network_reads_do_not_use_up_tries(tmp_path):
    from bazaar_agent.sdk import BazaarError

    log = DecisionLog(tmp_path)
    _started(log, TICK - 2)
    log.decide(_bid_row(70, TICK - 1, 21))
    team = FakeTeam()
    dealer_took_our_bid(team, 70, 701, "LAV-08", 21)
    real, fails = team.thread, {"n": 0}

    def thread(tid):
        if tid == 70 and fails["n"] < 8:
            fails["n"] += 1
            team.reads.append(f"thread {tid}")
            raise BazaarError("rate_limited" if fails["n"] % 2 else "network", "x", 429)
        return real(tid)

    team.thread = thread
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    _ticks(t, team, TICK, 12)
    assert fails["n"] == 8 and ledger.spent_since(0) == 21  # more failed reads than RESTART_TRIES, still booked


def test_a_trail_thread_that_can_never_be_adopted_stops_costing_reads(tmp_path):
    log = DecisionLog(tmp_path)
    inputs = {"dealer": "ghost", "thread": 80, "item": "LAV-08", "owner": log.writer()}
    log.decide(_bid_row(80, TICK - 2, 0, kind="dealer_opened", inputs=inputs, chosen=False, status="done", move={}))
    team = FakeTeam(threads=[{"id": 80, "with": "ghost", "team": "t01", "status": "open"}])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    n, real = {"trails": 0}, t.rec.decisions.thread_trails

    def counted(*a, **k):
        n["trails"] += 1
        return real(*a, **k)

    t.rec.decisions.thread_trails = counted
    _ticks(t, team, TICK, 60)
    assert n["trails"] == 40 and t._restart_checked  # restart_lookback_ticks
