"""PL1: pack acquisition buys resale inventory, not private holding value."""

from copy import deepcopy
from dataclasses import replace

import pytest

from bazaar_agent import strategy
from bazaar_agent.agents.taker import TakerConfig
from bazaar_agent.guardrails import Action, Context, Guardrails, check, load_guardrails
from bazaar_agent.pack_gate import gate_packs
from bazaar_agent.pack_open import SealedPack, choose
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock
from tests.test_intel import settle
from tests.test_maker import maker, posted
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, market
from tests.test_taker import at, dealer_ask, her, taker

RULES = Guardrails(pack_restock_enabled=True, max_price_pack=22, cash_floor=5, venue_bond_reserve=0)
PARAMETERS = PARAMS.model_copy(update={"pack_price_estimate": 22})


def low_value_book(rules=RULES, *, catalog=CATALOG, dealers=DEALERS):
    me = deepcopy(ME)
    me["affinity"] = {"LAV": 13.3 / 16.25, "LAT": 13.3 / 16.25}
    return strategy.build_playbook(me, catalog, EVENTS, dealers, PARAMETERS, rules)


def test_low_holding_value_does_not_veto_explicit_restock():
    book = low_value_book()
    move = next(m for m in book.packs if m.ref == "sobre_barrio")
    assert move.value == 13.3 and move.surplus < 0  # never disguise holding loss as expected score
    assert move.strategy == "pack_restock" and move.command and move.limit == 22
    assert move.ladder and move.ladder[1] == 22
    assert "resale and score not guaranteed" in move.reason
    calls = []
    gated = gate_packs(book, lambda state: (calls.append(state) or "no", 0.99), strategy.PackSlots(0, 3), {}, RULES, 1)
    assert gated.packs[0].command and calls == []
    off = low_value_book(RULES.model_copy(update={"pack_restock_enabled": False}))
    assert not next(m for m in off.packs if m.ref == move.ref).command


@pytest.mark.parametrize("used,quota", [(3, 3), (1, 1)])
def test_restock_still_respects_shared_and_dealer_pack_quota(used, quota):
    book = replace(low_value_book(), pack_quotas={"sobre_barrio": quota})
    result = gate_packs(book, None, strategy.PackSlots(used, 3), {"sobre_barrio": used}, RULES, 1)
    assert not next(m for m in result.packs if m.ref == "sobre_barrio").command


def test_unavailable_or_fully_exhausted_packs_are_not_actionable():
    assert not any(m.command for m in low_value_book(dealers=[]).packs)
    catalog = deepcopy(CATALOG)
    for page in catalog["sets"]:
        for card in page["cards"]:
            card["minted"] = card["print_run"]
    assert not any(m.command for m in low_value_book(catalog=catalog).packs)


def test_restock_ranks_immediately_tradable_pulls_not_holding_ev():
    m = market()
    m = replace(
        m,
        packs={"spares": ({"common": 1.0},), "rares": ({"rare": 1.0},)},
        quotes=(strategy.Quote("abuela", "spares", 22, None), strategy.Quote("abuela", "rares", 22, None)),
    )
    moves = strategy.pack_moves(m, PARAMETERS, RULES)
    assert moves[0].ref == "spares" and moves[0].value < moves[1].value
    assert moves[0].score > moves[1].score


def test_restock_does_not_remove_cash_floor_price_or_sale_protections():
    ctx = Context(cash=26, held={"SAL-07": 1, "LAV-01": 2}, tick=1810, t_hours=15)
    assert not check(Action("buy", "sobre_barrio", "pack", 22), ctx, RULES).allowed
    assert not check(Action("buy", "sobre_barrio", "pack", 23), replace(ctx, cash=500), RULES).allowed
    protected = RULES.model_copy(update={"protect_page_sets": "SAL,LAV"})
    assert not check(Action("sell", "SAL-07", "uncommon", 29, 118.6), ctx, protected).allowed
    assert not check(Action("sell", "LAV-01", "common", 1, 4), ctx, protected).allowed


def test_restock_opening_ignores_historical_sealed_price_but_not_unknown_pack():
    m = market()
    print_ = strategy.intel.tape([settle(90, 90, "t02", "t03", "sobre_barrio", 200, tick=90, persona=None)])[0]
    m = replace(m, prints=(print_,))
    pack = SealedPack(10, "sobre_barrio")
    assert choose(m, pack, PARAMETERS).verdict == "keep"
    assert choose(m, pack, PARAMETERS, restock=True).verdict == "open"
    assert choose(m, SealedPack(11, "unknown"), PARAMETERS, restock=True).verdict == "keep"


