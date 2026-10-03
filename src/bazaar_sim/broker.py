"""Team venues, their brokers, and the Market Test (the bench).

From level 2 a team may open a venue (bond 250 P + 20 P, fees capped at 10 % and 5 P per card).
It gets a broker key once (`simbk-...`; only its SHA-256 is kept). A broker sees its venue's book
(makers as pseudonyms) and pairs crossing offers: ask <= price and price + fee <= bid. On an `auto`
venue the engine crosses its best bid and ask every tick itself. Every `bench_every_ticks` every
venue gets the same synthetic book (`bench_offers`); the share of the possible gains a venue
realises is its efficiency.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from bazaar_sim import catalog, validate
from bazaar_sim.errors import SimError, invalid, not_found
from bazaar_sim.models import BenchRun, BenchTrader, Offer, Venue
from bazaar_sim.views import offer_view
from bazaar_sim.world import World

BOND = 250
OPENING_FEE = 20
MAX_FEE_BPS = 1000
MAX_FEE_PER_CARD = 5
FEE_NOTICE_TICKS = 2
CLOSE_COOLDOWN_TICKS = 10
BENCH_TRADERS = 10
BENCH_REF = "BENCH"


def broker_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def venue_for_broker(w: World, key: str) -> Venue | None:
    digest = broker_digest(key)
    return next((v for v in w.state.venues.values() if v.broker_key_hash == digest and v.status != "closed"), None)


def _fees(body: dict[str, Any], default_bps: int) -> tuple[int, int]:
    bps, per_card = body.get("fee_bps", default_bps), body.get("fee_per_card", 0)
    for name, value, cap in (("fee_bps", bps, MAX_FEE_BPS), ("fee_per_card", per_card, MAX_FEE_PER_CARD)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= cap:
            raise invalid(f"{name} must be a whole number from 0 to {cap}")
    return int(bps), int(per_card)


def open_venue(w: World, team_id: str, body: dict[str, Any]) -> dict[str, Any]:
    team = w.team(team_id)
    if len(team.unlocked) < 2:
        raise SimError("locked", "a venue needs level 2 (a second dealer unlocked)", 403)
    if team.venue and w.state.venues[team.venue].status in ("open", "closing"):
        raise SimError("venue_exists", f"you already run {team.venue}", 400)
    name = validate.clean_text(body.get("name"), validate.VENUE_NAME_CHARS).strip()
    if not name:
        raise invalid("a venue needs a name")
    bps, per_card = _fees(body, 300)
    rules = body.get("rules") or {}
    if not isinstance(rules, dict) or rules.get("mechanism", "board") not in ("auto", "board"):
        raise invalid('rules.mechanism is "auto" or "board"')
    if team.cash < BOND + OPENING_FEE:
        raise SimError("insufficient_cash", f"a venue costs {BOND} P bond + {OPENING_FEE} P", 400)
    key = "simbk-" + secrets.token_urlsafe(18)
    vid = f"v{w.next_id('venue'):02d}"
    team.cash -= BOND + OPENING_FEE
    venue = Venue(
        venue=vid,
        name=name,
        owner=team_id,
        owner_name=team.name,
        fee_bps=bps,
        fee_per_card=per_card,
        rules={"mechanism": rules.get("mechanism", "board"), **{k: v for k, v in rules.items() if k != "mechanism"}},
        bond=BOND,
        description=validate.clean_text(body.get("description"), 280),
        opened_tick=w.tick,
        live_tick=w.tick + w.config.venue_live_ticks,
        broker_key_hash=broker_digest(key),
    )
    w.state.venues[vid] = venue
    team.venue = vid
    w.emit(
        "venue.opened",
        {
            "venue": vid,
            "name": name,
            "owner": team_id,
            "fee_bps": bps,
            "fee_per_card": per_card,
            "rules": venue.rules,
            "bond": BOND,
        },
    )
    return {"venue": vid, "broker_key": key, "name": name, "fee_bps": bps, "fee_per_card": per_card}


def _owned_venue(w: World, team_id: str, vid: str) -> Venue:
    venue = w.state.venues.get(vid)
    if venue is None:
        raise not_found(f"venue {vid}")
    if venue.owner != team_id:
        raise SimError("not_owner", f"venue {vid} is not yours", 403)
    return venue


def set_fee(w: World, team_id: str, vid: str, body: dict[str, Any]) -> dict[str, Any]:
    venue = _owned_venue(w, team_id, vid)
    bps, per_card = _fees(
        {"fee_bps": body.get("fee_bps"), "fee_per_card": body.get("fee_per_card", venue.fee_per_card)}, 0
    )
    effective = w.tick + FEE_NOTICE_TICKS
    venue.pending_fee = {"fee_bps": bps, "fee_per_card": per_card, "effective_tick": effective}
    w.emit("venue.fee_announced", {"venue": vid, "fee_bps": bps, "fee_per_card": per_card, "effective_tick": effective})
    return {"ok": True, "venue": vid, "effective_tick": effective}


def close_venue(w: World, team_id: str, vid: str) -> dict[str, Any]:
    venue = _owned_venue(w, team_id, vid)
    if venue.status == "open":
        venue.status = "closing"
        venue.closed_tick = w.tick
        for o in w.state.offers.values():
            if o.venue == vid and o.status == "open":
                o.status = "cancelled"
        w.emit("venue.closing", {"venue": vid, "bond_back_tick": w.tick + CLOSE_COOLDOWN_TICKS})
    return {"ok": True, "venue": vid, "status": venue.status}


def announce(w: World, venue: Venue, body: dict[str, Any]) -> dict[str, Any]:
    text = validate.clean_text(body.get("text"), 280)
    w.emit("venue.announcement", {"venue": venue.venue, "name": venue.name, "text": text}, actor=venue.venue)
    return {"ok": True}


# ---------------------------------------------------------------- the broker's book and matches


def _bench_run(w: World) -> BenchRun | None:
    return next((r for r in w.state.bench if r.start_tick <= w.tick <= r.end_tick and not r.scored), None)


def _bench_offer(t: BenchTrader, run: int) -> dict[str, Any]:
    if t.side == "sell":
        return {
            "id": t.id,
            "run": run,
            "give": {"assets": [{"kind": "card", "ref": BENCH_REF}]},
            "want": {"cash": t.quote},
        }
    return {"id": t.id, "run": run, "give": {"cash": t.quote}, "want": {"types": [f"card:{BENCH_REF}"]}}


def _bench_open(run: BenchRun, vid: str) -> list[BenchTrader]:
    used = {i for pair in run.matched.get(vid, []) for i in pair}
    return [t for t in run.traders if t.id not in used]


def book(w: World, venue: Venue) -> dict[str, Any]:
    offers = [o for o in w.state.offers.values() if o.venue == venue.venue and o.status == "open" and o.thread is None]
    run = _bench_run(w)
    bench = [_bench_offer(t, run.run) for t in _bench_open(run, venue.venue)] if run else []
    tape = [e.payload for e in w.state.events if e.type == "settlement" and e.payload.get("venue") == venue.venue]
    return {
        "venue": venue.venue,
        "offers": [offer_view(w, o, masked=True) for o in sorted(offers, key=lambda o: o.id)],
        "bench_offers": bench,
        "fee_bps": venue.fee_bps,
        "fee_per_card": venue.fee_per_card,
        "settlements": tape[-50:],
        "tick": w.tick,
    }


def _sell_ref(offer: Offer, w: World) -> str | None:
    if offer.give.cash or len(offer.give.assets) != 1 or not offer.want.cash or offer.want.assets or offer.want.types:
        return None
    return w.asset(offer.give.assets[0]).ref


def _buy_ref(offer: Offer) -> str | None:
    """The card a plain bid wants: cash for exactly one card type and nothing else (a mixed bid never matches)."""
    if not offer.give.cash or offer.give.assets or len(offer.want.types) != 1 or offer.want.cash or offer.want.assets:
        return None
    return offer.want.types[0].partition(":")[2]


def match(w: World, venue: Venue, body: dict[str, Any]) -> dict[str, Any]:
    if venue.rules.get("mechanism") != "board":
        raise invalid("on an auto venue the engine crosses every pair: a broker acts on a board venue")
    price = validate.price(body.get("price"))
    sell, buy = body.get("sell"), body.get("buy")
    if isinstance(sell, str) and isinstance(buy, str):
        return _bench_match(w, venue, sell, buy, price)
    if isinstance(sell, bool) or isinstance(buy, bool) or not isinstance(sell, int) or not isinstance(buy, int):
        raise invalid("sell and buy are offer ids (or bench ids like b3-7)")
    s, b = w.state.offers.get(sell), w.state.offers.get(buy)
    for o, name in ((s, "sell"), (b, "buy")):
        if o is None or o.venue != venue.venue or o.status != "open" or o.thread is not None:
            raise invalid(f"{name} is not an open offer on {venue.venue}")
    assert s is not None and b is not None
    ref = _sell_ref(s, w)
    if ref is None or _buy_ref(b) != ref or s.maker == b.maker:
        raise invalid("the pair does not cross: one card for cash against cash for that card, two makers")
    fee = round(price * venue.fee_bps / 10_000) + venue.fee_per_card
    if not s.want.cash <= price or price + fee > b.give.cash:
        raise invalid(f"needs ask <= price and price + fee <= bid (fee {fee})")
    _queue_match(w, venue, s, b, price, fee, by="broker")
    return {"ok": True, "sell": sell, "buy": buy, "price": price, "fee": fee, "settles_tick": w.tick + 1}


def _queue_match(w: World, venue: Venue, s: Offer, b: Offer, price: int, fee: int, *, by: str) -> None:
    for o in (s, b):
        o.status = "accepted"
        o.accepted_by = f"broker:{venue.venue}"
        o.accepted_tick = w.tick
    w.state.matches.append({"venue": venue.venue, "sell": s.id, "buy": b.id, "price": price, "fee": fee, "by": by})


def _bench_match(w: World, venue: Venue, sell: str, buy: str, price: int) -> dict[str, Any]:
    run = _bench_run(w)
    if run is None:
        raise invalid("no Market Test is running")
    open_ids = {t.id: t for t in _bench_open(run, venue.venue)}
    s, b = open_ids.get(sell), open_ids.get(buy)
    if s is None or b is None or s.side != "sell" or b.side != "buy":
        raise invalid("sell and buy must be open bench offers of the running test")
    fee = round(price * venue.fee_bps / 10_000) + venue.fee_per_card
    if not s.quote <= price or price + fee > b.quote:
        raise invalid(f"needs ask <= price and price + fee <= bid (fee {fee})")
    run.matched.setdefault(venue.venue, []).append([s.id, b.id])
    return {"ok": True, "sell": sell, "buy": buy, "price": price, "fee": fee}


def settle_match(w: World, m: dict[str, Any]) -> None:
    """A crossed pair settles like two accepts at once: the seller's card for the buyer's cash, fee to the venue."""
    from bazaar_sim import market

    s, b = w.state.offers.get(m["sell"]), w.state.offers.get(m["buy"])
    venue = w.state.venues.get(m["venue"])
    if s is None or b is None or venue is None:
        return
    asset = w.state.assets.get(s.give.assets[0])
    seller, buyer = w.team(s.maker), w.team(b.maker)
    price, fee = int(m["price"]), int(m["fee"])
    if asset is None or asset.owner != seller.id or buyer.cash < price + fee:
        for o in (s, b):
            o.status = "failed"
        w.emit("settlement.failed", {"offer": s.id, "reason": "match no longer crosses"})
        return
    seller_counts, buyer_counts = w.held_counts(seller.id), w.held_counts(buyer.id)
    lost = catalog.held_copy_value(asset.ref, seller_counts.get(asset.ref, 1), seller.affinity)
    got = catalog.one_more_value(asset.ref, buyer_counts.get(asset.ref, 0), buyer.affinity)
    w.transfer(asset, buyer.id, f"match on {venue.venue}")
    buyer.cash -= price + fee
    seller.cash += price
    if venue.owner in w.state.teams:
        w.team(venue.owner).cash += fee
    for o in (s, b):
        o.status = "settled"
    seller.trade_gain = round(seller.trade_gain + (price - lost) / 10, 3)
    buyer.trade_gain = round(buyer.trade_gain + (got - price - fee) / 10, 3)
    venue.trades, venue.volume, venue.fees, venue.pairs = (
        venue.trades + 1,
        venue.volume + price,
        venue.fees + fee,
        venue.pairs + 1,
    )
    venue.traders = sorted({*venue.traders, seller.id, buyer.id})
    venue.value_created += max(0.0, got - lost)
    w.emit(
        "settlement",
        {
            "settlement": w.next_id("settlement"),
            "tick": w.tick,
            "kind": "match",
            "parties": [seller.id, buyer.id],
            "venue": venue.venue,
            "persona": None,
            "fee": fee,
            "items": [
                {
                    "id": asset.id,
                    "kind": asset.kind,
                    "ref": asset.ref,
                    "serial": asset.serial,
                    "frm": seller.id,
                    "to": buyer.id,
                    "name": catalog.cards()[asset.ref].name,
                }
            ],
            "price": price,
        },
    )
    market._cancel_stale(w, [asset.id])


# ---------------------------------------------------------------- per tick: fees, closing, auto venues, bench


def venue_tick(w: World) -> None:
    for venue in w.state.venues.values():
        pending = venue.pending_fee
        if pending and w.tick >= pending["effective_tick"]:
            venue.fee_bps, venue.fee_per_card, venue.pending_fee = pending["fee_bps"], pending["fee_per_card"], None
            w.emit(
                "venue.fee_changed",
                {"venue": venue.venue, "fee_bps": venue.fee_bps, "fee_per_card": venue.fee_per_card},
            )
        if venue.status == "closing" and w.tick >= (venue.closed_tick or 0) + CLOSE_COOLDOWN_TICKS:
            venue.status = "closed"
            if venue.owner in w.state.teams:
                w.team(venue.owner).cash += venue.bond
                w.team(venue.owner).venue = None
            w.emit("venue.closed", {"venue": venue.venue, "bond_returned": venue.bond})
        if venue.status == "open" and venue.rules.get("mechanism") == "auto" and not venue.house:
            _auto_cross(w, venue)


def _auto_cross(w: World, venue: Venue) -> None:
    """The venue's own mechanism: its best bid against its best ask for each card, at the ask."""
    queued = {i for m in w.state.matches for i in (m["sell"], m["buy"])}
    offers = [
        o for o in w.state.offers.values() if o.venue == venue.venue and o.status == "open" and o.id not in queued
    ]
    sells = sorted((o for o in offers if _sell_ref(o, w)), key=lambda o: (o.want.cash, o.id))
    buys = sorted((o for o in offers if _buy_ref(o)), key=lambda o: (-o.give.cash, o.id))
    for s in sells:
        ref = _sell_ref(s, w)
        price = s.want.cash
        fee = round(price * venue.fee_bps / 10_000) + venue.fee_per_card
        b = next(
            (
                b
                for b in buys
                if b.status == "open" and _buy_ref(b) == ref and b.maker != s.maker and b.give.cash >= price + fee
            ),
            None,
        )
        if b is not None:
            _queue_match(w, venue, s, b, price, fee, by="auto")
    run = _bench_run(w)
    if run is not None:
        _auto_bench(venue, run)


