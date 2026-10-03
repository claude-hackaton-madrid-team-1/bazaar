"""The trade desk: holdings from the feed, fair prices on the affinity map, the share rule, the posting."""

import itertools
import json
import random
from collections import Counter

import pytest

from bazaar_agent import trade_desk as td
from bazaar_agent.affinity import AffinityMap, TeamAffinity
from bazaar_agent.agents.market import venues_from
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import build_market
from tests.agent_fakes import RASTRO
from tests.test_strategy import CATALOG, ME, PARAMS

SETS = ("LAV", "LAT", "RET")
VENUE = venues_from({"venues": [RASTRO]})[0]  # 5 % + 1 P per card, paid by the accepting side


def known(team, **mult):
    """A team whose multipliers we know for sure (a degenerate posterior): values are exact."""
    full = {s: mult.get(s, 1.0) for s in SETS}
    top = max(full.values())
    return TeamAffinity(
        team,
        SETS,
        {s: 1.0 if a == top else 0.0 for s, a in full.items()},
        dict(full),
        {s: {a: 1.0} for s, a in full.items()},
        {},
        1,
    )


def got(eid, to, ref, asset, price=20, frm="t20", tick=5):  # a team sold it, unless a dealer is named
    items = [{"id": asset, "ref": ref, "frm": frm, "to": to, "kind": "card"}]
    persona = frm if not frm.startswith("t") else None
    payload = {"items": items, "price": price, "persona": persona, "parties": [frm, to], "settlement": eid}
    return {"id": eid, "tick": tick, "type": "settlement", "payload": payload}


