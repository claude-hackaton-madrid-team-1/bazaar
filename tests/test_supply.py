"""N14b part 2: the supply map (starting hands, the feed, packs opened), valuation reading it, the pack EV
with our album need, and the open-or-keep decision for sealed packs."""

from __future__ import annotations

from copy import deepcopy

import pytest

from bazaar_agent import guardrails as gr
from bazaar_agent import pack_open, strategy, supply
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.sdk import BazaarError
from bazaar_agent.supply_cli import scan_ids
from bazaar_agent.supply_db import SCAN_FILE, ScanStore, read_scan_file, write_scan_file
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts, rows
from tests.test_intel import settle
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, RULES


def scanned(aid, ref, owner="a team", moved=False):
    history = [{"from": "world", "tick": 0, "to": "a team", "why": "starting grant"}]
    if moved:
        history.append({"from": "a team", "tick": 9, "to": "a team", "why": "trade"})
    return {"id": aid, "kind": "card", "ref": ref, "owner": owner, "history": history}


def opened_pack(eid, team, pack="sobre_barrio"):
    return {"id": eid, "tick": 3, "type": "pack.opened", "payload": {"team": team, "pack": pack, "best": None}}


# LAV-09's only copy was dealt to t04 (asset 47 is in block 4) and never moved; LAT-09 #200 went to t13
# and was then sold to t14 (feed); t07 opened two packs.
SCAN = [scanned(47, "LAV-09"), scanned(200, "LAT-09", moved=True), scanned(5, "LAT-09", owner="t01")]
FEED = [
    *EVENTS,
    settle(30, 30, "t13", "t14", "LAT-09", 65, tick=9, kind="card", persona=None, asset_id=200),
    opened_pack(31, "t07"),
    opened_pack(32, "t07"),
]


# ---------------------------------------------------------------- the map


@pytest.mark.parametrize("aid, team", [(1, "t01"), (15, "t01"), (16, "t02"), (47, "t04"), (270, "t18")])
def test_starting_assets_come_in_blocks_of_fifteen_per_team(aid, team):
    assert supply.start_team(aid) == team


@pytest.mark.parametrize("aid", [0, 271, 5000])
def test_ids_outside_the_starting_270_have_no_starting_team(aid):
    assert supply.start_team(aid) is None


def test_an_anonymous_owner_is_the_starting_team_only_while_the_card_never_moved():
    assert supply.scan_holder(scanned(47, "LAV-09")) == "t04"
    assert supply.scan_holder(scanned(47, "LAV-09", moved=True)) is None
    assert supply.scan_holder(scanned(47, "LAV-09", owner="t09", moved=True)) == "t09"


def test_the_feed_moves_assets_and_me_is_the_truth_for_ours():
    me = deepcopy(ME)
    assets = supply.asset_map(SCAN, FEED, me)
    assert (assets[47].holder, assets[47].origin) == ("t04", "start")
    assert (assets[200].holder, assets[200].origin, assets[200].tick) == ("t14", "feed", 9)
    assert assets[5].holder == "t01"
    me["assets"] = [a for a in me["assets"] if a["id"] != 5]  # we sold LAT-09 #5: no longer ours
    assert supply.asset_map(SCAN, FEED, me)[5].holder is None


def test_cards_and_pages_supply():
    sm = supply.supply_map(CATALOG, ME, FEED, SCAN)
    lav9, lat9 = sm.cards["LAV-09"], sm.cards["LAT-09"]
    assert (lav9.minted, lav9.ours, lav9.holders, lav9.unplaced) == (1, 0, (("t04", 1),), 0)
    assert (lat9.ours, lat9.holders, lat9.unplaced) == (1, (("t14", 1),), 1)
    assert sm.sellers("LAT-09") == ("t14",) and sm.sellers("LAT-09", exclude=["t14"]) == ()
    lav = sm.sets["LAV"]
    assert (lav.released, lav.pages_possible, lav.bottleneck, lav.our_have) == (True, 0, ("LAV-10",), 2)
    assert sm.sets["RET"].released is False
    assert (sm.packs_opened, sm.scanned) == ({"t07": 2}, 3)


def test_a_rescan_shows_new_ids_and_cards_that_changed_hands():
    after = [scanned(47, "LAV-09", moved=True), scanned(200, "LAT-09", moved=True), scanned(301, "LAV-02")]
    assert supply.scan_diff(SCAN, after) == {"added": [301], "moved": [47]}


# ---------------------------------------------------------------- valuation reads it


def test_the_starting_hand_names_the_holder_of_a_rare_the_feed_never_showed():
    book = strategy.build_playbook(ME, CATALOG, FEED, DEALERS, PARAMS, RULES, SCAN)
    lav9 = next(mv for mv in book.buys if mv.ref == "LAV-09")
    assert "t04" in lav9.counterparties and "t04" in lav9.reason


