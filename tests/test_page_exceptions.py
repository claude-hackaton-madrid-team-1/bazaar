"""SX1: `protect_page_exceptions`, the card refs `protect_page_sets` lets us sell as a last copy.

Omar, Sat 3 Oct ~20:20: LAT-10 may be sold (our LAT page is 4/10, our value 35); every other card of every set
stays protected, and every other rule (sell floor, move impact, approvals) still binds the sale.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bazaar_agent import approvals
from bazaar_agent import guardrails as gr
from bazaar_agent import move_impact as mi

ALL_SETS = "LAV,SAL,MAL,RET,LAT,CHA"
EXCEPT_LAT10 = gr.Guardrails(protect_page_sets=ALL_SETS, protect_page_exceptions="LAT-10")
NO_EXCEPTION = gr.Guardrails(protect_page_sets=ALL_SETS)


def ctx(held: dict[str, int]) -> gr.Context:
    return gr.Context(cash=400, held=held, tick=10, t_hours=4.1)


def sell(item: str, rarity: str, price: int, value: float, held: dict[str, int], rules: gr.Guardrails) -> gr.Verdict:
    return gr.check(gr.Action("sell", item, rarity, price, your_value=value), ctx(held), rules)


@pytest.mark.parametrize("ref", ["LAT-10", "lat-10", " LAT-10 ", "Lat-10"])
def test_the_excepted_card_is_not_protected_however_it_is_spelled(ref):
    assert not EXCEPT_LAT10.protects(ref, "rare", 1)
    assert NO_EXCEPTION.protects(ref, "rare", 1)  # without the exception: today's refusal


@pytest.mark.parametrize(
    "ref, rarity",
    [("LAT-01", "common"), ("LAT-03", "common"), ("LAT-02", "common"), ("LAT-08", "uncommon"), ("LAT-09", "rare")],
)
def test_every_other_card_of_its_set_stays_protected(ref, rarity):
    assert EXCEPT_LAT10.protects(ref, rarity, 1)
    verdict = sell(ref, rarity, 200, 10.0, {ref: 1}, EXCEPT_LAT10)
    assert not verdict.allowed and "protect_page_sets" in str(verdict)


@pytest.mark.parametrize(
    "ref, rarity",
    [("SAL-07", "uncommon"), ("LAV-10", "rare"), ("RET-01", "common"), ("CHA-09", "rare"), ("MAL-10", "rare")],
)
def test_other_sets_are_unaffected(ref, rarity):
    assert EXCEPT_LAT10.protects(ref, rarity, 1)
    assert not sell(ref, rarity, 200, 10.0, {ref: 1}, EXCEPT_LAT10).allowed


def test_the_last_lat10_sells_at_200_with_the_exception_and_is_refused_without_it():
    assert sell("LAT-10", "rare", 200, 35.0, {"LAT-10": 1}, EXCEPT_LAT10).allowed
    refused = sell("LAT-10", "rare", 200, 35.0, {"LAT-10": 1}, NO_EXCEPTION)
    assert not refused.allowed and "protect_page_sets" in str(refused)


def test_the_exception_never_lowers_the_sell_floor():
    verdict = sell("LAT-10", "rare", 30, 35.0, {"LAT-10": 1}, EXCEPT_LAT10)
    assert not verdict.allowed and "your_value" in str(verdict)
    assert "protect_page_sets" not in str(verdict)


@pytest.mark.parametrize(
    "value, refs", [("LAT-10", ("LAT-10",)), ("lat-10, SAL-01", ("LAT-10", "SAL-01")), ("none", ()), ("-", ())]
)
def test_card_refs_parse_and_normalise(value, refs):
    assert gr.card_refs(value) == refs


@pytest.mark.parametrize("value", ["LAT", "LAT-1", "LAT-*", "LAT-10;rm", "LAT-10,LAT", "10", "LATI-10", "LAT-100"])
def test_a_malformed_exception_list_fails_the_load(value):
    with pytest.raises(gr.GuardrailsError, match="protect_page_exceptions"):
        gr.parse_guardrails(f"- `protect_page_exceptions` = {value} — x")


def test_none_restores_todays_behaviour():
    rules = gr.parse_guardrails(f"- `protect_page_sets` = {ALL_SETS} — x\n- `protect_page_exceptions` = none — y").rules
    assert rules.protects("LAT-10", "rare", 1)
    assert gr.Guardrails().protect_page_exceptions == "none"  # the model default: no exceptions


def test_a_duplicate_of_any_card_still_sells_and_an_exception_outside_the_protected_sets_changes_nothing():
    assert sell("LAT-03", "common", 12, 5.0, {"LAT-03": 2}, EXCEPT_LAT10).allowed
    off = gr.Guardrails(protect_page_sets="none", protect_page_exceptions="LAT-10")
    assert not off.protects("LAT-10", "rare", 1) and not off.protects("LAT-09", "rare", 1)


@pytest.fixture
def asked():
    writes: list[dict] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    if old is None:
        approvals._BOARD.pop("board", None)
    else:
        approvals.install(old)


@pytest.mark.score_impact
@pytest.mark.human_approval
def test_with_the_committed_rules_the_other_sale_guards_still_bind_lat10(asked):
    """The exception lifts protect_page_sets for LAT-10 and nothing else: the move-impact guard (facts unread, so
    the worst case), the approval threshold and the sell floor still decide."""
    rules = gr.load_guardrails().rules
    assert rules.max_score_loss_per_move > 0 and rules.human_approval_above == 250
    me = {
        "id": "t01",
        "assets": [{"id": 77, "kind": "card", "ref": "LAT-10", "rarity": "rare", "your_value": 35.0}],
        "album": {"pages": [{"set": "LAT", "have": 4, "of": 10, "complete": False}]},
    }
    base = replace(ctx({"LAT-10": 1}), cards=mi.our_cards(me), breakers=frozenset(), approvals=approvals.EMPTY)

    def ask(price: int) -> gr.Verdict:
        action = gr.Action("sell", "LAT-10", "rare", price, 35.0, counterparty=gr.ANY_TEAM, asset=77)
        return gr.check(action, base, rules)

    assert ask(200).allowed and ask(36).allowed  # above our value: a gain, whoever takes it
    assert asked == []
    assert "human approval" in str(ask(250)) and asked[-1]["card"] == "LAT-10"  # 250 and up: a human first
    assert "your_value" in str(ask(34))  # never below what the copy is worth to us
