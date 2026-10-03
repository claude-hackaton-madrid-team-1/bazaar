"""The official value cap (GET /api/me/value, Day-2 hint 1) on every path that buys: the runtime tools, the N17
swaps, and the ranking checks that must never read it. No network: every value read is a stub."""

import json

import pytest

from bazaar_agent import opportunities as op
from bazaar_agent import strategy
from bazaar_agent.agents.seller import Swap
from bazaar_agent.agents.team_desk import DeskView
from bazaar_agent.guardrails import Action, Context, Guardrails, check
from bazaar_agent.official_values import OfficialValues
from bazaar_agent.runtime import tools as tl
from tests.runtime_fakes import Team, backend
from tests.test_rivals import AMAP, VENUES
from tests.test_rivals import ctx as rivals_ctx
from tests.test_rivals import market as rivals_market
from tests.test_strategy import ME, PARAMS, RULES, playbook
from tests.test_team_desk import THEM, TICK, desk, their_offer, thread, view
from tests.test_team_desk import Team as DeskTeam

pytestmark = pytest.mark.official_values

HELD = {"LAV-01": 1, "LAV-06": 1, "LAT-03": 2, "LAT-09": 1}


class ValuedTeam(Team):
    """The runtime's team client with the SDK's `value(card)`: GET /api/me/value, answered from `values`."""

    def __init__(self, values, **kw):
        super().__init__(**kw)
        self._values = dict(values)
        self.value_reads: list[str] = []

    def value(self, card):
        self.value_reads.append(card)
        return {"card": card, "your_value": self._values[card]}


class BrokenValueTeam(Team):
    def value(self, card):
        raise RuntimeError("network: GET /api/me/value")


def run(b, name, args):
    text, failed = tl.call(tl.BY_NAME[name], b, args, tl.secrets_of(b.settings))
    assert not failed, text
    return json.loads(text)


def book(values: dict[str, float]) -> OfficialValues:
    return OfficialValues(lambda card: {"card": card, "your_value": values[card]})


def never_read() -> tuple[OfficialValues, list[str]]:
    asked: list[str] = []

    def read(card):
        asked.append(card)
        raise AssertionError(f"a ranking check read the official value of {card}")

    return OfficialValues(read), asked


# ---------------------------------------------------------------- runtime tools (desk / MCP)


def test_dealer_buy_above_the_official_value_is_refused_by_the_tool(tmp_path):
    team = ValuedTeam({"LAV-08": 15.0})
    answer = run(backend(tmp_path, team=team), "dealer_buy", {"item": "LAV-08", "max_price": 20, "start": 10})
    assert answer["status"] == "rejected" and "official value" in answer["guardrail"]
    assert "price 20 > official value 15" in answer["guardrail"]
    assert team.value_reads == ["LAV-08"] and team.sent == []


def test_dealer_buy_at_or_under_the_official_value_is_approved_as_a_dry_run(tmp_path):
    team = ValuedTeam({"LAV-08": 25.0})
    answer = run(backend(tmp_path, team=team), "dealer_buy", {"item": "LAV-08", "max_price": 20, "start": 10})
    assert answer["status"] == "approved" and answer["guardrail"] == "allowed" and answer["sent"] is False


def test_the_official_value_margin_lowers_the_dealer_buy_cap(tmp_path):
    team = ValuedTeam({"LAV-08": 25.0})
    b = backend(tmp_path, team=team, rules=Guardrails(official_value_margin=6))
    answer = run(b, "dealer_buy", {"item": "LAV-08", "max_price": 20, "start": 10})
    assert answer["status"] == "rejected" and "official_value_margin 6" in answer["guardrail"]


def test_a_board_bid_above_the_official_value_is_refused_by_sell_bid(tmp_path):
    team = ValuedTeam({"LAV-09": 50.0})
    answer = run(backend(tmp_path, team=team), "sell_bid", {"ref": "LAV-09", "price": 60})
    assert answer["status"] == "rejected" and "official value 50" in answer["guardrail"]
    assert team.sent == []


def test_a_board_bid_under_the_official_value_is_approved_by_sell_bid(tmp_path):
    team = ValuedTeam({"LAV-09": 70.0})
    answer = run(backend(tmp_path, team=team), "sell_bid", {"ref": "LAV-09", "price": 60})
    assert answer["status"] == "approved" and answer["guardrail"] == "allowed" and answer["sent"] is False


def test_a_rule_that_already_refuses_the_bid_spends_no_value_read(tmp_path):
    team = ValuedTeam({"LAV-09": 900.0})
    answer = run(backend(tmp_path, team=team), "sell_bid", {"ref": "LAV-09", "price": 500})
    assert answer["status"] == "rejected" and "max_price_rare 80" in answer["guardrail"]
    assert team.value_reads == []  # checked last: the key's 5 req/s is not spent on a buy refused anyway


