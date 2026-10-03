"""The simulator's rules (RULES.md) on a hand-turned clock: settlement, per-tick caps, refusals, levels."""

from dataclasses import replace

import pytest

from bazaar_sim import broker, catalog, duels, market, scoring, threads, views
from bazaar_sim.errors import SimError
from bazaar_sim.models import DealRecord, WorldState
from bazaar_sim.world import LIMITS, World
from tests.simkit import QUIET, manual_world

US, THEM = "t01", "t02"


def refused(code: str, fn, *args):
    with pytest.raises(SimError) as e:
        fn(*args)
    assert e.value.code == code, e.value.body()
    return e.value


def dealer_offer(w: World, tid: int) -> dict:
    view = views.thread_view(w, w.state.threads[tid])
    return [o for o in view["standing_offers"] if o["maker"] == "abuela" and o["status"] == "open"][-1]


def a_card(w: World, team: str) -> int:
    return next(a.id for a in w.holdings(team) if a.kind == "card")


# ---------------------------------------------------------------- settlement and the clock


def test_an_accepted_offer_settles_on_the_next_tick_all_at_once():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "LAV-03"}}})
    m.step()
    offer = dealer_offer(m.world, th.id)
    assert offer["want"]["cash"] == 12 and offer["give"]["types"] == ["card:LAV-03"]
    cash, held = m.world.team(US).cash, m.world.held_counts(US)["LAV-03"]
    out = market.accept(m.world, US, offer["id"], {})
    assert out["settles_tick"] == m.world.tick + 1
    assert m.world.team(US).cash == cash  # nothing moves inside the tick
    m.step()
    assert m.world.team(US).cash == cash - 12
    assert m.world.held_counts(US)["LAV-03"] == held + 1
    assert m.world.state.threads[th.id].status == "deal"
    settlement = [e for e in m.world.state.events if e.type == "settlement"][-1].payload
    assert settlement["parties"] == ["abuela", US] and settlement["price"] == 12 and settlement["persona"] == "abuela"


def test_abuela_answers_on_the_next_tick_and_accepts_a_bid_at_her_floor():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "SAL-03"}}})
    floor = th.neg.limit
    m.step()
    threads.say(m.world, US, th.id, {"text": "hola", "price": floor})
    assert m.world.state.threads[th.id].messages[-1].sender == US
    m.step()
    last = m.world.state.threads[th.id].messages[-1]
    assert last.sender == "abuela" and last.offer is None  # "Deal!": she took OUR offer
    assert m.world.state.threads[th.id].status == "deal"  # and it settled in that same tick (real feed)
    settlement = [e for e in m.world.state.events if e.type == "settlement"][-1]
    message = [e for e in m.world.state.events if e.type == "thread.message"][-1]
    assert settlement.tick == message.tick == m.world.tick and settlement.id > message.id
    deal = m.world.team(US).deals[-1]
    assert deal.price == floor and deal.negotiated and deal.share == 1.0


def test_one_accept_per_team_per_tick_is_a_429_with_next_tick():
    m = manual_world()
    w = m.world
    a, b = (
        market.offer_from_input(w, THEM, {"give": {"assets": [x]}, "want": {"cash": 5}})
        for x in [a.id for a in w.holdings(THEM) if a.kind == "card"][:2]
    )
    market.accept(w, US, a.id, {})
    err = refused("wait_for_tick", market.accept, w, US, b.id, {})
    assert err.status == 429 and err.extra["next_tick"] == w.tick + 1
    m.step()
    market.accept(w, US, b.id, {})


def test_one_message_per_conversation_per_tick_and_a_refusal_costs_nothing():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "LAV-01"}}})
    refused("insufficient_cash", threads.say, m.world, US, th.id, {"price": 5_000})
    threads.say(m.world, US, th.id, {"text": "hola", "price": 5})  # the refused one did not use the slot
    err = refused("wait_for_tick", threads.say, m.world, US, th.id, {"price": 6})
    assert err.extra["next_tick"] == m.world.tick + 1
    m.step()
    threads.say(m.world, US, th.id, {"price": 6})


