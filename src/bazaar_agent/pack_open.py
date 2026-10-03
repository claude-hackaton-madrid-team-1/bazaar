"""Open a sealed pack, or keep it sealed (N14b): the grant's `sobre_barrio` at 09:00, and any pack we buy.

A sealed pack is worth what a team would pay for it sealed; opened, it is worth its cards to us (the pack
EV, `strategy.pack_ev`: our album need from `/api/me`, page-bonus shares, only cards still mintable).
What we pull never scores by itself (RULES.md: luck); its cards score when they complete a page's value or
sell to a team that needs them (Marius's B9, #109: "open the free packs, list the duplicates"). So we open
unless a team has paid more for that pack sealed than it is worth to us opened. Pure: no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal

from bazaar_agent import intel
from bazaar_agent.strategy import Market, StrategyParams, is_team, pack_ev


@dataclass(frozen=True)
class SealedPack:
    asset_id: int
    pack: str


@dataclass(frozen=True)
class PackChoice:
    pack: SealedPack
    verdict: Literal["open", "keep"]
    ev: float  # what its cards are worth to us opened
    sealed_price: float | None  # what teams paid for one sealed (team-to-team tape), if anyone did
    reason: str


def sealed_packs(me: Mapping[str, Any]) -> list[SealedPack]:
    return [
        SealedPack(int(a["id"]), str(a.get("ref")))
        for a in me.get("assets") or []
        if isinstance(a, dict) and a.get("kind") == "pack" and isinstance(a.get("id"), int) and a.get("ref")
    ]


def sealed_price(prints: Sequence[intel.Print], pack: str) -> float | None:
    """The median a team paid another team for this pack sealed (dealer sales are not a resale price)."""
    paid = [p.price for p in prints if p.ref == pack and p.persona is None and is_team(p.seller)]
    return float(median(paid)) if paid else None


def choose(m: Market, sealed: SealedPack, params: StrategyParams) -> PackChoice:
    if sealed.pack not in m.packs:
        return PackChoice(sealed, "keep", 0.0, None, f"{sealed.pack} is not in the catalog: kept sealed")
    ev, how = pack_ev(m, m.packs[sealed.pack], params.model_copy(update={"pack_ev_album": True}))  # its cards
    resale = sealed_price(m.prints, sealed.pack)
    if resale is not None and resale > ev:
        return PackChoice(sealed, "keep", ev, resale, f"teams pay {resale:g} for it sealed, above its EV {ev:.1f}")
    seen = f"teams paid {resale:g} sealed" if resale is not None else "no team ever bought one sealed"
    return PackChoice(sealed, "open", ev, resale, f"EV {ev:.1f} to us ({how}); {seen}")
