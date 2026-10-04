"""Card hunt (`agents/card_hunt.py`, BAZAAR_CARD_HUNT): dealer deals only for empty ladder slots, team trades on
the deterministic gate, never across our value, the thread and accept limits, duels first (fakes, no network)."""

from dataclasses import replace
from types import SimpleNamespace

from bazaar_agent.affinity import AffinityMap
from bazaar_agent.agents import card_hunt
from bazaar_agent.agents import dealer_sell_desk as sell
from bazaar_agent.agents.ladder_probe import LadderSlots
from bazaar_agent.agents.market import OpenOffer, board_offers
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.agents.taker import Taker, TakerConfig, ask_candidates
from bazaar_agent.guardrails import Context, Guardrails
from bazaar_agent.opportunities import score_offer
from bazaar_agent.strategy import build_market
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, bid, clock, parts, rows
from tests.test_dealer_sell_desk import MARKET, ME_DUP
from tests.test_strategy import CATALOG, ME, PARAMS, market, playbook
from tests.test_taker import VENUES, board, taker
from tests.test_team_desk import Team, desk, view
from tests.test_team_desk_jev import Asked, opened

# ---------------------------------------------------------------- dealers: ladder slots only

SLOTS = LadderSlots({"abuela": 1, "chato": 2, "pilar": 3}, {"abuela": 3, "chato": 1})


def test_a_dealer_fills_a_slot_only_on_a_known_level_with_an_empty_slot():
    assert not card_hunt.fills_slot("abuela", SLOTS)  # L1 has its three scored deals
    assert card_hunt.fills_slot("chato", SLOTS) and card_hunt.fills_slot("pilar", SLOTS)
    assert not card_hunt.fills_slot("picaros", SLOTS)  # unknown level: never a slot
    assert not card_hunt.fills_slot("chato", None)


def hunting_taker(tmp_path, **config):
    t = Taker(
        FakeTeam(),
        FakePublic(),
        live=False,
        log=(lines := []).append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0, card_hunt=True, **config),
        **parts(tmp_path),
    )
    return t, lines


def test_the_hunt_drops_collecting_buys_and_packs_and_keeps_ladder_slot_buys(tmp_path):
    book = playbook()
    (buy,) = [mv for mv in book.buys if mv.source == "abuela"][:1]
    moves = [buy, replace(buy, source="chato", ref="LAV-08"), replace(buy, source="picaros"), *book.packs]
    t, lines = hunting_taker(tmp_path)
    run = SimpleNamespace(slots=SLOTS, snap=SimpleNamespace(clock=clock()))
    kept = t._ladder_only(run, moves)  # type: ignore[arg-type]
    assert [mv.source for mv in kept] == ["chato"]  # abuela full, picaros unknown, packs never
    assert any("card hunt:" in line and "dropped" in line for line in lines)


def test_the_hunt_switch_is_on_from_the_cli_and_off_in_code():
    assert TakerConfig().card_hunt is False
    assert card_hunt.CARD_HUNT_ENV == "BAZAAR_CARD_HUNT"


# ---------------------------------------------------------------- never across our value


def test_an_ask_over_our_own_bid_is_taken_only_below_our_value():
    offers = board(ask(1, "LAV-08", 28, asset=901))
    ours = OpenOffer(77, "bid", "LAV-08", 25, "rastro", None, 140, 90)
    assert ask_candidates(market(), offers, VENUES, PARAMS, set(), {"LAV-08": ours}) == []  # before: wait
    (c,) = ask_candidates(market(), offers, VENUES, PARAMS, set(), {"LAV-08": ours}, None, True)
    assert c.replaces_bid == ours and c.surplus >= PARAMS.min_buy_surplus and c.total < c.value
    dear = board(ask(2, "LAV-08", 200, asset=902))
    assert ask_candidates(market(), dear, VENUES, PARAMS, set(), {"LAV-08": ours}, None, True) == []


def sale(price: int, me: dict, horizon: int | None, keep: frozenset[str] = frozenset()):
    m = build_market(me, CATALOG, [], [])
    (o,) = board_offers({"offers": [bid(9, "LAV-06", price)]}, "rastro", "t01")
    ctx = Context(400, {"LAV-06": 1}, TICK, 1.5)
    return score_offer(
        o, m, me, PARAMS, Guardrails(), AffinityMap(), VENUES["rastro"], ctx, page_horizon=horizon, keep_sets=keep
    )


def test_a_page_that_cannot_complete_puts_no_bonus_at_stake_but_the_value_still_floors_the_sale():
    # LAV-06: our single copy (value 40) of a page missing 4 of 6 cards.
    before, hunt = sale(60, ME, None), sale(60, ME, card_hunt.PAGE_HORIZON)
    assert before is not None and hunt is not None and hunt.ours > before.ours
    assert hunt.ours == 60 - VENUES["rastro"].fee(60) - 40.0  # exactly our value: no bonus share
    kept = sale(60, ME, card_hunt.PAGE_HORIZON, frozenset({"LAV"}))  # a page we are still completing
    assert kept is not None and kept.ours == before.ours
    low = sale(30, ME, card_hunt.PAGE_HORIZON)
    assert low is not None and low.ours < 0  # below our value: never a candidate (sell_min_surplus > 0)