def test_twelve_new_listings_per_tick_and_a_cancelled_one_still_counts():
    m = manual_world()
    card = a_card(m.world, US)
    for _ in range(12):
        offer = market.offer_from_input(m.world, US, {"give": {"assets": [card]}, "want": {"cash": 50}})
        market.cancel(m.world, US, offer.id)
    refused("wait_for_tick", market.offer_from_input, m.world, US, {"give": {"assets": [card]}, "want": {"cash": 50}})


def test_thirty_open_offers_at_most():
    m = manual_world(limits={**LIMITS, "offers_per_team_per_tick": 100})
    card = a_card(m.world, US)
    for _ in range(30):
        market.offer_from_input(m.world, US, {"give": {"assets": [card]}, "want": {"cash": 50}})
    refused("too_many_offers", market.offer_from_input, m.world, US, {"give": {"assets": [card]}, "want": {"cash": 50}})


def test_one_conversation_per_dealer_and_six_in_all():
    m = manual_world()
    threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
    refused("thread_exists", threads.open_thread, m.world, US, {"with": "abuela", "topic": {"buy": {"card": "LAV-01"}}})
    for other in ("t02", "t03", "t04", "t05", "t06"):
        threads.open_thread(m.world, US, {"with": other})
    refused("too_many_threads", threads.open_thread, m.world, US, {"with": "t07"})


def test_offer_refusals_insufficient_cash_not_owner_and_bad_shapes():
    m = manual_world()
    w = m.world
    refused("insufficient_cash", market.offer_from_input, w, US, {"give": {"cash": 401}, "want": {"cards": ["LAV-09"]}})
    refused("not_owner", market.offer_from_input, w, US, {"give": {"assets": [a_card(w, THEM)]}, "want": {"cash": 9}})
    refused("invalid", market.offer_from_input, w, US, {"give": {"cash": 5}, "want": {"cash": 5}})
    refused("invalid", market.offer_from_input, w, US, {"give": {"cash": 0}, "want": {"cards": ["LAV-09"]}})
    refused("invalid", market.offer_from_input, w, US, {"give": {"cash": 10_000_001}, "want": {"cards": ["LAV-09"]}})
    refused("invalid", market.offer_from_input, w, US, {"give": {"cash": 5}, "want": {"cards": ["NOPE"]}})


def test_you_cannot_trade_on_your_own_venue():
    m = manual_world()
    w = m.world
    w.team(US).unlocked.append("chato")
    vid = broker.open_venue(w, US, {"name": "Mercado Uno", "fee_bps": 100, "rules": {"mechanism": "board"}})["venue"]
    refused(
        "self_venue",
        market.offer_from_input,
        w,
        US,
        {"venue": vid, "give": {"assets": [a_card(w, US)]}, "want": {"cash": 9}},
    )
    theirs = market.offer_from_input(
        w, THEM, {"venue": vid, "give": {"assets": [a_card(w, THEM)]}, "want": {"cash": 9}}
    )
    refused("self_venue", market.accept, w, US, theirs.id, {})


def test_a_venue_needs_level_two_and_the_bond():
    m = manual_world()
    w = m.world
    refused("locked", broker.open_venue, w, US, {"name": "x"})
    w.team(US).unlocked.append("chato")
    w.team(US).cash = 100
    refused("insufficient_cash", broker.open_venue, w, US, {"name": "x"})
    w.team(US).cash = 400
    refused("invalid", broker.open_venue, w, US, {"name": "x", "fee_bps": 1001})
    opened = broker.open_venue(w, US, {"name": "x"})
    assert opened["broker_key"].startswith("simbk-") and w.team(US).cash == 400 - 270
    assert w.state.venues[opened["venue"]].broker_key_hash != opened["broker_key"]  # only the digest is kept


