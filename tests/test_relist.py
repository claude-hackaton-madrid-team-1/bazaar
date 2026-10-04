"""Relisting a copy after its ask lapsed unsold: step down, never the same price twice in a row, rest."""

from bazaar_agent.agents.relist import AskTrail, market_median, relist_floor, relist_price, step_down
from bazaar_agent.decisions import RELIST_REST, Decision, DecisionLog
from bazaar_agent.intel import Print
from tests.agent_fakes import TICK, clock, rows
from tests.test_maker import NoAccept, maker, posted

RULES = {"step_share": 0.05, "min_share": 0.6, "max_lapses": 4, "cooldown_ticks": 40}


def price(target, trail, *, open_price=None, cost_floor=0, median=None, tick=500):
    return relist_price(target, trail, open_price=open_price, cost_floor=cost_floor, median=median, tick=tick, **RULES)


def walk(target, first, cost_floor, lapses):
    """The asks one copy would get over `lapses` unsold lapses, from a first ask of `first` (None: a rest)."""
    prices, since, rest_until, out, tick = [first], 1, None, [first], 0
    for _ in range(lapses):
        tick += 20
        r = price(target, AskTrail(1, tuple(prices), since, rest_until), cost_floor=cost_floor, tick=tick)
        if r.rest_until is not None:
            since, rest_until = 0, r.rest_until
        out.append(r.price)
        if r.price is not None:
            prices.append(r.price)
            since += 1
    return out


def test_step_is_five_percent_and_at_least_one():
    assert step_down(10, 0.05) == 9 and step_down(26, 0.05) == 25 and step_down(76, 0.05) == 72
    assert step_down(200, 0.05) == 190


def test_first_listing_is_the_target_and_an_open_ask_stands():
    assert price(10, AskTrail(11)).price == 10
    trail = AskTrail(11, (10, 9), 2)
    assert price(10, trail, open_price=9).price == 9  # no churn back up to the target
    assert price(8, trail, open_price=9).price == 8  # the strategy's target fell: follow it down


def test_a_lapsed_ask_steps_down_and_never_repeats_until_it_rests():
    # SAL-01 #11 was posted 16 times at 10 on Sat 3 Oct. Floor: 0.6 × 10 = 6 (its value is lower).
    asks = walk(10, 10, 3, 6)
    assert asks[:5] == [10, 9, 8, 7, None]  # 4 asks lapsed unsold: rest 40 ticks
    assert asks[5] is None  # still resting
    posted_asks = [a for a in asks if a is not None]
    assert all(a != b for a, b in zip(posted_asks, posted_asks[1:], strict=False))


def test_at_the_floor_the_copy_rests_instead_of_repeating_then_lists_once_more():
    trail = AskTrail(5, (12, 11), 2)
    r = price(12, trail, cost_floor=11, tick=300)
    assert r.price is None and r.rest_until == 340 and "would repeat" in r.why
    resting = AskTrail(5, (12, 11), 0, 340)
    assert price(12, resting, cost_floor=11, tick=339).price is None  # still resting: no new rest row
    assert price(12, resting, cost_floor=11, tick=339).rest_until is None
    assert price(12, resting, cost_floor=11, tick=340).price == 11  # a rest stands between the two posts
    again = AskTrail(5, (12, 11, 11), 1, 340)
    assert price(12, again, cost_floor=11, tick=360).rest_until == 400


def test_floor_is_the_highest_of_cost_and_share_of_first_ask():
    assert relist_floor(AskTrail(1, (76, 72)), 40, 0.6) == 46  # ceil(0.6 × 76)
    assert relist_floor(AskTrail(1, (10,)), 8, 0.6) == 8
    assert price(76, AskTrail(1, (76, 50), 2), cost_floor=40).price == 48  # 50 − round(2.5) = 48
    assert price(76, AskTrail(1, (76, 48), 2), cost_floor=40).price == 46  # 48 − 2, at the floor 46
    assert price(76, AskTrail(1, (76, 47), 2), cost_floor=40).price == 46


def test_market_median_pulls_a_relist_down_to_it_but_never_below_the_floor():
    trail = AskTrail(676, (26,), 1)
    assert price(26, trail, cost_floor=16, median=22.5).price == 23  # MAL-08: the uncommon median on rastro
    assert price(26, trail, cost_floor=16, median=12.0).price == 25  # median under the floor 16: plain step
    assert price(26, trail, cost_floor=16, median=30.0).price == 25  # median above: plain step


