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


def test_pack_buys_stop_at_max_packs_per_game_hour():
    rules = REAL.rules
    assert rules.max_packs_per_game_hour == 3
    for kind in ("buy", "bid", "accept_buy"):
        action = gr.Action(kind, "sobre_barrio", "pack", 17)
        assert gr.check(action, ctx(packs_last_hour=2), rules).allowed
        assert "max_packs_per_game_hour 3" in str(gr.check(action, ctx(packs_last_hour=3), rules))
    assert gr.check(gr.Action("buy", "LAV-03", "common", 9), ctx(packs_last_hour=3), rules).allowed


def test_the_ledger_counts_pack_spends_by_pack_id_in_the_last_game_hour(tmp_path: Path):
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 10, 0.2, 17, "sobre_barrio")  # older than one game hour at t=1.6
    ledger.record("spend", 60, 1.0, 17, "sobre_barrio")
    ledger.record("spend", 61, 1.1, 18, "sobre_barrio")
    ledger.record("spend", 62, 1.2, 9, "LAV-04")  # a card, not a pack
    ledger.record("accept", 63, 1.3, 0, "duel:7")  # not a spend
    assert ledger.packs_since(0.6) == {"sobre_barrio": 2}
    c = gr.context_from({"cash": 300, "assets": []}, 96, 1.6, ledger, gr.Guardrails())
    assert c.packs_last_hour == 2
    assert [gr.is_pack(i) for i in ("sobre_barrio", "LAV-04", "duel:7", "")] == [True, False, False, False]


def test_a_buy_with_no_price_cap_for_its_rarity_is_refused():
    rules = REAL.rules
    assert "no max_price for rarity 'epic'" in str(gr.check(gr.Action("bid", "LAV-11", "epic", 150), ctx(), rules))
    assert not gr.check(gr.Action("buy", "XYZ-01", None, 5), ctx(), rules).allowed