def test_supply_scarcity_counts_only_the_copies_other_teams_would_sell():
    m = strategy.build_market(ME, CATALOG, FEED, DEALERS, SCAN)
    lat9 = m.cards["LAT-09"]
    assert "t14" in m.chasers["LAT"]  # t14 bought LAT-09 in the feed: it chases LAT
    assert strategy.for_sale(m, lat9) == 1  # 3 minted, 1 ours, t14 keeps its copy
    assert strategy.for_sale(strategy.replace(m, chasers={}), lat9) == 2
    on = PARAMS.model_copy(update={"supply_scarcity": True})
    assert strategy.buy_case(m, m.cards["LAV-08"], on).supply_note.startswith("10/90 minted, 10 for sale")
    assert strategy.buy_case(m, m.cards["LAV-08"], PARAMS).supply_note.startswith("10/90 minted")


def test_without_a_supply_map_every_copy_we_lack_is_for_sale():
    m = strategy.replace(strategy.build_market(ME, CATALOG, FEED, DEALERS), supply=None)
    assert strategy.for_sale(m, m.cards["LAT-09"]) == 2


def test_the_committed_strategy_counts_supply_and_keeps_fridays_pack_ev_for_buys():
    params = strategy.load_strategy().params  # B9 (#109): no Abuela packs on Saturday
    assert params.supply_scarcity and not params.pack_ev_album and not params.dealer_mints_unminted


# ---------------------------------------------------------------- packs


def test_pull_odds_skip_printed_out_cards_and_fall_back_a_rarity():
    m = strategy.build_market(ME, CATALOG, EVENTS, DEALERS)
    probs = strategy.pack_cards(m, m.packs["sobre_barrio"])
    assert set(probs) == {"LAV-01", "LAV-02", "LAT-03", "LAV-06", "LAV-08"}
    assert sum(probs.values()) == pytest.approx(3.0)
    out = deepcopy(CATALOG)
    for s in out["sets"]:
        for c in s["cards"]:
            if c["rarity"] == "uncommon":
                c["minted"] = c["print_run"]  # every uncommon printed out: that slot gives a common
    m2 = strategy.build_market(ME, out, EVENTS, DEALERS)
    assert set(strategy.pack_cards(m2, m2.packs["sobre_barrio"])) == {"LAV-01", "LAV-02", "LAT-03"}


def test_the_album_ev_counts_the_page_bonus_share_of_the_cards_we_lack():
    m = strategy.build_market(ME, CATALOG, EVENTS, DEALERS)
    slots = m.packs["sobre_barrio"]
    old, _ = strategy.pack_ev(m, slots, PARAMS)
    new, how = strategy.pack_ev(m, slots, PARAMS.model_copy(update={"pack_ev_album": True}))
    assert new > old and "new to our album" in how
    lav2 = m.cards["LAV-02"]
    assert strategy.keep_value(m, lav2, PARAMS) > strategy.copy_value(m, lav2, 0)  # + its bonus share


def test_a_pack_is_proposed_only_when_its_ev_beats_the_price():
    album = PARAMS.model_copy(update={"pack_ev_album": True})
    pack = next(mv for mv in strategy.build_playbook(ME, CATALOG, EVENTS, DEALERS, album, RULES).packs)
    assert pack.ref == "sobre_barrio" and pack.value - pack.price >= album.min_buy_surplus and pack.command
    dear = album.model_copy(update={"pack_price_estimate": 60})
    dear_pack = strategy.build_playbook(ME, CATALOG, EVENTS, DEALERS, dear, RULES).packs[0]
    assert dear_pack.command == ""


def test_at_most_three_packs_a_game_hour():
    ctx = gr.Context(cash=400, held={}, tick=10, t_hours=4.5, packs_last_hour=3)
    verdict = gr.check(gr.Action("buy", "sobre_barrio", "pack", 17), ctx, gr.load_guardrails().rules)
    assert not verdict.allowed and "max_packs_per_game_hour" in str(verdict)


def test_open_unless_a_team_paid_more_for_it_sealed():
    m = strategy.build_market(ME, CATALOG, EVENTS, DEALERS)
    sealed = pack_open.sealed_packs(ME)
    assert sealed == [pack_open.SealedPack(6, "sobre_barrio")]
    choice = pack_open.choose(m, sealed[0], PARAMS)
    assert choice.verdict == "open" and "no team ever bought one sealed" in choice.reason
    assert "new to our album" in choice.reason  # opened, its cards are valued card by card
    resold = [*EVENTS, settle(40, 40, "t03", "t09", "sobre_barrio", 90, tick=9, persona=None, asset_id=500)]
    m2 = strategy.build_market(ME, CATALOG, resold, DEALERS)
    kept = pack_open.choose(m2, sealed[0], PARAMS)
    assert (kept.verdict, kept.sealed_price) == ("keep", 90.0)
    assert pack_open.choose(m, pack_open.SealedPack(9, "sobre_misterio"), PARAMS).verdict == "keep"


