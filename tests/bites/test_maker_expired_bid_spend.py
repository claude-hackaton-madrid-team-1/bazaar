"""BITE X15: a maker bid that expires unfilled keeps its spend in the ledger, and its repost books it again.

A bid's cash is booked as spend when it is posted (it can fill on any later tick). Only a cancel books the
refund; an offer that lapses at `expires_in_ticks` (40, `MakerConfig.offer_ttl_ticks`) is simply gone from
`/api/me/offers`, and the maker reposts the same target. One standing bid is then counted once per TTL:
3x per game hour at Saturday's 30 s ticks, 6x at Sunday's 15 s ticks, until `max_spend_per_game_hour`
(150) refuses the repost itself and every buy the taker and the dealer desk would make that hour.

The tests are strict xfails: they pass when the bug is fixed (then drop the marker).
"""

import pytest

from bazaar_agent.ticks import Clock
from tests.test_maker import NoAccept, maker, posted

SUNDAY_TICK_S = 15.0
TTL = 40  # MakerConfig.offer_ttl_ticks


def sunday(tick: int, t0: int, h0: float) -> Clock:
    return Clock(
        tick=tick, tick_seconds=SUNDAY_TICK_S, next_tick_in=10.0, t_hours=h0 + (tick - t0) * SUNDAY_TICK_S / 3600
    )


@pytest.mark.xfail(strict=True, reason="BITE X15: expired bids are never refunded; reposts book the spend again")
def test_one_standing_bid_reposted_after_expiry_counts_once_in_the_hour(tmp_path):
    team = NoAccept()  # the strategy's one bid target: LAV-09 at 65 (plus two asks)
    m, _ = maker(tmp_path, team, live=True)
    t0, h0 = 2000, 20.0
    for cycle in range(3):  # 3 TTLs = 30 min on Sunday: well inside one game hour
        team.offers = []  # the previous bid lapsed unfilled: the server no longer lists it
        m.on_tick(sunday(t0 + cycle * TTL, t0, h0))
    now = sunday(t0 + 2 * TTL, t0, h0)
    # Only one bid of 65 was ever open at a time: the hour's committed spend is 65.
    assert m.ledger.spent_since(now.t_hours - 1.0) == 65


@pytest.mark.xfail(strict=True, reason="BITE X15: phantom spend from expired bids blocks the repost (and every buy)")
def test_the_bid_target_stays_on_the_board_all_hour(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team, live=True)
    t0, h0 = 2000, 20.0
    for cycle in range(5):  # 50 min on Sunday
        team.offers = []
        m.on_tick(sunday(t0 + cycle * TTL, t0, h0))
    bids = [p for p in posted(team) if p[1].get("cash")]
    assert len(bids) == 5, [line for line in lines if "max_spend_per_game_hour" in line][:1]