def test_persona_quota_per_game_hour():
    m = manual_world()
    team = m.world.team(US)
    team.deals = [DealRecord(dealer="abuela", item="LAV-01", price=8, tick=0, hour=0, share=0.5, negotiated=True)] * 8
    refused("persona_quota", threads.open_thread, m.world, US, {"with": "abuela", "topic": {"buy": {"card": "LAV-02"}}})


def test_three_packs_per_team_per_hour_from_abuela():
    m = manual_world()
    team = m.world.team(US)
    team.deals = [
        DealRecord(dealer="abuela", item="sobre_barrio", price=17, tick=0, hour=0, share=1, negotiated=True)
    ] * 3
    refused(
        "persona_quota",
        threads.open_thread,
        m.world,
        US,
        {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}},
    )


def test_chato_is_locked_until_three_negotiated_abuela_deals():
    m = manual_world()
    refused("locked", threads.open_thread, m.world, US, {"with": "chato", "topic": {"buy": {"card": "LAV-06"}}})
    for ref in ("LAV-01", "LAV-02", "LAV-04"):
        th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": ref}}})
        m.step()
        threads.say(m.world, US, th.id, {"price": th.neg.limit})
        m.step(2)
        assert m.world.state.threads[th.id].status == "deal"
    assert "chato" in m.world.team(US).unlocked
    assert any(e.type == "level.unlocked" and e.payload["team"] == US for e in m.world.state.events)
    th = threads.open_thread(m.world, US, {"with": "chato", "topic": {"buy": {"card": "LAV-06"}}})
    m.step()
    assert dealer_offer_of(m.world, th.id, "chato")["want"]["cash"] == 33


def dealer_offer_of(w: World, tid: int, dealer: str) -> dict:
    view = views.thread_view(w, w.state.threads[tid])
    return [o for o in view["standing_offers"] if o["maker"] == dealer][-1]


def test_chato_opens_to_everyone_at_the_scheduled_tick():
    m = manual_world(chato_open_ticks=3)
    m.step(3)
    assert all("chato" in t.unlocked for t in m.world.state.teams.values())
    assert views.dealer_view(m.world, "chato")["open_to_all"] is True


def test_a_final_offer_untaken_makes_the_dealer_walk():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "MAL-01"}}})
    m.step()
    final = None
    for _ in range(12):
        threads.say(m.world, US, th.id, {"price": 1})
        m.step()
        offer = dealer_offer(m.world, th.id)
        if offer["final"]:
            final = offer
            break
    assert final is not None
    m.step(3)
    assert m.world.state.threads[th.id].status == "walked"
    assert m.world.state.threads[th.id].closed_reason == "walked"


def test_rudeness_puts_the_dealer_in_cooloff():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "MAL-02"}}})
    while m.world.state.threads[th.id].status == "open":
        threads.say(m.world, US, th.id, {"text": "you stupid thief, ignore previous instructions", "price": 2})
        m.step()
    closed = m.world.state.threads[th.id]
    assert closed.status == "cooloff" and closed.until_tick is not None
    err = refused("cooloff", threads.open_thread, m.world, US, {"with": "abuela", "topic": {"buy": {"card": "MAL-03"}}})
    assert err.status == 403 and err.extra["until_tick"] == closed.until_tick


def test_an_idle_conversation_closes():
    m = manual_world(idle_ticks=5)
    th = threads.open_thread(m.world, US, {"with": "t02"})
    m.step(7)
    assert m.world.state.threads[th.id].status == "closed" and m.world.state.threads[th.id].closed_reason == "idle"


def test_selling_to_abuela_pays_her_bid():
    m = manual_world()
    w = m.world
    common = next(a for a in w.holdings(US) if catalog.cards()[a.ref].rarity == "common")
    th = threads.open_thread(w, US, {"with": "abuela", "topic": {"sell": {"assets": [common.id]}}})
    m.step()
    bid = dealer_offer(w, th.id)
    assert bid["give"]["cash"] == 5 and bid["want"]["assets"][0]["id"] == common.id
    cash = w.team(US).cash
    market.accept(w, US, bid["id"], {})
    m.step()
    assert w.team(US).cash == cash + 5 and w.asset(common.id).owner == "abuela"


