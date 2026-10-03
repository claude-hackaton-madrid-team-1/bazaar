"""The Workshop (SA1): free spares only, the triple ranking, the guardrails and the taker's step."""

from dataclasses import replace
from types import SimpleNamespace

from bazaar_agent import move_impact
from bazaar_agent.agents import taller as tl
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Action, Context, Guardrails, Ledger, check, load_guardrails
from bazaar_agent.level_watch import LevelWatch
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts, rows
from tests.test_strategy import ME

BOOKS = {"common": 10, "uncommon": 25, "rare": 70, "epic": 180, "legendary": 450}
CATALOG = {
    "rarities": {r: {"book": b} for r, b in BOOKS.items()},
    "sets": [
        {
            "id": "LAV",
            "cards": [
                {"id": "LAV-01", "rarity": "common", "page": True},
                {"id": "LAV-02", "rarity": "common", "page": True},
                {"id": "LAV-06", "rarity": "uncommon", "page": True},
                {"id": "LAV-07", "rarity": "uncommon", "page": True},
                {"id": "LAV-09", "rarity": "rare", "page": True},
                {"id": "LAV-11", "rarity": "epic", "page": False},
                {"id": "LAV-12", "rarity": "legendary", "page": False, "hidden": True},
            ],
        },
        {"id": "SAL", "cards": [{"id": "SAL-01", "rarity": "common", "page": True}]},
    ],
}
DEALERS = [
    {"id": "abuela", "status": "active", "level": 1, "menu": {"buys": [{"rarity": "common"}, {"rarity": "uncommon"}]}},
    {"id": "chato", "status": "active", "level": 2, "menu": {"buys": [{"rarity": "uncommon"}, {"rarity": "rare"}]}},
    {"id": "picaros", "status": "active", "level": 4, "menu": {"buys": [{"rarity": "uncommon"}]}},
    {"id": "banco", "status": "active", "level": 5, "menu": {"buys": [{"rarity": "epic"}]}},
]


def card(aid, ref, value):
    return {"id": aid, "kind": "card", "ref": ref, "your_value": value}


def me(*assets, unlocked=("abuela", "chato", "picaros", "banco")):
    return {
        "id": "t01",
        "cash": 100,
        "unlocked": list(unlocked),
        "album": {"pages": [{"set": "LAV"}, {"set": "SAL"}]},
        "assets": list(assets),
    }


# LAV-01 ×3 (2 spares), SAL-01 ×2 (1 spare), LAV-02 ×1 (kept): three common spares; LAV-06/07 missing nothing
SPARES = me(
    card(1, "LAV-01", 4.0),
    card(2, "LAV-01", 4.0),
    card(3, "LAV-01", 4.0),
    card(4, "SAL-01", 1.3),
    card(5, "SAL-01", 1.3),
    card(6, "LAV-02", 2.0),
    card(7, "LAV-06", 40.0),
    card(8, "LAV-07", 40.0),
    card(9, "LAV-09", 80.0),
)


# ---------------------------------------------------------------- free spares and the ranking


def test_one_free_copy_of_each_card_always_stays_and_a_busy_copy_is_never_free():
    spares = tl.free_spares(SPARES, CATALOG)
    assert sorted(s.asset_id for s in spares["common"]) == [2, 3, 5]  # LAV-01 keeps #1, SAL-01 keeps #4
    assert "uncommon" not in spares and "rare" not in spares  # single copies: never a spare
    busy = tl.free_spares(SPARES, CATALOG, busy={2, 4})  # LAV-01 #2 and SAL-01 #4 sit in open offers of ours
    assert sorted(s.asset_id for s in busy["common"]) == [3]  # SAL-01 #5 is now its last free copy: kept


def test_a_copy_of_an_unknown_or_hidden_card_or_without_a_value_is_never_a_spare():
    odd = me(card(1, "XXX-01", 1.0), card(2, "XXX-01", 1.0), card(3, "LAV-12", 1.0), card(4, "LAV-12", 1.0))
    odd["assets"] += [{"id": 5, "kind": "card", "ref": "LAV-01"}, {"id": 6, "kind": "card", "ref": "LAV-01"}]
    assert tl.free_spares(odd, CATALOG) == {}


