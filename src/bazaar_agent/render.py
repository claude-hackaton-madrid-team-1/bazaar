"""Rich tables for the CLI. Presentation only."""

from __future__ import annotations

from rich.table import Table

from bazaar_agent.ticks import Clock, action_budget_s


def clock_table(c: Clock) -> Table:
    t = Table(title=f"Clock · {c.round_name or ''}", show_header=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    rows = [
        ("tick", str(c.tick)),
        ("game hour", f"{c.t_hours:.2f}"),
        ("tick length", f"{c.tick_seconds:.0f} s"),
        ("next tick in", f"{c.next_tick_in:.1f} s"),
        ("action budget now", f"{action_budget_s(c):.1f} s"),
        ("doors / paused", f"{c.doors} / {c.paused}"),
        ("closes", c.closes or "-"),
        ("next opens", c.next_opens or "-"),
        ("accepts / tick", str(c.limits.accepts_per_team_per_tick)),
        ("messages / thread / tick", str(c.limits.messages_per_side_per_tick)),
        ("new listings / tick", str(c.limits.offers_per_team_per_tick)),
        (
            "open threads / offers",
            f"{c.limits.max_open_threads_per_team} / {c.limits.max_open_offers_per_team}",
        ),
    ]
    for k, v in rows:
        t.add_row(k, v)
    return t


def _n(v: float | int | None, fmt: str = "{:.0f}") -> str:
    return "-" if v is None else fmt.format(v)


def tape_table(prints: list, limit: int) -> Table:
    t = Table(title=f"Tape · last {min(limit, len(prints))} of {len(prints)} settlements")
    for col in ("tick", "buyer", "seller", "item", "kind", "items", "price", "fee", "venue/dealer"):
        t.add_column(col, justify="right" if col in ("tick", "items", "price", "fee") else "left")
    for p in prints[-limit:]:
        t.add_row(
            str(p.tick),
            p.buyer,
            p.seller,
            p.ref,
            p.kind,
            str(p.items),
            str(p.price),
            str(p.fee),
            p.persona or p.venue or "-",
        )
    return t


def curves_table(summaries: list) -> Table:
    t = Table(title="Dealer curves · every team's threads, rebuilt from the feed")
    for col in (
        "dealer",
        "item",
        "threads",
        "fills",
        "fill min",
        "fill med",
        "fill max",
        "open ask med",
        "final med",
        "steps→fill",
    ):
        t.add_column(col, justify="left" if col in ("dealer", "item") else "right")
    for s in summaries:
        t.add_row(
            s.dealer,
            s.item,
            str(s.threads),
            str(s.fills),
            _n(s.fill_min),
            _n(s.fill_median, "{:.1f}"),
            _n(s.fill_max),
            _n(s.opening_ask_median, "{:.1f}"),
            _n(s.final_median, "{:.1f}"),
            _n(s.steps_to_fill_median, "{:.1f}"),
        )
    return t


def threads_table(threads: list, limit: int) -> Table:
    t = Table(title=f"Dealer threads · last {min(limit, len(threads))}")
    for col in ("thread", "team", "dealer", "side", "item", "team prices", "dealer prices", "final", "fill"):
        t.add_column(col)
    for d in threads[-limit:]:
        t.add_row(
            str(d.thread),
            d.team,
            d.dealer,
            d.side,
            d.item,
            " ".join(map(str, d.team_prices)) or "-",
            " ".join(map(str, d.dealer_prices)) or "-",
            _n(d.final_price),
            _n(d.fill_price),
        )
    return t


def teams_table(flows: list) -> Table:
    t = Table(title="Competition · team flow from the public feed")
    for col in (
        "team",
        "dealer thr",
        "bids",
        "buys",
        "spent",
        "avg pack",
        "sells",
        "earned",
        "listings",
        "top set (×1.6?)",
        "set interest",
    ):
        t.add_column(col, justify="left" if col in ("team", "top set (×1.6?)", "set interest") else "right")
    for f in flows:
        interest = " ".join(f"{s}{n:+d}" for s, n in f.set_interest.most_common() if n)
        t.add_row(
            f.team,
            str(f.dealer_threads),
            str(f.bids),
            str(f.buys),
            str(f.spent),
            _n(f.avg_pack_price, "{:.1f}"),
            str(f.sells),
            str(f.earned),
            str(f.listings),
            f.top_set or "-",
            interest or "-",
        )
    return t


def book_table(lines: list, venue: str) -> Table:
    t = Table(title=f"Order book · {venue} · {len(lines)} lines")
    for col in ("card", "side", "price", "maker", "pseudonym", "offer", "expires"):
        t.add_column(col, justify="right" if col in ("price", "offer", "expires") else "left")
    for b in lines:
        style = "green" if b.side == "bid" else "red"
        t.add_row(
            b.card,
            f"[{style}]{b.side}[/{style}]",
            str(b.price),
            b.maker,
            b.pseudonym,
            str(b.offer_id),
            _n(b.expires_tick),
        )
    return t


def dealers_table(personas: list) -> Table:
    t = Table(title="Dealers")
    for col in ("id", "name", "status", "level", "traits", "sells", "deals/h"):
        t.add_column(col)
    for p in personas:
        traits = " ".join(f"{k[:5]}={v}" for k, v in (p.get("traits") or {}).items())
        menu = p.get("menu") or {}
        sells = "; ".join(
            f"{s.get('pack') or s.get('rarity')}@{s.get('list_price')}"
            + (f" (open {s['opening_ask']})" if s.get("opening_ask") else "")
            for s in menu.get("sells") or []
        )
        t.add_row(
            str(p.get("id")),
            str(p.get("name")),
            str(p.get("status")),
            _n(p.get("level")),
            traits,
            sells or "-",
            _n(menu.get("deals_per_team_per_hour")),
        )
    return t


def status_table(me: dict) -> Table:
    t = Table(title=f"{me.get('name', 'our team')} · status", show_header=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    score = me.get("score") or {}
    assets = me.get("assets") or []
    cards = [a for a in assets if a.get("kind") == "card"]
    packs = [a for a in assets if a.get("kind") == "pack"]
    for k, v in [
        ("cash", me.get("cash")),
        ("level", me.get("level")),
        ("cards / sealed packs", f"{len(cards)} / {len(packs)}"),
        ("collection value", me.get("collection_value")),
        ("score", score.get("score")),
        ("rank", score.get("rank")),
    ]:
        t.add_row(k, "-" if v is None else str(v))
    return t


def cards_table(me: dict) -> Table:
    t = Table(title="Our cards (your_value = what we lose by selling that copy)")
    for col in ("asset", "card", "name", "rarity", "serial", "your_value"):
        t.add_column(col, justify="right" if col in ("asset", "serial", "your_value") else "left")
    cards = sorted((a for a in me.get("assets") or [] if a.get("kind") == "card"), key=lambda a: str(a.get("ref")))
    for a in cards:
        t.add_row(
            str(a.get("id")),
            str(a.get("ref")),
            str(a.get("name")),
            str(a.get("rarity")),
            f"#{a.get('serial')}/{a.get('print_run')}",
            _n(a.get("your_value"), "{:.1f}"),
        )
    return t
