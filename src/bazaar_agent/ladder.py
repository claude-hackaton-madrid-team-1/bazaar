"""Ladder maximiser: what each dealer's secret limit looked like, and the bid plan that closes at it.

RULES.md "Scoring": the ladder counts the share of each dealer's price range a deal captured, the best
three deals per level, a missing one as zero. "Dealers": every conversation has its own secret limit,
the dealer only moves when we move, small steps earn small steps, and a bid that reaches the limit is
taken at our price. So the most a deal can capture is to close AT the limit, and the plan that gets
there is a +1 ladder that starts just under it, before the dealer's patience runs out.

This module reads every team's dealer conversations from the public feed, turn by turn (`conversations`),
brackets each conversation's secret limit from what the dealer countered and what it took
(`limit_bounds`), summarises the limits per dealer × price class × opening ask (`floor_table`) and turns a
row into a `BidPlan` capped by GUARDRAILS.md (`plan_for`). Pure functions, no network.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from bazaar_agent.agents.dealer import BidPlan
from bazaar_agent.evals.dealers import SELL, card_rarity, price_class
from bazaar_agent.intel import Event, offer_price

PLAN_WIDTH = 2  # PLAN.md W3: start = floor − 2, step 1, max = floor + 2
RECENT = 10  # newest conversations per dealer × class that decide which opening ask is current


@dataclass(frozen=True)
class Turn:
    tick: int
    dealer: bool  # True: the dealer spoke; False: the team did
    price: int | None
    final: bool = False


@dataclass
class Conversation:
    """One team's thread with one dealer, every priced turn in order, and its fill if one settled."""

    thread: int
    team: str
    dealer: str
    side: str  # "buy" or "sell", from the team's point of view
    item: str
    opened_tick: int
    turns: list[Turn] = field(default_factory=list)
    fill: int | None = None
    fill_tick: int | None = None

    @property
    def price_class(self) -> str | None:
        return price_class(self.item)

    @property
    def dealer_prices(self) -> list[int]:
        return [t.price for t in self.turns if t.dealer and t.price is not None]

    @property
    def team_prices(self) -> list[int]:
        return [t.price for t in self.turns if not t.dealer and t.price is not None]

    @property
    def opening(self) -> int | None:
        """The dealer's first structured price: the top of its range for this conversation."""
        prices = self.dealer_prices
        return prices[0] if prices else None

    @property
    def final_price(self) -> int | None:
        return next((t.price for t in self.turns if t.dealer and t.final), None)

    @property
    def patience(self) -> int | None:
        """Priced team turns before the dealer named its final offer (None: no final was named)."""
        bids = 0
        for t in self.turns:
            if t.dealer and t.final:
                return bids
            if not t.dealer and t.price is not None:
                bids += 1
        return None

    @property
    def first_drop(self) -> int | None:
        """How far the dealer moved on its first counter after the team's first price."""
        seen_team = seen_opening = False
        for t in self.turns:
            if not t.dealer and t.price is not None:
                seen_team = True
            elif t.dealer and t.price is not None:
                if seen_opening and seen_team and self.opening is not None:
                    return abs(self.opening - t.price)
                seen_opening = True
        return None

    @property
    def closed_by(self) -> str | None:
        """'our_price' when the dealer took the team's price, 'their_price' when the team took the dealer's."""
        if self.fill is None:
            return None
        if self.team_prices and self.fill == self.team_prices[-1]:
            return "our_price"
        return "their_price" if self.fill in self.dealer_prices else None


def _topic(topic: dict[str, Any]) -> tuple[str, str]:
    for side in ("buy", "sell"):
        spec = topic.get(side)
        if isinstance(spec, dict):
            if "pack" in spec:
                return side, str(spec["pack"])
            if "card" in spec:
                return side, str(spec["card"])
            if "assets" in spec:
                return side, "assets:" + ",".join(str(a) for a in spec["assets"])
            if "rarity" in spec:
                return side, f"{spec.get('rarity')}:{spec.get('set', '*')}"
    return "?", "?"


