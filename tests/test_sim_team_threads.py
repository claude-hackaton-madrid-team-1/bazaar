"""The simulator's team-to-team threads (N17-1): rival bots judge and counter swaps at their private values,
a conversation between two teams ends after 200 messages, and a rival can open a silent inbound thread."""

from dataclasses import replace

import pytest

from bazaar_sim import catalog, market, rivals, threads
from bazaar_sim.errors import SimError
from bazaar_sim.models import Team
from bazaar_sim.world import World
from tests.simkit import QUIET, manual_world

US = "t01"


def _swap_pairs(w: World, rival: Team) -> list[tuple[float, int, str]]:
    """(the rival's gain at its values, our duplicate's asset id, a card it holds twice) for every swap of one
    of our duplicates it misses against one of its duplicates we miss, best first."""
    ours, theirs = w.held_counts(US), w.held_counts(rival.id)
    dups = {a.ref: a.id for a in w.holdings(US) if a.kind == "card" and ours[a.ref] > 1 and theirs[a.ref] == 0}
    spare = [ref for ref, n in theirs.items() if n > 1 and ours[ref] == 0]
    pairs = [
        (
            catalog.one_more_value(d, 0, rival.affinity) - catalog.held_copy_value(m, theirs[m], rival.affinity),
            aid,
            m,
        )
        for d, aid in dups.items()
        for m in spare
    ]
    return sorted(pairs, reverse=True)


def _best_swap(w: World) -> tuple[Team, float, int, str]:
    found = [(r, *p) for r in w.state.teams.values() if r.bot for p in _swap_pairs(w, r)[:1]]
    assert found, "the seed deals no swap between us and a rival"
    return max(found, key=lambda x: x[1])


def test_a_rival_accepts_a_swap_that_gains_it_value():
    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    rival, gain, ours, theirs = _best_swap(w)
    assert gain > rivals.SWAP_MIN_GAIN + 2  # 2: El Rastro's 1 P per card, two cards
    held_before = w.held_counts(US)[theirs]
    th = threads.open_thread(w, US, {"with": rival.id})
    threads.say(w, US, th.id, {"text": "swap?", "offer": {"give": {"assets": [ours]}, "want": {"cards": [theirs]}}})
    m.step(2)  # the rival accepts on its tick, the deal settles on the next one
    assert w.asset(ours).owner == rival.id and w.held_counts(US)[theirs] == held_before + 1
    assert w.state.threads[th.id].status == "deal"


def test_a_rival_counters_a_swap_short_of_its_value_with_a_cash_ask():
    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    rival = next(r for r in w.state.teams.values() if r.bot)
    held = w.held_counts(rival.id)
    # bad for the rival: a card it already holds (one more copy is worth little) for its ONLY copy of its best card
    ours = next(a.id for a in w.holdings(US) if a.kind == "card" and held[a.ref] > 0)
    theirs = max(
        (ref for ref, n in held.items() if n == 1 and catalog.card(ref) is not None),
        key=lambda ref: catalog.held_copy_value(ref, 1, rival.affinity),
    )
    probe = market.offer_from_input(w, US, {"give": {"assets": [ours]}, "want": {"cards": [theirs]}, "to": rival.id})
    gain = rivals._swap_gain(w, rival, probe)
    market.cancel(w, US, probe.id)  # only to read the gain: the swap itself goes through the thread
    assert gain is not None and gain < rivals.SWAP_MIN_GAIN
    th = threads.open_thread(w, US, {"with": rival.id})
    threads.say(w, US, th.id, {"text": "swap?", "offer": {"give": {"assets": [ours]}, "want": {"cards": [theirs]}}})
    m.step()
    reply = w.state.threads[th.id].messages[-1]
    assert reply.sender == rival.id and reply.offer is not None
    counter = w.state.offers[reply.offer]
    assert counter.to == US and counter.want.assets == [ours] and counter.want.cash > 0 and not counter.give.cash
    assert [w.asset(a).ref for a in counter.give.assets] == [theirs]
    market.accept(w, US, counter.id, {})
    m.step()
    assert w.asset(ours).owner == rival.id and w.state.threads[th.id].status == "deal"


def test_a_swap_dressed_as_a_sale_is_judged_as_a_swap():
    # A sale for 1 P that also wants the rival's best card is not a sale: the rival weighs what it gives.
    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    rival = next(r for r in w.state.teams.values() if r.bot)
    theirs = w.held_counts(rival.id)
    ours = w.held_counts(US)
    gift = next(
        a for a in w.holdings(US) if a.kind == "card" and theirs[a.ref] == 0 and ours[a.ref] > 1
    )  # a copy it is missing: as a plain sale for 1 P, it would take it
    best = max(
        (a for a in w.holdings(rival.id) if a.kind == "card"),
        key=lambda a: catalog.held_copy_value(a.ref, theirs[a.ref], rival.affinity),
    )
    offer = market.offer_from_input(
        w, US, {"give": {"assets": [gift.id]}, "want": {"cash": 1, "assets": [best.id]}, "to": rival.id}
    )
    worth_in = catalog.one_more_value(gift.ref, 0, rival.affinity)
    worth_out = catalog.held_copy_value(best.ref, theirs[best.ref], rival.affinity)
    assert worth_out > worth_in  # the trap: the rival would lose value
    assert not rivals._good_for(w, rival, offer)


def test_a_team_conversation_ends_after_200_messages():
    m = manual_world()
    w = m.world
    th = threads.open_thread(w, US, {"with": "t02"})
    for i in range(199):
        threads.post_message(w, th, US if i % 2 else "t02", f"m{i}", None, public_text=False)
    assert w.state.threads[th.id].status == "open"
    threads.say(w, US, th.id, {"text": "the 200th"})
    assert (w.state.threads[th.id].status, w.state.threads[th.id].closed_reason) == ("closed", "message_cap")
    with pytest.raises(SimError) as e:
        m.step()
        threads.say(w, US, th.id, {"text": "one more"})
    assert e.value.code == "thread_closed"


def test_a_rival_opens_one_silent_inbound_thread_only_when_enabled():
    off = manual_world(replace(QUIET, rivals=2))
    off.step(3)
    assert not [t for t in off.world.state.threads.values() if t.kind == "team" and t.with_ == US]
    on = manual_world(replace(QUIET, rivals=2, rival_inbound_threads=True))
    on.step(3)
    inbound = [t for t in on.world.state.threads.values() if t.kind == "team" and t.with_ == US]
    assert len(inbound) == 1 and inbound[0].status == "open" and inbound[0].messages == []
    assert on.world.team(inbound[0].team).bot