def test_the_triple_that_may_fill_a_missing_slot_goes_first_then_the_highest_buyer_level():
    cheap = [card(10, "LAV-06", 10.0), card(11, "LAV-06", 10.0), card(12, "LAV-07", 10.0)]
    held = me(*SPARES["assets"], *cheap)  # uncommon spares: LAV-06 #10, #11 and LAV-07 #12 (LAV-07 #8 is kept)
    ranked = tl.rank_triples(held, CATALOG, DEALERS)
    # no slot is missing: the result picaros (L4) buys beats the one only chato (L2) buys
    assert [(t.rarity, t.buyer, t.buyer_level) for t in ranked] == [("common", "picaros", 4), ("uncommon", "chato", 2)]
    no_rare = me(*[a for a in held["assets"] if a["ref"] != "LAV-09"])
    assert [(t.rarity, t.fills) for t in tl.rank_triples(no_rare, CATALOG, DEALERS)] == [
        ("uncommon", ("LAV-09",)),
        ("common", ()),
    ]


def test_a_triple_worth_more_to_us_than_the_result_book_is_never_made():
    dear = me(card(1, "LAV-06", 40.0), card(2, "LAV-06", 40.0), card(3, "LAV-07", 40.0), card(4, "LAV-07", 40.0))
    dear["assets"] += [card(5, "LAV-07", 40.0)]
    assert tl.rank_triples(dear, CATALOG, DEALERS) == []  # 3 × 40 = 120 > rare book 70


def test_ranking_prefers_a_pull_that_may_fill_a_slot_and_names_the_ladder_buyer():
    missing = me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"])  # LAV-07 (uncommon) is now missing
    t = tl.rank_triples(missing, CATALOG, DEALERS)[0]
    assert (t.rarity, t.to_rarity, t.fills) == ("common", "uncommon", ("LAV-07",))
    assert (t.buyer, t.buyer_level) == ("picaros", 4)  # the highest unlocked level that buys uncommons
    assert t.asset_ids == [5, 2, 3]  # the three we lose least by: SAL-01 1.3, then LAV-01 4.0 ×2
    assert "may fill 1 missing uncommon slot" in t.reason() and "picaros (L4)" in t.reason()


def test_a_dealer_we_have_not_unlocked_or_not_active_is_no_buyer():
    assert tl.best_buyer(DEALERS, ["abuela"], "uncommon") == ("abuela", 1)
    retired = [{**d, "status": "announced"} for d in DEALERS]
    assert tl.best_buyer(retired, ["abuela", "picaros"], "uncommon") == (None, 0)


def test_fewer_than_three_free_spares_of_a_rarity_make_no_triple():
    assert tl.rank_triples(SPARES, CATALOG, DEALERS, busy={5}) == []  # 2 and 3 are left


def test_pulled_reads_the_card_whatever_the_answer_shape():
    assert tl.pulled({"card": {"ref": "LAV-07", "name": "Samosas"}}) == "LAV-07 Samosas"
    assert tl.pulled({"cards": [{"ref": "SAL-06"}]}) == "SAL-06"
    assert tl.pulled(None) == "?"


# ---------------------------------------------------------------- the guardrails


def ctx(**kw):
    base = Context(cash=100, held={"LAV-01": 3, "SAL-01": 2}, tick=10, t_hours=7.5, stops=())
    return replace(base, **kw)


ON = Guardrails(taller_enabled=True, max_taller_per_game_hour=2)


def test_check_refuses_a_craft_while_taller_enabled_is_false():
    verdict = check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(), Guardrails())
    assert not verdict.allowed and "taller_enabled = false" in verdict.violations


def test_check_keeps_one_free_copy_of_every_card_of_any_set():
    assert check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(), ON).allowed
    last = check(Action("taller", "LAV-01,LAV-01,LAV-01", "common"), ctx(), ON)
    assert not last.allowed and "LAV-01: giving 3 of our 3 free copies leaves none" in last.violations[0]
    listed = check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(sellable={"LAV-01": 2, "SAL-01": 2}), ON)
    assert not listed.allowed  # one LAV-01 sits in an open ask of ours: two given would leave no free copy


