"""Venues and their boards as the agents see them: fees, one-card asks and bids, our own open offers.

Pure: payloads in (`/api/venues`, `/api/venues/{id}/offers`, `/api/me/offers`), typed rows out.
Fees: the accepting side pays `ceil(price × fee_bps / 10000 + fee_per_card × cards)` (checked against
the tape on 2026-10-02: El Rastro, 500 bps + 1 P per card, charged 2 on 12 P, 5 on 65 P and 5 on 70 P).
Only plain shapes are traded: one card for cash (an ask) or cash for one card (a bid); anything else
on a board is skipped, never guessed at. Words persuade, structure binds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["ask", "bid"]


@dataclass(frozen=True)
class Venue:
    id: str
    owner: str
    fee_bps: int
    fee_per_card: int
    status: str
    mechanism: str
    trades: int
    house: bool

    def fee(self, price: int, cards: int = 1) -> int:
        raw = price * self.fee_bps / 10_000 + self.fee_per_card * cards
        return math.ceil(raw - 1e-9) if raw > 0 else 0


def venues_from(payload: dict[str, Any]) -> list[Venue]:
    rows = []
    for v in payload.get("venues") or []:
        if not isinstance(v, dict) or not v.get("venue"):
            continue
        rows.append(
            Venue(
                id=str(v["venue"]),
                owner=str(v.get("owner") or ""),
                fee_bps=int(v.get("fee_bps") or 0),
                fee_per_card=int(v.get("fee_per_card") or 0),
                status=str(v.get("status") or ""),
                mechanism=str((v.get("rules") or {}).get("mechanism") or ("board" if v.get("house") else "")),
                trades=int(v.get("trades") or 0),
                house=bool(v.get("house")),
            )
        )
    return rows


def tradable_venues(venues: Iterable[Venue], us: str) -> list[Venue]:
    """Open venues we may trade on: never our own (the server refuses with `self_venue`)."""
    return [v for v in venues if v.status == "open" and v.owner != us]


@dataclass(frozen=True)
class BoardOffer:
    id: int
    venue: str
    maker: str
    side: Side
    ref: str
    price: int
    asset_id: int | None
    rarity: str | None
    expires_tick: int | None
    created_tick: int | None


def _cards_wanted(want: dict[str, Any]) -> list[str]:
    return [str(t).split(":", 1)[-1] for t in (want.get("types") or []) + (want.get("cards") or [])]


def parse_offer(o: dict[str, Any], venue: str | None = None) -> BoardOffer | None:
    """One card for cash (ask) or cash for one card (bid); None for every other shape."""
    give, want = o.get("give") or {}, o.get("want") or {}
    if not isinstance(o.get("id"), int):
        return None
    assets = [a for a in give.get("assets") or [] if isinstance(a, dict)]
    oid, where, maker = int(o["id"]), str(o.get("venue") or venue or ""), str(o.get("maker") or "")
    expires, created = o.get("expires_tick"), o.get("created_tick")
    if (
        len(assets) == 1
        and len(give.get("assets") or []) == 1
        and assets[0].get("kind", "card") == "card"
        and not give.get("cash")
        and not _cards_wanted(give)
        and int(want.get("cash") or 0) > 0
        and not want.get("assets")
        and not _cards_wanted(want)
    ):
        a = assets[0]
        asset_id = int(a["id"]) if isinstance(a.get("id"), int) else None
        ref, price = str(a.get("ref")), int(want["cash"])
        return BoardOffer(oid, where, maker, "ask", ref, price, asset_id, a.get("rarity"), expires, created)
    wanted = _cards_wanted(want)
    if int(give.get("cash") or 0) > 0 and not give.get("assets") and len(wanted) == 1 and not want.get("cash"):
        return BoardOffer(oid, where, maker, "bid", wanted[0], int(give["cash"]), None, None, expires, created)
    return None


def board_offers(payload: dict[str, Any], venue: str, us: str) -> list[BoardOffer]:
    """Open plain offers on one venue that we may accept (open to anyone, or addressed to us)."""
    rows = []
    for o in payload.get("offers") or []:
        if not isinstance(o, dict) or o.get("status") not in (None, "open") or o.get("to") not in (None, us):
            continue
        if (parsed := parse_offer(o, venue)) is not None:
            rows.append(parsed)
    return rows


@dataclass(frozen=True)
class OpenOffer:
    """One of OUR open offers (from `/api/me/offers`)."""

    id: int
    side: Side
    ref: str
    price: int
    venue: str
    asset_id: int | None
    expires_tick: int | None
    created_tick: int | None


def our_open_offers(response: dict[str, Any], us: str) -> tuple[list[OpenOffer], int]:
    """(our plain open BOARD offers, how many open offers we hold in all). An offer counts as ours unless
    another team addressed it to us (the same rule as `seller.open_commitments`); offers inside a thread
    (our dealer bids) count toward the total but are the dealer desk's, never the maker's."""
    mine, total = [], 0
    for rows in response.values():
        for o in rows if isinstance(rows, list) else []:
            if not isinstance(o, dict) or o.get("status") not in (None, "open", "queued"):
                continue
            if o.get("to") == us and o.get("maker") != us:
                continue
            total += 1
            if o.get("thread") is None and (p := parse_offer(o)) is not None:
                mine.append(
                    OpenOffer(p.id, p.side, p.ref, p.price, p.venue, p.asset_id, p.expires_tick, p.created_tick)
                )
    return mine, total


def best_venue(venues: Iterable[Venue], us: str, price: int) -> Venue | None:
    """Where an offer is likeliest to fill: the venue's trades so far (activity), discounted by the fee
    share its taker pays. El Rastro wins until a team venue trades as much at a lower fee."""

    def score(v: Venue) -> float:
        fee_share = min(1.0, v.fee(price) / max(1, price))
        return (v.trades + 1) * (1.0 - fee_share)

    candidates = [v for v in tradable_venues(venues, us) if v.mechanism in ("board", "auto", "")]
    return max(candidates, key=lambda v: (score(v), v.house, v.id), default=None)
