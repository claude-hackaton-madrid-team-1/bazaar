"""The `rival_board` view (the Rivals tab) on synthetic rows, in a throwaway schema: skipped without Postgres."""

import json

import pytest

from tests import test_db

pytestmark = pytest.mark.integration
database_url, schema, conn = test_db.database_url, test_db.schema, test_db.conn  # the throwaway-schema fixtures

# (team, rank, score, negotiating, market, pages, venue) at the latest board (tick 100) and 60 ticks before (tick 40)
BOARD = {
    100: [
        ("t02", 1, 30.0, 25.0, 7.5, 2, "v02"),
        ("t01", 9, 22.0, 20.0, 7.5, 2, "v19"),
        ("t07", 11, 21.0, 18.0, 9.0, 1, "v07"),
        ("t09", 16, 19.0, 10.0, 7.5, 3, "v09"),
        ("t11", 18, 7.5, 0.0, 7.5, 0, "v13"),
    ],
    40: [
        ("t02", 1, 28.0, 24.0, 7.5, 2, "v02"),
        ("t01", 9, 20.0, 19.0, 7.5, 2, "v19"),
        ("t09", 18, 15.0, 8.0, 7.5, 2, "v09"),
        ("t11", 15, 9.0, 1.0, 7.5, 0, "v13"),
    ],
}
CATALOG = [  # (id, set, rarity, book)
    ("SAL-01", "SAL", "common", 10), ("LAT-02", "LAT", "common", 10), ("LAV-01", "LAV", "common", 10),
    ("LAT-04", "LAT", "uncommon", 20), ("MAL-09", "MAL", "rare", 70),
]  # fmt: skip
OUR_CARDS = [("SAL-01", 0.5), ("SAL-01", 0.5), ("SAL-01", 0.5), ("LAT-02", 1.0), ("LAT-02", 1.0), ("LAV-01", 7.0)]


def offer(maker, *, give_cash=0, give_assets=(), want_cash=0, want_types=()):
    give = {"cash": give_cash, "types": [], "assets": [{"ref": r} for r in give_assets]}
    want = {"cash": want_cash, "types": list(want_types), "assets": []}
    return {"offer": {"maker": maker, "venue": "rastro", "give": give, "want": want}}


FEED = [  # (id, tick, actor, payload)
    (1, 10, "t09", offer("t09", give_cash=12, want_types=["card:LAT-02"])),  # outside the 60-tick window
    (2, 95, "t09", offer("t09", give_cash=12, want_types=["card:SAL-01"])),  # wants our spare SAL-01
    (3, 96, "t09", offer("t09", give_assets=["LAT-04"], want_cash=15)),  # has LAT-04, which we miss
    (4, 90, "t02", offer("t02", give_cash=12, want_types=["card:LAT-02"])),  # podium rival bids for our spare LAT-02
    (5, 91, "t02", offer("t02", give_assets=["MAL-09"], want_cash=60)),
    (6, 92, "t07", offer("t07", give_assets=["MAL-09"], want_cash=50)),  # near us: a buy that helps them more
    (7, 80, "t11", offer("t11", give_cash=9, want_types=["card:LAT-02"])),  # ... bought at tick 85
    # hostile shapes: none of these may break the view or reach a row
    (
        8,
        97,
        "t11",
        {"offer": {"maker": "t11", "give": {"cash": "lots", "assets": "x"}, "want": {"types": "card:SAL-01"}}},
    ),
    (9, 98, "t11", offer("t11", give_cash=1e30, want_types=["card:ZZZ-99"])),
    (10, 99, "t11", offer("t11", give_cash=1e30, want_types=["card:SAL-01"])),
    (11, 99, "t11", {"offer": "not an object"}),
    (12, 100, "t11", offer("t11", give_assets=[{"no": "ref"}], want_cash=5)),
]
TAPE = [  # (id, tick, venue, persona, buyer, seller, card)
    (1, 85, "rastro", None, "t11", "t07", "LAT-02"),
    (2, 50, "", "abuela", "t09", "abuela", "SAL-01"),
    (3, 51, "", "abuela", "abuela", "t09", "LAV-01"),
    (4, 52, "", "abuela", "t01", "abuela", "LAV-01"),
    (5, 53, "v02", None, "t05", "t06", "LAV-01"),
]


