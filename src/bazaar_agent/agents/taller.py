"""The Workshop (`taller`, a level): three spare copies of one rarity become one card of the next rarity.

`/api/levels` (Sat 3 Oct, active since game hour 7.2): `POST /api/taller {"assets": [a, b, c]}`: three spare copies
of one rarity (you keep at least one of each card) become one card of the next rarity. The pull is luck, shown and
never scored. So a craft scores nothing by itself: it turns duplicates worth 0.1-0.25x to us into a card that may
fill a missing page slot or be sold to a higher-level dealer on the ladder (Chato, Pilar and Los Pícaros buy
uncommons; Pilar and Chato rares; Don Ernesto epics).

Only FREE spares go in: per card, the copies we hold minus the copies an open offer of ours gives (or a sell of
the last ticks may still take) minus one we always keep. The route is not in `docs/api/openapi.json`; its shape is
the level's own `how` above, sent through the team client's `call` (one request, a write is never re-sent).
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bazaar_agent.intel import TEAM_ID

TALLER_PATH = "/api/taller"
LEVEL_ID = "taller"
INPUTS = 3


@dataclass(frozen=True)
class Spare:
    asset_id: int
    ref: str
    rarity: str
    your_value: float


@dataclass(frozen=True)
class Triple:
    rarity: str
    to_rarity: str
    spares: tuple[Spare, ...]
    fills: tuple[str, ...]  # missing page cards of `to_rarity` in released sets: a pull may complete a slot
    buyer: str | None  # the highest-level dealer we can reach that buys `to_rarity` (a ladder sale)
    buyer_level: int

    @property
    def asset_ids(self) -> list[int]:
        return [s.asset_id for s in self.spares]

    @property
    def refs(self) -> list[str]:
        return [s.ref for s in self.spares]

    @property
    def cost(self) -> float:
        """What the three copies are worth to us (`your_value`): what the craft gives up."""
        return round(sum(s.your_value for s in self.spares), 1)

    def reason(self) -> str:
        fills = f"may fill {len(self.fills)} missing {self.to_rarity} slot(s)" if self.fills else "fills no slot"
        sale = f"{self.buyer} (L{self.buyer_level}) buys {self.to_rarity}" if self.buyer else "no dealer buys it"
        return f"3 spare {self.rarity}s worth {self.cost:g} to us → 1 {self.to_rarity}: {fills}; {sale}"


def rarity_order(catalog: Mapping[str, Any]) -> tuple[str, ...]:
    """Rarities cheapest first, by their catalog book value."""
    rarities = catalog.get("rarities") or {}
    if not isinstance(rarities, Mapping):
        return ()
    return tuple(sorted(rarities, key=lambda r: float((rarities[r] or {}).get("book") or 0)))


def next_rarity(rarity: str, order: Sequence[str]) -> str | None:
    if rarity not in order:
        return None
    i = list(order).index(rarity)
    return order[i + 1] if i + 1 < len(order) else None


def cards_of(catalog: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(c["id"]): c
        for s in catalog.get("sets") or []
        if isinstance(s, Mapping)
        for c in s.get("cards") or []
        if isinstance(c, Mapping) and c.get("id")
    }


def free_spares(me: Mapping[str, Any], catalog: Mapping[str, Any], busy: Iterable[int] = ()) -> dict[str, list[Spare]]:
    """rarity -> our free spare copies, cheapest to give up first. `busy`: copies an open offer of ours gives (or
    a recent sell may still take): never free. Per card one free copy always stays (a page never loses its last
    copy, whatever the set), and a copy whose card or value we cannot read is never a spare."""
    cards, taken = cards_of(catalog), set(busy)
    free: dict[str, list[Spare]] = {}
    for a in me.get("assets") or []:
        if not isinstance(a, Mapping) or a.get("kind") != "card" or not isinstance(a.get("id"), int):
            continue
        card, value = cards.get(str(a.get("ref"))), a.get("your_value")
        if card is None or card.get("hidden") or not isinstance(value, int | float) or isinstance(value, bool):
            continue
        if a["id"] in taken:
            continue
        free.setdefault(str(a["ref"]), []).append(Spare(int(a["id"]), str(a["ref"]), str(card.get("rarity")), value))
    out: dict[str, list[Spare]] = {}
    for copies in free.values():
        kept = sorted(copies, key=lambda s: (-s.your_value, s.asset_id))[0]  # the copy we keep: the most valued
        for s in copies:
            if s is not kept:
                out.setdefault(s.rarity, []).append(s)
    return {r: sorted(v, key=lambda s: (s.your_value, s.ref, s.asset_id)) for r, v in out.items()}


def free_counts(me: Mapping[str, Any], busy: Iterable[int] = ()) -> dict[str, int]:
    """card ref -> the copies we hold that no open offer of ours gives (`busy`): what the guardrails' keep-one rule
    counts (`Context.sellable`)."""
    taken = set(busy)
    return dict(
        Counter(
            str(a.get("ref"))
            for a in me.get("assets") or []
            if isinstance(a, Mapping) and a.get("kind") == "card" and a.get("id") not in taken
        )
    )


def sell_thread_assets(threads: Iterable[Any]) -> set[int]:
    """Our copies a sell thread of ours is about (topic `{"sell": {"assets": [id]}}`, `/api/me/threads`): the dealer
    may take our ask with words only, so such a copy is never free."""
    out: set[int] = set()
    for t in threads:
        if not isinstance(t, Mapping) or TEAM_ID.match(str(t.get("with") or "")):
            continue  # a team thread's topic is the other team's choice (#239 review): only our dealer threads
        topic = t.get("topic")
        sell = topic.get("sell") if isinstance(topic, Mapping) else None
        assets = sell.get("assets") if isinstance(sell, Mapping) else None
        for a in assets if isinstance(assets, list) else []:
            aid = a.get("id") if isinstance(a, Mapping) else a
            if isinstance(aid, int) and not isinstance(aid, bool):
                out.add(aid)
    return out


def missing_slots(me: Mapping[str, Any], catalog: Mapping[str, Any], rarity: str) -> tuple[str, ...]:
    """Page cards of `rarity` in our released album pages that we hold no copy of."""
    released = {str(p.get("set")) for p in (me.get("album") or {}).get("pages") or [] if isinstance(p, Mapping)}
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if isinstance(a, Mapping))
    return tuple(
        sorted(
            ref
            for ref, c in cards_of(catalog).items()
            if c.get("page") and c.get("rarity") == rarity and ref.split("-", 1)[0] in released and not held[ref]
        )
    )


def best_buyer(dealers: Iterable[Any], unlocked: Iterable[str], rarity: str) -> tuple[str | None, int]:
    """The highest-level active dealer we have unlocked whose `/api/dealers` menu buys `rarity` (any set)."""
    ours = set(unlocked)
    best: tuple[str | None, int] = (None, 0)
    for d in dealers:
        if not isinstance(d, Mapping) or d.get("status") != "active" or str(d.get("id")) not in ours:
            continue
        buys = (d.get("menu") or {}).get("buys") or []
        raw = d.get("level")
        level = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
        if any(isinstance(b, Mapping) and b.get("rarity") == rarity for b in buys) and level > best[1]:
            best = (str(d["id"]), level)
    return best


def rank_triples(
    me: Mapping[str, Any],
    catalog: Mapping[str, Any],
    dealers: Iterable[Any],
    busy: Iterable[int] = (),
) -> list[Triple]:
    """One triple per rarity with three free spares (the three we lose least by), best first: one whose pull may
    fill a missing page slot, then the highest dealer level that buys the result (the ladder weighs higher levels
    more), then the cheapest to give up. A triple worth more to us than the result's book value is never made."""
    order, spares = rarity_order(catalog), free_spares(me, catalog, busy)
    books = catalog.get("rarities") or {}
    dealer_list = list(dealers)
    out = []
    for rarity, copies in spares.items():
        to = next_rarity(rarity, order)
        if to is None or len(copies) < INPUTS:
            continue
        pick = tuple(copies[:INPUTS])
        buyer, level = best_buyer(dealer_list, me.get("unlocked") or [], to)
        triple = Triple(rarity, to, pick, missing_slots(me, catalog, to), buyer, level)
        if triple.cost <= float((books.get(to) or {}).get("book") or 0):
            out.append(triple)
    return sorted(out, key=lambda t: (not t.fills, -t.buyer_level, t.cost, t.rarity))