def test_the_sell_desk_hunt_never_sells_a_complete_page_copy_and_floors_at_our_value():
    rules = Guardrails(protect_complete_pages_only=True)
    found = sell.candidates(ME_DUP, CATALOG, MARKET, PARAMS, rules, hunt=True)
    assert all(c.ref != "LAT-09" for c in found)  # LAT is complete: its only copy stays
    assert all(c.floor >= c.your_value + rules.dealer_sell_min_surplus for c in found)
    m = build_market(ME_DUP, CATALOG, [], [])
    lav06 = m.cards["LAV-06"]
    assert sell.hunt_single(m, lav06, rules, 1)  # 4 of 6 missing: cannot complete
    assert not sell.hunt_single(m, lav06, Guardrails(), 1)  # needs protect_complete_pages_only
    assert not sell.hunt_single(m, m.cards["LAT-09"], rules, 1)  # complete page


def test_a_dealer_sale_under_the_hunt_only_fills_an_empty_ladder_slot():
    c = sell.Candidate(1, "LAV-08", "uncommon", 2.0, 2.0, 4, "abuela", 6.0, level=1)
    levels = {"abuela": 1, "chato": 2}
    assert sell.ladder_slot_sells([c], levels, {"abuela": 3}) == []
    assert sell.ladder_slot_sells([c], levels, {"abuela": 2}) == [c]
    assert sell.ladder_slot_sells([replace(c, level=None)], levels, {}) == []  # unknown level
    assert sell.ladder_slot_sells([c], levels, None) == []  # unknown deals: fail closed


# ---------------------------------------------------------------- the team desk: deterministic gate, limits


def hunting_desk(tmp_path, team, **rules):
    d, lines = desk(tmp_path, team, **rules)
    d.hunt = True
    return d, lines


def test_the_hunt_opens_a_fair_swap_without_asking_jev(tmp_path):
    team = Team()
    d, _ = hunting_desk(tmp_path, team)
    jev = Asked(JevAdvice("undecided", 0.4, reason="below_threshold"))
    d.converse(view(jev=jev), set())
    assert [s[0] for s in opened(team)] == ["open_thread", "say"] and jev.states == []


def test_the_hunt_never_sends_a_swap_the_rules_refuse(tmp_path):
    team = Team()
    d, _ = hunting_desk(tmp_path, team, team_swap_min_surplus=1000)  # our gain can never reach it
    d.converse(view(), set())
    assert opened(team) == []


def test_the_hunt_desk_uses_more_threads_but_never_the_servers_six(tmp_path):
    rules = Guardrails()
    max_open, reserve = card_hunt.desk_limits(rules)
    assert max_open >= rules.team_threads_max_open and reserve <= rules.team_threads_dealer_reserve
    team = Team()
    d, _ = hunting_desk(tmp_path, team)
    d.converse(view(in_use=6), set())  # every conversation in use
    assert opened(team) == []


# ---------------------------------------------------------------- accepts: duels first, one per tick


def test_the_hunt_still_steps_back_when_a_duel_holds_the_accept(tmp_path):
    team = FakeTeam()
    hunt = TakerConfig(max_dealer_threads=0, card_hunt=True)
    t, lines, ledger = taker(
        tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True, config=hunt
    )
    assert ledger.reserve_accept(TICK, 1.5, 0, "duel:7", 1)  # the duel player went first
    t.on_tick(clock())
    assert team.sent == [] and ledger.accepts_in_tick(TICK) == 1


def test_the_hunt_takes_one_accept_per_tick_and_records_it(tmp_path):
    team = FakeTeam()
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]})
    hunt = TakerConfig(max_dealer_threads=0, card_hunt=True)
    t, _, ledger = taker(tmp_path, team, public, live=True, config=hunt)
    t.on_tick(clock())
    assert len([s for s in team.sent if s[0] == "accept"]) == 1 and ledger.accepts_in_tick(TICK) == 1
    assert [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]


def test_under_the_live_guardrails_a_far_from_complete_single_copy_sells_above_value_and_a_complete_one_never(
    tmp_path,
):
    # The repo's GUARDRAILS.md (protect_page_sets on every set, protect_complete_pages_only true) and the taker's
    # own context from /me: the check() the accept will meet, with the complete pages it reads from /me.
    from bazaar_agent.guardrails import Ledger, context_from, load_guardrails

    rules = load_guardrails().rules
    assert rules.protect_complete_pages_only
    me = {**ME, "album": {"pages": [  # the live /me shape: each page says whether it is complete
        {"set": "LAV", "have": 2, "of": 6, "complete": False},
        {"set": "LAT", "have": 2, "of": 2, "complete": True},
    ]}}  # fmt: skip
    ctx = context_from(me, TICK, 1.5, Ledger(tmp_path / "ledger.jsonl"), rules)
    m = build_market(me, CATALOG, [], [])
    (o,) = board_offers({"offers": [bid(9, "LAV-06", 60)]}, "rastro", "t01")
    op = score_offer(o, m, me, PARAMS, rules, AffinityMap(), VENUES["rastro"], ctx, page_horizon=2)
    assert op is not None and op.ours >= PARAMS.sell_min_surplus and op.verdict == "allowed", op
    (lat,) = board_offers({"offers": [bid(10, "LAT-09", 500)]}, "rastro", "t01")
    op = score_offer(lat, m, me, PARAMS, rules, AffinityMap(), VENUES["rastro"], ctx, page_horizon=2)
    assert op is None or op.verdict != "allowed"  # LAT is complete: its only copy never goes
