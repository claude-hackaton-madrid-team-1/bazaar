"""Who to sell a card to (`buyers.rank_buyers`): willingness to pay from the tape, interest, need, rivals."""

from bazaar_agent import buyers as by

CATALOG = {
    "sets": [
        {
            "id": "SAL",
            "cards": [
                {"id": "SAL-01", "rarity": "common", "page": True, "book": 6},
                {"id": "SAL-02", "rarity": "common", "page": True, "book": 6},
                {"id": "SAL-03", "rarity": "uncommon", "page": True, "book": 14},
            ],
        },
        {"id": "LAV", "cards": [{"id": "LAV-04", "rarity": "common", "page": True, "book": 6}]},
    ]
}


def settle(sid, buyer, seller, ref, price, tick=10):
    return {
        "id": sid,
        "type": "settlement",
        "tick": tick,
        "payload": {
            "settlement": sid,
            "price": price,
            "items": [{"frm": seller, "to": buyer, "ref": ref, "kind": "card"}],
        },
    }


def board(*rows):
    return {"teams": [{"team": t, "rank": r} for t, r in rows]}


BOARD = board(("t14", 1), ("t13", 2), ("t18", 3), ("t12", 4), ("t10", 5), ("t17", 6), ("t01", 9), ("t16", 11))


def test_ranks_from_the_leaderboard_skip_bad_rows():
    assert by.leaderboard_ranks({"teams": [{"team": "t02", "rank": 7}, {"team": "x"}, "junk", {"rank": 3}]}) == {
        "t02": 7
    }
    assert by.leaderboard_ranks(None) == {}


def test_willingness_to_pay_prefers_the_same_set_and_rarity():
    events = [
        settle(1, "t16", "t03", "SAL-02", 9),
        settle(2, "t16", "t03", "SAL-01", 11),
        settle(3, "t16", "t04", "LAV-04", 30),
    ]
    w = by.willingness(by.team_buys(events, by.card_info(CATALOG)), "t16", by.card_info(CATALOG)["SAL-01"], {})
    assert w.price == 10 and "SAL common" in w.basis


def test_willingness_falls_back_to_rarity_then_market_then_book():
    info = by.card_info(CATALOG)
    buys = by.team_buys([settle(1, "t16", "t03", "LAV-04", 8)], info)
    assert by.willingness(buys, "t16", info["SAL-01"], {}).price == 8  # any common
    assert by.willingness(buys, "t09", info["SAL-01"], {"common": 7.0}).price == 7  # the market's commons
    assert by.willingness(buys, "t09", info["SAL-03"], {}).price == 14  # book


def test_a_team_that_already_holds_the_card_ranks_below_one_that_misses_it():
    rows = by.rank_buyers(
        "SAL-01",
        catalog=CATALOG,
        events=[settle(1, "t16", "t03", "SAL-02", 10), settle(2, "t08", "t03", "SAL-02", 10)],
        holders={"SAL-01": {"t16": 1}},
        interest={},
        ranks=by.leaderboard_ranks(board(("t16", 11), ("t08", 16), ("t01", 9))),
        us="t01",
        our_value=4.0,
    )
    assert [r.team for r in rows][:2] == ["t08", "t16"]
    assert rows[0].missing is True and rows[1].missing is False
    assert "holds 1" in rows[1].why


def test_a_rival_loses_to_an_equal_priced_buyer_elsewhere():
    events = [settle(1, "t12", "t03", "SAL-02", 10), settle(2, "t16", "t03", "SAL-02", 10)]
    rows = by.rank_buyers(
        "SAL-01",
        catalog=CATALOG,
        events=events,
        holders={},
        interest={},
        ranks=by.leaderboard_ranks(BOARD),
        us="t01",
        our_value=4.0,
    )
    assert rows[0].team == "t16"
    t12 = next(r for r in rows if r.team == "t12")
    assert t12.rival and "top 5" in t12.why


def test_a_team_just_above_us_is_a_rival_too():
    assert by.is_rival(6, 9, by.BuyerConfig()) == "3 ranks above us"
    assert by.is_rival(5, 30, by.BuyerConfig()) == "top 5"
    assert by.is_rival(11, 9, by.BuyerConfig()) is None
    assert by.is_rival(None, 9, by.BuyerConfig()) is None


def test_never_complete_a_top_five_page_below_one_and_a_half_times_our_value():
    holders = {"SAL-02": {"t12": 1}, "SAL-03": {"t12": 1}}
    kw = dict(catalog=CATALOG, events=[], holders=holders, interest={}, ranks=by.leaderboard_ranks(BOARD), us="t01")
    low = next(r for r in by.rank_buyers("SAL-01", our_value=6.0, price=8, **kw) if r.team == "t12")
    assert low.blocked and "completes" in low.why
    high = next(r for r in by.rank_buyers("SAL-01", our_value=6.0, price=9, **kw) if r.team == "t12")
    assert not high.blocked


def test_interest_raises_a_buyer_and_is_explained():
    rows = by.rank_buyers(
        "SAL-01",
        catalog=CATALOG,
        events=[],
        holders={},
        interest={"t08": {"SAL": 0.9}, "t16": {"SAL": 0.1}},
        ranks=by.leaderboard_ranks(board(("t08", 16), ("t16", 11), ("t01", 9))),
        us="t01",
        our_value=4.0,
    )
    assert rows[0].team == "t08" and "likes SAL 0.90" in rows[0].why


def test_we_never_rank_ourselves_and_an_unknown_card_ranks_nobody():
    kw = dict(
        catalog=CATALOG, events=[], holders={}, interest={}, ranks=by.leaderboard_ranks(BOARD), us="t01", our_value=1.0
    )
    assert all(r.team != "t01" for r in by.rank_buyers("SAL-01", **kw))
    assert by.rank_buyers("XXX-01", **kw) == []


def test_pick_addressee_skips_blocked_rivals_and_tried_prices():
    events = [settle(1, "t16", "t03", "SAL-02", 10), settle(2, "t08", "t03", "SAL-02", 9)]
    rows = by.rank_buyers(
        "SAL-01",
        catalog=CATALOG,
        events=events,
        holders={},
        interest={},
        ranks=by.leaderboard_ranks(BOARD),
        us="t01",
        our_value=4.0,
    )
    assert by.pick(rows) == "t16"
    assert by.pick(rows, tried={"t16"}) == "t08"
    assert by.pick([r for r in rows if r.rival]) is None  # only rivals: the ask stays public
    assert by.pick([]) is None
