import json
from copy import deepcopy

import pytest

from bazaar_agent import intel, render, strategy
from bazaar_agent.guardrails import Context, Guardrails, GuardrailsError
from tests.test_intel import opened, settle
from tests.test_render import text

RARITIES = {
    "common": {"book": 10, "print_run": 300},
    "uncommon": {"book": 25, "print_run": 90},
    "rare": {"book": 70, "print_run": 30},
    "epic": {"book": 180, "print_run": 9},
    "legendary": {"book": 450, "print_run": 3},
}


def card(ref, rarity, minted, page=True):
    r = RARITIES[rarity]
    return {
        "id": ref,
        "name": ref,
        "rarity": rarity,
        "book": r["book"],
        "page": page,
        "minted": minted,
        "print_run": r["print_run"],
    }


CATALOG = {
    "values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1},
    "rarities": RARITIES,
    "packs": [
        {
            "id": "sobre_barrio",
            "expected_book": 33.8,
            "slots": [{"common": 1.0}, {"common": 1.0}, {"common": 0.75, "uncommon": 0.25}],
        },
        {
            "id": "sobre_bienvenida",
            "expected_book": 78.0,
            "slots": [{"common": 1.0}, {"uncommon": 1.0}, {"uncommon": 0.6, "rare": 0.4}],
        },
    ],
    "sets": [
        {
            "id": "LAV",
            "cards": [
                card("LAV-01", "common", 15),
                card("LAV-02", "common", 12),
                card("LAV-06", "uncommon", 7),
                card("LAV-08", "uncommon", 10),
                card("LAV-09", "rare", 1),
                card("LAV-10", "rare", 0),
                card("LAV-11", "epic", 0, page=False),
            ],
        },
        {"id": "LAT", "cards": [card("LAT-03", "common", 11), card("LAT-09", "rare", 3)]},
        {"id": "RET", "cards": [card("RET-01", "common", 0)]},  # not released: not in our album
    ],
}

ME = {
    "id": "t01",
    "cash": 400,
    "tick": 50,
    "unlocked": ["abuela"],
    "affinity": {"LAV": 1.6, "LAT": 0.5, "RET": 0.7},
    "album": {"pages": [{"set": "LAV", "have": 2, "of": 6}, {"set": "LAT", "have": 2, "of": 2}]},
    "assets": [
        {"id": 1, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 16.0},
        {"id": 2, "kind": "card", "ref": "LAV-06", "rarity": "uncommon", "your_value": 40.0},
        {"id": 3, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2},
        {"id": 4, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2},
        {"id": 5, "kind": "card", "ref": "LAT-09", "rarity": "rare", "your_value": 35.0},
        {"id": 6, "kind": "pack", "ref": "sobre_barrio"},
    ],
}

ABUELA = {
    "id": "abuela",
    "status": "active",
    "level": 1,
    "menu": {
        "sells": [
            {"pack": "sobre_barrio", "list_price": 26, "opening_ask": 30, "per_team_per_hour": 3},
            {"rarity": "common", "sets": "released", "list_price": 10},
            {"rarity": "uncommon", "sets": "released", "list_price": 25},
        ]
    },
}
DEALERS = [ABUELA, {"id": "chato", "status": "announced", "level": None}]


