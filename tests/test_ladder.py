import json
import random
from pathlib import Path

import pytest

from bazaar_agent.agents.dealer import BidPlan
from bazaar_agent.ladder import (
    Conversation,
    FloorRow,
    Turn,
    conversations,
    floor_table,
    from_rows,
    limit_bounds,
    limit_point,
    main_rows,
    plan_for,
    to_rows,
)
from bazaar_agent.ladder_replay import Episode, backtest, draw, episode_from, fit, play, summarise

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "dealer_threads.json"


@pytest.fixture(scope="module")
def real() -> list[Conversation]:
    return from_rows(json.loads(FIXTURE.read_text())["rows"])


def opened(tid, team="t05", dealer="abuela", topic=None, tick=0):
    topic = topic or {"buy": {"card": "LAV-06"}}
    payload = {"kind": "persona", "team": team, "with": dealer, "topic": topic, "thread": tid}
    return {"type": "thread.opened", "tick": tick, "payload": payload}


def said(tid, sender, price, tick, final=False, dealer="abuela"):
    offer = {"give": {"cash": price}, "want": {}, "final": final} if price is not None else None
    return {
        "type": "thread.message",
        "tick": tick,
        "payload": {"kind": "persona", "thread": tid, "sender": sender, "with": dealer, "offer": offer},
    }


def settled(price, tick, buyer="t05", dealer="abuela", ref="LAV-06"):
    items = [{"to": buyer, "frm": dealer, "ref": ref, "kind": "card"}]
    payload = {"price": price, "persona": dealer, "items": items, "settlement": tick}
    return {"id": 1000 + tick, "type": "settlement", "tick": tick, "payload": payload}


def test_a_thread_reads_turn_by_turn_with_its_fill():
    events = [
        opened(1),
        said(1, "t05", 15, 1),
        said(1, "abuela", 29, 1),
        said(1, "t05", 17, 2),
        said(1, "abuela", 25, 2),
        said(1, "t05", 21, 3),
        said(1, "abuela", None, 3),
        settled(21, 4),
    ]
    (c,) = conversations(events)
    assert (c.opening, c.team_prices, c.dealer_prices) == (29, [15, 17, 21], [29, 25])
    assert (c.fill, c.closed_by, c.first_drop, c.patience) == (21, "our_price", 4, None)
    assert limit_bounds(c) == (18, 21, True)  # she countered 17, took 21
    assert limit_point(c) == 21


def test_a_fill_never_goes_to_an_abandoned_thread_that_never_named_its_price():
    events = [
        opened(1, tick=0),
        said(1, "t05", 15, 0),
        said(1, "abuela", 29, 1),
        opened(2, tick=5),
        said(2, "t05", 22, 5),
        said(2, "abuela", None, 5),
        opened(3, tick=6),  # opened later, never priced 22
        settled(22, 6),
    ]
    fills = {c.thread: c.fill for c in conversations(events)}
    assert fills == {1: None, 2: 22, 3: None}


def test_a_fill_goes_to_the_thread_about_that_item():
    """Friday thread 101 (LAV-04, our bids 6..9) must not take LAV-03's settlement at 7 from thread 99."""
    events = [
        opened(99, topic={"buy": {"card": "LAV-03"}}, tick=54),
        said(99, "t05", 6, 54),
        said(99, "abuela", 7, 55),
        opened(101, topic={"buy": {"card": "LAV-04"}}, tick=56),
        said(101, "t05", 6, 56),
        settled(7, 56, ref="LAV-03"),
        said(101, "abuela", 12, 57),
        said(101, "t05", 7, 57),
    ]
    fills = {c.thread: c.fill for c in conversations(events)}
    assert fills == {99: 7, 101: None}


