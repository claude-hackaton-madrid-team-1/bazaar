"""Structured offers: post, cancel, accept, and settlement on the next tick (all at once or not at all).

Words persuade, structure binds: only an accepted offer moves cards or cash. An offer above your
cash is refused at once (`insufficient_cash`); an asset already promised in an accepted offer is
`asset_locked`; you cannot trade on your own venue (`self_venue`). The accepting side pays the
venue fee (El Rastro: 5 % + 1 P per card). Dealer deals pay no fee and mint the item sold.
"""

from __future__ import annotations

from typing import Any

from bazaar_sim import catalog, threads, validate
from bazaar_sim.errors import SimError, invalid, not_found, wait_for_tick
from bazaar_sim.models import Asset, DealRecord, Offer, Side
from bazaar_sim.views import asset_name, offer_view
from bazaar_sim.world import World

DEFAULT_EXPIRES = 40
SETTLEMENT_TAPE = 50  # recent settlements a broker book shows


def is_dealer(party: str) -> bool:
    return party in catalog.raw_dealers()


def create_offer(
    w: World,
    maker: str,
    to: str | None,
    venue: str | None,
    thread: int | None,
    give: Side,
    want: Side,
    expires_in: int,
    *,
    final: bool = False,
) -> Offer:
    offer = Offer(
        id=w.next_id("offer"),
        maker=maker,
        to=to,
        venue=venue,
        thread=thread,
        give=give,
        want=want,
        expires_tick=w.tick + expires_in,
        created_tick=w.tick,
        final=final,
    )
    w.state.offers[offer.id] = offer
    return offer


def locked_assets(w: World) -> set[int]:
    """Assets promised by an accepted offer that has not settled yet."""
    out: set[int] = set()
    for o in w.state.offers.values():
        if o.status == "accepted":
            out |= set(o.give.assets) | set(o.want.assets) | set(o.accept_assets)
    return out


def venue_fee(w: World, venue: str | None, cash: int, cards: int) -> int:
    v = w.state.venues.get(venue) if venue else None
    if v is None:
        return 0
    return round(cash * v.fee_bps / 10_000) + v.fee_per_card * cards


def _check_venue(w: World, maker: str, venue: str) -> None:
    v = w.state.venues.get(venue)
    if v is None:
        raise not_found(f"venue {venue}")
    if v.status != "open" or w.tick < v.live_tick:
        raise SimError("venue_not_live", f"venue {venue} is not trading yet", 400)
    if v.owner == maker:
        raise SimError("self_venue", "you cannot trade on your own venue with your team key", 403)


def _check_owned(w: World, team_id: str, ids: list[int]) -> None:
    locked = locked_assets(w)
    for aid in ids:
        asset = w.asset(aid)
        if asset.owner != team_id:
            raise SimError("not_owner", f"asset {aid} is not yours", 403)
        if aid in locked:
            raise SimError("asset_locked", f"asset {aid} is promised in an accepted offer", 400)


def offer_from_input(w: World, maker: str, body: dict[str, Any], *, thread: int | None = None) -> Offer:
    """`POST /api/offers` (and a team thread's `offer`): validate, then list it."""
    venue = body.get("venue") or "rastro"
    if not isinstance(venue, str):
        raise invalid("venue must be a venue id")
    _check_venue(w, maker, venue)
    to = body.get("to")
    if to is not None and (not isinstance(to, str) or to not in w.state.teams or to == maker):
        raise invalid("`to` must be another team's id")
    give_cash, give_assets, give_cards = validate.side_input(body.get("give"), "give")
    want_cash, want_assets, want_cards = validate.side_input(body.get("want"), "want")
    if give_cards:
        raise invalid("give.cards is not a thing: give the asset ids you own")
    if give_cash and want_cash:
        raise invalid("cash on both sides")
    if not (give_cash or give_assets) or not (want_cash or want_assets or want_cards):
        raise invalid("an offer gives something and wants something")
    for ref in want_cards:
        if catalog.card(ref) is None:
            raise invalid(f"unknown card {ref}")
    for aid in want_assets:
        w.asset(aid)
    team = w.team(maker)
    if give_cash > team.cash:
        raise SimError("insufficient_cash", f"you have {team.cash} P, not {give_cash}", 400)
    _check_owned(w, maker, give_assets)
    open_now = [o for o in w.state.offers.values() if o.maker == maker and o.status == "open" and o.thread is None]
    if thread is None and len(open_now) >= w.limit("max_open_offers_per_team"):
        raise SimError("too_many_offers", f"at most {w.limit('max_open_offers_per_team')} open offers", 400)
    cap = w.limit("offers_per_team_per_tick")
    if w.used(maker, "listings") >= cap:
        raise wait_for_tick(f"{cap} new listings per tick (a cancelled one still counts)", w.tick + 1)
    expires = validate.ticks(body.get("expires_in_ticks"), DEFAULT_EXPIRES)
    w.use(maker, "listings", cap, "listings per tick")
    offer = create_offer(
        w,
        maker,
        to,
        venue,
        thread,
        Side(cash=give_cash, assets=give_assets),
        Side(cash=want_cash, assets=want_assets, types=[f"card:{r}" for r in want_cards]),
        expires,
    )
    if thread is None:
        w.emit("offer.listed", {"venue": venue, "offer": offer_view(w, offer)}, actor=maker)
    return offer


