"""The operator's cockpit: one read-only screen of everything Saturday's gates look at.

`bazaar cockpit` reads, never writes:
- keyless: `/api/clock`, `/api/schedule`, `/api/dealers`, and our agents' public `GET /health`;
- with the team key, reads only: `/api/me`, `/api/me/offers`, `/api/me/threads`, `/api/duels`;
- Postgres with `default_transaction_read_only` (the ledger and who writes it), and the monitor's alerts file.

Each source is read on its own: one that fails shows its error in its panel and the others still render.
`build()` is pure (raw responses in, panels out), so the tests feed it fixtures or a local simulator.
Every line carries a status: `ok`, `warn` (look at it), `bad` (act now), or `info`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from bazaar_agent import timeline as tl

Status = Literal["ok", "warn", "bad", "info"]
MARK: dict[str, str] = {"ok": "  ok", "warn": "WARN", "bad": " BAD", "info": "    "}
HEALTH_URLS = {  # docs/services.md: the agents' public read-only status (no key, no numbers)
    "taker": "https://bazaar-taker-production.up.railway.app/health",
    "maker": "https://bazaar-maker-production.up.railway.app/health",
}
HEADROOM_WARN = 30  # primas above the floor (after open bids) under which buys are about to stop
DUEL_DEADLINE_WARN = 2  # ticks: a live duel this close to its deadline with no offer of ours
TICK_LAG_WARN = 2  # ticks an agent may trail the server before it looks stuck


@dataclass(frozen=True)
class Line:
    label: str
    value: str
    status: Status = "info"


@dataclass(frozen=True)
class Panel:
    title: str
    lines: tuple[Line, ...]

    @property
    def status(self) -> Status:
        for s in ("bad", "warn", "ok"):
            if any(line.status == s for line in self.lines):
                return s  # type: ignore[return-value]
        return "info"


@dataclass(frozen=True)
class LedgerView:
    where: str  # "postgres ledger table" or "file ledger.jsonl"
    shared: bool
    spent_last_hour: int = 0
    packs_last_hour: int = 0
    accepts_this_tick: int = 0
    listings_this_tick: int = 0
    writers: Mapping[str, int] = field(default_factory=dict)  # source -> newest tick it wrote (Postgres only)


@dataclass(frozen=True)
class Limits:
    """The guardrail values the cockpit compares against (GUARDRAILS.md)."""

    cash_floor: int = 270
    venue_bond_reserve: int = 0  # PR #71's head: kept on top of the floor until our venue opens
    max_spend_per_game_hour: int = 150
    max_packs_per_game_hour: int = 3
    max_accepts_per_tick: int = 1
    trading_enabled: bool = True
    paused_here: bool = False  # this checkout's PAUSE file (each Railway volume has its own)


@dataclass
class Reads:
    """Raw responses, one per source; a source that failed is None with its error in `errors`."""

    now: datetime
    clock: Mapping[str, Any] | None = None
    schedule: Mapping[str, Any] | None = None
    dealers: Mapping[str, Any] | None = None
    me: Mapping[str, Any] | None = None
    offers: Mapping[str, Any] | None = None
    threads: Mapping[str, Any] | None = None
    duels: Mapping[str, Any] | None = None
    health: dict[str, Mapping[str, Any] | None] = field(default_factory=dict)
    ledger: LedgerView | None = None
    alerts: list[Mapping[str, Any]] = field(default_factory=list)
    plays: Mapping[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def _n(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _missing(reads: Reads, *sources: str) -> Line | None:
    for s in sources:
        if getattr(reads, s) is None:
            return Line(s, f"unavailable: {reads.errors.get(s, 'not read')}", "bad")
    return None


def _limits(clock: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return (clock or {}).get("limits") or {}


def _ours(offer: Mapping[str, Any], team: str | None) -> bool:
    return team is None or offer.get("maker") in (team, None)


def committed_cash(offers: Mapping[str, Any], threads: Iterable[Mapping[str, Any]], team: str | None) -> int:
    """Cash our open or queued offers would take if they filled, the way the guardrails count it.

    Board offers (every list in the `/api/me/offers` body) and the offers inside our threads, one per offer id:
    a dealer-thread bid may appear in both, and `/api/me/offers` wins over a thread's older snapshot.
    """
    from bazaar_agent.agents.seller import offers_in, open_commitments

    by_id: dict[Any, Mapping[str, Any]] = {}
    for o in [*thread_offers(threads), *offers_in(dict(offers))]:
        by_id[o.get("id", id(o))] = o
    return open_commitments([dict(o) for o in by_id.values()], team or "").cash


def thread_offers(threads: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every structured offer inside our threads (each message may carry one)."""
    out: list[Mapping[str, Any]] = []
    for t in threads:
        for m in t.get("messages") or []:
            if isinstance(m.get("offer"), Mapping):
                out.append(m["offer"])
    return out


