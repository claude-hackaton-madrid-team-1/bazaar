"""Dealer ladder outcomes: the share of each dealer's price range a deal captured.

RULES.md "Scoring": the ladder counts the share of each dealer's price range you captured, your
best three deals per level (a missing one as zero), higher levels weighing more. "Dealers": a deal
at the dealer's opening price does not count toward unlocking the next level.

The dealer's range is learned from every team's threads (`dealer_curves`), per dealer and price
class (a card's rarity, a pack, or a sale to the dealer): from its list price (the highest opening
ask seen; for a sale, its lowest opening bid) to its fill floor (the lowest fill seen; for a sale,
the highest). The organisers know each dealer's secret limit; we only see fills, so a floor learned
from few threads overstates the share until a cheaper fill shows up.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.evals.model import Outcome, label_for
from bazaar_agent.intel import DealerThread, set_of

RARITY_BY_NUMBER = ((5, "common"), (8, "uncommon"), (10, "rare"), (11, "epic"), (12, "legendary"))  # RULES.md
RARITIES = frozenset(rarity for _, rarity in RARITY_BY_NUMBER)
SELL = "sell"


def card_rarity(ref: str) -> str | None:
    """'LAV-06' -> 'uncommon': each set has five commons, three uncommons, two rares, an epic, a legendary."""
    number = ref.split("-", 1)[1] if set_of(ref) else ""
    if not number.isdigit():
        return None
    return next((rarity for top, rarity in RARITY_BY_NUMBER if int(number) <= top), None)


def price_class(item: str) -> str | None:
    """The bucket a dealer prices alike: 'card:uncommon', 'pack:sobre_barrio', or 'sell' (a sale to it)."""
    if item.startswith("assets:"):
        return SELL
    if ":" in item:  # a rarity request, "uncommon:LAV"
        requested = item.split(":", 1)[0]
        return f"card:{requested}" if requested in RARITIES else None
    if set_of(item):
        rarity = card_rarity(item)
        return f"card:{rarity}" if rarity else None
    return f"pack:{item}" if item and item != "?" else None


@dataclass(frozen=True)
class CurveRow:
    """The `dealer_curves` columns a range needs (every team's threads)."""

    dealer: str
    item: str
    opening_ask: int | None
    fill_price: int | None


@dataclass(frozen=True)
class DealerRange:
    dealer: str
    price_class: str
    list_price: int | None  # buy: the highest opening ask; sell: the lowest opening bid
    floor: int | None  # buy: the lowest fill; sell: the highest fill (the best the dealer gave)
    threads: int
    fills: int


def learned_ranges(rows: Iterable[CurveRow]) -> dict[tuple[str, str], DealerRange]:
    groups: dict[tuple[str, str], list[CurveRow]] = defaultdict(list)
    for row in rows:
        if (cls := price_class(row.item)) is not None:
            groups[(row.dealer, cls)].append(row)
    out = {}
    for (dealer, cls), members in groups.items():
        opens = [r.opening_ask for r in members if r.opening_ask is not None]
        fills = [r.fill_price for r in members if r.fill_price is not None]
        selling = cls == SELL
        out[(dealer, cls)] = DealerRange(
            dealer=dealer,
            price_class=cls,
            list_price=(min(opens) if selling else max(opens)) if opens else None,
            floor=(max(fills) if selling else min(fills)) if fills else None,
            threads=len(members),
            fills=len(fills),
        )
    return out


def _share(side: str, price: int, top: int, floor: int) -> float | None:
    """Captured share of [top, floor]: a buy moves the price down from the list, a sale up from it."""
    span = top - floor if side == "buy" else floor - top
    if span <= 0:
        return None
    gained = top - price if side == "buy" else price - top
    return min(1.0, max(0.0, gained / span))


def score_thread(
    thread: DealerThread, ranges: Mapping[tuple[str, str], DealerRange], levels: Mapping[str, int]
) -> Outcome:
    """One of our dealer threads, deal or not. A thread without a fill captured nothing."""
    cls = price_class(thread.item)
    learned = ranges.get((thread.dealer, cls)) if cls else None
    level = levels.get(thread.dealer)
    details: dict[str, Any] = {
        "thread": thread.thread,
        "dealer": thread.dealer,
        "level": level,
        "item": thread.item,
        "side": thread.side,
        "price_class": cls,
        "opening_ask": thread.opening_ask,
        "our_prices": list(thread.team_prices),
        "dealer_prices": list(thread.dealer_prices),
        "fill_price": thread.fill_price,
        "steps": thread.steps,
    }
    tick = thread.fill_tick if thread.fill_tick is not None else thread.last_tick
    base = Outcome("dealer", f"thread:{thread.thread}", 0.0, "bad", "", tick, details=details)
    if thread.fill_price is None:
        last = f"its last price {thread.dealer_prices[-1]}" if thread.dealer_prices else "it never answered"
        ours = f"our last {thread.team_prices[-1]}" if thread.team_prices else "we never bid"
        text = (
            f"No deal with {thread.dealer} for {thread.item} ({last}, {ours}): captured nothing. "
            "It counts as zero only while its level has fewer than three deals."
        )
        return _with(base, 0.0, text, details | {"ladder_share": 0.0})
    return _scored_deal(base, thread, thread.fill_price, learned, details)


def _scored_deal(
    base: Outcome, thread: DealerThread, price: int, learned: DealerRange | None, details: dict[str, Any]
) -> Outcome:
    selling = thread.side == "sell"
    opening = thread.opening_ask
    top = learned.list_price if learned and learned.list_price is not None else opening
    if top is not None and opening is not None:
        top = min(top, opening) if selling else max(top, opening)
    floor = learned.floor if learned and learned.floor is not None else price
    floor = max(floor, price) if selling else min(floor, price)
    share = _share(thread.side, price, top, floor) if top is not None else None
    at_opening = opening is not None and price == opening
    note = (
        " We took the dealer's opening price: that deal does not count toward unlocking a level." if at_opening else ""
    )
    details |= {"range_top": top, "range_floor": floor, "at_opening_price": at_opening, "ladder_share": share}
    if share is None:
        cls = details["price_class"]
        text = f"Deal at {price} with {thread.dealer}, but no price range is learned for {cls} yet.{note}"
        return _with(base, None, text, details)
    verb = "sold" if selling else "bought"
    text = (
        f"{verb.capitalize()} {thread.item} from {thread.dealer} at {price}: range {top}→{floor} "
        f"(list → best fill seen), captured {share:.0%}.{note}"
    )
    return _with(base, share, text, details)


def _with(base: Outcome, score: float | None, text: str, details: Mapping[str, Any]) -> Outcome:
    share = details.get("ladder_share")
    return Outcome(
        base.target,
        base.subject,
        None if score is None else round(score, 4),
        "ok" if score is None else label_for(score),
        text,
        base.tick,
        ladder_share=None if share is None else round(float(share), 4),
        details=details,
    )