def cancel(w: World, team_id: str, oid: int) -> dict[str, Any]:
    offer = w.state.offers.get(oid)
    if offer is None or offer.maker != team_id:
        raise not_found(f"offer {oid}")
    if offer.status == "accepted":
        raise SimError("offer_accepted", f"offer {oid} is accepted and settles next tick", 400)
    if offer.status == "open":
        offer.status = "cancelled"
        w.emit("offer.cancelled", {"offer": oid, "venue": offer.venue})
    return {"ok": True, "offer": oid, "status": offer.status}


def my_offers(w: World, team_id: str) -> dict[str, Any]:
    mine = [o for o in w.state.offers.values() if o.maker == team_id and o.status in ("open", "accepted")]
    to_us = [o for o in w.state.offers.values() if o.to == team_id and o.status == "open" and o.maker != team_id]
    return {"offers": [offer_view(w, o) for o in sorted(mine + to_us, key=lambda o: o.id)]}


# ---------------------------------------------------------------- accepting


def _pick_copies(w: World, team_id: str, types: list[str], chosen: list[int]) -> list[int]:
    """The accepter's copies for each wanted type: the ones named in the body, else the least valuable."""
    locked = locked_assets(w)
    picked: list[int] = []
    team = w.team(team_id)
    counts = w.held_counts(team_id)
    for t in types:
        kind, _, ref = t.partition(":")
        named = [a for a in chosen if a not in picked and w.asset(a).ref == ref and w.asset(a).owner == team_id]
        if named and named[0] in locked:
            raise SimError("asset_locked", f"asset {named[0]} is promised in an accepted offer", 400)
        if named:
            picked.append(named[0])
            continue
        mine = [
            a
            for a in w.holdings(team_id)
            if a.ref == ref and a.kind == kind and a.id not in locked and a.id not in picked
        ]
        if not mine:
            raise SimError("not_owner", f"you hold no free copy of {ref}", 403)
        cheapest = min(mine, key=lambda a: (catalog.held_copy_value(a.ref, counts[a.ref], team.affinity), -a.id))
        picked.append(cheapest.id)
    return picked


def accept(w: World, team_id: str, oid: int, body: dict[str, Any]) -> dict[str, Any]:
    offer = w.state.offers.get(oid)
    if offer is None:
        raise not_found(f"offer {oid}")
    if offer.status != "open" or offer.expires_tick < w.tick:
        raise SimError("offer_not_open", f"offer {oid} is {offer.status}", 400)
    if offer.maker == team_id:
        raise invalid("you cannot accept your own offer")
    if offer.to is not None and offer.to != team_id:
        raise SimError("not_addressed", f"offer {oid} is addressed to someone else", 403)
    if offer.venue is not None:
        venue = w.state.venues.get(offer.venue)
        if venue is not None and venue.owner == team_id:
            raise SimError("self_venue", "you cannot trade on your own venue with your team key", 403)
    cap = w.limit("accepts_per_team_per_tick")
    if w.used(team_id, "accepts") >= cap:
        raise wait_for_tick(f"{cap} accepted offer per team per tick", w.tick + 1)
    chosen = validate.int_list(body.get("assets"), "assets")
    team = w.team(team_id)
    cards = len(offer.give.assets) + len(offer.want.assets) + len(offer.want.types)
    fee = venue_fee(w, offer.venue, offer.give.cash or offer.want.cash, cards)
    if offer.want.cash + fee > team.cash:
        raise SimError("insufficient_cash", f"you need {offer.want.cash + fee} P, you have {team.cash}", 400)
    _check_owned(w, team_id, offer.want.assets)
    picks = _pick_copies(w, team_id, offer.want.types, chosen)
    w.use(team_id, "accepts", cap, "accepts per tick")
    offer.status = "accepted"
    offer.accepted_by = team_id
    offer.accepted_tick = w.tick
    offer.accept_assets = picks
    return {"ok": True, "offer": oid, "status": "accepted", "settles_tick": w.tick + 1}


