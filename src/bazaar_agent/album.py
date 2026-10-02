"""Our album from `GET /api/me` + `GET /api/catalog`: what each page has, and what is missing.

Always read this before deciding what to buy or sell: a missing page card is worth
book x affinity to us; a duplicate is worth a fraction of that (marginal 1, 0.25, 0.1).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MissingCard:
    ref: str
    name: str
    rarity: str
    book: float
    value_to_us: float  # first copy: book x affinity


@dataclass(frozen=True)
class PageView:
    set_code: str
    name: str
    affinity: float
    have: int
    of: int
    complete: bool
    missing: tuple[MissingCard, ...]
    duplicates: tuple[str, ...]


def album_view(me: dict[str, Any], catalog: dict[str, Any]) -> list[PageView]:
    affinity: dict[str, float] = me.get("affinity") or {}
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")
    pages = {p["set"]: p for p in (me.get("album") or {}).get("pages") or []}
    views = []
    for s in catalog.get("sets") or []:
        code = str(s.get("id"))
        if code not in pages:
            continue  # not released yet
        aff = float(affinity.get(code, 1.0))
        page_cards = [c for c in s.get("cards") or [] if c.get("page")]
        missing = tuple(
            MissingCard(
                str(c["id"]),
                str(c.get("name")),
                str(c.get("rarity")),
                float(c.get("book") or 0),
                round(float(c.get("book") or 0) * aff, 1),
            )
            for c in page_cards
            if held[str(c["id"])] == 0
        )
        dups = tuple(sorted(ref for ref, n in held.items() if n > 1 and ref.startswith(f"{code}-")))
        p = pages[code]
        views.append(
            PageView(
                code,
                str(p.get("name")),
                aff,
                int(p.get("have", 0)),
                int(p.get("of", 0)),
                bool(p.get("complete")),
                missing,
                dups,
            )
        )
    return sorted(views, key=lambda v: -v.affinity)
