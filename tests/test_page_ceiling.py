"""A page completer's ladder top is not held to the dealer's fills (thread 1093, ticks 778-781).

The card was the last one missing from a page, so its value carried the whole page bonus, yet the taker's top was
the dealer's highest fill for its rarity (67) and every later shaping (the learned policy's walk, the persona prior,
a trickster's accept cap) was fill-derived too. Now its top is our value minus the minimum surplus, the rarity cap
and, at the open, the official value cap; the start (the lowest proven fill) and the step pace stay as before.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from bazaar_agent import approvals, intel, strategy
from bazaar_agent import guardrails as gr
from bazaar_agent.agents.dealer import BidPlan, Negotiation, decide
from bazaar_agent.agents.dealer import Move as DMove
from bazaar_agent.agents.dealer_plan import plan_dealer_buy
from bazaar_agent.agents.persona_desk import shape
from bazaar_agent.agents.taker import official_top
from bazaar_agent.agents.trickster import forgiving_plan
from bazaar_agent.learn.evolve import Ladder, LadderPolicy
from bazaar_agent.persona_model import parse_persona, parse_personas
from tests.test_intel import settle
from tests.test_strategy import CATALOG, ME, PARAMS

RULES = gr.Guardrails(max_price_rare=95)
CHATO = {
    "id": "chato",
    "status": "active",
    "level": 2,
    "menu": {"sells": [{"rarity": "rare", "sets": "released", "list_price": 77}]},
}
# Chato's rare sales to other teams: the lowest 48, the highest 67 (no one ever paid more for a rare).
FILLS = [
    settle(100 + i, 100 + i, "chato", team, "LAT-09", price, tick=10 + i, kind="card", persona="chato")
    for i, (team, price) in enumerate([("t03", 48), ("t05", 58), ("t07", 67)])
]


def me_missing(*missing: str) -> dict[str, Any]:
    """We hold one copy of every LAV page card except `missing`."""
    me = deepcopy(ME)
    page = ["LAV-01", "LAV-02", "LAV-06", "LAV-08", "LAV-09", "LAV-10"]
    held = {a["ref"] for a in me["assets"]}
    for i, ref in enumerate(r for r in page if r not in missing and r not in held):
        me["assets"].append({"id": 100 + i, "kind": "card", "ref": ref, "rarity": "rare", "your_value": 1.0})
    me["unlocked"] = ["abuela", "chato"]
    return me


def lav09(me: dict[str, Any], rules: gr.Guardrails = RULES) -> strategy.Move:
    book = strategy.build_playbook(me, CATALOG, FILLS, [CHATO], PARAMS, rules)
    return next(m for m in book.buys if m.ref == "LAV-09" and m.source == "chato")


# ---------------------------------------------------------------- bid_range and the strategy's dealer buy


def test_bid_range_drops_the_fill_ceiling_only_for_a_page_completer():
    assert strategy.bid_range([48, 58, 67], 58, 194.0, 95, 2) == (48, 67)  # today: never above what anyone paid
    assert strategy.bid_range([48, 58, 67], 58, 194.0, 95, 2, completes=True) == (48, 95)  # the rarity cap binds
    assert strategy.bid_range([48, 58, 67], 58, 80.0, 95, 2, completes=True) == (48, 78)  # our value - surplus binds
    assert strategy.bid_range([48, 58, 67], 58, 194.0, None, 2, completes=True) == (48, 192)


def test_the_last_missing_page_card_is_laddered_to_the_cap_from_the_lowest_fill():
    mv = lav09(me_missing("LAV-09"))
    assert mv.completes_page and mv.ladder == (48, 95, strategy.ladder_step(48, 67, RULES.dealer_max_ticks_per_thread))
    assert mv.limit == 95 and "--start 48 --max 95 --step 2" in mv.command
    assert "top 95: completes the page" in mv.reason


def test_a_card_that_does_not_complete_its_page_keeps_the_fill_ceiling():
    mv = lav09(me_missing("LAV-09", "LAV-08"))
    assert not mv.completes_page and mv.ladder is not None and mv.ladder[:2] == (48, 67) and mv.limit == 67


def test_the_rarity_cap_still_binds_a_page_completer():
    mv = lav09(me_missing("LAV-09"), RULES.model_copy(update={"max_price_rare": 80}))
    assert mv.ladder is not None and mv.ladder[:2] == (48, 80)


# ---------------------------------------------------------------- the shaping after the strategy


def completer(ladder: tuple[int, int, int] = (48, 95, 2), completes: bool = True) -> strategy.Move:
    return strategy.Move(
        "buy", "dealer_floor", "LAV-09", "rare", 194.0, 58.0, 136.0, 0.5, 10.0, "picaros", ("picaros",), "buy",
        ladder[1], "r", "cmd", ladder=ladder, completes_page=completes,
    )  # fmt: skip


def test_a_learned_policy_lowers_the_start_and_step_but_not_a_page_completers_top():
    policy = LadderPolicy("picaros", "card:rare", Ladder(48, 1, 67), "best replayed share", (48, 58, 67), 91, 3.5, 1060)
    plan = plan_dealer_buy(completer(), policy, None, RULES, 2.0)
    assert plan.move is not None and plan.move.ladder == (48, 95, 1) and plan.move.limit == 95
    assert "top 95 kept: completes the page" in plan.move.reason
    plain = plan_dealer_buy(completer(completes=False), policy, None, RULES, 2.0)
    assert plain.move is not None and plain.move.ladder == (48, 67, 1)  # any other card: as before


def test_a_persona_prior_never_lowers_a_page_completers_top():
    traits = {"patience": 0.3, "generosity": 0.2, "shrewdness": 0.9, "memory": 0.9, "strictness": 0.9}
    trick = {
        "id": "picaros", "status": "active", "level": 4, "traits": {**traits, "chattiness": 0.2},
        "menu": {"sells": [{"rarity": "rare", "list_price": 50}], "deals_per_team_per_hour": 6},
        "unlock": {"always": False}, "open_to_all": False,
    }  # fmt: skip
    personas = parse_personas([trick])

    def shaped(mv: strategy.Move) -> strategy.Move:
        (out,) = shape([mv], personas, {}, (), [], "t01", ["picaros"], 500, 30.0).moves
        return out

    plain = shaped(completer((30, 80, 3), completes=False))
    assert plain.ladder == (30, 63, 1) and plain.limit == 63  # the prior's walk lowers any other card's top
    mine = shaped(completer((30, 80, 3)))
    assert mine.ladder == (30, 80, 1) and mine.limit == 80 and "top 80 kept: completes the page" in mine.reason


PICAROS = {
    "id": "picaros",
    "kind": "trickster",
    "level": 4,
    "status": "active",
    "traits": {"chattiness": 0.8, "strictness": 0.1, "memory": 0.3, "shrewdness": 0.7},
    "menu": {"sells": [{"rarity": "rare", "sets": "released", "list_price": 63}]},
}
SOLD = [settle(i, i, "picaros", "t07", "LAV-10", p, tick=700 + i, kind="card", persona="picaros") for i, p in
        enumerate([48, 58, 67], 1)]  # fmt: skip


def test_a_trickster_final_inside_our_top_closes_a_page_completer_its_list_price_included():
    # Tick 1201-1205: "we take an ask only at or under 58, never at its list price 63", and its finals were 60-63.
    persona = parse_persona(PICAROS)
    plain = forgiving_plan(BidPlan(48, 2, 95), persona, "LAV-09", "rare", intel.tape(SOLD), gr.Guardrails())
    assert (plain.forgiving, plain.accept_max, plain.list_price) == (True, 54, 63)  # any other card: as before
    n = Negotiation(plain, [48, 49, 50])
    n.see_ask(73)
    assert decide(n, 63, 9, True).kind == "bid"  # its FINAL 63 is not its limit: we step by 1, never meeting it
    mine = forgiving_plan(BidPlan(48, 2, 95), persona, "LAV-09", "rare", intel.tape(SOLD), gr.Guardrails(), None, True)
    assert mine == BidPlan(48, 2, 95)  # a page completer: no forgiving plan
    n = Negotiation(mine, [48, 50, 52])
    n.see_ask(73)
    assert decide(n, 63, 9, True) == DMove("accept", 63, 9, "final within limit")  # its final closes the page
    assert decide(n, 96, 9, True).kind == "walk"  # a final above our top: never
    early = Negotiation(mine, [48])
    early.see_ask(73)
    assert decide(early, 73, 9, True).kind == "walk"  # its opening ask as a final: never taken (scores nothing)


# ---------------------------------------------------------------- the official value cap at the open


class Values:
    def __init__(self, value: float | None) -> None:
        self.value = value

    def cached(self, ref: str, tick: int, held: int) -> float | None:
        return self.value


@pytest.mark.parametrize(
    ("official", "expected"),
    [(77.0, 77), (149.9, None), (40.0, None), (None, None)],  # lowered; above the top; below the start; unread
)
def test_the_open_lowers_the_top_to_the_official_value(official: float | None, expected: int | None):
    ctx = gr.Context(cash=400, held={}, tick=5, t_hours=1.0, values=Values(official))  # type: ignore[arg-type]
    assert official_top(BidPlan(48, 2, 95), "LAV-09", ctx, gr.Guardrails()) == expected


def test_a_capped_plan_keeps_nothing_above_its_top():
    plan = BidPlan(48, 1, 95, final_max=99, lift_after=4, accept_max=95).capped(77)
    assert (plan.start, plan.max_price, plan.final_max, plan.accept_max) == (48, 77, 77, 77)
    assert BidPlan(48, 1, 95).capped(77).final_max is None


# ---------------------------------------------------------------- no human approval for buys


@pytest.fixture
def asked():
    """This process's approval board, with no database, the requests kept in a list (as tests/test_approvals.py)."""
    writes: list[dict] = []
    old = approvals.install(approvals.ApprovalBoard(None, write=writes.append))
    yield writes
    approvals._BOARD.pop("board", None)
    if old is not None:
        approvals.install(old)