# ---------------------------------------------------------------- settlement (start of every tick)


def settle_due(w: World) -> None:
    due = [o for o in w.state.offers.values() if o.status == "accepted" and (o.accepted_tick or 0) < w.tick]
    for offer in sorted(due, key=lambda o: (o.accepted_tick or 0, o.id)):
        if offer.accepted_by and offer.accepted_by.startswith("broker:"):
            continue  # a broker's crossed pair settles as one match below
        _settle(w, offer)
    matches, w.state.matches = w.state.matches, []
    for match in matches:
        _settle_match(w, match)


def settle_now(w: World, offer: Offer) -> None:
    """Settle one accepted offer at once (a dealer's own acceptance, made at the tick boundary)."""
    if offer.status == "accepted":
        _settle(w, offer)


def _fail(w: World, offer: Offer, reason: str) -> None:
    offer.status = "failed"
    w.emit("settlement.failed", {"offer": offer.id, "reason": reason})
    if offer.thread is not None:
        th = w.state.threads.get(offer.thread)
        if th is not None and th.status == "open":
            threads.end(w, th, "closed", reason)


def _payable(w: World, party: str, cash: int) -> bool:
    return is_dealer(party) or w.team(party).cash >= cash


def _move_cash(w: World, frm: str, to: str, cash: int) -> None:
    if cash <= 0:
        return
    if not is_dealer(frm) and frm != "world":
        w.team(frm).cash -= cash
    if not is_dealer(to) and to != "world" and to in w.state.teams:
        w.team(to).cash += cash


def _can_deliver(w: World, frm: str, side: Side, extra: list[int]) -> bool:
    """`frm` still holds every asset of the side, and a dealer can still mint every type it sells."""
    if any(not _owns_now(w, frm, a) for a in [*side.assets, *extra]):
        return False
    return not is_dealer(frm) or all(w.mintable(t.partition(":")[2]) for t in side.types)


def _deliverables(w: World, frm: str, side: Side, extra: list[int]) -> list[Asset]:
    """The assets `frm` hands over for a side; a dealer mints the items it sells."""
    out = [w.asset(a) for a in [*side.assets, *extra]]
    if is_dealer(frm):
        out += [w.mint(t.partition(":")[2], frm, f"stock of {frm}") for t in side.types]
    return out


def _settle(w: World, offer: Offer) -> None:
    maker, taker = offer.maker, str(offer.accepted_by)
    cards = len(offer.give.assets) + len(offer.want.assets) + len(offer.want.types)
    cash_price = offer.give.cash or offer.want.cash
    dealer = maker if is_dealer(maker) else taker if is_dealer(taker) else None
    fee = 0 if dealer else venue_fee(w, offer.venue, cash_price, cards)
    if not _payable(w, maker, offer.give.cash) or not _payable(w, taker, offer.want.cash + fee):
        return _fail(w, offer, "insufficient_cash")
    taker_extra = offer.accept_assets if not is_dealer(taker) else []
    if not _can_deliver(w, maker, offer.give, []) or not _can_deliver(w, taker, offer.want, taker_extra):
        return _fail(w, offer, "sold_out" if dealer else "not_owner")
    gains_before = _values_before(w, maker, taker, offer)
    from_maker = _deliverables(w, maker, offer.give, [])
    from_taker = _deliverables(w, taker, offer.want, taker_extra)
    why = f"deal with {dealer}" if dealer else f"trade on {offer.venue}"
    items = [_hand(w, a, taker, why) for a in from_maker] + [_hand(w, a, maker, why) for a in from_taker]
    _move_cash(w, maker, taker, offer.give.cash)
    _move_cash(w, taker, maker, offer.want.cash)
    if fee:
        owner = w.state.venues[str(offer.venue)].owner
        _move_cash(w, taker, owner, fee)
    offer.status = "settled"
    _cancel_stale(w, [a["id"] for a in items])
    _record(w, offer, maker, taker, dealer, cash_price, fee, items, gains_before)


def _owns_now(w: World, party: str, aid: int) -> bool:
    asset = w.state.assets.get(aid)
    return asset is not None and asset.owner == party


def _hand(w: World, asset: Asset, to: str, why: str) -> dict[str, Any]:
    frm = asset.owner
    w.transfer(asset, to, why)
    return {
        "id": asset.id,
        "kind": asset.kind,
        "ref": asset.ref,
        "serial": asset.serial,
        "frm": frm,
        "to": to,
        "name": asset_name(asset),
    }