def test_market_median_reads_the_venue_card_then_rarity_in_the_window():
    def p(tick, ref, price, venue="rastro", items=1, seller="t03", buyer="t02"):
        return Print(tick, tick, buyer, seller, None, venue, ref, "card", items, price, 0)

    rarities = {"MAL-08": "uncommon", "LAT-08": "uncommon", "SAL-01": "common"}
    prints = [
        p(400, "LAT-08", 20),
        p(450, "LAT-08", 25, seller="t05"),
        p(455, "MAL-08", 23, seller="t07"),
        p(460, "SAL-01", 9),
        p(470, "LAT-08", 99, venue="v03"),
    ]
    assert market_median(prints, "rastro", "MAL-08", "uncommon", rarities, 500, "t01") == 23  # 3 fills by rarity
    assert market_median(prints, "rastro", "LAT-08", "uncommon", rarities, 500, "t01") == 23  # 2 of LAT-08: too few
    assert market_median(prints, "rastro", "LAT-08", "uncommon", rarities, 560, "t01") is None  # 400 left
    assert market_median(prints, "rastro", "SAL-03", "rare", rarities, 500, "t01") is None


def test_one_staged_fill_or_our_own_fills_never_set_the_median():
    def p(ref, price, seller, buyer="t02", items=1):
        return Print(1, 450, buyer, seller, None, "rastro", ref, "card", items, price, 0)

    rarities = {"SAL-01": "common", "SAL-02": "common"}
    one_seller = [p("SAL-02", 5, "t09"), p("SAL-02", 5, "t09"), p("SAL-02", 5, "t09")]
    assert market_median(one_seller, "rastro", "SAL-01", "common", rarities, 500, "t01") is None
    ours = [p("SAL-01", 6, "t01"), p("SAL-01", 6, "t04", buyer="t01"), p("SAL-01", 6, "t05"), p("SAL-01", 6, "t06")]
    assert market_median(ours, "rastro", "SAL-01", "common", rarities, 500, "t01") is None  # 2 left: too few
    bundles = [p("SAL-01", 6, s, items=2) for s in ("t04", "t05", "t06")]
    assert market_median(bundles, "rastro", "SAL-01", "common", rarities, 500, "t01") is None


def test_a_target_above_every_past_ask_lists_afresh():
    # PR #205 review P1: MAL-02 #671 open at 9; a chaser appears and the strategy now prices it at 25.
    assert price(25, AskTrail(671, (10, 9), 2), open_price=9).price == 25
    assert price(25, AskTrail(671, (10, 9), 0, 600), tick=550).price == 25  # even while resting
    assert price(10, AskTrail(671, (10, 9), 2), open_price=9).price == 9  # the same need: the step stands


def test_a_floor_that_rose_above_the_last_ask_lists_at_the_floor_instead_of_resting():
    r = price(25, AskTrail(1, (25, 21), 2), cost_floor=22)
    assert (r.price, r.rest_until, r.floor) == (22, None, 22)


def test_ask_rows_read_live_sent_asks_and_rests_only(tmp_path):
    log = DecisionLog(tmp_path)

    def row(kind, tick, inputs, dry=False, status="approved", chosen=True):
        return log.decide(Decision("maker", tick, kind, inputs, "r", "allowed", chosen, status, dry))

    row("post_ask", 10, {"asset_id": 11, "price": 10})
    failed = row("post_ask", 11, {"asset_id": 11, "price": 10})
    log.settle(failed, "failed")
    row("post_ask", 12, {"asset_id": 11, "price": 9}, dry=True)
    row("post_ask", 13, {"asset_id": 11, "price": 9}, status="rejected", chosen=False)
    row("post_bid", 14, {"asset_id": None, "price": 9})
    row(RELIST_REST, 15, {"asset_id": 11, "until_tick": 55})
    row("post_ask", 2, {"asset_id": 11, "price": 12})
    assert log.ask_rows("maker", 5) == [(10, "post_ask", 11, 10), (15, RELIST_REST, 11, 55)]


def test_the_live_maker_steps_a_lapsed_ask_down_and_a_restart_remembers(tmp_path):
    team = NoAccept()
    prices: list[int] = []
    for i in range(3):  # each tick the ask lapsed: the server lists no open offer of ours
        m, _ = maker(tmp_path, team, live=True)  # a new process each tick: the history is in the decision log
        team.offers = []
        before = len(posted(team))
        m.on_tick(clock(tick=TICK + 20 * i))
        prices += [p[2]["cash"] for p in posted(team)[before:] if p[1].get("assets") == [4]]
        # Observe the acknowledged listings while they stand. A vanished offer
        # that was never observed must remain reserved, rather than guessed expired.
        team.offers = [
            {**r["request"], **r["response"], "maker": "t01", "created_tick": r["tick"], "expires_tick": r["tick"] + 20}
            for r in rows(tmp_path, "executions.jsonl")
            if r["sdk_method"] == "list_offer" and r["tick"] == TICK + 20 * i
        ]
        m.on_tick(clock(tick=TICK + 20 * i + 1))
    assert prices == [10, 9, 8], prices
