"""Venues and their boards as the agents see them: fees, one-card asks and bids, our own open offers.

Pure: payloads in (`/api/venues`, `/api/venues/{id}/offers`, `/api/me/offers`), typed rows out.
Fees: the accepting side pays `ceil(price × fee_bps / 10000 + fee_per_card × cards)` (checked against
the tape on 2026-10-02: El Rastro, 500 bps + 1 P per card, charged 2 on 12 P, 5 on 65 P and 5 on 70 P).
A fee change is announced ahead (`pending_fee`, `effective_tick`); an accept at T settles at T+1, so a change
effective by then is priced in: the higher of the two fees, since which one the server charges is unverified.
Only plain shapes are traded: one card for cash (an ask) or cash for one card (a bid); anything else
on a board is skipped, never guessed at. Words persuade, structure binds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
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
    pending_fee: tuple[int, int] | None = None  # announced (fee_bps, fee_per_card), in force by settlement
    starter: bool = False  # a free starter stall (`auto`, given to a team without its own venue)

    def fee(self, price: int, cards: int = 1) -> int:
        fee = _fee(self.fee_bps, self.fee_per_card, price, cards)
        return max(fee, _fee(*self.pending_fee, price, cards)) if self.pending_fee else fee


def _fee(bps: int, per_card: int, price: int, cards: int) -> int:
    raw = price * bps / 10_000 + per_card * cards
    return math.ceil(raw - 1e-9) if raw > 0 else 0


FEE_BPS_CAP, FEE_PER_CARD_CAP = 1000, 5  # RULES.md: venue fees are capped at 10 % and 5 P per card
SETTLE_SLACK_TICKS = 2  # an accept now settles at tick + 1, or tick + 2 when it slips into the next tick


def _whole(value: Any, cap: int) -> int | None:
    """A whole, finite number from a rival's venue row, clamped to [0, cap]; None when it cannot be read."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return min(max(int(value), 0), cap)


def _pending_fee(pending: Any, tick: int | None, per_card_now: int) -> tuple[int, int] | None:
    """The announced fee when an accept now could settle under it (effective by tick + SETTLE_SLACK_TICKS;
    always when the tick or the effective tick is unknown); None when nothing is pending or it takes effect
    later. A fee we cannot read is priced at the RULES cap; a missing `fee_per_card` keeps today's."""
    if not isinstance(pending, dict):
        return None
    effective = pending.get("effective_tick")
    later = (
        isinstance(effective, int) and not isinstance(effective, bool) and effective > (tick or 0) + SETTLE_SLACK_TICKS
    )
    if tick is not None and later:
        return None
    bps = _whole(pending.get("fee_bps"), FEE_BPS_CAP)
    raw_per_card = pending.get("fee_per_card")
    per_card = per_card_now if raw_per_card is None else _whole(raw_per_card, FEE_PER_CARD_CAP)
    if bps is None or per_card is None:
        return FEE_BPS_CAP, FEE_PER_CARD_CAP
    return bps, per_card


def venues_from(payload: dict[str, Any], tick: int | None = None) -> list[Venue]:
    """`/api/venues` as typed rows; `tick` (the current one) decides whether an announced fee is priced in."""
    rows = []
    for v in payload.get("venues") or []:
        if not isinstance(v, dict) or not v.get("venue"):
            continue
        try:
            per_card = int(v.get("fee_per_card") or 0)
            rows.append(
                Venue(
                    id=str(v["venue"]),
                    owner=str(v.get("owner") or ""),
                    fee_bps=int(v.get("fee_bps") or 0),
                    fee_per_card=per_card,
                    status=str(v.get("status") or ""),
                    mechanism=str((v.get("rules") or {}).get("mechanism") or ("board" if v.get("house") else "")),
                    trades=int(v.get("trades") or 0),
                    house=bool(v.get("house")),
                    pending_fee=_pending_fee(v.get("pending_fee"), tick, per_card),
                    starter=v.get("starter") is True,
                )
            )
        except (TypeError, ValueError, OverflowError, AttributeError):  # a row we cannot read: skip that venue
            continue
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


SIDE_KEYS = frozenset({"cash", "assets", "types", "cards"})  # what an offer side may carry; anything else is not plain


def extra_structure(side: dict[str, Any]) -> bool:
    """A non-empty key we do not price (`want.packs`, `give.debt`, ...): the offer is not a plain shape."""
    return any(value not in (None, 0, [], {}, "") for key, value in side.items() if key not in SIDE_KEYS)


def parse_offer(o: dict[str, Any], venue: str | None = None) -> BoardOffer | None:
    """One card for cash (ask) or cash for one card (bid); None for every other shape, including one that
    carries any extra structure (a bid that also wants one of our assets, an unknown key): skipped, never
    guessed at. Words persuade, structure binds."""
    give, want = o.get("give") or {}, o.get("want") or {}
    if not isinstance(o.get("id"), int) or not isinstance(give, dict) or not isinstance(want, dict):
        return None
    if extra_structure(give) or extra_structure(want):
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
    if (
        int(give.get("cash") or 0) > 0
        and not give.get("assets")
        and not _cards_wanted(give)
        and len(wanted) == 1
        and not want.get("cash")
        and not want.get("assets")
    ):
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


def addressed_to_us(response: dict[str, Any], us: str) -> list[dict[str, Any]]:
    """The open board offers another team addressed to us, as `/api/me/offers` returns them ("your open and queued
    offers, and open offers addressed to you", the kit SDK). A keyless board never shows them (Sat 3 Oct: 127
    arrived, 0 were read). An offer inside a thread is the team desk's, never one of these."""
    out = []
    for rows in response.values():
        for o in rows if isinstance(rows, list) else []:
            if not isinstance(o, dict) or not us or o.get("to") != us or o.get("maker") in (us, None, ""):
                continue
            if o.get("status") in (None, "open") and o.get("thread") is None and isinstance(o.get("id"), int):
                out.append(o)
    return out


def best_venue(
    venues: Iterable[Venue], us: str, price: int, *, to: str | None = None, demand: Mapping[str, int] | None = None
) -> Venue | None:
    """Route public asks toward observed card-specific net bids, else activity and fees.

    An addressed offer already has a counterparty: minimise their acceptance fee, and
    never choose their own venue (they cannot accept there). Demand is a hint, not a fill.
    """

    def score(v: Venue) -> float:
        fee_share = min(1.0, v.fee(price) / max(1, price))
        return (v.trades + 1) * (1.0 - fee_share)

    candidates = [
        v for v in tradable_venues(venues, us) if v.mechanism in ("board", "auto", "") and (to is None or v.owner != to)
    ]
    if to is not None:
        return max(candidates, key=lambda v: (-v.fee(price), score(v), v.house, v.id), default=None)
    liquid = [v for v in candidates if demand and demand.get(v.id, 0) >= price]
    if liquid and demand is not None:
        return max(liquid, key=lambda v: (demand[v.id], -v.fee(price), score(v), v.id))
    return max(candidates, key=lambda v: (score(v), v.house, v.id), default=None)
