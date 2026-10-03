"""The ladder probe (SG1, `questions/strategies.json` `ladder_probe_worth_it`): one small negotiated buy per
unlocked dealer per game hour, so each dealer level gets scored deals even when the strategy finds no card
with surplus enough to buy.

The ladder scores the share of a dealer's range we capture, (opening ask − price) / (opening ask − her limit),
on our best three deals per level; a deal at her opening ask scores nothing, and cash spent on a dealer buy is
not score by itself. So a probe is planned only when its top still captures `ladder_probe_min_share` of the
range seen so far (her lowest fill stands in for her limit), and its top never passes the official value of
one more copy (the guardrails refuse a bid above it and the thread walks), the rarity cap or the cash above
the floor. The probe is a strategy `Move`: the taker guards it and the dealer desk drives it like any other
dealer buy (`Negotiation.bid_cap` keeps every bid below her opening ask until she comes down)."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from statistics import median
from typing import Any

from bazaar_agent import intel
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import Card, Market, Move, Quote, dealer_command, dealer_fills

STRATEGY = "ladder_probe"
START_RATIO = 0.6  # the first bid, as a share of her lowest fill: room for her to come down to us step by step
STEP = 1  # small distinct steps: the dealer only moves when we do

ValueOf = Callable[[str], float | None]


@dataclass(frozen=True)
class Probe:
    dealer: str
    ref: str
    rarity: str
    opening: int  # her opening ask for the rarity (observed median, else her menu)
    lowest_fill: int  # the lowest price she sold the rarity at (feed)
    start: int
    top: int
    share: float  # of her range our top still captures
    value: float | None  # the official value of one more copy; None in a plan made without reading it

    def facts(self) -> dict[str, Any]:
        """What Jev and the decision row read (JSON-safe)."""
        return {
            "dealer": self.dealer,
            "card": self.ref,
            "rarity": self.rarity,
            "opening": self.opening,
            "lowest_fill": self.lowest_fill,
            "start": self.start,
            "top": self.top,
            "share": self.share,
        }

    def move(self) -> Move:
        value = float(self.value if self.value is not None else self.top)
        reason = (
            f"ladder probe: {self.dealer} opens ~{self.opening}, lowest fill {self.lowest_fill}; "
            f"ladder {self.start}→{self.top} keeps ≥ {self.share:.2f} of her range; official value {value:g}"
        )
        return Move(
            "buy",
            STRATEGY,
            self.ref,
            self.rarity,
            round(value, 1),
            float(self.lowest_fill),
            round(value - self.lowest_fill, 1),
            0.0,
            0.0,  # ranks after every strategy buy: a dealer with a real buy opens that one
            self.dealer,
            (self.dealer,),
            "buy",
            self.top,
            reason,
            dealer_command(self.ref, self.dealer, self.start, self.top, STEP),
            ladder=(self.start, self.top, STEP),
        )


def _rarity_of(m: Market) -> dict[str, str]:
    return {ref: c.rarity for ref, c in m.cards.items()}


def opening_asks(
    m: Market, events: Iterable[intel.Event], dealers: Iterable[dict[str, Any]]
) -> dict[tuple[str, str], int]:
    """(dealer, rarity) -> her opening ask: the median first ask in the feed's buy threads, else the menu's
    `opening_ask`, else its list price (the menu's figures are a floor for what she opens at)."""
    out = {(q.dealer, q.item): q.list_price for q in m.quotes}
    for d in dealers:
        for s in (d.get("menu") or {}).get("sells") or []:
            key = (str(d.get("id")), str(s.get("rarity") or s.get("pack")))
            if key in out and isinstance(s.get("opening_ask"), int):
                out[key] = max(out[key], int(s["opening_ask"]))
    rarity_of: dict[str, str] = _rarity_of(m)
    seen: dict[tuple[str, str], list[int]] = {}
    for t in intel.dealer_threads(events):
        if t.side == "buy" and t.opening_ask is not None and t.item in rarity_of:
            seen.setdefault((t.dealer, rarity_of[t.item]), []).append(t.opening_ask)
    for key, asks in seen.items():
        if key in out:
            out[key] = math.floor(median(asks))
    return out


def candidates(m: Market, dealer: str, quotes: Iterable[Quote]) -> list[Card]:
    """Missing page cards of a released set this dealer sells (by rarity and set), cheapest rarity first, then
    the most valuable to us (book × affinity: the official value tracks it, so it is likeliest to reach her
    fills)."""
    price: dict[str, Quote] = {}
    for q in quotes:
        if q.dealer == dealer:
            price.setdefault(q.item, q)
    out = [
        c
        for c in m.cards.values()
        if c.page
        and c.set_code in m.released
        and m.held.get(c.ref, 0) == 0
        and c.minted < c.print_run
        and (sold := price.get(c.rarity)) is not None
        and (sold.sets is None or c.set_code in sold.sets)
    ]
    return sorted(out, key=lambda c: (price[c.rarity].list_price, -c.book * m.affinity.get(c.set_code, 1.0), c.ref))


def range_share(opening: int, top: int, lowest_fill: int) -> float | None:
    """The share of her range a deal at `top` captures; None when the range is unknown or empty."""
    if opening <= lowest_fill:
        return None
    return round((opening - top) / (opening - lowest_fill), 3)


def plan_one(
    m: Market,
    dealer: str,
    opens: Mapping[tuple[str, str], int],
    rules: Guardrails,
    cash_room: int,
    value_of: ValueOf | None,
) -> Probe | None:
    """The probe for one dealer: the cheapest missing card it sells, or None. `value_of` None plans without the
    official value (a cheap pre-check: nothing is read, the top optimistic: just under her opening ask);
    otherwise it is read once, for the chosen card only."""
    cards = candidates(m, dealer, m.quotes)
    if not cards:
        return None
    card = cards[0]
    fills = [p.price for p in dealer_fills(m, dealer) if _rarity_of(m).get(p.ref) == card.rarity]
    opening, cap = opens.get((dealer, card.rarity)), rules.max_price_for(card.rarity)
    if not fills or opening is None or cap is None:
        return None
    top = min(cap, cash_room)
    value = None
    if value_of is not None:
        value = value_of(card.ref)
        if value is None:  # unread: a bid would be refused anyway (fail closed)
            return None
        top = min(top, math.floor(value - rules.official_value_margin + 1e-9))
    lowest = min(fills)
    if value_of is None:  # the cheap pre-check: the official value (read later) only ever lowers the top
        top = min(top, opening - 1)
    share = range_share(opening, top, lowest)
    # Her lowest fill bounds her limit from above: a top below it would walk after tying up her thread.
    if top < max(1, lowest) or share is None or share < rules.ladder_probe_min_share:
        return None
    start = min(top, max(1, math.floor(START_RATIO * lowest)))
    return Probe(dealer, card.ref, card.rarity, opening, lowest, start, top, share, value)


def plan_probes(
    m: Market,
    opens: Mapping[tuple[str, str], int],
    rules: Guardrails,
    cash_room: int,
    skip: Iterable[str] = (),
    value_of: ValueOf | None = None,
) -> list[Probe]:
    """At most one probe per unlocked dealer (`m.quotes` holds only the active dealers we unlocked), except
    the dealers in `skip` (probed this hour, or busy with a thread)."""
    dealers = sorted({q.dealer for q in m.quotes} - set(skip))
    planned = (plan_one(m, d, opens, rules, cash_room, value_of) for d in dealers)
    return [p for p in planned if p is not None]


def our_dealer_deals(events: Iterable[intel.Event], us: str) -> dict[str, int]:
    """dealer -> our buy threads with that dealer that filled (feed window): how scored each level already is."""
    out: dict[str, int] = {}
    for t in intel.dealer_threads(events, us or None):
        if t.ours and t.side == "buy" and t.fill_price is not None:
            out[t.dealer] = out.get(t.dealer, 0) + 1
    return out


def probe_state(
    probes: Iterable[Probe], cash: int, floor: int, room: int, spent: int, deals: Mapping[str, int]
) -> dict[str, Any]:
    """What Jev reads for `ladder_probe_worth_it` (compact, JSON-safe)."""
    return {
        "cash": cash,
        "cash_floor": floor,
        "cash_room": room,
        "spent_last_hour": spent,
        "ladder": {"our_dealer_deals": dict(deals)},
        "probe": [p.facts() for p in probes],
    }
