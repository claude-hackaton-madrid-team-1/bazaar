"""ROI buys (`roi_buy_*`, Marius, Sun 4 Oct): the taker buys any card on any open venue whose official value of one
more copy beats ask + fee by `roi_buy_min_surplus`, a held card too, through the same accept path and guardrails.
Every value here is synthetic."""

import pytest

from bazaar_agent.guardrails import Action, Context, Guardrails, check
from bazaar_agent.official_values import OfficialValues
from tests.agent_fakes import FakePublic, ask, rows
from tests.test_official_value_agents import ValuedTeam, at, taker

pytestmark = pytest.mark.official_values  # the real official value cap (tests/conftest.py)

ON = {"roi_buy_enabled": True, "roi_buy_max_spend": 150, "cash_floor": 5}
HELD = "LAV-01"  # ME holds one copy (common)
TICK = 100


def board(*offers):
    return FakePublic(boards={"rastro": list(offers)})


def run_taker(tmp_path, values, *offers, **rules):
    t = ValuedTeam(values=values, default=1.0)
    tk, lines = taker(tmp_path, t, board(*offers), live=True, **(ON | rules))
    tk.on_tick(at(t, TICK))
    return t, lines


def test_a_held_card_below_its_official_value_is_bought_as_an_roi_buy(tmp_path):
    t, _ = run_taker(tmp_path, {HELD: 20.0}, ask(1, HELD, 8))  # 8 + Rastro fee 2 = 10 < 20
    assert ("accept", 1) in t.sent
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "roi_buy" and r.get("chosen")]
    assert row["inputs"]["roi"] is True and row["inputs"]["surplus"] == 10.0


@pytest.mark.parametrize("value", [10.0, 9.0, 10.5])  # 8 + fee 2 = 10: zero, negative, under the min surplus 1
def test_no_roi_buy_without_a_surplus_of_at_least_the_minimum(tmp_path, value):
    t, _ = run_taker(tmp_path, {HELD: value}, ask(1, HELD, 8))
    assert ("accept", 1) not in t.sent


def test_a_held_card_is_never_bought_with_the_roi_path_off(tmp_path):
    t, _ = run_taker(tmp_path, {HELD: 50.0}, ask(1, HELD, 8), roi_buy_enabled=False)
    assert ("accept", 1) not in t.sent and HELD not in t.value_calls


def test_the_scan_reads_at_most_the_cap_of_values_per_tick(tmp_path):
    offers = [ask(1, "LAV-01", 5), ask(2, "LAT-03", 6), ask(3, "LAV-06", 7), ask(4, "LAT-09", 8)]  # all held
    t, _ = run_taker(tmp_path, {}, *offers, roi_buy_max_value_reads_per_tick=3)  # every value 1: none qualifies
    assert len(t.value_calls) == 3 and t.value_calls == ["LAV-01", "LAT-03", "LAV-06"]  # cheapest first
    assert not [s for s in t.sent if s[0] == "accept"]


def test_the_scan_takes_the_best_surplus_within_the_reads(tmp_path):
    offers = [ask(1, "LAV-01", 5), ask(2, "LAT-03", 6)]
    t, _ = run_taker(tmp_path, {"LAV-01": 8.0, "LAT-03": 20.0}, *offers)
    assert ("accept", 2) in t.sent and ("accept", 1) not in t.sent


def test_the_total_spend_cap_stops_the_scan(tmp_path):
    t, _ = run_taker(tmp_path, {HELD: 50.0}, ask(1, HELD, 8), roi_buy_max_spend=9)  # 8 + fee 2 > 9
    assert ("accept", 1) not in t.sent and t.value_calls == []


# ---------------------------------------------------------------- the guardrails


def ctx(value, **kw):
    values = OfficialValues(lambda card: {"card": card, "your_value": value})
    base = {"cash": 400, "held": {HELD: 1}, "tick": TICK, "t_hours": 1.5, "values": values, "breakers": frozenset()}
    return Context(**(base | kw))


RULES = Guardrails(**ON, max_price_epic=240, off_page_min_surplus=10, no_buyback_ticks=0)


def test_a_held_card_passes_the_check_only_as_an_roi_buy():
    plain = check(Action("accept_buy", HELD, "common", 10), ctx(20.0), RULES)
    assert not plain.allowed and "block_buying_held_cards" in str(plain)
    assert check(Action("accept_buy", HELD, "common", 10, roi=True), ctx(20.0), RULES).allowed
    off = check(
        Action("accept_buy", HELD, "common", 10, roi=True),
        ctx(20.0),
        RULES.model_copy(update={"roi_buy_enabled": False}),
    )
    assert not off.allowed and "roi_buy_enabled = false" in str(off)


def test_an_roi_buy_stays_strictly_under_the_official_value():
    assert not check(Action("accept_buy", HELD, "common", 10, roi=True), ctx(10.0), RULES).allowed
    assert check(Action("accept_buy", HELD, "common", 10, roi=True), ctx(11.0), RULES).allowed


def test_the_spend_cap_binds_an_roi_buy():
    v = check(Action("accept_buy", HELD, "common", 10, roi=True), ctx(20.0, roi_spent=145), RULES)
    assert not v.allowed and "roi_buy_max_spend 150" in str(v)


def test_an_off_page_roi_buy_keeps_off_page_min_surplus():
    epic, rules = "LAV-11", RULES.model_copy(update={"roi_buy_max_spend": 300, "max_spend_per_game_hour": 300})
    denied = check(Action("accept_buy", epic, "epic", 195, roi=True), ctx(200.0), rules)
    assert not denied.allowed and "official value 200" in str(denied)
    assert check(Action("accept_buy", epic, "epic", 190, roi=True), ctx(200.0), rules).allowed


def test_a_missing_page_buy_ranks_before_an_roi_buy(tmp_path):
    t, _ = run_taker(tmp_path, {HELD: 50.0, "LAV-02": 30.0}, ask(1, HELD, 8), ask(2, "LAV-02", 10))
    assert ("accept", 2) in t.sent and ("accept", 1) not in t.sent  # one accept per tick: the page card's


def test_a_card_read_short_is_not_read_again_so_the_reads_move_on(tmp_path):
    offers = [ask(1, "LAV-01", 5), ask(2, "LAT-03", 6), ask(3, "LAV-06", 7), ask(4, "LAT-09", 8)]
    t = ValuedTeam(values={"LAT-09": 30.0}, default=1.0)
    tk, _ = taker(tmp_path, t, board(*offers), live=True, **(ON | {"roi_buy_max_value_reads_per_tick": 3}))
    tk.on_tick(at(t, TICK))
    assert t.value_calls == ["LAV-01", "LAT-03", "LAV-06"] and not [s for s in t.sent if s[0] == "accept"]
    tk.on_tick(at(t, TICK + 1))  # the three read short stay known: the fourth card is read and bought
    assert t.value_calls[3:] == ["LAT-09"] and ("accept", 4) in t.sent
