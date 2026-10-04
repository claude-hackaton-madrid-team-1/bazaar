"""Doña Pilar readiness (level 3, a collector, opens to everyone ~12:50 Madrid on 2026-10-03).

What our agents do when she appears, proven with fakes only (no network): her quote is picked up from
`/api/dealers` + `/me.unlocked` every tick, her gold pack (list 420) is never an actionable move under
`max_price_pack`, the taker opens no thread with her, and her kind "collector" breaks nothing that iterates
dealers. The simulator half (her style and her over-book bids for SAL/RET) is at the bottom.
"""

from copy import deepcopy

from bazaar_agent import strategy
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Guardrails, load_guardrails
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.store import LearningStore
from bazaar_agent.monitor import dealer_snapshots
from bazaar_sim import catalog as sim_catalog
from bazaar_sim import dealers as sim_dealers
from bazaar_sim import threads as sim_threads
from bazaar_sim import views as sim_views
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts, rows
from tests.simkit import manual_world
from tests.test_strategy import ABUELA, CATALOG, EVENTS, ME, PARAMS

# The real keyless /api/dealers entry, verified 2026-10-03.
PILAR = {
    "id": "pilar",
    "name": "Doña Pilar",
    "status": "active",
    "level": 3,
    "title": "collector from Salamanca, buys what completes her albums",
    "kind": "collector",
    "enabled": True,
    "traits": {
        "patience": 0.6,
        "generosity": 0.5,
        "shrewdness": 0.75,
        "memory": 0.7,
        "strictness": 0.6,
        "chattiness": 0.55,
    },
    "unlock": {
        "always": False,
        "early_deals_with": "chato",
        "early_min_deals": 3,
        "early_min_level": 2,
        "open_to_all_at": "+5.51h",
    },
    "open_to_all": False,
    "menu": {
        "sells": [
            {"pack": "sobre_oro", "name": "Gold pack", "list_price": 420, "opening_ask": 504, "per_team_per_hour": 1}
        ],
        "buys": [
            {"rarity": "uncommon", "sets": ["SAL", "RET"]},
            {"rarity": "rare", "sets": ["SAL", "RET"]},
            {"rarity": "epic", "sets": ["SAL", "RET"]},
            {"rarity": "uncommon", "sets": "released"},
            {"rarity": "rare", "sets": "released"},
            {"rarity": "epic", "sets": "released"},
        ],
        "deals_per_team_per_hour": 6,
    },
}
GOLD = {
    "id": "sobre_oro",
    "name": "Gold pack",
    "expected_book": 410.5,
    "slots": [{"uncommon": 1.0}, {"uncommon": 1.0}, {"rare": 1.0}, {"rare": 1.0}, {"epic": 0.85, "legendary": 0.15}],
}
CATALOG_GOLD = {**deepcopy(CATALOG), "packs": [*deepcopy(CATALOG["packs"]), GOLD]}
DEALERS = [ABUELA, PILAR]
UNLOCKED = {**deepcopy(ME), "unlocked": ["abuela", "pilar"]}
SAL_RARE, LAV_RARE = "SAL-09", "LAV-09"  # simulator catalog refs, both rare, book 70


def yes_judge(state):
    return "yes", 0.99  # Jev says yes to every pack: only the strategy and the guardrails can stop a gold pack


def taker(tmp_path, team, dealers, rules=None):
    kw = {**parts(tmp_path), "feed": MarketFeed(lambda n: [])}
    if rules is not None:
        kw["rules"] = rules
    t = Taker(
        team,
        FakePublic(dealers=dealers, events=[], catalog=CATALOG_GOLD),
        live=False,  # dry run
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        **kw,
    )
    t.pack_judge = yes_judge
    return t


# ---------------------------------------------------------------- (a) her quote, only once unlocked


