"""B8 wiring: the days-sign latch turns `duel_days_signed` on for v2's policy and guard together, and only then."""

from bazaar_agent import guardrails as gr
from bazaar_agent.agents import duel_days as dd
from bazaar_agent.agents.duel_v2 import V2Params, plan_moves
from bazaar_agent.agents.duelist import duel_action

SIM_TEXT = "primas you gain (+) or lose (-) per delivery day, 0-10"
CTX = gr.Context(cash=0, held={}, tick=0, t_hours=0)


def duel(meaning: str | None = SIM_TEXT, weight: float = 2.0) -> dict:
    """A two-issue duel one tick in: we sell at cost 100, gaining `weight` P per day if signed; the rival bid 70."""
    return {
        "duel": 1,
        "status": "live",
        "role": "seller",
        "issues": ["price", "days"],
        "your_days_weight": weight,
        "days_meaning": meaning,
        "your_limit": 100,
        "rival": "Rival Azul",
        "deadline_tick": 112,
        "decay_per_round": 0.08,
        "rounds": 0,
        "rival_offer": {"price": 70, "days": 10},  # worth 90 to us even signed: v2 anchors
        "your_offer": None,
        "messages": [{"tick": 100, "from": "Rival Azul", "price": 70, "days": 10, "text": ""}],
    }


def tick_rules(rules: gr.Guardrails, switch: dd.DaysSwitch, d: dict, real: bool) -> gr.Guardrails:
    """What `duel run` and the runtime do each tick."""
    switch.observe([d], real)
    return dd.effective_rules(rules, switch)


def test_with_duel_days_auto_off_the_rules_are_todays_even_after_a_signed_real_payload(tmp_path):
    rules = gr.Guardrails(duel_policy="v2")
    assert rules.duel_days_auto is False
    switch = dd.latch(tmp_path)
    assert tick_rules(rules, switch, duel(), real=True) is rules  # the very same object: nothing changes
    assert switch.verdict == "signed"  # the evidence is still recorded for when Marius flips the switch


def test_a_real_signed_payload_lets_v2_offer_ten_days_and_the_guard_allows_them(tmp_path):
    rules = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
    rules_t = tick_rules(rules, dd.latch(tmp_path), duel(weight=2.0), real=True)
    assert rules_t.duel_days_signed and not rules.duel_days_signed
    move = plan_moves([duel(weight=2.0)], 101, {1: 100}, V2Params.from_rules(rules_t))[1]
    assert move.kind == "offer" and move.days == 10
    assert gr.check(duel_action(duel(weight=2.0), move), CTX, rules_t).allowed
    today = plan_moves([duel(weight=2.0)], 101, {1: 100}, V2Params.from_rules(rules))[1]
    assert today.days == 0  # the worst case: every day may cost us


def test_the_simulators_words_and_null_never_turn_it_on(tmp_path):
    rules = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
    switch = dd.latch(tmp_path)
    assert tick_rules(rules, switch, duel(SIM_TEXT), real=False) is rules
    assert tick_rules(rules, switch, duel(None), real=True) is rules
    assert switch.verdict == "unknown"
    assert dd.real_game("https://bazaar.causaprima.ai") and dd.real_game("https://bazaar.causaprima.ai/api")
    assert not dd.real_game("https://bazaar-sim-production-1d48.up.railway.app")
    assert not dd.real_game("http://127.0.0.1:8765")


def test_disagreeing_real_payloads_turn_it_off_for_good_across_restarts(tmp_path):
    rules = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
    switch = dd.latch(tmp_path)
    assert tick_rules(rules, switch, duel(SIM_TEXT), real=True).duel_days_signed
    assert tick_rules(rules, switch, duel("each day costs you primas"), real=True) is rules
    restarted = dd.latch(tmp_path)
    assert restarted.verdict == "conflict" and tick_rules(rules, restarted, duel(SIM_TEXT), real=True) is rules


def test_v1_never_goes_signed_even_when_the_rules_say_so(tmp_path):
    rules = gr.Guardrails(duel_policy="v1", duel_days_auto=True)
    rules_t = tick_rules(rules, dd.latch(tmp_path), duel(), real=True)
    assert rules_t.duel_days_signed  # the flag flips ...
    action = gr.Action("duel_offer", "1", price=95, limit=100, role="seller", days=10, days_weight=2.0)
    assert not gr.check(action, CTX, rules_t).allowed  # ... but the guard keeps #60's worst case for v1


def test_the_runtimes_duel_move_follows_the_latch_too(tmp_path):
    from tests.agent_fakes import clock
    from tests.runtime_fakes import Public, Team, backend
    from tests.test_runtime_tools import run

    two = duel(weight=2.0) | {"duel": 7, "started_tick": 100, "deadline_tick": 112}
    rules = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
    b = backend(tmp_path, live=True, team=Team(duels=[two]), rules=rules, public=Public(now=clock(tick=101)))
    assert dd.real_game(b.settings.bazaar_url)
    moved, _ = run(b, "duel_move", {"duel_id": 7})
    assert moved["request"]["kind"] == "offer" and moved["request"]["days"] == 10
    assert dd.latch(b.settings.data_dir).verdict == "signed"
    off = backend(tmp_path / "off", live=True, team=Team(duels=[two]), rules=gr.Guardrails(duel_policy="v2"),
                  public=Public(now=clock(tick=101)))  # fmt: skip
    today, _ = run(off, "duel_move", {"duel_id": 7})
    assert today["request"]["days"] == 0
