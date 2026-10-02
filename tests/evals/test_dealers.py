"""Dealer ladder scorer on our real Abuela and Chato threads (feed, 2026-10-02)."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent.evals.dealers import CurveRow, card_rarity, learned_ranges, price_class, score_thread
from bazaar_agent.intel import dealer_threads
from tests.evals.conftest import LEVELS, OURS


@pytest.fixture(scope="module")
def ours(feed_ours: list[dict[str, Any]]) -> dict[int, Any]:
    return {t.thread: t for t in dealer_threads(feed_ours, OURS) if t.ours}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ("LAV-03", "card:common"),
        ("LAV-06", "card:uncommon"),
        ("SAL-09", "card:rare"),
        ("MAL-11", "card:epic"),
        ("CHA-12", "card:legendary"),
        ("uncommon:LAV", "card:uncommon"),
        ("sobre_barrio", "pack:sobre_barrio"),
        ("assets:113,328", "sell"),
        ("weird:LAV", None),
        ("?", None),
    ],
)
def test_price_class(item: str, expected: str | None) -> None:
    assert price_class(item) == expected


def test_card_rarity_follows_the_print_layout() -> None:
    assert [card_rarity(f"LAV-{n:02d}") for n in (1, 5, 6, 8, 9, 10, 11, 12)] == [
        "common",
        "common",
        "uncommon",
        "uncommon",
        "rare",
        "rare",
        "epic",
        "legendary",
    ]
    assert card_rarity("sobre_barrio") is None


def test_ranges_are_learned_from_every_teams_threads(curve_rows: list[CurveRow]) -> None:
    ranges = learned_ranges(curve_rows)
    commons, uncommons = ranges[("abuela", "card:common")], ranges[("abuela", "card:uncommon")]
    assert (commons.list_price, commons.floor) == (12, 7)
    assert (uncommons.list_price, uncommons.floor) == (29, 17)
    sales = ranges[("abuela", "sell")]
    assert sales.list_price is not None and sales.floor is not None and sales.floor > sales.list_price


def test_our_deals_score_their_share_of_abuelas_range(ours: dict[int, Any], curve_rows: list[CurveRow]) -> None:
    ranges = learned_ranges(curve_rows)
    scored = {n: score_thread(t, ranges, LEVELS) for n, t in ours.items()}
    assert {n: o.score for n, o in scored.items()} == {85: 0.0, 99: 1.0, 101: 0.6, 110: 0.6, 115: 0.5833, 187: 0.0}
    assert scored[101].details["level"] == 1  # Abuela
    assert (scored[101].ladder_share, scored[101].tick, scored[101].label) == (0.6, 60, "good")
    assert "range 12→7" in scored[101].explanation


def test_taking_the_dealers_opening_price_is_called_out(ours: dict[int, Any], curve_rows: list[CurveRow]) -> None:
    o = score_thread(ours[99], learned_ranges(curve_rows), LEVELS)  # her first ask 7, we took it
    assert o.details["at_opening_price"] is True
    assert "does not count toward unlocking" in o.explanation


def test_a_thread_without_a_fill_captured_nothing(ours: dict[int, Any], curve_rows: list[CurveRow]) -> None:
    chato = score_thread(ours[187], learned_ranges(curve_rows), LEVELS)
    assert (chato.score, chato.label, chato.details["level"], chato.tick) == (0.0, "bad", 2, 104)
    assert "its last price 31, our last 24" in chato.explanation


def test_a_fill_below_every_learned_floor_captures_the_whole_range(ours: dict[int, Any]) -> None:
    rows = [CurveRow("abuela", "LAV-04", 12, 10)]
    o = score_thread(ours[101], learned_ranges(rows), LEVELS)  # we paid 9: the floor moves to 9
    assert (o.score, o.details["range_floor"]) == (1.0, 9)


def test_no_learned_range_leaves_a_deal_unscored(ours: dict[int, Any]) -> None:
    thread = ours[101]
    lone = type(thread)(**{**thread.__dict__, "dealer_prices": [], "item": "LAV-04"})
    o = score_thread(lone, {}, LEVELS)
    assert o.score is None and "no price range" in o.explanation


def test_a_sale_to_a_dealer_scores_upwards_from_its_opening_bid(ours: dict[int, Any]) -> None:
    sale = type(ours[101])(
        thread=900, team=OURS, dealer="abuela", side="sell", item="assets:5", opened_tick=1, dealer_prices=[5]
    )
    sale.fill_price, sale.fill_tick = 14, 3
    o = score_thread(sale, learned_ranges([CurveRow("abuela", "assets:9", 5, 23)]), LEVELS)
    assert o.score == round(9 / 18, 4) and "Sold" in o.explanation
