"""Read the public feed like a trading system's market data: tape, dealer quotes, team flow, book.

Pure functions over feed events (see `feed.py`); no network, so every view is testable offline.
Observed shapes (2026-10-02): `settlement` carries parties, items (frm/to) and price;
`thread.message` carries the structured offer (and the dealer's text; a team's text is null);
`offer.listed` carries the real team id, while the venue board shows only a pseudonym.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from statistics import median
from typing import Any

Event = dict[str, Any]
TEAM_ID = re.compile(r"^t\d+\Z")  # \Z, not $: "t05\n" is not a team id


# ---------------------------------------------------------------- us vs the competition


def is_ours(event: Event, team: str | None) -> bool:
    """True when our team did it or it is about us: we are its actor, its team, owner or maker, or a
    party to the settlement. Feed tables keep every event; competitor views and alerts skip these."""
    if not team:
        return False
    p = event.get("payload") or {}
    offer = p.get("offer")
    maker = offer.get("maker") if isinstance(offer, dict) else None
    named = (event.get("actor"), p.get("team"), p.get("with"), p.get("owner"), p.get("sender"), maker)
    parties = p.get("parties")
    return team in named or (isinstance(parties, list) and team in parties)


def split_us[T](rows: Iterable[T], team: str | None, key: Callable[[T], str]) -> tuple[list[T], list[T]]:
    """(the competition, us): our rows are tagged and shown apart, never dropped."""
    theirs: list[T] = []
    us: list[T] = []
    for row in rows:
        (us if team and key(row) == team else theirs).append(row)
    return theirs, us


def _cash(side: dict[str, Any] | None) -> int:
    return int((side or {}).get("cash") or 0)


def offer_price(offer: dict[str, Any]) -> int | None:
    """The cash amount of a one-sided cash offer, whichever side carries it."""
    price = _cash(offer.get("give")) or _cash(offer.get("want"))
    return price or None


def set_of(ref: str | None) -> str | None:
    """'LAV-07' -> 'LAV'; packs and other refs have no set."""
    if not ref or "-" not in ref:
        return None
    prefix = ref.split("-", 1)[0]
    return prefix if prefix.isalpha() and prefix.isupper() else None


# ---------------------------------------------------------------- tape


@dataclass(frozen=True)
class Print:
    settlement: int
    tick: int
    buyer: str
    seller: str
    persona: str | None
    venue: str | None
    ref: str
    kind: str
    items: int
    price: int
    fee: int


def tape(events: Iterable[Event]) -> list[Print]:
    prints = []
    for e in events:
        if e.get("type") != "settlement":
            continue
        p = e.get("payload") or {}
        items = p.get("items") or []
        if not items:
            continue
        first = items[0]
        prints.append(
            Print(
                settlement=int(p.get("settlement") or e["id"]),
                tick=int(p.get("tick") or e.get("tick") or 0),
                buyer=str(first.get("to")),
                seller=str(first.get("frm")),
                persona=p.get("persona"),
                venue=p.get("venue"),
                ref=str(first.get("ref")),
                kind=str(first.get("kind")),
                items=len(items),
                price=int(p.get("price") or 0),
                fee=int(p.get("fee") or 0),
            )
        )
    return prints


def book_values(catalog: dict[str, Any] | None) -> dict[str, float]:
    """card ref -> book value, from `/api/catalog`."""
    return {
        str(c["id"]): float(c.get("book") or 0)
        for s in (catalog or {}).get("sets") or []
        for c in s.get("cards") or []
        if c.get("id")
    }


def settled_volume(events: Iterable[Event], us: str, book: dict[str, float] | None = None) -> dict[str, int]:
    """Primas we settled with each other team: the notional of every team-to-team settlement we are a party
    to, the larger of its cash and the book of the cards that moved (a swap has no cash). Dealers are not
    counterparties."""
    out: Counter[str] = Counter()
    for e in events:
        p = e.get("payload") or {}
        if e.get("type") != "settlement" or p.get("persona") or us not in (p.get("parties") or []):
            continue
        others = {str(x) for x in p.get("parties") or [] if x != us and TEAM_ID.match(str(x))}
        if len(others) != 1:
            continue
        items = p.get("items") or []
        books = round(sum((book or {}).get(str(i.get("ref")), 0.0) for i in items))
        out[others.pop()] += max(int(p.get("price") or 0), books)
    return dict(out)


# ---------------------------------------------------------------- dealer threads (quotes)


@dataclass
class DealerThread:
    thread: int
    team: str
    dealer: str
    side: str  # "buy" or "sell", from the team's point of view
    item: str
    opened_tick: int
    asset_ids: tuple[int, ...] = ()
    team_prices: list[int] = field(default_factory=list)
    dealer_prices: list[int] = field(default_factory=list)
    final_price: int | None = None
    last_tick: int = 0
    fill_price: int | None = None
    fill_tick: int | None = None
    ours: bool = False  # our own thread (see `dealer_threads(..., ours=)`)

    @property
    def opening_ask(self) -> int | None:
        return self.dealer_prices[0] if self.dealer_prices else None

    @property
    def steps(self) -> int:
        return len(self.team_prices)


def _topic_item(topic: dict[str, Any]) -> tuple[str, str, tuple[int, ...]]:
    for side in ("buy", "sell"):
        spec = topic.get(side)
        if isinstance(spec, dict):
            if "pack" in spec:
                return side, str(spec["pack"]), ()
            if "card" in spec:
                return side, str(spec["card"]), ()
            if "assets" in spec:
                ids = tuple(int(a) for a in spec["assets"] if isinstance(a, int))
                return side, f"assets:{','.join(map(str, ids))}", ids
            if "rarity" in spec:
                return side, f"{spec.get('rarity')}:{spec.get('set', '*')}", ()
    return "?", "?", ()


def _matches(thread: DealerThread, p: Print, items: list[dict[str, Any]]) -> bool:
    if p.persona != thread.dealer or thread.team not in (p.buyer, p.seller):
        return False
    if thread.asset_ids:
        return any(i.get("id") in thread.asset_ids for i in items)
    if ":" in thread.item:  # rarity request: any item from the dealer to the team
        return p.buyer == thread.team
    return any(i.get("ref") == thread.item for i in items)


def dealer_threads(events: Iterable[Event], ours: str | None = None) -> list[DealerThread]:
    """Every dealer conversation in the feed, ours and other teams', with its fill if one settled.
    With `ours` (our team id) our own threads carry `ours=True`."""
    threads: dict[int, DealerThread] = {}
    settlements: list[tuple[Print, list[dict[str, Any]]]] = []
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "thread.opened" and p.get("kind") == "persona":
            side, item, ids = _topic_item(p.get("topic") or {})
            threads[int(p["thread"])] = DealerThread(
                thread=int(p["thread"]),
                team=str(p.get("team")),
                dealer=str(p.get("with")),
                side=side,
                item=item,
                opened_tick=int(e.get("tick", 0)),
                asset_ids=ids,
                last_tick=int(e.get("tick", 0)),
                ours=bool(ours) and p.get("team") == ours,
            )
        elif kind == "thread.message" and p.get("kind") == "persona":
            t = threads.get(int(p.get("thread", -1)))
            offer = p.get("offer") or {}
            price = offer_price(offer) if offer else None
            if t is None or price is None:
                continue
            t.last_tick = int(e.get("tick", t.last_tick))
            if p.get("sender") == t.dealer:
                t.dealer_prices.append(price)
                if offer.get("final"):
                    t.final_price = price
            else:
                t.team_prices.append(price)
        elif kind == "settlement":
            for pr in tape([e]):
                settlements.append((pr, (e.get("payload") or {}).get("items") or []))
    used: set[int] = set()
    # Newest thread first: a team has one open conversation per dealer, so a fill belongs to the
    # latest thread opened before it, never to an older one that was abandoned without a deal.
    for t in sorted(threads.values(), key=lambda x: (x.opened_tick, x.thread), reverse=True):
        for pr, items in settlements:
            if pr.settlement in used or pr.tick < t.opened_tick or not _matches(t, pr, items):
                continue
            t.fill_price, t.fill_tick = pr.price, pr.tick
            used.add(pr.settlement)
            break
    return sorted(threads.values(), key=lambda x: x.thread)


@dataclass(frozen=True)
class CurveSummary:
    dealer: str
    item: str
    threads: int
    fills: int
    fill_min: int | None
    fill_median: float | None
    fill_max: int | None
    opening_ask_median: float | None
    final_median: float | None
    steps_to_fill_median: float | None
    ours: int = 0  # how many of the threads are ours


def curve_summary(threads: Iterable[DealerThread]) -> list[CurveSummary]:
    groups: dict[tuple[str, str], list[DealerThread]] = defaultdict(list)
    for t in threads:
        groups[(t.dealer, t.item)].append(t)

    def med(values: list[int]) -> float | None:
        return float(median(values)) if values else None

    out = []
    for (dealer, item), ts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        fills = [t.fill_price for t in ts if t.fill_price is not None]
        out.append(
            CurveSummary(
                dealer=dealer,
                item=item,
                threads=len(ts),
                fills=len(fills),
                fill_min=min(fills) if fills else None,
                fill_median=med(fills),
                fill_max=max(fills) if fills else None,
                opening_ask_median=med([t.opening_ask for t in ts if t.opening_ask is not None]),
                final_median=med([t.final_price for t in ts if t.final_price is not None]),
                steps_to_fill_median=med([t.steps for t in ts if t.fill_price is not None]),
                ours=sum(t.ours for t in ts),
            )
        )
    return out


# ---------------------------------------------------------------- team flow (competition)


@dataclass
class TeamFlow:
    team: str
    dealer_threads: int = 0
    bids: int = 0
    buys: int = 0
    sells: int = 0
    spent: int = 0
    earned: int = 0
    pack_prices: list[int] = field(default_factory=list)
    listings: int = 0
    set_interest: Counter[str] = field(default_factory=Counter)

    @property
    def avg_pack_price(self) -> float | None:
        return sum(self.pack_prices) / len(self.pack_prices) if self.pack_prices else None

    @property
    def top_set(self) -> str | None:
        """Most-chased set: a hint at the team's ×1.6 affinity (every team has exactly one)."""
        best = [(n, s) for s, n in self.set_interest.items() if n > 0]
        return max(best)[1] if best else None


