from bazaar_agent import intel


def opened(eid, thread, team, topic, tick=0, dealer="abuela"):
    return {
        "id": eid,
        "tick": tick,
        "type": "thread.opened",
        "payload": {"thread": thread, "kind": "persona", "team": team, "with": dealer, "topic": topic},
    }


def msg(eid, thread, team, sender, give_cash=0, want_cash=0, final=False, tick=0):
    offer = {
        "id": eid,
        "maker": sender,
        "give": {"cash": give_cash, "assets": [], "types": []},
        "want": {"cash": want_cash, "assets": [], "types": []},
        "final": final,
    }
    return {
        "id": eid,
        "tick": tick,
        "type": "thread.message",
        "payload": {
            "thread": thread,
            "kind": "persona",
            "sender": sender,
            "team": team,
            "with": "abuela",
            "offer": offer,
        },
    }


def settle(eid, sid, frm, to, ref, price, tick, kind="pack", persona="abuela", asset_id=900):
    return {
        "id": eid,
        "tick": tick,
        "type": "settlement",
        "payload": {
            "settlement": sid,
            "tick": tick,
            "kind": "trade",
            "parties": [frm, to],
            "venue": None,
            "persona": persona,
            "fee": 0,
            "price": price,
            "items": [{"id": asset_id, "kind": kind, "ref": ref, "frm": frm, "to": to}],
        },
    }


PACK = {"buy": {"pack": "sobre_barrio"}}

EVENTS = [
    opened(1, 10, "t05", PACK, tick=1),
    msg(2, 10, "t05", "t05", give_cash=12, tick=1),
    msg(3, 10, "t05", "abuela", want_cash=24, tick=1),
    msg(4, 10, "t05", "t05", give_cash=15, tick=2),
    msg(5, 10, "t05", "abuela", want_cash=20, final=True, tick=2),
    settle(6, 1, "abuela", "t05", "sobre_barrio", 20, tick=3),
    opened(7, 11, "t06", {"buy": {"card": "LAV-07"}}, tick=2),
    msg(8, 11, "t06", "t06", give_cash=9, tick=2),
    msg(9, 11, "t06", "abuela", want_cash=29, tick=2),
    opened(10, 12, "t06", {"sell": {"assets": [171]}}, tick=3),
    msg(11, 12, "t06", "abuela", give_cash=13, tick=3),
    settle(12, 2, "t06", "abuela", "LAT-03", 13, tick=4, kind="card", asset_id=171),
    {
        "id": 13,
        "tick": 4,
        "type": "offer.listed",
        "actor": "t06",
        "payload": {
            "venue": "rastro",
            "offer": {"id": 123, "give": {"assets": [{"ref": "LAT-05", "set": "LAT"}]}, "want": {"cash": 10}},
        },
    },
]


def test_tape_reads_buyer_seller_and_price():
    prints = intel.tape(EVENTS)
    assert [(p.buyer, p.seller, p.ref, p.price) for p in prints] == [
        ("t05", "abuela", "sobre_barrio", 20),
        ("abuela", "t06", "LAT-03", 13),
    ]


def test_dealer_threads_rebuild_quotes_finals_and_fills():
    threads = {t.thread: t for t in intel.dealer_threads(EVENTS)}
    pack = threads[10]
    assert (pack.team_prices, pack.dealer_prices, pack.final_price, pack.fill_price) == (
        [12, 15],
        [24, 20],
        20,
        20,
    )
    assert (pack.opening_ask, pack.steps) == (24, 2)
    assert threads[11].fill_price is None  # LAV-07 never settled
    assert (threads[12].side, threads[12].fill_price) == ("sell", 13)  # matched by asset id


def test_curve_summary_groups_by_dealer_and_item():
    summary = {s.item: s for s in intel.curve_summary(intel.dealer_threads(EVENTS))}
    assert (summary["sobre_barrio"].fills, summary["sobre_barrio"].fill_median) == (1, 20.0)
    assert summary["LAV-07"].fills == 0


def test_team_flows_infer_set_interest_from_what_teams_chase_and_dump():
    flows = {f.team: f for f in intel.team_flows(EVENTS)}
    assert (flows["t05"].buys, flows["t05"].spent, flows["t05"].avg_pack_price) == (1, 20, 20.0)
    t06 = flows["t06"]
    assert (t06.sells, t06.earned, t06.listings) == (1, 13, 1)
    assert t06.set_interest["LAV"] == 1 and t06.set_interest["LAT"] == -2
    assert t06.top_set == "LAV"


def test_order_book_resolves_pseudonyms_to_teams():
    board = [
        {
            "id": 123,
            "maker": "mdadbc40c",
            "give": {"cash": 0, "assets": [{"ref": "LAT-05"}]},
            "want": {"cash": 10, "types": []},
            "expires_tick": 21,
        },
        {
            "id": 124,
            "maker": "zz",
            "give": {"cash": 30, "assets": []},
            "want": {"cash": 0, "types": ["card:LAV-09"]},
        },
        {"id": 125, "maker": "zz", "give": {"cash": 0}, "want": {"cash": 0}},
    ]
    lines = intel.order_book(board, intel.listed_makers(EVENTS))
    assert [(b.card, b.side, b.price, b.maker) for b in lines] == [
        ("LAT-05", "ask", 10, "t06"),
        ("LAV-09", "bid", 30, "zz"),
    ]


def test_set_of():
    assert (intel.set_of("LAV-07"), intel.set_of("sobre_barrio"), intel.set_of(None)) == ("LAV", None, None)
