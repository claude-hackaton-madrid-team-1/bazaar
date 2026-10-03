"""Arbitrage arithmetic (fees per venue, crossings, duplicate buys) and the offline study on synthetic feeds."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent import arb
from bazaar_agent import arb_study as st
from bazaar_agent.agents.market import BoardOffer, Venue
from bazaar_agent.cli import app
from bazaar_agent.strategy import build_market

FIX = Path(__file__).parent / "fixtures" / "api"
RASTRO = Venue("rastro", "", 500, 1, "open", "board", 40, True)
V01 = Venue("v01", "t06", 50, 0, "open", "board", 0, False)  # Friday's t06 board, 50 bps
V02 = Venue("v02", "t12", 0, 0, "open", "board", 0, False)  # t12, 0 bps
V03 = Venue("v03", "t13", 100, 0, "open", "board", 0, False)  # t13, 100 bps
VENUES = {v.id: v for v in (RASTRO, V01, V02, V03)}


def ask(oid, ref, price, venue="rastro", maker="t06"):
    return BoardOffer(oid, venue, maker, "ask", ref, price, 1000 + oid, "common", None, None)


def bid(oid, ref, price, venue="v02", maker="t17"):
    return BoardOffer(oid, venue, maker, "bid", ref, price, None, None, None, None)


# ---------------------------------------------------------------- fees


@pytest.mark.parametrize(
    "venue, price, fee",
    [
        (RASTRO, 12, 2),  # 0.6 + 1 = 1.6 → 2 (the tape, 2026-10-02)
        (RASTRO, 65, 5),  # 3.25 + 1 → 5
        (RASTRO, 70, 5),  # 3.5 + 1 = 4.5 → 5
        (RASTRO, 20, 2),  # exactly 2: no rounding up
        (V01, 10, 1),  # 0.05 → 1 (ceil)
        (V01, 200, 1),  # exactly 1
        (V02, 99, 0),
        (V03, 50, 1),  # 0.5 → 1
        (V03, 100, 1),
    ],
)
def test_fee_per_venue(venue, price, fee):
    assert venue.fee(price) == fee
    assert arb.leg_cost(venue, price) == price + fee
    assert arb.leg_proceeds(venue, price) == price - fee


def test_a_crossing_pays_both_fees():
    (c,) = arb.crossings([ask(1, "MAL-04", 9), bid(2, "MAL-04", 16)], VENUES, min_net=1)
    assert (c.cost, c.proceeds, c.net, c.gross) == (11, 16, 5, 7)  # 9 + 2 on El Rastro, 16 − 0 on v02
    both_on_rastro = [ask(1, "MAL-04", 9), bid(2, "MAL-04", 16, venue="rastro")]  # 16 − 2 − 11 = 3
    assert [c.net for c in arb.crossings(both_on_rastro, VENUES, min_net=3)] == [3]
    assert not arb.crossings(both_on_rastro, VENUES, min_net=4)


def test_crossings_need_different_makers_known_venues_and_skip_ours():
    offers = [ask(1, "MAL-04", 5), bid(2, "MAL-04", 12, maker="t06"), bid(3, "MAL-04", 12, venue="v99")]
    assert not arb.crossings(offers, VENUES)  # same maker: a ring; v99: not a venue we may trade on
    offers = [ask(1, "MAL-04", 5), bid(2, "MAL-04", 12)]
    assert not arb.crossings(offers, VENUES, exclude={1})
    pseudonym = [ask(1, "MAL-04", 5, maker="m3950d43b"), bid(2, "MAL-04", 12)]
    assert arb.crossings(pseudonym, VENUES) and not arb.crossings(pseudonym, VENUES, known_makers=True)


def test_one_crossing_per_ask_and_per_bid():
    offers = [ask(1, "MAL-04", 5), ask(2, "MAL-04", 6), bid(3, "MAL-04", 14), bid(4, "MAL-04", 12)]
    found = arb.best_per_ask(arb.crossings(offers, VENUES))
    assert [(c.ask.id, c.bid.id, c.net) for c in found] == [(1, 3, 7), (2, 4, 4)]


def test_legs_at_our_values():
    worth = {"MAL-04": 2.25}.get  # a held common at 0.9: 10 × 0.9 × 0.25
    (c,) = arb.crossings([ask(1, "MAL-04", 9), bid(2, "MAL-04", 16)], VENUES, value=worth)
    assert c.legs == (-8.75, 13.75) and not c.both_legs_positive
    (safe,) = arb.crossings([ask(1, "MAL-04", 1), bid(2, "MAL-04", 16)], VENUES, value={"MAL-04": 5.0}.get)
    assert safe.legs == (2.0, 11.0) and safe.both_legs_positive


def test_duplicate_buys_take_the_cheapest_ask_per_held_card():
    offers = [
        ask(1, "LAV-09", 15, venue="v02"),  # a rare in a 1.6 set: one more copy 70 × 1.6 × 0.25 = 28
        ask(2, "LAV-09", 20, venue="v02"),
        ask(3, "LAV-10", 10, venue="v02"),  # not held: not a duplicate
        bid(4, "LAV-09", 30),
    ]
    found = arb.dup_buys(offers, VENUES, {"LAV-09": 1}, {"LAV-09": 28.0}.get, min_surplus=3)
    assert [(d.ask.id, d.cost, d.surplus) for d in found] == [(1, 15, 13.0)]
    assert not arb.dup_buys(offers, VENUES, {"LAV-09": 1}, {"LAV-09": 28.0}.get, min_surplus=14)


def market(held=None):
    me = json.loads((FIX / "get_api_me.team.json").read_text())["body"]
    cat = json.loads((FIX / "get_api_catalog.anon.json").read_text())["body"]
    me = {**me, "affinity": {"LAV": 1.6, "MAL": 0.9, "LAT": 0.5, "SAL": 0.7}}  # example multipliers
    if held is not None:
        me["assets"] = [{"kind": "card", "ref": r, "id": 9000 + i} for i, r in enumerate(held)]
    return build_market(me, cat, [], [])


def test_scan_values_one_more_copy_by_what_we_hold():
    m = market(["LAV-09", "MAL-04", "MAL-04"])
    value = arb.next_copy_values(m)
    assert (value("LAV-09"), value("MAL-04"), value("LAT-01"), value("XXX-01")) == (28.0, 0.9, 5.0, None)
    offers = [ask(1, "LAV-09", 15, venue="v02"), ask(2, "MAL-04", 1, venue="v02"), bid(3, "MAL-04", 9)]
    s = arb.scan(m, VENUES, offers, ours=set(), min_net=3, min_surplus=3)
    assert [(c.ref, c.net) for c in s.crossings] == [("MAL-04", 8)]
    assert [(d.ask.ref, d.surplus) for d in s.dups] == [("LAV-09", 13.0)]
    text = arb.render_scan(s, 12, 3, 3)
    assert "MAL-04" in text and "LAV-09" in text and "+13" in text


# ---------------------------------------------------------------- the study (synthetic feed)


def listed(eid, tick, oid, maker, give, want, venue="rastro", expires=None):
    offer = {"id": oid, "to": None, "give": give, "want": want, "maker": maker, "venue": venue, "thread": None}
    offer["expires_tick"] = expires if expires is not None else tick + 10
    payload = {"offer": offer, "venue": venue}
    return {"id": eid, "tick": tick, "type": "offer.listed", "actor": maker, "payload": payload}


def an_ask(eid, tick, oid, maker, ref, price, asset, venue="rastro", rarity="common"):
    give = {"cash": 0, "assets": [{"id": asset, "ref": ref, "kind": "card", "rarity": rarity}]}
    return listed(eid, tick, oid, maker, give, {"cash": price}, venue)


def a_bid(eid, tick, oid, maker, ref, price, venue="rastro"):
    return listed(eid, tick, oid, maker, {"cash": price}, {"types": [f"card:{ref}"]}, venue)


def feed():
    return [
        {"id": 1, "tick": 0, "type": "venue.opened", "payload": {"venue": "v02", "owner": "t12", "fee_bps": 0}},
        an_ask(2, 1, 10, "t06", "MAL-04", 9, 500),
        a_bid(3, 1, 11, "t17", "MAL-04", 16),  # crossed on El Rastro: 16 − 2 − (9 + 2) = 3
        a_bid(4, 1, 12, "t18", "MAL-04", 13, venue="v02"),  # on v02: 13 − 0 − 11 = 2
        an_ask(5, 2, 13, "t08", "LAV-09", 15, 501, rarity="rare"),
        {"id": 6, "tick": 3, "type": "offer.cancelled", "payload": {"offer": 11}},
        {
            "id": 7,
            "tick": 4,
            "type": "settlement",
            "payload": {"venue": "rastro", "price": 15, "items": [{"id": 501, "ref": "LAV-09", "to": "t02"}]},
        },
        {"id": 8, "tick": 6, "type": "venue.fee_changed", "payload": {"venue": "v02", "fee_bps": 100}},
    ]


def test_spans_rebuild_each_offers_life():
    rows = {s.offer.id: s for s in st.spans(feed())}
    assert (rows[11].start, rows[11].end, rows[11].how) == (1, 3, "cancelled")
    assert (rows[13].end, rows[13].how) == (4, "filled")
    assert rows[10].how == "open" and rows[10].offer.rarity == "common"


def test_fees_follow_the_feed():
    fees = st.venues_by_tick(feed(), 6)
    assert set(fees[0]) == {"rastro", "v02"} and fees[5]["v02"].fee_bps == 0 and fees[6]["v02"].fee_bps == 100


def test_crossing_study_counts_executable_and_scheduled_pairs():
    events = feed()
    rows, fees = st.spans(events), st.venues_by_tick(events, 6)
    c = st.crossing_study(rows, fees, min_net=3, label="fees")
    # ask 10 × bid 11 crosses by 3 at ticks 1 and 2; the bid is cancelled at 3, so only tick 1 is executable
    assert (c.pairs_overlapping, c.pairs_gross_positive, c.pairs_net_ok, c.executable, c.scheduled) == (2, 2, 1, 1, 1)
    assert c.net_scheduled == 3 and c.best[0].ticks == [1, 2] and c.best[0].executable == [1]
    zero = st.crossing_study(rows, fees, min_net=3, label="zero", zero=True)
    assert zero.pairs_net_ok == 2 and zero.scheduled == 1  # both pairs share the ask's accept slots


def test_dup_study_by_tier_and_the_render():
    s = st.study(feed(), min_net=3, min_surplus=3)
    top = next(d for d in s.dups if d.tier == 1.6 and d.copy == 2)
    assert top.hits == 1 and top.surplus == 11.0 and top.best["rare"] == 11.0  # 28 − (15 + 2 on El Rastro)
    text = st.render(s, min_net=3, min_surplus=3)
    assert "| 1.6 | 2 |" in text and "Crossings" in text


def test_private_dups_use_our_affinity_and_holdings():
    rows, fees = st.spans(feed()), st.venues_by_tick(feed(), 6)
    me = {"affinity": {"LAV": 1.6}, "assets": [{"kind": "card", "ref": "LAV-09"}]}
    ((span, surplus),) = st.private_dups(rows, fees, me, min_surplus=3)
    assert span.offer.ref == "LAV-09" and surplus == 28.0 - 17


def test_cli_study_writes_the_tables(tmp_path):
    stream = tmp_path / "stream.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in feed()) + "\n")
    out = tmp_path / "study.md"
    result = CliRunner().invoke(app, ["arb", "study", str(stream), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Arbitrage study" in out.read_text()


def test_cli_scan_reads_files_and_sends_nothing(tmp_path):
    boards = tmp_path / "boards"
    boards.mkdir()
    (boards / "rastro.json").write_text((FIX / "get_api_venues_rastro_offers.anon.json").read_text())
    events = tmp_path / "events.jsonl"
    events.write_text("")
    args = [
        "arb",
        "scan",
        "--me",
        str(FIX / "get_api_me.team.json"),
        "--catalog",
        str(FIX / "get_api_catalog.anon.json"),
    ]
    args += ["--venues", str(FIX / "get_api_venues.anon.json"), "--boards", str(boards), "--events", str(events)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Arbitrage scan" in result.output and "only reads" in result.output


def test_a_cancel_as_a_dict_one_offer_per_fill_and_a_closed_venue():
    events = [
        {"id": 1, "tick": 0, "type": "venue.opened", "payload": {"venue": "v02", "owner": "t12", "fee_bps": 0}},
        an_ask(2, 1, 10, "t06", "MAL-04", 9, 500),
        a_bid(3, 1, 11, "t02", "MAL-04", 9),  # t02 also bids 9: its accept of the ask must not end this bid
        a_bid(4, 1, 12, "t17", "MAL-04", 12),
        {"id": 5, "tick": 2, "type": "offer.cancelled", "payload": {"offer": {"id": 12}}},
        {
            "id": 6,
            "tick": 3,
            "type": "settlement",
            "payload": {"venue": "rastro", "price": 9, "items": [{"id": 500, "ref": "MAL-04", "to": "t02"}]},
        },
        {"id": 7, "tick": 4, "type": "venue.closed", "payload": {"venue": "v02"}},
    ]
    rows = {s.offer.id: s for s in st.spans(events)}
    assert (rows[12].end, rows[12].how) == (2, "cancelled")
    assert (rows[10].how, rows[11].how) == ("filled", "open")
    fees = st.venues_by_tick(events, 5)
    assert "v02" in fees[3] and "v02" not in fees[4]


def test_scan_lists_the_closest_below_each_bar_and_says_what_the_taker_would_do():
    m = market(["LAV-09", "MAL-04", "MAL-04"])
    offers = [
        ask(1, "MAL-04", 1, venue="v02"),
        bid(3, "MAL-04", 9),  # net +8: taken
        ask(4, "LAV-01", 9, venue="v02", maker="m3950d43b"),
        bid(5, "LAV-01", 12, maker="t18"),  # net +3 but the seller is a pseudonym
        ask(6, "SAL-01", 10, venue="v02"),
        bid(7, "SAL-01", 11, maker="t19"),  # net +1: below the bar
        ask(8, "LAV-09", 30, venue="v02"),  # duplicate: 28 − 30 = −2, below the bar
    ]
    s = arb.scan(m, VENUES, offers, ours=set(), min_net=3, min_surplus=3, near=5)
    assert [(c.ref, c.net) for c in s.crossings] == [("MAL-04", 8), ("LAV-01", 3)]
    assert [(c.ref, c.net) for c in s.near_crossings] == [("SAL-01", 1)]
    # LAV-09 at 30 against 28 (−2); MAL-04 held twice: a third copy at 1 P is worth 0.9 (−0.1)
    assert [(d.ask.ref, d.surplus) for d in s.near_dups] == [("MAL-04", -0.1), ("LAV-09", -2.0)] and s.dups == []
    assert [arb.why_not(c, 1.0) for c in s.crossings] == ["", "maker unknown"]
    text = arb.render_scan(s, None, 3, 3)
    assert "would take" in text and "maker unknown" in text and "Closest below the bar" in text
