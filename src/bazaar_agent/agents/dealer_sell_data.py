"""What the dealer sell desk knows about dealers, read from data instead of constants.

- **Who buys what**: the `traders` table (`kind = 'dealer'`: id, name, level, menu, unlock, status), whose
  `menu.buys` lists the rarities (and `sets`: "released" or a list of set codes) each dealer buys from us.
  Without the database: `/api/dealers` (the same personas). Only dealers we have unlocked (`/me` `unlocked`)
  are buyers for us, so a dealer appears by itself the tick we unlock it.
- **What they pay**: `dealer_curves`, the sell rows only (item `assets:<id>`, one asset), per dealer and the
  copy's rarity: the opening bid and the fills of the threads that ended in a deal. The copy's rarity comes
  from the settlement items in `feed_events`, else the card scan (`supply_assets` → `cards`). Without the
  database: the feed window (`intel.dealer_threads`). No fill yet: the dealer's opening bid stands in.
- **How many deals an hour**: `menu.deals_per_team_per_hour`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from bazaar_agent.evals.dealers import SELL, price_class

REFRESH_TICKS = 10  # the desk reads the dealers and their curves again this often


@dataclass(frozen=True)
class Fill:
    """A dealer's bids for one rarity on sell threads: its lowest opening bid, the typical (`expected`, the
    mean), highest (`top`) and `median` fill. With no fill yet, all of them are its opening bid."""

    opening: int
    expected: float
    top: int
    fills: int = 0
    median: float | None = None  # None: not known (a hand-built Fill); `typical` falls back to the mean

    @property
    def typical(self) -> float:
        """The robust typical fill: the median (one outlier deal does not move it), else the mean."""
        return self.expected if self.median is None else self.median


@dataclass(frozen=True)
class Trader:
    id: str
    name: str
    level: int | None
    menu: Mapping[str, Any]
    unlock: Mapping[str, Any] = field(default_factory=dict)
    status: str = "active"
    open_to_all: bool = False

    def buys(self, rarity: str, set_code: str, released: Iterable[str] = ()) -> bool:
        """`menu.buys` lists this rarity for this set: `sets` "released" (or absent) means every released set,
        a list means those set codes."""
        for b in self.menu.get("buys") or []:
            if not isinstance(b, Mapping) or b.get("rarity") != rarity:
                continue
            sets = b.get("sets", "released")
            if isinstance(sets, list | tuple):
                if set_code in {str(s) for s in sets}:
                    return True
            elif set_code in set(released) or not released:
                return True
        return False

    @property
    def deals_per_hour(self) -> int | None:
        value = self.menu.get("deals_per_team_per_hour")
        return value if type(value) is int and value >= 0 else None

    @property
    def greeting(self) -> str:
        return self.name or self.id


NOT_DEALERS = ("team", "bench", "rival_alias")  # the other `traders` kinds; every `/api/dealers` kind is a dealer


def trader_from(row: Mapping[str, Any]) -> Trader | None:
    """A dealer from a `traders` row or an `/api/dealers` persona of any kind (dealer, collector, trickster,
    banker: the table stores them all as 'dealer'); None for anything else."""
    if not row.get("id") or row.get("kind") in NOT_DEALERS:
        return None
    menu = row.get("menu") if isinstance(row.get("menu"), Mapping) else {}
    unlock = row.get("unlock") if isinstance(row.get("unlock"), Mapping) else {}
    level = row.get("level")
    return Trader(
        str(row["id"]),
        str(row.get("name") or row["id"]),
        level if type(level) is int else None,
        menu or {},
        unlock or {},
        str(row.get("status") or "active"),
        bool(row.get("open_to_all")) or bool((unlock or {}).get("always")),
    )


@dataclass(frozen=True)
class SellCurve:
    """One sell thread to a dealer: its opening bid, and its fill when it ended in a deal."""

    dealer: str
    rarity: str
    opening: int | None
    fill: int | None


def fills_from(curves: Iterable[SellCurve]) -> dict[tuple[str, str], Fill]:
    groups: dict[tuple[str, str], list[SellCurve]] = defaultdict(list)
    for c in curves:
        groups[(c.dealer, c.rarity)].append(c)
    out: dict[tuple[str, str], Fill] = {}
    for key, rows in groups.items():
        opens = [r.opening for r in rows if r.opening is not None]
        fills = [r.fill for r in rows if r.fill is not None]
        if fills:
            opening = min(opens) if opens else min(fills)
            mean, mid = round(sum(fills) / len(fills), 1), round(float(median(fills)), 1)
            out[key] = Fill(opening, mean, max(fills), len(fills), mid)
        elif opens:
            out[key] = Fill(min(opens), float(min(opens)), min(opens), 0, float(min(opens)))
    return out


@dataclass(frozen=True)
class SellMarket:
    traders: tuple[Trader, ...] = ()
    fills: Mapping[tuple[str, str], Fill] = field(default_factory=dict)
    source: str = "none"

    def trader(self, dealer: str) -> Trader | None:
        return next((t for t in self.traders if t.id == dealer), None)

    def buyers(self, me: Mapping[str, Any], rarity: str, set_code: str, released: Iterable[str] = ()) -> list[Trader]:
        """Dealers that buy this card and that we have unlocked (`/me` `unlocked`; without that field, the
        dealers open to everyone)."""
        unlocked = me.get("unlocked")
        allowed = {str(u) for u in unlocked} if isinstance(unlocked, list) else None
        out = []
        for t in self.traders:
            if t.status not in ("active", "open") or not t.buys(rarity, set_code, released):
                continue
            if (t.id in allowed) if allowed is not None else t.open_to_all:
                out.append(t)
        return out


# ---------------------------------------------------------------- from the shared database

TRADERS_SQL = "select id, kind, name, level, menu, unlock, status from traders where kind = 'dealer'"
# Sell rows of `dealer_curves` (one asset), with the copy's rarity: settlement items first, then the card scan.
SELL_CURVES_SQL = """
with rarity as (
  select distinct on (aid) aid, rarity from (
    select (it->>'id')::int as aid, it->>'rarity' as rarity, 0 as pref
      from feed_events, jsonb_array_elements(payload->'items') it
     where type = 'settlement' and jsonb_typeof(payload->'items') = 'array'
       and it->>'id' ~ '^[0-9]+$' and it->>'rarity' is not null
    union all
    select s.id, c.rarity, 1 from supply_assets s join cards c on c.id = s.ref where c.rarity is not null
  ) x order by aid, pref)
