"""The simulator's El Taller (`POST /api/taller`, our model of `/api/levels` `taller`): three cards of one rarity for
one card of the next, never the team's last copy of a card."""

from bazaar_sim import catalog, market
from tests.simkit import manual_world
from tests.test_sim_world import US, refused


def give(w, ref, n):
    return [w.mint(ref, US, "test").id for _ in range(n)]


def unheld_common(w):
    released = catalog.released_sets()
    cards = catalog.cards().values()
    return next(
        c.ref for c in cards if c.rarity == "common" and c.set_code in released and not w.held_counts(US)[c.ref]
    )


def test_three_spare_commons_become_one_uncommon_and_the_team_keeps_one_of_each():
    w = manual_world().world
    ref = unheld_common(w)
    ids = give(w, ref, 4)
    out = market.taller(w, US, ids[:3])
    assert catalog.cards()[out["card"]["ref"]].rarity in ("uncommon", "common")  # a sold-out rarity falls back
    assert w.held_counts(US)[ref] == 1
    assert all(w.asset(i).owner == "taller" for i in ids[:3])
    assert any(e.type == "taller.used" for e in w.state.events)


def test_the_last_copy_mixed_rarities_and_bad_shapes_are_refused():
    w = manual_world().world
    ref = unheld_common(w)
    ids = give(w, ref, 3)
    refused("keep_one", market.taller, w, US, ids)  # every copy we hold: one must stay
    refused("invalid", market.taller, w, US, ids[:2])
    refused("invalid", market.taller, w, US, [ids[0], ids[0], ids[1]])
    rare = next(c.ref for c in catalog.cards().values() if c.rarity == "rare")
    mixed = ids[:2] + give(w, rare, 2)[:1]
    refused("invalid", market.taller, w, US, mixed)