def action_item(triple: Triple) -> str:
    """The guardrail action's item: the three card refs (`guardrails._taller_violations` reads them back)."""
    return ",".join(triple.refs)


def craft(client: Any, asset_ids: Sequence[int]) -> Any:
    """POST /api/taller: one request through the team client (a write is never re-sent)."""
    return client.call("POST", TALLER_PATH, {"assets": [int(a) for a in asset_ids]})


def pulled(answer: Any) -> str:
    """What the answer says we pulled (its shape is not documented: a card object or ref, top level or nested), as
    one printable line: the game's text never reorders or breaks a log line."""
    return clean(_pulled(answer)) or "?"


def clean(text: str, cap: int = 80) -> str:
    """One printable line, capped: no control, format (bidi) or line characters from the game's text."""
    kept = "".join(ch if ch.isprintable() and not unicodedata.category(ch).startswith("C") else " " for ch in text)
    return " ".join(kept.split())[:cap]


def _pulled(answer: Any) -> str:
    if not isinstance(answer, Mapping):
        return "?"
    for key in ("card", "asset", "result"):
        got = answer.get(key)
        if isinstance(got, Mapping):
            ref, name = got.get("ref") or got.get("id"), got.get("name")
            return " ".join(str(x)[:40] for x in (ref, name) if x) or "?"
        if isinstance(got, str):
            return got[:40]
    cards = answer.get("cards")
    if isinstance(cards, list) and cards and isinstance(cards[0], Mapping):
        return str(cards[0].get("ref") or cards[0].get("name") or "?")[:40]
    return "?"