select d.dealer, d.item, r.rarity, d.opening_ask, d.fill_price, d.outcome
  from dealer_curves d join rarity r on r.aid = substring(d.item from 8)::int
 where d.item ~ '^assets:[0-9]+$'
"""


def market_from_db(conn: Any) -> SellMarket:
    """The dealers (`traders`) and their sell curves (`dealer_curves`) from the shared Postgres."""
    with conn.cursor() as cur:
        cur.execute(TRADERS_SQL)
        names = [d[0] for d in cur.description]
        traders = [trader_from(dict(zip(names, row, strict=True))) for row in cur.fetchall()]
        cur.execute(SELL_CURVES_SQL)
        curves = [
            SellCurve(str(dealer), str(rarity), opening, fill if outcome == "deal" else None)
            for dealer, item, rarity, opening, fill, outcome in cur.fetchall()
            if dealer and rarity and price_class(str(item)) == SELL
        ]
    return SellMarket(tuple(t for t in traders if t is not None), fills_from(curves), "postgres")


def db_loader(connect: Callable[[], Any], log: Callable[[str], None]) -> Callable[[Any], SellMarket | None]:
    """`connect` → `market_from_db`; any failure is said once per refresh and answers None (feed fallback)."""

    def load(_snap: Any) -> SellMarket | None:
        try:
            conn = connect()
            try:
                return market_from_db(conn)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - the feed fallback keeps the desk going
            log(f"dealer_sell: dealer data from Postgres unavailable ({type(e).__name__}); using the API and feed")
            return None

    return load


# ---------------------------------------------------------------- fallback: /api/dealers and the feed window


def market_from_feed(
    dealers: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]], me: Mapping[str, Any]
) -> SellMarket:
    """The same view without the database: the personas from `/api/dealers`, the sell threads of the feed window
    (`intel.dealer_threads`), each copy's rarity from the window's settlement items or our own `/me` assets."""
    from bazaar_agent.intel import dealer_threads

    events = list(events)
    rarity: dict[int, str] = {}
    for a in me.get("assets") or []:
        if isinstance(a.get("id"), int) and a.get("rarity"):
            rarity[int(a["id"])] = str(a["rarity"])
    for e in events:
        if e.get("type") != "settlement":
            continue
        for it in (e.get("payload") or {}).get("items") or []:
            if isinstance(it, Mapping) and isinstance(it.get("id"), int) and it.get("rarity"):
                rarity[int(it["id"])] = str(it["rarity"])
    curves = []
    for t in dealer_threads([dict(e) for e in events]):
        if t.side != "sell" or len(t.asset_ids) != 1 or t.asset_ids[0] not in rarity:
            continue
        curves.append(SellCurve(t.dealer, rarity[t.asset_ids[0]], t.opening_ask, t.fill_price))
    traders = [trader_from(d) for d in dealers if isinstance(d, Mapping)]
    return SellMarket(tuple(t for t in traders if t is not None), fills_from(curves), "api+feed")


# ---------------------------------------------------------------- the ladder's slots today (RULES.md "Scoring")

LADDER_SLOTS = 3  # our best three dealer deals per level count; a missing one counts as zero


def full_levels(levels: Mapping[str, int | None], deals: Mapping[str, int]) -> frozenset[int]:
    """The levels whose ladder slots are full today: `LADDER_SLOTS` scored deals with their dealers."""
    count: dict[int, int] = {}
    for dealer, n in deals.items():
        level = levels.get(dealer)
        if level is not None:
            count[level] = count.get(level, 0) + n
    return frozenset(level for level, n in count.items() if n >= LADDER_SLOTS)


def ladder_deals(events: Iterable[Mapping[str, Any]], us: str) -> dict[str, int]:
    """dealer -> our dealer deals that score today (buys and sells: both are ladder deals), from the snapshot's
    feed events (no request): our threads since the latest `day.opened` that filled away from the dealer's own
    opening price (a deal there captures none of its range). Only our own events are matched: a day of every
    team's threads would make `intel.dealer_threads` slow."""
    from bazaar_agent.intel import dealer_threads

    if not us:
        return {}
    rows = [e for e in events if isinstance(e, Mapping)]
    since = max((_tick(e) for e in rows if e.get("type") == "day.opened"), default=0)
    threads: set[int] = set()
    mine: list[dict[str, Any]] = []
    for e in rows:
        p, kind = e.get("payload"), e.get("type")
        if _tick(e) < since or not isinstance(p, Mapping):
            continue
        thread = p.get("thread") if type(p.get("thread")) is int else None
        if kind == "thread.opened" and p.get("team") == us and thread is not None:
            threads.add(thread)
        elif not (kind == "thread.message" and thread in threads) and not (
            kind == "settlement" and isinstance(p.get("parties"), list) and us in p["parties"]
        ):
            continue
        mine.append(dict(e))
    out: dict[str, int] = {}
    for t in dealer_threads(mine, us):
        if t.ours and t.fill_price is not None and t.fill_price != t.opening_ask:
            out[t.dealer] = out.get(t.dealer, 0) + 1
    return out


def _tick(event: Mapping[str, Any]) -> int:
    tick = event.get("tick")
    return tick if type(tick) is int else 0
