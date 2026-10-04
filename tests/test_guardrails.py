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
    assert "max_spend" in str(gr.check(gr.Action("buy", "LAV-03", "common", 9), ctx(spent_last_hour=245), rules))
    assert "already hold LAV-01" in str(gr.check(gr.Action("buy", "LAV-01", "common", 5), ctx(), rules))


def test_kill_switch_accept_quota_sells_and_flags():
    rules = REAL.rules
    assert "pause file" in str(gr.check(gr.Action("bid", "LAV-03", "common", 9), ctx(paused=True), rules))
    off = gr.parse_guardrails("- `trading_enabled` = false — x").rules
    assert not gr.check(gr.Action("bid", "LAV-03", "common", 9), ctx(), off).allowed
    assert "accept(s) already" in str(gr.check(gr.Action("duel_accept", "7"), ctx(accepts_this_tick=1), rules))
    assert "your_value" in str(gr.check(gr.Action("sell", "LAT-08", "rare", 30, your_value=35.0), ctx(), rules))
    assert gr.check(gr.Action("sell", "LAT-08", "rare", 40, your_value=35.0), ctx(held={"LAT-08": 2}), rules).allowed
    last_lat08 = gr.check(gr.Action("sell", "LAT-08", "rare", 40, your_value=35.0), ctx(held={"LAT-08": 1}), rules)
    assert "protect_page_sets" in str(last_lat08)  # La Latina stays protected...
    lat10 = gr.Action("sell", "LAT-10", "rare", 200, your_value=35.0)  # ...but for its one card (SX1)
    assert gr.check(lat10, ctx(held={"LAT-10": 1}), rules).allowed
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
    legendary = gr.check(gr.Action("bid", "LAV-12", "legendary", 150), ctx(), rules)
    assert "no max_price for rarity 'legendary'" in str(legendary)
    assert not gr.check(gr.Action("buy", "XYZ-01", None, 5), ctx(), rules).allowed
    # An epic has a hard cap since buy targets (GUARDRAILS.md `max_price_epic`); without the file, none.
    epic = gr.check(gr.Action("bid", "LAV-11", "epic", rules.max_price_epic + 1), ctx(), rules)
    assert f"max_price_epic {rules.max_price_epic}" in str(epic)
    assert "no max_price for rarity 'epic'" in str(
        gr.check(gr.Action("bid", "LAV-11", "epic", 5), ctx(), gr.Guardrails())
    )


# ---------------------------------------------------------------- our venue (build only)

VENUE_KINDS = ("venue_open", "venue_close", "venue_fee", "venue_announce", "broker_match")


def test_the_committed_file_runs_our_venue_with_a_5_floor_and_holds_no_reserve_once_it_is_open():
    """Our venue v19 opened Sat 3 Oct at game hour 3.5 (allow_venue_open = true from 3.0); cash_floor then went
    100 -> 50 so the taker can buy again, 50 -> 20 (with max_spend_per_game_hour 150 -> 250) at ~16:35 by
    team decision, and 20 -> 5 at ~17:20 (Opus decider, to unblock LAV-10 from Los Pícaros). With our venue
    open the floor is `cash_floor` alone; without one (the starter stall does not
    count) every purchase would still keep `cash_floor` + `venue_bond_reserve`."""
    rules = REAL.rules
    assert rules.allow_venue_open is True
    assert (rules.cash_floor, rules.venue_bond_reserve, rules.venue_open_after_game_hours) == (5, 270, 3.0)
    assert rules.max_spend_per_game_hour == 250
    opened = ctx(cash=119, has_venue=True)
    assert gr.effective_cash_floor(rules, opened) == 5 and gr.floor_text(rules, opened) == "cash_floor 5"
    buy = gr.Action("buy", "LAV-09", "rare", 92)
    assert gr.check(buy, opened, rules).allowed  # 119 - 92 = 27: refused at the old 50 floor
    assert "cash 119 - 115 < cash_floor 5" in str(gr.check(gr.Action("buy", "LAV-09", "rare", 115), opened, rules))
    assert gr.effective_cash_floor(rules, ctx(cash=400)) == 275  # no venue of ours: 5 + 270
    assert gr.Guardrails().allow_venue_open is False  # the model's default stays off: only the file turns it on


def test_the_committed_duel_endgame_is_b11s_defence():
    """A rival who waits us out must not get the minimum: in the last tick only, and never below 0.3 of the pie."""
    assert (REAL.rules.duel_endgame_ticks, REAL.rules.duel_endgame_min_share) == (1, 0.3)


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


def test_dealer_final_lift_off_keeps_every_cap_as_today():
    rules = REAL.rules
    assert rules.dealer_final_lift == 0
    assert rules.final_cap_for("uncommon") == rules.max_price_uncommon
    final = gr.check(gr.Action("accept_buy", "LAV-08", "uncommon", 31, final=True), ctx(), rules)
    assert str(final) == "denied: price 31 > max_price_uncommon 30"


