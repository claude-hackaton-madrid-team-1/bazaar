"""The supply map: how many copies of each card exist, who holds them, and how many complete pages fit.

Sources (no network here; `bazaar supply scan` does the reads):
- the 270 starting assets: ids 1–270 are 18 starting hands of 15 cards in consecutive blocks, block k is
  team k's hand (block 1 = ours, t01). A card scan (`GET /api/cards/{id}`) gives each id's card, but
  another team's owner is anonymised ("a team"): the block still names who was dealt it (#22);
- the public feed: settlements (asset id, card, from, to), team listings (the lister holds the asset) and
  `pack.opened` (which team opened which pack: its cards are new ids, beyond the 270);
- the catalog's `minted` per card (every copy in existence, dealer mints and pack pulls included);
- `/api/me` for our own copies (always the truth for us).

A page needs one copy of each of its page cards, so the number of complete pages that can exist is the
fewest copies minted of any of its page cards: the "bottleneck" (on Friday, LAV-09 and MAL-09: one copy).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Event = dict[str, Any]
HANDS = 18
HAND_SIZE = 15
START_ASSETS = HANDS * HAND_SIZE  # ids 1–270: the starting hands, dealt in blocks of 15
PAGE_RARITIES = ("common", "uncommon", "rare")


def team_id(n: int) -> str:
    return f"t{n:02d}"


def start_team(asset_id: int) -> str | None:
    """The team a starting asset was dealt to (block k of 15 ids = team k); None beyond id 270."""
    return team_id((asset_id - 1) // HAND_SIZE + 1) if 1 <= asset_id <= START_ASSETS else None


def is_team(party: Any) -> bool:
    s = str(party or "")
    return len(s) >= 2 and s[0] == "t" and s[1:].isdigit()


@dataclass(frozen=True)
class Asset:
    """One numbered copy: a card (or a sealed pack) and the last team we know holds it."""

    id: int
    ref: str
    kind: str  # "card" | "pack"
    holder: str | None  # a team id; None when the trail is lost (a dealer bought it, or "a team")
    origin: str  # "start" | "scan" | "feed" | "me"
    tick: int | None  # when we last saw it move (None: the starting deal)


def scan_holder(row: Mapping[str, Any]) -> str | None:
    """Who holds a scanned asset: the owner when the API names a team; else the starting team, but only
    while its history is the starting grant alone (it never moved)."""
    owner = row.get("owner")
    if is_team(owner):
        return str(owner)
    history = row.get("history") or []
    moved = [h for h in history if isinstance(h, dict) and h.get("why") != "starting grant"]
    return start_team(int(row["id"])) if not moved else None


def assets_from_scan(scan: Iterable[Mapping[str, Any]]) -> dict[int, Asset]:
    out = {}
    for row in scan:
        if isinstance(row.get("id"), int) and row.get("ref"):
            aid = int(row["id"])
            origin = "start" if aid <= START_ASSETS else "scan"
            out[aid] = Asset(aid, str(row["ref"]), str(row.get("kind") or "card"), scan_holder(row), origin, None)
    return out


def _moves(events: Iterable[Event]) -> Iterable[tuple[int, str, str, str | None, int | None]]:
    """(asset id, ref, kind, holder, tick) for every asset the feed shows changing hands or listed."""
    for e in sorted(events, key=lambda e: int(e.get("id") or 0)):
        p, tick = e.get("payload") or {}, e.get("tick")
        if e.get("type") == "settlement":
            for item in p.get("items") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), int) and item.get("ref"):
                    holder = str(item["to"]) if is_team(item.get("to")) else None
                    yield item["id"], str(item["ref"]), str(item.get("kind") or "card"), holder, tick
        elif e.get("type") == "offer.listed" and is_team(e.get("actor")):
            for a in ((p.get("offer") or {}).get("give") or {}).get("assets") or []:
                if isinstance(a, dict) and isinstance(a.get("id"), int) and a.get("ref"):
                    yield a["id"], str(a["ref"]), str(a.get("kind") or "card"), str(e["actor"]), tick


def asset_map(scan: Iterable[Mapping[str, Any]], events: Iterable[Event], me: Mapping[str, Any]) -> dict[int, Asset]:
    """Every asset we can place: the scan, then the feed in event order, then `/api/me` for ours (an asset
    the map still gives us that `/me` no longer holds has left: holder unknown)."""
    assets = assets_from_scan(scan)
    for aid, ref, kind, holder, tick in _moves(events):
        assets[aid] = Asset(aid, ref, kind, holder, "feed", tick)
    us = str(me.get("id") or "")
    ours = {int(a["id"]): a for a in me.get("assets") or [] if isinstance(a, dict) and isinstance(a.get("id"), int)}
    for aid, a in assets.items():
        if a.holder == us and aid not in ours:
            assets[aid] = Asset(aid, a.ref, a.kind, None, a.origin, a.tick)
    for aid, row in ours.items():
        prev = assets.get(aid)
        kind = str(row.get("kind") or "card")
        assets[aid] = Asset(aid, str(row.get("ref")), kind, us, "me", prev.tick if prev else None)
    return assets


def packs_opened(events: Iterable[Event]) -> Counter[str]:
    """Packs each team opened (the feed's `pack.opened`): every one minted new copies."""
    return Counter(str((e.get("payload") or {}).get("team")) for e in events if e.get("type") == "pack.opened")


@dataclass(frozen=True)
class CardSupply:
    ref: str
    set_code: str
    rarity: str
    page: bool
    minted: int
    print_run: int
    ours: int
    holders: tuple[tuple[str, int], ...]  # other teams and the copies we place with them, most first
    unplaced: int  # copies minted that we cannot place (dealer stock, unscanned pulls, anonymous moves)

    @property
    def others(self) -> int:
        return sum(n for _, n in self.holders)

    @property
    def mintable(self) -> int:
        return max(0, self.print_run - self.minted)


@dataclass(frozen=True)
class SetSupply:
    set_code: str
    released: bool
    pages_possible: int  # complete pages that can exist now: the fewest copies of any page card
    bottleneck: tuple[str, ...]  # the page cards with that fewest number of copies
    our_have: int
    page_cards: int


@dataclass(frozen=True)
class SupplyMap:
    tick: int | None
    cards: dict[str, CardSupply]
    sets: dict[str, SetSupply]
    packs_opened: dict[str, int]
    assets: int  # assets placed
    scanned: int  # of those, from a card scan

    def sellers(self, ref: str, exclude: Iterable[str] = ()) -> tuple[str, ...]:
        """Teams we place a copy with, minus `exclude` (e.g. the teams that chase that set)."""
        skip = set(exclude)
        card = self.cards.get(ref)
        return tuple(t for t, _ in card.holders if t not in skip) if card else ()


def card_supply(
    catalog: Mapping[str, Any], assets: Mapping[int, Asset], us: str, held: Mapping[str, int]
) -> dict[str, CardSupply]:
    placed: dict[str, Counter[str]] = defaultdict(Counter)
    for a in assets.values():
        if a.kind == "card" and a.holder and a.holder != us:
            placed[a.ref][a.holder] += 1
    out = {}
    for s in catalog.get("sets") or []:
        for c in s.get("cards") or []:
            ref = str(c.get("id"))
            minted, ours = int(c.get("minted") or 0), int(held.get(ref, 0))
            holders = tuple(sorted(placed[ref].items(), key=lambda kv: (-kv[1], kv[0])))
            others = sum(n for _, n in holders)
            out[ref] = CardSupply(
                ref,
                str(s.get("id")),
                str(c.get("rarity")),
                bool(c.get("page")),
                minted,
                int(c.get("print_run") or 0),
                ours,
                holders,
                max(0, minted - ours - others),
            )
    return out


def set_supply(
    catalog: Mapping[str, Any], cards: Mapping[str, CardSupply], released: Sequence[str]
) -> dict[str, SetSupply]:
    out = {}
    for s in catalog.get("sets") or []:
        code = str(s.get("id"))
        page = [cards[str(c.get("id"))] for c in s.get("cards") or [] if c.get("page") and str(c.get("id")) in cards]
        fewest = min((c.minted for c in page), default=0)
        out[code] = SetSupply(
            code,
            code in released,
            fewest,
            tuple(sorted(c.ref for c in page if c.minted == fewest)),
            sum(1 for c in page if c.ours > 0),
            len(page),
        )
    return out


def supply_map(
    catalog: Mapping[str, Any],
    me: Mapping[str, Any],
    events: Sequence[Event],
    scan: Iterable[Mapping[str, Any]] = (),
) -> SupplyMap:
    us = str(me.get("id") or "")
    scan_rows = list(scan)
    assets = asset_map(scan_rows, events, me)
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if isinstance(a, dict) and a.get("kind") == "card")
    cards = card_supply(catalog, assets, us, held)
    released = [str(p.get("set")) for p in (me.get("album") or {}).get("pages") or [] if isinstance(p, dict)]
    return SupplyMap(
        tick=me.get("tick") if isinstance(me.get("tick"), int) else None,
        cards=cards,
        sets=set_supply(catalog, cards, released),
        packs_opened=dict(packs_opened(events)),
        assets=len(assets),
        scanned=sum(1 for r in scan_rows if isinstance(r.get("id"), int)),
    )


def scan_diff(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]) -> dict[str, list[int]]:
    """Two scans compared: new asset ids, and ids whose history grew (they changed hands)."""
    old = {int(r["id"]): len(r.get("history") or []) for r in before if isinstance(r.get("id"), int)}
    new = {int(r["id"]): len(r.get("history") or []) for r in after if isinstance(r.get("id"), int)}
    return {
        "added": sorted(set(new) - set(old)),
        "moved": sorted(i for i in set(new) & set(old) if new[i] > old[i]),
    }
