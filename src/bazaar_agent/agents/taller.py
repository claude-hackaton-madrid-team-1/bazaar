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

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from bazaar_agent.agents.seller import UNSETTLED_TICKS, open_commitments
from bazaar_agent.guardrails import (
    TALLER_RARITIES,
    Action,
    Context,
    Guardrails,
    LedgerStore,
    Verdict,
    check,
    context_from,
    is_pack,
)

TALLER_PATH = "/api/taller"
LEVEL_ID = "taller"
INPUTS = 3
# The ledger row of a craft: kind `spend` at price 0 (the shared table only takes spend, accept and listing), item
# `taller:<refs>`. It adds nothing to a spend or pack count; every process counts it by this prefix (the hourly cap).
TALLER_ITEM = "taller:"
CARD_REF = re.compile(r"^[A-Z]{3}-\d{2}$")


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


def free_spares(
    me: Mapping[str, Any], catalog: Mapping[str, Any], busy: Iterable[int] = (), crowded: int | None = None
) -> dict[str, list[Spare]]:
    """rarity -> our free spare copies, cheapest to give up first. `busy`: copies an open offer of ours gives (or
    a recent sell may still take): never free. Per card one free copy always stays (a page never loses its last
    copy, whatever the set), and a copy whose card or value we cannot read is never a spare. `crowded`
    (`max_copies_kept`): copies of a card we hold more often than this go first."""
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
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if isinstance(a, Mapping))

    def order(s: Spare) -> tuple[bool, float, str, int]:
        return (crowded is not None and held[s.ref] <= crowded, s.your_value, s.ref, s.asset_id)

    return {r: sorted(v, key=order) for r, v in out.items()}


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
    crowded: int | None = None,
) -> list[Triple]:
    """One triple per rarity with three free spares (the three we lose least by), best first: one whose pull may
    fill a missing page slot, then the highest dealer level that buys the result (the ladder weighs higher levels
    more), then the cheapest to give up. A triple worth more to us than the result's book value is never made, and
    only commons and uncommons go in (`guardrails.TALLER_RARITIES`)."""
    order, spares = rarity_order(catalog), free_spares(me, catalog, busy, crowded)
    books = catalog.get("rarities") or {}
    dealer_list = list(dealers)
    out = []
    for rarity, copies in spares.items():
        to = next_rarity(rarity, order)
        if to is None or rarity not in TALLER_RARITIES or len(copies) < INPUTS:
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
    """What the answer says we pulled (its shape is not documented: a card object or ref, top level or nested)."""
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


# ---------------------------------------------------------------- one craft: the taker's and `bazaar taller`'s path


def settling(ledger: LedgerStore, tick: int, me: Mapping[str, Any]) -> tuple[frozenset[int], str | None]:
    """Copies an accept of the last UNSETTLED_TICKS may still hand over (/me shows them until it settles), from the
    shared ledger. `sell:<asset>` names its copy; a plain card ref (a dealer sell, or a buy) takes one copy of that
    card, the cheapest; a `duel:` or a pack gives no card. Any other `<kind>:<id>` (a team swap's `team:<thread>`)
    cannot name its copy: the reason no craft goes this tick (fail closed)."""
    copies: dict[str, list[tuple[float, int]]] = {}
    for a in me.get("assets") or []:
        if isinstance(a, Mapping) and a.get("kind") == "card" and isinstance(a.get("id"), int):
            value = a.get("your_value")
            v = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
            copies.setdefault(str(a.get("ref")), []).append((v, int(a["id"])))
    busy: set[int] = set()
    opaque: set[str] = set()
    for t in range(tick - UNSETTLED_TICKS, tick + 1):
        for item in ledger.accept_items(t):
            kind, _, rest = item.partition(":")
            if not item or kind == "duel" or is_pack(item):
                continue
            if kind == "sell" and rest.isdigit():
                busy.add(int(rest))
            elif not rest:
                free = sorted(c for c in copies.get(item, []) if c[1] not in busy)
                if free:
                    busy.add(free[0][1])
            else:
                opaque.add(item)
    why = f"accept {', '.join(sorted(opaque))} still settling may hand over a copy we cannot name" if opaque else None
    return frozenset(busy), why


def busy_copies(
    me: Mapping[str, Any], offers: Iterable[dict[str, Any]], ledger: LedgerStore, tick: int
) -> tuple[frozenset[int], str | None]:
    """Every copy that is not free: in an open (or accepted, settling) offer of ours, board or thread, and in an
    accept still settling (`settling`); plus the reason to hold every craft this tick, if any."""
    listed = open_commitments(offers, str(me.get("id") or "")).listed
    held_back, hold = settling(ledger, tick, me)
    return frozenset(listed | held_back), hold


