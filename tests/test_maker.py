"""The maker: sell floor, cash floor with open bids, listing/offer caps, reprice/cancel, tick budget, no accepts."""

from bazaar_agent.agents.maker import Maker, MakerConfig, Target, plan_offers, sell_floor, targets_from
from bazaar_agent.agents.market import OpenOffer, best_venue, venues_from
from bazaar_agent.guardrails import Guardrails
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
    actions = plan_offers([target], mine, TICK, MakerConfig())
    assert [(a.kind, a.offer.id if a.offer else None) for a in actions] == [
        ("cancel", 2),
        ("cancel", 3),
        ("reprice", 1),
    ]
    lapsing = [OpenOffer(1, "ask", "LAT-09", 80, "rastro", 5, TICK, 90)]
    assert plan_offers([target], lapsing, TICK, MakerConfig()) == []  # it expires now: repost once it is gone
    steady = [OpenOffer(1, "ask", "LAT-09", 69, "rastro", 5, 140, 90)]  # 1 P off 68: below the 5 % bar
    assert plan_offers([target], steady, TICK, MakerConfig()) == []


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


def test_a_maker_with_no_venue_posts_nothing(tmp_path):
    team = NoAccept()
    m, lines = maker(tmp_path, team, FakePublic(venues=(OURS,)), live=True)
    m.on_tick(clock())
    assert posted(team) == [] and any("no venue we may trade on" in line for line in lines)


def test_an_asset_without_your_value_is_never_listed(tmp_path):
    me = {**ME, "assets": [{**a, "your_value": None} if a.get("id") == 5 else a for a in ME["assets"]]}
    team = NoAccept(me=me)
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert not [p for p in posted(team) if 5 in p[1].get("assets", [])]