def _auto_bench(venue: Venue, run: BenchRun) -> None:
    fee_of = lambda p: round(p * venue.fee_bps / 10_000) + venue.fee_per_card  # noqa: E731
    while True:
        open_now = _bench_open(run, venue.venue)
        asks = sorted((t for t in open_now if t.side == "sell"), key=lambda t: t.quote)
        bids = sorted((t for t in open_now if t.side == "buy"), key=lambda t: -t.quote)
        if not asks or not bids or asks[0].quote + fee_of(asks[0].quote) > bids[0].quote:
            return
        run.matched.setdefault(venue.venue, []).append([asks[0].id, bids[0].id])


def bench_tick(w: World) -> None:
    cfg = w.config
    first, every = cfg.bench_first_tick, cfg.bench_every_ticks
    if w.tick >= first and (w.tick - first) % every == 0:
        _start_bench(w)
    for run in w.state.bench:
        if not run.scored and w.tick > run.end_tick:
            _score_bench(w, run)
    w.state.bench = w.state.bench[-10:]


def _start_bench(w: World) -> None:
    rng = w.rng("bench")
    run_id = w.next_id("bench")
    traders = []
    for k in range(BENCH_TRADERS):
        if k % 2 == 0:
            cost = rng.randint(20, 60)
            traders.append(
                BenchTrader(id=f"b{run_id}-{k}", side="sell", limit=cost, quote=round(cost * rng.uniform(1.05, 1.3)))
            )
        else:
            value = rng.randint(40, 95)
            traders.append(
                BenchTrader(id=f"b{run_id}-{k}", side="buy", limit=value, quote=round(value * rng.uniform(0.75, 0.95)))
            )
    w.state.bench.append(
        BenchRun(run=run_id, start_tick=w.tick, end_tick=w.tick + w.config.bench_ticks - 1, traders=traders)
    )
    w.emit("bench.started", {"run": run_id, "ticks": w.config.bench_ticks, "traders": BENCH_TRADERS})


def possible_gains(traders: list[BenchTrader]) -> int:
    sells = sorted(t.limit for t in traders if t.side == "sell")
    buys = sorted((t.limit for t in traders if t.side == "buy"), reverse=True)
    return sum(max(0, b - s) for s, b in zip(sells, buys, strict=False))


def _score_bench(w: World, run: BenchRun) -> None:
    by_id = {t.id: t for t in run.traders}
    best = possible_gains(run.traders) or 1
    for team in w.state.teams.values():
        venue = w.state.venues.get(team.venue) if team.venue else None
        if venue is None or venue.status != "open":
            continue
        pairs = run.matched.get(venue.venue, [])
        realised = sum(max(0, by_id[b].limit - by_id[s].limit) for s, b in pairs)
        efficiency = round(realised / best, 3)
        team.bench_efficiency, team.bench_venue = efficiency, venue.venue
        team.mm_points = round(team.mm_points + 10 * efficiency, 2)
    run.scored = True
    w.emit("bench.finished", {"run": run.run, "possible": best})
