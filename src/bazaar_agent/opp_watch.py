"""Live opportunity alerts for the monitor: W8's arbitrage and duplicate scan plus B4's opportunity scanner,
read-only, deduplicated, and a log of every opportunity with how long it stood.

Once every `every_ticks` the monitor reads the venue boards (keyless public reads: the team key's 5 req/s is
not touched) and scores what stands:
  - `arb`: a standing ask we could buy and resell into a standing bid (net after both fees, `arb.scan`);
  - `dup`: an ask for a card we hold whose next copy is worth more than its cost (`arb.scan`);
  - `buy` / `sell`: B4's `opportunities.scan`, a missing page card below its value to us, a bid above what
    selling our least valuable copy costs us.
An opportunity is keyed by its offers (`arb:<ask>:<bid>`, `dup:<ask>`, `buy:<offer>`, `sell:<offer>`). It is
logged when first seen (`open`) and when it is gone (`closed`, with first/last tick, scans seen, best and last
value), as long as its value reaches `log_floor`; it raises ONE alert in its life, the first scan its value
reaches the kind's alert threshold. Nothing here sends anything to the game: the switches (`arb_enabled`,
`dup_buy_enabled`, the taker's `accept_bids`) are decided by a person on what this log shows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any

from bazaar_agent import arb
from bazaar_agent.agents.market import BoardOffer, Venue, board_offers, tradable_venues, venues_from
from bazaar_agent.guardrails import Context, Guardrails, held_cards
from bazaar_agent.intel import Event, listed_makers
from bazaar_agent.monitor import Alert
from bazaar_agent.strategy import Market, StrategyParams, build_market

LOG_FILE = "opportunities.jsonl"
KINDS = ("arb", "dup", "buy", "sell")


@dataclass(frozen=True)
class WatchParams:
    every_ticks: int = 1  # scan the boards once every N ticks
    arb_alert_net: float = 3.0  # alert a crossing netting at least this after both fees (arb_min_net_spread)
    dup_alert_surplus: float = 3.0  # alert a duplicate ask worth at least this more than its cost (dup_min_surplus)
    opp_alert_surplus: float = 5.0  # alert a B4 buy/sell opportunity worth at least this to us
    log_floor: float = 0.0  # log every opportunity whose value reaches this (0: every one that gains us anything)
    b4: bool = True  # also run B4's opportunity scanner (buy/sell)

    def threshold(self, kind: str) -> float:
        return {"arb": self.arb_alert_net, "dup": self.dup_alert_surplus}.get(kind, self.opp_alert_surplus)


@dataclass(frozen=True)
class Seen:
    """One opportunity standing in one scan."""

    key: str
    kind: str  # arb | dup | buy | sell
    ref: str
    value: float  # arb: net after both fees; dup: surplus over cost; buy/sell: what accepting gains us
    detail: str
    makers: tuple[str, ...]
    takeable: str  # "" when the taker would take it with its switch on, else why not (guardrails, maker)


@dataclass
class Tracked:
    seen: Seen
    first_tick: int
    last_tick: int
    scans: int = 1
    best: float = 0.0
    alerted: bool = False


def seen_from_scan(s: arb.Scan) -> list[Seen]:
    out = []
    for c in s.crossings:
        legs = c.legs
        detail = (
            f"buy {c.ask.price} on {c.ask.venue} ({c.ask.maker}) → sell {c.bid.price} on {c.bid.venue} "
            f"({c.bid.maker}): net {c.net:+d}" + ("" if legs is None else f", legs {legs[0]:+g}/{legs[1]:+g}")
        )
        takeable = arb.why_not(c, s.sell_ratio)
        out.append(
            Seen(f"arb:{c.ask.id}:{c.bid.id}", "arb", c.ref, float(c.net), detail, (c.ask.maker, c.bid.maker), takeable)
        )
    for d in s.dups:
        detail = f"ask {d.ask.price} on {d.ask.venue} ({d.ask.maker}): one more copy worth {d.value:g}, cost {d.cost}"
        out.append(Seen(f"dup:{d.ask.id}", "dup", d.ask.ref, d.surplus, detail, (d.ask.maker,), ""))
    return out


def seen_from_opportunities(opps: Iterable[Any]) -> list[Seen]:
    """B4's `opportunities.Opportunity` rows (duck-typed: offer_id, kind, ref, ours, maker, venue, price, verdict)."""
    out = []
    for o in opps:
        verb = "buy at" if o.kind == "buy" else "sell into"
        detail = f"{verb} {o.price} on {o.venue} ({o.maker}): {o.ours:+g} to us{f', {o.tag}' if o.tag else ''}"
        takeable = "" if o.verdict == "allowed" else o.verdict
        out.append(Seen(f"{o.kind}:{o.offer_id}", o.kind, o.ref, float(o.ours), detail, (o.maker,), takeable))
    return out