def test_a_rarity_request_takes_only_a_card_of_that_rarity():
    events = [
        opened(1, topic={"buy": {"rarity": "uncommon", "set": "LAV"}}),
        said(1, "t05", 10, 1),
        settled(10, 2, ref="LAV-03"),  # a common, from another thread we never saw open
    ]
    assert conversations(events)[0].fill is None
    assert conversations([*events[:2], settled(10, 2, ref="LAV-07")])[0].fill == 10


def test_one_odd_opening_does_not_change_the_regime_we_plan_on(real):
    c = Conversation(9999, "t05", "abuela", "buy", "LAV-07", 999, turns=[Turn(999, True, 17), Turn(999, False, 16)])
    rows = main_rows(floor_table([*real, c]))
    assert rows[("abuela", "card:uncommon")].opening == 29


def test_the_final_offer_bounds_the_limit_and_counts_patience():
    c = Conversation(1, "t04", "abuela", "buy", "sobre_barrio", 0)
    c.turns = [Turn(0, True, 30), Turn(1, False, 9), Turn(1, True, 25), Turn(2, False, 11), Turn(2, True, 24, True)]
    assert (c.patience, c.final_price) == (2, 24)
    assert limit_bounds(c) == (12, 24, True)


def test_a_sale_to_the_dealer_is_bounded_the_other_way():
    c = Conversation(1, "t13", "chato", "sell", "assets:448", 0, fill=46)
    c.turns = [Turn(0, False, 135), Turn(0, True, 39), Turn(1, False, 124), Turn(1, True, 46, True)]
    assert limit_bounds(c) == (46, 123, True)
    assert limit_point(c) == 46


def test_rows_round_trip(real):
    assert [r.as_dict() for r in floor_table(from_rows(to_rows(real)))] == [r.as_dict() for r in floor_table(real)]


def test_floor_table_from_every_teams_friday_threads(real):
    rows = main_rows(floor_table(real))
    unc = rows[("abuela", "card:uncommon")]
    assert (unc.opening, unc.floor(0.1), unc.floor(0.5), unc.floor(0.9)) == (29, 21, 23, 25)
    assert rows[("abuela", "pack:sobre_barrio")].opening == 30  # the 17 opening was the first hour only
    assert rows[("abuela", "card:common")].floor() == 10
    assert rows[("chato", "card:rare")].floor() == 91
    assert rows[("chato", "card:uncommon")].floor() == 29


def row(limits, opening=29):
    return FloorRow("abuela", "card:uncommon", opening, len(limits), len(limits), tuple(sorted(limits)), 0, 5.0, 4.0)


def test_plan_is_floor_minus_two_to_floor_plus_two_under_the_cap():
    choice = plan_for(row([21, 22, 23, 24, 25]), cap=26)
    assert choice.plan == BidPlan(21, 1, 25)
    assert plan_for(row([21, 22, 23, 24, 25]), cap=24).plan == BidPlan(21, 1, 24)
    commons = FloorRow("abuela", "card:common", 12, 5, 5, (9, 10, 10, 10, 10), 5, None, 2.0)
    assert plan_for(commons, cap=12).plan == BidPlan(8, 1, 11)  # never up to her opening ask (no share)


def test_no_plan_when_the_cap_sits_below_most_limits():
    choice = plan_for(row([89, 90, 91, 91, 93], opening=97), cap=80)
    assert choice.plan is None and "below market" in choice.reason


def test_no_plan_without_enough_closed_conversations():
    assert plan_for(row([]), cap=26).plan is None
    thin = plan_for(row([22, 23, 24, 25]), cap=26)
    assert thin.plan is None and "fewer than 5" in thin.reason
    assert plan_for(row([22, 23, 24, 25]), cap=26, min_closed=4).plan is not None


def ep(limit, patience=5, first_drop=4, opens_first=True, opening=29, later=(1,)):
    return Episode(opening, limit, patience, first_drop, later, opens_first)


