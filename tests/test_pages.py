import json

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli, pages
from bazaar_agent.guardrails import Guardrails
from tests.test_intel import opened, settle
from tests.test_sim_cli import run, session  # noqa: F401  (the in-process simulator)
from tests.test_strategy import PARAMS, card

RULES = Guardrails()  # cash_floor 270, max_spend_per_game_hour 150, max_price_rare 80, max_price_uncommon 26

CATALOG = {
    "values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1},
    "rarities": {"common": {"book": 10}, "uncommon": {"book": 25}, "rare": {"book": 70}, "epic": {"book": 180}},
    "packs": [],
    "sets": [
        {
            "id": "LAV",
            "cards": [
                card("LAV-01", "common", 20),
                card("LAV-06", "uncommon", 10),
                card("LAV-09", "rare", 3),
                card("LAV-10", "rare", 3),
                card("LAV-11", "epic", 0, page=False),
            ],
        },
        {"id": "LAT", "cards": [card("LAT-01", "common", 20), card("LAT-09", "rare", 2)]},
    ],
}

ME = {
    "id": "t01",
    "cash": 503,
    "tick": 159,
    "unlocked": ["abuela", "chato"],
    "affinity": {"LAV": 1.6, "LAT": 0.5},
    "album": {"pages": [{"set": "LAV", "have": 1, "of": 4}, {"set": "LAT", "have": 1, "of": 2}]},
    "assets": [
        {"id": 1, "kind": "card", "ref": "LAV-01", "serial": 25, "your_value": 16.0},
        {"id": 2, "kind": "card", "ref": "LAT-01", "serial": 3, "your_value": 5.0},
        {"id": 3, "kind": "pack", "ref": "sobre_bienvenida"},
    ],
}

DEALERS = [
    {
        "id": "abuela",
        "status": "active",
        "level": 1,
        "menu": {"sells": [{"rarity": "common", "list_price": 10}, {"rarity": "uncommon", "list_price": 25}]},
    },
    {"id": "chato", "status": "active", "level": 2, "menu": {"sells": [{"rarity": "rare", "list_price": 77}]}},
]