def conversations(events: Iterable[Event]) -> list[Conversation]:
    """Every dealer conversation in the feed, turn by turn. A settlement is matched to the newest open
    conversation of that team with that dealer whose own prices include the settled price (a fill is
    always a price someone in the thread named), so an abandoned thread never inherits a later fill."""
    threads: dict[int, Conversation] = {}
    settlements: list[Event] = []
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "thread.opened" and p.get("kind") == "persona":
            side, item = _topic(p.get("topic") or {})
            tid = int(p["thread"])
            threads[tid] = Conversation(tid, str(p.get("team")), str(p.get("with")), side, item, int(e.get("tick", 0)))
        elif kind == "thread.message" and p.get("kind") == "persona":
            c = threads.get(int(p.get("thread", -1)))
            if c is None:
                continue
            offer = p.get("offer") or {}
            price = offer_price(offer) if offer else None
            c.turns.append(Turn(int(e.get("tick", 0)), p.get("sender") == c.dealer, price, bool(offer.get("final"))))
        elif kind == "settlement":
            settlements.append(e)
    by_party: dict[tuple[str, str, str], list[Conversation]] = defaultdict(list)
    for c in sorted(threads.values(), key=lambda c: (c.opened_tick, c.thread), reverse=True):
        by_party[(c.dealer, c.team, c.side)].append(c)  # newest first
    for e in settlements:
        p = e.get("payload") or {}
        dealer, items = p.get("persona"), p.get("items") or []
        if not dealer or not items or p.get("price") is None:
            continue
        price, tick = int(p["price"]), int(p.get("tick") or e.get("tick") or 0)
        frm, to = str(items[0].get("frm")), str(items[0].get("to"))
        side, team = ("buy", to) if frm == dealer else ("sell", frm)
        for c in by_party.get((dealer, team, side), []):
            named = {t.price for t in c.turns if t.tick <= tick}  # a price named before it settled
            if c.fill is None and c.opened_tick <= tick and price in named and _same_item(c, items):
                c.fill, c.fill_tick = price, tick
                break
    return sorted(threads.values(), key=lambda c: c.thread)


def _same_item(c: Conversation, items: list[dict[str, Any]]) -> bool:
    """The settlement moved what the thread was about: that card or pack, those assets, or any card
    of a requested rarity."""
    if c.item.startswith("assets:"):
        wanted = {int(a) for a in c.item.split(":", 1)[1].split(",") if a.strip().isdigit()}
        return any(i.get("id") in wanted for i in items)
    if ":" in c.item:  # a rarity request, "uncommon:LAV": any card of that rarity (and set)
        rarity, code = c.item.split(":", 1)
        return any(
            card_rarity(str(i.get("ref"))) == rarity and code in ("*", str(i.get("ref")).split("-", 1)[0])
            for i in items
        )
    return any(i.get("ref") == c.item for i in items)


def _countered(c: Conversation) -> list[int]:
    """Team prices the dealer answered with a price of its own afterwards: it did not take them."""
    out, pending = [], []
    for t in c.turns:
        if not t.dealer and t.price is not None:
            pending.append(t.price)
        elif t.dealer and t.price is not None:
            out.extend(pending)
            pending = []
    return out


def limit_bounds(c: Conversation) -> tuple[int | None, int | None, bool]:
    """(lo, hi, closed): inclusive bounds on the secret limit. Buying: every price the dealer quoted is
    at or above its limit and every bid it countered is below it. Selling: the mirror image."""
    quoted, countered = c.dealer_prices, _countered(c)
    closed = c.fill is not None or c.final_price is not None
    if c.side == "sell":
        lo = max([*quoted, *([c.fill] if c.fill is not None else [])], default=None)
        hi = min(countered) - 1 if countered else None
        return lo, hi, closed
    hi = min([*quoted, *([c.fill] if c.fill is not None else [])], default=None)
    lo = max(countered) + 1 if countered else None
    return lo, hi, closed


def limit_point(c: Conversation) -> int | None:
    """The price that closed (or would have closed) this conversation for us: the bound on the dealer's
    side once a fill or a final pinned it down. None while only open quotes bound the limit."""
    lo, hi, closed = limit_bounds(c)
    if not closed or (lo is None and hi == c.opening) or (hi is None and lo == c.opening):
        return None  # the team took the dealer's opening price untested: it says nothing about the limit
    return lo if c.side == "sell" else hi


