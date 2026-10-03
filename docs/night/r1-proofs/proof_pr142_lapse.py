"""r1 proofs for #142 (B14). Not committed."""

import pytest

from bazaar_agent.ticks import Clock
from tests.agent_fakes import bid
from tests.test_maker import maker
from tests.test_maker_lapsed_bids import Listing

H0, T0, TTL = 20.0, 2000, 40


def at(tick, seconds):
    return Clock(tick=tick, tick_seconds=seconds, next_tick_in=10.0, t_hours=H0 + (tick - T0) * seconds / 3600)


@pytest.mark.parametrize("seconds", [30.0, 15.0])
def test_a_pre_restart_bid_that_lapses_leaves_phantom_spend(tmp_path, seconds):
    """An old process booked bid 777's spend at T0; the new maker sees it lapse and refunds it with refund_row
    (60 s/tick). Its refund leaves the hour before its spend: the hour counts 65 that nobody spent."""
    team = Listing()
    m, _ = maker(tmp_path, team, live=True)
    spend_at = at(T0, seconds)
    m.ledger.record("spend", T0, spend_at.t_hours, 65, "LAV-09")  # the old process's post
    team.offers = [bid(777, "LAV-09", 65, created=T0, expires=T0 + TTL)]
    for tick in (T0 + 1, T0 + 20):
        team.now = at(tick, seconds)
        m.on_tick(team.now)
    team.offers = [o for o in team.offers if o["id"] != 777]
    for tick in (T0 + TTL, T0 + TTL + 1):
        team.now = at(tick, seconds)
        m.on_tick(team.now)
    rows = [e for e in m.ledger.entries() if e["kind"] == "spend" and e["tick"] == T0]
    assert sorted(e["price"] for e in rows) == [-65, 65]  # refunded once
    phantom = []
    for minute in range(0, 61):
        now = spend_at.t_hours + minute / 60
        net = sum(e["price"] for e in rows if e["t_hours"] > now - 1.0)
        if net:
            phantom.append(minute)
    assert phantom == [], f"bid 777 (lapsed unfilled) still counts 65 at minutes {phantom[0]}..{phantom[-1]} after its spend"


def test_two_maker_processes_both_refund_one_lapse(tmp_path):
    """Two maker processes on one shared ledger (deploy overlap, a local `--live` maker beside Railway's): both
    track the team's bids from /api/me/offers, both confirm the lapse, both book a refund."""
    team = Listing()
    a, _ = maker(tmp_path, team, live=True)
    b, _ = maker(tmp_path, team, live=True)
    b.ledger = a.ledger
    team.now = at(T0, 30.0)
    a.on_tick(team.now)  # A posts the bid (books 65)
    (o,) = [o for o in team.offers if o["give"].get("cash")]
    team.now = at(T0 + 1, 30.0)
    b.on_tick(team.now)  # B sees it open (and may repost nothing: it is listed)
    team.offers = [x for x in team.offers if x["id"] != o["id"]]
    for tick in (T0 + TTL, T0 + TTL + 1):
        team.now = at(tick, 30.0)
        a.on_tick(team.now)
        b.on_tick(team.now)
    refunds = [e for e in a.ledger.entries() if e["kind"] == "spend" and e["price"] < 0 and e["tick"] <= T0 + 1]
    assert len(refunds) == 1, f"one lapsed 65 bid refunded {len(refunds)}x: {refunds}"
