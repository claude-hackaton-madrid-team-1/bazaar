"""The maker: sell floor, cash floor with open bids, listing/offer caps, reprice/cancel, tick budget, no accepts."""

from bazaar_agent.agents.maker import Maker, MakerConfig, Target, plan_offers, sell_floor, targets_from
from bazaar_agent.agents.market import OpenOffer, best_venue, venues_from
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ticks import Clock
from tests.agent_fakes import CHEAP, OURS, RASTRO, TICK, FakePublic, FakeTeam, bid, clock, our_ask, parts, rows
from tests.test_strategy import ME, playbook


def maker(tmp_path, team, public=None, *, live=False, **rules):
    lines: list[str] = []
    m = Maker(team, public or FakePublic(), live=live, log=lines.append, now=lambda: 1000.0, **parts(tmp_path, **rules))
    return m, lines


def posted(team):
    return [s for s in team.sent if s[0] == "list_offer"]


class NoAccept(FakeTeam):
    def accept(self, offer_id, assets=None):
        raise AssertionError("the maker never uses the team's accept slot")


def test_targets_are_the_strategys_sells_and_team_bids():
    targets = targets_from(playbook())
    assert [(t.side, t.ref, t.price) for t in targets] == [
        ("bid", "LAV-09", 65),
        ("ask", "LAT-09", 68),
        ("ask", "LAT-03", 10),
    ]
    assert {t.asset_id for t in targets if t.side == "ask"} == {5, 4}


def test_dry_run_sends_nothing_and_records_would_posts(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team)
    m.on_tick(clock())
    assert team.sent == []
    assert any(line.startswith(f"tick {TICK} maker: WOULD post ask LAT-09 #5 at 68 on rastro") for line in lines)
    kinds = sorted(r["kind"] for r in rows(tmp_path) if r.get("chosen"))
    assert kinds == ["post_ask", "post_ask", "post_bid"] and all(r["dry_run"] for r in rows(tmp_path))
    assert rows(tmp_path, "executions.jsonl") == []
    assert team.reads.count("me") == 1


def test_open_bids_and_cash_never_break_the_cash_floor(tmp_path):
    # cash 350, floor 270. Alone, the 65 LAV-09 bid fits (285 left); with our dealer-thread bid of 40 open
    # (the desk's, so the maker does not cancel it) it would leave 245: refused.
    alone = NoAccept(me={**ME, "cash": 350})
    maker(tmp_path, alone, live=True)[0].on_tick(clock())
    assert [p for p in posted(alone) if p[1].get("cash")] == [
        ("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro")
    ]

    team = NoAccept(me={**ME, "cash": 350}, offers=[bid(70, "LAV-08", 40, thread=5000)])
    m, lines = maker(tmp_path / "b", team, live=True)
    m.on_tick(clock())
    assert not [p for p in posted(team) if p[1].get("cash")] and ("cancel", 70) not in team.sent
    assert any("cash 310 - 65 < cash_floor 270" in line for line in lines)


def test_a_bid_is_posted_live_with_its_spend_and_listing_in_the_ledger(tmp_path):
    team = NoAccept()
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    bids = [p for p in posted(team) if p[1].get("cash")]
    assert bids == [("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro")]
    ledger = m.ledger
    assert ledger.spent_since(0) == 65 and ledger.count_in_tick("listing", TICK) == 3
    assert {r["sdk_method"] for r in rows(tmp_path, "executions.jsonl")} == {"list_offer"}


def test_never_lists_below_the_sell_floor(tmp_path, monkeypatch):
    from bazaar_agent.agents import maker as maker_module

    assert sell_floor(35.0, Guardrails()) == 35 and sell_floor(35.0, Guardrails(sell_min_value_ratio=2.0)) == 70
    assert sell_floor(1.2, Guardrails()) == 2
    cheap = Target("ask", "LAT-09", "rare", 30, 5, 45.0, 99.0, "a target priced below what the copy is worth")
    monkeypatch.setattr(maker_module, "targets_from", lambda book: [cheap])
    team = NoAccept()
    m, lines = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert posted(team) == [] and any("ask 30 is below the sell floor 35" in line for line in lines)


def test_listing_and_open_offer_caps_hold(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team, live=True)
    m.on_tick(clock(offers_per_team_per_tick=1))
    assert len(posted(team)) == 1 and any("no new listing left this tick" in line for line in lines)

    full = NoAccept(offers=[bid(100 + i, f"MAL-0{i}", 1, thread=600 + i) for i in range(3)])  # the desk's
    m2, lines2 = maker(tmp_path / "b", full, live=True)
    m2.on_tick(clock(max_open_offers_per_team=3))
    assert posted(full) == []
    assert any("open offers (max_open_offers_per_team 3)" in line for line in lines2)