def listed(eid, team, ref):
    offer = {"id": eid, "give": {"assets": [{"ref": ref}]}, "want": {"cash": 90}}
    return {"id": eid, "tick": 7, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


EVENTS = [
    opened(1, 10, "t10", {"buy": {"card": "LAV-08"}}, tick=1),
    opened(2, 11, "t03", {"buy": {"card": "LAV-02"}}, tick=1),
    opened(3, 12, "t07", {"buy": {"card": "LAT-03"}}, tick=1),
    settle(4, 1, "abuela", "t05", "sobre_barrio", 17, tick=2),
    settle(5, 2, "abuela", "t05", "sobre_barrio", 21, tick=3),
    settle(6, 3, "abuela", "t10", "LAV-08", 22, tick=3, kind="card"),
    settle(7, 4, "abuela", "t03", "LAV-06", 18, tick=4, kind="card"),
    settle(8, 5, "abuela", "t03", "LAV-02", 8, tick=4, kind="card"),
    settle(9, 6, "abuela", "t07", "LAT-03", 9, tick=5, kind="card"),
    settle(10, 7, "t13", "t14", "LAT-09", 65, tick=6, kind="card", persona=None),
    settle(11, 8, "t09", "abuela", "LAT-03", 5, tick=6, kind="card"),  # the dealer bought: not a team's price
    listed(12, "t09", "LAV-09"),
]

PARAMS = strategy.StrategyParams(
    page_bonus_weight=1.0,
    scarcity_weight=1.0,
    scarce_minted_max=5,
    min_buy_surplus=2,
    sell_need_share=0.6,
    sell_min_surplus=5,
    rare_fallback_price=70,
    pack_price_estimate=17,
    max_moves=12,
)
RULES = Guardrails()


def market(catalog=CATALOG, dealers=DEALERS):
    return strategy.build_market(ME, catalog, EVENTS, dealers)


def playbook(params=PARAMS, rules=RULES, catalog=CATALOG, dealers=DEALERS):
    return strategy.build_playbook(ME, catalog, EVENTS, dealers, params, rules)


def with_minted(ref, minted):
    catalog = deepcopy(CATALOG)
    for s in catalog["sets"]:
        for c in s["cards"]:
            if c["id"] == ref:
                c["minted"] = minted
    return catalog


# ---------------------------------------------------------------- parameters


def params_text(**overrides):
    values = {**PARAMS.model_dump(), **overrides}
    return "\n".join(f"- `{k}` = {v} — why" for k, v in values.items() if v is not None)


def test_strategy_md_loads_every_parameter_with_its_reason():
    loaded = strategy.load_strategy()
    assert loaded.params.max_moves >= 1 and 0 < loaded.params.sell_need_share <= 1
    assert {line.rule_id for line in loaded.lines} == set(strategy.StrategyParams.model_fields)
    assert all(line.why for line in loaded.lines)


@pytest.mark.parametrize(
    ("text_in", "problem"),
    [
        (params_text() + "\n- `bogus_param` = 1 — not a real parameter", "bogus_param"),
        (params_text(sell_need_share=2), "sell_need_share"),
        (params_text(scarce_minted_max="lots"), "scarce_minted_max"),
        (params_text(max_moves=None), "max_moves"),
    ],
)
def test_bad_unknown_or_missing_parameters_fail_fast(text_in, problem):
    with pytest.raises(GuardrailsError, match=problem):
        strategy.parse_strategy(text_in)


def test_a_missing_strategy_file_refuses_to_start(tmp_path):
    with pytest.raises(GuardrailsError, match="missing"):
        strategy.load_strategy(tmp_path / "STRATEGY.md")


# ---------------------------------------------------------------- supply, prices, value


def test_supply_flags_scarcity_and_where_each_card_can_come_from():
    supply = {s.ref: s for s in strategy.supply_view(market(), PARAMS)}
    assert "RET-01" not in supply  # not released
    assert (supply["LAV-09"].scarce, supply["LAV-09"].availability) == (True, "teams")
    assert (supply["LAV-02"].scarce, supply["LAV-02"].availability) == (False, "dealer")
    assert (supply["LAV-10"].minted, supply["LAV-10"].availability) == (0, "none")  # no pack we can buy pulls a rare
    assert (supply["LAT-09"].ours, supply["LAT-09"].availability) == (1, "teams")  # two other copies exist
    only_ours = {s.ref: s for s in strategy.supply_view(market(with_minted("LAT-09", 1)), PARAMS)}
    assert only_ours["LAT-09"].availability == "none"  # we hold every minted copy


def test_a_zero_minted_rare_pullable_from_a_sold_pack_is_packs_and_still_never_a_buy():
    abuela = deepcopy(ABUELA)
    abuela["menu"]["sells"].append({"pack": "sobre_bienvenida", "list_price": 60})
    book = playbook(dealers=[abuela])
    assert {s.ref: s.availability for s in book.supply}["LAV-10"] == "packs"
    assert "LAV-10" not in [m.ref for m in book.buys]
    assert any(line.startswith("LAV-10: 0 minted") for line in book.skipped)


def test_price_estimate_falls_back_from_ref_to_rarity_to_list_to_fallback():
    m = market()
    rarity_of = {ref: c.rarity for ref, c in m.cards.items()}
    prints = m.prints
    assert strategy.estimate_price("LAV-02", "common", prints, rarity_of, 10, 10) == strategy.Estimate(
        8, "tape LAV-02 ×1"
    )
    assert strategy.estimate_price("LAV-01", "common", prints, rarity_of, 10, 10) == strategy.Estimate(
        8.5, "tape common ×2"
    )
    assert strategy.estimate_price("LAV-08", "uncommon", [], rarity_of, 25, 25).basis == "dealer list"
    assert strategy.estimate_price("LAV-09", "rare", [], rarity_of, None, 70) == strategy.Estimate(70, "fallback")


def test_only_one_item_prints_a_team_paid_count_toward_prices():
    m = market()
    assert all(strategy.is_team(p.buyer) and p.items == 1 for p in m.prints)
    assert 5 not in [p.price for p in m.prints]  # t09 sold LAT-03 to the dealer for 5


def test_each_missing_page_card_carries_its_book_share_of_the_page_bonus():
    shares = strategy.bonus_shares(market(), "LAV")
    page_value = (10 + 10 + 25 + 25 + 70 + 70) * 1.6  # the six LAV page cards, not the epic
    assert sum(shares.values()) == pytest.approx(0.25 * page_value)
    assert shares == pytest.approx({"LAV-02": 4.8, "LAV-08": 12.0, "LAV-09": 33.6, "LAV-10": 33.6})
    assert strategy.bonus_shares(market(), "LAT") == {}  # complete page: nothing left to unlock


def test_holders_come_from_settlements_listings_and_gifts():
    gift = {"id": 99, "type": "gift.given", "payload": {"team": "t11", "cards": ["LAV-02"]}}
    holders = strategy.likely_holders([*EVENTS, gift], us="t01")
    assert holders["LAV-09"] == ("t09",)  # listed it
    assert holders["LAT-09"] == ("t14",)  # t13 sold it to t14
    assert holders["LAV-02"] == ("t03", "t11")


def test_dealer_quotes_keep_only_active_dealers_we_unlocked():
    quotes, newest = strategy.dealer_quotes(DEALERS, ["abuela"])
    assert {q.item for q in quotes} == {"sobre_barrio", "common", "uncommon"} and newest == "abuela"
    assert next(q for q in quotes if q.item == "sobre_barrio").per_team_per_hour == 3
    assert strategy.dealer_quotes(DEALERS, []) == ((), None)
    assert playbook().pack_quotas == {"sobre_barrio": 3}


def test_a_dealer_line_for_one_set_covers_only_that_set():
    lav_only = deepcopy(ABUELA)
    lav_only["menu"]["sells"] = [{"rarity": "common", "sets": "LAV", "list_price": 10}]
    m = market(dealers=[lav_only])
    assert strategy.quote_for(m, m.cards["LAV-02"]) is not None
    assert strategy.quote_for(m, m.cards["LAT-03"]) is None
    assert strategy._set_scope(["LAV", "LAT"]) == ("LAV", "LAT") and strategy._set_scope("released") is None


def test_urgency_mixes_scarcity_and_demand():
    m = market()
    assert strategy.urgency_of(m.cards["LAV-09"], 2, PARAMS) == pytest.approx((1.0 + 2 / 3) / 2, abs=1e-3)
    assert strategy.urgency_of(m.cards["LAV-10"], 0, PARAMS) == 0.5  # nothing minted: maximally scarce
    no_supply = PARAMS.model_copy(update={"scarce_minted_max": 0})
    assert strategy.urgency_of(m.cards["LAV-02"], 0, no_supply) == 0.0


def test_bid_range_never_exceeds_cap_value_or_what_anyone_paid():
    assert strategy.bid_range([8, 9], 8, 20.8, 12, 2) == (8, 9)
    assert strategy.bid_range([17, 21, 30], 17, 25.0, 20, 2) == (17, 20)
    assert strategy.bid_range([], 23, 60, 26, 2) == (23, 23)
    assert strategy.bid_range([30], 30, 2.5, 26, 2) is None


# ---------------------------------------------------------------- moves


def test_buys_rank_rares_first_with_exact_commands():
    book = playbook()
    assert [m.ref for m in book.buys] == ["LAV-09", "LAV-08", "LAV-02"]
    lav09, lav08, lav02 = book.buys
    assert (lav09.value, lav09.price, lav09.surplus, lav09.score) == (145.6, 65.0, 80.6, 147.74)
    assert lav09.command == "uv run bazaar sell bid LAV-09 --price 65"
    assert lav09.counterparties == ("t09",) and lav09.strategy == "complete_pages+scarcity_first"
    assert lav08.command == "uv run bazaar dealer buy LAV-08 --start 18 --max 22 --dealer abuela"
    assert lav08.strategy == "complete_pages+dealer_floor+level_unlock"
    assert (lav02.value, lav02.price, lav02.score) == (20.8, 8.0, 19.74)
    assert lav02.command == "uv run bazaar dealer buy LAV-02 --start 8 --max 9 --dealer abuela"


def test_a_rare_with_zero_minted_copies_is_never_a_buy():
    book = playbook()
    assert "LAV-10" not in [m.ref for m in book.buys]
    assert "LAV-10: 0 minted, not buyable (none) — pull or wait" in book.skipped
    assert all(s.minted > 0 for s in book.supply if s.ref in {m.ref for m in book.buys})


def test_held_cards_are_never_buys_and_page_bonus_weight_zero_drops_the_share():
    assert {m.ref for m in playbook().buys}.isdisjoint({"LAV-01", "LAV-06", "LAT-03", "LAT-09"})
    no_bonus = playbook(params=PARAMS.model_copy(update={"page_bonus_weight": 0.0}))
    assert next(m for m in no_bonus.buys if m.ref == "LAV-09").value == 112.0


def test_a_rare_bid_names_holders_that_chase_the_set_themselves():
    events = [*EVENTS, settle(20, 9, "t09", "t10", "LAV-09", 72, tick=8, kind="card", persona=None)]
    book = strategy.build_playbook(ME, CATALOG, events, DEALERS, PARAMS, RULES)
    lav09 = next(m for m in book.buys if m.ref == "LAV-09")
    assert lav09.counterparties == ("t10",) and "t10 chase LAV themselves" in lav09.reason
    assert lav09.command == "uv run bazaar sell bid LAV-09 --price 72"  # its own tape print now


def test_buys_below_the_minimum_surplus_are_explained_not_proposed():
    book = playbook(params=PARAMS.model_copy(update={"min_buy_surplus": 100}))
    assert book.buys == ()
    assert any("surplus too small" in line for line in book.skipped)


def test_sells_go_to_chasers_at_their_need_and_never_below_what_we_lose():
    book = playbook()
    assert [m.ref for m in book.sells] == ["LAT-09", "LAT-03"]
    lat09, lat03 = book.sells
    # LAT's page is complete in the fixture: selling our only LAT-09 also gives up the whole page bonus.
    assert (lat09.value, lat09.price, lat09.surplus, lat09.score) == (45.0, 68.0, 23.0, 42.16)
    assert "ours 35 + page bonus 10.0" in lat09.reason
    assert lat09.command == "uv run bazaar sell list 5 --price 68" and lat09.asset_id == 5
    assert lat09.counterparties == ("t07", "t14")
    assert lat03.command == "uv run bazaar sell list 4 --price 10" and "duplicate" in lat03.reason
    assert lat03.value == 1.2  # a duplicate gives up no page bonus
    assert all(m.price >= m.value + PARAMS.sell_min_surplus for m in book.sells)
    strict = playbook(rules=Guardrails(sell_min_value_ratio=3.0))
    assert all(m.price >= 3.0 * m.value for m in strict.sells)
    assert next(m for m in strict.sells if m.ref == "LAT-09").price == 135


def test_the_page_bonus_at_stake_is_all_of_it_on_a_complete_page_and_a_share_otherwise():
    m = market()
    assert strategy.bonus_at_stake(m, m.cards["LAT-09"], PARAMS) == pytest.approx(0.25 * 80 * 0.5)
    assert strategy.bonus_at_stake(m, m.cards["LAT-03"], PARAMS) == 0.0  # we keep a copy
    page_bonus = 0.25 * 210 * 1.6  # LAV: missing LAV-02, LAV-08, LAV-09, LAV-10 (book 175)
    assert strategy.bonus_at_stake(m, m.cards["LAV-01"], PARAMS) == pytest.approx(page_bonus * 10 / 185)
    unweighted = PARAMS.model_copy(update={"page_bonus_weight": 0.0})
    assert strategy.bonus_at_stake(m, m.cards["LAV-01"], unweighted) == 0.0
    assert strategy.bonus_at_stake(m, m.cards["LAV-11"], PARAMS) == 0.0  # not a page card


def test_a_copy_without_your_value_is_never_offered():
    me = deepcopy(ME)
    me["assets"][4]["your_value"] = None  # LAT-09
    book = strategy.build_playbook(me, CATALOG, EVENTS, DEALERS, PARAMS, RULES)
    assert "LAT-09" not in [m.ref for m in book.sells]


def test_ranking_caps_each_side_and_breaks_ties_toward_duplicates_then_low_affinity():
    book = playbook(params=PARAMS.model_copy(update={"max_moves": 1}))
    assert [m.ref for m in book.buys] == ["LAV-09"] and [m.ref for m in book.sells] == ["LAT-09"]
    m = market()
    base = playbook().sells[0]
    tied = [
        strategy.Move(**{**base.__dict__, "ref": "LAV-01", "score": 10.0}),
        strategy.Move(**{**base.__dict__, "ref": "LAT-09", "score": 10.0}),
        strategy.Move(**{**base.__dict__, "ref": "LAT-03", "score": 10.0}),
        strategy.Move(**{**base.__dict__, "ref": "LAV-06", "score": 11.0}),
    ]
    assert [mv.ref for mv in strategy.rank(tied, m, PARAMS)] == ["LAV-06", "LAT-03", "LAT-09", "LAV-01"]


# ---------------------------------------------------------------- packs


def test_pack_expected_value_uses_what_we_hold_and_the_copy_marginals():
    m = market()
    common = (10 * 1.6 * 0.25 + 10 * 1.6 + 10 * 0.5 * 0.1) / 3  # LAV-01 2nd copy, LAV-02 1st, LAT-03 3rd
    uncommon = (25 * 1.6 * 0.25 + 25 * 1.6) / 2
    assert strategy.rarity_value(m, "common") == pytest.approx(common)
    assert strategy.rarity_value(m, "uncommon") == pytest.approx(uncommon)
    barrio, bienvenida = playbook().packs
    assert barrio.ref == "sobre_barrio" and barrio.value == round(2.75 * common + 0.25 * uncommon, 1)
    assert (barrio.price, barrio.surplus) == (17.0, 8.0)
    assert barrio.command == "uv run bazaar dealer buy sobre_barrio --start 17 --max 20 --dealer abuela"
    assert bienvenida.command == "" and "no dealer we can reach sells it" in bienvenida.reason


def test_a_printed_out_rarity_falls_back_to_the_next_one_down():
    catalog = deepcopy(CATALOG)
    for s in catalog["sets"]:
        for c in s["cards"]:
            c["minted"] = c["print_run"] if c["rarity"] in ("rare", "common") else c["minted"]
    m = strategy.build_market(ME, catalog, EVENTS, DEALERS)
    assert strategy.rarity_value(m, "rare") == pytest.approx(strategy.rarity_value(m, "uncommon"))
    assert strategy.rarity_value(m, "common") == 0.0  # nothing below common


def test_a_ladder_capped_below_the_market_price_is_not_proposed():
    tight = Guardrails(max_price_uncommon=20, max_price_rare=60, max_price_pack=15)
    book = playbook(rules=tight)
    refs = [m.ref for m in book.buys]
    assert "LAV-08" not in refs and "LAV-09" not in refs  # abuela fills LAV-08 at 22; teams pay rares 65
    assert any(line.startswith("LAV-08:") and "cap below market" in line for line in book.skipped)
    assert any(line.startswith("LAV-09:") and "cap below market" in line for line in book.skipped)
    barrio = next(m for m in book.packs if m.ref == "sobre_barrio")
    assert barrio.command == "" and "max_price_pack caps us at 15" in barrio.reason


def test_packs_are_capped_at_max_moves_too():
    assert len(playbook(params=PARAMS.model_copy(update={"max_moves": 1})).packs) == 1


def test_a_pack_not_worth_its_price_gets_no_command():
    expensive = PARAMS.model_copy(update={"pack_price_estimate": 40})
    barrio = next(m for m in playbook(params=expensive).packs if m.ref == "sobre_barrio")
    assert barrio.surplus < 0 and barrio.command == ""


# ---------------------------------------------------------------- guardrails, output


def test_guarded_shows_what_guardrails_would_say_for_each_move():
    ctx = Context(cash=300, held={"LAV-01": 1, "LAT-03": 2, "LAT-09": 1}, tick=50, t_hours=1.0)
    book = strategy.guarded(playbook(), ctx, RULES)
    verdicts = {m.ref: m.guardrail for m in book.buys}
    assert "cash_floor" in verdicts["LAV-09"]  # 300 - 65 < 270
    assert verdicts["LAV-02"] == "allowed"
    assert all(m.guardrail == "allowed" for m in book.sells)
    assert next(m for m in book.packs if not m.command).guardrail == "-"


def test_an_asset_already_in_our_open_offers_is_not_listed_again():
    ctx = Context(cash=400, held={"LAT-03": 2, "LAT-09": 1}, tick=50, t_hours=1.0)
    book = strategy.guarded(playbook(), ctx, RULES, listed=frozenset({5}))
    lat09 = next(m for m in book.sells if m.ref == "LAT-09")
    assert lat09.guardrail == "denied: asset 5 is already in one of our open offers"


def test_playbook_serialises_to_json_with_its_parameters():
    data = strategy.playbook_dict(playbook(), strategy.load_strategy())
    again = json.loads(json.dumps(data))
    assert again["params"]["max_moves"] >= 1 and again["source"] == "STRATEGY.md"
    assert again["buys"][0]["command"].startswith("uv run bazaar")


def test_strategy_tables_render_every_move_and_parameter():
    book = playbook()
    assert "LAV-09" in text(render.scarce_supply_table(list(book.supply)))
    assert "147.7" in text(render.moves_table("Buys", list(book.buys)))
    assert render.move_commands(list(book.buys))[0] == "  #1 uv run bazaar sell bid LAV-09 --price 65"
    info = render.move_commands([m for m in book.packs if not m.command])[0]
    assert info.startswith("  #1 - (no command: ") and "no dealer we can reach sells it" in info
    assert "max_moves" in text(render.params_table(list(strategy.load_strategy().lines)))


def test_real_feed_slice_runs_end_to_end():
    real = [
        settle(1940, 82, "t08", "t10", "LAV-10", 70, tick=49, kind="card", persona=None),
        settle(123, 1, "abuela", "t07", "sobre_barrio", 17, tick=2),
    ]
    book = strategy.build_playbook(ME, with_minted("LAV-10", 2), real, DEALERS, PARAMS, RULES)
    lav10 = next(m for m in book.buys if m.ref == "LAV-10")
    assert lav10.counterparties == ("t10",) and lav10.price == 70.0
    assert intel.tape(real)[0].ref == "LAV-10"


def test_a_new_dealer_without_fills_is_laddered_from_the_deepest_discount_seen():
    chato = {
        "id": "chato",
        "status": "active",
        "level": 2,
        "menu": {"sells": [{"rarity": "rare", "sets": "released", "list_price": 77}]},
    }
    me = {**ME, "unlocked": ["abuela", "chato"]}
    book = strategy.build_playbook(me, CATALOG, EVENTS, [ABUELA, chato], PARAMS, RULES)
    lav09 = next(m for m in book.buys if m.ref == "LAV-09")
    # abuela's deepest discount: a 26 P pack filled at 17 (0.654) → open a 77 P rare at 50, reach 77 in 14 ticks
    assert lav09.command == "uv run bazaar dealer buy LAV-09 --start 50 --max 77 --step 3 --dealer chato"
    assert "level_unlock" in lav09.strategy and (lav09.price, lav09.surplus) == (77.0, 68.6)
    assert strategy.opening_ratio(strategy.build_market(me, CATALOG, [], [ABUELA, chato])) is None


def test_ladder_steps_fit_the_thread_and_openings_follow_the_ratio():
    assert strategy.bid_range([], 77, 145.6, 80, 2, 17 / 26) == (50, 77)
    assert strategy.bid_range([], 77, 145.6, 80, 2) == (77, 77)  # nothing learned anywhere: list price
    steps = (strategy.ladder_step(50, 77, 14), strategy.ladder_step(8, 9, 14), strategy.ladder_step(5, 5, 1))
    assert steps == (3, 1, 1)


def test_ladder_floor_quantile_opens_dealer_card_buys_from_the_floor_table():
    from dataclasses import replace as dc_replace
    from pathlib import Path

    from bazaar_agent.ladder import floor_table, from_rows, main_rows

    rows = json.loads((Path(__file__).parent / "fixtures" / "evals" / "dealer_threads.json").read_text())["rows"]
    m = dc_replace(market(), floors=main_rows(floor_table(from_rows(rows))))

    def lav08(params):
        moves, _ = strategy.buy_moves(m, params, RULES)
        return next(mv for mv in moves if mv.ref == "LAV-08").ladder

    assert lav08(PARAMS) == (18, 22, 1)  # today: the lowest fill seen up to the highest anyone paid
    assert lav08(PARAMS.model_copy(update={"ladder_floor_quantile": 0.5})) == (21, 25, 1)  # floor 23 ± 2
    assert strategy.floor_range(m.floors[("abuela", "card:uncommon")], 24.0, 26, 2, 0.5) == (21, 22)  # our value
    assert strategy.floor_range(m.floors[("chato", "card:rare")], 200.0, 80, 2, 0.5) is None  # cap below market


def test_ladder_floor_quantile_falls_back_to_todays_ladder_without_a_floor():
    """A thin feed (no floors, or too few closed threads) keeps the lowest-fill ladder."""
    on = PARAMS.model_copy(update={"ladder_floor_quantile": 0.5})
    moves, _ = strategy.buy_moves(market(), on, RULES)  # EVENTS have no thread messages: floors == {}
    assert market().floors == {} and next(mv for mv in moves if mv.ref == "LAV-08").ladder == (18, 22, 1)


def test_a_floor_plan_below_the_market_never_drops_todays_buy():
    from dataclasses import replace as dc_replace

    from bazaar_agent.ladder import FloorRow

    low = FloorRow("abuela", "card:uncommon", 29, 9, 9, (14, 14, 15, 15, 15, 15, 16, 16, 16), 9, 5.0, 3.0)
    m = dc_replace(market(), floors={("abuela", "card:uncommon"): low})  # floor 15 → 13..17, LAV-08 fills ~22
    moves, _ = strategy.buy_moves(m, PARAMS.model_copy(update={"ladder_floor_quantile": 0.5}), RULES)
    assert next(mv for mv in moves if mv.ref == "LAV-08").ladder == (18, 22, 1)  # today's ladder, not dropped


def test_ladder_level_deals_routes_card_buys_to_the_newest_dealer_until_it_has_enough():
    chato = {
        "id": "chato",
        "status": "active",
        "level": 2,
        "menu": {"sells": [{"rarity": "uncommon", "sets": "released", "list_price": 30}]},
    }
    me = {**ME, "unlocked": ["abuela", "chato"]}
    chato_fill = settle(20, 9, "chato", "t07", "LAT-06", 28, tick=6, kind="card", persona="chato")
    m = strategy.build_market(me, CATALOG, [*EVENTS, chato_fill], [ABUELA, chato])
    on = PARAMS.model_copy(update={"ladder_level_deals": 3})
    capped = Guardrails(dealer_price_caps="chato:uncommon=31")

    def lav08(market_, params, rules):
        moves, _ = strategy.buy_moves(market_, params, rules)
        return next(mv for mv in moves if mv.ref == "LAV-08")

    assert lav08(m, PARAMS, capped).source == "abuela"  # off (0): the cheapest dealer
    assert lav08(m, on, capped).source == "chato" and "level_unlock" in lav08(m, on, capped).strategy
    assert lav08(m, on, RULES).source == "abuela"  # Chato's plan is above max_price_uncommon 26: no route
    done = [
        settle(30 + i, 20 + i, "chato", "t01", "LAT-06", 28, tick=7, kind="card", persona="chato") for i in range(3)
    ]
    m_done = strategy.build_market(me, CATALOG, [*EVENTS, chato_fill, *done], [ABUELA, chato])
    assert lav08(m_done, on, capped).source == "abuela"  # three deals with Chato: back to the cheapest
