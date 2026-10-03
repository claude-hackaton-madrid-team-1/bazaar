"""The offline arbitrage study: replay a captured feed and count what arbitrage and duplicate buys were on offer.

Input: feed events (the monitor's `stream.jsonl` or the shared DB's feed), never a live call. The board at
each tick is rebuilt from `offer.listed` (real team ids), `offer.cancelled`, `settlement` (an ask filled by
its asset id; a bid by its maker, card and price) and `expires_tick`; venue fees from `venue.opened` and
`venue.fee_changed` (El Rastro: 500 bps + 1 P per card). An offer stands at tick t when it was listed at or
before t and neither filled, cancelled nor expired by t (a settlement at t means it was taken at t − 1).

Counted, each with El Rastro's fees and with every fee at zero (a 0 bps team venue):
- crossings: an ask and a bid for the same card from different makers standing in the same tick, by gross
  spread and by net after both fees; how many ticks each lasted; how many were EXECUTABLE (the bid still
  stands the next tick, when we would hold the card) and how many of those fit one accept per tick (two
  accepts each: the ask, then the bid);
- tape exits (not executable, a ceiling): asks cheaper, fee included, than the most a team had paid on the
  tape for that card;
- duplicate buys: asks whose cost (fee included) is at least `min_surplus` below the value of a 2nd or 3rd
  copy (book × affinity × 0.25 / 0.1), per affinity tier (the six multipliers every team shares, shuffled).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bazaar_agent.agents.market import BoardOffer, Venue, parse_offer
from bazaar_agent.arb import crossings, leg_cost
from bazaar_agent.intel import TEAM_ID, Event, tape

RASTRO = Venue("rastro", "", 500, 1, "open", "board", 0, True)
TIERS = (1.6, 1.3, 1.1, 0.9, 0.7, 0.5)  # the six set multipliers (RULES.md: same six for every team, shuffled)
MARGINALS = (1.0, 0.25, 0.1)  # catalog values.copy_marginals
BOOK = {"common": 10.0, "uncommon": 25.0, "rare": 70.0, "epic": 180.0, "legendary": 450.0}


def load_events(path: Path) -> list[Event]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class Span:
    """One plain offer's life on its venue: standing from `start` while tick < `end`."""

    offer: BoardOffer
    start: int
    end: int
    how: str  # filled | cancelled | expired | open


def spans(events: Iterable[Event], last_tick: int | None = None) -> list[Span]:
    """Every plain ask and bid open to anyone, with when it left the board and how."""
    events = sorted(events, key=lambda e: (int(e.get("tick") or 0), int(e.get("id") or 0)))
    horizon = (last_tick if last_tick is not None else max((int(e.get("tick") or 0) for e in events), default=0)) + 1
    rows: dict[int, Span] = {}
    for e in events:
        t, p = int(e.get("tick") or 0), e.get("payload") or {}
        if e.get("type") == "offer.listed":
            o = p.get("offer") or {}
            if o.get("to") is not None or o.get("thread") is not None:
                continue
            parsed = parse_offer(o, p.get("venue"))
            if parsed is None:
                continue
            if parsed.rarity is None and parsed.side == "ask":
                rarity = next((a.get("rarity") for a in (o.get("give") or {}).get("assets") or []), None)
                parsed = replace(parsed, rarity=rarity)
            expires = parsed.expires_tick if isinstance(parsed.expires_tick, int) else horizon
            rows[parsed.id] = Span(parsed, t, min(expires, horizon), "expired" if expires < horizon else "open")
        elif e.get("type") == "offer.cancelled":
            oid = p.get("offer")
            oid = oid.get("id") if isinstance(oid, dict) else oid  # observed: the bare id; a dict is read too
            s = rows.get(oid) if isinstance(oid, int) else None
            if s is not None and s.start <= t < s.end:
                s.end, s.how = t, "cancelled"
        elif e.get("type") == "settlement" and p.get("venue"):
            for item in p.get("items") or []:
                _fill(rows.values(), p, item, t)
    return list(rows.values())