def lists(eid, team, ref, asset, tick=6):
    offer = {"id": eid, "give": {"assets": [{"id": asset, "ref": ref}]}, "want": {"cash": 30}}
    return {"id": eid, "tick": tick, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


EVENTS = [
    got(1, "t03", "LAV-08", 801),
    got(2, "t03", "LAV-08", 802),
    got(3, "t09", "LAV-09", 901, price=90, frm="chato"),
    got(4, "t05", "LAV-02", 803, price=9),
    got(5, "t06", "LAV-02", 804, price=9),
]
AMAP = AffinityMap(
    {
        "t03": known("t03", LAV=0.5, LAT=1.1),
        "t09": known("t09", LAV=0.5, LAT=1.6),
        "t15": known("t15", LAT=1.6, LAV=0.7),
        "t18": known("t18", LAT=1.3, LAV=0.9),
        "t05": known("t05", LAV=0.5, RET=1.6),
        "t06": known("t06", LAV=0.7, LAT=1.1),
    }
)


def market(events=EVENTS):
    return build_market(ME, CATALOG, events, [])


# ---------------------------------------------------------------- holdings


def test_holdings_follow_settlements_and_listings_in_order():
    events = [got(1, "t03", "LAV-08", 801), lists(2, "t03", "LAV-08", 801), got(3, "t05", "LAV-08", 801, frm="t03")]
    owner = td.holdings(events)
    assert owner[801].holder == "t05"
    assert td.holdings(events[:2])[801].holder == "t03"
    copies = td.team_copies(td.holdings(EVENTS + [got(4, "t01", "LAV-02", 7)]), "t01")
    assert copies["t03"]["LAV-08"] == 2 and "t01" not in copies


def test_value_distribution_and_tail_probabilities():
    dist = td.value_dist(AMAP.teams["t15"], "LAT", 70, 1.0)
    assert dist == [(pytest.approx(112.0), 1.0)]
    assert td.value_dist(None, "LAT", 70, 0.25) == [(17.5, 1.0)]  # unknown team: book × marginal
    assert td.p_at_least([(10, 0.5), (20, 0.5)], 15) == 0.5 and td.p_at_most([(10, 0.5), (20, 0.5)], 15) == 0.5


# ---------------------------------------------------------------- prices


def test_an_ask_keeps_half_the_pie_for_the_buyer_at_most():
    m = market()
    ours = td.our_copies(m, ME, PARAMS, Guardrails())
    lat09 = next(o for o in ours if o.ref == "LAT-09")
    assert lat09.loss == pytest.approx(45.0)  # 35 + the whole LAT page bonus (0.25 × 80 × 0.5): the page is full
    asks = {(t.counterparty, t.refs[0]): t for t in td.ask_trades(m, ours, AMAP, {}, td.PlanParams(), VENUE)}
    t = asks[("t15", "LAT-09")]
    assert t.give == {"assets": [5]} and t.want == {"cash": t.price}
    pie = 112 - t.fee - 45
    assert t.price == pytest.approx(45 + pie / 2, abs=1)  # at most our half: the buyer keeps the rest
    assert t.ours >= PARAMS.sell_min_surplus and t.theirs > 0 and t.p_fill == 1.0
    assert t.volume == t.price  # notional: the cash, at least the book (70)
    assert asks[("t03", "LAT-09")].price < t.price  # 70 × 1.1 = 77 to t03: a smaller pie, a lower ask


def test_a_bid_never_passes_the_cap_or_beats_a_dealer():
    m = market()
    copies = td.team_copies(td.holdings(EVENTS), "t01")
    wanted = {w.ref: w for w in td.wanted_cards(m, PARAMS, Guardrails())}
    assert wanted["LAV-08"].top == 26  # max_price_uncommon
    bids = {t.refs[0]: t for t in td.bid_trades(m, list(wanted.values()), AMAP, copies, td.PlanParams(), VENUE)}
    b = bids["LAV-08"]
    assert b.counterparty == "t03" and b.price <= 26 and b.ours > 0 and b.theirs > 0
    assert b.give == {"cash": b.price} and b.want == {"cards": ["LAV-08"]}
    # the cap binds below the fair price: we bid the cap, they still gain
    tight = Guardrails(max_price_uncommon=10)
    w2 = [w for w in td.wanted_cards(m, PARAMS, tight) if w.ref == "LAV-08"]
    (capped,) = td.bid_trades(m, w2, AMAP, copies, td.PlanParams(), VENUE)
    assert capped.price == 10 and capped.theirs > 0
    # a dealer sold it for 15: worth no more than 15 to us, a team bid below it, and short-lived
    dealer = td.dealer_prices([got(9, "t07", "LAV-08", 777, price=15, frm="abuela")])
    assert dealer == {"LAV-08": [15]}
    (w,) = [w for w in td.wanted_cards(m, PARAMS, Guardrails(), dealer) if w.ref == "LAV-08"]
    assert (w.worth, w.top, w.dealer) == (15.0, 13, True)  # min(worth − min surplus, cap, fill − 1)
    (cheap,) = td.bid_trades(m, [w], AMAP, copies, td.PlanParams(), VENUE)
    assert cheap.price <= 13 and cheap.expires == 10 and "--expires 10" in td.command(cheap)


def test_a_swap_splits_the_pie_with_a_cash_leg():
    m = market()
    copies = td.team_copies(td.holdings(EVENTS), "t01")
    ours = td.our_copies(m, ME, PARAMS, Guardrails())
    wanted = td.wanted_cards(m, PARAMS, Guardrails())
    swaps = td.swap_trades(m, ours, wanted, AMAP, copies, td.PlanParams(), VENUE)
    s = next(t for t in swaps if t.counterparty == "t09" and t.refs == ("LAT-09", "LAV-09"))
    assert s.give["assets"] == [5] and s.want["cards"] == ["LAV-09"]
    assert abs(s.ours - s.theirs) <= 1  # the cash leg splits the pie
    assert s.ours > 0 and s.theirs > 0 and s.fee == 2  # 1 P per card, theirs to pay
    cash = s.want.get("cash", 0) - s.give.get("cash", 0)
    assert cash == s.price


# ---------------------------------------------------------------- the share rule


def trade(team, volume, expected, ref, kind="ask", cash=0, rarity="common"):
    asset = None if kind == "bid" else sum(map(ord, ref))
    give = {"cash": cash} if kind == "bid" else {"assets": [asset]}
    price = cash if kind == "bid" else volume
    return td.Trade(kind, team, give, {}, (ref,), asset, price, 0, expected, 1.0, 1.0, volume, "", rarity)


def brute(pool, pp, cash_room):
    best = 0.0
    for n in range(len(pool) + 1):
        for combo in itertools.combinations(pool, n):
            items = [i for t in combo for i in td.items_used(t)]
            cash = sum(td._cash_out(t) for t in combo)
            if len(items) != len(set(items)) or cash > cash_room or len(combo) > pp.listings:
                continue
            if td._fair(combo, pp.max_share):
                best = max(best, sum(t.expected for t in combo))
    return best


@pytest.mark.parametrize("seed", range(25))
def test_choose_finds_the_best_fair_plan(seed):
    rng = random.Random(seed)
    teams = ["t02", "t03", "t04", "t05", "t06"]
    pool = []
    for i in range(9):
        kind = rng.choice(["ask", "bid"])
        cash = rng.randint(5, 40) if kind == "bid" else 0
        pool.append(trade(rng.choice(teams), rng.randint(5, 80), rng.randint(1, 30), f"C-{i % 7}", kind, cash))
    pp = td.PlanParams(listings=12, threads=0, max_share=0.25)
    listings, threads, free, proven = td.choose(pool, [], pp, cash_room=60)
    assert proven and threads == []
    assert td._fair(listings, 0.25)
    assert sum(t.expected for t in listings) == pytest.approx(brute(pool, pp, 60))
    assert sum(t.expected for t in free) >= sum(t.expected for t in listings) - 1e-9


def test_one_big_trade_cannot_be_planned_alone():
    pool = [trade("t02", 70, 20, "C-1")] + [trade(t, 10, 2, f"C-{i + 2}") for i, t in enumerate(["t03", "t04"])]
    listings, _, free, _ = td.choose(pool, [], td.PlanParams(threads=0), cash_room=0)
    assert listings == [] and len(free) == 3  # 70 of 90 is 78 %: nothing passes but an empty plan
    more = pool + [trade(t, 70, 1, f"C-{i + 9}") for i, t in enumerate(["t05", "t06", "t07"])]
    listings, _, _, _ = td.choose(more, [], td.PlanParams(threads=0), cash_room=0)
    assert td._fair(listings, 0.25) and any(t.refs == ("C-1",) for t in listings)


# ---------------------------------------------------------------- posting


def test_post_as_lists_for_anyone_until_a_team_could_pass_its_share():
    rules = Guardrails(max_counterparty_share=0.25, counterparty_cap_base=200)  # 50 per team
    me = {**ME, "cash": 1000}
    refs = ["LAV-02", "LAV-08", "LAV-10"]
    bids = [trade(t, 20, 5, ref, "bid", 20, "uncommon") for t, ref in zip(["t02", "t03", "t04"], refs, strict=True)]
    # t09 settled 30 with us: a public offer counts against it too, so 30 + 20 is the most public exposure
    posted, problems = td.post_as(bids, me, td.Start(settled={"t09": 30}), rules)
    assert [t.to for t in posted] == [None, "t03", "t04"] and problems == []
    posted, problems = td.post_as(bids[:1], me, td.Start(settled={"t02": 45}), rules)  # neither way fits t02 any more
    assert posted[0].to == "t02" and len(problems) == 1 and "counterparty t02: 45 + 20" in problems[0]


def test_post_as_refuses_what_the_cash_floor_refuses():
    bid = trade("t02", 20, 5, "LAV-08", "bid", 20, "uncommon")
    _, problems = td.post_as([bid], {**ME, "cash": 280}, td.Start(), Guardrails())
    assert problems and "cash_floor" in problems[0]


# ---------------------------------------------------------------- the plan


def test_a_small_world_has_no_plan_that_meets_the_share():
    plan = td.build_plan(ME, CATALOG, EVENTS, AMAP, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    assert plan.listings == plan.threads == () and plan.proven  # 2 teams absorb all the volume: none fair
    assert plan.unconstrained > 0 and plan.held_back
    loose = td.build_plan(ME, CATALOG, EVENTS, AMAP, PARAMS, Guardrails(), td.PlanParams(max_share=0.6), VENUE)
    assert loose.listings and max(loose.shares.values()) <= 0.6


def rich_world():
    """Three LAT uncommons we hold at 0.5 (the LAT page is not complete), teams that want them, and the
    holders of LAV cards we miss."""
    from tests.test_strategy import card

    catalog = {**CATALOG, "sets": [dict(s) for s in CATALOG["sets"]]}
    lat = next(s for s in catalog["sets"] if s["id"] == "LAT")
    lat["cards"] = [*lat["cards"], *(card(f"LAT-0{i}", "uncommon", 5) for i in (6, 7, 8)), card("LAT-10", "rare", 2)]
    me = {
        **ME,
        "cash": 420,
        "album": {"pages": [{"set": "LAV", "have": 2, "of": 6}, {"set": "LAT", "have": 5, "of": 5}]},
    }
    me["assets"] = [
        *ME["assets"],
        *(
            {"id": 60 + i, "kind": "card", "ref": f"LAT-0{i}", "rarity": "uncommon", "your_value": 12.5}
            for i in (6, 7, 8)
        ),
    ]
    amap = AffinityMap(
        {
            **AMAP.teams,
            "t02": known("t02", LAT=1.6),
            "t04": known("t04", LAT=1.3, LAV=0.7),
            "t07": known("t07", LAT=1.6, LAV=0.5),
        }
    )
    return me, catalog, amap


def test_the_plan_is_fair_and_every_trade_pays_us():
    me, catalog, amap = rich_world()
    plan = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    trades = [*plan.listings, *plan.threads]
    assert trades and plan.checks == () and plan.proven
    assert all(t.ours > 0 and t.theirs > 0 for t in trades)
    assert max(plan.shares.values()) <= 0.25 and len(plan.shares) >= 4
    assert plan.worst_share <= 0.25  # even if one team took every listing posted for anyone
    assert sum(td._cash_out(t) for t in trades) <= plan.cash_room == 150
    assert plan.unconstrained >= plan.expected > 0
    assert len({i for t in trades for i in td.items_used(t)}) == sum(len(td.items_used(t)) for t in trades)  # each once


def test_the_page_buy_list_names_holders_dealers_and_the_cap():
    plan = td.build_plan(ME, CATALOG, EVENTS, AMAP, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    page = {r.ref: r for r in plan.page}
    assert set(page) == {"LAV-02", "LAV-08", "LAV-09", "LAV-10"}  # LAV-01 and LAV-06 we hold
    assert page["LAV-09"].holders[0][0] == "t09" and page["LAV-09"].dealer == (90,)
    assert [h[0] for h in page["LAV-02"].holders] == ["t05", "t06"]  # the one that loses least first
    assert "above max_price_rare 80" in page["LAV-09"].verdict
    assert "no known holder" in page["LAV-10"].verdict


def test_verify_names_every_broken_promise():
    bad = td.Trade(
        "bid", "t02", {"cash": 90}, {"cards": ["LAV-09"]}, ("LAV-09",), None, 90, 5, -1, -2, 1, 90, "", "rare"
    )
    plan = td.TradePlan(1, 300, 30, (bad,), (), (), {"t02": 1.0}, (), 1)
    problems = td.verify(plan, td.PlanParams(), Guardrails())
    assert len(problems) == 6 and any("could take 100%" in p for p in problems)
    assert any("no surplus for us" in p for p in problems) and any("nothing left for them" in p for p in problems)
    assert any("above max_price_rare 80" in p for p in problems) and any("100%" in p for p in problems)
    assert any("> 30 above cash_floor" in p for p in problems)


def test_requests_and_the_page_are_what_a_human_or_the_maker_would_send(tmp_path):
    me, catalog, amap = rich_world()
    plan = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    assert plan.listings
    data = td.plan_dict(plan)
    for row in data["listings"]:
        assert row["request"]["venue"] == "rastro" and row["request"]["give"] == row["give"]
    for row in data["threads"]:
        req = row["requests"]
        assert req["open_thread"]["with"] == row["counterparty"] and req["message"]["offer"]["want"] == row["want"]
    json.dumps(data, default=str)
    page = td.plan_markdown(plan, td.PlanParams())
    assert "# Trade plan" in page and "## LAV page buy list" in page and "| LAV-09 | rare |" in page


def test_the_cli_writes_the_plan_from_files(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bazaar_agent import cli

    (tmp_path / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS))
    (tmp_path / "me.json").write_text(json.dumps({"body": ME}))
    (tmp_path / "catalog.json").write_text(json.dumps({"body": CATALOG}))
    (tmp_path / "venues.json").write_text(json.dumps({"body": {"venues": [RASTRO]}}))
    args = ["trade-plan", "--events", str(tmp_path / "feed.jsonl"), "--me", str(tmp_path / "me.json")]
    args += ["--catalog", str(tmp_path / "catalog.json"), "--venues", str(tmp_path / "venues.json")]
    out = CliRunner().invoke(cli.app, [*args, "--out", str(tmp_path / "out")])
    assert out.exit_code == 0, out.output
    plan = json.loads((tmp_path / "out" / "trade-plan.json").read_text())
    assert plan["checks"] == [] and plan["proven"] and (tmp_path / "out" / "trade-plan.md").is_file()


def test_a_thread_proposal_opens_a_team_thread_and_sends_one_structured_offer():
    swap = td.Trade(
        "swap",
        "t09",
        {"assets": [5], "cash": 13},
        {"cards": ["LAV-09"]},
        ("LAT-09", "LAV-09"),
        5,
        -13,
        2,
        40,
        40,
        1.0,
        140,
        "",
        "rare",
        "t09",
    )
    req = td.thread_proposal(swap)
    assert req["open_thread"] == {
        "with": "t09",
        "venue": "rastro",
        "topic": {"swap": {"give": "LAT-09", "want": "LAV-09"}},
    }
    assert req["message"]["offer"] == {"give": {"assets": [5], "cash": 13}, "want": {"cards": ["LAV-09"]}}
    assert (
        req["message"]["text"]
        == "Swap proposal: our LAT-09 + 13 P for your LAV-09. Accept the offer if it works for you."
    )
    ask = td.Trade(
        "ask", "t15", {"assets": [5]}, {"cash": 76}, ("LAT-09",), 5, 76, 5, 31, 31, 1.0, 76, "", "rare", to="t15"
    )
    assert td.listing_request(ask) == {
        "venue": "rastro",
        "give": {"assets": [5]},
        "want": {"cash": 76},
        "expires_in_ticks": 40,
        "to": "t15",
    }


def test_a_cash_budget_keeps_the_plan_to_swaps_and_asks():
    me, catalog, amap = rich_world()
    plan = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(cash_budget=0), VENUE)
    assert plan.cash_room == 0 and plan.checks == ()
    assert all(td._cash_out(t) == 0 for t in (*plan.listings, *plan.threads))


def test_our_open_offers_and_this_hours_spend_shrink_the_plan():
    me, catalog, amap = rich_world()
    free = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    open_bid = {
        "id": 70,
        "maker": "t01",
        "to": None,
        "status": "open",
        "give": {"cash": 100},
        "want": {"cards": ["MAL-01"]},
    }
    busy = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(), VENUE, [open_bid], spent=20)
    assert free.cash_room == 150 and busy.cash_room == 50  # min(420 - 100 - 270, 150 - 20)
    assert sum(td._cash_out(t) for t in (*busy.listings, *busy.threads)) <= 50 and busy.checks == ()


def test_what_if_reports_the_cap_and_refused_trades_leave_the_plan():
    me, catalog, amap = rich_world()
    off = td.build_plan(me, catalog, EVENTS, amap, PARAMS, Guardrails(), td.PlanParams(), VENUE)
    assert [w.split(":")[0] for w in off.what_if] == [
        "with max_counterparty_share 0.25 and counterparty_cap_base 200",
        "with max_counterparty_share 0.25 and counterparty_cap_base 400",
    ]
    on = Guardrails(max_counterparty_share=0.25, counterparty_cap_base=60)  # 15 P per team: most trades refused
    plan = td.build_plan(me, catalog, EVENTS, amap, PARAMS, on, td.PlanParams(), VENUE)
    assert plan.what_if == () and plan.dropped and plan.checks == ()
    assert len(plan.dropped) == len(set(plan.dropped))
    for t in (*plan.listings, *plan.threads):
        assert not td._refused(t, plan.dropped)
    assert "## Refused by the guardrails and replaced" in td.plan_markdown(plan, td.PlanParams())


def test_a_large_pool_is_cut_before_the_search():
    import time

    rng = random.Random(7)
    teams = [f"t{i:02d}" for i in range(2, 19)]
    big = [trade(rng.choice(teams), rng.randint(5, 90), rng.randint(1, 40), f"C-{i % 60}") for i in range(1500)]
    pool = td._pool(big, td.PlanParams())
    assert len(pool) <= 120 and max(Counter(i for t in pool for i in td.items_used(t)).values()) <= 6
    began = time.monotonic()
    listings, _, _, _ = td.choose(big, [], td.PlanParams(threads=0), cash_room=0, max_nodes=50_000)
    assert time.monotonic() - began < 30 and td._fair(listings, 0.25)


def test_without_a_file_the_feed_comes_from_the_db_then_the_capture_then_the_live_window(monkeypatch, tmp_path):
    from bazaar_agent import cli

    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("bazaar_agent.config.read_env_file", lambda path: {})

    class Row:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Conn:
        closed, autocommit = False, False

        def execute(self, sql, args):
            return Row([(e["id"], e["tick"], e["type"], "", e["payload"]) for e in EVENTS])

    monkeypatch.setattr(cli, "_db_connect", lambda app: lambda: Conn())
    assert [e["id"] for e in cli._history(None, live=False)] == [e["id"] for e in EVENTS]

    def down():
        raise OSError("no db")

    monkeypatch.setattr(cli, "_db_connect", lambda app: down)
    monkeypatch.setattr(cli, "_events", lambda live: [{"id": 1, "tick": 0, "type": "clock", "payload": {}}])
    assert [e["id"] for e in cli._history(None, live=False)] == [1]  # nothing captured: the live window


def test_public_listings_count_against_every_team_in_the_plan():
    me = {**ME, "cash": 1000}
    refs = ["LAV-02", "LAV-08", "LAV-10", "LAT-10"]
    bids = [
        trade(t, 25, 5, ref, "bid", 25, "uncommon") for t, ref in zip(["t02", "t03", "t04", "t05"], refs, strict=True)
    ]
    # 4 × 25 planned, 25 % each: one public listing leaves room for nobody else to be addressed
    posted, problems = td.post_as(bids, me, td.Start(), Guardrails(), share=0.25)
    assert problems == [] and [t.to for t in posted] == ["t02", "t03", "t04", "t05"]
    loose, _ = td.post_as(bids, me, td.Start(), Guardrails(), share=1.0)
    assert [t.to for t in loose] == [None] * 4  # today's posting: the cap off, everything public
