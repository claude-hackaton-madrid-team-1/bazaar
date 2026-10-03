"""B14 (bite X15): a maker bid that lapses unfilled gives its spend back, dated at the spend; every doubt keeps it.

The flipped bite tests live in `tests/bites/test_maker_expired_bid_spend.py`. Here: a bid gone before its expiry
(filled, or cancelled by whoever booked the refund), a card that came (now, on the confirming tick, or in the
feed), the exact dating, a bid from before a restart, the kill switch, a dry run, and the maker's own cancels.
"""

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.ticks import Clock
from tests.agent_fakes import bid
from tests.test_maker import NoAccept, maker
from tests.test_strategy import EVENTS

TTL = 40
H0, T0 = 20.0, 2000


def at(tick: int, seconds: float = 30.0) -> Clock:
    return Clock(tick=tick, tick_seconds=seconds, next_tick_in=10.0, t_hours=H0 + (tick - T0) * seconds / 3600)


class Listing(NoAccept):
    """Lists every bid the maker posts as open (the server's view), until a test takes it away."""

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        posted = super().list_offer(give, want, venue, to, expires_in_ticks)
        if give.get("cash"):
            ref = str((want.get("cards") or want.get("types") or ["?"])[0]).split(":")[-1]
            tick = self.now.tick
            self.offers.append(bid(posted["id"], ref, give["cash"], created=tick, expires=tick + expires_in_ticks))
        return posted

    def cancel(self, offer_id):
        self.offers = [o for o in self.offers if o["id"] != offer_id]
        return super().cancel(offer_id)


def spend_rows(m):
    return [e for e in m.ledger.entries() if e["kind"] == "spend"]


def run(m, team, ticks, seconds=30.0):
    for tick in ticks:
        team.now = at(tick, seconds)
        m.on_tick(team.now)


def posted_bid(team):
    """The maker's one bid target (LAV-09 at 65) posted on the first tick."""
    (o,) = [o for o in team.offers if o["give"].get("cash")]
    return o


def test_a_bid_that_lapses_is_refunded_one_tick_later_dated_at_its_spend(tmp_path):
    team = Listing()
    m, lines = maker(tmp_path, team, live=True)
    run(m, team, [T0])
    o = posted_bid(team)
    run(m, team, [T0 + 10, T0 + 20])
    team.offers.remove(o)  # lapsed at its expiry: the server no longer lists it
    run(m, team, [T0 + TTL])  # seen gone: confirmed next tick (an accept settles on the next tick)
    assert sum(e["price"] for e in spend_rows(m)) == 130  # the repost + the lapse being confirmed
    run(m, team, [T0 + TTL + 1])
    spend, repost, refund = spend_rows(m)
    assert refund["price"] == -65 and (refund["tick"], refund["t_hours"]) == (spend["tick"], spend["t_hours"])
    assert m.ledger.spent_since(team.now.t_hours - 1.0) == 65
    assert any("lapsed unfilled: refunded" in line for line in lines)


def test_a_bid_gone_before_its_expiry_filled_or_was_cancelled_elsewhere(tmp_path):
    """The taker's `_withdraw` and `bazaar flatten` book their own refund; a fill keeps the spend."""
    team = Listing()
    m, _ = maker(tmp_path, team, live=True)
    run(m, team, [T0])
    team.offers.remove(posted_bid(team))
    run(m, team, [T0 + 5, T0 + 6, T0 + 7])
    assert min(e["price"] for e in spend_rows(m)) > 0  # no refund row


def _lapse_then(tmp_path, *, card_now=False, card_later=False, feed=False):
    team = Listing()
    events = list(EVENTS)
    m, _ = maker(tmp_path, team, live=True)
    m.feed = MarketFeed(lambda n: list(events))  # the public live window the maker reads every tick
    run(m, team, [T0])
    team.offers.remove(posted_bid(team))
    card = {"id": 9001, "kind": "card", "ref": "LAV-09", "rarity": "rare", "your_value": 70.0}
    if card_now:
        team._me["assets"].append(card)
    if feed:
        items = [{"id": 9001, "to": "t01", "frm": "t07", "ref": "LAV-09", "kind": "card"}]
        payload = {"kind": "trade", "items": items, "price": 65, "venue": "rastro", "parties": ["t01", "t07"]}
        events.append({"id": 99_999, "tick": T0 + TTL, "type": "settlement", "actor": "", "payload": payload})
    run(m, team, [T0 + TTL])
    if card_later:
        team._me["assets"].append(card)
    run(m, team, [T0 + TTL + 1])
    return m


def first_bid_refunds(m):
    """Refunds of the bid posted at T0 (the maker's later cancel of its repost is dated at the repost)."""
    return [e for e in spend_rows(m) if e["price"] < 0 and e["tick"] == T0]


def test_a_lapse_whose_card_came_is_a_fill(tmp_path):
    assert first_bid_refunds(_lapse_then(tmp_path, card_now=True)) == []


def test_a_card_that_comes_on_the_confirming_tick_cancels_the_refund(tmp_path):
    assert first_bid_refunds(_lapse_then(tmp_path, card_later=True)) == []


def test_a_settlement_of_the_card_in_the_feed_cancels_the_refund(tmp_path):
    assert first_bid_refunds(_lapse_then(tmp_path, feed=True)) == []


def test_without_a_card_or_a_settlement_the_lapse_is_refunded(tmp_path):
    assert [e["price"] for e in first_bid_refunds(_lapse_then(tmp_path))] == [-65]


