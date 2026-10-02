"""Static game data (cards, packs, dealers) and the value rules every team's private values follow.

The data files are the real server's own responses (catalog, Abuela), captured on 2026-10-02, with
`minted` reset: the simulator counts its own copies. El Chato is calibrated from the real feed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data"
RARITY_ORDER = ("common", "uncommon", "rare", "epic", "legendary")
# Every team gets these six set multipliers, shuffled (RULES.md). The real values are private; our
# own were LAV x1.6 and LAT x0.5 on Friday, so the spread covers both.
AFFINITY_VALUES = (1.6, 1.3, 1.1, 0.9, 0.7, 0.5)


@dataclass(frozen=True)
class Card:
    ref: str
    name: str
    set_code: str
    rarity: str
    book: int
    print_run: int
    page: bool


@cache
def raw_catalog() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    return data


@cache
def raw_dealers() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads((DATA / "dealers.json").read_text(encoding="utf-8"))
    return data


@cache
def cards() -> dict[str, Card]:
    out: dict[str, Card] = {}
    for s in raw_catalog()["sets"]:
        for c in s["cards"]:
            out[c["id"]] = Card(
                c["id"], c["name"], s["id"], c["rarity"], int(c["book"]), int(c["print_run"]), bool(c["page"])
            )
    return out


@cache
def packs() -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in raw_catalog()["packs"]}


def set_codes() -> list[str]:
    return [s["id"] for s in raw_catalog()["sets"]]


def released_sets() -> list[str]:
    return [s["id"] for s in raw_catalog()["sets"] if s.get("released")]


def set_name(code: str) -> str:
    return next(str(s["name"]) for s in raw_catalog()["sets"] if s["id"] == code)


def page_cards(code: str) -> list[Card]:
    return [c for c in cards().values() if c.set_code == code and c.page]


def card(ref: str) -> Card | None:
    return cards().get(ref)


def marginals() -> list[float]:
    return [float(m) for m in raw_catalog()["values"]["copy_marginals"]]


def page_bonus() -> float:
    return float(raw_catalog()["values"]["page_bonus"])


def master_bonus() -> float:
    return float(raw_catalog()["values"]["master_bonus"])


def marginal(index: int) -> float:
    """The value share of the copy at `index` (0 = first copy). Beyond the list a copy is worth nothing."""
    m = marginals()
    return m[index] if 0 <= index < len(m) else 0.0


def held_copy_value(ref: str, copies_held: int, affinity: dict[str, float]) -> float:
    """`your_value` of a copy you hold: what you lose by giving one away (the last copy's marginal)."""
    c = cards()[ref]
    return round(c.book * affinity.get(c.set_code, 1.0) * marginal(copies_held - 1), 2)


def one_more_value(ref: str, copies_held: int, affinity: dict[str, float]) -> float:
    """`GET /api/me/value`: the value of ONE MORE copy."""
    c = cards()[ref]
    return round(c.book * affinity.get(c.set_code, 1.0) * marginal(copies_held), 2)


def page_value(code: str, affinity: dict[str, float]) -> float:
    return sum(c.book for c in page_cards(code)) * affinity.get(code, 1.0)


def collection_value(held: dict[str, int], affinity: dict[str, float], sets: list[str]) -> float:
    """Every copy at its marginal, plus the page bonus per complete page and the master bonus on top."""
    total = 0.0
    for ref, n in held.items():
        c = cards().get(ref)
        if c is None:
            continue
        total += c.book * affinity.get(c.set_code, 1.0) * sum(marginal(i) for i in range(n))
    for code in sets:
        if page_complete(code, held):
            total += page_bonus() * page_value(code, affinity)
            if master_complete(code, held):
                total += master_bonus() * page_value(code, affinity)
    return round(total, 2)


def page_complete(code: str, held: dict[str, int]) -> bool:
    return all(held.get(c.ref, 0) > 0 for c in page_cards(code))


def master_complete(code: str, held: dict[str, int]) -> bool:
    extra = [c for c in cards().values() if c.set_code == code and not c.page]
    return page_complete(code, held) and all(held.get(c.ref, 0) > 0 for c in extra)


def next_rarity_down(rarity: str) -> str | None:
    i = RARITY_ORDER.index(rarity)
    return RARITY_ORDER[i - 1] if i > 0 else None


def pack_name(pack_id: str) -> str:
    return str(packs()[pack_id]["name"])


def dealer_menu_sells(dealer: dict[str, Any], *, pack: str | None = None, rarity: str | None = None) -> dict | None:
    for item in dealer["menu"]["sells"]:
        if pack is not None and item.get("pack") == pack:
            return dict(item)
        if rarity is not None and item.get("rarity") == rarity:
            return dict(item)
    return None


def dealer_buys(dealer: dict[str, Any], rarity: str) -> bool:
    return any(item.get("rarity") == rarity for item in dealer["menu"]["buys"])