def team_flows(events: Iterable[Event]) -> list[TeamFlow]:
    flows: dict[str, TeamFlow] = {}

    def flow(team: str) -> TeamFlow:
        return flows.setdefault(team, TeamFlow(team))

    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "thread.opened" and p.get("team"):
            f = flow(str(p["team"]))
            f.dealer_threads += 1 if p.get("kind") == "persona" else 0
            card = ((p.get("topic") or {}).get("buy") or {}).get("card")
            if s := set_of(card):
                f.set_interest[s] += 1
        elif kind == "thread.message" and p.get("sender") == p.get("team") and p.get("team"):
            flow(str(p["team"])).bids += 1
        elif kind == "offer.listed" and e.get("actor"):
            f = flow(str(e["actor"]))
            f.listings += 1
            offer = p.get("offer") or {}
            for a in (offer.get("give") or {}).get("assets") or []:
                if s := a.get("set") or set_of(a.get("ref")):
                    f.set_interest[s] -= 1  # selling a set's card: likely not their ×1.6 set
            for ref in (offer.get("want") or {}).get("types") or []:
                if s := set_of(str(ref).split(":")[-1]):
                    f.set_interest[s] += 1
        elif kind == "settlement":
            for pr in tape([e]):
                for team, is_buyer in ((pr.buyer, True), (pr.seller, False)):
                    if not team.startswith("t"):
                        continue
                    f = flow(team)
                    if is_buyer:
                        f.buys += 1
                        f.spent += pr.price
                        if pr.kind == "pack":
                            f.pack_prices.append(pr.price)
                        if s := set_of(pr.ref):
                            f.set_interest[s] += 1
                    else:
                        f.sells += 1
                        f.earned += pr.price
                        if s := set_of(pr.ref):
                            f.set_interest[s] -= 1
    return sorted(flows.values(), key=lambda f: f.team)


