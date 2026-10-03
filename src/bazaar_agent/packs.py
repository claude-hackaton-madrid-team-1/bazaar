"""Packs as inventory: what a sealed dealer pack is worth to the score, against the same cash elsewhere.

RULES.md "Scoring": pack luck never scores, and cards held score nothing by themselves. A pack scores
only through:
1. its purchase, as a dealer deal: a ladder share, if it is one of that level's best three;
2. its cards, as team trades: the surplus over our private value when we sell one to a team that
   values it more (W7: "private value scores only as surplus in a team trade").

So a pack's scored value is the expected trade surplus of reselling its cards, at the prices and
fill rates teams really trade at, and the opportunity cost is what the same cash buys on the ladder or
in W4's trades. Pure functions over the catalog, the feed and a `strategy.Market` (our holdings and
values come from `/api/me`). Assumption, unverified: a slot draws uniformly among the released sets'
page cards of its rarity.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from bazaar_agent import intel
from bazaar_agent.pages import rastro_fee
from bazaar_agent.strategy import Card, Market, copy_value, is_team


@dataclass(frozen=True)
class TapeRarity:
    """How teams traded one rarity among themselves: a single card for cash."""

    rarity: str
    listed: int  # single-card asks for cash posted by teams
    sold: int  # single-card team-to-team settlements
    median_price: float | None

    @property
    def fill_rate(self) -> float:
        return min(1.0, self.sold / self.listed) if self.listed else 0.0


def tape_by_rarity(events: Iterable[intel.Event], rarity_of: Mapping[str, str]) -> dict[str, TapeRarity]:
    """Friday's team market per rarity: asks listed, cards sold between teams, the median price paid."""
    listed: dict[str, int] = defaultdict(int)
    prices: dict[str, list[int]] = defaultdict(list)
    for e in events:
        p = e.get("payload") or {}
        if e.get("type") == "offer.listed":
            offer = p.get("offer") or p
            give, want = offer.get("give") or {}, offer.get("want") or {}
            assets = [a for a in give.get("assets") or [] if isinstance(a, dict)]
            if len(assets) == 1 and want.get("cash") and not give.get("cash"):
                listed[rarity_of.get(str(assets[0].get("ref")), "?")] += 1
        elif e.get("type") == "settlement" and not p.get("persona"):
            items = p.get("items") or []
            if len(items) == 1 and p.get("price") and is_team(str(items[0].get("to"))):
                prices[rarity_of.get(str(items[0].get("ref")), "?")].append(int(p["price"]))
    rarities = set(listed) | set(prices)
    return {
        r: TapeRarity(r, listed.get(r, 0), len(prices.get(r, [])), float(median(prices[r])) if prices.get(r) else None)
        for r in rarities
    }


@dataclass(frozen=True)
class CardOutcome:
    """One card a pack may hold, and what it is worth to us: kept (private, not scored) or resold."""

    ref: str
    rarity: str
    probability: float  # chance this card is in the pack (summed over its slots)
    keep_value: float  # our private value of this copy (scores nothing by itself)
    sale_net: float  # the tape price after El Rastro's fee
    fill: float  # chance a listing of it sells (the tape's fill rate, or 1 for a chaser in the what-if)
    chasers: int  # teams whose top set is this card's set

    @property
    def scored(self) -> float:
        """Expected trade surplus from listing it: sold with probability `fill`, at net price − our value,
        and only when that surplus is positive (otherwise we keep the card)."""
        return self.fill * max(0.0, self.sale_net - self.keep_value)


@dataclass(frozen=True)
class PackValue:
    pack: str
    price: float  # what the pack is expected to cost from the dealer
    expected_book: float
    keep_value: float  # E[private value of its cards to us]: scores nothing
    scored_surplus: float  # E[trade surplus from reselling its cards]: what scores
    resale_cash: float  # E[cash back from reselling] (cash scores nothing; it is the budget)
    cards: tuple[CardOutcome, ...] = field(default_factory=tuple)

    @property
    def surplus_per_prima(self) -> float:
        return self.scored_surplus / self.price if self.price else 0.0


def pack_cards(m: Market, slots: Sequence[Mapping[str, float]], sets: Sequence[str]) -> dict[str, float]:
    """P(each card is in the pack): every slot draws a rarity by its odds, then a page card of that rarity
    uniformly from `sets` (assumption). Probabilities add over slots (expected copies)."""
    out: dict[str, float] = defaultdict(float)
    for slot in slots:
        for rarity, odds in slot.items():
            pool = [c for c in m.cards.values() if c.set_code in sets and c.page and c.rarity == rarity]
            for c in pool:
                out[c.ref] += float(odds) / len(pool)
    return dict(out)