@dataclass
class Tracker:
    """Opportunities standing now; what opened, what closed, and the one alert each may raise."""

    params: WatchParams
    open: dict[str, Tracked] = field(default_factory=dict)

    def update(self, tick: int, seen: Iterable[Seen]) -> tuple[list[Alert], list[dict[str, Any]]]:
        alerts: list[Alert] = []
        rows: list[dict[str, Any]] = []
        now = {s.key: s for s in seen if s.value >= self.params.log_floor}
        for key in list(self.open):
            if key not in now:
                rows.append(self._row("closed", self.open.pop(key)))
        for key, s in now.items():
            t = self.open.get(key)
            if t is None:
                t = self.open[key] = Tracked(s, tick, tick, 1, s.value)
                rows.append(self._row("open", t))
            else:
                t.seen, t.last_tick, t.scans, t.best = s, tick, t.scans + 1, max(t.best, s.value)
            if not t.alerted and s.value >= self.params.threshold(s.kind):
                t.alerted = True
                note = "" if not s.takeable else f" (the taker would not: {s.takeable})"
                alerts.append(Alert(tick, f"opportunity:{s.kind}", s.ref, s.detail + note))
        return alerts, rows

    def close_all(self) -> list[dict[str, Any]]:
        rows = [self._row("closed", t) for t in self.open.values()]
        self.open.clear()
        return rows

    @staticmethod
    def _row(event: str, t: Tracked) -> dict[str, Any]:
        return {
            "event": event,
            **{k: v for k, v in asdict(t.seen).items() if k != "makers"},
            "makers": list(t.seen.makers),
            "first_tick": t.first_tick,
            "last_tick": t.last_tick,
            "scans": t.scans,
            "best": round(t.best, 2),
            "alerted": t.alerted,
        }


def append_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- one scan (reads: public, keyless)


@dataclass
class Scanner:
    """Reads the boards and scores them with both scanners. `refresh` rebuilds what is costly to compute (the
    rival affinity map, the tape) from the whole feed history; makers are named from every event seen."""

    params: WatchParams
    rules: Guardrails
    strategy: StrategyParams
    catalog: dict[str, Any] = field(default_factory=dict)
    history: list[Event] = field(default_factory=list)
    makers: dict[int, str] = field(default_factory=dict)
    amap: Any = None

    def refresh(self, catalog: dict[str, Any], history: Sequence[Event], us: str, me: dict[str, Any]) -> None:
        self.catalog, self.history = catalog, list(history)
        self.makers |= listed_makers(history)
        if self.params.b4:
            from bazaar_agent import affinity as af

            sets, mult = af.catalog_sets(catalog), af.multipliers_from(me)
            self.amap = af.affinity_map(self.history, sets, mult, catalog, exclude=[us])

    def learn(self, events: Iterable[Event]) -> None:
        self.makers |= listed_makers(events)

    def scan(self, public: Any, me: dict[str, Any], tick: int) -> tuple[list[Seen], int]:
        """Every opportunity standing now, and how many board reads it took."""
        us = str(me.get("id") or "")
        venues = {v.id: v for v in tradable_venues(venues_from(public.venues()), us)}
        offers: list[BoardOffer] = []
        reads = 1
        for vid in venues:
            reads += 1
            offers += [
                replace(o, maker=self.makers.get(o.id, o.maker)) for o in board_offers(public.board(vid), vid, us)
            ]
        ours = {o.id for o in offers if o.maker == us}
        market = build_market(me, self.catalog, self.history, [])
        floor = self.params.log_floor
        ratio = self.rules.sell_min_value_ratio
        s = arb.scan(market, venues, offers, ours=ours, min_net=int(floor), min_surplus=floor, sell_ratio=ratio)
        seen = seen_from_scan(s)
        if self.params.b4 and self.amap is not None:
            seen += self._b4(offers, market, me, venues, tick)
        return seen, reads

    def _b4(
        self, offers: list[BoardOffer], market: Market, me: dict[str, Any], venues: dict[str, Venue], tick: int
    ) -> list[Seen]:
        from bazaar_agent import opportunities as op

        ctx = Context(int(me.get("cash") or 0), held_cards(me), tick, 0.0)  # /me only: no ledger, no open offers
        found = op.scan(
            offers,
            market,
            me,
            self.strategy,
            self.rules,
            self.amap,
            venues,
            ctx,
            self.history,
            self.catalog,
            min_surplus=self.params.log_floor,
        )
        return seen_from_opportunities(found)


