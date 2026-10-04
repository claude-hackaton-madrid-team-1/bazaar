"""SX1: `protect_page_exceptions`, the cards (REF:MIN) whose last copy `protect_page_sets` lets us sell.

Omar, Sat 3 Oct ~20:20: LAT-10 may be sold (our LAT page is 4/10, our value 35), "at least 200, then the agent comes
down". The coordinator posts 200 by hand; no sale of it (any kind) goes below MIN 80, and the maker's floors never
price it below 80. Every other card of every set stays protected, and every other sale rule still binds.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bazaar_agent import approvals
from bazaar_agent import guardrails as gr
from bazaar_agent import move_impact as mi
from bazaar_agent.agents.maker import Target
from bazaar_agent.agents.maker_jev import price_candidates
from bazaar_agent.agents.relist import AskTrail, relist_price
from bazaar_agent.agents.seller import Swap
from tests.test_strategy import PARAMS

ALL_SETS = "LAV,SAL,MAL,RET,LAT,CHA"
EXCEPT_LAT10 = gr.Guardrails(protect_page_sets=ALL_SETS, protect_page_exceptions="LAT-10:80")
NO_EXCEPTION = gr.Guardrails(protect_page_sets=ALL_SETS)
SELL_KINDS = ("sell", "accept_sell", "dealer_sell")


def ctx(held: dict[str, int]) -> gr.Context:
    return gr.Context(cash=400, held=held, tick=10, t_hours=4.1)


def sell(item, rarity, price, value, held, rules, kind="sell") -> gr.Verdict:
    return gr.check(gr.Action(kind, item, rarity, price, your_value=value), ctx(held), rules)


def test_the_excepted_card_is_not_protected_and_any_other_spelling_of_it_is():
    assert not EXCEPT_LAT10.protects("LAT-10", "rare", 1)
    assert NO_EXCEPTION.protects("LAT-10", "rare", 1)  # without the exception: today's refusal
    # /me refs are canonical: an odd spelling of the item is never excepted (its copies count 0: fail closed)
    for odd in ("lat-10", " LAT-10 ", "Lat-10", "LAT-10​", "LAT-１０", "LAT-010"):
        assert EXCEPT_LAT10.protects(odd, "rare", 1), odd
    lower = gr.Guardrails(protect_page_sets=ALL_SETS, protect_page_exceptions="lat-10:80")
    assert lower.excepted("LAT-10") and lower.exception_min("LAT-10") == 80


@pytest.mark.parametrize(
    "ref, rarity",
    [("LAT-01", "common"), ("LAT-03", "common"), ("LAT-02", "common"), ("LAT-08", "uncommon"), ("LAT-09", "rare")],
)
def test_every_other_card_of_its_set_stays_protected(ref, rarity):
    assert EXCEPT_LAT10.protects(ref, rarity, 1) and EXCEPT_LAT10.exception_min(ref) == 0
    verdict = sell(ref, rarity, 200, 10.0, {ref: 1}, EXCEPT_LAT10)
    assert not verdict.allowed and "protect_page_sets" in str(verdict)


@pytest.mark.parametrize(
    "ref, rarity",
    [("SAL-07", "uncommon"), ("LAV-10", "rare"), ("RET-01", "common"), ("CHA-09", "rare"), ("MAL-10", "rare")],
)
def test_other_sets_are_unaffected(ref, rarity):
    assert EXCEPT_LAT10.protects(ref, rarity, 1)
    assert not sell(ref, rarity, 200, 10.0, {ref: 1}, EXCEPT_LAT10).allowed


@pytest.mark.parametrize("kind", SELL_KINDS)
def test_every_sale_kind_of_lat10_needs_at_least_its_min(kind):
    assert sell("LAT-10", "rare", 80, 35.0, {"LAT-10": 1}, EXCEPT_LAT10, kind).allowed
    assert sell("LAT-10", "rare", 200, 35.0, {"LAT-10": 1}, EXCEPT_LAT10, kind).allowed
    low = sell("LAT-10", "rare", 79, 35.0, {"LAT-10": 1}, EXCEPT_LAT10, kind)
    assert not low.allowed and "LAT-10 sells for 80 P or more" in str(low)
    assert not sell("LAT-10", "rare", None, 35.0, {"LAT-10": 1}, EXCEPT_LAT10, kind).allowed  # no price: refused
    assert "protect_page_sets" in str(sell("LAT-10", "rare", 200, 35.0, {"LAT-10": 1}, NO_EXCEPTION, kind))


def test_a_swap_giving_lat10_counts_cash_plus_the_card_received():
    def leg(worth: float, want_cash: int) -> gr.Action:
        swap = Swap(77, "LAT-10", "rare", 35.0, "MAL-09", "rare", worth, "rastro", "t05", want_cash=want_cash)
        return swap.actions()[0]

    assert gr.check(leg(60, 20), ctx({"LAT-10": 1}), EXCEPT_LAT10).allowed  # 60 + 20 = 80
    refused = gr.check(leg(60, 19), ctx({"LAT-10": 1}), EXCEPT_LAT10)
    assert not refused.allowed and "sells for 80 P or more" in str(refused)


def test_the_maker_never_prices_lat10_below_its_min():
    cheap = Target("ask", "LAT-10", "rare", 60, 77, 45.0, 1.0, "r")  # a tape below 80
    assert min(price_candidates(cheap, PARAMS, EXCEPT_LAT10).values()) >= 80
    usual = Target("ask", "LAT-10", "rare", 86, 77, 45.0, 1.0, "r")  # the audit's first ask
    prices = price_candidates(usual, PARAMS, EXCEPT_LAT10)
    assert prices["aggressive"] == 86 and prices["quick_sale"] == 80
    assert min(price_candidates(usual, PARAMS, NO_EXCEPTION).values()) < 80  # without it: down to ~50
    floor = max(45, EXCEPT_LAT10.exception_min("LAT-10"))  # the relist cost floor, as maker._relisted builds it
    for prices_so_far in ((86,), (86, 82), (86, 82, 78)):
        r = relist_price(
            86,
            AskTrail(77, prices_so_far, len(prices_so_far)),
            open_price=None,
            cost_floor=floor,
            median=50.0,
            tick=200,
            step_share=0.05,
            min_share=0.6,
            max_lapses=10,
            cooldown_ticks=40,
        )
        assert r.price is None or r.price >= 80, r


def test_the_exception_never_lowers_the_sell_floor():
    rules = gr.Guardrails(protect_page_sets=ALL_SETS, protect_page_exceptions="LAT-10:20")
    verdict = sell("LAT-10", "rare", 30, 35.0, {"LAT-10": 1}, rules)
    assert not verdict.allowed and "your_value" in str(verdict)


@pytest.mark.parametrize(
    "value, mins",
    [("LAT-10:80", {"LAT-10": 80}), ("lat-10:80, SAL-01:12", {"LAT-10": 80, "SAL-01": 12}), ("none", {}), ("-", {})],
)
def test_card_minimums_parse_and_normalise(value, mins):
    assert gr.card_minimums(value) == mins


@pytest.mark.parametrize(
    "value",
    [
        "LAT-10",
        "LAT-10:",
        "LAT-10:0",
        "LAT-10:-5",
        "LAT-10:8.5",
        "LAT-10:abc",
        "LAT:80",
        "LAT-1:80",
        "LAT-*:80",
        "LAT-10:80;rm",
        "LAT-10:80,LAT",
        "10",
        "LATI-10:80",
        "LAT-100:80",
        "LAT-１０:80",
        "LAT-١٠:80",
        "ſal-07:5",
        "LAT-10:１００",
        "LAT-10:80,LAT-10:90",
    ],
)
def test_a_malformed_exception_list_fails_the_load(value):
    with pytest.raises(gr.GuardrailsError, match="protect_page_exceptions"):
        gr.parse_guardrails(f"- `protect_page_exceptions` = {value} — x")


def test_none_restores_todays_behaviour():
    rules = gr.parse_guardrails(f"- `protect_page_sets` = {ALL_SETS} — x\n- `protect_page_exceptions` = none — y").rules
    assert rules.protects("LAT-10", "rare", 1) and rules.exception_min("LAT-10") == 0
    assert gr.Guardrails().protect_page_exceptions == "none"  # the model default: no exceptions


def test_a_duplicate_of_any_other_card_still_sells():
    assert sell("LAT-03", "common", 12, 5.0, {"LAT-03": 2}, EXCEPT_LAT10).allowed


def test_an_excepted_sale_must_hand_over_a_copy_of_that_card():
    me = {
        "id": "t01",
        "assets": [
            {"id": 77, "kind": "card", "ref": "LAT-10", "rarity": "rare", "your_value": 35.0},
            {"id": 78, "kind": "card", "ref": "LAT-09", "rarity": "rare", "your_value": 35.0},
        ],
    }
    base = replace(ctx({"LAT-10": 1, "LAT-09": 1}), cards=mi.our_cards(me))
    assert gr.check(gr.Action("sell", "LAT-10", "rare", 200, 35.0, asset=77), base, EXCEPT_LAT10).allowed
    wrong = gr.check(gr.Action("sell", "LAT-10", "rare", 200, 35.0, asset=78), base, EXCEPT_LAT10)
    assert not wrong.allowed and "asset 78 is not a copy of LAT-10" in str(wrong)  # the label lifts nothing


@pytest.fixture
def asked():
    writes: list[dict] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    if old is None:
        approvals._BOARD.pop("board", None)
    else:
        approvals.install(old)


@pytest.mark.human_approval
def test_committed_rules_protect_last_copy_and_require_approval_at_60(asked):
    rules = gr.load_guardrails().rules
    assert rules.protect_page_exceptions == "none"
    assert rules.human_approval_above == 60
    for ref in ("LAT-10", "LAT-09"):
        for price in (59, 60, 250):
            verdict = sell(ref, "rare", price, 35.0, {ref: 1}, rules)
            assert not verdict.allowed and "protect_page_sets" in str(verdict)
        assert sell(ref, "rare", 59, 35.0, {ref: 2}, rules).allowed
        verdict = sell(ref, "rare", 60, 35.0, {ref: 2}, rules)
        assert not verdict.allowed and "human approval" in str(verdict)
        assert asked[-1]["card"] == ref


@pytest.mark.parametrize("kind", SELL_KINDS)
@pytest.mark.parametrize("ref", ["LAT-10", "LAT-09"])
def test_committed_rules_never_except_a_last_page_copy(ref, kind):
    rules = gr.load_guardrails().rules
    for price in (35, 80, 90, 250):
        refused = sell(ref, "rare", price, 35.0, {ref: 1}, rules, kind)
        assert not refused.allowed and "protect_page_sets" in str(refused)
    refused = sell(ref, "rare", 34, 35.0, {ref: 2}, rules, kind)
    assert not refused.allowed and "your_value" in str(refused)
