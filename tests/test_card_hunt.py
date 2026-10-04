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


def hunting_taker(tmp_path, rules=None, **config):
    t = Taker(
        FakeTeam(),
        FakePublic(),
        live=False,
        log=(lines := []).append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0, card_hunt=True, **config),
        **parts(tmp_path, **(rules or {})),
    )
    return t, lines


def test_the_hunt_drops_collecting_buys_and_packs_and_keeps_ladder_slot_buys(tmp_path):
    book = playbook()
    (buy,) = [mv for mv in book.buys if mv.source == "abuela"][:1]
    assert book.packs and all(mv.strategy == "pack_value" for mv in book.packs)
    moves = [buy, replace(buy, source="chato", ref="LAV-08"), replace(buy, source="picaros"), *book.packs]
    t, lines = hunting_taker(tmp_path)
    run = SimpleNamespace(slots=SLOTS, snap=SimpleNamespace(clock=clock()))
    kept = t._ladder_only(run, moves)  # type: ignore[arg-type]
    assert [mv.source for mv in kept] == ["chato"]  # abuela full, picaros unknown, holding-value packs never
    assert any("card hunt:" in line and "dropped" in line for line in lines)


def test_the_hunt_keeps_packs_bought_to_resell_to_teams(tmp_path):
    book = playbook()
    collect = book.packs[0]
    restock = replace(collect, strategy=card_hunt.RESALE_PACKS)  # #289: open and resell the pulls to teams
    t, _ = hunting_taker(tmp_path, rules={"pack_restock_enabled": True})
    run = SimpleNamespace(slots=SLOTS, snap=SimpleNamespace(clock=clock()))
    kept = t._ladder_only(run, [collect, restock])  # type: ignore[arg-type]
    assert kept == [restock]
    off, _ = hunting_taker(tmp_path, rules={"pack_restock_enabled": False})  # the switch stays the switch
    assert off._ladder_only(run, [collect, restock]) == []  # type: ignore[arg-type]


def test_the_hunt_switch_is_on_from_the_cli_and_off_in_code():
    assert TakerConfig().card_hunt is False
    assert card_hunt.CARD_HUNT_ENV == "BAZAAR_CARD_HUNT"


# ---------------------------------------------------------------- never across our value


def test_an_ask_over_our_own_bid_is_taken_only_below_our_value():
    # #287 made this the taker's own rule (no hunt switch): our lower bid no longer blocks a profitable ask.
    offers = board(ask(1, "LAV-08", 28, asset=901))
    ours = OpenOffer(77, "bid", "LAV-08", 25, "rastro", None, 140, 90)
    (c,) = ask_candidates(market(), offers, VENUES, PARAMS, set(), {"LAV-08": ours})
    assert c.replaces_bid == ours and c.surplus >= PARAMS.min_buy_surplus and c.total < c.value
    dear = board(ask(2, "LAV-08", 200, asset=902))
    assert ask_candidates(market(), dear, VENUES, PARAMS, set(), {"LAV-08": ours}) == []


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


def test_the_hunting_sell_desk_never_sells_an_only_copy_and_floors_at_our_value(tmp_path):
    from tests.agent_fakes import FakeTeam as _Team
    from tests.test_dealer_sell_desk import maker

    rules = Guardrails(protect_complete_pages_only=True)
    found = sell.candidates(ME_DUP, CATALOG, MARKET, PARAMS, rules)
    held = {r: sum(a.get("ref") == r for a in ME_DUP["assets"]) for r in {c.ref for c in found}}
    assert found and all(held[c.ref] > 1 for c in found)  # duplicates only: LAV-06, LAT-09 are only copies
    assert all(c.floor >= c.your_value + rules.dealer_sell_min_surplus for c in found)
    team = _Team(me=ME_DUP)
    m, _ = maker(tmp_path, team, live=True, dealer_sell_enabled=True, protect_complete_pages_only=True)
    m.sell_desk.hunt = True
    m.on_tick(clock())
    sold = [s for s in team.sent if s[0] == "open_thread"]
    only = {int(a["id"]) for a in ME_DUP["assets"] if a.get("ref") in ("LAV-01", "LAV-06", "LAT-09")}
    assert all(s[2]["sell"]["assets"][0] not in only for s in sold)  # a duplicate, never an only copy


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


def test_the_hunt_desk_takes_one_more_thread_keeps_the_dealer_reserve_and_never_the_servers_six(tmp_path):
    rules = Guardrails()
    max_open, reserve = card_hunt.desk_limits(rules)
    assert max_open == max(rules.team_threads_max_open, card_hunt.DESK_MAX_OPEN) <= rules.team_threads_max_open + 1
    assert reserve == rules.team_threads_dealer_reserve  # the ladder probes keep their conversations
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


def test_the_hunt_probes_an_empty_ladder_slot_without_the_gate_and_reads_at_most_one_value_per_tick(tmp_path):
    from tests.test_ladder_probe import DEALERS as PROBE_DEALERS
    from tests.test_ladder_probe import PARAMS as PROBE_PARAMS
    from tests.test_ladder_probe import ValueTeam, gate, probe_rows

    team = ValueTeam()
    g, asks = gate("no")  # the gate would refuse: the hunt never asks it
    kw = parts(tmp_path)
    kw["params"] = lambda tick: PROBE_PARAMS.model_copy(update={"min_buy_surplus": 1000})
    t = Taker(
        team,
        FakePublic(dealers=PROBE_DEALERS),
        live=False,
        log=[].append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=1, card_hunt=True),
        strategy_gate=g,
        **kw,
    )
    t.on_tick(clock())
    assert asks == [] and len(probe_rows(tmp_path)) == 1
    assert len(team.value_reads) <= 1  # one dealer per tick: one official value read at most
