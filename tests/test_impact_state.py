"""MI1: the decider reads the score guard's estimate (`impact_board.sell_state`) of the copy a team swap gives and of
each spare the dealer sell desk could sell, and the dealer sell guard prices the exact copy it sells. No network, no
Postgres: the conftest's empty impact board reads no facts (every origin unknown, k at its fallback)."""

import json
from types import SimpleNamespace

from bazaar_agent import guardrails as gr
from bazaar_agent import impact_board
from bazaar_agent import move_impact as mi
from bazaar_agent.agents import dealer_sell_desk
from bazaar_agent.agents import team_desk as td
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.agents.team_desk import _Plan
from bazaar_agent.guardrails import Guardrails
from tests.agent_fakes import FakeTeam, clock
from tests.test_dealer_sell_desk import ME_DUP, talk
from tests.test_team_desk import THEM, TICK, Team, desk, their_offer, thread, trade, view
from tests.test_team_desk_jev import Asked

K = Guardrails().score_per_neg_point_fallback  # 0.053: no snapshot of ours measured k
LADDER = Guardrails().dealer_ladder_score
AS_STATE = set(mi.estimate("sell", "LAT-03", 1, 1.0, None, mi.fallback_slope(K), LADDER).as_state())
STATE_KEYS = AS_STATE | {"max_score_loss_per_move", "facts_read"}  # `sell_state`: the estimate plus the bar


class FactsBoard(impact_board.ImpactBoard):
    """An impact board that read `facts` (no Postgres); the conftest puts the empty one back after the test."""

    def __init__(self, facts: mi.Facts) -> None:
        super().__init__(None)
        self.facts = facts

    def read(self, tick: int) -> mi.Facts | None:
        return self.facts


# ---------------------------------------------------------------- the team desk: the copy a swap gives


def test_jev_reads_the_score_impact_of_the_copy_a_swap_gives(tmp_path):
    d, _ = desk(tmp_path, Team())
    jev = Asked(JevAdvice("yes", 0.95))
    d.converse(view(jev=jev), set())
    swap = jev.states[0]["swap"]
    impact = swap["score_impact"]
    received = 16 - 1  # LAV-02's worth to us (16.0) and the 1 P we add (`Swap.actions()`)
    assert swap["cash"] == -1 and set(impact) == STATE_KEYS
    assert impact["team_trade"] is True and impact["copy_origin"] == "unknown" and impact["facts_read"] is False
    # a team trade at private values: our LAT-03 #3 (your_value 1.2) leaves at what we receive
    assert impact["neg_points_delta"] == round(received - 1.2, 1)
    assert impact["score_delta"] == round((received - 1.2) * K, 3)


def test_an_accept_prices_the_copy_at_what_the_guard_sees_it_leave_for(tmp_path, monkeypatch):
    """Taking their offer we pay the fee: the guard's sale is the card's worth less their ask and the fee, and the
    decider's estimate is priced at that same number."""
    seen: list[gr.Action] = []
    real = td.check

    def spy(action, ctx, rules):
        seen.append(action)
        return real(action, ctx, rules)

    monkeypatch.setattr(td, "check", spy)
    d, _ = desk(tmp_path, Team())
    d.converse(view(), set())
    fair = thread(messages=[{"sender": THEM, "tick": TICK + 1, "text": "trato"}], offers=[their_offer(cash_out=1)])
    (a,) = d.proposals(view([fair], tick=TICK + 1))
    seen.clear()
    d.guard_accept(view(tick=TICK + 1), a)
    (sold,) = [x for x in seen if x.kind == "sell"]
    state = d.swap_state(view(tick=TICK + 1), a.trade, a.offer.net_cash, a.fee, a.thread_id, 0, "accept")
    impact = state["swap"]["score_impact"]
    assert sold.price == 16 - 1 - 3  # their ask 1 and the fee 3 (we are the accepting side)
    assert impact["neg_points_delta"] == round(sold.price - sold.your_value, 1)
    assert impact["score_delta"] == round((sold.price - sold.your_value) * K, 3)