def test_listings_already_made_this_tick_by_another_process_count(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team, live=True)
    for _ in range(12):
        m.ledger.record("listing", TICK, 1.5, 1, "x")
    m.on_tick(clock())
    assert posted(team) == []


def test_plan_reprices_moved_targets_cancels_stale_ones_and_leaves_lapsing_offers():
    target = Target("ask", "LAT-09", "rare", 68, 5, 45.0, 42.0, "r")
    mine = [
        OpenOffer(1, "ask", "LAT-09", 80, "rastro", 5, 140, 90),  # moved 80 -> 68: reprice
        OpenOffer(2, "bid", "LAV-02", 9, "rastro", None, 140, 90),  # not a target any more: cancel
        OpenOffer(3, "ask", "LAT-09", 68, "rastro", 5, 140, 90),  # a second offer for the same copy: cancel
    ]
    actions = plan_offers([target], mine, TICK, MakerConfig(), Guardrails())
    assert [(a.kind, a.offer.id if a.offer else None) for a in actions] == [
        ("cancel", 2),
        ("cancel", 3),
        ("reprice", 1),
    ]
    lapsing = [OpenOffer(1, "ask", "LAT-09", 80, "rastro", 5, TICK, 90)]
    assert (
        plan_offers([target], lapsing, TICK, MakerConfig(), Guardrails()) == []
    )  # it expires now: repost once it is gone
    steady = [OpenOffer(1, "ask", "LAT-09", 69, "rastro", 5, 140, 90)]  # 1 P off 68: below the 5 % bar
    assert plan_offers([target], steady, TICK, MakerConfig(), Guardrails()) == []


def test_live_reprice_cancels_then_posts_and_a_cancelled_bid_refunds_its_spend(tmp_path):
    team = NoAccept(offers=[our_ask(1, 5, "LAT-09", 90), bid(2, "LAV-02", 9)])
    m, _ = maker(tmp_path, team, live=True)
    m.ledger.record("spend", TICK - 3, 1.45, 9, "LAV-02")  # the bid's cash, counted when it was posted
    m.on_tick(clock())
    assert team.sent[:2] == [("cancel", 2), ("cancel", 1)]
    assert ("list_offer", {"assets": [5]}, {"cash": 68}, "rastro") in team.sent
    assert m.ledger.spent_since(0) == 9 - 9 + 65  # refund, then the new LAV-09 bid


def test_moves_are_dropped_when_the_tick_budget_is_spent(tmp_path):
    team = NoAccept(offers=[bid(2, "LAV-02", 9)])
    m, lines = maker(tmp_path, team, live=True)
    m.on_tick(clock(next_tick_in=1.5))
    assert team.sent == []
    assert any(line.startswith(f"tick {TICK} maker: DROPPED: cancel bid 2") for line in lines)
    assert {r["status"] for r in rows(tmp_path) if r.get("chosen") is False and r.get("kind") != "post_bid"} <= {
        "expired",
        "rejected",
    }


def test_best_venue_weighs_activity_against_the_fee_and_never_picks_ours():
    venues = venues_from({"venues": [RASTRO, CHEAP, OURS]})
    assert best_venue(venues, "t01", 20).id == "rastro"  # 41 × (1 - 2/20) beats an idle free venue
    busy_cheap = venues_from({"venues": [RASTRO, {**CHEAP, "trades": 60}, OURS]})
    assert best_venue(busy_cheap, "t01", 20).id == "v02"
    assert best_venue(venues_from({"venues": [OURS]}), "t01", 20) is None  # self_venue


def test_best_venue_skips_an_avoided_owner_and_never_avoids_the_house():
    busy_cheap = venues_from({"venues": [RASTRO, {**CHEAP, "trades": 60}, OURS]})
    assert best_venue(busy_cheap, "t01", 20, avoid={"t12"}).id == "rastro"  # t12's busy free venue: a rival's
    assert best_venue(busy_cheap, "t01", 20, avoid=frozenset()).id == "v02"  # nothing avoided: today's choice
    assert best_venue(venues_from({"venues": [RASTRO]}), "t01", 20, avoid={"world"}).id == "rastro"  # the house
    assert best_venue(venues_from({"venues": [{**CHEAP, "trades": 60}]}), "t01", 20, avoid={"t12"}) is None


def test_a_maker_with_no_venue_posts_nothing(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team, FakePublic(venues=(OURS,)), live=True)
    m.on_tick(clock())
    assert posted(team) == [] and any("no venue we may trade on" in line for line in lines)


