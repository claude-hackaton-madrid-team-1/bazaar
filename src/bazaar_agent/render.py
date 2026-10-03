"""Rich tables for the CLI. Presentation only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text

from bazaar_agent.conversation import Thread, lines, topic_ref
from bazaar_agent.ticks import Clock, action_budget_s

if TYPE_CHECKING:
    from bazaar_agent.pages import Scenario


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


def curves_table(summaries: list, title: str = "every team's threads") -> Table:
    t = Table(title=f"Dealer curves · {title}, rebuilt from the feed")
    for col in (
        "dealer",
        "item",
        "threads",
        "ours",
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
            str(s.ours) if s.ours else "-",
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
    for col in ("thread", "team", "ours", "dealer", "side", "item", "team prices", "dealer prices", "final", "fill"):
        t.add_column(col)
    for d in threads[-limit:]:
        t.add_row(
            str(d.thread),
            d.team,
            "[bold]us[/bold]" if d.ours else "",
            d.dealer,
            d.side,
            d.item,
            " ".join(map(str, d.team_prices)) or "-",
            " ".join(map(str, d.dealer_prices)) or "-",
            _n(d.final_price),
            _n(d.fill_price),
        )
    return t


def affinity_table(amap: Any, title: str = "Rival affinity map · P(set holds the team's top multiplier)") -> Table:
    t = Table(title=title)
    sets = next(iter(amap.teams.values())).sets if amap.teams else ()
    for col in ("team", "signals", "top set", "P", "runner-up", *sets):
        t.add_column(col, justify="left" if col in ("team", "top set", "runner-up") else "right")
    for a in amap.teams.values():
        ranked = [s for s in sorted(a.p_top, key=lambda s: -a.p_top[s]) if s != a.top_set]
        second = ranked[0] if ranked else "-"
        t.add_row(
            a.team,
            str(a.signals),
            a.top_set if a.signals else "- (no signal)",
            f"{a.confidence:.2f}",
            f"{second} {a.p_top.get(second, 0):.2f}" if second != "-" else "-",
            *(f"{a.p_top[s]:.2f}" for s in sets),
        )
    return t


def teams_table(
    flows: list, title: str = "Competition · team flow from the public feed", us: str | None = None
) -> Table:
    t = Table(title=title)
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
            f"{f.team} [bold](us)[/bold]" if us and f.team == us else f.team,
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


def book_table(lines: list, venue: str, title: str = "Order book", us: str | None = None) -> Table:
    t = Table(title=f"{title} · {venue} · {len(lines)} lines")
    for col in ("card", "side", "price", "maker", "pseudonym", "offer", "expires"):
        t.add_column(col, justify="right" if col in ("price", "offer", "expires") else "left")
    for b in lines:
        style = "green" if b.side == "bid" else "red"
        t.add_row(
            b.card,
            f"[{style}]{b.side}[/{style}]",
            str(b.price),
            f"{b.maker} [bold](us)[/bold]" if us and b.maker == us else b.maker,
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


def status_table(me: dict, target: str | None = None) -> Table:
    t = Table(title=f"{me.get('name', 'our team')} · status", show_header=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    score = me.get("score") or {}
    assets = me.get("assets") or []
    cards = [a for a in assets if a.get("kind") == "card"]
    packs = [a for a in assets if a.get("kind") == "pack"]
    if target:
        t.add_row("target", target.removeprefix("target: "))
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


def album_table(pages: list) -> Table:
    t = Table(title="Album · from /api/me (check before every buy or sell)")
    for col in ("set", "affinity", "page", "missing page cards (worth to us)", "duplicates"):
        t.add_column(col, justify="right" if col in ("affinity", "page") else "left")
    for p in pages:
        missing = ", ".join(f"{m.ref} {m.rarity[:1].upper()} {m.value_to_us:.0f}" for m in p.missing) or "complete"
        t.add_row(
            f"{p.set_code} {p.name}", f"×{p.affinity}", f"{p.have}/{p.of}", missing, ", ".join(p.duplicates) or "-"
        )
    return t


def threads_list_table(threads: list[Thread]) -> Table:
    t = Table(title=f"Our threads · {len(threads)} (GET /api/me/threads; `bazaar thread <id>` for one)")
    for col in ("thread", "with", "item", "status", "msgs", "last message"):
        t.add_column(col, justify="right" if col in ("thread", "msgs") else "left")
    for th in threads:
        last = lines(th)[-1] if th.messages else None
        status = f"{th.status} ({th.closed_reason})" if th.closed_reason else th.status
        said = "-"
        if last is not None:
            price = f" [{last.price}]" if last.price is not None else ""
            said = f"t{_n(last.tick)} {last.sender}{price}: {last.text[:48]}"
        # their words are untrusted: Text() shows them, never parses them as markup
        t.add_row(str(th.id), th.with_ or "-", topic_ref(th.topic) or "-", status, str(len(th.messages)), Text(said))
    return t


def thread_table(th: Thread) -> Table:
    closed = f" ({th.closed_reason})" if th.closed_reason else ""
    title = f"Thread {th.id} · {th.with_ or '-'} · {topic_ref(th.topic) or '-'} {th.item or ''} · {th.status}{closed}"
    t = Table(title=title, caption=_standing(th))
    for col in ("msg", "tick", "sender", "text", "price", "final", "offer", "offer status"):
        t.add_column(col, justify="right" if col in ("msg", "tick", "price", "offer") else "left")
    for line in lines(th):
        style = "cyan" if line.sender == th.with_ else "green"
        t.add_row(
            _n(line.id),
            _n(line.tick),
            Text(line.sender, style=style),
            Text(line.text),  # their words are untrusted: never parsed as markup
            _n(line.price),
            "FINAL" if line.final else "",
            _n(line.offer_id),
            line.offer_status or "-",
        )
    return t


def _standing(th: Thread) -> str:
    offers = [f"#{o.id} {o.maker} {o.price}{' FINAL' if o.final else ''}" for o in th.standing_offers]
    return "standing offers: " + (", ".join(offers) if offers else "none")


def obs_table(enabled: bool, endpoint: str, ui_url: str, project: str, has_key: bool, health: str) -> Table:
    t = Table(title="Observability · OpenTelemetry → Arize Phoenix", show_header=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    state = "[green]ON[/green]" if enabled else "[yellow]off[/yellow] (export BAZAAR_TRACING=1, or add it to .env)"
    for k, v in [
        ("tracing", state),
        ("spans go to", endpoint),
        ("Phoenix UI", ui_url),
        ("project", project),
        ("PHOENIX_API_KEY", "set (sent as a bearer token)" if has_key else "not set (local Phoenix needs none)"),
        ("Phoenix", health),
    ]:
        t.add_row(k, v)
    return t


# ---------------------------------------------------------------- strategy playbook and offers


def scarce_supply_table(supply: list) -> Table:
    rows = sorted((s for s in supply if s.scarce), key=lambda s: (s.minted, s.ref))
    t = Table(title=f"Scarce supply · {len(rows)} cards at or below scarce_minted_max copies (supply is finite)")
    for col in ("card", "rarity", "minted", "print run", "ours", "availability"):
        t.add_column(col, justify="right" if col in ("minted", "print run", "ours") else "left")
    for s in rows:
        t.add_row(s.ref, s.rarity, str(s.minted), str(s.print_run), str(s.ours), s.availability)
    return t


def moves_table(title: str, moves: list) -> Table:
    t = Table(title=title)
    numbers = ("#", "value", "price", "surplus", "urgency", "score")
    for col in (*numbers[:1], "card", "strategy", *numbers[1:], "jev", "guardrails"):
        t.add_column(col, justify="right" if col in numbers else "left", overflow="fold")
    t.add_column("why", overflow="fold")
    for i, m in enumerate(moves, start=1):
        t.add_row(
            str(i),
            f"{m.ref} {m.rarity[:1].upper()}",
            m.strategy,
            f"{m.value:.1f}",
            f"{m.price:g}",
            f"{m.surplus:+.1f}",
            f"{m.urgency:.2f}",
            f"{m.score:.1f}",
            m.jev,
            m.guardrail,
            m.reason,
        )
    return t


def move_commands(moves: list) -> list[str]:
    """One plain line per move, so a command copies whole (a table cell would wrap it)."""
    lines = []
    for i, m in enumerate(moves, start=1):
        why_not = m.reason.rsplit("; ", 1)[-1]
        lines.append(f"  #{i} {m.command or f'- (no command: {why_not})'}")
    return lines


def params_table(lines: list) -> Table:
    t = Table(title="Strategy parameters · STRATEGY.md (edit it, then rerun `bazaar strategy`)")
    for col in ("param", "value", "why", "line"):
        t.add_column(col, justify="right" if col == "line" else "left")
    for r in lines:
        t.add_row(r.rule_id, r.raw_value, r.why, str(r.line))
    return t


def offers_table(offers: list, label: str = "offers") -> Table:
    from bazaar_agent.agents.seller import offer_side

    t = Table(title=f"Our {label} · {len(offers)} (cancel ours with `bazaar sell cancel <id>`)")
    for col in ("id", "status", "venue", "maker", "to", "give", "want", "expires"):
        t.add_column(col, justify="right" if col in ("id", "expires") else "left")
    for o in offers:
        t.add_row(
            str(o.get("id")),
            str(o.get("status") or "-"),
            str(o.get("venue") or "-"),
            str(o.get("maker") or "-"),
            str(o.get("to") or "-"),
            offer_side(o.get("give")),
            offer_side(o.get("want")),
            _n(o.get("expires_tick")),
        )
    return t


# ---------------------------------------------------------------- page economics (`bazaar plan pages`)


def pages_table(pages: list) -> Table:
    t = Table(title="Pages · what finishing each is worth and costs (private value, primas)")
    numbers = ("affinity", "have", "bonus", "cost", "value", "surplus")
    for col in ("page", *numbers, "verdict"):
        t.add_column(col, justify="right" if col in numbers else "left")
    t.add_column("why", overflow="fold")
    for p in pages:
        cost = p.cost if p.cost is not None else p.cost_if_unblocked
        t.add_row(
            p.set_code,
            f"×{p.affinity:g}",
            f"{p.have}/{p.of}",
            f"{p.bonus:.0f}",
            "-" if cost is None else f"{cost:.0f}",
            f"{p.value:.0f}",
            "-" if p.surplus is None else f"{p.surplus:+.0f}",
            p.verdict,
            p.why,
        )
    return t


def page_cards_table(pages: list) -> Table:
    t = Table(title="Missing page cards · by source (ladder = dealer deal, trade = team on a venue)")
    numbers = ("value", "+bonus", "minted≥", "price", "max")
    for col in ("card", *numbers[:3], "source", "channel", *numbers[3:]):
        t.add_column(col, justify="right" if col in numbers else "left")
    t.add_column("basis · blocked · who", overflow="fold")
    for p in pages:
        for c in p.missing:
            for i, s in enumerate(c.sources or [None]):
                head = (f"{c.ref} {c.rarity[:1].upper()}", f"{c.value:.0f}", f"{c.bonus_share:.0f}", str(c.minted))
                first = head if i == 0 else ("", "", "", "")
                if s is None:
                    t.add_row(*first, "-", "-", "-", "-", "no source: none minted beyond the holders' own")
                    continue
                why = " · ".join(x for x in (s.basis, s.blocked or "", ", ".join(s.sellers), s.note) if x)
                t.add_row(*first, s.source, s.channel, f"{s.price:g}", str(s.max_price), why)
    return t


def buy_order_table(wants: list) -> Table:
    t = Table(title="Buy order · dealer legs first, the card that completes a page from a team last")
    numbers = ("#", "price", "max", "trade surplus")
    for col in ("#", "card", "page", "source", "channel", *numbers[1:]):
        t.add_column(col, justify="right" if col in numbers else "left")
    t.add_column("sellers", overflow="fold")
    for i, w in enumerate(wants, start=1):
        gain = w.trade_surplus
        t.add_row(
            str(i),
            w.card.ref + (" (completes)" if w.completes else ""),
            w.page,
            w.source.source,
            w.source.channel,
            f"{w.source.price:g}",
            str(w.source.max_price),
            "ladder share" if gain is None else f"{gain:+.0f}",
            ", ".join(w.source.sellers),
        )
    return t


def scenarios_table(scenarios: list) -> Table:
    t = Table(title="Cash plan · venue scenarios under GUARDRAILS.md (what-ifs say so)")
    numbers = ("floor", "venue", "dealer deals", "page cards", "trade surplus", "end cash")
    for col in ("scenario", *numbers):
        t.add_column(col, justify="right" if col == "floor" or col in numbers else "left")
    t.add_column("held", overflow="fold")
    for s in scenarios:
        venue = "-" if s.venue_hour is None else (f"h{s.venue_opened}" if s.venue_opened is not None else "refused")
        cards = list(s.bought)
        held = [x for x in s.held if not x.startswith("ladder")]
        extra = ", ".join(
            x
            for x in (
                f"{s.ladder_held} held" if s.ladder_held else "",
                f"{s.ladder_duplicates} dup" if s.ladder_duplicates else "",
            )
            if x
        )
        t.add_row(
            s.name,
            str(s.floor),
            venue,
            str(s.dealer_deals) + (f" ({extra})" if extra else ""),
            str(len(cards)),
            f"{s.trade_surplus:.0f}",
            f"{s.end_cash:.0f}",
            ", ".join(held) or "-",
        )
    return t


def scenario_steps(scenario: Scenario) -> list[str]:
    """One plain line per step of a scenario: hour, what, from whom, cash out, max price, cash after, why."""
    out = [f"{scenario.name} (floor {scenario.floor}):"]
    for s in scenario.steps:
        cap = f" max {s.max_price}" if s.max_price is not None else ""
        what = f"h{s.hour:<2} {s.kind:<6} {s.item:<20} {s.source:<10}"
        out.append(f"  {what} {s.amount:>7.1f}{cap:>8} → {s.cash_after:>6.1f}  {s.note}")
    return out