@pytest.mark.human_approval
def test_buys_need_no_human_approval_sells_still_do(asked):
    rules = gr.Guardrails(cash_floor=0, max_spend_per_game_hour=1000, max_price_rare=95, human_approval_above=60)
    off = rules.model_copy(update={"human_approval_buys": False})
    ctx = gr.Context(cash=400, held={}, tick=781, t_hours=1.0, breakers=frozenset(), approvals=approvals.ApprovalBook())
    final = gr.Action("bid", "LAV-09", "rare", 63, final=True)
    assert gr.check(final, ctx, rules).violations == ("needs human approval: LAV-09 buy 63",)  # thread 1093's walk
    assert gr.check(final, ctx, off).allowed and len(asked) == 1  # no approval asked for: the final closes
    sell = gr.Action("sell", "LAT-09", "rare", 68, your_value=1.0)
    assert "needs human approval: LAT-09 sell 68" in gr.check(sell, ctx, off).violations
    over = gr.Action("bid", "LAV-09", "rare", 96)
    assert "price 96 > max_price_rare 95" in gr.check(over, ctx, off).violations  # every other cap still binds


def test_the_shipped_rules_turn_buy_approvals_off():
    rules = gr.load_guardrails().rules
    assert rules.human_approval_buys is False and rules.human_approval_above > 0


def test_a_card_an_open_offer_wants_is_refused_with_its_own_reason():
    from bazaar_agent.agents.seller import Commitments, committed_context

    base = gr.Context(cash=400, held={"LAV-01": 1}, tick=5, t_hours=1.0, breakers=frozenset())
    ctx = committed_context(base, Commitments(60, ("LAV-09",)))
    rules = gr.Guardrails(cash_floor=0, max_spend_per_game_hour=1000, max_price_rare=95)
    assert gr.check(gr.Action("buy", "LAV-09", "rare", 48), ctx, rules).violations[0] == (
        "an offer of ours, open or settling, already wants LAV-09 (block_buying_held_cards)"
    )  # the taker's thread for it is open: a second buy is still refused, but /me holds none
    assert (
        "we already hold LAV-01 (block_buying_held_cards)"
        in gr.check(gr.Action("buy", "LAV-01", "common", 5), ctx, rules).violations
    )