def craft_context(
    me: Mapping[str, Any],
    busy: Iterable[int],
    hold: str | None,
    tick: int,
    t_hours: float,
    ledger: LedgerStore,
    rules: Guardrails,
) -> Context:
    """The guardrail context of a craft on a /me read just before: the kill switch, the free copies of each card
    (`sellable`), the crafts every process booked this game hour (`taller_last_hour`, shared ledger) and `hold`."""
    base = context_from(dict(me), tick, t_hours, ledger, rules)
    done = ledger.count_since("spend", t_hours - 1.0, TALLER_ITEM)
    return replace(base, sellable=free_counts(me, busy), taller_last_hour=done, taller_hold=hold)


def triple_from_ids(
    me: Mapping[str, Any], catalog: Mapping[str, Any], asset_ids: Sequence[int], busy: Iterable[int] = ()
) -> Triple:
    """Three hand-picked copies (`bazaar taller a b c`). Never a busy copy; the guardrails still check the rest
    (one rarity, commons or uncommons, a free copy of each card kept, the cap)."""
    if len(asset_ids) != INPUTS or len(set(asset_ids)) != INPUTS:
        raise ValueError(f"the Workshop takes {INPUTS} different asset ids, not {list(asset_ids)}")
    taken, cards = set(busy), cards_of(catalog)
    by_id = {a.get("id"): a for a in me.get("assets") or [] if isinstance(a, Mapping) and a.get("kind") == "card"}
    spares = []
    for aid in asset_ids:
        a = by_id.get(aid)
        if a is None:
            raise ValueError(f"asset {aid} is not a card we hold (read /api/me)")
        if aid in taken:
            raise ValueError(f"asset {aid} is in an offer of ours or an accept still settling")
        value = a.get("your_value")
        v = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
        spares.append(Spare(int(aid), str(a.get("ref")), str((cards.get(str(a.get("ref"))) or {}).get("rarity")), v))
    rarities = {s.rarity for s in spares}
    rarity = rarities.pop() if len(rarities) == 1 else "mixed"
    to = next_rarity(rarity, rarity_order(catalog)) or "?"
    return Triple(rarity, to, tuple(spares), missing_slots(me, catalog, to), None, 0)


def craft_action(triple: Triple) -> Action:
    return Action("taller", action_item(triple), triple.rarity, assets=tuple(triple.asset_ids))


@dataclass(frozen=True)
class Crafted:
    verdict: Verdict
    sent: bool  # the request went out (live, allowed)
    answer: Any  # the server's answer as received (None: refused or not sent)
    decision_id: int


def craft_one(
    team: Any,
    triple: Triple,
    ctx: Context,
    rules: Guardrails,
    rec: Any,
    ledger: LedgerStore,
    *,
    tick: int,
    t_hours: float,
    live: bool,
) -> Crafted:
    """Check, record and (live) send ONE craft. The ledger row is booked BEFORE the send, so a refusal over-counts
    the hourly cap (fail safe), never under-counts it. The answer is recorded as received (`executions`, scrubbed)
    by `rec.send` before anything reads it; `pulled` then reads it whatever its shape, and never raises."""
    verdict = check(craft_action(triple), ctx, rules)
    given = ", ".join(f"{s.ref} #{s.asset_id}" for s in triple.spares)
    did = int(
        rec.decide(
            tick,
            "taller",
            f"Workshop: {given} → 1 {triple.to_rarity} · {verdict}",
            inputs={
                "assets": triple.asset_ids,
                "refs": triple.refs,
                "rarity": triple.rarity,
                "to": triple.to_rarity,
                "cost": triple.cost,
                "fills": list(triple.fills),
                "buyer": triple.buyer,
                "buyer_level": triple.buyer_level,
                "taller_last_hour": ctx.taller_last_hour,
            },
            reason=triple.reason(),
            guardrail=str(verdict),
            chosen=verdict.allowed,
            status="approved" if verdict.allowed else "rejected",
            move={"taller": {"assets": triple.asset_ids}},
        )
    )
    if not verdict.allowed or not live:
        return Crafted(verdict, False, None, did)
    ledger.record("spend", tick, t_hours, 0, TALLER_ITEM + action_item(triple))
    answer = rec.send(did, tick, "taller", {"assets": triple.asset_ids}, lambda: craft(team, triple.asset_ids))
    return Crafted(verdict, True, answer, did)