def listed(eid, offer_id, team, ref, price, asset_id, expires=200, tick=150):
    assets = [{"id": asset_id, "ref": ref, "serial": 7}]
    offer = {"id": offer_id, "to": None, "give": {"assets": assets}, "want": {"cash": price}, "expires_tick": expires}
    return {"id": eid, "tick": tick, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


EVENTS = [
    # Chato's rares: fills at 85 and 90, both above max_price_rare 80
    opened(1, 10, "t10", {"buy": {"card": "LAV-10"}}, tick=1, dealer="chato"),
    settle(2, 1, "chato", "t10", "LAV-10", 90, tick=3, kind="card", persona="chato", asset_id=301),
    opened(3, 11, "t05", {"buy": {"card": "LAV-09"}}, tick=4, dealer="chato"),
    settle(4, 2, "chato", "t05", "LAV-09", 85, tick=6, kind="card", persona="chato", asset_id=302),
    # Abuela's uncommons: 22 and 24
    opened(5, 12, "t03", {"buy": {"card": "LAV-06"}}, tick=5),
    settle(6, 3, "abuela", "t03", "LAV-06", 22, tick=7, kind="card", asset_id=303),
    opened(7, 13, "t08", {"buy": {"card": "LAV-06"}}, tick=6),
    settle(8, 4, "abuela", "t08", "LAV-06", 24, tick=8, kind="card", asset_id=304),
    # a team sold LAV-09 to t07 at 70 (t07 does not chase LAV); t10 and t05 chase LAV (their dealer topics)
    settle(9, 5, "t02", "t07", "LAV-09", 70, tick=9, kind="card", persona=None, asset_id=305),
    opened(10, 14, "t10", {"buy": {"card": "LAV-06"}}, tick=10),
    opened(17, 15, "t07", {"buy": {"card": "LAT-01"}}, tick=11),  # t07's top set is LAT
    opened(18, 16, "t07", {"buy": {"card": "LAT-09"}}, tick=12),
    # one LAT-09 ask still open, one cancelled, one expired, one whose copy settled since
    listed(11, 500, "t15", "LAT-09", 60, 401),
    listed(12, 501, "t16", "LAT-09", 40, 402),
    {"id": 13, "tick": 151, "type": "offer.cancelled", "actor": "", "payload": {"offer": 501}},
    listed(14, 502, "t17", "LAT-09", 30, 403, expires=100),
    listed(15, 503, "t18", "LAT-09", 35, 404),
    settle(16, 6, "t18", "t04", "LAT-09", 35, tick=152, kind="card", persona=None, asset_id=404),
]

SCHEDULE = {
    "now_hours": 0.5,
    "upcoming": [
        {"action": "bench", "at_hours": 3.0, "params": {"ticks": 16}},
        {"action": "grant_all", "at_hours": 4.05, "note": "Saturday", "params": {"cash": 150, "packs": ["x"]}},
        {"action": "grant_all", "at_hours": 18.05, "note": "Sunday", "params": {"cash": 150}},
        {"action": "grant_all", "at_hours": 0.2, "note": "past", "params": {"cash": 999}},
        {"action": "round", "at_hours": 4.0, "params": {"name": "Saturday"}},
        {"action": "round", "at_hours": 18.0, "params": {"name": "Sunday"}},
        {"action": "end_round", "at_hours": 24.0, "params": {}},
    ],
}


def lav():
    return next(p for p in pages.page_economics(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES) if p.set_code == "LAV")


def missing(page, ref):
    return next(c for c in page.missing if c.ref == ref)


def by_source(c, source):
    return next(s for s in c.sources if s.source == source)


def test_minted_is_raised_to_the_highest_serial_seen_in_the_feed_or_our_hand():
    seen = pages.serials_seen([listed(1, 1, "t02", "LAT-09", 50, 9)])
    assert seen == {"LAT-09": 7}
    m = pages.refresh_minted(pages.strategy.build_market(ME, CATALOG, [], DEALERS), [], ME["assets"])
    assert m.cards["LAV-01"].minted == 25  # our own copy is serial 25: the catalog's 20 is stale
    assert m.cards["LAT-01"].minted == 20  # a lower serial never lowers it


def test_open_asks_drop_cancelled_expired_and_settled_offers():
    asks = pages.open_asks(EVENTS, tick=159)
    assert [(a.offer, a.maker, a.price) for a in asks] == [(500, "t15", 60)]
    bought_then_listed = [
        settle(1, 1, "t02", "t09", "LAT-09", 50, tick=100, kind="card", persona=None, asset_id=777),
        listed(2, 600, "t09", "LAT-09", 70, 777, tick=120),
    ]
    assert [a.offer for a in pages.open_asks(bought_then_listed, tick=159)] == [600]


def test_dealer_fills_are_grouped_by_dealer_and_rarity():
    table = pages.dealer_fill_table(EVENTS)
    assert table[("chato", "card:rare")].fills == (85, 90)
    assert table[("abuela", "card:uncommon")].low == 22
    assert table[("abuela", "card:uncommon")].mid == 23.0


def test_a_dealer_whose_lowest_fill_beats_the_cap_is_blocked_and_scores_on_the_ladder():
    chato = by_source(missing(lav(), "LAV-10"), "chato")
    assert chato.channel == "ladder"
    assert chato.price == 87.5
    assert chato.blocked == "max_price_rare 80 < lowest chato fill 85"


def test_team_price_is_the_cheapest_holders_reservation_and_a_chaser_wants_the_top_multiplier():
    page = lav()
    lav09 = by_source(missing(page, "LAV-09"), "teams")
    # t05 chases LAV (book 70 × 1.6 = 112) and paid 85; t07 does not: max(tape 70, paid 70, 70 × mean 1.05)
    assert lav09.sellers == ("t07",)
    assert lav09.channel == "trade"
    assert lav09.price == 74 + pages.rastro_fee(74)
    assert lav09.blocked is None
    lav10 = by_source(missing(page, "LAV-10"), "teams")
    assert lav10.sellers == ("t10",)  # its only holder chases LAV: 112 > max_price_rare 80
    assert lav10.blocked == "max_price_rare 80 < team price 112"


def test_an_open_ask_is_a_willing_seller_at_its_price():
    lat = next(p for p in pages.page_economics(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES) if p.set_code == "LAT")
    lat09 = by_source(missing(lat, "LAT-09"), "teams")
    assert (lat09.sellers, lat09.price) == (("t15",), 60 + pages.rastro_fee(60))
    assert lat09.blocked and "surplus below min_buy_surplus" in lat09.blocked  # 70 × 0.5 + bonus < 64


def test_each_missing_card_carries_its_value_and_book_share_of_the_page_bonus():
    page = lav()
    assert page.bonus == pytest.approx(0.25 * (10 + 25 + 70 + 70) * 1.6)  # 70
    lav09 = missing(page, "LAV-09")
    assert lav09.value == 112.0
    assert lav09.bonus_share == pytest.approx(70 * 70 / 165, abs=0.01)  # by book among the missing 25 + 70 + 70
    assert lav09.value_with_bonus == pytest.approx(112 + 70 * 70 / 165, abs=0.01)


def test_a_page_with_a_card_no_source_fills_is_blocked_and_says_which_and_why():
    page = lav()
    assert page.verdict == "blocked"
    assert "LAV-10" in page.why and "max_price_rare 80" in page.why
    assert page.cost is None
    assert page.cost_if_unblocked == pytest.approx(23 + 74 + pages.rastro_fee(74) + 87.5)


def test_verdicts_finish_skip_and_complete():
    lav09 = missing(lav(), "LAV-09")
    assert pages.page_verdict([lav09], bonus=70)[0] == "finish"
    assert pages.page_verdict([lav09], bonus=-200)[0] == "skip"
    assert pages.page_verdict([], bonus=70)[0] == "complete"


def test_buy_order_puts_dealer_legs_first_and_the_completing_team_leg_last_with_the_whole_bonus():
    page = lav()
    cards = [missing(page, "LAV-06"), missing(page, "LAV-09")]
    fin = pages.PageEconomics("LAV", 1.6, 2, 4, 70.0, tuple(cards), "finish", "")
    wants = pages.buy_list([fin], PARAMS.min_buy_surplus)
    assert [(w.card.ref, w.source.source, w.completes) for w in wants] == [
        ("LAV-06", "abuela", False),
        ("LAV-09", "teams", True),
    ]
    assert wants[0].trade_surplus is None  # a dealer buy scores as a ladder share
    assert wants[1].trade_surplus == pytest.approx(112 + 70 - wants[1].source.price)


def test_single_cards_need_their_own_surplus_without_any_page_bonus():
    wants = pages.buy_list([lav()], PARAMS.min_buy_surplus)  # blocked page: only singles
    assert [w.card.ref for w in wants] == ["LAV-06", "LAV-09"]  # best surplus per primas first
    assert all(not w.finishing and not w.completes for w in wants)
    assert wants[1].trade_surplus == pytest.approx(112 - wants[1].source.price)


def test_grants_come_from_the_schedule_and_only_those_still_to_come():
    grants = pages.grants_from(SCHEDULE)
    assert [(g.hour, g.cash) for g in grants] == [(4.05, 150), (18.05, 150)]
    assert [g.cash for g in pages.grants_from({"body": SCHEDULE}, after_hours=5)] == [150]


def test_the_ladder_plan_becomes_one_slot_per_scheduled_deal():
    plan = {
        "schedule": [
            {
                "game_hour": 4,
                "dealer": "abuela",
                "price_class": "card:uncommon",
                "plan": {"max": 25},
                "expected": {"price": 22.1},
            },
            {"game_hour": 5, "dealer": "abuela", "price_class": "pack:sobre_barrio", "plan": None},
        ]
    }
    assert pages.ladder_slots_from(plan) == [pages.LadderSlot(4, "abuela", "card:uncommon", 22.1, 25)]


def want(ref, source, price, channel="trade", rarity="rare", page="LAV", **kw):
    c = pages.CardEconomics(ref, page, rarity, 70, 1.6, 112, 0, 3, 30, (), (), ())
    return pages.Want(c, pages.Source(source, channel, price, None, "", 80, ()), page, **kw)


def test_the_venue_is_refused_when_its_bond_and_fee_would_take_cash_below_the_floor():
    grants = [pages.Grant(4.05, 150, "Saturday")]
    s = pages.cash_plan("v", 353, 4, grants, [], RULES, venue_hour=4)
    assert s.venue_opened is None
    assert "503 < 540" in s.steps[-1].note  # 270 bond + fee on top of cash_floor 270
    ok = pages.cash_plan("v", 353, 4, grants, [], RULES, venue_hour=4, floor=0)
    assert ok.venue_opened == 4 and ok.end_cash == 233


def test_cash_for_a_planned_venue_is_kept_until_it_opens():
    wants = [want("LAV-09", "teams", 75)]
    s = pages.cash_plan("v", 560, 4, [], wants, RULES, venue_hour=6)
    kinds = [(x.hour, x.kind, x.item) for x in s.steps]
    assert kinds == [(6, "venue", "venue"), (24, "held", "LAV-09")]  # 560 − 75 < 540 before, 290 − 75 < 270 after
    s = pages.cash_plan("v", 560, 4, [], wants, RULES, venue_hour=None)
    assert s.bought == ("LAV-09",)


def test_a_ladder_slot_buys_the_page_card_it_fits_and_otherwise_spends_as_planned():
    slots = [
        pages.LadderSlot(4, "abuela", "card:uncommon", 22.1, 25),
        pages.LadderSlot(4, "abuela", "card:common", 9, 12),
    ]
    wants = [want("LAV-06", "abuela", 23, channel="ladder", rarity="uncommon")]
    s = pages.cash_plan("l", 400, 4, [], wants, RULES, ladder=slots)
    assert [(x.kind, x.item, x.amount) for x in s.steps] == [("buy", "LAV-06", 23), ("ladder", "card:common", 9)]
    assert s.dealer_deals == 2
    assert "ladder slot card:uncommon" in s.steps[0].note


def test_the_hour_spend_cap_moves_buys_to_the_next_hour():
    wants = [want("LAV-09", "teams", 80), want("LAV-10", "teams", 80)]
    s = pages.cash_plan("h", 1000, 4, [], wants, RULES)
    assert [(x.hour, x.item) for x in s.steps] == [(4, "LAV-09"), (5, "LAV-10")]


def test_a_page_leg_waits_until_the_card_that_completes_the_page_fits_too():
    legs = [
        want("LAV-09", "teams", 75, finishing=True),
        want("LAV-10", "teams", 75, finishing=True, completes=True, page_bonus=70),
    ]
    s = pages.cash_plan("p", 270 + 100, 4, [pages.Grant(18.05, 150, "Sunday")], legs, RULES)
    assert [(x.hour, x.kind, x.item) for x in s.steps] == [
        (18, "grant", "+150"),
        (18, "buy", "LAV-09"),
        (18, "buy", "LAV-10"),
    ]
    assert s.trade_surplus == pytest.approx((112 - 75) + (112 + 70 - 75))


def test_the_plan_runs_every_scenario_and_says_to_open_packs_first():
    plan = pages.build_plan(ME, CATALOG, EVENTS, DEALERS, SCHEDULE, PARAMS, RULES, now_hours=4.0, what_if_floor=0)
    names = [s.name for s in plan.scenarios]
    assert names[:4] == ["venue at open (h4)", "venue at h9", "venue at the last round (h18)", "no venue"]
    assert "venue at open (h4) · what-if cash_floor 0" in names
    assert plan.notes[0].startswith("open first (sobre_bienvenida)")
    data = pages.plan_dict(plan)
    json.dumps(data)  # serialisable
    assert data["pages"][0]["missing"][0]["sources"]


def test_bazaar_plan_pages_runs_offline_from_files(tmp_path, monkeypatch):
    files = {"me": ME, "catalog": {"body": CATALOG}, "dealers": {"personas": DEALERS}, "schedule": SCHEDULE}
    args = ["plan", "pages", "--now-hours", "4", "--json"]
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
        args += [f"--{name}", str(tmp_path / f"{name}.json")]
    feed = tmp_path / "feed.jsonl"
    feed.write_text("\n".join(json.dumps(e) for e in EVENTS) + "\n" + json.dumps({"id": 99, "type": "agent.me"}))
    (tmp_path / "chasers.json").write_text(json.dumps({"LAV": ["t07"]}))
    args += ["--feed", str(feed), "--chasers", str(tmp_path / "chasers.json")]
    monkeypatch.setattr(cli, "public_client", lambda settings: pytest.fail("no network"))
    result = CliRunner().invoke(cli.app, args, env={"COLUMNS": "250", "BAZAAR_SIM": "1"})
    assert result.exit_code == 0, result.output
    data = json.loads(result.output[result.output.index("{") :])
    lav09 = next(c for p in data["pages"] for c in p["missing"] if c["ref"] == "LAV-09")
    assert next(s for s in lav09["sources"] if s["source"] == "teams")["blocked"]  # t07 chases LAV now
    table = CliRunner().invoke(cli.app, [a for a in args if a != "--json"] + ["--steps"], env={"COLUMNS": "250"})
    assert table.exit_code == 0, table.output
    assert "Cash plan" in table.output and "h4" in table.output
    assert "--now-hours <the hour they open at>" not in table.output  # --now-hours was given
    bare = [a for a in args if a not in ("--json", "--now-hours", "4")]
    warned = CliRunner().invoke(cli.app, bare, env={"COLUMNS": "250"})
    assert warned.exit_code == 0 and "--now-hours <the hour they open at>" in warned.output


def test_a_team_source_is_picked_over_a_cheaper_dealer_when_its_trade_scores_on_its_own():
    dealer = pages.Source("abuela", "ladder", 22, 17, "", 26, ("abuela",))
    team = pages.Source("teams", "trade", 27, 24, "", 26, ("t03",))
    c = pages.CardEconomics("LAV-08", "LAV", "uncommon", 25, 1.6, 40, 16, 13, 90, (), (), (dealer, team))
    assert c.best is dealer  # the cheapest
    assert c.pick(min_surplus=2) is team  # 40 − 27 = 13 of trade surplus beats a 4th dealer deal (scores 0)
    assert c.pick(min_surplus=20) is dealer


def test_trades_from_reads_w4_plan_and_its_cards_are_not_bought_twice():
    plan = {
        "listings": [{"counterparty": "t03", "give": {"cash": 22}, "want": {"cards": ["LAV-06"]}, "expected": 30}],
        "threads": [
            {"counterparty": "t08", "give": {"assets": [7], "cash": 14}, "want": {"cards": ["LAV-09"]}, "expected": 9}
        ],
    }
    trades = pages.trades_from(plan)
    assert [(t.counterparty, t.refs_in, t.cash_out, t.expected) for t in trades] == [
        ("t03", ("LAV-06",), 22, 30.0),
        ("t08", ("LAV-09",), 14, 9.0),
    ]
    p = pages.build_plan(ME, CATALOG, EVENTS, DEALERS, SCHEDULE, PARAMS, RULES, now_hours=4.0, trades=trades)
    assert not {w.card.ref for w in p.wants} & {"LAV-06", "LAV-09"}
    assert p.notes[1] == "bought by the trade plan, not again here: LAV-06, LAV-09"
    nv = next(s for s in p.scenarios if s.name == "no venue")
    assert [(x.kind, x.item, x.amount) for x in nv.steps[1:3]] == [("trade", "LAV-06", 22), ("trade", "LAV-09", 14)]
    assert nv.trade_surplus == pytest.approx(39)


def test_only_the_first_three_planned_deals_per_dealer_can_score():
    slots = [pages.LadderSlot(4, d, "card:common", 9, 12) for d in ("abuela",) * 5 + ("chato",) * 2]
    assert [s.dealer for s in pages.best_three(slots)] == ["abuela"] * 3 + ["chato"] * 2


def test_a_dealer_single_waits_for_a_ladder_slot_of_its_class():
    single = want("LAV-06", "abuela", 23, channel="ladder", rarity="uncommon", slot_only=True)
    s = pages.cash_plan("x", 600, 4, [], [single], RULES)
    assert s.steps[-1].kind == "held" and s.steps[-1].note == "no planned ladder deal of its class left"
    slot = pages.LadderSlot(5, "abuela", "card:uncommon", 22, 25)
    assert pages.cash_plan("x", 600, 4, [], [single], RULES, ladder=[slot]).bought == ("LAV-06",)


def test_a_held_page_leg_says_the_other_legs_are_what_does_not_fit():
    legs = [want("LAV-09", "teams", 75, finishing=True), want("LAV-10", "teams", 75, finishing=True, completes=True)]
    s = pages.cash_plan("p", 400, 4, [], legs, RULES)
    assert s.held == ("LAV-09", "LAV-10")
    assert s.steps[0].note == "cash 400 − 75 − the page's other team legs 75 < floor 270"


def test_a_dealer_still_inside_our_best_three_scores_and_each_pick_uses_one_of_its_deals():
    def rare(ref):
        chato = pages.Source("chato", "ladder", 90, 85, "", 93, ("chato",))
        team = pages.Source("teams", "trade", 95, 90, "", 93, ("t07",))
        return pages.CardEconomics(ref, "LAV", "rare", 70, 1.6, 112, 0, 3, 30, (), (), (chato, team))

    page = pages.PageEconomics("LAV", 1.6, 0, 3, 70.0, tuple(rare(r) for r in ("A", "B", "C")), "finish", "")
    wants = pages.buy_list([page], 2, scoring={"chato": 2})
    assert sorted(w.source.source for w in wants) == ["chato", "chato", "teams"]  # the third deal would not score
    assert wants[-1].completes and wants[-1].source.source == "teams"  # the team leg completes the page
    assert pages.scoring_dealers(EVENTS, "t10", DEALERS, since_tick=0) == {"abuela": 3, "chato": 2}  # t10 + chato once


def test_w4_affinity_map_gives_chasers_and_each_holders_expected_multiplier():
    aff = {
        "t07": {"p_top": {"LAV": 0.43, "LAT": 0.33}, "expected": {"LAV": 1.37, "LAT": 1.0}},
        "t05": {"p_top": {"LAV": 0.78}, "expected": {"LAV": 1.53}},
    }
    chasers, expected = pages.from_affinity_map(aff)
    assert chasers == {"LAV": ["t05"]}
    assert expected["t07"]["LAV"] == 1.37
    page = next(
        p
        for p in pages.page_economics(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES, chasers, expected)
        if p.set_code == "LAV"
    )
    lav09 = by_source(missing(page, "LAV-09"), "teams")
    assert lav09.sellers == ("t07",)  # 70 × 1.37 → 96, still below t05's 70 × 1.53 → 108
    assert lav09.blocked == "max_price_rare 80 < team price 96"


def test_a_ladder_slot_naming_a_card_the_trade_plan_buys_is_a_duplicate_and_spends_nothing():
    trade = pages.PlannedTrade("t03", ("LAV-06",), 22, 0, 30.0, "bid")
    slots = [
        pages.LadderSlot(4, "abuela", "card:uncommon", 22.2, 25, "LAV-06"),
        pages.LadderSlot(4, "abuela", "card:rare", 70, 77, "LAV-09"),
    ]
    team_pick = want("LAV-09", "teams", 75)
    s = pages.cash_plan("d", 600, 4, [], [team_pick], RULES, ladder=slots, trades=[trade])
    assert [(x.kind, x.item, x.amount) for x in s.steps] == [
        ("trade", "LAV-06", 22),
        ("held", "ladder LAV-06", 0),
        ("ladder", "LAV-09", 70),  # the slot buys it from the dealer, so the team buy is dropped
    ]
    assert s.steps[1].note == "duplicate: the trade plan already buys LAV-06 from a team"
    assert (s.dealer_deals, s.ladder_held, s.ladder_duplicates) == (1, 0, 1)
    assert (
        pages.ladder_slots_from(
            {
                "schedule": [
                    {
                        "game_hour": 4,
                        "dealer": "abuela",
                        "price_class": "card:rare",
                        "ref": "LAV-09",
                        "plan": {"max": 77},
                        "expected": {},
                    }
                ]
            }
        )[0].ref
        == "LAV-09"
    )


def test_bazaar_plan_pages_reads_every_input_from_the_simulator_over_http(session):  # noqa: F811
    """No file at all: /api/me with the sim key, the public catalog, dealers, schedule and feed (GET only)."""
    _, sim, _ = session

    def ours():
        state = sim.world.state
        offers = sum(o.maker == "t01" for o in state.offers.values())
        threads = sum(t.team == "t01" for t in state.threads.values())
        return offers, threads, state.teams["t01"].cash

    before = ours()
    out = run("plan", "pages", "--json", "--now-hours", "4", "--no-live")
    data = json.loads(out[out.index("{") :])
    assert {p["set"] for p in data["pages"]} >= {"LAV", "MAL", "LAT", "SAL"}
    assert "no venue" in [s["name"] for s in data["scenarios"]]
    assert all(s["floor"] == RULES.cash_floor for s in data["scenarios"])
    assert ours() == before  # read-only: no offer, no thread, no cash moved


def test_a_later_venue_hour_already_past_is_not_planned():
    plan = pages.build_plan(ME, CATALOG, EVENTS, DEALERS, SCHEDULE, PARAMS, RULES, now_hours=12.0, venue_later=9)
    assert [s.name for s in plan.scenarios if s.venue_hour is not None][:2] == [
        "venue at open (h12)",
        "venue at the last round (h18)",
    ]


def test_a_trade_that_brings_cash_in_is_credited_and_a_held_trade_waits_for_a_later_hour():
    sale = pages.PlannedTrade("t09", (), 0, 80, 20.0, "sell a duplicate")
    rare = pages.PlannedTrade("t07", ("LAV-09",), 60, 0, 30.0, "bid")
    s = pages.cash_plan("t", 300, 4, [pages.Grant(6.05, 500, "grant")], [], RULES, trades=[rare, sale])
    assert [(x.hour, x.kind, x.item, x.amount) for x in s.steps] == [
        (4, "trade", "sell", -80),  # trades that bring cash in go first: 380
        (4, "trade", "LAV-09", 60),  # ...so the bid fits the same hour (300 − 60 alone is under the floor)
        (6, "grant", "+500", -500),
    ]
    late = pages.cash_plan("t", 300, 4, [pages.Grant(6.05, 500, "grant")], [], RULES, trades=[rare])
    assert [(x.hour, x.kind) for x in late.steps] == [(6, "grant"), (6, "trade")]
    assert late.trade_surplus == 30


def test_end_cash_without_any_step_is_the_starting_cash():
    assert pages.cash_plan("idle", 1000, 20, [], [], RULES).end_cash == 1000


def test_a_slot_naming_a_card_records_the_slots_dealer_even_when_the_list_picked_another():
    abuela_pick = want("LAV-06", "abuela", 22, channel="ladder", rarity="uncommon")
    slot = pages.LadderSlot(4, "chato", "card:uncommon", 29, 31, "LAV-06")
    s = pages.cash_plan("x", 600, 4, [], [abuela_pick], RULES, ladder=[slot])
    step = s.steps[0]
    assert (step.kind, step.item, step.source, step.amount) == ("ladder", "LAV-06", "chato", 29)
    assert step.note == "W3 slot buys LAV-06 from chato (the buy list picked abuela)"


def test_a_buy_above_the_hour_cap_says_so_rather_than_blaming_the_floor():
    s = pages.cash_plan("cap", 1000, 4, [], [want("LAV-09", "teams", 180)], RULES)
    assert s.steps[-1].note == "price 180 > max_spend_per_game_hour 150"


def test_only_future_ladder_slots_that_will_run_count_against_the_best_three():
    slots = [
        pages.LadderSlot(4, "abuela", "card:common", 9, 12),  # already run: the feed has it
        pages.LadderSlot(8, "abuela", "card:uncommon", 22, 25, "LAV-06"),  # the trade plan buys LAV-06
        pages.LadderSlot(9, "abuela", "card:common", 9, 12),
    ]
    free = pages.scoring_dealers([], "t01", DEALERS, slots, start_hour=8, taken={"LAV-06"})
    assert free == {"abuela": 2, "chato": 3}


def test_plan_files_from_other_tools_are_checked_at_the_cli(tmp_path, monkeypatch):
    bad = tmp_path / "ladder.json"
    bad.write_text(json.dumps({"schedule": [{"game_hour": 4, "dealer": "abuela", "plan": {"start": 8}}]}))
    files = {"me": ME, "catalog": CATALOG, "dealers": DEALERS, "schedule": SCHEDULE}
    args = ["plan", "pages", "--ladder-plan", str(bad)]
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
        args += [f"--{name}", str(tmp_path / f"{name}.json")]
    (tmp_path / "feed.jsonl").write_text(json.dumps(EVENTS[0]) + "\n")
    args += ["--feed", str(tmp_path / "feed.jsonl")]
    result = CliRunner().invoke(cli.app, args, env={"COLUMNS": "250", "BAZAAR_SIM": "1"})
    assert result.exit_code == 1
    assert "is not a W3 ladder plan (schedule rows): KeyError" in result.output
    with pytest.raises((TypeError, ValueError)):
        pages.multipliers_from({"t07": {"LAV": None}})


def test_past_deals_close_no_slot_by_default_and_planned_slots_count_only_until_the_next_round():
    friday = [
        settle(2, 1, "abuela", "t01", "LAV-06", 22, tick=5, kind="card"),
        settle(3, 2, "abuela", "t01", "LAV-01", 9, tick=6, kind="card"),
        settle(4, 3, "abuela", "t01", "LAV-01", 9, tick=7, kind="card"),
    ]
    # a planned deal near the floor replaces a weaker one among our best three: Friday's do not close a slot
    assert pages.scoring_dealers(friday, "t01", DEALERS) == {"abuela": 3, "chato": 3}
    assert pages.scoring_dealers(friday, "t01", DEALERS, since_tick=0) == {"chato": 3}
    slots = [pages.LadderSlot(h, "abuela", "card:common", 9, 12) for h in (4, 5, 18, 19)]
    assert pages.scoring_dealers([], "t01", DEALERS, slots, start_hour=4, until_hour=18.0) == {"abuela": 1, "chato": 3}


def test_w3_slots_move_with_the_real_opening_hour():
    row = {"game_hour": 4, "dealer": "abuela", "price_class": "card:common", "plan": {"max": 12}, "expected": {}}
    plan = {"window": {"t_start": 4.0}, "schedule": [row]}
    assert pages.ladder_slots_from(plan)[0].hour == 4
    assert pages.ladder_slots_from(plan, open_hour=2)[0].hour == 2  # the clock resumed at h2.65


def test_without_71s_rule_the_venue_opens_and_the_floor_then_blocks_the_buys():
    grants = [pages.Grant(4.05, 150, "Saturday")]
    s = pages.cash_plan("v", 353, 4, grants, [want("LAV-09", "teams", 75)], RULES, venue_hour=4, venue_floor_rule=False)
    assert s.venue_opened == 4 and s.held == ("LAV-09",)
    assert s.steps[-1].note == "cash 233 − 75 < floor 270"


def test_a_what_if_or_w3s_dealer_cap_unblocks_only_that_dealer_and_says_which_rule():
    assert pages.parse_caps("chato:rare=93, chato:uncommon=31") == {("chato", "rare"): 93, ("chato", "uncommon"): 31}
    for bad in ("chato=93", "chato:rares=93", "Chato:rare=93", "chato: =93", "chato:rare=0"):
        with pytest.raises(ValueError, match="dealer:rarity=price"):
            pages.parse_caps(bad)
    caps = pages.Caps.of(RULES, {("chato", "rare"): 93})
    assert caps.cap("rare", "chato") == (93, "what-if chato:rare")
    assert caps.cap("rare", "abuela") == (80, "max_price_rare")
    assert caps.unloaded() == {("chato", "rare"): 93}

    class W3Rules(Guardrails):  # #81's guardrails expose `dealer_caps`
        @property
        def dealer_caps(self):
            return {("chato", "rare"): 93}

    w3 = pages.Caps.of(W3Rules(), {("chato", "rare"): 93})
    assert w3.cap("rare", "chato") == (93, "what-if chato:rare") and w3.unloaded() == {}  # GUARDRAILS.md holds it
    assert pages.Caps.of(W3Rules()).cap("rare", "chato") == (93, "dealer_price_caps chato:rare")
    page = next(
        p
        for p in pages.page_economics(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES, what_if={("chato", "rare"): 86})
        if p.set_code == "LAV"
    )
    lav10 = missing(page, "LAV-10")
    assert (
        by_source(lav10, "chato").blocked
        == "what-if chato:rare 86 < median chato fill 87.5: fills only at its luckiest"
    )
    assert by_source(lav10, "teams").blocked == "max_price_rare 80 < team price 112"  # teams keep max_price_rare


def test_the_card_that_completes_a_page_waits_for_the_other_legs_or_it_completes_nothing():
    legs = [
        want("LAV-09", "chato", 200, channel="ladder", finishing=True),  # above the hour cap: never fits
        want("LAV-10", "teams", 75, finishing=True, completes=True, page_bonus=70),
    ]
    s = pages.cash_plan("p", 1000, 4, [], legs, RULES)
    assert s.bought == () and s.trade_surplus == 0
    assert s.steps[-1].note == "waits for the page's other legs: LAV-09"


def test_a_ladder_slot_never_buys_the_completing_card_before_the_other_legs():
    legs = [
        want("LAV-09", "chato", 200, channel="ladder", finishing=True),  # above the hour cap: never fits
        want("LAV-10", "abuela", 70, channel="ladder", finishing=True, completes=True, page_bonus=70),
    ]
    named = pages.LadderSlot(4, "abuela", "card:rare", 70, 77, "LAV-10")
    s = pages.cash_plan("s", 1000, 4, [], legs, RULES, ladder=[named])
    assert s.steps[0].kind == "held" and s.steps[0].note == "waits for the page's other legs: LAV-09"
    by_class = pages.LadderSlot(4, "abuela", "card:rare", 70, 77)
    s = pages.cash_plan("s", 1000, 4, [], legs, RULES, ladder=[by_class])
    assert s.bought == () and s.steps[0].note.startswith("W3 slot (no page card fits it)")


def test_a_ladder_slot_that_does_not_fit_waits_for_the_grant():
    slot = pages.LadderSlot(2, "abuela", "card:uncommon", 22, 25)
    s = pages.cash_plan("w", 280, 2, [pages.Grant(4.05, 150, "Saturday")], [], RULES, ladder=[slot])
    assert [(x.hour, x.kind) for x in s.steps] == [(4, "grant"), (4, "ladder")]  # 280 − 22 < 270 until then
    early = pages.LadderSlot(1, "abuela", "card:common", 9, 12)  # before the plan starts: history
    assert pages.cash_plan("w", 600, 2, [], [], RULES, ladder=[early]).steps == ()


def test_our_deals_today_hold_their_best_three_slots_and_planning_a_later_day_counts_none(monkeypatch):
    """r1 review: Friday's deals must not close Saturday's slots, and a mid-day re-run must count today's."""
    friday = [{"id": 1, "tick": 0, "type": "day.opened", "payload": {"day": "fri"}}]
    friday += [settle(10 + i, 100 + i, "abuela", "t01", "LAV-06", 22, tick=20 + i, kind="card") for i in range(3)]
    seen = {}
    real = pages.scoring_dealers

    def spy(*a, **kw):
        seen["out"] = real(*a, **kw)
        return seen["out"]

    monkeypatch.setattr(pages, "scoring_dealers", spy)
    pages.build_plan(ME, CATALOG, friday, DEALERS, SCHEDULE, PARAMS, RULES, now_hours=4.0)  # Saturday, from Friday
    assert seen["out"].get("abuela") == 3
    pages.build_plan(ME, CATALOG, friday, DEALERS, SCHEDULE, PARAMS, RULES)  # Friday itself: its deals hold
    assert "abuela" not in seen["out"]
    saturday = [*friday, {"id": 20, "tick": 160, "type": "day.opened", "payload": {"day": "sat"}}]
    saturday.append(settle(21, 200, "abuela", "t01", "LAV-01", 9, tick=161, kind="card"))
    today = {**SCHEDULE, "now_hours": 5.5}
    pages.build_plan(ME, CATALOG, saturday, DEALERS, today, PARAMS, RULES, now_hours=5.5)  # a mid-morning re-run
    assert seen["out"].get("abuela") == 2  # one deal today; Friday's three are replaced by better ones


def test_w3_slots_move_back_only_when_the_clock_shows_an_earlier_hour_than_w3_planned_for():
    row = {"game_hour": 4, "dealer": "abuela", "price_class": "card:common", "plan": {"max": 12}, "expected": {}}
    plan = {"window": {"t_start": 4.0}, "schedule": [row]}
    assert pages.ladder_slots_from(plan, open_hour=2)[0].hour == 2
    assert pages.ladder_slots_from(plan, open_hour=5)[0].hour == 4  # a re-run after the open moves nothing
