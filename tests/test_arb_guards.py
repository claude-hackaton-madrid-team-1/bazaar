"""The two guarded exceptions to `block_buying_held_cards`: arbitrage (resell at once) and duplicates (keep
on their own surplus). Off by default: with the committed GUARDRAILS.md every held-card buy is refused as
before, whatever it claims."""

from pathlib import Path

from bazaar_agent import guardrails as gr

REAL = gr.load_guardrails().rules
ON = REAL.model_copy(
    update={
        "arb_enabled": True,
        "arb_min_net_spread": 3,
        "arb_max_inventory_p": 60,
        "dup_buy_enabled": True,
        "dup_min_surplus": 3.0,
        "dup_max_spend_per_hour": 40,
    }
)


def ctx(**kw):
    base = {"cash": 400, "held": {"LAV-01": 1, "LAV-09": 1}, "tick": 10, "t_hours": 1.0}
    return gr.Context(**{**base, **kw})


def arb(price=10, exit_net=5, item="LAV-01", kind="accept_buy"):
    return gr.Action(kind, item, "common", price, held_buy="arb", exit_net=exit_net)


def dup(price=15, value=28.0, item="LAV-09", kind="accept_buy"):
    return gr.Action(kind, item, "rare", price, held_buy="dup", next_copy_value=value)


def test_defaults_keep_todays_behaviour():
    assert (REAL.arb_enabled, REAL.dup_buy_enabled, REAL.block_buying_held_cards) == (False, False, True)
    for action in (arb(), dup(), gr.Action("accept_buy", "LAV-01", "common", 5)):
        verdict = gr.check(action, ctx(), REAL)
        assert not verdict.allowed
    assert "arb_enabled = false" in str(gr.check(arb(), ctx(), REAL))
    assert "dup_buy_enabled = false" in str(gr.check(dup(), ctx(), REAL))
    # a claim never loosens anything for a card we do not hold either: the switch is checked first
    assert "arb_enabled = false" in str(gr.check(arb(item="LAV-03"), ctx(), REAL))


def test_a_plain_buy_of_a_held_card_is_still_refused_with_the_switches_on():
    assert "block_buying_held_cards" in str(gr.check(gr.Action("accept_buy", "LAV-01", "common", 5), ctx(), ON))


def test_arbitrage_needs_the_net_spread():
    assert gr.check(arb(exit_net=3), ctx(), ON).allowed
    assert "arb_min_net_spread" in str(gr.check(arb(exit_net=2), ctx(), ON))
    assert "arb_min_net_spread" in str(gr.check(arb(exit_net=None), ctx(), ON))  # no exit: fail closed


def test_arbitrage_inventory_cap():
    assert gr.check(arb(price=10), ctx(arb_inventory=50), ON).allowed
    assert "arb_max_inventory_p" in str(gr.check(arb(price=11), ctx(arb_inventory=50), ON))


def test_arbitrage_is_an_accept_never_a_bid():
    assert "never a posted bid" in str(gr.check(arb(kind="bid"), ctx(), ON))


def test_arbitrage_still_meets_every_other_rule():
    assert "cash_floor" in str(gr.check(arb(price=12), ctx(cash=280), ON))
    assert "max_spend" in str(gr.check(arb(price=10), ctx(spent_last_hour=145), ON))
    assert "max_price_common" in str(gr.check(arb(price=13), ctx(), ON))
    assert "accept(s) already" in str(gr.check(arb(), ctx(accepts_this_tick=1), ON))
    assert gr.check(arb(), ctx(stops=("pause file .local/PAUSE exists",)), ON).halted


def test_duplicate_threshold():
    assert gr.check(dup(price=25, value=28.0), ctx(), ON).allowed  # 28 − 25 = 3 ≥ 3
    assert "dup_min_surplus" in str(gr.check(dup(price=26, value=28.0), ctx(), ON))
    assert "dup_min_surplus" in str(gr.check(dup(value=None), ctx(), ON))  # unknown value: fail closed


def test_duplicate_hourly_cap():
    assert gr.check(dup(price=15), ctx(dup_spent_last_hour=25), ON).allowed  # 25 + 15 = 40
    assert "dup_max_spend_per_hour" in str(gr.check(dup(price=15), ctx(dup_spent_last_hour=26), ON))


def test_duplicate_still_meets_every_other_rule():
    assert "max_price_rare" in str(gr.check(dup(price=81, value=200.0), ctx(), ON))
    assert "cash_floor" in str(gr.check(dup(price=15), ctx(cash=280), ON))


def row(ref="LAV-01", asset=900, bid=2):
    return gr.ArbRow(ref, asset, bid, "v02", 16, "t06", "t17").item()


def test_an_arb_row_round_trips_through_the_ledger_item():
    item = row()
    assert item == "arb:LAV-01:900:2:v02:16:t06:t17"
    assert gr.ArbRow.parse(item) == gr.ArbRow("LAV-01", 900, 2, "v02", 16, "t06", "t17")
    assert gr.ArbRow.parse("arb:LAV-01:1") is None and gr.ArbRow.parse("arb:LAV-01:x:2:v02:16:t06:t17") is None


def test_arb_inventory_counts_the_copies_bought_that_we_still_hold():
    rows = [(row(asset=900), 10, 5), (row(asset=901), 12, 6), (row("MAL-04", 902), 7, 7), ("arb:junk", 4, 8)]
    # 900 still ours, 901 sold by its exit, 902 sold later by the maker; a malformed row counts (fail closed)
    assert gr.arb_inventory(rows, {900, 1, 2}) == 10 + 4
    # another LAV-01 copy coming in (a pack, a page buy) never revives a closed row: identity, not counts
    assert gr.arb_inventory(rows, {900, 777}) == 10 + 4
    assert gr.arb_inventory(rows, {900, 901, 902}) == 10 + 12 + 7 + 4


def test_the_ledger_feeds_the_context_only_when_a_switch_is_on(tmp_path: Path):
    ledger = gr.Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 5, 0.5, 10, row(asset=900))
    ledger.record("spend", 6, 0.2, 9, gr.dup_item("LAV-09"))  # more than an hour before t = 1.6
    ledger.record("spend", 60, 1.5, 15, gr.dup_item("LAV-09"))
    ledger.record("spend", 60, 1.5, 7, "LAV-03")
    me = {
        "cash": 384,
        "assets": [{"kind": "card", "ref": "LAV-01", "id": 1}, {"kind": "card", "ref": "LAV-01", "id": 900}],
    }
    on = gr.context_from(me, 70, 1.6, ledger, ON)
    assert (on.arb_inventory, on.dup_spent_last_hour, on.spent_last_hour) == (10, 15, 22)
    off = gr.context_from(me, 70, 1.6, ledger, REAL)
    assert (off.arb_inventory, off.dup_spent_last_hour) == (0, 0)
    assert ledger.spend_rows("dup:", 1.0) == [("dup:LAV-09", 15, 60)]
    assert not ledger.packs_since(0.0)  # tagged items are never mistaken for packs