def test_check_holds_the_hourly_cap_and_three_copies_exactly():
    capped = check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(taller_last_hour=2), ON)
    assert not capped.allowed and "max_taller_per_game_hour 2" in capped.violations[0]
    two = check(Action("taller", "LAV-01,SAL-01", "common"), ctx(), ON)
    assert not two.allowed and "three copies, not 2" in two.violations[0]


def test_the_kill_switch_halts_a_craft():
    verdict = check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(stops=("paused",)), ON)
    assert not verdict.allowed and verdict.halted


def test_a_craft_of_copies_a_team_trade_brought_us_answers_to_the_score_impact_rule():
    rules = ON.model_copy(update={"max_score_loss_per_move": 0.2})
    cards = move_impact.our_cards(SPARES)
    action = Action("taller", "SAL-01,LAV-01,LAV-01", "common", assets=(5, 2, 3))
    ours = move_impact.Facts(team="t01", origins={})  # no settlement brought them: starting stock or packs
    assert check(action, ctx(cards=cards, impact=ours), rules).allowed
    bought = move_impact.Facts(team="t01", origins={2: move_impact.Origin("team", "t07", 3, 50)})
    refused = check(action, ctx(cards=cards, impact=bought), rules)  # LAV-01 #2 (4.0) came from t07: -4 × 0.053
    assert not refused.allowed and "max_score_loss_per_move" in refused.violations[0]
    unnamed = check(Action("taller", "SAL-01,LAV-01,LAV-01", "common"), ctx(cards=cards, impact=ours), rules)
    assert not unnamed.allowed and "not named one by one" in unnamed.violations[0]


def test_guardrails_md_ships_the_workshop_on_and_capped():
    # Omar, Sat 3 Oct ~22:15: the Workshop is on; the taker's own crafts stay capped per game hour
    rules = load_guardrails().rules
    assert rules.taller_enabled is True and rules.max_taller_per_game_hour == 2


# ---------------------------------------------------------------- the taker's step


LEVELS = {"levels": [{"id": "taller", "kind": "taller", "name": "The Workshop", "state": "active", "how": "..."}]}


class News:
    """The news sentinel as the taker sees it: its level watch, no request."""

    def __init__(self, payload=None):
        self.levels = LevelWatch(lambda rows: None, lambda line: None)
        if payload is not None:
            self.levels.update(payload, 1)
        self.matrix, self.upcoming = None, []

    def on_tick(self, *args, **kwargs):
        return []


class Team(FakeTeam):
    def call(self, method, path, body=None):
        self.sent.append(("call", method, path, body))
        return {"card": {"ref": "LAV-07", "name": "Samosas"}}