def test_a_team_client_without_value_refuses_every_card_buy(tmp_path):
    b = backend(tmp_path, team=Team())  # an SDK without `value()`: fail closed
    for name, args in (
        ("dealer_buy", {"item": "LAV-08", "max_price": 20, "start": 10}),
        ("sell_bid", {"ref": "LAV-09", "price": 60}),
    ):
        answer = run(b, name, args)
        assert answer["status"] == "rejected" and "could not be read" in answer["guardrail"], (name, answer)
    assert b.values.failures == 2


def test_a_failing_value_read_refuses_the_buy(tmp_path):
    answer = run(backend(tmp_path, team=BrokenValueTeam()), "sell_bid", {"ref": "LAV-09", "price": 60})
    assert answer["status"] == "rejected" and "official value of LAV-09 could not be read" in answer["guardrail"]


# ---------------------------------------------------------------- N17 swaps


def swap(your_value=1.2, give_cash=0, want_cash=0) -> Swap:
    """Our duplicate LAT-03 (+ cash we add) for any LAV-02 (+ cash they add), worth 16 to our model."""
    return Swap(3, "LAT-03", "common", your_value, "LAV-02", "common", 16.0, "rastro", THEM, give_cash, want_cash, 20)


def swap_verdicts(s: Swap, official: float) -> list[str]:
    ctx = Context(400, dict(HELD), TICK, 1.5, values=book({"LAV-02": official}))
    return [p for a in s.actions() for p in check(a, ctx, Guardrails()).violations]


def test_a_swap_whose_cash_plus_copy_given_exceeds_the_official_value_is_refused():
    (why,) = swap_verdicts(swap(give_cash=9), official=10.0)  # 9 + 1.2 > 10
    assert "price 9 + copy given 1.2 > official value 10 of LAV-02" in why


def test_a_fair_swap_passes_the_official_value_cap():
    assert swap_verdicts(swap(give_cash=8), official=10.0) == []  # 8 + 1.2 <= 10


def test_their_cash_lowers_what_the_swap_costs_us():
    assert any("official value" in p for p in swap_verdicts(swap(your_value=12.0), official=10.0))
    assert swap_verdicts(swap(your_value=12.0, want_cash=3), official=10.0) == []  # 12 - 3 <= 10


def valued_view(official: float, **kw) -> tuple[DeskView, OfficialValues]:
    values = book({"LAV-02": official})
    base = view(**kw)
    return (
        DeskView(**{**base.__dict__, "ctx": lambda _t: Context(400, dict(HELD), base.tick, 1.5, values=values)}),
        values,
    )


def taken_counter(tmp_path):
    d, _ = desk(tmp_path, DeskTeam())
    d.converse(view(), set())
    fair = thread(messages=[{"sender": THEM, "tick": TICK + 1, "text": "trato"}], offers=[their_offer(cash_out=1)])
    (a,) = d.proposals(view([fair], tick=TICK + 1))
    return d, a  # we pay their 1 P + the 3 P fee and give LAT-03 (1.2): 5.2 for LAV-02


def test_the_team_desk_refuses_to_accept_a_swap_above_the_official_value(tmp_path):
    d, a = taken_counter(tmp_path)
    v, values = valued_view(5.0, tick=TICK + 1)
    verdict = d.guard_accept(v, a)
    assert not verdict.allowed and any("official value 5 of LAV-02" in p for p in verdict.violations)
    assert values.reads == 1


def test_the_team_desk_accepts_a_swap_under_the_official_value(tmp_path):
    d, a = taken_counter(tmp_path)
    v, _ = valued_view(16.0, tick=TICK + 1)
    assert d.guard_accept(v, a).allowed


# ---------------------------------------------------------------- ranking checks never read


def test_the_strategy_ranking_never_reads_an_official_value():
    values, asked = never_read()
    ctx = Context(cash=400, held={"LAV-01": 1, "LAT-03": 2, "LAT-09": 1}, tick=50, t_hours=1.0, values=values)
    ranked = strategy.guarded(playbook(), ctx, RULES)
    assert next(m for m in ranked.buys if m.ref == "LAV-02").guardrail == "allowed"
    assert asked == [] and values.reads == 0


def test_scoring_a_board_ask_never_reads_an_official_value():
    from bazaar_agent.agents.market import BoardOffer

    values, asked = never_read()
    ctx = Context(**{**rivals_ctx().__dict__, "values": values})
    offer = BoardOffer(6, "rastro", "t06", "ask", "LAV-08", 15, 73, None, 80, 30)
    s = op.score_offer(offer, rivals_market(), ME, PARAMS, Guardrails(), AMAP, VENUES["rastro"], ctx)
    assert s is not None and s.kind == "buy" and s.allowed
    assert asked == [] and values.reads == 0


def test_the_same_context_without_ranking_does_read_and_cap():
    values = book({"LAV-02": 4.0})
    ctx = Context(cash=400, held={}, tick=50, t_hours=1.0, values=values)
    verdict = check(Action("buy", "LAV-02", "common", 5), ctx, RULES)
    assert not verdict.allowed and "official value 4" in str(verdict) and values.reads == 1
