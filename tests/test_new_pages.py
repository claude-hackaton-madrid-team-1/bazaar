"""New pages mid-game (N14b): El Retiro on Saturday, Chamberí on Sunday.

The catalog lists RET and CHA before release with every card at zero minted; what changes at release is the
set's page in /api/me. The taker and the maker must rank on it the first tick it shows up (no restart), and
never sell our only copy of a card the new page needs (`protect_page_sets`). The release fixture and the
`dealer_mints_unminted` switch come from the night shift's B26 (#129).
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from bazaar_agent import guardrails as gr
from bazaar_agent import strategy
from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.runtime import MarketFeed, PageWatch
from bazaar_agent.agents.seller import open_commitments, post, sell_listing
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.album import album_view
from tests.agent_fakes import FakePublic, FakeTeam, bid, clock, our_ask, parts, rows
from tests.test_intel import settle
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, RULES

PROTECT = gr.Guardrails(protect_page_sets="RET,CHA")
MINTS = PARAMS.model_copy(update={"dealer_mints_unminted": True})
RET_PAGE = {"set": "RET", "name": "El Retiro", "have": 0, "of": 1, "complete": False}
# t08 buys RET-01 twice (Abuela, then a team at 25): RET is its set, and the tape prices the card.
RET_EVENTS = [
    *EVENTS,
    settle(20, 20, "abuela", "t08", "RET-01", 9, tick=8, kind="card", asset_id=901),
    settle(21, 21, "t13", "t08", "RET-01", 25, tick=9, kind="card", persona=None, asset_id=902),
]


def released_ret(me: dict, copies: int = 0, affinity: float | None = None) -> dict:
    """/me the tick after RET is released: its album page appears, plus `copies` RET-01 from the grant pack."""
    me = deepcopy(me)
    me["album"]["pages"].append(dict(RET_PAGE, have=min(copies, 1)))
    me["assets"] += [
        {"id": 300 + i, "kind": "card", "ref": "RET-01", "set": "RET", "rarity": "common", "your_value": 7.0}
        for i in range(copies)
    ]
    if affinity is not None:
        me["affinity"]["RET"] = affinity
    return me


def ctx(held: dict[str, int]) -> gr.Context:
    return gr.Context(cash=400, held=held, tick=10, t_hours=4.1)


# ---------------------------------------------------------------- the guardrail


def test_the_committed_guardrails_protect_el_retiro_and_chamberi():
    rules = gr.load_guardrails().rules
    assert gr.set_codes(rules.protect_page_sets) == ("RET", "CHA")
    assert rules.protects("RET-01", "common", 1) and rules.protects("CHA-09", "rare", 1)
    assert not gr.Guardrails().protects("RET-01", "common", 1)  # the model default is today's behaviour


@pytest.mark.parametrize("value, codes", [("RET,CHA", ("RET", "CHA")), (" RET ", ("RET",)), ("none", ()), ("-", ())])
def test_set_codes_parse_and_none_turns_the_rule_off(value, codes):
    assert gr.set_codes(value) == codes


def test_a_bad_set_code_fails_fast():
    with pytest.raises(gr.GuardrailsError, match="protect_page_sets"):
        gr.parse_guardrails("- `protect_page_sets` = retiro — x")


def test_only_the_last_copy_of_a_new_page_card_is_refused():
    one, two = ctx({"RET-01": 1}), ctx({"RET-01": 2})
    for kind in ("sell", "accept_sell"):
        verdict = gr.check(gr.Action(kind, "RET-01", "common", 40, 7.0), one, PROTECT)
        assert not verdict.allowed and "protect_page_sets" in str(verdict)
    assert gr.check(gr.Action("sell", "RET-01", "common", 40, 7.0), two, PROTECT).allowed  # a duplicate sells
    assert gr.check(gr.Action("sell", "RET-11", "epic", 400, 126.0), ctx({"RET-11": 1}), PROTECT).allowed
    assert gr.check(gr.Action("sell", "LAT-09", "rare", 68, 35.0), ctx({"LAT-09": 1}), PROTECT).allowed
    assert gr.check(gr.Action("bid", "RET-02", "common", 9), one, PROTECT).allowed  # buying is untouched
    assert gr.check(gr.Action("sell", "RET-01", "common", 40, 7.0), one, RULES).allowed  # `none`: today


def sell_verdict(me: dict, target: str, offers: list[dict]) -> gr.Verdict:
    """`bazaar sell list` / the desk's `sell_list`: one listing checked with our open offers."""
    held: dict[str, int] = {}
    for a in me["assets"]:
        if a["kind"] == "card":
            held[a["ref"]] = held.get(a["ref"], 0) + 1
    listing = sell_listing(me, target, 40)
    return post(None, listing, ctx(held), PROTECT, live=False, commitments=open_commitments(offers, "t01")).verdict