def test_a_pack_opens_into_its_slots_with_luck():
    m = manual_world()
    w = m.world
    th = threads.open_thread(w, US, {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
    m.step()
    market.accept(w, US, dealer_offer(w, th.id)["id"], {})
    m.step()
    pack = next(a for a in w.holdings(US) if a.kind == "pack")
    opened = market.open_pack(w, US, pack.id)
    assert len(opened["cards"]) == 3 and all("your_value" in c for c in opened["cards"])
    assert isinstance(opened["luck"], float)
    refused("not_owner", market.open_pack, w, US, pack.id)
    assert any(e.type == "pack.opened" for e in w.state.events)


def test_print_runs_are_fixed():
    m = manual_world()
    w = m.world
    w.state.minted["LAV-09"] = 30
    assert not w.mintable("LAV-09")
    refused("sold_out", w.mint, "LAV-09", US, "test")


# ---------------------------------------------------------------- private values and the album


def test_your_value_is_book_times_affinity_times_the_copy_marginal():
    m = manual_world()
    w = m.world
    team = w.team(US)
    counts = w.held_counts(US)
    me = scoring.me_view(w, US)
    for asset in me["assets"]:
        card = catalog.cards()[asset["ref"]]
        n = counts[asset["ref"]]
        expected = round(card.book * team.affinity[card.set_code] * catalog.marginals()[min(n - 1, 2)], 2)
        assert asset["your_value"] == (expected if n <= 3 else 0.0)
    ref = "LAV-09"
    one_more = scoring.value_view(w, US, ref)
    assert one_more["your_value"] == round(70 * team.affinity["LAV"] * catalog.marginal(counts.get(ref, 0)), 2)
    assert sorted(team.affinity.values()) == sorted(catalog.AFFINITY_VALUES)
    assert me["album"]["slots"] == 40 and len(me["album"]["pages"]) == 4


def test_every_team_starts_with_400_primas_and_11_3_1_cards():
    m = manual_world(rivals=3)
    for team in m.world.state.teams.values():
        rarities = [catalog.cards()[a.ref].rarity for a in m.world.holdings(team.id)]
        assert team.cash == 400
        assert (rarities.count("common"), rarities.count("uncommon"), rarities.count("rare")) == (11, 3, 1)


# ---------------------------------------------------------------- venues, brokers, the bench


def _venue(w: World, mechanism: str) -> tuple[str, str]:
    w.team(US).unlocked.append("chato")
    opened = broker.open_venue(
        w, US, {"name": "Uno", "fee_bps": 200, "fee_per_card": 1, "rules": {"mechanism": mechanism}}
    )
    return opened["venue"], opened["broker_key"]


def test_a_broker_pairs_a_crossing_bid_and_ask_and_the_owner_earns_the_fee():
    m = manual_world()
    w = m.world
    vid, key = _venue(w, "board")
    asset = next(a for a in w.holdings(THEM) if a.kind == "card")
    ask = market.offer_from_input(w, THEM, {"venue": vid, "give": {"assets": [asset.id]}, "want": {"cash": 20}})
    bid = market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 30}, "want": {"cards": [asset.ref]}})
    venue = broker.venue_for_broker(w, key)
    assert venue is not None and broker.book(w, venue)["offers"][0]["maker"].startswith("m")  # pseudonyms
    with pytest.raises(SimError):
        broker.match(w, venue, {"sell": ask.id, "buy": bid.id, "price": 29})  # 29 + fee 2 > 30
    broker.match(w, venue, {"sell": ask.id, "buy": bid.id, "price": 25})
    owner_cash = w.team(US).cash
    m.step()
    assert w.asset(asset.id).owner == "t03"
    assert w.team(US).cash == owner_cash + market.venue_fee(w, vid, 25, 1) == owner_cash + 1  # 2 % of 25 rounds to 0
    assert w.state.venues[vid].trades == 1


