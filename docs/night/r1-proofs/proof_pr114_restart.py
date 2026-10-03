"""Reviewer proof tests for PR #114 (B17). Not committed."""

from bazaar_agent.decisions import DecisionLog
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid
from tests.test_taker_restart import _bid_row


def _old_opens_and_bids(tmp_path, team):
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))  # opens 5000 with abuela, bids 18
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    # a REAL thread bid: made this tick, stands 2 ticks (sim OFFER_TTL_TICKS = 2)
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    return old, ledger


def test_deal_one_tick_after_a_fast_restart_is_booked(tmp_path):
    """Restart within a tick: at the new process's first tick the thread is open and our bid is fresh, so
    it is neither wrapped up (open) nor adopted (fresh). The dealer takes the bid one tick later."""
    team = FakeTeam()
    _, ledger = _old_opens_and_bids(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK + 1))
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    for k in range(2, 8):
        new.on_tick(at(team, TICK + k))
    assert ledger.spent_since(0) == 18


def test_two_live_processes_book_one_deal_once(tmp_path):
    """Railway redeploy overlap (or laptop + Railway): B starts while A still owns thread 5000."""
    team = FakeTeam()
    a, ledger = _old_opens_and_bids(tmp_path, team)
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    b, _, _ = make_taker(tmp_path, team, FakePublic())
    b.on_tick(at(team, TICK + 1))  # B's restart wrap-up sees 5000 closed and unwrapped: books 18
    a.on_tick(at(team, TICK + 1))  # A's _finished books 18 again
    assert ledger.spent_since(0) == 18


def test_a_deal_behind_many_walked_threads_is_booked(tmp_path):
    """Walks write no THREAD_CLOSED row, so every walked thread of the last 40 ticks is re-read at restart,
    oldest first, 3 per tick for 5 ticks. A deal on the newest thread is never reached."""
    log = DecisionLog(tmp_path)
    team = FakeTeam()
    for tid in range(10, 26):  # 16 walked threads
        log.decide(_bid_row(tid, TICK - 30 + (tid - 10), 12))
        team.thread_payloads[tid] = {"id": tid, "status": "walked", "closed_reason": "walked", "with": "abuela"}
    log.decide(_bid_row(99, TICK - 2, 18))
    dealer_took_our_bid(team, 99, 991, "LAV-08", 18)
    t, _, ledger = make_taker(tmp_path, team, FakePublic(), max_spend_per_game_hour=0)
    for k in range(8):
        t.on_tick(at(team, TICK + k))
    assert ledger.spent_since(0) == 18


def test_adoption_never_happens_with_real_two_tick_bids(tmp_path):
    """orphan_after_ticks = 3 but a thread bid lapses 2 ticks after it is made: a standing bid is always
    'fresh', so no thread is ever adopted; it is closed instead once the bid lapsed."""
    team = FakeTeam(threads=[{"id": 40, "with": "abuela", "team": "t01", "status": "open"}])
    t, _, _ = make_taker(tmp_path, team, FakePublic())
    adopted = False
    for k in range(10):
        tick = TICK + k
        # the old bid made at TICK-1, expires TICK+1: listed while tick <= expires_tick
        team.offers = [thread_bid(77, 40, "LAV-08", 20) | {"created_tick": TICK - 1, "expires_tick": TICK + 1}]
        team.offers = [o for o in team.offers if o["expires_tick"] >= tick]
        t.on_tick(at(team, tick))
        adopted = adopted or "abuela" in t.convs
    assert adopted


def test_deal_after_a_same_tick_restart_is_booked(tmp_path):
    """Crash/restart inside the tick the old process bid in: the new process's first tick is that same tick,
    the dealer takes the bid at the next boundary (as the sim and Friday's feed do)."""
    team = FakeTeam()
    _, ledger = _old_opens_and_bids(tmp_path, team)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK))  # same tick: thread open, bid fresh -> neither wrapped up nor adopted
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    for k in range(1, 8):
        new.on_tick(at(team, TICK + k))
    assert ledger.spent_since(0) == 18