def test_dealer_final_lift_lets_only_a_final_pass_the_card_cap():
    rules = gr.parse_guardrails("- `dealer_final_lift` = 0.15 — x").rules
    assert (rules.final_cap_for("uncommon"), rules.final_cap_for("rare"), rules.final_cap_for("common")) == (29, 92, 13)
    assert rules.final_cap_for("pack") == rules.max_price_pack  # packs keep their cap
    assert rules.final_cap_for("epic") is None  # no cap, never bought
    assert gr.check(gr.Action("accept_buy", "LAV-08", "uncommon", 29, final=True), ctx(), rules).allowed
    assert gr.check(gr.Action("bid", "LAV-08", "uncommon", 29, final=True), ctx(), rules).allowed  # meet her final
    above = gr.check(gr.Action("accept_buy", "LAV-08", "uncommon", 30, final=True), ctx(), rules)
    assert "dealer final cap 29 (max_price_uncommon 26 lifted)" in str(above)
    for plain in (gr.Action("accept_buy", "LAV-08", "uncommon", 27), gr.Action("bid", "LAV-08", "uncommon", 27)):
        assert "price 27 > max_price_uncommon 26" in str(gr.check(plain, ctx(), rules))  # our own bids: the cap
    opening = gr.Action("buy", "LAV-08", "uncommon", 27, final=True)  # opening a thread is never a final
    assert not gr.check(opening, ctx(), rules).allowed
    pack = gr.Action("accept_buy", "sobre_barrio", "pack", 21, final=True)
    assert "max_price_pack 20" in str(gr.check(pack, ctx(), rules))


def test_dealer_final_lift_still_meets_cash_floor_and_hourly_spend():
    rules = gr.parse_guardrails("- `dealer_final_lift` = 0.25 — x").rules
    final = gr.Action("accept_buy", "LAV-09", "rare", 95, final=True)
    assert gr.check(final, ctx(cash=400), rules).allowed
    assert "cash_floor" in str(gr.check(final, ctx(cash=360), rules))
    assert "max_spend_per_game_hour" in str(gr.check(final, ctx(spent_last_hour=60), rules))
    with pytest.raises(gr.GuardrailsError):
        gr.parse_guardrails("- `dealer_final_lift` = 0.9 — too much")


WORST = REAL.rules.model_copy(update={"duel_days_signed_roles": "none"})  # the worst case, whatever GUARDRAILS.md sets


def duel_check(kind="duel_offer", price=105, limit=100, role="seller", days=None, weight=None, rules=WORST):
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
    v1 = REAL.rules.model_copy(update={"duel_policy": "v1"})
    assert "your_days_weight" in str(duel_check(days=0, rules=v1))  # v1 cannot value the days: denied
    for days in (-5, 11, float("nan")):  # outside RULES.md's 0 to 10: -5 days would pass 95 as worth 105
        assert "days" in str(duel_check(price=95, days=days, weight=2.0)), days
    assert "cannot value" in str(duel_check(price=None)) and "cannot value" in str(duel_check(limit=None))
    assert "cannot value" in str(duel_check(role=None))
    off = gr.parse_guardrails("- `duel_inside_limit` = false — x").rules
    assert duel_check(price=50, rules=off).allowed


def test_under_v2_a_duel_move_outside_our_limit_is_still_denied():
    """v2 lets 0 days through without a weight (they cost nothing under either sign, B2c); everything else holds."""
    v2 = WORST.model_copy(update={"duel_policy": "v2"})
    assert duel_check(rules=v2).allowed and duel_check(role="buyer", price=95, rules=v2).allowed
    for kind in ("duel_offer", "duel_accept"):
        assert "duel_inside_limit" in str(duel_check(kind, price=100, rules=v2))  # on the limit: no surplus
        assert "duel_inside_limit" in str(duel_check(kind, price=99, rules=v2))
        assert "duel_inside_limit" in str(duel_check(kind, price=101, role="buyer", rules=v2))
        assert "duel_inside_limit" in str(duel_check(kind, price=100, days=0, rules=v2))  # 0 days: still the limit
    assert duel_check(days=0, rules=v2).allowed  # 0 days without a weight: free
    assert "your_days_weight" in str(duel_check(days=5, rules=v2))  # days > 0 without a weight: still denied
    assert "worth 95" in str(duel_check(price=105, days=5, weight=2.0, rules=v2))  # unsigned: the worst case
    for days in (-5, 11, float("nan")):
        assert "days" in str(duel_check(price=95, days=days, weight=2.0, rules=v2)), days
    assert "cannot value" in str(duel_check(price=None, rules=v2)) and "cannot value" in str(
        duel_check(role=None, rules=v2)
    )


@pytest.mark.parametrize("value", ["abuela;chato", "Abuela", "abuela, ,chato"])
def test_a_bad_trusted_dealer_list_fails_fast(value):
    with pytest.raises(gr.GuardrailsError, match="flag_trusted_dealers"):
        gr.parse_guardrails(f"- `flag_trusted_dealers` = {value} — x")


def test_a_second_venue_needs_max_venues_2():
    ctx_one = ctx(has_venue=True)
    one = gr.check(
        gr.Action("venue_open", "venue", None, 270),
        ctx_one,
        gr.Guardrails(allow_venue_open=True, venue_open_after_game_hours=0),
    )
    assert not one.allowed and "never open a second one" in str(one)
    two = gr.check(
        gr.Action("venue_open", "venue", None, 270),
        ctx_one,
        gr.Guardrails(allow_venue_open=True, venue_open_after_game_hours=0, max_venues=2),
    )
    assert "never open a second one" not in str(two)