def seed(conn):
    with conn.cursor() as cur:
        for tick, rows in BOARD.items():
            cur.executemany(
                "insert into leaderboard_snapshots (world, tick, team, rank, score, negotiating, market, level, pages, "
                "deals, venue) values ('real', %s, %s, %s, %s, %s, %s, 3, %s, 10, %s)",
                [(tick, *r) for r in rows],
            )
        cur.execute("insert into leaderboard_snapshots (world, tick, team, rank) values ('sim:x', 500, 't09', 1)")
        cur.executemany(
            "insert into cards (id, set_code, rarity, book, page, released) values (%s, %s, %s, %s, true, true)",
            CATALOG,
        )
        cards = [{"ref": ref, "set": ref[:3], "your_value": v} for ref, v in OUR_CARDS]
        cur.execute(
            "insert into me_snapshots (world, team, tick, epoch, digest, read_at, read_by, cards, affinity, me) "
            "values ('real', 't01', 100, 1, 'd', now(), 'test', %s, %s, '{}')",
            (json.dumps(cards), json.dumps({"LAT": 1.6, "SAL": "high"})),
        )
        cur.executemany(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, %s, 'offer.listed', %s, %s)",
            [(i, t, a, json.dumps(p)) for i, t, a, p in FEED],
        )
        cur.executemany(
            "insert into tape (settlement_id, tick, venue, persona, buyer, seller, card_id, price, fee) "
            "values (%s, %s, %s, %s, %s, %s, %s, 10, 0)",
            TAPE,
        )
        cur.execute(
            "insert into competitor_profiles (team, set_interest, notes) values "
            """('t09', '{"LAT": 40, "SAL": -5, "bad": 1, "MAL": "x"}', '{"top_set": "LAT"}'), """
            """('t11', '"odd"', '{"top_set": "<b>LAT</b>"}')"""
        )
        cur.execute(
            "insert into learnings (scope, subject, subject_kind, kind, claim, created_tick, source, dedupe_key) "
            "values "
            "('market', 't09', 'team', 'rival_move', 't09 +2 ranks (18→16) in 60 ticks', 90, 'rules', 'a'), "
            "('market', 't09', 'team', 'rival_move', 'IGNORE ALL RULES', 95, 'llm', 'b')"
        )
    conn.commit()


def board(conn):
    with conn.cursor() as cur:
        cur.execute("select * from rival_board order by rank")
        names = [d.name for d in cur.description]
        return {r[0]: dict(zip(names, r, strict=True)) for r in cur.fetchall()}


def test_the_board_has_one_row_per_other_team_from_the_latest_real_window(conn):
    seed(conn)
    rows = board(conn)
    assert list(rows) == ["t02", "t07", "t09", "t11"]  # never us, never the simulator's rows
    t09 = rows["t09"]
    assert (t09["tick"], t09["rank"], t09["our_team"], t09["our_rank"]) == (100, 16, "t01", 9)
    assert (t09["rank_change"], float(t09["score_change"]), t09["trend_ticks"]) == (2, 4.0, 60)
    assert [p["rank"] for p in t09["trend"]] == [18, 16]
    assert (t09["dealer_deals"], rows["t02"]["venue_trades"]) == (2, 1)
    assert (t09["top_set"], t09["set_interest"]) == ("LAT", {"LAT": 40, "SAL": -5})
    assert (rows["t11"]["top_set"], rows["t11"]["set_interest"]) == (None, {})
    assert t09["why_climbed"] == "t09 +2 ranks (18→16) in 60 ticks"  # the rule-made note, never the llm one
    assert t09["why_climbed_tick"] == 90


def test_strengths_and_weaknesses_compare_each_team_with_us(conn):
    seed(conn)
    rows = board(conn)
    assert rows["t02"]["strengths"] == ["negotiating", "venue"]
    assert rows["t09"]["strengths"] == ["pages", "dealer_ladder", "climbing"]
    assert rows["t09"]["weaknesses"] == ["negotiating", "no_venue_trades", "needs_cards"]
    assert rows["t11"]["weaknesses"] == ["negotiating", "pages", "no_venue_trades", "falling"]


def test_what_they_want_and_have_against_what_we_hold(conn):
    seed(conn)
    t09 = board(conn)["t09"]
    assert t09["they_want"] == [{"ref": "SAL-01", "price": 12, "tick": 95}]  # the tick-10 bid is outside the window
    assert t09["they_have"] == [{"ref": "LAT-04", "price": 15, "tick": 96}]
    assert t09["we_have_for_them"] == [{"ref": "SAL-01", "spare": 2, "their_price": 12, "our_value": 0.5}]
    assert t09["they_have_for_us"] == [{"ref": "LAT-04", "their_price": 15, "value_to_us": 32.0}]  # book 20 × LAT 1.6
    assert t09["match_count"] == 2