def _fill(rows: Iterable[Span], p: dict[str, Any], item: dict[str, Any], t: int) -> None:
    standing = [s for s in rows if s.offer.venue == p.get("venue") and s.start < t <= s.end and s.how != "filled"]
    asks = [s for s in standing if s.offer.side == "ask" and s.offer.asset_id == item.get("id")]
    bids = [
        s
        for s in standing
        if s.offer.side == "bid"
        and s.offer.ref == item.get("ref")
        and s.offer.maker == item.get("to")
        and s.offer.price == p.get("price")
    ]
    # one settled item took one offer: the ask that gave this copy, else the oldest matching bid
    for s in asks[:1] or sorted(bids, key=lambda s: s.start)[:1]:
        s.end, s.how = t, "filled"


def venues_by_tick(events: Iterable[Event], last_tick: int) -> list[dict[str, Venue]]:
    """The venues and their fees in force at each tick 0..last_tick (a fee change counts from its tick)."""
    changes: dict[int, list[Venue]] = {}
    known: dict[str, Venue] = {}
    for e in sorted(events, key=lambda e: (int(e.get("tick") or 0), int(e.get("id") or 0))):
        p, t = e.get("payload") or {}, int(e.get("tick") or 0)
        if e.get("type") == "venue.opened" and p.get("venue"):
            mech = str((p.get("rules") or {}).get("mechanism") or "board")
            bps, per_card = int(p.get("fee_bps") or 0), int(p.get("fee_per_card") or 0)
            v = Venue(str(p["venue"]), str(p.get("owner") or ""), bps, per_card, "open", mech, 0, False)
        elif e.get("type") == "venue.closed" and p.get("venue") in known:
            v = replace(known[p["venue"]], status="closed")
        elif e.get("type") == "venue.fee_changed" and p.get("venue") in known:
            v = replace(
                known[p["venue"]], fee_bps=int(p.get("fee_bps") or 0), fee_per_card=int(p.get("fee_per_card") or 0)
            )
        else:
            continue
        known[v.id] = v
        changes.setdefault(t, []).append(v)
    out, now = [], {"rastro": RASTRO}
    for t in range(last_tick + 1):
        for v in changes.get(t, []):
            now = {k: x for k, x in {**now, v.id: v}.items() if x.status == "open"}
        out.append(now)
    return out


def zero_fees(venues: dict[str, Venue]) -> dict[str, Venue]:
    return {k: replace(v, fee_bps=0, fee_per_card=0) for k, v in venues.items()}


def boards(rows: list[Span], last_tick: int) -> Iterator[tuple[int, list[BoardOffer]]]:
    for t in range(last_tick + 1):
        yield t, [s.offer for s in rows if s.start <= t < s.end]


# ---------------------------------------------------------------- crossings


@dataclass
class PairStats:
    ask: BoardOffer
    bid: BoardOffer
    gross: int
    net: int  # the best net it reached (fees can change while it stands)
    ticks: list[int] = field(default_factory=list)  # ticks the pair crossed by at least min_net
    nets: dict[int, int] = field(default_factory=dict)  # tick -> net that tick
    executable: list[int] = field(default_factory=list)  # ... and the bid still stood the next tick


@dataclass(frozen=True)
class CrossingStudy:
    label: str
    pairs_overlapping: int  # ask/bid pairs for one card, different makers, standing in one tick
    pairs_gross_positive: int
    pairs_net_ok: int  # net ≥ min_net in at least one tick
    executable: int  # ... with the bid standing the next tick
    scheduled: int  # executable ones that fit one accept per tick (ask at t, bid at t + 1)
    net_scheduled: int  # primas of net spread they would have made
    median_ticks: float  # how long a net-ok crossing lasted
    best: tuple[PairStats, ...]


