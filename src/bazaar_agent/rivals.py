"""Rival behaviour from the public feed: every board offer's life, the board at any tick, and per-team profiles.

Pure functions over feed events. The feed names the maker of every listing (`offer.listed` carries the team
id the board hides behind a pseudonym), says when one is cancelled (`offer.cancelled`), and shows every
settlement; an offer that filled is the settlement of its copy (an ask) or of the card it wanted (a bid) from
or to its maker after it was listed, and one that neither filled nor was cancelled lapsed at `expires_tick`.
Nothing settles while the doors are closed, so the board at the last tick of a day is the board that opens
the next one.

A profile says how a team prices and how fast it moves:
  - its asks against the tape (median team-to-team price of the card, else of its rarity) and against its
    own value of the card (book × its expected multiplier from the affinity map × the copy marginal): a
    team that asks below its own value sells cheap (a snipe source);
  - its bids against the tape and against our value: a team that bids above the tape overpays (sell to it);
  - how often its offers fill, how fast it takes other teams' offers (ticks from listing to settlement),
    how often it reprices and by how much.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Literal

from bazaar_agent import intel
from bazaar_agent.affinity import AffinityMap

Event = dict[str, Any]
Outcome = Literal["open", "filled", "cancelled", "expired"]


@dataclass
class Listed:
    """One board offer from `offer.listed`, with how it ended."""

    id: int
    tick: int
    maker: str
    side: Literal["ask", "bid"]
    ref: str
    price: int
    asset_id: int | None
    venue: str
    to: str | None
    expires_tick: int | None
    outcome: Outcome = "open"
    end_tick: int | None = None
    taker: str | None = None
    fill_price: int | None = None

    def open_at(self, tick: int) -> bool:
        """Listed by `tick` and not yet ended (filled, cancelled or lapsed) at it."""
        if self.tick > tick:
            return False
        if self.end_tick is not None and self.end_tick <= tick:
            return False
        return self.expires_tick is None or self.expires_tick > tick


def _plain(offer: dict[str, Any]) -> tuple[Literal["ask", "bid"], str, int, int | None] | None:
    """(side, card, price, asset id) of a one-card-for-cash offer; None for any other shape."""
    give, want = offer.get("give") or {}, offer.get("want") or {}
    assets = [a for a in give.get("assets") or [] if isinstance(a, dict)]
    wanted = [str(t).split(":", 1)[-1] for t in (want.get("types") or []) + (want.get("cards") or [])]
    if len(assets) == 1 and not wanted and int(want.get("cash") or 0) > 0 and not give.get("cash"):
        a = assets[0]
        if intel.set_of(a.get("ref")):
            return "ask", str(a["ref"]), int(want["cash"]), a.get("id") if isinstance(a.get("id"), int) else None
    bid = len(wanted) == 1 and not assets and int(give.get("cash") or 0) > 0 and not want.get("cash")
    if bid and intel.set_of(wanted[0]):
        return "bid", wanted[0], int(give["cash"]), None
    return None


def listings(events: Iterable[Event]) -> list[Listed]:
    """Every plain board offer a team listed, with its outcome as of the last event."""
    events = list(events)
    rows: dict[int, Listed] = {}
    settlements: list[tuple[int, dict[str, Any]]] = []
    for e in events:
        kind, p, tick = e.get("type"), e.get("payload") or {}, int(e.get("tick") or 0)
        if kind == "offer.listed" and intel.TEAM_ID.match(str(e.get("actor") or "")):
            offer = p.get("offer") or {}
            shape = _plain(offer)
            if shape is None or not isinstance(offer.get("id"), int) or offer.get("thread") is not None:
                continue
            side, ref, price, asset = shape
            created = offer.get("created_tick")
            rows[offer["id"]] = Listed(
                offer["id"],
                created if isinstance(created, int) else tick,
                str(e["actor"]),
                side,
                ref,
                price,
                asset,
                str(offer.get("venue") or p.get("venue") or ""),
                offer.get("to"),
                offer.get("expires_tick") if isinstance(offer.get("expires_tick"), int) else None,
            )
        elif kind == "offer.cancelled" and isinstance(p.get("offer"), int) and p["offer"] in rows:
            row = rows[p["offer"]]
            if row.outcome == "open":
                row.outcome, row.end_tick = "cancelled", tick
        elif kind == "settlement" and not p.get("persona"):
            settlements.append((tick, p))
    used: set[tuple[int, int]] = set()
    for tick, p in settlements:  # oldest first: each settlement fills at most one open offer
        items = p.get("items") or []
        if len(items) != 1:
            continue
        item = items[0]
        frm, to, ref = str(item.get("frm")), str(item.get("to")), str(item.get("ref"))
        sid = int(p.get("settlement") or 0)
        for row in sorted(rows.values(), key=lambda r: (r.tick, r.id)):
            if row.outcome != "open" or row.tick > tick or (sid, row.id) in used:
                continue
            if row.expires_tick is not None and tick > row.expires_tick + 1:  # accepted at its last tick
                continue
            ask_fill = row.side == "ask" and row.maker == frm and (row.asset_id == item.get("id") or row.ref == ref)
            bid_fill = row.side == "bid" and row.maker == to and row.ref == ref
            if ask_fill or bid_fill:
                row.outcome, row.end_tick = "filled", tick
                row.taker, row.fill_price = (to if ask_fill else frm), int(p.get("price") or 0)
                used.add((sid, row.id))
                break
    last = max((int(e.get("tick") or 0) for e in events), default=0)
    for row in rows.values():
        if row.outcome == "open" and row.expires_tick is not None and row.expires_tick <= last:
            row.outcome, row.end_tick = "expired", row.expires_tick
    return sorted(rows.values(), key=lambda r: (r.tick, r.id))


def board_at(rows: Sequence[Listed], tick: int) -> list[Listed]:
    """The offers open on the boards at `tick`: what a scanner would have seen then."""
    return [r for r in rows if r.open_at(tick)]


# ---------------------------------------------------------------- the tape as a price reference


def tape_reference(events: Iterable[Event], rarity_of: dict[str, str]) -> tuple[dict[str, float], dict[str, float]]:
    """(median team-to-team price per card, per rarity) from one-card settlements between teams."""
    by_ref: dict[str, list[int]] = defaultdict(list)
    by_rarity: dict[str, list[int]] = defaultdict(list)
    for p in intel.tape(events):
        if p.persona is None and p.items == 1 and intel.TEAM_ID.match(p.buyer) and intel.TEAM_ID.match(p.seller):
            by_ref[p.ref].append(p.price)
            if p.ref in rarity_of:
                by_rarity[rarity_of[p.ref]].append(p.price)
    return (
        {k: float(median(v)) for k, v in by_ref.items()},
        {k: float(median(v)) for k, v in by_rarity.items()},
    )


def market_price(ref: str, rarity: str | None, refs: dict[str, float], rarities: dict[str, float]) -> float | None:
    return refs.get(ref) or (rarities.get(rarity) if rarity else None)


# ---------------------------------------------------------------- profiles


@dataclass
class TeamProfile:
    team: str
    top_set: str | None = None
    p_top: float = 0.0
    asks: int = 0
    asks_filled: int = 0
    bids: int = 0
    bids_filled: int = 0
    cancels: int = 0
    reprices: int = 0
    reprice_steps: list[float] = field(default_factory=list)  # relative change of a repriced copy's ask
    ask_vs_tape: list[float] = field(default_factory=list)  # ask / market price
    ask_vs_own: list[float] = field(default_factory=list)  # ask / the team's own expected value of that copy
    bid_vs_tape: list[float] = field(default_factory=list)
    takes: int = 0  # other teams' board offers this team took
    take_latency: list[int] = field(default_factory=list)  # ticks from listing to the settlement it took
    sold_below_own: int = 0  # asks that filled below the team's own expected value

    @staticmethod
    def _med(values: Sequence[float]) -> float | None:
        return round(float(median(values)), 3) if values else None

    @property
    def ask_fill_rate(self) -> float | None:
        return round(self.asks_filled / self.asks, 3) if self.asks else None

    @property
    def median_ask_vs_tape(self) -> float | None:
        return self._med(self.ask_vs_tape)

    @property
    def median_ask_vs_own(self) -> float | None:
        return self._med(self.ask_vs_own)

    @property
    def median_bid_vs_tape(self) -> float | None:
        return self._med(self.bid_vs_tape)

    @property
    def median_take_latency(self) -> float | None:
        return self._med([float(x) for x in self.take_latency])

    @property
    def median_reprice_step(self) -> float | None:
        return self._med(self.reprice_steps)

    @property
    def tags(self) -> tuple[str, ...]:
        """What a trader acts on: `cheap seller` (asks a median below its own value), `overbidder` (bids a
        median above the tape), `fast taker` (takes within 2 ticks), `relister` (reprices often)."""
        out = []
        if self.median_ask_vs_own is not None and self.median_ask_vs_own < 1.0 and len(self.ask_vs_own) >= 3:
            out.append("cheap seller")
        if self.median_bid_vs_tape is not None and self.median_bid_vs_tape > 1.0 and len(self.bid_vs_tape) >= 3:
            out.append("overbidder")
        if self.median_take_latency is not None and self.median_take_latency <= 2 and self.takes >= 3:
            out.append("fast taker")
        if self.asks and self.reprices >= max(3, self.asks // 4):
            out.append("relister")
        return tuple(out)


def own_value(amap: AffinityMap, team: str, ref: str, book: float, copies: int) -> float:
    """A team's expected value of one more (bids) or its last (asks, `copies` held) copy: book × its
    expected multiplier × the copy marginal (1, 0.25, 0.1)."""
    set_code = intel.set_of(ref) or ""
    marginal = (1.0, 0.25, 0.1)[min(max(copies - 1, 0), 2)]
    return book * amap.expected(team, set_code) * marginal


def profiles(
    rows: Sequence[Listed],
    events: Iterable[Event],
    amap: AffinityMap,
    catalog: dict[str, Any],
    exclude: Iterable[str] = (),
) -> dict[str, TeamProfile]:
    """One profile per team that listed or took a board offer (never the excluded, i.e. us)."""
    events = list(events)
    book = intel.book_values(catalog)
    cards = [c for s in catalog.get("sets") or [] for c in s.get("cards") or [] if c.get("id")]
    rarity_of = {str(c["id"]): str(c.get("rarity")) for c in cards}
    refs, rarities = tape_reference(events, rarity_of)
    # distinct copies a team listed of a card: a floor on what it holds (its last copy is worth the most)
    distinct: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in rows:
        if r.side == "ask" and r.asset_id is not None:
            distinct[(r.maker, r.ref)].add(r.asset_id)
    skip = set(exclude)
    out: dict[str, TeamProfile] = {}

    def prof(team: str) -> TeamProfile:
        if team not in out:
            t = amap.teams.get(team)
            known = t is not None and t.signals > 0
            out[team] = TeamProfile(team, t.top_set if t and known else None, t.confidence if t and known else 0.0)
        return out[team]

    last_ask: dict[tuple[str, int], int] = {}
    for r in rows:
        if r.maker in skip:
            continue
        p = prof(r.maker)
        ref_price = market_price(r.ref, rarity_of.get(r.ref), refs, rarities)
        if r.side == "ask":
            p.asks += 1
            p.asks_filled += r.outcome == "filled"
            if ref_price:
                p.ask_vs_tape.append(r.price / ref_price)
            copies = len(distinct.get((r.maker, r.ref), ())) or 1
            mine = own_value(amap, r.maker, r.ref, book.get(r.ref, 0.0), copies)
            if mine > 0:
                p.ask_vs_own.append(r.price / mine)
                if r.outcome == "filled" and r.price < mine:
                    p.sold_below_own += 1
            if r.asset_id is not None:
                key = (r.maker, r.asset_id)
                if key in last_ask and last_ask[key] != r.price:
                    p.reprices += 1
                    p.reprice_steps.append((r.price - last_ask[key]) / last_ask[key])
                last_ask[key] = r.price
        else:
            p.bids += 1
            p.bids_filled += r.outcome == "filled"
            if ref_price:
                p.bid_vs_tape.append(r.price / ref_price)
        p.cancels += r.outcome == "cancelled"
        if r.outcome == "filled" and r.taker and r.taker not in skip and intel.TEAM_ID.match(r.taker):
            t = prof(r.taker)
            t.takes += 1
            t.take_latency.append(int(r.end_tick or r.tick) - r.tick)
    return dict(sorted(out.items(), key=lambda kv: (len(kv[0]), kv[0])))