def pack_value(
    m: Market,
    pack: str,
    price: float,
    tape: Mapping[str, TapeRarity],
    *,
    sets: Sequence[str] | None = None,
    chasers: Mapping[str, Sequence[str]] | None = None,
    chaser_fill: float | None = None,
) -> PackValue:
    """A pack's expected value to us. `chaser_fill` is the what-if: a card whose set has a chaser sells
    with this probability (W4's model, a chaser who values it buys) instead of the tape's fill rate."""
    slots = m.packs.get(pack) or ()
    sets = list(sets or m.released)
    chasers = chasers if chasers is not None else m.chasers
    outcomes = []
    for ref, p in sorted(pack_cards(m, slots, sets).items()):
        card: Card = m.cards[ref]
        t = tape.get(card.rarity)
        gross = t.median_price if t and t.median_price else card.book
        net = gross - rastro_fee(gross)
        n_chasers = len(chasers.get(card.set_code, ()))
        fill = t.fill_rate if t else 0.0
        if chaser_fill is not None and n_chasers:
            fill = chaser_fill
        keep = copy_value(m, card, m.held.get(ref, 0))
        outcomes.append(CardOutcome(ref, card.rarity, p, keep, net, fill, n_chasers))
    return PackValue(
        pack,
        price,
        sum(o.probability * m.cards[o.ref].book for o in outcomes),
        sum(o.probability * o.keep_value for o in outcomes),
        sum(o.probability * o.scored for o in outcomes),
        sum(o.probability * o.fill * o.sale_net for o in outcomes if o.sale_net > o.keep_value),
        tuple(outcomes),
    )


# ---------------------------------------------------------------- the same cash elsewhere (round points)


@dataclass(frozen=True)
class Use:
    """One use of cash and the round points it buys (W5's score model #78, W7's table #87)."""

    name: str
    cash: float
    points_low: float
    points_high: float
    source: str

    @property
    def per_prima(self) -> tuple[float, float]:
        return (self.points_low / self.cash, self.points_high / self.cash) if self.cash else (0.0, 0.0)


# W5 (#78): a trade-surplus prima is worth weight 5 / the top-3 trade raw (100–300 P, assumed) round points.
TRADE_POINTS_PER_PRIMA = (5 / 300, 5 / 100)


def uses_of_cash(packs: Sequence[PackValue]) -> list[Use]:
    """W7's (#87) and W5's (#78) round-point estimates next to the packs', all per prima of cash."""
    low, high = TRADE_POINTS_PER_PRIMA
    out = [
        Use("Abuela best three (W3, ~0.95 share)", 54, 2.4, 2.4, "W7 §4 / W5 ladder model"),
        Use("three Chato uncommons (needs chato:uncommon=31)", 87, 1.8, 1.8, "W7 §4"),
        Use("W4's seven trades (+80 P expected surplus)", 82, 80 * low, 80 * high, "W7 §4 (model fills)"),
        Use("W4's seven trades at Friday's fill rates (+4.8 P)", 82, 4.8 * low, 4.8 * high, "W4 §3"),
        Use("a 4th+ Abuela deal (outside the best three)", 22, 0.0, 0.0, "RULES.md: best three per level"),
    ]
    for p in packs:
        out.append(
            Use(
                f"one {p.pack} at {p.price:g} P, cards resold",
                p.price,
                p.scored_surplus * low,
                p.scored_surplus * high,
                "this model",
            )
        )
    return out


def released_sets(m: Market, extra: Iterable[str] = ()) -> list[str]:
    return sorted(set(m.released) | set(extra))


def pack_fills(events: Iterable[intel.Event], pack: str) -> list[int]:
    """What teams paid a dealer for this pack, in the regime of its most common opening ask (Abuela opened
    at 17 in a minority of Friday threads and at 30 in most; W3)."""
    threads = [t for t in intel.dealer_threads(events) if t.item == pack and t.side == "buy" and t.opening_ask]
    if not threads:
        return []
    openings = [t.opening_ask for t in threads]
    regime = max(set(openings), key=openings.count)
    return [t.fill_price for t in threads if t.opening_ask == regime and t.fill_price is not None]


def expected_price(fills: Sequence[float], cap: int | None) -> tuple[float, str]:
    """What a pack costs: the median of the limits the dealer closed at, or `cap` when lower (W3)."""
    if not fills:
        return float("nan"), "no fill seen"
    mid = float(median(fills))
    if cap is not None and cap < mid:
        return float(cap), f"capped at {cap} (median limit {mid:g}: most threads would not fill)"
    return mid, f"median limit {mid:g}"


def summary_row(p: PackValue) -> dict[str, Any]:
    return {
        "pack": p.pack,
        "price": round(p.price, 1) if not math.isnan(p.price) else None,
        "expected_book": round(p.expected_book, 1),
        "keep_value": round(p.keep_value, 1),
        "scored_surplus": round(p.scored_surplus, 2),
        "resale_cash": round(p.resale_cash, 2),
        "surplus_per_prima": round(p.surplus_per_prima, 3),
    }