def crossing_study(
    rows: list[Span], fees: list[dict[str, Venue]], *, min_net: int, label: str, zero: bool = False
) -> CrossingStudy:
    last = len(fees) - 1
    end = {s.offer.id: s.end for s in rows}
    pairs: dict[tuple[int, int], PairStats] = {}
    overlapping: set[tuple[int, int]] = set()
    gross_positive: set[tuple[int, int]] = set()
    for t, board in boards(rows, last):
        venues = zero_fees(fees[t]) if zero else fees[t]
        for c in crossings(board, venues, min_net=-(10**9)):
            key = (c.ask.id, c.bid.id)
            overlapping.add(key)
            if c.gross > 0:
                gross_positive.add(key)
            if c.net < min_net:
                continue
            ps = pairs.setdefault(key, PairStats(c.ask, c.bid, c.gross, c.net))
            ps.net, ps.gross = max(ps.net, c.net), max(ps.gross, c.gross)
            ps.ticks.append(t)
            ps.nets[t] = c.net
            if end[c.bid.id] > t + 1:
                ps.executable.append(t)
    # one accept per tick: the ask at t and the bid at t + 1 take two slots; one card fills one bid
    busy: set[int] = set()
    used: set[int] = set()
    scheduled: list[tuple[PairStats, int]] = []
    for ps in sorted(pairs.values(), key=lambda p: (min(p.executable or [10**9]), -p.net)):
        if ps.ask.id in used or ps.bid.id in used:
            continue
        slot = next((t for t in ps.executable if t not in busy and t + 1 not in busy), None)
        if slot is not None:
            busy |= {slot, slot + 1}
            used |= {ps.ask.id, ps.bid.id}
            scheduled.append((ps, slot))
    lasted = sorted(len(p.ticks) for p in pairs.values())
    median = float(lasted[len(lasted) // 2]) if lasted else 0.0
    return CrossingStudy(
        label,
        len(overlapping),
        len(gross_positive),
        len(pairs),
        sum(1 for p in pairs.values() if p.executable),
        len(scheduled),
        sum(p.nets[t] for p, t in scheduled),
        median,
        tuple(sorted(pairs.values(), key=lambda p: -p.net)[:10]),
    )


# ---------------------------------------------------------------- tape exits and duplicates


def tape_exits(events: list[Event], rows: list[Span], fees: list[dict[str, Venue]]) -> list[tuple[Span, int, int]]:
    """(ask, its cost, the most a team had paid for that card before it was listed) where cost < that price."""
    paid: dict[str, list[tuple[int, int]]] = {}
    for p in tape(events):
        if p.items == 1 and TEAM_ID.match(p.buyer) and p.venue:
            paid.setdefault(p.ref, []).append((p.tick, p.price))
    out = []
    for s in rows:
        if s.offer.side != "ask" or s.offer.venue not in fees[min(s.start, len(fees) - 1)]:
            continue
        before = [price for tick, price in paid.get(s.offer.ref, []) if tick <= s.start]
        cost = leg_cost(fees[min(s.start, len(fees) - 1)][s.offer.venue], s.offer.price)
        if before and cost < max(before):
            out.append((s, cost, max(before)))
    return out


@dataclass(frozen=True)
class DupTier:
    tier: float
    copy: int  # 2 = the second copy (marginal 0.25), 3 = the third (0.1)
    asks: int
    hits: int  # surplus ≥ min_surplus
    surplus: float  # their total surplus
    best: dict[str, float]  # rarity -> the best (least negative) surplus of any ask


def dup_study(rows: list[Span], fees: list[dict[str, Venue]], *, min_surplus: float) -> list[DupTier]:
    asks = [s for s in rows if s.offer.side == "ask" and s.offer.rarity in BOOK]
    out = []
    for tier in TIERS:
        for copy in (2, 3):
            hits, total, best = 0, 0.0, dict[str, float]()
            for s in asks:
                venue = fees[min(s.start, len(fees) - 1)].get(s.offer.venue)
                if venue is None:
                    continue
                rarity = str(s.offer.rarity)
                surplus = BOOK[rarity] * tier * MARGINALS[copy - 1] - leg_cost(venue, s.offer.price)
                best[rarity] = round(max(best.get(rarity, -(10.0**9)), surplus), 1)
                if surplus >= min_surplus:
                    hits, total = hits + 1, total + surplus
            out.append(DupTier(tier, copy, len(asks), hits, round(total, 1), best))
    return out


def private_dups(
    rows: list[Span], fees: list[dict[str, Venue]], me: dict[str, Any], *, min_surplus: float
) -> list[tuple[Span, float]]:
    """With OUR affinities and holdings (an `agent.me` snapshot): asks for cards we held, with their surplus.
    Private: printed to the console or the _night folder only, never committed."""
    affinity = {str(k): float(v) for k, v in (me.get("affinity") or {}).items()}
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")
    out = []
    for s in rows:
        n, rarity = held.get(s.offer.ref, 0), s.offer.rarity
        venue = fees[min(s.start, len(fees) - 1)].get(s.offer.venue)
        if s.offer.side != "ask" or not n or rarity not in BOOK or venue is None:
            continue
        marginal = MARGINALS[n] if n < len(MARGINALS) else 0.0
        value = BOOK[str(rarity)] * affinity.get(s.offer.ref.split("-")[0], 1.0) * marginal
        surplus = round(value - leg_cost(venue, s.offer.price), 1)
        if surplus >= min_surplus:
            out.append((s, surplus))
    return out


# ---------------------------------------------------------------- the whole study


@dataclass(frozen=True)
class Study:
    ticks: tuple[int, int]
    listed: Counter[tuple[str, str]]  # (venue, side) -> plain public offers
    how: Counter[tuple[str, str]]  # (side, how it left) -> offers
    bid_lifetimes: dict[str, float]  # share of bids standing ≥ 1, 2, 3 ticks
    with_fees: CrossingStudy
    zero_fee: CrossingStudy
    tape_exits: int
    tape_exit_margin: int
    dups: list[DupTier]


def study(events: list[Event], *, min_net: int = 3, min_surplus: float = 3.0) -> Study:
    last = max((int(e.get("tick") or 0) for e in events), default=0)
    rows = spans(events, last)
    fees = venues_by_tick(events, last)
    bids = [s for s in rows if s.offer.side == "bid"]
    life = {
        f"≥{k} ticks": round(sum(1 for s in bids if s.end - s.start >= k) / max(1, len(bids)), 2) for k in (1, 2, 3)
    }
    exits = tape_exits(events, rows, fees)
    first = min((int(e.get("tick") or 0) for e in events), default=0)
    return Study(
        (first, last),
        Counter((s.offer.venue, s.offer.side) for s in rows),
        Counter((s.offer.side, s.how) for s in rows),
        life,
        crossing_study(rows, fees, min_net=min_net, label="fees as charged"),
        crossing_study(rows, fees, min_net=min_net, label="every fee at 0 (a 0 bps team venue)", zero=True),
        len(exits),
        sum(paid - cost for _, cost, paid in exits),
        dup_study(rows, fees, min_surplus=min_surplus),
    )


def render(s: Study, *, min_net: int, min_surplus: float) -> str:
    """The study as Markdown tables (no private values)."""
    lines = [
        f"# Arbitrage study, ticks {s.ticks[0]}–{s.ticks[1]}",
        "",
        "## The board",
        "",
        "| venue | side | public plain offers |",
        "|---|---|---:|",
        *[f"| {v} | {side} | {n} |" for (v, side), n in sorted(s.listed.items())],
        "",
        "| side | left the board by | offers |",
        "|---|---|---:|",
        *[f"| {side} | {how} | {n} |" for (side, how), n in sorted(s.how.items())],
        "",
        "Bids standing at least k ticks: " + ", ".join(f"{k} {v:.0%}" for k, v in s.bid_lifetimes.items()),
        "",
        f"## Crossings (ask + bid for one card, different makers, same tick; net ≥ {min_net} P)",
        "",
        "| fees | overlapping pairs | gross > 0 | net ≥ min | executable | fit 1 accept/tick | net P |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for c in (s.with_fees, s.zero_fee):
        lines.append(
            f"| {c.label} | {c.pairs_overlapping} | {c.pairs_gross_positive} | {c.pairs_net_ok} | {c.executable} "
            f"| {c.scheduled} | {c.net_scheduled} |"
        )
    for c in (s.with_fees, s.zero_fee):
        if c.best:
            lines += ["", f"Best crossings, {c.label} (median {c.median_ticks:g} ticks crossed):", ""]
            lines += [
                "| card | ask | bid | gross | net | ticks | executable ticks |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            lines += [
                f"| {p.ask.ref} | {p.ask.price} ({p.ask.maker}) | {p.bid.price} ({p.bid.maker}) | {p.gross} | {p.net} "
                f"| {len(p.ticks)} | {len(p.executable)} |"
                for p in c.best
            ]
    lines += [
        "",
        f"Tape exits (not executable, a ceiling): {s.tape_exits} asks cost less, fee included, than the most a team "
        f"had paid for the card before; {s.tape_exit_margin} P of margin in all.",
        "",
        f"## Duplicate buys (value of one more copy − ask − fee ≥ {min_surplus:g} P), per affinity tier",
        "",
        "| tier | copy | asks | ≥ min | surplus P | best common | best uncommon | best rare |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for d in s.dups:
        best = [d.best.get(r) for r in ("common", "uncommon", "rare")]
        cells = " | ".join("-" if b is None else f"{b:+g}" for b in best)
        lines.append(f"| {d.tier:g} | {d.copy} | {d.asks} | {d.hits} | {d.surplus:g} | {cells} |")
    return "\n".join(lines) + "\n"
