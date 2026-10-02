from pathlib import Path

import pytest

from bazaar_agent import guardrails as gr

REAL = gr.load_guardrails()


def ctx(**kw):
    base = {"cash": 400, "held": {"LAV-01": 1}, "tick": 10, "t_hours": 1.0}
    return gr.Context(**{**base, **kw})


def test_the_committed_file_parses_and_every_rule_is_enforced_somewhere():
    ids = {r.rule_id for r in REAL.lines}
    assert ids == set(gr.Guardrails.model_fields)  # every field is set explicitly in GUARDRAILS.md
    assert ids <= set(gr.ENFORCED_BY)
    assert REAL.principles  # principles are shown even though code does not enforce them


def test_unknown_rule_bad_value_and_duplicates_fail_fast():
    with pytest.raises(gr.GuardrailsError, match="typo_rule"):
        gr.parse_guardrails("- `typo_rule` = 1 — oops")
    with pytest.raises(gr.GuardrailsError, match="cash_floor"):
        gr.parse_guardrails("- `cash_floor` = lots — oops")
    with pytest.raises(gr.GuardrailsError, match="twice"):
        gr.parse_guardrails("- `cash_floor` = 1 — a\n- `cash_floor` = 2 — b")
    with pytest.raises(gr.GuardrailsError, match="not a rule line"):
        gr.parse_guardrails("- `cash_floor` 270 missing equals")


def test_values_are_typed():
    rules = gr.parse_guardrails("- `trading_enabled` = false — x\n- `jev_timeout_s` = 2.5 — y").rules
    assert (rules.trading_enabled, rules.jev_timeout_s, rules.cash_floor) == (False, 2.5, 270)


def test_price_caps_cash_floor_spend_cap_and_album():
    rules = REAL.rules
    assert gr.check(gr.Action("bid", "LAV-03", "common", 9), ctx(), rules).allowed
    assert "max_price_common" in str(gr.check(gr.Action("bid", "LAV-03", "common", 13), ctx(), rules))
    assert "cash_floor" in str(gr.check(gr.Action("buy", "LAV-09", "rare", 60), ctx(cash=300), rules))
    assert "max_spend" in str(gr.check(gr.Action("buy", "LAV-03", "common", 9), ctx(spent_last_hour=145), rules))
    assert "already hold LAV-01" in str(gr.check(gr.Action("buy", "LAV-01", "common", 5), ctx(), rules))


def test_kill_switch_accept_quota_sells_and_flags():
    rules = REAL.rules
    assert "pause file" in str(gr.check(gr.Action("bid", "LAV-03", "common", 9), ctx(paused=True), rules))
    off = gr.parse_guardrails("- `trading_enabled` = false — x").rules
    assert not gr.check(gr.Action("bid", "LAV-03", "common", 9), ctx(), off).allowed
    assert "accept(s) already" in str(gr.check(gr.Action("duel_accept", "7"), ctx(accepts_this_tick=1), rules))
    assert "your_value" in str(gr.check(gr.Action("sell", "LAT-09", "rare", 30, your_value=35.0), ctx(), rules))
    assert gr.check(gr.Action("sell", "LAT-09", "rare", 40, your_value=35.0), ctx(), rules).allowed
    assert "allow_flags" in str(gr.check(gr.Action("flag", "m1"), ctx(), rules))


def test_ledger_counts_spend_per_game_hour_and_accepts_per_tick(tmp_path: Path):
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 10, 0.5, 7, "LAV-03")
    ledger.record("spend", 70, 1.6, 9, "LAV-04")
    ledger.record("accept", 70, 1.6, 9, "LAV-04")
    assert ledger.spent_since(0.7) == 9 and ledger.spent_since(0.0) == 16
    assert (ledger.accepts_in_tick(70), ledger.accepts_in_tick(71)) == (1, 0)
    me = {"cash": 384, "assets": [{"kind": "card", "ref": "LAV-03"}, {"kind": "pack", "ref": "sobre_barrio"}]}
    c = gr.context_from(me, 70, 1.6, ledger, gr.Guardrails())
    assert (c.cash, c.held, c.spent_last_hour, c.accepts_this_tick) == (384, {"LAV-03": 1}, 9, 1)