def test_an_auto_venue_crosses_its_book_every_tick():
    m = manual_world()
    w = m.world
    vid, _ = _venue(w, "auto")
    asset = next(a for a in w.holdings(THEM) if a.kind == "card")
    market.offer_from_input(w, THEM, {"venue": vid, "give": {"assets": [asset.id]}, "want": {"cash": 10}})
    market.offer_from_input(w, "t03", {"venue": vid, "give": {"cash": 15}, "want": {"cards": [asset.ref]}})
    m.step(2)
    assert w.asset(asset.id).owner == "t03"


def test_the_market_test_scores_the_share_of_possible_gains():
    m = manual_world(bench_first_tick=1, bench_ticks=3)
    w = m.world
    vid, key = _venue(w, "board")
    m.step()
    venue = broker.venue_for_broker(w, key)
    assert venue is not None
    bench = broker.book(w, venue)["bench_offers"]
    assert len(bench) == broker.BENCH_TRADERS
    run = w.state.bench[-1]
    sells = sorted((t for t in run.traders if t.side == "sell"), key=lambda t: t.quote)
    buys = sorted((t for t in run.traders if t.side == "buy"), key=lambda t: -t.quote)
    fee = lambda p: round(p * 200 / 10_000) + 1  # noqa: E731
    if sells[0].quote + fee(sells[0].quote) <= buys[0].quote:
        broker.match(w, venue, {"sell": sells[0].id, "buy": buys[0].id, "price": sells[0].quote})
    m.step(4)
    team = w.team(US)
    assert team.bench_efficiency is not None and 0 <= team.bench_efficiency <= 1
    assert any(e.type == "bench.finished" for e in w.state.events)


def test_a_fee_change_waits_for_its_notice_and_a_closed_venue_returns_the_bond():
    m = manual_world()
    w = m.world
    vid, _ = _venue(w, "board")
    broker.set_fee(w, US, vid, {"fee_bps": 50})
    assert w.state.venues[vid].fee_bps == 200
    m.step(2)
    assert w.state.venues[vid].fee_bps == 50
    cash = w.team(US).cash
    broker.close_venue(w, US, vid)
    m.step(broker.CLOSE_COOLDOWN_TICKS)
    assert w.state.venues[vid].status == "closed" and w.team(US).cash == cash + broker.BOND


# ---------------------------------------------------------------- duels


def test_duels_have_the_real_payload_shape_and_end_in_a_deal():
    m = manual_world(duel_first_tick=1, duel_ticks=12)
    w = m.world
    m.step()
    mine = duels.duels_view(w, US, False)["duels"]
    assert {d["role"] for d in mine} == {"seller", "buyer"}
    keys = {
        "duel",
        "session",
        "status",
        "role",
        "item",
        "issues",
        "your_days_weight",
        "days_meaning",
        "your_limit",
        "limit_meaning",
        "rival",
        "deadline_tick",
        "decay_per_round",
        "rounds",
        "your_offer",
        "rival_offer",
        "messages",
        "result",
        "price",
        "days",
    }
    assert set(mine[0]) == keys
    seller = next(d for d in mine if d["role"] == "seller")
    assert seller["rival_offer"] is not None  # the rival opens
    duels.say(w, US, seller["duel"], {"text": "hola", "price": seller["your_limit"] + 1})
    m.step(2)
    done = next(d for d in duels.duels_view(w, US, True)["duels"] if d["duel"] == seller["duel"])
    assert done["status"] == "deal" and done["price"] == seller["your_limit"] + 1
    assert done["result"]["practice"] is True and done["result"]["points"] == 0.0


def test_two_issue_sessions_refuse_a_price_without_days_and_score_a_deal():
    m = manual_world(duel_first_tick=1, duel_every_ticks=20, duel_ticks=12)
    w = m.world
    m.step(21)  # session 2: price and days
    live = duels.duels_view(w, US, False)["duels"]
    assert live and all(d["issues"] == ["price", "days"] for d in live)
    buyer = next(d for d in live if d["role"] == "buyer")
    refused("missing_days", duels.say, w, US, buyer["duel"], {"price": 10})
    duels.accept(w, US, buyer["duel"])
    m.step()
    done = next(d for d in duels.duels_view(w, US, True)["duels"] if d["duel"] == buyer["duel"])
    assert done["status"] == "deal" and done["result"]["practice"] is False
    assert w.team(US).duel_points == done["result"]["points"]