# ---------------------------------------------------------------- order book


@dataclass(frozen=True)
class BookLine:
    offer_id: int
    side: str  # "ask" (selling a card for cash) or "bid" (cash for a card)
    card: str
    price: int
    maker: str  # real team id when the feed revealed it, else the board pseudonym
    pseudonym: str
    expires_tick: int | None


def listed_makers(events: Iterable[Event]) -> dict[int, str]:
    """offer id -> real team id, from public `offer.listed` events."""
    makers = {}
    for e in events:
        if e.get("type") == "offer.listed" and e.get("actor"):
            offer = (e.get("payload") or {}).get("offer") or {}
            if isinstance(offer.get("id"), int):
                makers[offer["id"]] = str(e["actor"])
    return makers


def order_book(board_offers: Iterable[dict[str, Any]], makers: dict[int, str]) -> list[BookLine]:
    lines = []
    for o in board_offers:
        give, want = o.get("give") or {}, o.get("want") or {}
        assets = give.get("assets") or []
        wanted: list[Any] = want.get("types") or want.get("cards") or []
        if assets and _cash(want):
            side, card, price = "ask", str(assets[0].get("ref")), _cash(want)
        elif _cash(give) and wanted:
            side, card, price = "bid", str(wanted[0]).split(":")[-1], _cash(give)
        else:
            continue
        oid = int(o["id"])
        pseud = str(o.get("maker"))
        lines.append(BookLine(oid, side, card, price, makers.get(oid, pseud), pseud, o.get("expires_tick")))
    return sorted(lines, key=lambda b: (b.card, b.side, b.price if b.side == "ask" else -b.price))
