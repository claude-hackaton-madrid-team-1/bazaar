"""Bite A3: the spend ledger when the DEALER accepts our standing thread bid (not our accept).

A thread bid is not booked when it is said (only `seller.post` books board bids); the deal is booked
once by `Taker._finished` when the thread turns `deal`. With what price, and only once?

Each test PASSES when the code is correct; a failure proves the bite.
"""

import pytest

from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker
from tests.bites.strictness import STRICT


def _spend_rows(ledger):
    return [e for e in ledger.entries() if e["kind"] == "spend"]


def test_a3_dealer_takes_our_bid_booked_once_at_our_bid(tmp_path):
    team = FakeTeam()
    t, _, ledger = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))  # opens 5000, bids 18
    t.on_tick(at(team, TICK + 1))  # no answer yet: bids 19
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18), ("say", 5000, 19)]
    assert _spend_rows(ledger) == []  # a thread bid is not booked when said
    dealer_took_our_bid(team, 5000, 5003, "LAV-08", 19)
    t.on_tick(at(team, TICK + 2))
    t.on_tick(at(team, TICK + 3))
    rows = [(e["price"], e["item"]) for e in _spend_rows(ledger)]
    assert rows == [(19, "LAV-08")], f"expected one spend row of 19, got {rows}"


class _LostReply(FakeTeam):
    """The bid reaches the game but the reply is lost (connection drop): the SDK raises `network`."""

    def __init__(self, fail_on_say, **kw):
        super().__init__(**kw)
        self.fail_on_say, self.says = fail_on_say, 0

    def say(self, tid, text="", price=None, offer=None, topic=None):
        self.says += 1
        out = super().say(tid, text, price=price, offer=offer, topic=topic)
        if self.says == self.fail_on_say:
            raise BazaarError("network", "POST /api/threads/5000/messages: connection reset", 0)
        return out


@pytest.mark.parametrize(
    "offer_in_messages",
    [
        pytest.param(
            True,
            id="thread-shows-settled-offer",
            marks=pytest.mark.xfail(strict=STRICT, reason="BITE X7c: main; fixed by #72"),
        ),
        pytest.param(
            False, id="thread-without-offer", marks=pytest.mark.xfail(strict=STRICT, reason="BITE X7c: main and #72")
        ),
    ],
)
def test_a3_dealer_takes_a_bid_whose_reply_was_lost_is_still_booked(tmp_path, offer_in_messages):
    team = _LostReply(fail_on_say=1)
    t, lines, ledger = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))  # opens 5000; the 18 lands but the reply is lost
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18, offer_in_messages=offer_in_messages)
    t.on_tick(at(team, TICK + 1))
    rows = [(e["price"], e["item"]) for e in _spend_rows(ledger)]
    assert rows == [(18, "LAV-08")], f"deal at 18 booked as {rows}; log: {[x for x in lines if 'thread 5000' in x]}"


@pytest.mark.parametrize(
    "offer_in_messages",
    [
        pytest.param(
            True,
            id="thread-shows-settled-offer",
            marks=pytest.mark.xfail(strict=STRICT, reason="BITE X7c: main; fixed by #72"),
        ),
        pytest.param(
            False, id="thread-without-offer", marks=pytest.mark.xfail(strict=STRICT, reason="BITE X7c: main and #72")
        ),
    ],
)
def test_a3_a_later_bid_whose_reply_was_lost_is_booked_at_its_price(tmp_path, offer_in_messages):
    team = _LostReply(fail_on_say=2)
    t, lines, ledger = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))  # bids 18
    t.on_tick(at(team, TICK + 1))  # bids 19: lands, reply lost
    dealer_took_our_bid(team, 5000, 5003, "LAV-08", 19, offer_in_messages=offer_in_messages)
    t.on_tick(at(team, TICK + 2))
    rows = [(e["price"], e["item"]) for e in _spend_rows(ledger)]
    assert rows == [(19, "LAV-08")], f"deal at 19 booked as {rows}"