def _quantile(values: Sequence[int], q: float) -> int | None:
    """Nearest-rank quantile on whole primas (a plan bids whole primas)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


@dataclass(frozen=True)
class FloorRow:
    """One dealer × price class × opening ask: how its conversations closed (every team's threads)."""

    dealer: str
    price_class: str
    opening: int
    conversations: int
    closed: int  # conversations whose limit a fill or a final pinned down
    limits: tuple[int, ...]  # one point per closed conversation, sorted
    fills: int
    patience: float | None  # median priced team turns before the final
    first_drop: float | None  # median first counter, in primas
    recent: int = 0  # how many of this dealer × class's newest RECENT conversations opened at this ask

    def floor(self, q: float = 0.5) -> int | None:
        return _quantile(self.limits, q)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dealer": self.dealer,
            "price_class": self.price_class,
            "opening": self.opening,
            "conversations": self.conversations,
            "closed": self.closed,
            "fills": self.fills,
            "floor_p10": self.floor(0.1),
            "floor_p25": self.floor(0.25),
            "floor_p50": self.floor(0.5),
            "floor_p75": self.floor(0.75),
            "floor_p90": self.floor(0.9),
            "patience_median": self.patience,
            "first_drop_median": self.first_drop,
        }


def floor_table(convs: Iterable[Conversation], *, since_tick: int = 0) -> list[FloorRow]:
    """Rows per dealer × price class × opening ask, busiest first. `since_tick` drops older threads
    (a dealer's opening ask changed during Friday's first hour: each opening is its own regime)."""
    groups: dict[tuple[str, str, int], list[Conversation]] = defaultdict(list)
    for c in convs:
        cls, opening = c.price_class, c.opening
        if cls is None or opening is None or c.opened_tick < since_tick or c.side not in ("buy", "sell"):
            continue
        if (cls == SELL) != (c.side == "sell"):
            continue
        groups[(c.dealer, cls, opening)].append(c)
    by_class: dict[tuple[str, str], list[Conversation]] = defaultdict(list)
    for (dealer, cls, _), members in groups.items():
        by_class[(dealer, cls)].extend(members)
    recent: dict[tuple[str, str, int], int] = defaultdict(int)
    for (dealer, cls), members in by_class.items():
        for c in sorted(members, key=lambda c: (c.opened_tick, c.thread), reverse=True)[:RECENT]:
            recent[(dealer, cls, c.opening or 0)] += 1
    rows = []
    for (dealer, cls, opening), members in groups.items():
        points = sorted(p for c in members if (p := limit_point(c)) is not None)
        patience = [p for c in members if (p := c.patience) is not None]
        drops = [d for c in members if (d := c.first_drop) is not None]
        rows.append(
            FloorRow(
                dealer=dealer,
                price_class=cls,
                opening=opening,
                conversations=len(members),
                closed=len(points),
                limits=tuple(points),
                fills=sum(c.fill is not None for c in members),
                patience=float(median(patience)) if patience else None,
                first_drop=float(median(drops)) if drops else None,
                recent=recent.get((dealer, cls, opening), 0),
            )
        )
    return sorted(rows, key=lambda r: (r.dealer, r.price_class, -r.conversations))


def main_rows(rows: Iterable[FloorRow]) -> dict[tuple[str, str], FloorRow]:
    """The regime to plan on per dealer × price class: the opening ask most of its RECENT newest
    conversations had (a dealer that changes its opening starts a new regime, which needs its own
    closed threads before `plan_for` trusts it; one odd thread does not flip it), then the most closed."""
    best: dict[tuple[str, str], FloorRow] = {}
    for r in rows:
        key = (r.dealer, r.price_class)
        if key not in best or (r.recent, r.closed, r.conversations) > (
            best[key].recent,
            best[key].closed,
            best[key].conversations,
        ):
            best[key] = r
    return best


def rarity_of_class(cls: str) -> str | None:
    """'card:uncommon' → 'uncommon', 'pack:sobre_barrio' → 'pack': the GUARDRAILS.md price cap it meets."""
    if cls.startswith("card:"):
        return cls.split(":", 1)[1]
    return "pack" if cls.startswith("pack:") else None


@dataclass(frozen=True)
class PlanChoice:
    """A BidPlan for one row, or why there is none."""

    row: FloorRow
    plan: BidPlan | None
    floor: int | None
    cap: int | None
    reason: str


MIN_CLOSED = 5  # closed conversations a floor needs before a plan trusts it


