"""Synthetic rival teams: a market for our taker and maker to trade against.

Each rival is an ordinary team in the world (cash, cards, private affinity) with no key. Every tick,
deterministically from the world seed, it may:
- take one good offer on El Rastro: a card it is missing at or below what it is worth to it, or a
  bid for a duplicate it holds at a premium over what the copy is worth to it (player offers first);
- list a duplicate, or bid for a missing page card, keeping a few of each open;
- haggle with Abuela for a missing common (bids up one prima a tick, accepts her ask within 9 P);
- answer a team thread: accept a structured offer that is good for it, else counter once a tick.
Everything goes through the same rule-checked actions a player's request does.
"""

from __future__ import annotations

from typing import Any

from bazaar_sim import catalog, market, threads
from bazaar_sim.errors import SimError
from bazaar_sim.models import Offer, Team, Thread
from bazaar_sim.world import World

TAKE_PREMIUM = 1.15  # a rival pays up to this over its own value for a card it is missing
SELL_PREMIUM = 1.2  # and sells a duplicate for at least this over the copy's value to it
MAX_LISTINGS = 3
MAX_BIDS = 2
LISTING_TICKS = 20
ABUELA_MAX = 9
ABUELA_EVERY = 25


def on_tick(w: World) -> None:
    for team in [t for t in w.state.teams.values() if t.bot]:
        for act in (_take, _answer_threads, _haggle, _list_duplicate, _bid_missing):
            try:
                act(w, team)
            except SimError:
                continue  # a refused move costs a rival nothing, exactly like a player


def _value_to(w: World, team: Team, ref: str) -> float:
    return catalog.one_more_value(ref, w.held_counts(team.id).get(ref, 0), team.affinity)


def _good_for(w: World, team: Team, offer: Offer) -> bool:
    """Would this rival gain by accepting the whole offer, at its private values?"""
    counts = w.held_counts(team.id)
    fee = market.venue_fee(w, offer.venue, offer.give.cash or offer.want.cash, 1)
    if offer.want.cash and not offer.give.cash and len(offer.give.assets) == 1 and not offer.want.types:
        ref = w.asset(offer.give.assets[0]).ref
        worth = catalog.one_more_value(ref, counts.get(ref, 0), team.affinity)
        return (
            counts.get(ref, 0) == 0
            and offer.want.cash + fee <= worth * TAKE_PREMIUM
            and team.cash > offer.want.cash + fee
        )
    if offer.give.cash and len(offer.want.types) == 1 and not offer.want.cash and not offer.give.assets:
        ref = offer.want.types[0].partition(":")[2]
        held = counts.get(ref, 0)
        if held == 0:
            return False
        worth = catalog.held_copy_value(ref, held, team.affinity)
        floor = catalog.cards()[ref].book * 0.6
        return offer.give.cash - fee >= max(floor, worth * SELL_PREMIUM) and team.cash >= fee
    return False


def _take(w: World, team: Team) -> None:
    if w.used(team.id, "accepts") >= w.limit("accepts_per_team_per_tick"):
        return
    board = [
        o
        for o in w.state.offers.values()
        if o.status == "open"
        and o.venue == "rastro"
        and o.thread is None
        and o.maker != team.id
        and o.to in (None, team.id)
    ]
    board.sort(key=lambda o: (w.state.teams[o.maker].bot if o.maker in w.state.teams else True, o.id))
    for offer in board:
        if _good_for(w, team, offer):
            market.accept(w, team.id, offer.id, {})
            return


def _open_listings(w: World, team: Team) -> list[Offer]:
    return [o for o in w.state.offers.values() if o.maker == team.id and o.status == "open" and o.thread is None]


def _list_duplicate(w: World, team: Team) -> None:
    rng = w.rng("rival-list", team.id)
    if rng.random() > 0.25:
        return
    mine = _open_listings(w, team)
    listed = {a for o in mine for a in o.give.assets}
    if sum(1 for o in mine if o.give.assets) >= MAX_LISTINGS:
        return
    counts = w.held_counts(team.id)
    spare = [a for a in w.holdings(team.id) if a.kind == "card" and counts[a.ref] > 1 and a.id not in listed]
    if not spare:
        return
    asset = rng.choice(spare)
    book = catalog.cards()[asset.ref].book
    ask = max(2, round(book * rng.uniform(1.1, 1.6)))
    body = {"venue": "rastro", "give": {"assets": [asset.id]}, "want": {"cash": ask}, "expires_in_ticks": LISTING_TICKS}
    market.offer_from_input(w, team.id, body)