def test_a_swap_the_desk_cannot_price_has_no_score_impact(tmp_path):
    d, _ = desk(tmp_path, Team())
    d._plan = _Plan(TICK, (trade(),), {})  # no worth for LAV-02: `_swap` cannot build the swap
    assert d.swap_state(view(), trade(), -1, 0, None, 0)["swap"]["score_impact"] is None


def test_a_broken_estimate_never_costs_the_swap_its_state(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("impact board down")

    monkeypatch.setattr(impact_board, "sell_state", boom)
    d, _ = desk(tmp_path, Team())
    state = d.swap_state(view(), trade(), -1, 0, None, 0)
    assert state["swap"]["score_impact"] is None and state["swap"]["give"]["card"] == "LAT-03"


# ---------------------------------------------------------------- the dealer sell desk: each spare, and its guard


def sell_desk() -> dealer_sell_desk.SellDesk:
    rules = Guardrails(dealer_sell_enabled=True)
    return dealer_sell_desk.SellDesk(FakeTeam(), rules, None, True, lambda line: None, lambda c: None)


def snap() -> SimpleNamespace:
    return SimpleNamespace(me=ME_DUP, clock=clock())


def test_the_dealer_sell_gate_reads_one_estimate_per_duplicate():
    state = sell_desk().gate_state(snap())
    dups, impacts = state["duplicates"], state["score_impact"]
    assert [e["card"] for e in impacts] == [d["card"] for d in dups] == ["LAT-03", "LAV-08"]
    for e in impacts:
        assert set(e) == STATE_KEYS | {"card"} and e["team_trade"] is False  # sold to a dealer
        # the cheapest copy at its own your_value (the desk's floor is never lower): only the dealer ladder moves
        assert e["neg_points_delta"] == 0.0 and e["score_delta"] == LADDER
    json.dumps(state)  # JSON-safe for Jev


def test_the_estimate_names_the_team_a_spare_was_bought_from():
    """The SAL-07 shape: our cheapest LAV-08 (#8, your_value 2.0) came from team t02; the decider sees it."""
    impact_board.install(FactsBoard(mi.Facts("t01", {8: mi.Origin("team", "t02", 23, 320)})))
    lav = sell_desk().gate_state(snap())["score_impact"][1]
    assert lav["card"] == "LAV-08" and lav["facts_read"] is True
    assert lav["copy_origin"] == "team" and lav["copy_from"] == "t02"


def test_a_broken_estimate_is_none_and_the_gate_state_still_builds(monkeypatch):
    real = impact_board.sell_state

    def flaky(me, ref, *args, **kwargs):
        if ref == "LAT-03":
            raise RuntimeError("impact board down")
        return real(me, ref, *args, **kwargs)

    monkeypatch.setattr(impact_board, "sell_state", flaky)
    state = sell_desk().gate_state(snap())
    assert state["score_impact"][0] is None and state["score_impact"][1]["card"] == "LAV-08"
    assert [d["card"] for d in state["duplicates"]] == ["LAT-03", "LAV-08"]


def test_the_dealer_sell_guard_prices_the_exact_copy_it_sells(tmp_path, monkeypatch):
    seen: list[gr.Action] = []
    real = gr.check

    def spy(action, ctx, rules):
        seen.append(action)
        return real(action, ctx, rules)

    monkeypatch.setattr(gr, "check", spy)  # `standard_hooks` imports `check` when it builds the guard
    t, _ = talk(tmp_path, FakeTeam(), floor=14)
    t.hooks.guard("dealer_sell", 20)
    t.hooks.guard("accept_sell", 14)
    assert [(a.kind, a.item, a.asset) for a in seen] == [("dealer_sell", "LAV-08", 8), ("accept_sell", "LAV-08", 8)]