def test_a_cancelled_bid_refunds_its_spend_in_the_hour_it_was_spent(tmp_path):
    # A bid of 150 posted at h3.0 (tick 70), cancelled at h3.5 (tick 100), then 150 of real buys at h3.9. At
    # h4.05 the last game hour holds those 150: a refund booked at cancel time (h3.5) would outlive its spend
    # (h3.0) inside the window, sum the hour to 0 and let another 150 through.
    team = NoAccept(offers=[bid(2, "LAV-02", 150, created=TICK - 30)])
    m, _ = maker(tmp_path, team, live=True)
    m.ledger.record("spend", TICK - 30, 3.0, 150, "LAV-02")  # the bid's cash, counted when it was posted
    m.on_tick(Clock(tick=TICK, tick_seconds=60.0, next_tick_in=40.0, t_hours=3.5))
    assert ("cancel", 2) in team.sent
    m.ledger.record("spend", TICK + 24, 3.9, 150, "LAV-08")
    assert m.ledger.spent_since(4.05 - 1.0) == 150 + 65  # the 150 bought at h3.9 and the new LAV-09 bid


def test_a_reprice_whose_repost_would_be_denied_keeps_the_offer(tmp_path):
    # Our LAV-09 bid stands at 50; the target moved to 65. Cash 330: the old bid leaves 280, the new one would
    # leave 265 < 270. Cancelling first would leave the card with no bid for a tick and nothing posted.
    team = NoAccept(me={**ME, "cash": 330}, offers=[bid(2, "LAV-09", 50)])
    m, lines = maker(tmp_path, team, live=True)
    m.ledger.record("spend", TICK - 10, 1.4, 50, "LAV-09")
    m.on_tick(clock())
    assert ("cancel", 2) not in team.sent and not [p for p in posted(team) if p[1].get("cash")]
    assert any("keep LAV-09 at 50: denied: cash 330 - 65 < cash_floor 270" in line for line in lines)
    assert m.ledger.spent_since(0) == 50  # no refund: the bid is still open


def test_an_ask_below_its_floor_is_cancelled_even_with_no_listing_left(tmp_path):
    # LAT-09 now costs us 45 to sell (its floor); our ask at 40 could fill below it. Every listing of the
    # tick is used, so it cannot be repriced, but a cancel needs no listing slot.
    team = NoAccept(offers=[our_ask(1, 5, "LAT-09", 40)])
    m, lines = maker(tmp_path, team, live=True)
    for _ in range(12):
        m.ledger.record("listing", TICK, 1.5, 1, "x")
    m.on_tick(clock())
    assert team.sent == [("cancel", 1)]
    assert any("ask 40 is below its floor 45; not repriced: no listing left" in line for line in lines)
    # And an ask below its floor is repriced even when its target moved less than reprice_min_change.
    near = Target("ask", "LAT-09", "rare", 46, 5, 45.5, 42.0, "r")
    (action,) = plan_offers(
        [near], [OpenOffer(1, "ask", "LAT-09", 45, "rastro", 5, 140, 90)], TICK, MakerConfig(), Guardrails()
    )
    assert action.kind == "reprice"


def test_a_bid_post_lost_to_a_network_error_is_still_booked_as_spend(tmp_path):
    from bazaar_agent.sdk import BazaarError

    class Flaky(NoAccept):
        def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
            self.sent.append(("list_offer", give, want, venue))
            raise BazaarError("network", "POST /api/offers: connection reset", 0)

    team = Flaky()
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert ("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro") in team.sent
    assert m.ledger.spent_since(0) == 65  # the bid may be open: the hourly cap counts it


def test_an_asset_without_your_value_is_never_listed(tmp_path):
    me = {**ME, "assets": [{**a, "your_value": None} if a.get("id") == 5 else a for a in ME["assets"]]}
    team = NoAccept(me=me)
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert not [p for p in posted(team) if 5 in p[1].get("assets", [])]


def test_best_venue_weighs_a_team_venue_down_without_ruling_it_out():
    # El Rastro: 41 x (1 - 2/20) = 36.9; a free team venue with 60 trades: 61, or 30.5 at a 0.5 penalty
    busy_cheap = venues_from({"venues": [RASTRO, {**CHEAP, "trades": 60}, OURS]})
    assert best_venue(busy_cheap, "t01", 20).id == "v02"  # 0: activity and fee only (today)
    assert best_venue(busy_cheap, "t01", 20, team_penalty=0.5).id == "rastro"  # its owner would score our trade
    much_busier = venues_from({"venues": [RASTRO, {**CHEAP, "trades": 120}, OURS]})
    assert best_venue(much_busier, "t01", 20, team_penalty=0.5).id == "v02"  # weighed, not forbidden: 60.5 > 36.9
    assert best_venue(venues_from({"venues": [{**CHEAP, "trades": 60}]}), "t01", 20, team_penalty=1.0).id == "v02"