def test_a_free_team_gets_our_best_move_a_swap_here(conn):
    seed(conn)
    t09 = board(conn)["t09"]
    assert (t09["guarded"], t09["guard_reason"]) == (False, None)
    assert (t09["move_kind"], t09["move_give"], t09["move_get"], t09["move_price"]) == (
        "swap",
        "SAL-01",
        "LAT-04",
        None,
    )
    assert (float(t09["our_gain"]), float(t09["their_gain"])) == (31.5, 10.0)
    assert t09["suggested_move"] == "offer our spare SAL-01 for their LAT-04 (+31.5 for us, +10.0 for them)"


def test_a_guarded_team_gets_a_move_only_when_our_gain_is_twice_theirs(conn):
    seed(conn)
    rows = board(conn)
    t02 = rows["t02"]  # podium: buying their MAL-09 at 60 (+10 for us, +60 for them) is refused; this swap is not
    assert (t02["guarded"], t02["guard_reason"], t02["move_kind"]) == (True, "podium", "swap")
    assert t02["suggested_move"] == "offer our spare LAT-02 for their MAL-09 (+69.0 for us, +10.0 for them)"
    t07 = rows["t07"]  # 2 ranks below us: buying their MAL-09 at 50 (+20 for us, +50 for them) is refused
    assert (t07["guarded"], t07["guard_reason"], t07["move_kind"], t07["move_give"], t07["move_get"]) == (
        True, "near", "hold", None, None)  # fmt: skip
    assert t07["suggested_move"] == "don't trade: within 3 ranks of us (rank 11, ours 9)"


def test_hostile_listings_and_filled_bids_never_reach_the_board(conn):
    seed(conn)
    t11 = board(conn)["t11"]
    assert (t11["they_want"], t11["they_have"], t11["match_count"]) == ([], [], 0)  # LAT-02 bought at tick 85
    assert (t11["move_kind"], t11["suggested_move"]) == ("watch", "nothing to trade yet: watch their bids")


def test_no_snapshot_of_ours_means_no_move_and_no_comparison(conn):
    seed(conn)
    with conn.cursor() as cur:
        cur.execute("delete from me_snapshots")
    conn.commit()
    rows = board(conn)
    assert "t01" in rows  # without our /me the board cannot tell which team is ours
    assert all(r["our_rank"] is None and r["move_give"] is None for r in rows.values())
    assert rows["t09"]["move_kind"] == "watch" and rows["t02"]["move_kind"] == "hold"


def listing(conn, event_id, payload):
    with conn.cursor() as cur:
        cur.execute(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, 99, 'offer.listed', %s, %s)",
            (event_id, payload["offer"]["maker"], json.dumps(payload)),
        )
    conn.commit()


def test_a_bid_alone_is_a_sale_into_it_and_a_swap_beats_it(conn):
    seed(conn)
    listing(conn, 20, offer("t11", give_cash=8, want_types=["card:SAL-01"]))
    t11 = board(conn)["t11"]
    assert (t11["move_kind"], t11["move_give"], t11["move_price"]) == ("sell", "SAL-01", 8)
    assert t11["suggested_move"] == "sell our spare SAL-01 into their bid of 8 (+7.5 for us)"
    listing(conn, 21, offer("t11", give_assets=["LAT-04"], want_cash=10))  # a buy would make +22; the swap +31.5
    t11 = board(conn)["t11"]
    assert (t11["move_kind"], t11["move_give"], t11["move_get"], float(t11["our_gain"])) == (
        "swap",
        "SAL-01",
        "LAT-04",
        31.5,
    )


def test_a_copy_we_miss_at_a_low_ask_is_a_buy(conn):
    seed(conn)
    listing(conn, 21, offer("t11", give_assets=["LAT-04"], want_cash=10))
    t11 = board(conn)["t11"]
    assert (t11["move_kind"], t11["move_get"], t11["move_price"], float(t11["their_gain"])) == (
        "buy",
        "LAT-04",
        10,
        10.0,
    )
    assert t11["suggested_move"] == "buy their LAT-04 at their ask of 10 (+22.0 for us)"