def _values_before(w: World, maker: str, taker: str, offer: Offer) -> dict[str, float]:
    """Each team's private value of what it gives and gets, read before anything moves."""
    out: dict[str, float] = {}
    for team_id, gets, gives in ((maker, offer.want, offer.give), (taker, offer.give, offer.want)):
        if is_dealer(team_id):
            continue
        team, counts = w.team(team_id), w.held_counts(team_id)
        got_refs = [w.asset(a).ref for a in gets.assets if a in w.state.assets] + [
            t.partition(":")[2] for t in gets.types
        ]
        give_ids = [*gives.assets, *(offer.accept_assets if team_id == taker else [])]
        give_refs = [w.asset(a).ref for a in give_ids if a in w.state.assets]
        gained = sum(catalog.one_more_value(r, counts.get(r, 0), team.affinity) for r in got_refs if catalog.card(r))
        lost = sum(catalog.held_copy_value(r, counts.get(r, 1), team.affinity) for r in give_refs if catalog.card(r))
        out[team_id] = gained - lost + gets.cash - gives.cash
    return out


def _cancel_stale(w: World, moved: list[int]) -> None:
    for o in w.state.offers.values():
        if o.status == "open" and set(o.give.assets) & set(moved):
            o.status = "cancelled"
            if o.thread is None:
                w.emit("offer.cancelled", {"offer": o.id, "venue": o.venue})


def _record(
    w: World,
    offer: Offer,
    maker: str,
    taker: str,
    dealer: str | None,
    price: int,
    fee: int,
    items: list[dict[str, Any]],
    gains: dict[str, float],
) -> None:
    w.emit(
        "settlement",
        {
            "settlement": w.next_id("settlement"),
            "tick": w.tick,
            "kind": "trade",
            "parties": [maker, taker],
            "venue": offer.venue,
            "persona": dealer,
            "fee": fee,
            "items": items,
            "price": price,
        },
    )
    if dealer is not None:
        _dealer_deal(w, offer, dealer, maker if taker == dealer else taker, price)
        return
    for team_id, gain in gains.items():
        w.team(team_id).trade_gain = round(
            w.team(team_id).trade_gain + (gain - (fee if team_id == taker else 0)) / 10, 3
        )
    venue = w.state.venues.get(str(offer.venue))
    if venue is not None:
        venue.trades += 1
        venue.volume += price
        venue.fees += fee
        venue.traders = sorted({*venue.traders, maker, taker})
        venue.value_created += max(0.0, sum(gains.values()))
    if offer.thread is not None:
        th = w.state.threads.get(offer.thread)
        if th is not None and th.status == "open":
            th.status = "deal"
            th.closed_reason = "deal"


def _dealer_deal(w: World, offer: Offer, dealer: str, team_id: str, price: int) -> None:
    th = w.state.threads.get(offer.thread) if offer.thread is not None else None
    if th is None or th.neg is None:
        return
    neg = th.neg
    span = abs(neg.opening - neg.limit)
    captured = (neg.opening - price) if neg.side == "sell" else (price - neg.opening)
    share = 0.0 if span == 0 else max(0.0, min(1.0, captured / span))
    team = w.team(team_id)
    team.deals.append(
        DealRecord(
            dealer=dealer,
            item=neg.item,
            price=price,
            tick=w.tick,
            hour=w.hour,
            share=round(share, 3),
            negotiated=price != neg.opening,
        )
    )
    th.status = "deal"
    th.closed_reason = "deal"
    threads.maybe_unlock(w, team_id)


def _settle_match(w: World, match: dict[str, Any]) -> None:
    from bazaar_sim import broker

    broker.settle_match(w, match)


def expire(w: World) -> None:
    for o in w.state.offers.values():
        if o.status == "open" and o.expires_tick < w.tick:
            o.status = "expired"
            if o.thread is None:
                w.emit("offer.cancelled", {"offer": o.id, "venue": o.venue, "reason": "expired"})


def bench_tick(w: World) -> None:
    from bazaar_sim import broker

    broker.bench_tick(w)


def venue_tick(w: World) -> None:
    from bazaar_sim import broker

    broker.venue_tick(w)


