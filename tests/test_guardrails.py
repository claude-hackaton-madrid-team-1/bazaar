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
    assert "cash_floor" in str(gr.check(gr.Action("buy", "LAV-09", "rare", 60), ctx(cash=150), rules))
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


# ---------------------------------------------------------------- our venue (build only)

VENUE_KINDS = ("venue_open", "venue_close", "venue_fee", "venue_announce", "broker_match")


def test_the_committed_file_keeps_our_venue_off_and_holds_no_bond_reserve():
    """Team decision Sat 06:08: #71 ships with allow_venue_open = false. While it is false NO reserve is held:
    the floor every writer sees is `cash_floor` alone (guardrails.effective_cash_floor zeroes the reserve)."""
    rules = REAL.rules
    assert rules.allow_venue_open is False
    assert (rules.cash_floor, rules.venue_bond_reserve, rules.venue_open_after_game_hours) == (100, 270, 6.5)
    for has_venue in (False, True):
        c = ctx(cash=400, has_venue=has_venue)
        assert gr.effective_cash_floor(rules, c) == rules.cash_floor
        assert gr.floor_text(rules, c) == f"cash_floor {rules.cash_floor}"
    # a buy that leaves cash_floor + 1 is allowed: it would be refused if the 270 reserve were applied
    leaves_101 = gr.check(gr.Action("buy", "LAV-09", "rare", 60), ctx(cash=161), rules)
    assert leaves_101.allowed, leaves_101
    assert gr.Guardrails().allow_venue_open is False  # the model's default stays off too


def test_allow_venue_open_false_refuses_every_venue_write_but_close():
    off = gr.Guardrails()
    for kind in ("venue_open", "venue_fee", "venue_announce", "broker_match"):
        assert "allow_venue_open = false" in str(gr.check(gr.Action(kind), ctx(cash=600, t_hours=7.0), off))
    assert gr.check(gr.Action("venue_close", "v07"), ctx(), off).allowed
    on = gr.Guardrails(allow_venue_open=True)
    for kind in VENUE_KINDS:
        assert gr.check(gr.Action(kind), ctx(cash=600, t_hours=7.0), on).allowed


def test_the_venue_bond_and_opening_fee_never_take_cash_below_the_floor():
    on = gr.Guardrails(allow_venue_open=True, cash_floor=100)
    assert gr.VENUE_COST == 270
    assert gr.check(gr.Action("venue_open"), ctx(cash=370, t_hours=6.5), on).allowed
    denied = gr.check(gr.Action("venue_open"), ctx(cash=369, t_hours=6.5), on)
    assert "cash 369 - venue bond and fee 270 < cash_floor 100" in str(denied)
    # the bond is not a purchase: no rarity cap, no spend cap
    assert gr.check(gr.Action("venue_open"), ctx(cash=600, spent_last_hour=150, t_hours=7.0), on).allowed


def test_the_venue_opens_once_and_not_before_its_game_hour():
    on = gr.Guardrails(allow_venue_open=True, cash_floor=100)
    early = gr.check(gr.Action("venue_open"), ctx(cash=600, t_hours=6.49), on)
    assert "game hour 6.49 < venue_open_after_game_hours 6.5" in str(early)
    twice = gr.check(gr.Action("venue_open"), ctx(cash=600, t_hours=7.0, has_venue=True), on)
    assert "never open a second one" in str(twice)
    assert gr.check(gr.Action("broker_match"), ctx(t_hours=1.0, has_venue=True), on).allowed  # matching any time


def test_the_bond_reserve_lifts_the_floor_for_every_purchase_until_the_venue_opens():
    planned = gr.Guardrails(allow_venue_open=True, cash_floor=100, venue_bond_reserve=270)
    buy = gr.Action("buy", "LAV-09", "rare", 30)
    assert gr.effective_cash_floor(planned, ctx()) == 370
    assert gr.check(buy, ctx(cash=400), planned).allowed  # 370 left
    denied = gr.check(buy, ctx(cash=399), planned)
    assert "cash 399 - 30 < cash_floor 100 + venue_bond_reserve 270" in str(denied)
    opened = ctx(cash=131, has_venue=True)
    assert gr.effective_cash_floor(planned, opened) == 100 and gr.check(buy, opened, planned).allowed
    assert "cash 129 - 30 < cash_floor 100" in str(gr.check(buy, ctx(cash=129, has_venue=True), planned))
    not_planned = gr.Guardrails(allow_venue_open=False, cash_floor=100)
    assert gr.effective_cash_floor(not_planned, ctx()) == 100  # no venue planned: nothing to reserve