# ---------------------------------------------------------------- panels


def clock_panel(reads: Reads) -> Panel:
    if (miss := _missing(reads, "clock")) is not None:
        return Panel("Clock", (miss,))
    c = reads.clock or {}
    doors = str(c.get("doors", "open"))
    lim = _limits(c)
    lines = [
        Line("doors", f"{doors}{' (paused)' if c.get('paused') else ''}", "ok" if doors == "open" else "warn"),
        Line("tick", f"{c.get('tick')} · game hour {c.get('t_hours')} · {c.get('tick_seconds')} s ticks"),
        Line("round", f"{c.get('round')} {c.get('round_name') or ''}".strip()),
        Line(
            "limits",
            f"{lim.get('accepts_per_team_per_tick', '?')} accept, {lim.get('offers_per_team_per_tick', '?')} listings"
            f" per tick · {lim.get('max_open_threads_per_team', '?')} threads · "
            f"{lim.get('max_open_offers_per_team', '?')} open offers",
        ),
    ]
    if doors != "open":
        lines.append(Line("next opening", str(c.get("next_opens") or "-")))
    return Panel("Clock", tuple(lines))


def next_panel(reads: Reads, count: int = 4) -> Panel:
    """The playbook's next scheduled events (B6 timeline), with each event's play when the plays file is given."""
    if (miss := _missing(reads, "clock", "schedule")) is not None:
        return Panel("Next (playbook)", (miss,))
    events, days = tl.parse_events(reads.schedule or {}), tl.parse_days(reads.clock or {})
    found = tl.anchors(reads.clock or {}, events, reads.now)
    if not found:
        return Panel("Next (playbook)", (Line("schedule", "no anchor: the clock has no next opening", "warn"),))
    doc = tl.as_dict(tl.timeline(events, days, found), found, "live")
    if reads.plays:
        doc = tl.with_plays(doc, reads.plays)
    first = found[0].name
    upcoming = []
    for e in doc["events"]:
        start = e["slots"][first]["start"]
        if e["slots"][first]["status"] == "scheduled" and start and datetime.fromisoformat(start) >= reads.now:
            upcoming.append((datetime.fromisoformat(start), e))
    lines = []
    for start, e in sorted(upcoming, key=lambda x: x[0])[:count]:
        minutes = int((start - reads.now).total_seconds() // 60)
        other = [
            f"{a.name} {datetime.fromisoformat(s):%H:%M}"
            for a in found[1:]
            if (s := e["slots"][a.name]["start"]) and e["slots"][a.name]["status"] == "scheduled"
        ]
        when = f"{start:%a %H:%M} ({first}, in {minutes} min)" + (f" · {', '.join(other)}" if other else "")
        play = (e.get("play") or {}).get("do") or e["note"]
        lines.append(Line(f"h{e['at_hours']:g} {e['name']}", f"{when} · {str(play).split('. ')[0]}"))
    return Panel("Next (playbook)", tuple(lines) or (Line("schedule", "nothing left today"),))


def cash_panel(reads: Reads, limits: Limits) -> Panel:
    if (miss := _missing(reads, "me")) is not None:
        return Panel("Cash", (miss,))
    me = reads.me or {}
    team = me.get("id")
    cash = _n(me.get("cash"))
    reserve = limits.venue_bond_reserve if limits.venue_bond_reserve and not me.get("venue") else 0
    floor = limits.cash_floor + reserve
    floor_line = Line(
        "floor", f"{floor} P (cash_floor {limits.cash_floor}" + (f" + venue reserve {reserve}" if reserve else "") + ")"
    )
    if reads.offers is None or reads.threads is None:  # unknown open bids: never show a headroom we cannot back
        missing = "offers" if reads.offers is None else "threads"
        lines = [
            Line("cash", f"{cash} P · open bids unknown"),
            floor_line,
            Line("headroom", f"unknown: {missing} unreadable ({reads.errors.get(missing, 'not read')})", "bad"),
        ]
    else:
        committed = committed_cash(reads.offers, (reads.threads or {}).get("threads") or [], team)
        headroom = cash - committed - floor
        status: Status = "bad" if headroom < 0 else "warn" if headroom < HEADROOM_WARN else "ok"
        lines = [
            Line("cash", f"{cash} P · open bids {committed} P"),
            floor_line,
            Line("headroom", f"{headroom} P above the floor after open bids", status),
        ]
    if reads.ledger is not None:
        spent = reads.ledger.spent_last_hour
        cap = limits.max_spend_per_game_hour
        lines.append(Line("spent, last game hour", f"{spent} / {cap} P", "bad" if spent >= cap else "ok"))
        packs = reads.ledger.packs_last_hour
        lines.append(
            Line(
                "packs, last game hour",
                f"{packs} / {limits.max_packs_per_game_hour}",
                "warn" if packs >= limits.max_packs_per_game_hour else "ok",
            )
        )
    sealed = [a for a in me.get("assets") or [] if a.get("kind") == "pack"]
    if sealed:  # r2 X21: no agent opens packs
        ids = ", ".join(str(a.get("id")) for a in sealed)
        lines.append(Line("sealed packs", f"{len(sealed)} (assets {ids}): open them by hand", "warn"))
    if not limits.trading_enabled:
        lines.append(Line("trading_enabled", "false: every write is refused", "warn"))
    if limits.paused_here:
        lines.append(Line("PAUSE", "this checkout's PAUSE file exists", "warn"))
    return Panel("Cash", tuple(lines))


def ledger_panel(reads: Reads, limits: Limits, tick: int | None) -> Panel:
    if (miss := _missing(reads, "ledger")) is not None:
        return Panel("Ledger", (miss,))
    led = reads.ledger
    assert led is not None
    lines = [
        Line(
            "where",
            led.where + (" (shared across machines)" if led.shared else " (THIS machine only)"),
            "ok" if led.shared else "bad",
        ),
        Line(
            "accepts this tick",
            f"{led.accepts_this_tick} / {limits.max_accepts_per_tick}",
            "warn" if led.accepts_this_tick >= limits.max_accepts_per_tick else "ok",
        ),
        Line("listings this tick", str(led.listings_this_tick)),
    ]
    for source, last in sorted(led.writers.items()):
        lag = (tick - last) if tick is not None else None
        lines.append(Line(f"writer {source or '?'}", f"last row at tick {last}" + (f" ({lag} ago)" if lag else "")))
    if led.shared and not led.writers:
        lines.append(Line("writers", "no process wrote the shared ledger in the last 120 ticks", "warn"))
    return Panel("Ledger", tuple(lines))


def agents_panel(reads: Reads) -> Panel:
    lines = []
    server_tick = _n((reads.clock or {}).get("tick")) if reads.clock else None
    for name, h in sorted(reads.health.items()):
        if h is None:
            lines.append(Line(name, f"unreachable: {reads.errors.get(f'health:{name}', '?')}", "bad"))
            continue
        mode = str(h.get("mode"))
        target = str((h.get("target") or {}).get("mode") or "?")
        tick = h.get("tick")
        lag = server_tick - _n(tick) if server_tick is not None and tick is not None else None
        stuck = lag is not None and lag > TICK_LAG_WARN and (reads.clock or {}).get("doors") == "open"
        status: Status = "bad" if not h.get("ok", True) or stuck else "ok"
        text = f"{mode} · target {target} · tick {tick}" + (f" ({lag} behind)" if lag else "")
        lines.append(Line(name, text, status))
    lines.append(Line("duels", "no /health: watch `railway logs --service bazaar-duels` during a session"))
    return Panel("Agents", tuple(lines))


def caps_panel(reads: Reads) -> Panel:
    if (miss := _missing(reads, "offers", "threads", "clock")) is not None:
        return Panel("Caps", (miss,))
    lim = _limits(reads.clock)
    team = (reads.me or {}).get("id")
    from bazaar_agent.agents.seller import offers_in

    board = [o for o in offers_in(dict(reads.offers or {})) if o.get("status") == "open" and _ours(o, team)]
    threads = [t for t in (reads.threads or {}).get("threads") or [] if t.get("status") == "open"]

    def vs(n: int, cap: Any) -> Status:
        return "bad" if cap and n >= _n(cap) else "warn" if cap and n >= _n(cap) - 1 else "ok"

    max_offers, max_threads = lim.get("max_open_offers_per_team"), lim.get("max_open_threads_per_team")
    lines = [
        Line("open offers", f"{len(board)} / {max_offers or '?'}", vs(len(board), max_offers)),
        Line("open threads", f"{len(threads)} / {max_threads or '?'}", vs(len(threads), max_threads)),
    ]
    for t in threads:
        lines.append(Line(f"  thread {t.get('id')}", f"{t.get('kind')} with {t.get('with')}: {t.get('topic')}"))
    return Panel("Caps", tuple(lines))


def duels_panel(reads: Reads) -> Panel:
    if (miss := _missing(reads, "duels", "clock")) is not None:
        return Panel("Duels", (miss,))
    tick = _n((reads.clock or {}).get("tick"))
    live = [d for d in (reads.duels or {}).get("duels") or [] if d.get("status") == "live"]
    if not live:
        return Panel("Duels", (Line("live", "none"),))
    lines = [Line("live", f"{len(live)} (sessions {sorted({d.get('session') for d in live})})", "ok")]
    for d in sorted(live, key=lambda d: _n(d.get("deadline_tick"))):
        left = _n(d.get("deadline_tick")) - tick
        silent = d.get("your_offer") is None
        status: Status = "warn" if left <= DUEL_DEADLINE_WARN and silent else "info"
        issues = "+".join(d.get("issues") or ["price"])
        rival = (d.get("rival_offer") or {}).get("price")
        text = (
            f"{d.get('role')} '{d.get('item')}' ({issues}) · {left} ticks left · rounds {d.get('rounds')}"
            f" · rival at {rival if rival is not None else '-'}" + (" · we have not offered" if silent else "")
        )
        lines.append(Line(f"duel {d.get('duel')}", text, status))
    return Panel("Duels", tuple(lines))


def dealer_deals(threads: Iterable[Mapping[str, Any]]) -> dict[str, list[int]]:
    """Our settled dealer deals by dealer: the price of the accepted offer in each `deal` thread."""
    out: dict[str, list[int]] = {}
    for t in threads:
        if t.get("kind") != "persona" or t.get("status") != "deal":
            continue
        prices = [
            _n((o.get("give") or {}).get("cash") or (o.get("want") or {}).get("cash"))
            for o in thread_offers([t])
            if o.get("status") in ("accepted", "settled")
        ]
        out.setdefault(str(t.get("with")), []).extend(prices[-1:])
    return out


def ladder_panel(reads: Reads) -> Panel:
    if (miss := _missing(reads, "me", "threads")) is not None:
        return Panel("Ladder", (miss,))
    score = (reads.me or {}).get("score") or {}
    levels = {
        str(p.get("id")): p.get("level")
        for p in ((reads.dealers or {}).get("personas") or (reads.dealers or {}).get("dealers") or [])
    }
    lines = [
        Line("ladder_points", str(score.get("ladder_points", "-"))),
        Line("deals (official)", str(score.get("deals", "-"))),
    ]
    deals = dealer_deals((reads.threads or {}).get("threads") or [])
    for dealer in sorted(set(levels) | set(deals)):
        got = deals.get(dealer, [])
        status: Status = "ok" if len(got) >= 3 else "warn" if levels.get(dealer) else "info"
        lines.append(
            Line(
                f"L{levels.get(dealer) or '?'} {dealer}",
                f"{len(got)} deal(s) in /api/me/threads"
                + (f": {', '.join(map(str, got))} P" if got else "")
                + " (best three count per level)",
                status,
            )
        )
    return Panel("Ladder", tuple(lines))


def market_panel(reads: Reads) -> Panel:
    if (miss := _missing(reads, "me")) is not None:
        return Panel("Market Test", (miss,))
    me = reads.me or {}
    score = me.get("score") or {}
    venue = me.get("venue") or score.get("venue")
    lines = [
        Line("our venue", str(venue) if venue else "none (free stall, if it scores for us: gate G3)"),
        Line(
            "bench",
            f"points {score.get('bench_points')} · efficiency {score.get('bench_efficiency')}"
            f" · venue {score.get('bench_venue')}",
        ),
    ]
    if reads.clock and reads.schedule:
        events, days = tl.parse_events(reads.schedule), tl.parse_days(reads.clock)
        found = tl.anchors(reads.clock, events, reads.now)
        if found:
            nxt = [
                r.slots[found[0].name].start
                for r in tl.timeline([e for e in events if e.action == "bench"], days, found[:1])
                if r.slots[found[0].name].status == "scheduled"
            ]
            upcoming = sorted(s for s in nxt if s and s >= reads.now)
            if upcoming:
                minutes = int((upcoming[0] - reads.now).total_seconds() // 60)
                lines.append(Line("next Market Test", f"{upcoming[0]:%a %H:%M} ({found[0].name}, in {minutes} min)"))
    return Panel("Market Test", tuple(lines))


def alerts_panel(reads: Reads, count: int = 5) -> Panel:
    rows = reads.alerts[-count:]
    if not rows:
        return Panel("Alerts", (Line("monitor", reads.errors.get("alerts", "no alerts yet")),))
    return Panel(
        "Alerts",
        tuple(Line(f"tick {a.get('tick')} {a.get('kind')}", f"{a.get('subject')}: {a.get('detail')}") for a in rows),
    )


def build(reads: Reads, limits: Limits) -> list[Panel]:
    tick = _n((reads.clock or {}).get("tick")) if reads.clock else None
    return [
        clock_panel(reads),
        next_panel(reads),
        cash_panel(reads, limits),
        ledger_panel(reads, limits, tick),
        agents_panel(reads),
        caps_panel(reads),
        duels_panel(reads),
        ladder_panel(reads),
        market_panel(reads),
        alerts_panel(reads),
    ]


def render(panels: Sequence[Panel], now: datetime) -> list[str]:
    worst = next((s for s in ("bad", "warn") if any(p.status == s for p in panels)), "ok")
    out = [f"Team 1 cockpit · {now:%a %H:%M:%S} · overall {worst.upper()}"]
    for p in panels:
        out.append(f"\n[{MARK[p.status].strip() or '-'}] {p.title}")
        out += [f"  {MARK[line.status]}  {line.label}: {line.value}" for line in p.lines]
    return out


def as_dict(panels: Sequence[Panel]) -> list[dict[str, Any]]:
    return [
        {
            "title": p.title,
            "status": p.status,
            "lines": [{"label": x.label, "value": x.value, "status": x.status} for x in p.lines],
        }
        for p in panels
    ]


def read_each(reads: Reads, sources: Mapping[str, Callable[[], Any]]) -> Reads:
    """Fill `reads` source by source; a failure is kept as its error text, never raised."""
    for name, fetch in sources.items():
        try:
            value = fetch()
        except Exception as e:  # a cockpit shows what failed and keeps the rest
            reads.errors[name] = f"{type(e).__name__}: {e}"[:200]
            value = None
        if name.startswith("health:"):
            reads.health[name.split(":", 1)[1]] = value
        elif name == "alerts":
            reads.alerts = list(value or [])
        else:
            setattr(reads, name, value)
    return reads


# ---------------------------------------------------------------- the reads (I/O; everything above is pure)

LEDGER_WINDOW_TICKS = 120  # an hour of 30 s ticks: who wrote the shared ledger lately


def ledger_view(store: Any, tick: int, t_hours: float, writers: Mapping[str, int] | None = None) -> LedgerView:
    """The guardrails' own counters (`LedgerStore`), as the cockpit shows them."""
    shared = not str(store.where).startswith("file")
    return LedgerView(
        where=str(store.where),
        shared=shared,
        spent_last_hour=store.spent_since(t_hours - 1.0),
        packs_last_hour=sum(store.packs_since(t_hours - 1.0).values()),
        accepts_this_tick=store.accepts_in_tick(tick),
        listings_this_tick=store.count_in_tick("listing", tick),
        writers=dict(writers or {}),
    )


def read_ledger(data_dir: Any, tick: int, t_hours: float, connect: Callable[[], Any] | None = None) -> LedgerView:
    """The shared Postgres ledger, read-only (no schema DDL, unlike the writers' `connect_ready`), else the file."""
    from bazaar_agent.guardrails import Ledger
    from bazaar_agent.ledger_pg import PgLedger

    try:
        if connect is None:
            from bazaar_agent.pgconn import connect as pg_connect

            conn = pg_connect(app="bazaar-cockpit")
        else:
            conn = connect()
        conn.autocommit = True
        conn.execute("set default_transaction_read_only = on")
    except Exception:  # no Postgres here: what this machine's processes would use
        return ledger_view(Ledger(data_dir / "ledger.jsonl"), tick, t_hours)
    try:
        rows = conn.execute(
            "select coalesce(source, ''), max(tick) from ledger where tick >= %s group by source",
            (tick - LEDGER_WINDOW_TICKS,),
        ).fetchall()
        return ledger_view(PgLedger(conn, "cockpit"), tick, t_hours, {str(r[0]): int(r[1]) for r in rows})
    finally:
        conn.close()


def limits_from(rules: Any, paused_here: bool) -> Limits:
    return Limits(
        cash_floor=int(rules.cash_floor),
        venue_bond_reserve=int(getattr(rules, "venue_bond_reserve", 0) or 0),
        max_spend_per_game_hour=int(rules.max_spend_per_game_hour),
        max_packs_per_game_hour=int(rules.max_packs_per_game_hour),
        max_accepts_per_tick=int(rules.max_accepts_per_tick),
        trading_enabled=bool(rules.trading_enabled),
        paused_here=paused_here,
    )


def get_json(url: str, timeout: float = 3.0) -> Mapping[str, Any]:
    import httpx

    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, Mapping) else {}