def test_an_open_bid_for_the_card_does_not_make_our_only_copy_sellable():
    # #145 review P2: the committed context counted the copy we bid for as held, so 1 looked like 2.
    verdict = sell_verdict(released_ret(ME, copies=1), "300", [bid(71, "RET-01", 9)])
    assert not verdict.allowed and "protect_page_sets" in str(verdict)


def test_a_copy_already_in_an_open_ask_counts_as_sold():
    # #145 review P2: two copies, #301 already listed: listing #300 too could take the page's last card.
    two = released_ret(ME, copies=2)
    assert sell_verdict(two, "300", []).allowed
    verdict = sell_verdict(two, "300", [our_ask(72, 301, "RET-01", 30)])
    assert not verdict.allowed and "protect_page_sets" in str(verdict)


def test_an_accepted_ask_still_settling_counts_as_sold():
    # #145 security P3: a team accepted our ask on #301 this tick; /me still shows both copies until it settles.
    accepted = {**our_ask(73, 301, "RET-01", 30), "status": "accepted"}
    verdict = sell_verdict(released_ret(ME, copies=2), "300", [accepted])
    assert not verdict.allowed and "protect_page_sets" in str(verdict)


def test_an_ask_that_does_not_name_its_card_counts_against_every_card():
    # #145 security P3: a bare asset id cannot be matched to a card, so it fails closed.
    bare = {**our_ask(74, 301, "RET-01", 30), "give": {"cash": 0, "assets": [301], "types": []}}
    assert not sell_verdict(released_ret(ME, copies=2), "300", [bare]).allowed


@pytest.mark.parametrize("rarity", [None, "None", "", "Common", "RARE", "mystery"])
def test_any_rarity_but_epic_or_legendary_counts_as_a_page_card(rarity):
    # #145 security P3: odd spellings of a rarity (or none) must not fail open.
    assert PROTECT.protects("RET-03", rarity, 1)
    assert PROTECT.protects("ret-03", rarity, 1)  # a lowercase ref too
    assert not PROTECT.protects("RET-11", "Epic", 1) and not PROTECT.protects("RET-12", "legendary", 1)


def test_a_copy_of_unknown_rarity_is_treated_as_a_page_card():
    assert not gr.check(gr.Action("sell", "CHA-03", None, 40, 9.0), ctx({"CHA-03": 1}), PROTECT).allowed


# ---------------------------------------------------------------- the strategy


def test_before_the_release_the_new_set_is_invisible_and_after_it_appears_everywhere():
    before = strategy.build_market(ME, CATALOG, EVENTS, DEALERS)
    after = strategy.build_market(released_ret(ME), CATALOG, EVENTS, DEALERS)
    assert "RET" not in before.released and "RET" in after.released
    assert "RET" not in {p.set_code for p in album_view(ME, CATALOG)}
    ret = next(p for p in album_view(released_ret(ME), CATALOG) if p.set_code == "RET")
    assert [c.ref for c in ret.missing] == ["RET-01"]
    assert any(s.ref == "RET-01" for s in strategy.supply_view(after, PARAMS))


def test_a_new_cards_zero_minted_copies_are_a_dealer_buy_only_with_dealer_mints_unminted():
    m = strategy.build_market(released_ret(ME, affinity=1.6), CATALOG, EVENTS, DEALERS)
    card = m.cards["RET-01"]
    assert card.minted == 0
    assert strategy.supply_of(m, card, PARAMS).availability in ("packs", "none")  # today: "pull or wait"
    assert isinstance(strategy.buy_move(m, card, PARAMS, RULES), str)
    assert strategy.supply_of(m, card, MINTS).availability == "dealer"  # Abuela sells released commons
    move = strategy.buy_move(m, card, MINTS, RULES)
    assert not isinstance(move, str) and (move.ref, move.source) == ("RET-01", "abuela")


def test_a_page_the_catalog_does_not_know_yet_is_skipped_without_a_crash():
    stale = deepcopy(CATALOG)
    stale["sets"] = [s for s in stale["sets"] if s["id"] != "RET"]
    m = strategy.build_market(released_ret(ME), stale, EVENTS, DEALERS)
    assert "RET" in m.released and not any(c.set_code == "RET" for c in m.cards.values())
    moves, _ = strategy.buy_moves(m, PARAMS, RULES)
    assert all(not mv.ref.startswith("RET-") for mv in moves)


def test_the_strategy_file_keeps_todays_default_for_dealer_mints_unminted():
    assert strategy.load_strategy().params.dealer_mints_unminted is False


def sells(me: dict, rules: gr.Guardrails) -> dict[str, int | None]:
    book = strategy.build_playbook(me, CATALOG, RET_EVENTS, DEALERS, PARAMS, rules)
    return {mv.ref: mv.asset_id for mv in book.sells}