# ---------------------------------------------------------------- the log, summarised for the switch decision


@dataclass(frozen=True)
class KindSummary:
    kind: str
    seen: int
    alerted: int
    takeable: int  # the taker would have taken it with its switch on
    lasted_2: int  # stood in at least two scans (an arbitrage needs its bid the next tick)
    median_ticks: float
    max_ticks: int
    best: float
    total_best: float  # Σ best value of the takeable ones that lasted ≥ 2 scans: a ceiling on what the switch earns


def summarise(rows: Iterable[dict[str, Any]], since_tick: int | None = None) -> list[KindSummary]:
    """One line per kind from the log's `closed` rows (the last state of every opportunity; still-open ones are
    counted from their `open` row)."""
    last: dict[str, dict[str, Any]] = {}
    for r in rows:
        if since_tick is not None and int(r.get("first_tick") or 0) < since_tick:
            continue
        if r.get("event") == "closed" or r.get("key") not in last:
            last[str(r.get("key"))] = r
    out = []
    for kind in KINDS:
        rs = [r for r in last.values() if r.get("kind") == kind]
        if not rs:
            continue
        ticks = sorted(int(r["last_tick"]) - int(r["first_tick"]) + 1 for r in rs)
        good = [r for r in rs if not r.get("takeable") and int(r.get("scans") or 1) >= 2]
        out.append(
            KindSummary(
                kind,
                len(rs),
                sum(1 for r in rs if r.get("alerted")),
                sum(1 for r in rs if not r.get("takeable")),
                sum(1 for r in rs if int(r.get("scans") or 1) >= 2),
                float(median(ticks)),
                max(ticks),
                max(float(r.get("best") or 0) for r in rs),
                round(sum(float(r.get("best") or 0) for r in good), 1),
            )
        )
    return out


def render_summary(summary: list[KindSummary], path: Path) -> str:
    lines = [
        f"# Opportunities seen ({path})",
        "",
        "| kind | seen | alerted | taker would take | stood ≥ 2 scans | median ticks | max ticks | best "
        "| Σ best (takeable, ≥ 2 scans) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {s.kind} | {s.seen} | {s.alerted} | {s.takeable} | {s.lasted_2} | {s.median_ticks:g} | {s.max_ticks} "
        f"| {s.best:+g} | {s.total_best:+g} |"
        for s in summary
    ]
    if not summary:
        lines.append("| - | 0 | | | | | | | |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the same tracker over a captured day


def replay(
    events: list[Event],
    me: dict[str, Any],
    catalog: dict[str, Any],
    params: WatchParams,
    rules: Guardrails,
    strategy: StrategyParams,
) -> list[dict[str, Any]]:
    """What the monitor would have logged had it scanned every tick of a captured feed: the boards rebuilt from
    `offer.listed` / cancels / fills (`arb_study`), our album as `me` has it now (an approximation)."""
    from bazaar_agent import arb_study

    last = max((int(e.get("tick") or 0) for e in events), default=0)
    rows_by_span, fees = arb_study.spans(events, last), arb_study.venues_by_tick(events, last)
    us = str(me.get("id") or "")
    s = Scanner(params, rules, strategy)
    s.refresh(catalog, events, us, me)
    market = build_market(me, catalog, events, [])
    tracker = Tracker(params)
    out: list[dict[str, Any]] = []
    for tick, board in arb_study.boards(rows_by_span, last):
        venues = {k: v for k, v in fees[tick].items() if v.owner != us}
        offers = [o for o in board if o.maker != us]
        floor = params.log_floor
        found = seen_from_scan(arb.scan(market, venues, offers, ours=set(), min_net=int(floor), min_surplus=floor))
        if params.b4 and s.amap is not None:
            found += s._b4(offers, market, me, venues, tick)
        out += tracker.update(tick, found)[1]
    return out + tracker.close_all()