def test_pilar_quotes_only_when_me_unlocked_names_her():
    quotes, newest = strategy.dealer_quotes(DEALERS, ["abuela"])
    assert "pilar" not in {q.dealer for q in quotes} and newest == "abuela"
    quotes, newest = strategy.dealer_quotes(DEALERS, ["abuela", "pilar"])
    assert strategy.Quote("pilar", "sobre_oro", 420, None, 1) in quotes and newest == "pilar"
    assert [q for q in quotes if q.dealer == "pilar"] == [strategy.Quote("pilar", "sobre_oro", 420, None, 1)]


def test_build_market_follows_me_unlocked_without_a_restart():
    locked = strategy.build_market(ME, CATALOG_GOLD, EVENTS, DEALERS)
    opened = strategy.build_market(UNLOCKED, CATALOG_GOLD, EVENTS, DEALERS)
    assert {q.dealer for q in locked.quotes} == {"abuela"}
    assert {q.dealer for q in opened.quotes} == {"abuela", "pilar"}
    assert strategy.quote_for(opened, opened.cards["LAV-08"]).dealer == "abuela"  # she sells no single card


def test_the_taker_rereads_dealers_and_me_every_tick(tmp_path):
    """One process: tick 100 Pilar is locked, tick 101 /me unlocks her. A cheap (hypothetical) gold pack
    proves the second tick sees her; the real one at 420 is checked in (b)."""
    cheap = deepcopy(PILAR)
    cheap["menu"]["sells"][0].update(list_price=15, opening_ask=18)
    team = FakeTeam(me=deepcopy(ME))
    t = taker(tmp_path, team, [cheap], rules=Guardrails(max_price_pack=400))
    t.on_tick(clock(tick=100))
    assert not [r for r in rows(tmp_path) if "pilar" in str(r.get("move"))]
    team._me["unlocked"] = ["abuela", "pilar"]
    team.now = clock(tick=101)
    t.on_tick(team.now)
    opens = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open" and r["tick"] == 101]
    assert any("pilar" in str(r.get("move")) and "sobre_oro" in str(r.get("move")) for r in opens), opens


# ---------------------------------------------------------------- (b) the gold pack is never a move today


def test_todays_restock_ceiling_is_30_but_default_stays_conservative():
    assert load_guardrails().rules.max_price_pack == 30
    assert Guardrails().max_price_pack == 20


def test_the_gold_pack_is_never_actionable_under_max_price_pack():
    m = strategy.build_market(UNLOCKED, CATALOG_GOLD, EVENTS, DEALERS)
    (gold,) = [mv for mv in strategy.pack_moves(m, PARAMS, Guardrails()) if mv.ref == "sobre_oro"]
    assert gold.source == "pilar" and gold.command == "" and gold.ladder is None
    assert gold.price == 420  # her list price, no fills yet
    book = strategy.build_playbook(UNLOCKED, CATALOG_GOLD, EVENTS, DEALERS, PARAMS, Guardrails())
    assert not [mv for mv in book.packs if mv.source == "pilar" and (mv.command or mv.ladder)]
    assert not [mv for mv in book.buys if mv.source == "pilar"]  # she sells no cards


def test_the_taker_opens_no_thread_with_pilar_on_a_tick_she_is_unlocked(tmp_path):
    team = FakeTeam(me=deepcopy(UNLOCKED))
    t = taker(tmp_path, team, DEALERS, rules=Guardrails())
    t.on_tick(clock())
    assert not [s for s in team.sent if "pilar" in str(s)]
    assert not [r for r in rows(tmp_path) if r.get("kind") == "dealer_open" and "pilar" in str(r.get("move"))]


# ---------------------------------------------------------------- (c) kind "collector" breaks nothing


def test_a_collector_kind_is_just_another_dealer_for_learner_and_monitor():
    known = {str(d.get("id")): "dealer" for d in DEALERS if d.get("id")}  # what the taker hands the learner
    assert known == {"abuela": "dealer", "pilar": "dealer"}
    blocks = LiveLearner(LearningStore()).blocks(EVENTS, "t01", clock(), known)
    assert blocks.stops("pilar") is None and blocks.stops("pilar", "sobre_oro") is None
    snaps = dealer_snapshots({"dealers": DEALERS})
    assert snaps["pilar"].kind == "dealer" and snaps["pilar"].level == 3 and snaps["pilar"].unlock["early_min_level"]


