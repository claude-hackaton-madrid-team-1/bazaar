"""Rival profiles and the opportunity scanner: offer lifecycles from the feed, scores, guardrails, the plan."""

import json

import pytest

from bazaar_agent import opportunities as op
from bazaar_agent import rivals as rv
from bazaar_agent.affinity import AffinityMap
from bazaar_agent.agents.market import BoardOffer, venues_from
from bazaar_agent.guardrails import Context, Guardrails, TradeBook
from bazaar_agent.strategy import build_market
from tests.agent_fakes import RASTRO
from tests.test_strategy import CATALOG, ME, PARAMS
from tests.test_trade_desk import known

VENUES = {v.id: v for v in venues_from({"venues": [RASTRO]})}
AMAP = AffinityMap({"t06": known("t06", LAV=0.5, LAT=1.6), "t18": known("t18", LAT=1.6), "t10": known("t10", LAV=1.6)})


def listed(oid, tick, team, give, want, expires=None, to=None):
    offer = {"id": oid, "maker": team, "to": to, "venue": "rastro", "thread": None, "give": give, "want": want}
    offer |= {"created_tick": tick, "expires_tick": expires if expires is not None else tick + 10}
    return {"id": 1000 + oid, "tick": tick, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


def ask(oid, tick, team, ref, price, asset, **kw):
    return listed(oid, tick, team, {"assets": [{"id": asset, "ref": ref}]}, {"cash": price}, **kw)


def bid(oid, tick, team, ref, price, **kw):
    return listed(oid, tick, team, {"cash": price}, {"types": [f"card:{ref}"]}, **kw)


def settle(eid, tick, frm, to, ref, asset, price):
    items = [{"id": asset, "ref": ref, "frm": frm, "to": to, "kind": "card"}]
    payload = {"items": items, "price": price, "persona": None, "parties": [frm, to], "settlement": eid}
    return {"id": 5000 + eid, "tick": tick, "type": "settlement", "payload": payload}


def cancelled(oid, tick):
    return {"id": 3000 + oid, "tick": tick, "type": "offer.cancelled", "payload": {"offer": oid, "venue": "rastro"}}


EVENTS = [
    ask(1, 10, "t06", "LAT-03", 9, 71),  # filled by t18 at 13
    settle(1, 13, "t06", "t18", "LAT-03", 71, 9),
    ask(2, 10, "t06", "LAV-02", 12, 72),  # cancelled at 12
    cancelled(2, 12),
    ask(3, 11, "t06", "LAV-02", 10, 72, expires=15),  # the same copy relisted, lapses at 15
    bid(4, 20, "t18", "LAT-09", 62, expires=60),  # still open at the end
    bid(5, 21, "t10", "LAV-09", 30, expires=40),  # filled by t06 selling at 25
    settle(2, 25, "t06", "t10", "LAV-09", 90, 30),
    ask(6, 30, "t06", "LAV-08", 15, 73, expires=80),  # still open: a snipe for us
    {"id": 9999, "tick": 50, "type": "clock", "payload": {}},
]


def test_every_offer_gets_its_outcome():
    rows = {r.id: r for r in rv.listings(EVENTS)}
    assert (rows[1].outcome, rows[1].end_tick, rows[1].taker, rows[1].fill_price) == ("filled", 13, "t18", 9)
    assert (rows[2].outcome, rows[2].end_tick) == ("cancelled", 12)
    assert (rows[3].outcome, rows[3].end_tick) == ("expired", 15)
    assert (rows[4].outcome, rows[5].outcome, rows[5].taker) == ("open", "filled", "t06")
    assert rows[6].outcome == "open"


def test_an_accept_on_the_last_tick_settles_the_next_one():
    events = [ask(1, 10, "t06", "LAT-03", 9, 71, expires=12), settle(1, 13, "t06", "t18", "LAT-03", 71, 9)]
    (row,) = rv.listings(events)
    assert row.outcome == "filled"
    late = [ask(1, 10, "t06", "LAT-03", 9, 71, expires=12), settle(1, 14, "t06", "t18", "LAT-03", 71, 9)]
    assert rv.listings(late)[0].outcome == "expired"  # a later sale came from elsewhere


def test_the_board_at_a_tick_is_what_was_listed_and_not_yet_over():
    rows = rv.listings(EVENTS)
    assert {r.id for r in rv.board_at(rows, 11)} == {1, 2, 3}  # 2 is cancelled at 12
    assert {r.id for r in rv.board_at(rows, 12)} == {1, 3}
    assert {r.id for r in rv.board_at(rows, 50)} == {4, 6}  # what opens the next day


def test_profiles_price_fill_take_and_reprice():
    rows = rv.listings(EVENTS)
    found = rv.profiles(rows, EVENTS, AMAP, CATALOG, exclude=["t01"])
    t06 = found["t06"]
    assert (t06.asks, t06.asks_filled, t06.cancels, t06.reprices) == (4, 1, 1, 1)
    assert t06.median_reprice_step == pytest.approx(-1 / 6, abs=1e-3)  # 12 → 10
    assert t06.top_set == "LAT" and t06.takes == 1  # it sold into t10's bid
    t18 = found["t18"]
    assert t18.takes == 1 and t18.take_latency == [3]
    # t06 values LAV-02 at 10 × 0.5: asking 12 then 10 is above its own value; LAT-03 at 16 × 1: 9 is below
    assert t06.ask_vs_own[0] == pytest.approx(9 / 16) and t06.sold_below_own == 1


def test_tags_name_cheap_sellers_and_overbidders():
    p = rv.TeamProfile("t09", ask_vs_own=[0.5, 0.6, 0.7], bid_vs_tape=[1.2, 1.3, 1.5], takes=3, take_latency=[1, 2, 2])
    assert p.tags == ("cheap seller", "overbidder", "fast taker")
    assert rv.TeamProfile("t09", ask_vs_own=[0.5]).tags == ()  # one data point is not a habit


def market():
    return build_market(ME, CATALOG, EVENTS, [])


def ctx(cash=400, trades=None):
    held = {}
    for a in ME["assets"]:
        if a.get("kind") == "card":
            held[a["ref"]] = held.get(a["ref"], 0) + 1
    return Context(cash, held, 50, 1.0, trades=trades)


def test_an_ask_below_our_value_is_a_buy_and_names_the_snipe():
    m, venue = market(), VENUES["rastro"]
    fair = BoardOffer(6, "rastro", "t06", "ask", "LAV-08", 15, 73, None, 80, 30)  # t06 values it 25 × 0.5 = 12.5
    s = op.score_offer(fair, m, ME, PARAMS, Guardrails(), AMAP, venue, ctx())
    assert s is not None and s.kind == "buy" and s.fee == 2 and s.allowed and s.tag == ""
    assert s.ours > 20 and s.theirs == pytest.approx(15 - 12.5)
    cheap = BoardOffer(10, "rastro", "t06", "ask", "LAV-08", 10, 73, None, 80, 30)  # below its own value
    s = op.score_offer(cheap, m, ME, PARAMS, Guardrails(), AMAP, venue, ctx())
    assert s is not None and s.tag == "snipe" and s.theirs == pytest.approx(-2.5)
    poor = op.score_offer(cheap, m, ME, PARAMS, Guardrails(), AMAP, venue, ctx(cash=280))
    assert poor is not None and poor.verdict.startswith("denied: cash 280 - 12 < cash_floor 270")


def test_a_bid_above_what_selling_costs_us_is_a_sell():
    o = BoardOffer(4, "rastro", "t18", "bid", "LAT-09", 62, None, None, 60, 20)
    s = op.score_offer(o, market(), ME, PARAMS, Guardrails(), AMAP, VENUES["rastro"], ctx(), ({"LAT-09": 55.0}, {}))
    assert s is not None and s.kind == "sell" and s.fee == 5 and s.tag == "overbid"  # above the tape (55)
    assert s.ours == pytest.approx(62 - 5 - 45)  # LAT-09: your_value 35 + the full LAT page bonus 10
    assert s.theirs == pytest.approx(112 - 62)


def test_what_is_not_ours_to_take_is_skipped():
    m = market()
    held_ask = BoardOffer(7, "rastro", "t06", "ask", "LAV-01", 5, 99, None, 80, 30)  # we hold LAV-01: W8's
    unheld_bid = BoardOffer(8, "rastro", "t18", "bid", "LAV-09", 50, None, None, 80, 30)  # we hold none
    unknown = BoardOffer(9, "rastro", "t18", "ask", "ZZZ-01", 5, 98, None, 80, 30)
    for o in (held_ask, unheld_bid, unknown):
        assert op.score_offer(o, m, ME, PARAMS, Guardrails(), AMAP, VENUES["rastro"], ctx()) is None


def test_the_counterparty_cap_reaches_the_scanner():
    o = BoardOffer(6, "rastro", "t06", "ask", "LAV-08", 15, 73, None, 80, 30)
    cap = Guardrails(max_counterparty_share=0.25)
    s = op.score_offer(o, market(), ME, PARAMS, cap, AMAP, VENUES["rastro"], ctx(trades=TradeBook({"t06": 40})))
    assert s is not None and "counterparty t06: 40 + 15" in s.verdict  # the ask, not ask + fee


def test_scan_ranks_allowed_first_and_flags_the_plan():
    from bazaar_agent.trade_desk import Trade

    rows = rv.listings(EVENTS)
    offers = op.offers_from(rv.board_at(rows, 50), "t01")
    plan = [
        Trade("bid", "t06", {"cash": 20}, {"cards": ["LAV-08"]}, ("LAV-08",), None, 20, 2, 30, 5, 1, 25, "", "uncommon")
    ]
    found = op.scan(offers, market(), ME, PARAMS, Guardrails(), AMAP, VENUES, ctx(), EVENTS, CATALOG, plan)
    assert [(f.kind, f.ref) for f in found] == [("buy", "LAV-08"), ("sell", "LAT-09")]
    assert found[0].plan == "bid LAV-08 with t06" and found[1].plan == ""
    assert (
        op.scan(offers, market(), ME, PARAMS, Guardrails(), AMAP, VENUES, ctx(), EVENTS, CATALOG, min_surplus=100) == []
    )


def test_replay_counts_what_others_took_and_what_was_left():
    rows = rv.listings(EVENTS)
    rp = op.replay(rows, market(), ME, PARAMS, Guardrails(), AMAP, VENUES, ctx(), EVENTS)
    refs = {(o.kind, o.ref): r.outcome for r, o in rp.worth_it}
    assert refs == {("buy", "LAV-02"): "expired", ("buy", "LAV-08"): "open", ("sell", "LAT-09"): "open"}
    assert rp.offers == 6 and rp.taken_by_others == 0 and rp.left_open == 3
    assert rp.left_surplus == rp.surplus > 0


def test_offers_from_never_shows_ours_or_offers_meant_for_another_team():
    rows = rv.listings(EVENTS + [ask(7, 40, "t01", "LAT-03", 9, 3), ask(8, 40, "t06", "LAT-04", 9, 74, to="t18")])
    ids = {o.id for o in op.offers_from(rv.board_at(rows, 45), "t01")}
    assert 7 not in ids and 8 not in ids and 6 in ids


def test_the_cli_scans_the_board_rebuilt_from_the_feed(tmp_path):
    from typer.testing import CliRunner

    from bazaar_agent.cli import app

    (tmp_path / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS))
    (tmp_path / "me.json").write_text(json.dumps({"body": ME}))
    (tmp_path / "catalog.json").write_text(json.dumps({"body": CATALOG}))
    (tmp_path / "venues.json").write_text(json.dumps({"body": {"venues": [RASTRO]}}))
    files = ["--events", str(tmp_path / "feed.jsonl"), "--me", str(tmp_path / "me.json")]
    files += ["--catalog", str(tmp_path / "catalog.json")]
    out = CliRunner().invoke(app, ["opportunities", *files, "--venues", str(tmp_path / "venues.json"), "--json"])
    assert out.exit_code == 0, out.output
    assert [(o["kind"], o["ref"]) for o in json.loads(out.output)] == [("buy", "LAV-08"), ("sell", "LAT-09")]
    out = CliRunner().invoke(app, ["rivals", *files, "--json"])
    assert out.exit_code == 0 and json.loads(out.output)["t06"]["asks"] == 4