def _bid_missing(w: World, team: Team) -> None:
    rng = w.rng("rival-bid", team.id)
    if rng.random() > 0.15:
        return
    bids = [o for o in _open_listings(w, team) if o.give.cash]
    if len(bids) >= MAX_BIDS:
        return
    counts = w.held_counts(team.id)
    wanted = {t.partition(":")[2] for o in bids for t in o.want.types}
    best_sets = sorted(catalog.released_sets(), key=lambda s: -team.affinity.get(s, 1.0))[:2]
    missing = [
        c.ref for s in best_sets for c in catalog.page_cards(s) if counts.get(c.ref, 0) == 0 and c.ref not in wanted
    ]
    if not missing:
        return
    ref = rng.choice(missing)
    bid = max(1, round(_value_to(w, team, ref) * rng.uniform(0.5, 0.8)))
    if bid >= team.cash - 50:
        return
    body = {"venue": "rastro", "give": {"cash": bid}, "want": {"cards": [ref]}, "expires_in_ticks": LISTING_TICKS}
    market.offer_from_input(w, team.id, body)


def _haggle(w: World, team: Team) -> None:
    """One cheap Abuela conversation at a time: the feed then shows dealer traffic, like the real one."""
    th = next(
        (t for t in w.state.threads.values() if t.team == team.id and t.with_ == "abuela" and t.status == "open"), None
    )
    if th is None:
        if (w.tick + int(team.id[1:])) % ABUELA_EVERY == 0 and team.cash > 100:
            counts = w.held_counts(team.id)
            commons = [c.ref for s in catalog.released_sets() for c in catalog.page_cards(s) if c.rarity == "common"]
            missing = [r for r in commons if counts.get(r, 0) == 0]
            if missing:
                ref = w.rng("rival-abuela", team.id).choice(missing)
                threads.open_thread(w, team.id, {"with": "abuela", "topic": {"buy": {"card": ref}}})
        return
    last = th.messages[-1] if th.messages else None
    if last is None or last.sender != "abuela":
        return
    ask_offer = w.state.offers.get(last.offer) if last.offer is not None else None
    if ask_offer is not None and ask_offer.status == "open" and ask_offer.want.cash <= ABUELA_MAX:
        market.accept(w, team.id, ask_offer.id, {})
        return
    if ask_offer is None:
        return
    bids = [w.state.offers[m.offer].give.cash for m in th.messages if m.sender == team.id and m.offer in w.state.offers]
    nxt = (bids[-1] + 1) if bids else 6
    if nxt > ABUELA_MAX:
        threads.close_thread(w, team.id, th.id)
        return
    threads.say(w, team.id, th.id, {"text": "Gracias, señora. ¿Le parece bien?", "price": nxt})


def _answer_threads(w: World, team: Team) -> None:
    for th in [
        t for t in w.state.threads.values() if t.kind == "team" and t.status == "open" and team.id in (t.team, t.with_)
    ]:
        _answer(w, team, th)


def _answer(w: World, team: Team, th: Thread) -> None:
    last = th.messages[-1] if th.messages else None
    if last is None or last.sender == team.id or last.offer is None:
        return
    offer = w.state.offers.get(last.offer)
    if offer is None or offer.status != "open" or offer.to != team.id:
        return
    if _good_for(w, team, offer) and w.used(team.id, "accepts") < w.limit("accepts_per_team_per_tick"):
        market.accept(w, team.id, offer.id, {})
        threads.post_message(w, th, team.id, "Deal.", None, public_text=False)
        return
    counter = _counter(w, team, offer)
    if counter is not None and w.used(team.id, f"msg:{th.id}") == 0:
        threads.say(w, team.id, th.id, {"text": "We can meet you here.", "offer": counter})


def _counter(w: World, team: Team, offer: Offer) -> dict[str, Any] | None:
    """Same items, the rival's price: halfway between the offer and what the item is worth to it."""
    if offer.want.cash and len(offer.give.assets) == 1:
        ref = w.asset(offer.give.assets[0]).ref
        worth = _value_to(w, team, ref)
        price = max(1, round((offer.want.cash + worth) / 2))
        if price >= offer.want.cash or price > team.cash:
            return None
        return {"give": {"cash": price}, "want": {"assets": list(offer.give.assets)}}
    return None