def test_a_bid_from_before_a_restart_is_refunded_at_the_conservative_date(tmp_path):
    """Seen open on the first tick (no spend of ours remembered): `refund_row` dates it from its created tick
    at the slowest pace, never after the spend."""
    team = Listing()
    old = bid(777, "LAV-09", 65, created=T0 - 10, expires=T0 + 5)
    team.offers = [old]
    m, _ = maker(tmp_path, team, live=True)
    run(m, team, [T0])
    team.offers.remove(old)
    run(m, team, [T0 + 6, T0 + 7])
    (refund,) = [e for e in spend_rows(m) if e["price"] < 0]
    assert refund["tick"] == T0 - 10 and refund["t_hours"] <= at(T0 - 10).t_hours


def test_a_bid_gone_under_the_kill_switch_is_not_refunded(tmp_path, monkeypatch):
    """The runbook is PAUSE, then `bazaar flatten`, which cancels our bids and books their refunds: under the
    switch a bid seen gone is never refunded again (a true lapse then over-counts, fail safe; review of #142)."""
    team = Listing()
    m, _ = maker(tmp_path, team, live=True)
    run(m, team, [T0])
    team.offers.remove(posted_bid(team))
    monkeypatch.setattr("bazaar_agent.agents.maker.kill_switch", lambda rules, path=None: ("trading_enabled = false",))
    run(m, team, [T0 + TTL, T0 + TTL + 1])
    assert [e["price"] for e in spend_rows(m)] == [65]  # nothing reposted while held, nothing refunded twice


def test_a_bid_someone_else_cancelled_in_its_last_ticks_is_refunded_once(tmp_path):
    """`bazaar flatten` (or the desk) cancels the bid at E-1 and books its refund; the feed shows the cancel, so
    the maker's lapse check does not refund it again (review of #142: the hour read -65)."""
    from bazaar_agent.guardrails import refund_row
    from tests.test_strategy import EVENTS

    team = Listing()
    events = list(EVENTS)
    m, _ = maker(tmp_path, team, live=True)
    m.feed = MarketFeed(lambda n: list(events))
    run(m, team, [T0])
    o = posted_bid(team)
    run(m, team, [T0 + TTL - 2])
    team.offers.remove(o)
    c = at(T0 + TTL - 1)
    m.ledger.record(*refund_row(65, "LAV-09", o["created_tick"], c.tick, c.t_hours, c.max_tick_seconds))
    cancel = {"offer": o["id"], "venue": "rastro"}
    events.append({"id": 99_997, "tick": T0 + TTL - 1, "type": "offer.cancelled", "actor": "", "payload": cancel})
    run(m, team, [T0 + TTL, T0 + TTL + 1, T0 + TTL + 2])
    assert [e["price"] for e in spend_rows(m) if e["price"] < 0] == [-65]


def test_a_settlement_event_with_an_odd_tick_does_not_stop_the_lapse_check(tmp_path):
    from tests.test_strategy import EVENTS

    team = Listing()
    events = list(EVENTS) + [{"id": 99_998, "tick": "abc", "type": "settlement", "payload": {"items": []}}]
    m, _ = maker(tmp_path, team, live=True)
    m.feed = MarketFeed(lambda n: list(events))
    run(m, team, [T0])
    team.offers.remove(posted_bid(team))
    run(m, team, [T0 + TTL, T0 + TTL + 1])
    assert -65 in [e["price"] for e in spend_rows(m)]  # the lapse was refunded; the odd event was skipped


def test_a_dry_run_books_nothing(tmp_path):
    team = Listing()
    team.offers = [bid(777, "LAV-09", 65, created=T0 - 10, expires=T0 + 5)]
    m, _ = maker(tmp_path, team, live=False)
    run(m, team, [T0])
    team.offers = []
    run(m, team, [T0 + 6, T0 + 7])
    assert spend_rows(m) == []


def test_the_makers_own_cancel_of_a_bid_it_posted_refunds_at_the_spend_and_only_once(tmp_path):
    """A reprice cancels the old bid: its refund is dated at its spend (not `refund_row`'s 60 s pace, which at
    15 s ticks dated it ~30 min early and let it count again), and the lapse check never refunds it twice."""
    team = Listing()
    m, _ = maker(tmp_path, team, live=True)
    run(m, team, [T0], seconds=15.0)
    o = posted_bid(team)
    o["give"]["cash"] = 80  # its target is now 65 < 80: the maker repricing cancels it
    run(m, team, [T0 + 30], seconds=15.0)
    team.offers = [x for x in team.offers if x["id"] != o["id"]]
    run(m, team, [T0 + TTL, T0 + TTL + 1, T0 + TTL + 2], seconds=15.0)
    rows = spend_rows(m)
    refunds = [e for e in rows if e["price"] < 0]
    assert len(refunds) == 1 and (refunds[0]["tick"], refunds[0]["t_hours"]) == (rows[0]["tick"], rows[0]["t_hours"])


def test_a_bid_listed_again_on_the_confirming_tick_is_alive_and_not_refunded(tmp_path):
    """One read missed it (the listing flickered): back on the next tick, it keeps its spend."""
    team = Listing()
    m, lines = maker(tmp_path, team, live=True)
    run(m, team, [T0])
    o = posted_bid(team)
    team.offers.remove(o)
    run(m, team, [T0 + TTL])
    team.offers.insert(0, o)
    run(m, team, [T0 + TTL + 1, T0 + TTL + 2])
    assert not any("lapsed unfilled" in line for line in lines)
    assert len(first_bid_refunds(m)) <= 1  # the maker may cancel one of its two bids as a duplicate: one refund
