"""A set released mid-game (RET on Saturday, CHA on Sunday, B26): the catalog lists it before release with every
card at zero minted; what changes is its album page in /me and the minted counts. No agent may crash or go blind."""

from __future__ import annotations

import re
from copy import deepcopy

from bazaar_agent import strategy
from bazaar_agent.album import album_view
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, RULES


def released_ret(me: dict) -> dict:
    """/me the tick after RET is released: its album page appears (0 of 1), nothing else changes."""
    me = deepcopy(me)
    me["album"]["pages"].append({"set": "RET", "name": "El Retiro", "have": 0, "of": 1, "complete": False})
    return me


def test_before_the_release_the_new_set_is_invisible_and_after_it_appears_everywhere():
    before = strategy.build_market(ME, CATALOG, EVENTS, DEALERS)
    after = strategy.build_market(released_ret(ME), CATALOG, EVENTS, DEALERS)
    assert "RET" not in before.released and "RET" in after.released
    assert "RET" not in {p.set_code for p in album_view(ME, CATALOG)}
    ret = next(p for p in album_view(released_ret(ME), CATALOG) if p.set_code == "RET")
    assert [c.ref for c in ret.missing] == ["RET-01"]
    assert any(s.ref == "RET-01" for s in strategy.supply_view(after, PARAMS))


def test_a_new_cards_zero_minted_copies_are_a_dealer_buy_only_with_dealer_mints_unminted():
    me = released_ret(ME)
    me["affinity"]["RET"] = 1.6  # a team chasing the new set
    m = strategy.build_market(me, CATALOG, EVENTS, DEALERS)
    card = m.cards["RET-01"]
    assert card.minted == 0
    assert strategy.supply_of(m, card, PARAMS).availability == "packs"  # today: "pull or wait" (Abuela's pack)
    assert isinstance(strategy.buy_move(m, card, PARAMS, RULES), str)
    mints = PARAMS.model_copy(update={"dealer_mints_unminted": True})
    supply = strategy.supply_of(m, card, mints)
    assert supply.availability == "dealer" and not supply.scarce  # Abuela mints released commons on demand
    assert strategy.buy_case(m, card, mints).urgency < strategy.buy_case(m, card, PARAMS).urgency
    move = strategy.buy_move(m, card, mints, RULES)
    assert not isinstance(move, str) and move.ref == "RET-01"


def test_a_page_the_catalog_does_not_know_yet_is_skipped_without_a_crash():
    stale = deepcopy(CATALOG)
    stale["sets"] = [s for s in stale["sets"] if s["id"] != "RET"]
    me = released_ret(ME)
    assert "RET" not in {p.set_code for p in album_view(me, stale)}
    m = strategy.build_market(me, stale, EVENTS, DEALERS)
    assert "RET" in m.released and not any(c.set_code == "RET" for c in m.cards.values())
    moves, _ = strategy.buy_moves(m, PARAMS, RULES)
    assert all(not mv.ref.startswith("RET-") for mv in moves)


def test_the_strategy_file_parses_the_switch_either_way_and_defaults_off_when_absent():
    text = strategy.STRATEGY_FILE.read_text(encoding="utf-8")
    assert "`dealer_mints_unminted`" in text
    on = re.sub(r"(`dealer_mints_unminted` = )\w+", r"\1true", text)
    off = re.sub(r"(`dealer_mints_unminted` = )\w+", r"\1false", text)
    assert strategy.parse_strategy(on).params.dealer_mints_unminted is True
    assert strategy.parse_strategy(off).params.dealer_mints_unminted is False
    absent = "\n".join(line for line in text.splitlines() if "`dealer_mints_unminted`" not in line)
    assert strategy.parse_strategy(absent).params.dealer_mints_unminted is False