def test_a_full_taker_tick_with_her_in_the_list_does_not_raise(tmp_path):
    team = FakeTeam(me=deepcopy(UNLOCKED))
    t = taker(tmp_path, team, [*DEALERS, {"id": "chato", "status": "active", "level": 2, "menu": {}}])
    t.on_tick(clock())
    t.on_tick(clock(tick=101))  # a second tick: the cooling, blocks and pack ledger paths again


# ---------------------------------------------------------------- the simulator's Pilar


def test_the_simulator_lists_her_like_the_real_api():
    sim = sim_catalog.raw_dealers()["pilar"]
    for field in ("id", "name", "kind", "level", "traits", "menu", "title"):
        assert sim[field] == PILAR[field], field
    assert sim["unlock"]["early_deals_with"] == "chato" and sim["unlock"]["early_min_deals"] == 3
    assert "pilar" in sim_dealers.STYLES


def test_simulated_pilar_is_locked_until_open_to_all_then_quotes_the_gold_pack():
    m = manual_world(pilar_open_ticks=3)
    w = m.world
    us = "t01"
    view = sim_views.dealer_view(w, "pilar")
    assert view["open_to_all"] is False and view["unlock"]["open_to_all_at"] > 0
    try:
        sim_threads.open_thread(w, us, {"with": "pilar", "topic": {"buy": {"pack": "sobre_oro"}}})
        raise AssertionError("pilar opened while locked")
    except Exception as e:  # SimError("locked")
        assert getattr(e, "code", None) == "locked", e
    m.step(3)
    assert "pilar" in w.team(us).unlocked and sim_views.dealer_view(w, "pilar")["open_to_all"] is True
    th = sim_threads.open_thread(w, us, {"with": "pilar", "topic": {"buy": {"pack": "sobre_oro"}}})
    assert th.neg is not None and th.neg.opening == 504 and 0.93 * 420 <= th.neg.limit <= 0.97 * 420 + 1


def test_simulated_pilar_pays_over_book_for_salamanca_and_under_book_for_the_rest():
    style = sim_dealers.STYLES["pilar"]
    assert sim_dealers.buy_share(style, "SAL", "rare") == 1.2
    assert sim_dealers.buy_share(style, "RET", "uncommon") == 1.2
    assert sim_dealers.buy_share(style, "LAV", "rare") == 0.9
    assert sim_dealers.buy_share(style, "SAL", "common") == 0.9  # loved sets only from uncommon up
    data = sim_catalog.raw_dealers()["pilar"]
    assert not sim_catalog.dealer_buys(data, "common", "SAL")
    assert sim_catalog.dealer_buys(data, "rare", "SAL") and sim_catalog.dealer_buys(data, "rare", "LAV")


def test_simulated_pilar_bids_book_x_1_2_for_a_salamanca_rare_and_opens_x_0_9_up_to_book_for_a_lavapies_one():
    m = manual_world(pilar_open_ticks=1)
    m.step(1)
    w, us = m.world, "t01"
    sal = w.mint(SAL_RARE, us, "test")
    th = sim_threads.open_thread(w, us, {"with": "pilar", "topic": {"sell": {"assets": [sal.id]}}})
    book = sim_catalog.cards()[SAL_RARE].book
    assert th.neg is not None and th.neg.side == "buy" and th.neg.limit == round(book * 1.2)
    w.state.threads[th.id].status = "closed"  # one open conversation per dealer
    lav = w.mint(LAV_RARE, us, "test")
    th2 = sim_threads.open_thread(w, us, {"with": "pilar", "topic": {"sell": {"assets": [lav.id]}}})
    lav_book = sim_catalog.cards()[LAV_RARE].book
    assert th2.neg is not None and th2.neg.opening == round(lav_book * 0.9) and th2.neg.limit == lav_book