def test_our_only_copy_of_a_new_page_card_is_never_a_sell_move():
    one = released_ret(ME, copies=1)
    assert "RET-01" in sells(one, RULES)  # today: t08 chases RET and the tape pays 17 for a 7 + 1.75 card
    assert "RET-01" not in sells(one, PROTECT)
    assert "LAT-09" in sells(one, PROTECT)  # an old page is untouched
    assert sells(released_ret(ME, copies=2), PROTECT)["RET-01"] in (300, 301)  # a duplicate still sells


# ---------------------------------------------------------------- the agents, across the release


def test_an_ask_the_maker_posted_this_tick_does_not_block_a_new_pages_duplicate(tmp_path):
    # #145 re-review P3: the in-tick row of a posted ask named no card, so the fail-closed count refused
    # every later protected sell that tick. Both the LAT-09 ask and the RET-01 duplicate go out.
    team = FakeTeam(me=released_ret(ME, copies=2))
    kw = {**parts(tmp_path), "rules": PROTECT, "feed": MarketFeed(lambda n: deepcopy(RET_EVENTS))}
    Maker(team, FakePublic(events=RET_EVENTS), live=True, log=lambda line: None, now=lambda: 1000.0, **kw).on_tick(
        clock()
    )
    listed = [s[1]["assets"][0] for s in team.sent if s[0] == "list_offer" and s[1].get("assets")]
    assert 5 in listed and ({300, 301} & set(listed))


def test_a_malformed_album_reads_as_no_pages():
    # #145 security P3: `album` that is not a dict raised before the kill-switch hold.
    from bazaar_agent.agents.runtime import album_pages

    assert album_pages({"album": "x"}) == frozenset() and album_pages({"album": {"pages": 3}}) == frozenset()


def test_an_accepted_ask_without_our_id_as_maker_still_counts_as_sold():
    # #145 security P3: an accepted row with no maker (or a pseudonym) fails closed like an open one.
    accepted = {**our_ask(73, 301, "RET-01", 30), "status": "accepted", "maker": None}
    assert not sell_verdict(released_ret(ME, copies=2), "300", [accepted]).allowed


def test_page_watch_reports_a_page_once_and_nothing_on_the_first_tick():
    watch = PageWatch()
    assert watch.new(ME) == ()
    assert watch.new(ME) == ()
    assert watch.new(released_ret(ME)) == ("RET",)
    assert watch.new(released_ret(ME)) == ()


def test_one_running_maker_drops_its_ask_for_the_new_pages_card_without_a_restart(tmp_path):
    team = FakeTeam(me=released_ret(ME, copies=1), offers=[our_ask(70, 300, "RET-01", 17)])
    lines: list[str] = []
    kw = {**parts(tmp_path), "rules": PROTECT, "feed": MarketFeed(lambda n: deepcopy(RET_EVENTS))}
    m = Maker(team, FakePublic(events=RET_EVENTS), live=True, log=lines.append, now=lambda: 1000.0, **kw)
    m.on_tick(clock())
    assert ("cancel", 70) in team.sent
    assert not [s for s in team.sent if s[0] == "list_offer" and s[1].get("assets") == [300]]
    assert any(s[0] == "list_offer" and s[1].get("assets") == [5] for s in team.sent)  # LAT-09 still listed


def test_one_running_taker_ranks_the_new_page_the_tick_it_appears(tmp_path):
    """Same Taker instance, two ticks: before the release no RET thread; after it, RET-01 is bought from
    Abuela (with `dealer_mints_unminted`) and the log says the page arrived. LAV-02/LAV-08 are held here,
    so Abuela has nothing else to sell us."""
    me = deepcopy(ME)
    me["assets"] += [
        {"id": 7, "kind": "card", "ref": "LAV-02", "rarity": "common", "your_value": 16.0},
        {"id": 8, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 40.0},
    ]
    team = FakeTeam(me=me)
    lines: list[str] = []
    kw = {**parts(tmp_path), "params": lambda tick: MINTS}
    t = Taker(
        team,
        FakePublic(),
        live=False,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=2),
        **kw,
    )
    t.on_tick(clock(tick=100))
    opened = [r for r in rows(tmp_path) if r["kind"] == "dealer_open"]
    assert opened == [] and not any("new page" in line for line in lines)

    team._me = released_ret(me, affinity=1.6)
    t.on_tick(clock(tick=101))
    opened = [r for r in rows(tmp_path) if r["kind"] == "dealer_open"]
    assert [(r["tick"], r["inputs"]["dealer"], r["inputs"]["item"]) for r in opened] == [(101, "abuela", "RET-01")]
    assert "tick 101 taker: new page(s) RET in /api/me: ranked on 3 pages from this tick, no restart" in lines
