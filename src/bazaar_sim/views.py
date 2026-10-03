"""The API's JSON shapes, rendered from the world. Shapes follow the captured responses in
`tests/fixtures/api/` and the real feed payloads (`offer.listed`, `thread.message`, `settlement`).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from bazaar_sim import catalog
from bazaar_sim.models import Asset, Offer, Side, Thread
from bazaar_sim.world import World


def asset_brief(asset: Asset) -> dict[str, Any]:
    """An asset as offers and settlements show it."""
    card = catalog.card(asset.ref)
    return {
        "id": asset.id,
        "kind": asset.kind,
        "ref": asset.ref,
        "serial": asset.serial,
        "rarity": card.rarity if card else None,
        "set": card.set_code if card else None,
        "print_run": card.print_run if card else None,
    }


def asset_name(asset: Asset) -> str:
    card = catalog.card(asset.ref)
    return card.name if card else catalog.pack_name(asset.ref)


def item_name(ref: str) -> str:
    card = catalog.card(ref)
    return card.name if card else catalog.pack_name(ref)


def side_view(w: World, side: Side) -> dict[str, Any]:
    assets = [asset_brief(w.state.assets[a]) for a in side.assets if a in w.state.assets]
    return {"cash": side.cash, "assets": assets, "types": list(side.types)}


def pseudonym(w: World, team_id: str) -> str:
    """How a venue board shows a maker: stable per world, never the team id."""
    if team_id in w.state.teams:
        return "m" + hashlib.sha256(f"{w.state.seed}:maker:{team_id}".encode()).hexdigest()[:8]
    return team_id


def offer_view(w: World, offer: Offer, *, masked: bool = False) -> dict[str, Any]:
    return {
        "id": offer.id,
        "maker": pseudonym(w, offer.maker) if masked else offer.maker,
        "to": offer.to,
        "venue": offer.venue,
        "thread": offer.thread,
        "status": offer.status,
        "give": side_view(w, offer.give),
        "want": side_view(w, offer.want),
        "expires_tick": offer.expires_tick,
        "created_tick": offer.created_tick,
        "final": offer.final,
    }


def thread_view(w: World, th: Thread) -> dict[str, Any]:
    messages = []
    for m in th.messages:
        offer = w.state.offers.get(m.offer) if m.offer is not None else None
        messages.append(
            {
                "message": m.id,
                "id": m.id,
                "tick": m.tick,
                "sender": m.sender,
                "text": m.text,
                "offer": offer_view(w, offer) if offer is not None else None,
            }
        )
    standing = [
        offer_view(w, o) for o in w.state.offers.values() if o.thread == th.id and o.status in ("open", "accepted")
    ]
    view: dict[str, Any] = {
        "id": th.id,
        "kind": th.kind,
        "with": th.with_,
        "team": th.team,
        "topic": th.topic,
        "venue": th.venue,
        "status": th.status,
        "closed_reason": th.closed_reason,
        "messages": messages,
        "standing_offers": standing,
    }
    if th.until_tick is not None:
        view["until_tick"] = th.until_tick
    return view


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone().isoformat(timespec="seconds")


def day_code(epoch: float) -> str:
    """The game's day codes are fri/sat/sun only (the spec's enum): any other weekday reports as sun."""
    code = datetime.fromtimestamp(epoch, UTC).astimezone().strftime("%a").lower()[:3]
    return code if code in ("fri", "sat", "sun") else "sun"


def clock_view(w: World) -> dict[str, Any]:
    c = w.state.clock
    now = w.now()
    closes = iso(now + 12 * 3600)
    today = day_code(now)
    day = {
        "closes": closes,
        "day": today,
        "name": "Simulator",
        "opens": iso(c.booted_at),
        "tick_seconds": c.tick_seconds,
    }
    return {
        "calendar": False,
        "calendar_on": False,
        "closes": closes,
        "days": [day],
        "doors": "open",
        "limits": dict(w.config.limits),
        "max_tick_seconds": 60.0,
        "min_tick_seconds": 0.2,
        "next_name": "Simulator",
        "next_opens": iso(now + 24 * 3600),
        "next_tick_in": w.next_tick_in(),
        "paused": c.paused,
        "round": 1,
        "round_name": "Simulator · El Rastro",
        "t_hours": w.t_hours,
        "tick": c.tick,
        "tick_seconds": c.tick_seconds,
        "today": today,
        "today_name": "Simulator",
    }


def health_view(w: World) -> dict[str, Any]:
    now = w.now()
    return {
        "doors": "open",
        "last_tick_age_s": round(now - w.state.clock.tick_started_at, 1),
        "loop_age_s": 0.1,
        "ok": True,
        "paused": w.state.clock.paused,
        "pending_voices": 0,
        "tick": w.tick,
        "uptime_s": int(now - w.state.clock.booted_at),
    }


def catalog_view(w: World) -> dict[str, Any]:
    data = catalog.raw_catalog()
    sets = []
    for s in data["sets"]:
        cards = [{**c, "minted": w.state.minted.get(c["id"], 0)} for c in s["cards"]]
        sets.append({**s, "cards": cards})
    return {**data, "sets": sets}


def feed_view(w: World, limit: int) -> dict[str, Any]:
    public = [e for e in w.state.events if e.scope == "public"]
    return {"events": [e.model_dump() for e in public[-max(1, min(500, limit)) :]]}


def card_view(w: World, asset: Asset, viewer: str) -> dict[str, Any]:
    owner = asset.owner if asset.owner == viewer or asset.owner not in w.state.teams else "a team"
    return {
        **asset_brief(asset),
        "name": asset_name(asset),
        "owner": owner,
        "history": [dict(h) for h in asset.history],
    }


def schedule_view(w: World) -> dict[str, Any]:
    from bazaar_sim.broker import bench_preset
    from bazaar_sim.duels import DECAY

    cfg = w.config
    hours_per_tick = cfg.tick_seconds / 3600.0
    session = w.state.duel_session
    upcoming: list[dict[str, Any]] = []
    for kind, first, every in (
        ("duels", cfg.duel_first_tick, cfg.duel_every_ticks),
        ("bench", cfg.bench_first_tick, cfg.bench_every_ticks),
    ):
        nxt = first if w.tick < first else first + ((w.tick - first) // every + 1) * every
        for i in range(3):
            if kind == "duels":
                n = session + 1 + i
                note = "Practice duels (not scored)" if n == 1 else f"Sim duels {n}"
                params: dict[str, Any] = {
                    "decay": DECAY,
                    "duel_ticks": cfg.duel_ticks,
                    "issues": ["price", "days"] if n % 2 == 0 else ["price"],
                    "max_concurrent": 2,
                    "name": note,
                    "practice": n == 1,
                    "rounds": 1,
                }
            else:
                p = bench_preset(w, w.state.counters.get("bench", 0) + 1 + i)
                note = "The Market Test: every venue gets the same synthetic book"
                params = {"ticks": p.ticks, "traders": p.traders}
                if p.name == "hard":
                    note = "The hard Market Test: firmer and more impatient traders"
                    params = {"name": "The hard Market Test", **params}
            at_tick = nxt + i * every
            upcoming.append(
                {"action": kind, "at_hours": round(at_tick * hours_per_tick, 3), "note": note, "params": params}
            )
    if w.tick < w.state.chato_open_tick:
        upcoming.append(
            {
                "action": "persona",
                "at_hours": round(w.state.chato_open_tick * hours_per_tick, 3),
                "note": "El Chato opens to everyone",
                "params": {"enabled": True, "id": "chato", "open_to_all": True},
            }
        )
    upcoming.sort(key=lambda u: u["at_hours"])
    return {"now_hours": round(w.t_hours, 3), "upcoming": upcoming}


def dealer_view(w: World, dealer_id: str) -> dict[str, Any]:
    data = dict(catalog.raw_dealers()[dealer_id])
    data.pop("teaser", None)
    data.pop("how", None)
    if dealer_id == "chato":
        hours = w.state.chato_open_tick * w.config.tick_seconds / 3600.0
        data["open_to_all"] = w.tick >= w.state.chato_open_tick
        data["unlock"] = {**data["unlock"], "open_to_all_at": round(hours, 3)}
    return data


def dealers_view(w: World) -> dict[str, Any]:
    return {"personas": [dealer_view(w, d) for d in catalog.raw_dealers()]}


def levels_view(w: World) -> dict[str, Any]:
    chato = catalog.raw_dealers()["chato"]
    left = max(0.0, (w.state.chato_open_tick - w.tick) * w.config.tick_seconds / 3600.0)
    return {
        "levels": [
            {
                "id": "chato",
                "kind": "persona",
                "name": chato["name"],
                "state": "active",
                "teaser": chato["teaser"],
                "how": chato["how"],
                "opens_to_all_in_hours": round(left, 3),
            }
        ]
    }


def venue_view(w: World, vid: str) -> dict[str, Any]:
    v = w.state.venues[vid]
    data = v.model_dump(exclude={"broker_key_hash", "live_tick", "closed_tick", "value_created"})
    data["traders"] = len(v.traders)
    return data


def venues_view(w: World) -> dict[str, Any]:
    return {"venues": [venue_view(w, vid) for vid, v in w.state.venues.items() if v.status != "closed"]}


def board_view(w: World, vid: str) -> dict[str, Any]:
    offers = [o for o in w.state.offers.values() if o.venue == vid and o.status == "open" and o.thread is None]
    return {"offers": [offer_view(w, o, masked=True) for o in sorted(offers, key=lambda o: o.id)]}