def run_taker(tmp_path, *, news, live=True, ticks=1, team=None, before=None, **rules):
    lines: list[str] = []
    team = team or Team(me=me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"]))
    public = FakePublic(catalog=CATALOG, dealers=DEALERS)
    kw = parts(tmp_path, **rules)
    t = Taker(team, public, live=live, log=lines.append, now=lambda: 1000.0, sleep=lambda s: None,
              config=TakerConfig(max_dealer_threads=0), news=news, **kw)  # fmt: skip
    if before is not None:
        before(t, kw["ledger"])
    for i in range(ticks):
        team.now = clock(tick=100 + i)
        t.on_tick(team.now)
    return team, lines


def crafts(team):
    return [s for s in team.sent if s[:3] == ("call", "POST", "/api/taller")]


def test_the_taker_crafts_free_spares_once_the_level_is_active(tmp_path):
    team, lines = run_taker(tmp_path, news=News(LEVELS), taller_enabled=True, max_taller_per_game_hour=2)
    assert crafts(team) == [("call", "POST", "/api/taller", {"assets": [5, 2, 3]})]
    row = next(r for r in rows(tmp_path) if r.get("kind") == "taller")
    assert row["chosen"] and row["inputs"]["fills"] == ["LAV-07"] and row["inputs"]["buyer"] == "picaros"
    assert any("Workshop crafted SAL-01, LAV-01, LAV-01 into LAV-07 Samosas" in line for line in lines)


def test_no_craft_before_the_sentinel_saw_the_level_active_or_with_the_switch_off(tmp_path):
    announced = {"levels": [{**LEVELS["levels"][0], "state": "announced"}]}
    for news, on in ((News(), True), (News(announced), True), (News(LEVELS), False)):
        team, _ = run_taker(tmp_path, news=news, taller_enabled=on)
        assert crafts(team) == []
    assert not [r for r in rows(tmp_path) if r.get("kind") == "taller"]


def test_the_hourly_cap_stops_a_second_craft_in_the_same_game_hour(tmp_path):
    team, _ = run_taker(tmp_path, news=News(LEVELS), ticks=3, taller_enabled=True, max_taller_per_game_hour=1)
    assert len(crafts(team)) == 1  # the fake /me still shows the spares: only the cap stops the next ones
    assert team.reads.count("duels") == 1  # at the cap (shared ledger) the step sends no request at all
    assert [e["item"] for e in Ledger(tmp_path / "ledger.jsonl").entries()] == ["taller:SAL-01,LAV-01,LAV-01"]


def test_a_dry_run_sends_nothing_and_says_the_craft_once(tmp_path):
    team, _ = run_taker(tmp_path, news=News(LEVELS), live=False, ticks=2, taller_enabled=True)
    assert crafts(team) == []
    assert len([r for r in rows(tmp_path) if r.get("kind") == "taller"]) == 1


def test_the_kill_switch_holds_the_workshop(tmp_path):
    (tmp_path / "PAUSE").touch()
    team, _ = run_taker(tmp_path, news=News(LEVELS), taller_enabled=True, pause_file=str(tmp_path / "PAUSE"))
    assert crafts(team) == []


def test_a_copy_in_an_open_offer_of_ours_is_never_crafted(tmp_path):
    ours = {"id": 77, "maker": "t01", "status": "open", "venue": "rastro", "give": {"assets": [{"id": 2,
            "ref": "LAV-01"}]}, "want": {"cash": 9}}  # fmt: skip
    team = Team(me=me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"]), offers=[ours])
    team, _ = run_taker(tmp_path, news=News(LEVELS), team=team, taller_enabled=True)
    assert crafts(team) == []  # only #3 and #5 are free spares now: no triple


def test_the_shared_fixture_me_has_no_triple():
    assert tl.rank_triples(ME, CATALOG, DEALERS) == []


def test_a_card_a_live_team_desk_talk_may_give_is_never_crafted(tmp_path):
    def talk(t, ledger):  # security-auditor + pr-reviewer P1 on #235: the desk gives LAV-01 #1 in a swap
        t.team_desk.talks[99] = SimpleNamespace(trade=SimpleNamespace(refs=("LAV-01", "LAV-09"), asset_id=1))

    team, _ = run_taker(tmp_path, news=News(LEVELS), before=talk, taller_enabled=True)
    assert crafts(team) == []  # LAV-01 is out: SAL-01 #5 is the only free spare left


def test_the_team_desk_posting_after_a_craft_sees_the_crafted_copies_promised(tmp_path):
    seen: list[list] = []

    def spy(t, ledger):
        t.team_desk.converse = lambda view, taken: seen.append(list(view.offers))

    team, _ = run_taker(tmp_path, news=News(LEVELS), before=spy, taller_enabled=True)
    assert crafts(team) and seen
    from bazaar_agent.agents.team_desk import spare_copy

    assert spare_copy(team.me(), seen[0], "t01", "LAV-01") is None  # #2, #3 crafted: #1 is no spare any more


def test_a_card_accepted_this_or_last_tick_is_never_crafted(tmp_path):
    def accepted(t, ledger):  # the maker's dealer sell reserves the card ref (dealer_sell_desk.standard_hooks)
        ledger.reserve_accept(99, 1.5, 9, "LAV-01", 1)

    team, _ = run_taker(tmp_path, news=News(LEVELS), before=accepted, taller_enabled=True)
    assert crafts(team) == []


def test_a_copy_a_sell_thread_of_ours_is_about_is_never_crafted(tmp_path):
    sell = {"id": 7, "with": "abuela", "status": "open", "topic": {"sell": {"assets": [2]}}}
    team = Team(me=me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"]), threads=[sell])
    team, _ = run_taker(tmp_path, news=News(LEVELS), team=team, taller_enabled=True)
    assert crafts(team) == []
    assert tl.sell_thread_assets([sell, {"topic": {"buy": {"card": "LAV-01"}}}, "junk"]) == {2}


def test_the_guard_refuses_copies_that_are_not_the_cards_named():
    cards = move_impact.our_cards(SPARES)
    lie = Action("taller", "SAL-01,LAV-01,LAV-01", "common", assets=(9, 2, 3))  # #9 is LAV-09, not SAL-01
    verdict = check(lie, ctx(cards=cards), ON)
    assert not verdict.allowed and "are not the cards" in verdict.violations[0]


def test_pulled_never_carries_control_or_bidi_characters():
    assert tl.pulled({"card": {"ref": "LAV-07", "name": "Sam\u202eosas\n[red]x"}}) == "LAV-07 Sam osas [red]x"


# ---------------------------------------------------------------- #239 review follow-ups (P2/P3)


def test_the_workshop_waits_while_the_maker_may_sell_to_dealers():
    both = ON.model_copy(update={"dealer_sell_enabled": True})  # nothing shared tells either what the other gives
    verdict = check(Action("taller", "LAV-01,LAV-01,SAL-01", "common"), ctx(), both)
    assert not verdict.allowed and "dealer_sell_enabled = true" in verdict.violations[0]


def test_the_guard_refuses_a_repeated_copy_or_copies_it_cannot_match():
    cards = move_impact.our_cards(SPARES)
    twice = check(Action("taller", "LAV-01,LAV-01,LAV-01", "common", assets=(2, 2, 3)), ctx(cards=cards), ON)
    assert not twice.allowed and any("repeat one" in v for v in twice.violations)
    blind = check(Action("taller", "SAL-01,LAV-01,LAV-01", "common", assets=(5, 2, 3)), ctx(), ON)
    assert not blind.allowed and any("no cards read" in v for v in blind.violations)


class Boom(Team):
    """The craft lands, then something after the POST fails (a decisions write, a hostile answer)."""

    def call(self, method, path, body=None):
        super().call(method, path, body)
        raise RuntimeError("after the send")


class Locked(Team):
    def call(self, method, path, body=None):
        from bazaar_agent.sdk import BazaarError

        self.sent.append(("call", method, path, body))
        raise BazaarError("locked", "not yet", 403)


def desk_spy(seen, takers):
    def spy(t, ledger):
        takers.append(t)
        t.team_desk.converse = lambda view, taken: seen.append(list(view.offers))

    return spy


def test_a_failure_after_the_post_still_promises_and_counts_the_craft(tmp_path):
    seen: list[list] = []
    takers: list = []
    team = Boom(me=me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"]))
    team, lines = run_taker(tmp_path, news=News(LEVELS), team=team, before=desk_spy(seen, takers), taller_enabled=True)
    assert crafts(team) and any("Workshop skipped (RuntimeError)" in line for line in lines)
    from bazaar_agent.agents.team_desk import spare_copy

    assert (
        seen
        and spare_copy(team.me(), seen[0], "t01", "LAV-01") is None
        and tl.crafts_last_hour(takers[0].ledger, 1.5) == 1
    )


def test_a_refused_craft_is_taken_back_and_rests(tmp_path):
    seen: list[list] = []
    takers: list = []
    team = Locked(me=me(*[a for a in SPARES["assets"] if a["ref"] != "LAV-07"]))
    team, _ = run_taker(tmp_path, news=News(LEVELS), team=team, before=desk_spy(seen, takers), taller_enabled=True)
    assert len(crafts(team)) == 1
    assert tl.crafts_last_hour(takers[0].ledger, 1.5) == 1  # shared cap conservatively retains refusals
    assert all(o.get("id") != -4 for o in seen[0])  # nothing promised
    assert takers[0]._taller_rest_until == 110


def test_only_our_dealer_threads_name_busy_copies():
    team = {"id": 8, "with": "t05", "topic": {"sell": {"assets": [1, 2, 3, 4, 5]}}}  # the other team's topic
    bad = {"id": 9, "with": "abuela", "topic": {"sell": {"assets": 5}}}
    assert tl.sell_thread_assets([team, bad, {"id": 7, "with": "abuela", "topic": {"sell": {"assets": [2]}}}]) == {2}
    assert tl.clean("a‮b\nc" * 50, 10) == "a b ca b c"  # "a b c" repeated, no line or bidi character left