def test_a_ladder_that_starts_under_the_limit_closes_at_it():
    r = play(BidPlan(21, 1, 25), ep(23))
    assert (r.price, r.how, r.bids, r.share) == (23, "our_bid", (21, 22, 23), 1.0)


def test_a_limit_above_the_plan_takes_her_final_inside_the_max_or_walks():
    assert play(BidPlan(21, 1, 25), ep(25, patience=3)).how in ("her_final", "our_bid")
    walked = play(BidPlan(21, 1, 22), ep(25))
    assert (walked.price, walked.how) == (None, "walk")


def test_replay_never_repeats_a_price_or_takes_a_non_final_opening_ask(real):
    rows = main_rows(floor_table(real))
    rng = random.Random(3)
    for key in [("abuela", "card:uncommon"), ("abuela", "card:common"), ("abuela", "pack:sobre_barrio")]:
        r = rows[key]
        model = fit(real, *key, r.opening)
        for start in (1, r.opening - 6, r.opening - 2):
            results = [play(BidPlan(start, 1, r.opening), draw(model, rng)) for _ in range(200)]
            assert not any(x.repeated for x in results)
            # #61: her opening ask is taken only as a final, or when no whole price is left below it
            assert all(x.how == "her_final" or x.bids[-1] + 1 >= x.opening for x in results if x.price == x.opening)


@pytest.mark.parametrize("key", [("abuela", "card:uncommon"), ("abuela", "card:common")])
def test_go_criteria_on_the_fitted_dealer(real, key):
    """PLAN.md W3 go: mean share ≥ 0.8, ≥ 90 % filled within 8 ticks, 0 repeated prices."""
    r = main_rows(floor_table(real))[key]
    choice = plan_for(r, cap={"card:uncommon": 26, "card:common": 12}[key[1]])
    assert choice.plan is not None
    s = backtest(choice.plan, fit(real, *key, r.opening), runs=2000, seed=7)
    assert s.mean_share >= 0.8 and s.fill_within >= 0.9 and s.repeated == 0


def test_replaying_each_real_conversation(real):
    r = main_rows(floor_table(real))[("abuela", "card:uncommon")]
    plan = plan_for(r, cap=26).plan
    assert plan is not None
    mine = [c for c in real if (c.dealer, c.price_class, c.opening, c.side) == ("abuela", "card:uncommon", 29, "buy")]
    results = [play(plan, e) for c in mine if (e := episode_from(c))]
    s = summarise(results)
    assert s.runs == 37 and s.mean_share >= 0.85 and s.repeated == 0


def test_levels_from_the_feed_and_the_deals_that_count_toward_the_next_one():
    from bazaar_agent.ladder import levels, negotiated_deals

    teaser = "«Better packs, friendly prices. If I like you.»"
    events = [
        {"type": "level.announced", "tick": 71, "payload": {"level": "chato", "name": "El Chato", "teaser": teaser}},
        {
            "type": "level.activated",
            "tick": 98,
            "payload": {
                "level": "chato",
                "name": "El Chato",
                "how": "he buys uncommon and rare cards",
                "opens_to_all_in_hours": 1.0,
            },
        },
        {
            "type": "level.unlocked",
            "tick": 98,
            "payload": {"team": "t01", "persona": "chato", "why": "4 deals with abuela"},
        },
    ]
    (lv,) = levels(events)
    assert (lv.dealer, lv.announced_tick, lv.activated_tick, lv.opens_to_all_in_hours) == ("chato", 71, 98, 1.0)
    assert lv.unlocked == (("t01", 98, "4 deals with abuela"),) and "buys" in (lv.how or "")
    at_opening = Conversation(1, "t01", "abuela", "buy", "LAV-03", 0, turns=[Turn(0, True, 7)], fill=7)
    haggled = Conversation(2, "t01", "abuela", "buy", "LAV-04", 0, turns=[Turn(0, True, 12)], fill=9)
    assert negotiated_deals([at_opening, haggled], "t01") == {"abuela": 1}
