"""The rank watch: a fast climb on the public leaderboard becomes one explained `rival_move` learning."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent.learn.model import Learning
from bazaar_agent.rank_watch import RankWatch, Standing, explain, standings


def row(team: str, rank: int, score: float, neg: float, market: float, **extra: Any) -> dict[str, Any]:
    n = int(team[1:])
    return {
        "team": team,
        "name": f"Team {n}",
        "score": score,
        "negotiating": neg,
        "market": market,
        "level": extra.get("level", 2),
        "album_filled": 30,
        "album_slots": 50,
        "pages_complete": extra.get("pages", 1),
        "luck": -3.0,
        "deals": extra.get("deals", 10),
        "venue": f"v{n:02d}",
        "rank": rank,
    }


def board(tick: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tick": tick,
        "t": 4.9,
        "snapshot_tick": tick,
        "next_refresh_tick": tick + 5,
        "weights": {"negotiating": 30.0, "market": 30.0},
        "teams": rows,
        "venues": [{"venue": "v14", "owner": "t14", "trades": 1, "volume": 19, "fee_bps": 0, "starter": True}],
    }


BEFORE = board(
    410,
    [
        row("t03", 1, 25.0, 15.0, 10.0),
        row("t01", 2, 24.0, 14.0, 10.0),
        row("t07", 3, 23.0, 13.0, 10.0),
        row("t12", 4, 22.5, 12.5, 10.0),
        row("t15", 5, 22.6, 12.0, 10.0),
        row("t14", 6, 22.55, 15.09, 7.46, deals=18),
    ],
)
AFTER = board(
    430,
    [
        row("t14", 1, 29.05, 17.19, 11.86, deals=21, pages=2),
        row("t03", 2, 25.1, 15.1, 10.0),
        row("t07", 3, 23.2, 13.2, 10.0),
        row("t01", 4, 23.0, 13.0, 10.0),
        row("t12", 5, 22.4, 12.4, 10.0),
        row("t15", 6, 22.3, 12.3, 10.0),
    ],
)


def settle(eid: int, tick: int, parties: list[str], price: int, ref: str, frm: str, to: str, **kw: Any) -> dict:
    return {
        "id": eid,
        "tick": tick,
        "t": 4.8,
        "type": "settlement",
        "actor": None,
        "payload": {
            "parties": parties,
            "venue": kw.get("venue"),
            "persona": kw.get("persona"),
            "price": price,
            "fee": 0,
            "items": [{"ref": ref, "rarity": "uncommon", "frm": frm, "to": to, "kind": "card"}],
        },
    }


EVENTS = [
    settle(1, 405, ["abuela", "t14"], 7, "RET-09", "abuela", "t14", persona="abuela"),  # before the window
    settle(2, 412, ["abuela", "t14"], 9, "RET-01", "abuela", "t14", persona="abuela"),
    settle(3, 415, ["abuela", "t14"], 9, "RET-02", "abuela", "t14", persona="abuela"),
    settle(4, 420, ["chato", "t14"], 30, "RET-07", "chato", "t14", persona="chato"),
    settle(5, 422, ["t13", "t14"], 20, "RET-08", "t13", "t14"),
    settle(6, 425, ["t15", "t12"], 19, "LAT-07", "t15", "t12", venue="v14"),
    {
        "id": 7,
        "tick": 426,
        "type": "level.unlocked",
        "actor": None,
        "payload": {"team": "t14", "persona": "chato", "level": 3},
    },
    settle(8, 433, ["chato", "t14"], 31, "RET-06", "chato", "t14", persona="chato"),  # after the window
    {"id": 9, "tick": 428, "type": "thread.opened", "actor": "t14", "payload": {"with": "abuela", "kind": "persona"}},
]


def watch(us: str | None = "t01", **kw: Any) -> tuple[RankWatch, list[list[Learning]], list[str]]:
    stored: list[list[Learning]] = []
    lines: list[str] = []
    return RankWatch(stored.append, lines.append, us=us, **kw), stored, lines


def by_team(rows: list[Standing]) -> dict[str, Standing]:
    return {s.team: s for s in rows}


def test_standings_read_every_valid_row_and_skip_bad_ones() -> None:
    bad = board(
        430, [row("t14", 1, 29.05, 17.19, 11.86), {"team": "t99"}, {"team": "x y", "rank": 2, "score": 1}, "no"]
    )
    rows = standings(bad)
    assert [s.team for s in rows] == ["t14"]
    s = rows[0]
    assert (s.tick, s.rank, s.score, s.negotiating, s.market, s.level, s.pages, s.venue) == (
        430,
        1,
        29.05,
        17.19,
        11.86,
        2,
        1,
        "v14",
    )


def test_explain_names_components_dealers_trades_and_venue() -> None:
    before, after = by_team(standings(BEFORE))["t14"], by_team(standings(AFTER))["t14"]
    text, detail = explain(before, after, EVENTS, AFTER)
    assert text.startswith("t14 +5 ranks (6→1) in 20 ticks: market +4.4, negotiating +2.1, pages +1")
    assert "dealers: abuela RET-01 9, abuela RET-02 9, chato RET-07 30" in text
    assert "teams: bought RET-08 20 from t13" in text
    assert "venue v14: 1 trade between other teams (LAT-07 19)" in text
    assert "unlocked: chato L3" in text
    assert detail["event_id"] == "t14:410-430"
    assert detail["deltas"] == {"score": 6.5, "negotiating": 2.1, "market": 4.4}
    assert detail["deals_gained"] == 3
    assert [d["price"] for d in detail["dealer_deals"]] == [9, 9, 30]
    assert detail["team_trades"][0]["counterparty"] == "t13"
    assert detail["venue_trades"] == [{"venue": "v14", "parties": ["t15", "t12"], "refs": ["LAT-07"], "price": 19.0}]
    assert detail["levels"] == [{"persona": "chato", "level": 3}]
    assert detail["evidence"] == [2, 3, 4, 5, 6, 7]


def test_explain_ignores_events_outside_the_tick_range() -> None:
    before, after = by_team(standings(BEFORE))["t14"], by_team(standings(AFTER))["t14"]
    text, detail = explain(before, after, EVENTS, AFTER)
    assert "RET-09" not in text and "RET-06" not in text
    assert 1 not in detail["evidence"] and 8 not in detail["evidence"]


def test_explain_skips_components_that_barely_moved_and_caps_lists() -> None:
    before = Standing(400, "t09", 9, 10.0, 5.0, 5.0, 2, 0, 5, None)
    after = Standing(410, "t09", 5, 10.05, 5.05, 5.0, 2, 0, 12, None)
    many = [
        settle(100 + i, 401 + i, ["abuela", "t09"], 9, f"LAV-0{i}", "abuela", "t09", persona="abuela") for i in range(7)
    ]
    text, detail = explain(before, after, many, {})
    assert "negotiating" not in text and "market" not in text
    assert "score +0.1 (others fell)" in text
    assert text.count("abuela LAV") == 5 and "+2 more" in text
    assert len(detail["dealer_deals"]) == 7


def test_a_five_rank_climb_is_one_learning_and_one_log_line() -> None:
    w, stored, lines = watch()
    assert w.observe(BEFORE, EVENTS, 410) == []
    out = w.observe(AFTER, EVENTS, 430)
    assert len(out) == 1 and stored == [out]
    lr = out[0]
    assert (lr.kind, lr.subject_kind, lr.subject, lr.tick, lr.team, lr.confidence) == (
        "rival_move",
        "team",
        "t14",
        430,
        None,
        0.9,
    )
    assert lr.detail["event_id"] == "t14:410-430"
    assert lr.evidence == (2, 3, 4, 5, 6, 7)
    assert "market +4.4" in lr.text and "chato RET-07 30" in lr.text
    assert len(lines) == 1 and lines[0].startswith("tick 430 rival move: t14 +5 ranks")


def test_a_two_rank_climb_is_not_a_move() -> None:
    w, stored, _ = watch()
    w.observe(BEFORE, [], 410)
    w.observe(AFTER, [], 430)
    # t07 stayed 3rd, t01 fell; t03 fell 1. Only t14 climbed 3+.
    assert [lr.subject for lr in stored[0]] == ["t14"]
    two = board(450, [row("t15", 4, 23.5, 13.0, 10.5), *[r for r in AFTER["teams"] if r["team"] != "t15"]])
    assert w.observe(two, [], 450) == []


def test_our_own_climb_is_never_reported() -> None:
    w, stored, _ = watch(us="t14")
    w.observe(BEFORE, EVENTS, 410)
    assert w.observe(AFTER, EVENTS, 430) == [] and stored == []


def test_a_climb_against_a_snapshot_older_than_the_window_is_ignored() -> None:
    w, stored, _ = watch()
    w.observe(BEFORE, EVENTS, 410)
    late = {**AFTER, "tick": 431, "snapshot_tick": 431}
    assert w.observe(late, EVENTS, 431) == [] and stored == []


def test_the_oldest_snapshot_inside_the_window_is_the_base() -> None:
    w, _, _ = watch()
    w.observe(BEFORE, [], 410)
    mid = board(420, [row("t14", 4, 25.0, 16.0, 9.0), *[r for r in BEFORE["teams"] if r["team"] != "t14"]])
    w.observe(mid, [], 420)
    out = w.observe(AFTER, [], 430)
    assert out[0].detail["rank_before"] == 6 and out[0].detail["event_id"] == "t14:410-430"


def test_a_repeated_snapshot_is_ignored() -> None:
    w, stored, _ = watch()
    w.observe(BEFORE, EVENTS, 410)
    assert len(w.observe(AFTER, EVENTS, 430)) == 1
    assert w.observe(AFTER, EVENTS, 431) == []
    assert len(stored) == 1


def test_a_store_that_raises_keeps_nothing_and_retries_the_same_board() -> None:
    calls: list[int] = []
    lines: list[str] = []

    def record(batch: list[Learning]) -> None:
        calls.append(len(batch))
        if len(calls) == 1:
            raise OSError("db down")

    w = RankWatch(record, lines.append, us="t01")
    w.observe(BEFORE, EVENTS, 410)
    assert w.observe(AFTER, EVENTS, 430) == []
    assert any("skipped (OSError)" in line for line in lines)
    assert len(w.observe(AFTER, EVENTS, 431)) == 1
    assert calls == [1, 1]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"tick": "x", "teams": "nope"},
        {"snapshot_tick": 5, "teams": [None, 3, {"team": None}]},
        {"snapshot_tick": 5, "teams": [{"team": "t1", "rank": True, "score": 1}]},
        None,
    ],
)
def test_a_malformed_board_never_raises(bad: Any) -> None:
    w, stored, _ = watch()
    assert w.observe(bad, [{"tick": "x"}, None], 5) == []  # type: ignore[list-item]
    assert stored == []


def test_history_is_bounded() -> None:
    w, _, _ = watch(history_ticks=60)
    for t in range(0, 300, 10):
        w.observe(board(t, [row("t14", 1, 1.0, 1.0, 0.0)]), [], t)
    assert min(s.tick for s in w.snapshots["t14"]) >= 290 - 60
    assert min(w.seen_ticks) >= 290 - 60


def test_one_climb_is_said_once_even_while_its_base_is_still_inside_the_window() -> None:
    stored: list[Learning] = []
    w = RankWatch(stored.extend, lambda line: None, window_ticks=20)
    w.observe(board(400, BEFORE["teams"]), [], 400)
    w.observe(board(410, AFTER["teams"]), [], 410)
    w.observe(board(420, AFTER["teams"]), [], 420)  # still rank 1; the tick-400 board is still in the window
    assert [lr.detail["event_id"] for lr in stored] == ["t14:400-410"]
