"""Bite A2: the cash floor and the hourly spend cap across accepts and standing dealer-thread bids.

An accept settles at the start of the next tick, and a dealer that takes our standing thread bid
settles it at once at the tick boundary; several dealers can do that in the same boundary. Every
check must therefore count, besides /api/me cash and the ledger: what this tick already sent, and
every bid still standing in a thread (each may fill at the next boundary).

Each test PASSES when the code is correct; a failure proves the bite.
"""

import pytest

from bazaar_agent.agents.dealer import BidPlan, Negotiation
from bazaar_agent.agents.desk import Conversation
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask
from tests.bites.kit import at, make_taker, standing_thread_bids, thread_bid
from tests.bites.strictness import STRICT

FLOOR = 270


def _accepted_totals(team, prices):
    return sum(prices[s[1]] for s in team.sent if s[0] == "accept")


@pytest.mark.xfail(strict=STRICT, reason="BITE X7: main only; fixed by #72 (drop the marker when it merges)")
def test_a2a_a_board_accept_and_a_dealer_bid_in_the_same_tick_keep_the_cash_floor(tmp_path):
    """Same tick: the taker accepts LAV-02 on El Rastro (10 + fee 2 = 12) and then opens abuela for
    LAV-08 and bids 18. /me was read before the accept, so the bid's check must still count the 12."""
    team = FakeTeam()
    team._me["cash"] = FLOOR + 12 + 18 - 1  # 299: room for one of them, not both
    t, lines, _ = make_taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}))
    t.on_tick(at(team, TICK))
    assert ("accept", 1) in team.sent  # the accept goes out first: the BID is what must give way
    committed = _accepted_totals(team, {1: 12}) + sum(standing_thread_bids(team).values())
    why = f"cash {team._me['cash']} - committed {committed} < cash_floor {FLOOR}; writes={team.sent}"
    assert team._me["cash"] - committed >= FLOOR, why


def _two_threads(team, t, *, a_price):
    """abuela (thread 40, LAV-08) already has our bid of `a_price` standing; chato (thread 41, LAV-05)
    has none yet. This tick abuela gets a_price+1 and chato its opening bid 20."""
    team.threads = [
        {"id": 40, "with": "abuela", "team": "t01", "status": "open"},
        {"id": 41, "with": "chato", "team": "t01", "status": "open"},
    ]
    team.offers = [thread_bid(77, 40, "LAV-08", a_price)]
    a = Conversation("abuela", "LAV-08", "uncommon", 52, "r", Negotiation(BidPlan(a_price, 1, 24)), 40, TICK - 1)
    a.neg.bids.append(a_price)
    b = Conversation("chato", "LAV-05", "uncommon", 40, "r", Negotiation(BidPlan(20, 1, 24)), 41, TICK - 1)
    t.convs = {"abuela": a, "chato": b}


@pytest.mark.xfail(strict=STRICT, reason="BITE X7: main only; fixed by #72 (drop the marker when it merges)")
def test_a2b_standing_thread_bids_together_stay_within_max_spend_per_game_hour(tmp_path):
    """Both dealers may take our bids at the same boundary. The ledger books a thread bid only when its
    deal settles, so the spend check must add the bids still standing in our threads."""
    team = FakeTeam()
    t, lines, ledger = make_taker(tmp_path, team, FakePublic(), threads=2, max_spend_per_game_hour=30)
    _two_threads(team, t, a_price=20)
    t.on_tick(at(team, TICK))
    standing = standing_thread_bids(team)
    worst = ledger.spent_since(0) + sum(standing.values())
    assert worst <= 30, f"if both dealers take our bids {standing}, spend {worst} > max_spend_per_game_hour 30"


@pytest.mark.xfail(strict=STRICT, reason="BITE X7: main only; fixed by #72 (drop the marker when it merges)")
def test_a2c_standing_thread_bids_together_keep_the_cash_floor(tmp_path):
    """Same two threads, cash-bound. Across ticks /api/me/offers lists our thread bids, so they count;
    within the tick, abuela's new 21 replaces her old 20 and chato's check must see 21, not 20."""
    team = FakeTeam()
    team._me["cash"] = FLOOR + 21 + 20 - 1  # 310
    t, lines, _ = make_taker(tmp_path, team, FakePublic(), threads=2)
    _two_threads(team, t, a_price=20)
    t.on_tick(at(team, TICK))
    standing = standing_thread_bids(team)
    total = sum(standing.values())
    why = f"if both dealers take our bids {standing}: cash {team._me['cash']} - {total} < {FLOOR}"
    assert team._me["cash"] - total >= FLOOR, why


def test_a2c_cross_tick_standing_bids_keep_the_cash_floor(tmp_path):
    """Control for the cross-tick case: abuela's 20 already stands (seen in /api/me/offers, not driven
    this tick); chato's opening 20 must not pass when both together breach the floor."""
    team = FakeTeam()
    team._me["cash"] = FLOOR + 20 + 20 - 1  # 309
    t, lines, _ = make_taker(tmp_path, team, FakePublic(), threads=2)
    _two_threads(team, t, a_price=20)
    del t.convs["abuela"]
    t.on_tick(at(team, TICK))
    standing = standing_thread_bids(team)
    assert team._me["cash"] - sum(standing.values()) >= FLOOR, f"{standing} breach the floor"


@pytest.mark.xfail(strict=STRICT, reason="BITE X18: main and #72 (settlement lag)")
def test_a2d_an_accept_from_the_previous_tick_still_settling_counts_against_the_floor(tmp_path):
    """Server-timing dependent. Tick T accepts LAV-02 (12). If /api/me at T+1 does not yet show the
    settlement (cash not debited, card not held), a second accept (LAV-08, 20 + fee 2 = 22) must still see
    the first one. The sim settles at the very start of T+1 (world.advance: settle_due first), so there
    this case cannot happen; it only bites if the real server settles lazily or /me lags."""
    team = FakeTeam()
    team._me["cash"] = FLOOR + 12 + 22 - 1  # 303
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})
    t, lines, ledger = make_taker(tmp_path, team, public)
    t.on_tick(at(team, TICK))
    assert ("accept", 1) in team.sent
    public.boards = {"rastro": [ask(2, "LAV-08", 20, asset=901)]}
    t.on_tick(at(team, TICK + 1))  # /me unchanged: still 303, LAV-02 not held yet
    spent = _accepted_totals(team, {1: 12, 2: 22})
    assert team._me["cash"] - spent >= FLOOR, (
        f"two accepts {[s for s in team.sent if s[0] == 'accept']} on consecutive ticks: "
        f"cash {team._me['cash']} - {spent} < {FLOOR} (ledger spend {ledger.spent_since(0)})"
    )
