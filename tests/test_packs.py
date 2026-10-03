from dataclasses import replace

import pytest

from bazaar_agent import packs as pk
from bazaar_agent.packs import TapeRarity
from tests.test_intel import settle
from tests.test_strategy import PARAMS, market

TAPE = {
    "common": TapeRarity("common", listed=100, sold=5, median_price=9.0),
    "uncommon": TapeRarity("uncommon", listed=10, sold=2, median_price=24.0),
}


def listed(eid, team, ref, price, asset=900):
    offer = {"id": eid, "maker": team, "give": {"assets": [{"id": asset, "ref": ref}]}, "want": {"cash": price}}
    return {"id": eid, "tick": 7, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


def test_the_team_tape_counts_single_card_asks_and_team_to_team_sales():
    events = [
        listed(1, "t05", "LAV-01", 12, asset=900),
        listed(2, "t05", "LAV-01", 10, asset=900),  # a reprice of the same card: listed once
        listed(3, "t06", "LAV-02", 11, asset=901),
        settle(4, 9, "t05", "t07", "LAV-01", 9, tick=8, kind="card", persona=None, asset_id=900),
        settle(5, 10, "abuela", "t07", "LAV-02", 10, tick=8, kind="card"),  # a dealer sale: not the team tape
        settle(6, 11, "v99", "t07", "LAV-02", 3, tick=8, kind="card", persona=None),  # not a team seller
    ]
    tape = pk.tape_by_rarity(events, {"LAV-01": "common", "LAV-02": "common"})
    assert tape["common"] == TapeRarity("common", 2, 1, 9.0) and tape["common"].fill_rate == 0.5


def test_pack_cards_follow_the_slot_odds_uniformly_over_the_sets():
    m = market()
    probs = pk.pack_cards(m, m.packs["sobre_barrio"], ["LAV"])
    commons = [r for r in probs if m.cards[r].rarity == "common"]
    assert pytest.approx(sum(probs[r] for r in commons)) == 2.75  # two common slots + 0.75 of the third
    assert pytest.approx(sum(p for r, p in probs.items() if m.cards[r].rarity == "uncommon")) == 0.25


def test_resale_scores_only_the_surplus_over_our_value_at_the_fill_rate():
    m = market()
    v = pk.pack_value(m, "sobre_barrio", 22, TAPE, PARAMS, sets=["LAT"])  # LAT: worth little to us (x0.5)
    o = next(o for o in v.cards if o.ref == "LAT-03")  # we hold two copies: the third is worth 0.1 x 5
    assert o.keep_value == pytest.approx(0.5) and o.sale_net == 9 - 2 and o.fill == 0.05
    assert o.scored == pytest.approx(0.05 * (7 - 0.5))
    assert v.scored_surplus < v.price / 10  # a pack never pays for itself in scored surplus at these fills
    chased = pk.pack_value(m, "sobre_barrio", 22, TAPE, PARAMS, sets=["LAT"], chasers={"LAT": ["t15"]}, chaser_fill=1.0)
    assert chased.scored_surplus > v.scored_surplus * 10  # the what-if: every chased card sells


def test_a_card_worth_more_to_us_than_its_sale_is_kept_not_sold():
    m = replace(market(), affinity={"LAV": 1.6})
    v = pk.pack_value(m, "sobre_barrio", 22, TAPE, PARAMS, sets=["LAV"])
    first_copy = next(o for o in v.cards if o.ref == "LAV-02")  # not held: 16 to us + its page bonus share
    assert first_copy.keep_value > 16 and first_copy.scored == 0.0


def test_the_price_is_the_median_and_a_lower_cap_makes_it_unbuyable_not_cheaper():
    assert pk.expected_price([21, 22, 23], cap=26) == (22.0, "median paid 22", True)
    price, basis, buyable = pk.expected_price([17, 21, 22, 23], cap=20)
    assert (price, buyable) == (21.5, False) and "1 of 4 fills were at or under it" in basis
    assert pk.expected_price([], cap=20) == (None, "no fill seen: pass --price", False)


def test_cash_uses_put_the_ladder_best_three_far_ahead_of_packs():
    m = market()
    pack = pk.pack_value(m, "sobre_barrio", 22, TAPE, PARAMS, sets=["LAV", "LAT"])
    uses = {u.name.split(" (")[0]: u.per_prima for u in pk.uses_of_cash([pack])}
    ladder_low = uses["Abuela best three"][0]
    pack_high = next(v for k, v in uses.items() if k.startswith("one sobre_barrio"))[1]
    assert ladder_low > 10 * pack_high


def ask(eid, thread, price, tick):
    offer = {"id": eid, "give": {"types": ["pack:sobre_barrio"]}, "want": {"cash": price}}
    payload = {"kind": "persona", "thread": thread, "sender": "abuela", "with": "abuela", "offer": offer}
    return {"id": eid, "tick": tick, "type": "thread.message", "payload": payload}


def test_pack_fills_come_from_the_dealers_usual_opening_only():
    from tests.test_intel import opened

    topic = {"buy": {"pack": "sobre_barrio"}}
    events = []
    for i, (opening, fill) in enumerate([(30, 22), (30, 21), (17, 17)]):
        t = 10 + i
        events += [opened(100 + i, t, f"t0{i + 2}", topic, tick=i), ask(200 + i, t, opening, i)]
        events.append(settle(300 + i, 400 + i, "abuela", f"t0{i + 2}", "sobre_barrio", fill, tick=i + 1))
    assert sorted(pk.pack_fills(events, "sobre_barrio")) == [21, 22]  # the 17 opening is a minority regime


def test_an_unknown_pack_is_an_error_not_a_zero():
    with pytest.raises(ValueError, match="no pack 'sobre_barrios'"):
        pk.pack_value(market(), "sobre_barrios", 22, TAPE, PARAMS)


def test_a_printed_out_rarity_gives_the_next_one_down_and_epics_are_drawn():
    m = market()
    printed = {r: replace(c, minted=c.print_run) if c.rarity == "uncommon" else c for r, c in m.cards.items()}
    probs = pk.pack_cards(replace(m, cards=printed), m.packs["sobre_barrio"], ["LAV"])
    assert all(m.cards[r].rarity == "common" for r in probs) and pytest.approx(sum(probs.values())) == 3.0
    epic = pk.pack_cards(m, [{"epic": 1.0}], ["LAV"])
    assert set(epic) == {"LAV-11"}  # a non-page card: drawn, not dropped