def open_pack(w: World, team_id: str, aid: int) -> dict[str, Any]:
    """Open a sealed pack: each slot rolls its rarity odds; a sold-out rarity gives the next one down."""
    from bazaar_sim.scoring import asset_view

    asset = w.asset(aid)
    if asset.owner != team_id:
        raise SimError("not_owner", f"asset {aid} is not yours", 403)
    if asset.kind != "pack":
        raise invalid(f"asset {aid} is a card, not a pack")
    if aid in locked_assets(w):
        raise SimError("asset_locked", f"asset {aid} is promised in an accepted offer", 400)
    pack = catalog.packs()[asset.ref]
    rng = w.rng("pack", aid)
    refs = _draw(w, pack["slots"], rng)  # every slot drawn before anything is minted: all or nothing
    pulled = [w.mint(ref, team_id, "pack") for ref in refs]
    asset.owner = "opened"
    asset.history.append({"from": team_id, "to": "opened", "tick": w.tick, "why": "opened"})
    luck = round(sum(catalog.cards()[a.ref].book for a in pulled) - float(pack["expected_book"]), 2)
    team = w.team(team_id)
    team.luck = round(team.luck + luck, 2)
    order = {r: i for i, r in enumerate(catalog.RARITY_ORDER)}
    best = max(pulled, key=lambda a: order[catalog.cards()[a.ref].rarity])
    best_rarity = catalog.cards()[best.ref].rarity
    w.emit(
        "pack.opened",
        {"team": team_id, "name": team.name, "pack": asset.ref, "best": best.ref if order[best_rarity] >= 2 else None},
    )
    counts = w.held_counts(team_id)
    return {"cards": [asset_view(w, team, counts, a) for a in pulled], "luck": luck}


TALLER_INPUTS = 3


def taller(w: World, team_id: str, assets: Any) -> dict[str, Any]:
    """El Taller, as `/api/levels` describes it (the real answer is unpublished; this shape is ours): three cards of
    one rarity, the team keeps at least one copy of each card, one random card of the next rarity in return."""
    from collections import Counter

    from bazaar_sim.scoring import asset_view

    ok = isinstance(assets, list) and all(isinstance(a, int) and not isinstance(a, bool) for a in assets)
    if not ok or len(assets) != TALLER_INPUTS or len(set(assets)) != TALLER_INPUTS:
        raise invalid(f"assets must be {TALLER_INPUTS} different asset ids")
    picked = [w.asset(a) for a in assets]
    for a in picked:
        if a.owner != team_id:
            raise SimError("not_owner", f"asset {a.id} is not yours", 403)
        if a.kind != "card":
            raise invalid(f"asset {a.id} is a pack, not a card")
        if a.id in locked_assets(w):
            raise SimError("asset_locked", f"asset {a.id} is promised in an accepted offer", 400)
    rarities = {catalog.cards()[a.ref].rarity for a in picked}
    rarity = rarities.pop() if len(rarities) == 1 else None
    order = catalog.RARITY_ORDER
    if rarity is None or rarity == order[-1]:
        raise invalid("three copies of one rarity below the top one")
    held, used = w.held_counts(team_id), Counter(a.ref for a in picked)
    if any(held[ref] - n < 1 for ref, n in used.items()):
        raise SimError("keep_one", "you keep at least one copy of each card", 400)
    (ref,) = _draw(w, [{order[order.index(rarity) + 1]: 1.0}], w.rng("taller", *assets))
    for a in picked:
        w.transfer(a, "taller", "taller")
    pulled = w.mint(ref, team_id, "taller")
    w.emit("taller.used", {"team": team_id, "rarity": rarity, "card": pulled.ref}, actor=team_id)
    return {"card": asset_view(w, w.team(team_id), w.held_counts(team_id), pulled), "spent": list(assets)}


def _draw(w: World, slots: list[dict[str, float]], rng: Any) -> list[str]:
    """One card ref per slot, counting the copies this pack already took, so a sold-out rarity gives
    the next one down and a pack that cannot be filled is refused before any copy is minted."""
    released = catalog.released_sets()
    taken: dict[str, int] = {}
    refs: list[str] = []
    for slot in slots:
        rarities, weights = zip(*slot.items(), strict=True)
        rarity: str | None = rng.choices(rarities, weights=weights)[0]
        while rarity is not None:
            pool = [
                c.ref
                for c in catalog.cards().values()
                if c.rarity == rarity
                and c.set_code in released
                and w.state.minted.get(c.ref, 0) + taken.get(c.ref, 0) < c.print_run
            ]
            if pool:
                ref = str(rng.choice(pool))
                taken[ref] = taken.get(ref, 0) + 1
                refs.append(ref)
                break
            rarity = catalog.next_rarity_down(rarity)
        if rarity is None:
            raise SimError("sold_out", "every rarity is out of print", 400)
    return refs
