"""Bite A1: a process restart orphans the taker's open dealer threads.

`Taker.convs` lives in memory only. After a restart (Railway redeploy, crash) the new process sees the
old thread in /api/me/threads?status=open, counts its dealer as busy (so it is never reopened) and its
slot as used, but never reads, bids, walks or wraps it up. If the dealer then takes the standing bid the
old process left in that thread, `_finished` never runs and the spend never reaches the ledger.

Each test PASSES when the code is correct; a failure proves the bite.
"""

import pytest

from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid


def _old_process_opens_and_bids(tmp_path, team):
    """Process 1 opens a thread with abuela for LAV-08 and bids 18 (the ladder's opening bid)."""
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))
    (opened,) = [s for s in team.sent if s[0] == "open_thread"]
    assert opened == ("open_thread", "abuela", {"buy": {"card": "LAV-08"}})
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]
    # The server now shows that thread as open and our bid standing in it (FakeTeam does not track it).
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18)]
    return old, ledger


@pytest.mark.parametrize(
    "restart",
    [
        pytest.param(False, id="same-process(control)"),
        pytest.param(True, id="after-restart"),  # BITE X3, fixed by B17
    ],
)
def test_a1_dealer_deal_on_a_thread_from_before_a_restart_is_booked_as_spend(tmp_path, restart):
    team = FakeTeam()
    proc, ledger = _old_process_opens_and_bids(tmp_path, team)
    if restart:  # redeploy: a fresh Taker, same shared ledger (Postgres in prod, the same file here)
        proc, _, _ = make_taker(tmp_path, team, FakePublic())
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)  # boundary: abuela takes our 18, settles at once
    proc.on_tick(at(team, TICK + 1))
    proc.on_tick(at(team, TICK + 2))
    assert ledger.spent_since(0) == 18, (
        f"abuela's deal at 18 on thread 5000 was never booked: ledger spend {ledger.spent_since(0)} "
        "(max_spend_per_game_hour undercounts)"
    )


def test_a1_an_open_thread_from_a_previous_process_is_adopted_or_closed(tmp_path):
    """A fresh process that finds our own open dealer thread must drive it (read it every tick) or close
    it; leaving it alone blocks that dealer and one of the team's thread slots until the dealer idles it
    out (40 ticks in the sim, ~20 min at 30 s ticks). The previous process is a taker that opened it and
    bid (its decisions say so: a thread no taker decision names belongs to a `dealer buy` and is left alone)."""
    team = FakeTeam()
    _old_process_opens_and_bids(tmp_path, team)
    t, lines, _ = make_taker(tmp_path, team, FakePublic())
    before = len(team.reads)
    for k in range(1, 4):
        t.on_tick(at(team, TICK + k))
    driven = "thread 5000" in team.reads[before:]
    closed = ("close_thread", 5000) in team.sent
    assert driven or closed, (
        f"orphan thread 5000 never read nor closed in 3 ticks; convs={sorted(t.convs)}; "
        f"writes={team.sent}; last log: {lines[-1]}"
    )