class PackTeam(FakeTeam):
    def open_pack(self, asset_id):
        self.sent.append(("open_pack", asset_id))
        return {"cards": [{"ref": "RET-01"}, {"ref": "LAV-02"}], "luck": 1.2}


def opener(tmp_path, team, *, live, **rules):
    lines: list[str] = []
    kw = parts(tmp_path, **rules)
    kw["feed"] = MarketFeed(lambda n: deepcopy(EVENTS))
    t = Taker(
        team,
        FakePublic(),
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        **kw,
    )
    return t, lines


def test_with_the_switch_off_the_pack_stays_sealed_and_is_said_once(tmp_path):
    team = PackTeam()
    t, _ = opener(tmp_path, team, live=True)
    t.on_tick(clock(tick=100))
    t.on_tick(clock(tick=101))
    packs = [r for r in rows(tmp_path) if r["kind"] == "pack_open"]
    assert len(packs) == 1 and packs[0]["status"] == "rejected" and "open_sealed_packs = false" in packs[0]["guardrail"]
    assert not [s for s in team.sent if s[0] == "open_pack"]


def test_with_the_switch_on_one_pack_is_opened_live(tmp_path):
    team = PackTeam()
    t, lines = opener(tmp_path, team, live=True, open_sealed_packs=True)
    t.on_tick(clock(tick=100))
    assert [s for s in team.sent if s[0] == "open_pack"] == [("open_pack", 6)]
    assert "tick 100 taker: opened sobre_barrio #6: RET-01, LAV-02" in lines


def test_a_dry_run_only_says_it_would_open(tmp_path):
    team = PackTeam()
    t, _ = opener(tmp_path, team, live=False, open_sealed_packs=True)
    t.on_tick(clock(tick=100))
    packs = [r for r in rows(tmp_path) if r["kind"] == "pack_open"]
    assert [r["status"] for r in packs] == ["approved"] and not [s for s in team.sent if s[0] == "open_pack"]


# ---------------------------------------------------------------- the scan and where it is kept


def test_the_scan_stops_after_a_gap_past_the_starting_assets():
    def read(aid):
        if aid > 272:
            raise BazaarError("unknown_asset", "no such asset", 404)
        return {"id": aid, "ref": "LAV-01"}

    naps: list[float] = []
    got, why = scan_ids(read, 268, max_id=400, gap=3, pause_s=0.5, sleep=naps.append)
    assert [r["id"] for r in got] == [268, 269, 270, 271, 272] and "3 unknown ids" in why
    assert naps == [0.5] * 7  # one pause between reads, none before the first


def test_the_first_refusal_ends_the_scan_and_is_never_retried():
    calls: list[int] = []

    def read(aid):
        calls.append(aid)
        if aid == 3:
            raise BazaarError("rate_limited", "slow down", 429)
        return {"id": aid, "ref": "LAV-01"}

    got, why = scan_ids(read, 1, max_id=10, gap=3, pause_s=0.0, sleep=lambda s: None)
    assert [r["id"] for r in got] == [1, 2] and calls == [1, 2, 3] and "rate_limited" in why


def test_scan_files_rotate_and_a_torn_line_is_skipped(tmp_path):
    write_scan_file(tmp_path, SCAN[:1])
    write_scan_file(tmp_path, SCAN)
    assert [r["id"] for r in read_scan_file(tmp_path / "scan.prev.jsonl")] == [47]
    with (tmp_path / SCAN_FILE).open("a") as handle:
        handle.write('{"id": 9, "ref": "LAV')
    assert [r["id"] for r in read_scan_file(tmp_path / SCAN_FILE)] == [47, 200, 5]


def test_the_scan_store_falls_back_to_the_file_and_never_retries_a_dead_database(tmp_path):
    write_scan_file(tmp_path, SCAN)
    attempts: list[int] = []

    def connect():
        attempts.append(1)
        raise OSError("down")

    logs: list[str] = []
    store = ScanStore(tmp_path, connect, logs.append, every=10)
    assert len(store.rows(100)) == 3 and len(attempts) == 1 and "using scan.jsonl" in logs[0]
    write_scan_file(tmp_path, SCAN[:1])
    assert len(store.rows(105)) == 3  # cached until 10 ticks have passed
    assert len(store.rows(110)) == 1 and len(attempts) == 1


def test_a_missing_table_is_asked_again_at_the_next_reload(tmp_path):
    import psycopg

    attempts: list[int] = []

    def connect():
        attempts.append(1)
        raise psycopg.errors.UndefinedTable("relation supply_assets does not exist")

    store = ScanStore(tmp_path, connect, every=10)
    assert store.rows(100) == [] and store.rows(110) == [] and len(attempts) == 2


def test_a_feed_without_a_scan_store_gives_no_scan():
    assert MarketFeed(lambda n: []).scan(5) == ()
