"""The score simulator against Friday's official numbers (fixture only: no database, no network)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bazaar_agent.evals import score_sim as ss

DATA = ss.load_data(Path(__file__).parents[1] / "fixtures" / "evals" / "friday_score.json")
MODEL = ss.ScoreModel()


def _feed() -> list[dict]:
    opened = {"type": "thread.opened", "tick": 3, "id": 1, "payload": {"thread": 7, "team": "t09", "with": "abuela"}}
    ask = {
        "type": "thread.message",
        "tick": 3,
        "id": 2,
        "payload": {"thread": 7, "sender": "abuela", "offer": {"want": {"cash": 29}, "give": {"cash": 0}}},
    }
    bid = {
        "type": "thread.message",
        "tick": 4,
        "id": 3,
        "payload": {"thread": 7, "sender": "t09", "offer": {"want": {"cash": 0}, "give": {"cash": 20}}},
    }
    settled = {
        "type": "settlement",
        "tick": 5,
        "id": 4,
        "payload": {
            "persona": "abuela",
            "parties": ["abuela", "t09"],
            "price": 22,
            "items": [{"kind": "card", "ref": "LAV-06", "frm": "abuela", "to": "t09"}],
        },
    }
    team_trade = {"type": "settlement", "tick": 6, "id": 5, "payload": {"persona": None, "parties": ["t01", "t02"]}}
    return [settled, bid, ask, opened, team_trade]


def test_a_dealer_settlement_carries_the_opening_price_of_its_thread() -> None:
    assert ss.deals_from_feed(_feed()) == [ss.DealerDeal(5, "t09", "abuela", "card:uncommon", 22, 29)]


def test_a_buy_captures_its_place_between_list_price_and_best_fill_and_a_sale_the_reverse() -> None:
    deals = [
        ss.DealerDeal(1, "a", "abuela", "card:uncommon", 17, 29),
        ss.DealerDeal(2, "b", "abuela", "card:uncommon", 23, 29),
        ss.DealerDeal(3, "a", "abuela", "sell:uncommon", 13, 12),
        ss.DealerDeal(4, "b", "abuela", "sell:uncommon", 16, 12),
    ]
    ranges = ss.learned_ranges(deals)
    assert ranges[("abuela", "card:uncommon")] == ss.PriceRange(29, 17)
    assert [ss.deal_share(d, ranges) for d in deals] == [1.0, 0.5, 0.25, 1.0]
    assert ss.deal_share(ss.DealerDeal(5, "c", "vault", "card:rare", 90, 97), ranges) == 0.0


def test_the_ladder_counts_the_best_three_per_level_a_missing_one_as_zero() -> None:
    deals = [ss.DealerDeal(t, "a", "abuela", "card:common", p, 12) for t, p in enumerate((7, 7, 7, 12))]
    deals += [ss.DealerDeal(9, "a", "chato", "card:rare", 82, 97), ss.DealerDeal(9, "b", "chato", "card:rare", 97, 97)]
    raw = ss.ladder_raw(deals, upto=99, model=MODEL, teams=["c"])
    assert raw == {"c": 0.0, "a": pytest.approx(1.0 + 0.5 * 1 / 3), "b": 0.0}
    assert ss.ladder_raw(deals, upto=3, model=MODEL)["a"] == pytest.approx(1.0)


def test_full_points_at_the_top_three_mean_and_never_above_the_weight() -> None:
    assert ss.top_mean([1, 5, 3, 4]) == 4.0
    assert ss.component_points(2.0, 4.0, 12.5) == 6.25
    assert ss.component_points(9.0, 4.0, 12.5) == 12.5
    assert ss.component_points(1.0, 0.0, 12.5) == 0.0


@pytest.mark.parametrize(
    ("efficiency", "fraction"), [(0.0, 0.0), (0.3, 0.25), (0.6, 0.5), (0.75, 0.75), (0.9, 1.0), (0.97, 1.0)]
)
def test_the_market_test_pays_half_at_the_stall_and_all_at_the_top_three_mean(efficiency, fraction) -> None:
    assert ss.bench_fraction(efficiency, stall=0.6, top3=0.9) == pytest.approx(fraction)


def test_a_new_round_grows_into_the_board_and_friday_counts_half() -> None:
    assert ss.board_score([(0.5, 1.0, 8.0), (1.0, 0.0, 30.0)]) == 8.0  # doors open: nobody halves
    assert ss.board_score([(0.5, 1.0, 8.0), (1.0, 0.5, 20.0)]) == pytest.approx((4 + 10) / 1.0)
    assert ss.final_game_points({"fri": 10.0, "sat": 20.0, "sun": 20.0}, MODEL) == pytest.approx(18.0)


def test_the_model_reproduces_our_official_score_at_tick_159() -> None:
    assert DATA.ours[159] == 8.34
    assert ss.our_negotiating(DATA.deals, 159, DATA.team, MODEL) == pytest.approx(8.34, abs=0.5)


def test_the_model_follows_the_shape_of_our_decline_while_our_raw_stood_still() -> None:
    cal = ss.calibrate(DATA.deals, DATA.board30, DATA.ours, DATA.team, MODEL)
    assert cal.rmse_ours < 0.4 and cal.max_err_ours < 0.6
    modelled = [m for t, _, m in cal.ours if t % 5 == 0]
    assert modelled == sorted(modelled, reverse=True)  # it falls as the other teams' Chato deals land


def test_the_public_board_at_tick_30_fits_with_the_cap_at_the_ladder_weight() -> None:
    cal = ss.calibrate(DATA.deals, DATA.board30, DATA.ours, DATA.team, MODEL)
    top = {team: modelled for team, _, modelled in cal.board}
    assert top["t05"] == top["t06"] == 12.5 == DATA.board30["t05"] == DATA.board30["t06"]
    assert cal.board_mae < 0.6


def test_our_raw_matches_the_official_ladder_points() -> None:
    raw = ss.ladder_raw(DATA.deals, 159, MODEL, teams=[DATA.team])[DATA.team]
    assert raw / MODEL.ladder_weight == pytest.approx(DATA.ladder_points, abs=0.001)


def test_a_level_2_weight_fitted_on_the_early_refreshes_predicts_the_later_ones() -> None:
    early = {t: s for t, s in DATA.ours.items() if t < 140}
    w2, _ = ss.fit_level2_weight(DATA.deals, early, DATA.team, MODEL)
    fitted = replace(MODEL, level_weights={1: 1.0, 2: w2})
    late = [ss.our_negotiating(DATA.deals, t, DATA.team, fitted) - s for t, s in DATA.ours.items() if t >= 140]
    assert 0.3 <= w2 <= 0.8 and max(map(abs, late)) < 0.5


def test_one_more_chato_deal_is_worth_about_a_point_at_tick_159() -> None:
    raw = ss.ladder_raw(DATA.deals, 155, MODEL, teams=[DATA.team])
    moves = {m.move: m.points for m in ss.ladder_marginals(raw[DATA.team], ss.top_mean(raw.values()), MODEL)}
    assert moves["first level-2 (Chato) deal at share 0.5"] == pytest.approx(0.94, abs=0.05)
    assert moves["three level-2 deals at share 0.5"] == pytest.approx(2.82, abs=0.05)


def test_a_saturday_round_point_is_worth_two_fifths_of_a_final_point() -> None:
    assert ss.final_points_per_round_point("sat", MODEL) == pytest.approx(0.4)
    assert ss.final_points_per_round_point("fri", MODEL) == pytest.approx(0.2)
    full = ss.RoundOutlook(1.0, 1.0, 1.0, 1.0, 1.0)
    assert full.total(MODEL) == 60.0
    assert ss.RoundOutlook(ladder=2.0, bench=0.5).points(MODEL) == {
        "ladder": 12.5,
        "duels": 0.0,
        "trades": 0.0,
        "bench": 7.5,
        "venue": 0.0,
    }


def test_the_live_check_puts_the_model_next_to_each_official_snapshot() -> None:
    snaps = [
        (t, {"negotiating": s, "ladder_points": DATA.ladder_points, "duel_points": 0.0}) for t, s in DATA.ours.items()
    ]
    rows = ss.live_check(DATA.deals, snaps[-3:], DATA.team, MODEL)
    assert [r.tick for r in rows] == [157, 158, 159]
    assert rows[-1].model_ladder_points == pytest.approx(DATA.ladder_points, abs=0.001)
    assert rows[-1].unexplained == pytest.approx(0.08, abs=0.01)  # no duels or trades on Friday: model error only
    fresh_round = ss.live_check(DATA.deals, snaps[-1:], DATA.team, MODEL, round_start=160)
    assert fresh_round[0].model_ladder_points == 0.0 and fresh_round[0].model_ladder == 0.0


def test_a_snapshot_without_a_score_is_skipped_and_a_capped_ladder_gains_nothing() -> None:
    rows = ss.live_check(DATA.deals, [(158, None), (159, {"negotiating": 8.34})], DATA.team, MODEL)  # type: ignore[list-item]
    assert [r.tick for r in rows] == [159]
    capped = replace(MODEL, cap=1.2)
    assert all(m.points == 0.0 for m in ss.ladder_marginals(1.5, 1.0, capped))