def test_duel_accepts_share_the_teams_one_accept_per_tick(monkeypatch):
    # RULES.md "Per tick": the team may accept ONE offer per tick, duels included (#151 review P2: the live sim
    # used to allow one accept per duel, so six duels on one deadline could all be accepted in one tick).
    monkeypatch.setenv("SIM_DUEL_PAIRS", "2")
    m = manual_world(duel_first_tick=1, duel_ticks=12)
    w = m.world
    m.step(2)
    open_offers = [d for d in duels.duels_view(w, US, False)["duels"] if d["rival_offer"] is not None]
    assert len(open_offers) >= 2
    duels.accept(w, US, open_offers[0]["duel"])
    err = refused("wait_for_tick", duels.accept, w, US, open_offers[1]["duel"])
    assert err.status == 429 and err.extra["next_tick"] == w.tick + 1
    card = [a.id for a in w.holdings(THEM) if a.kind == "card"][0]
    offer = market.offer_from_input(w, THEM, {"give": {"assets": [card]}, "want": {"cash": 5}})
    refused("wait_for_tick", market.accept, w, US, offer.id, {})  # the market shares the same slot
    m.step()
    later = [d for d in duels.duels_view(w, US, False)["duels"] if d["rival_offer"] is not None]
    if later:
        duels.accept(w, US, later[0]["duel"])  # a new tick, a new slot


def test_a_duel_without_a_deal_closes_at_its_deadline():
    m = manual_world(duel_first_tick=1, duel_ticks=4)
    m.step(6)
    assert all(d["status"] == "no_deal" for d in duels.duels_view(m.world, US, True)["duels"])


# ---------------------------------------------------------------- rivals, feed, persistence


def test_a_rival_buys_a_fairly_priced_duplicate_it_is_missing():
    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    rivals = [t for t in w.state.teams.values() if t.bot]
    ours = w.held_counts(US)
    best = max(
        (a for a in w.holdings(US) if a.kind == "card" and ours[a.ref] > 1),
        key=lambda a: sum(1 for r in rivals if w.held_counts(r.id).get(a.ref, 0) == 0),
    )
    market.offer_from_input(w, US, {"give": {"assets": [best.id]}, "want": {"cash": catalog.cards()[best.ref].book}})
    w.config = replace(w.config, rivals_enabled=True)
    m.step(3)
    assert w.asset(best.id).owner in {r.id for r in rivals}


def test_the_feed_is_public_only_and_capped_at_500():
    m = manual_world(duel_first_tick=1)
    m.step(3)
    for i in range(600):
        m.world.emit("announcement", {"text": f"n{i}"})
    events = views.feed_view(m.world, 1000)["events"]
    assert len(events) == 500 and all(e["scope"] == "public" for e in events)
    assert any(e.scope.startswith("team:") for e in m.world.state.events)
    assert events == sorted(events, key=lambda e: e["id"])


def test_the_whole_world_snapshots_to_json_and_back():
    m = manual_world(duel_first_tick=1)
    threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
    m.step(3)
    raw = m.world.state.model_dump_json()
    again = WorldState.model_validate_json(raw)
    assert again == m.world.state
    clone = World(again, m.world.config, now=m.clock)
    assert scoring.me_view(clone, US) == scoring.me_view(m.world, US)


def test_the_same_seed_gives_the_same_world():
    a, b = manual_world(rivals=4), manual_world(rivals=4)
    a.step(5)
    b.step(5)
    assert a.world.state.model_dump_json() == b.world.state.model_dump_json()