# ---------------------------------------------------------------- shared by the taker's step and `bazaar taller`

# A craft in the shared ledger: kind `spend` at price 0 (the table only takes spend, accept and listing), item
# `taller:<refs>`. It adds nothing to a spend or pack count; every process counts it by this prefix (the hourly cap).
TALLER_ITEM = "taller:"
SETTLING_TICKS = 2  # `seller.UNSETTLED_TICKS`: an accept settles on the next tick, /me may lag one more


def crafts_last_hour(ledger: Any, t_hours: float) -> int:
    """Crafts any process booked in the last game hour (`max_taller_per_game_hour`)."""
    return int(ledger.count_since("spend", t_hours - 1.0, TALLER_ITEM))


def book_craft(ledger: Any, tick: int, t_hours: float, refs: Sequence[str]) -> None:
    """Book a craft BEFORE its send: a refusal over-counts the hourly cap (fail safe), never under-counts it."""
    ledger.record("spend", tick, t_hours, 0, TALLER_ITEM + ",".join(refs))


def unnamed_settling(ledger: Any, tick: int) -> str | None:
    """Why no craft may go this tick: an accept of this or the last SETTLING_TICKS ticks that cannot name the copy
    it hands over (a team swap's `team:<thread>`). `sell:<asset>` names its copy, a plain card ref names its card
    (the step marks those busy), a `duel:` moves no card. Fail closed: any other `<kind>:<id>` holds."""
    items = {item for t in range(tick - SETTLING_TICKS, tick + 1) for item in ledger.accept_items(t)}
    unnamed = sorted(i for i in items if ":" in i and i.split(":", 1)[0] not in ("sell", "duel"))
    return f"accept {', '.join(unnamed)} still settling may hand over a copy we cannot name" if unnamed else None


def busy_copies(
    me: Mapping[str, Any],
    offers: Iterable[Mapping[str, Any]],
    threads: Iterable[Any],
    ledger: Any,
    tick: int,
    talk_refs: Iterable[str] = (),
) -> set[int]:
    """Copies never crafted, for the taker and `bazaar taller` alike: in an open (or accepted, settling) offer of
    ours (board, thread), in a sell thread of ours, in a `sell:` accept of this or the last tick, and every copy of a
    card an accept of those ticks (a card ref: a dealer sell) or a live team-desk talk (`talk_refs`) may move."""
    from bazaar_agent.agents.seller import open_commitments

    items = [item for t in (tick - 1, tick) for item in ledger.accept_items(t)]
    sold = {int(item[5:]) for item in items if item.startswith("sell:") and item[5:].isdigit()}
    refs = {item for item in items if ":" not in item and "-" in item} | set(talk_refs)
    held = [a for a in me.get("assets") or [] if isinstance(a, Mapping) and isinstance(a.get("id"), int)]
    busy = set(open_commitments([dict(o) for o in offers], str(me.get("id") or "")).listed)
    busy |= sold | sell_thread_assets(threads)
    return busy | {int(a["id"]) for a in held if str(a.get("ref")) in refs}