@pytest.mark.parametrize(
    ("venue", "runs"),
    [
        (None, False),
        ({"venue": "v07", "name": "Team 1 market", "status": "open"}, True),
        ({"venue": "v07", "status": "closing"}, True),
        ({"venue": "v07", "status": "closed"}, False),
        ({"venue": "s01", "status": "open", "starter": True}, False),
        ("v07", True),
        ("", False),
    ],
)
def test_runs_venue_reads_me_and_context_from_carries_it(venue, runs):
    assert gr.runs_venue({"venue": venue}) is runs
    ledger = gr.Ledger(Path("/nonexistent/ledger.jsonl"))
    assert gr.context_from({"cash": 400, "venue": venue}, 1, 0.1, ledger, REAL.rules).has_venue is runs


def test_the_kill_switch_and_the_pause_file_stop_every_venue_write():
    off = gr.Guardrails(allow_venue_open=True, trading_enabled=False)
    on = gr.Guardrails(allow_venue_open=True)
    for kind in VENUE_KINDS:
        assert "trading_enabled = false" in str(gr.check(gr.Action(kind), ctx(cash=600), off))
        assert "pause file" in str(gr.check(gr.Action(kind), ctx(cash=600, paused=True), on))


@pytest.mark.parametrize("venue", ["s05", {"venue": "s05", "status": "open"}])
def test_a_venue_named_next_to_a_starter_broker_key_is_the_free_stall(venue):
    """The kit: /me carries `starter_broker_key` while we have the free stall. Neither shape of the stall may
    drop the bond reserve for buyers or block our own opening."""
    me = {"cash": 400, "venue": venue, "starter_broker_key": "bk_" + "S7a11Only"}
    assert gr.runs_venue(me) is False
    ledger = gr.Ledger(Path("/nonexistent/ledger.jsonl"))
    planned = gr.Guardrails(allow_venue_open=True, cash_floor=100, venue_bond_reserve=270)
    c = gr.context_from(me, 400, 6.5, ledger, planned)
    assert gr.effective_cash_floor(planned, c) == 370
    assert gr.check(gr.Action("venue_open"), c, planned).allowed
    assert not gr.check(gr.Action("buy", "LAV-09", "rare", 40), c, planned).allowed  # 360 < 370


def test_a_me_venue_marked_starter_false_is_ours_whatever_the_stall_key_says():
    me = {"venue": {"venue": "v09", "status": "open", "starter": False}, "starter_broker_key": "bk_" + "Stale0ne"}
    assert gr.runs_venue(me) is True


@pytest.mark.parametrize("venue", ["s05", {"venue": "s05", "status": "open"}])
def test_a_me_without_its_secrets_still_shows_the_free_stall(venue):
    """Security review round 4, P1: `holdings.without_secrets` drops `starter_broker_key`; the marker it keeps
    must still tell the stall apart from our venue, or the bond reserve drops and the opening is refused."""
    from bazaar_agent.holdings import without_secrets

    stripped = without_secrets({"cash": 400, "venue": venue, "starter_broker_key": "bk_" + "S7a11Only"})
    assert "starter_broker_key" not in stripped and gr.runs_venue(stripped) is False
    planned = gr.Guardrails(allow_venue_open=True, cash_floor=100, venue_bond_reserve=270)
    c = gr.context_from(stripped, 400, 6.5, gr.Ledger(Path("/nonexistent/l.jsonl")), planned)
    assert gr.effective_cash_floor(planned, c) == 370 and gr.check(gr.Action("venue_open"), c, planned).allowed


def duel_check(kind="duel_offer", price=105, limit=100, role="seller", days=None, weight=None, rules=REAL.rules):
    action = gr.Action(kind, "9", None, price, limit=limit, role=role, days=days, days_weight=weight)
    return gr.check(action, ctx(), rules)


def test_a_duel_move_outside_our_limit_is_denied():
    assert REAL.rules.duel_inside_limit
    assert duel_check().allowed and duel_check(role="buyer", price=95).allowed
    for kind in ("duel_offer", "duel_accept"):
        assert "duel_inside_limit" in str(duel_check(kind, price=100))  # on the limit: no surplus
        assert "duel_inside_limit" in str(duel_check(kind, price=99))
        assert "duel_inside_limit" in str(duel_check(kind, price=101, role="buyer"))
    # Two issues: days always cost us |weight| each (the sign is unverified).
    assert "worth 95" in str(duel_check(price=105, days=5, weight=2.0))  # the bug: 105 with 5 days
    assert "worth 64" in str(duel_check(price=54, limit=60, role="buyer", days=5, weight=-2.0))
    assert duel_check(price=105, days=0, weight=2.0).allowed and duel_check(price=111, days=5, weight=-2.0).allowed
    assert "your_days_weight" in str(duel_check(days=0))  # cannot value the days: denied
    for days in (-5, 11, float("nan")):  # outside RULES.md's 0 to 10: -5 days would pass 95 as worth 105
        assert "days" in str(duel_check(price=95, days=days, weight=2.0)), days
    assert "cannot value" in str(duel_check(price=None)) and "cannot value" in str(duel_check(limit=None))
    assert "cannot value" in str(duel_check(role=None))
    off = gr.parse_guardrails("- `duel_inside_limit` = false — x").rules
    assert duel_check(price=50, rules=off).allowed