def plan_for(
    row: FloorRow, cap: int | None, *, q: float = 0.5, width: int = PLAN_WIDTH, min_closed: int = MIN_CLOSED
) -> PlanChoice:
    """start = floor − width, step 1, max = floor + width, never above the guardrail cap. No plan when
    fewer than `min_closed` conversations closed (a thin feed), or when the cap sits below the lower
    quartile of the limits seen: most conversations could not close."""
    floor = row.floor(q)
    if floor is None or row.closed < min_closed:
        return PlanChoice(row, None, floor, cap, f"{row.closed} closed conversations: fewer than {min_closed}")
    p25 = row.floor(0.25) or floor
    top = min(floor + width, row.opening - 1)  # a bid at its opening ask would close at its opening price
    top = top if cap is None else min(top, cap)
    if top < p25:
        bound = f"cap {cap}" if cap is not None and cap < row.opening - 1 else f"its opening ask {row.opening}"
        return PlanChoice(row, None, floor, cap, f"{bound} below market: 3 in 4 limits seen are above {p25 - 1}")
    start = max(1, min(floor - width, top))
    if start >= row.opening:
        return PlanChoice(row, None, floor, cap, f"floor {floor} is the opening ask {row.opening}: no range")
    return PlanChoice(row, BidPlan(start, 1, top), floor, cap, f"floor p{round(q * 100)} {floor} ± {width}")


def to_rows(convs: Iterable[Conversation]) -> list[list[Any]]:
    """A compact, public-only form of conversations (no words, no private values) for fixtures."""
    return [
        [c.thread, c.team, c.dealer, c.side, c.item, c.opened_tick, c.fill, c.fill_tick]
        + [[t.tick, int(t.dealer), t.price, int(t.final)] for t in c.turns]
        for c in convs
    ]


def from_rows(rows: Iterable[Sequence[Any]]) -> list[Conversation]:
    out = []
    for thread, team, dealer, side, item, opened, fill, fill_tick, *turns in rows:
        c = Conversation(int(thread), str(team), str(dealer), str(side), str(item), int(opened), fill=fill)
        c.fill_tick = fill_tick
        c.turns = [Turn(int(tick), bool(d), price, bool(final)) for tick, d, price, final in turns]
        out.append(c)
    return out


UNLOCK_DEALS = 3  # Friday: L2 opened to every team with 3 negotiated Abuela deals (t02, t04, t08, t16 at exactly 3)


@dataclass(frozen=True)
class Level:
    """A dealer level as the feed showed it: announced (a teaser), then activated (how it works)."""

    dealer: str
    name: str
    teaser: str
    announced_tick: int | None
    activated_tick: int | None
    how: str | None
    opens_to_all_in_hours: float | None
    unlocked: tuple[tuple[str, int, str], ...]  # (team, tick, why)


def levels(events: Iterable[Event]) -> list[Level]:
    """Every `level.announced` / `level.activated` / `level.unlocked` in the feed, per dealer."""
    info: dict[str, dict[str, Any]] = {}
    unlocked: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind in ("level.announced", "level.activated"):
            dealer = str(p.get("persona") or p.get("level") or "?")  # one key for every level event
            d = info.setdefault(dealer, {"name": p.get("name") or dealer, "teaser": p.get("teaser") or ""})
            d["announced" if kind == "level.announced" else "activated"] = int(e.get("tick", 0))
            if kind == "level.activated":
                d["how"], d["open_in"] = p.get("how"), p.get("opens_to_all_in_hours")
        elif kind == "level.unlocked":
            dealer = str(p.get("persona") or p.get("level") or "?")
            unlocked[dealer].append((str(p.get("team")), int(e.get("tick", 0)), str(p.get("why") or "")))
    return [
        Level(
            dealer,
            str(d["name"]),
            str(d["teaser"]),
            d.get("announced"),
            d.get("activated"),
            d.get("how"),
            d.get("open_in"),
            tuple(unlocked.get(dealer, [])),
        )
        for dealer, d in info.items()
    ]


def negotiated_deals(convs: Iterable[Conversation], team: str) -> dict[str, int]:
    """Our deals per dealer that count toward unlocking the next level: closed at a price other than the
    dealer's opening (Friday: t15's three deals at Abuela's opening never unlocked El Chato)."""
    out: dict[str, int] = defaultdict(int)
    for c in convs:
        if c.team == team and c.fill is not None and c.fill != c.opening:
            out[c.dealer] += 1
    return dict(out)