def test_no_new_bid_while_a_deal_settles_and_old_history_is_pruned():
    m = manual_world()
    th = threads.open_thread(m.world, US, {"with": "abuela", "topic": {"buy": {"card": "LAT-04"}}})
    m.step()
    market.accept(m.world, US, dealer_offer(m.world, th.id)["id"], {})
    refused("deal_pending", threads.say, m.world, US, th.id, {"price": 3})
    m.step()
    assert m.world.state.threads[th.id].status == "deal"
    m.clock.now += 1
    m.world.state.clock.tick += 700
    m.world.advance()
    assert th.id not in m.world.state.threads
    assert all(o.thread != th.id for o in m.world.state.offers.values())


def test_a_rival_answers_a_team_thread_with_a_counter_we_can_accept():
    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    rival = next(t for t in w.state.teams.values() if t.bot)
    theirs = w.held_counts(rival.id)
    ours = w.held_counts(US)
    asset = next(
        a
        for a in w.holdings(US)
        if a.kind == "card" and theirs.get(a.ref, 0) == 0 and catalog.one_more_value(a.ref, 0, rival.affinity) >= 8
    )
    assert ours[asset.ref] >= 1
    th = threads.open_thread(w, US, {"with": rival.id})
    threads.say(
        w, US, th.id, {"text": "a card for you", "offer": {"give": {"assets": [asset.id]}, "want": {"cash": 300}}}
    )
    m.step()
    reply = w.state.threads[th.id].messages[-1]
    assert reply.sender == rival.id and reply.offer is not None
    counter = w.state.offers[reply.offer]
    assert counter.to == US and counter.want.assets == [asset.id] and 0 < counter.give.cash < 300
    market.accept(w, US, counter.id, {})
    m.step()
    assert w.asset(asset.id).owner == rival.id and w.state.threads[th.id].status == "deal"


def test_a_mixed_bid_never_matches_a_single_card_ask():
    m = manual_world()
    w = m.world
    vid, key = _venue(w, "board")
    asset = next(a for a in w.holdings(THEM) if a.kind == "card")
    extra = next(a for a in w.holdings("t04") if a.kind == "card")
    ask = market.offer_from_input(w, THEM, {"venue": vid, "give": {"assets": [asset.id]}, "want": {"cash": 10}})
    mixed = market.offer_from_input(
        w, "t03", {"venue": vid, "give": {"cash": 30}, "want": {"cards": [asset.ref], "assets": [extra.id]}}
    )
    venue = broker.venue_for_broker(w, key)
    assert venue is not None
    refused("invalid", broker.match, w, venue, {"sell": ask.id, "buy": mixed.id, "price": 10})


def test_a_named_copy_already_promised_is_asset_locked():
    m = manual_world(limits={**LIMITS, "accepts_per_team_per_tick": 5})  # only the lock may refuse here
    w = m.world
    copy = a_card(w, US)
    ref = w.asset(copy).ref
    first = market.offer_from_input(w, THEM, {"give": {"cash": 5}, "want": {"cards": [ref]}})
    second = market.offer_from_input(w, "t03", {"give": {"cash": 5}, "want": {"cards": [ref]}})
    market.accept(w, US, first.id, {"assets": [copy]})
    refused("asset_locked", market.accept, w, US, second.id, {"assets": [copy]})


def test_a_pack_that_cannot_be_filled_grants_nothing():
    m = manual_world()
    w = m.world
    th = threads.open_thread(w, US, {"with": "abuela", "topic": {"buy": {"pack": "sobre_barrio"}}})
    m.step()
    market.accept(w, US, dealer_offer(w, th.id)["id"], {})
    m.step()
    pack = next(a for a in w.holdings(US) if a.kind == "pack")
    for card in catalog.cards().values():
        w.state.minted[card.ref] = card.print_run  # everything is out of print
    before = dict(w.state.minted), len(w.state.assets)
    refused("sold_out", market.open_pack, w, US, pack.id)
    assert (dict(w.state.minted), len(w.state.assets)) == before
    assert w.asset(pack.id).owner == US