def test_taker_buys_opens_then_maker_lists_duplicate_for_guarded_resale(tmp_path):
    class RestockTeam(FakeTeam):
        def open_pack(self, asset_id):
            self.sent.append(("open_pack", asset_id))
            self._me["assets"] = [a for a in self._me["assets"] if a["id"] != asset_id]
            pulled = {"id": 777, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 4.0}
            self._me["assets"].append(pulled)
            return {"cards": [pulled]}

    team = RestockTeam()
    team._me["assets"] = [a for a in team._me["assets"] if a["kind"] != "pack"]
    t, _, _ = taker(
        tmp_path,
        team,
        FakePublic(),
        live=True,
        config=TakerConfig(max_dealer_threads=1),
        pack_restock_enabled=True,
        open_sealed_packs=True,
        max_price_pack=30,
        cash_floor=5,
        venue_bond_reserve=0,
    )
    t.on_tick(clock(next_tick_in=5))  # too short for an LLM; sufficient for the deterministic send
    assert ("open_thread", "abuela", {"buy": {"pack": "sobre_barrio"}}) in team.sent
    assert t.pack_judge is None
    opening = dealer_ask(799, 30)
    opening["give"] = {"types": ["pack:sobre_barrio"]}
    her(team, 5000, opening)
    t.on_tick(at(team, TICK + 1))
    offer = dealer_ask(800, 24, final=True)
    offer["give"] = {"types": ["pack:sobre_barrio"]}
    her(team, 5000, offer)
    t.on_tick(at(team, TICK + 2))
    assert ("accept", 800) in team.sent
    # The fake server settles next tick; only then can fresh /me reveal the acquired pack.
    team._me["cash"] -= 24
    team._me["assets"].append({"id": 6, "kind": "pack", "ref": "sobre_barrio"})
    settled = {**offer, "status": "settled"}
    her(team, 5000, settled, status="deal")
    t.on_tick(at(team, TICK + 3))
    assert ("open_pack", 6) in team.sent
    team.reads.clear()
    m, _ = maker(
        tmp_path, team, live=True, cash_floor=5, venue_bond_reserve=0, protect_page_sets="LAV,SAL,MAL,RET,LAT,CHA"
    )
    m.on_tick(at(team, TICK + 4))
    listed = [p for p in posted(team) if p[1].get("assets") == [777]]
    assert listed and listed[0][2]["cash"] > 4.0
    assert "me" in team.reads  # sale uses refreshed inventory and its actual server your_value


def test_taker_restock_respects_expired_tick(tmp_path):
    team = FakeTeam()
    t, _, _ = taker(
        tmp_path,
        team,
        FakePublic(),
        live=True,
        config=TakerConfig(max_dealer_threads=1),
        pack_restock_enabled=True,
        max_price_pack=22,
    )
    t.on_tick(clock(next_tick_in=1))
    assert team.sent == []


def test_deployed_policy_is_explicit_and_quota_stays_three():
    rules = load_guardrails().rules
    assert not rules.pack_restock_enabled and not rules.dealer_sell_enabled
    assert rules.max_packs_per_game_hour == 3 and rules.cash_floor > 0
    ctx = Context(cash=rules.max_price_pack + rules.cash_floor + 1, held={}, tick=100, t_hours=1, has_venue=True)
    at_cap = Action("buy", "sobre_barrio", "pack", rules.max_price_pack)
    assert check(at_cap, ctx, rules).allowed
    assert not check(replace(at_cap, price=rules.max_price_pack + 1), ctx, rules).allowed
    assert not check(at_cap, replace(ctx, packs_last_hour=rules.max_packs_per_game_hour), rules).allowed
    assert not check(at_cap, replace(ctx, cash=rules.max_price_pack + rules.cash_floor - 1), rules).allowed


@pytest.mark.parametrize("pending", [False, True])
def test_restock_does_not_open_pack_promised_in_offer_or_unknown_publication(tmp_path, pending):
    from bazaar_agent.agents import publication

    class PromisedPackTeam(FakeTeam):
        def open_pack(self, asset_id):
            self.sent.append(("open_pack", asset_id))
            return {"cards": []}

    team = PromisedPackTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, pack_restock_enabled=True, open_sealed_packs=True)
    if pending:
        publication.reserve(ledger, TICK, clock().t_hours, "t01", {"assets": [6]}, {"cash": 22})
    else:
        team.offers = [
            {
                "id": 800,
                "maker": "t01",
                "status": "open",
                "venue": "rastro",
                "give": {"assets": [{"id": 6, "kind": "pack", "ref": "sobre_barrio"}]},
                "want": {"cash": 22},
            }
        ]
    t.on_tick(clock())
    assert not any(sent[0] == "open_pack" for sent in team.sent)


@pytest.mark.parametrize("synthetic", [False, True])
def test_pack_snapshot_check_never_releases_unknown_promises(tmp_path, monkeypatch, synthetic):
    from bazaar_agent.agents import publication

    team = FakeTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, pack_restock_enabled=True, open_sealed_packs=True)
    publication.reserve(ledger, TICK, clock().t_hours, "t01", {"assets": [6]}, {"cash": 22})
    # Another worker may have read newer holdings than this tick's snapshot. Absence is no release proof.
    publication.reserve(ledger, TICK, clock().t_hours, "t01", {"assets": [777], "cash": 30}, {"cards": ["LAV-02"]})
    before = ledger.publication_rows()
    if synthetic:

        def snapshot(run, threads):
            run.offers.append(
                {
                    "id": -99,
                    "maker": "t01",
                    "status": "open",
                    "publication_pending": True,
                    "give": {"assets": [{"id": 6, "ref": "sobre_barrio"}]},
                    "want": {"cash": 22},
                    "thread": None,
                    "to": None,
                    "created_tick": TICK,
                }
            )

        monkeypatch.setattr(t, "_workshop", snapshot)
    t.on_tick(clock())
    assert ledger.publication_rows() == before
    assert not any(sent[0] == "open_pack" for sent in team.sent)
