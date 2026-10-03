"""`bazaar ladder`: the dealer floor table and the ladder plan for a window. Offline: no key, no writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bazaar_agent.config import REPO_ROOT, load_settings
from bazaar_agent.feed import FeedStore, load_events
from bazaar_agent.ladder import Conversation, conversations, floor_table, from_rows
from bazaar_agent.ladder_plan import DEFAULT_QUOTAS, Grant, Window, plan_document, quotas_from_dealers

app = typer.Typer(no_args_is_help=True, help="Ladder maximiser: dealer floors and the plan for a window (offline)")
console = Console()

FRIDAY = REPO_ROOT / "tests" / "fixtures" / "evals" / "dealer_threads.json"  # every team's threads, Fri t 0–159
SOURCE_HELP = "auto: the captured feed when it holds 50+ dealer threads, else the Friday snapshot; or feed | fixture"


def _conversations(source: str) -> tuple[list[Conversation], str]:
    if source in ("auto", "feed"):
        convs = conversations(load_events(FeedStore(load_settings().feed_dir)))
        if source == "feed" or len(convs) >= 50:
            return convs, f"captured feed ({len(convs)} dealer threads)"
    rows = json.loads(FRIDAY.read_text())["rows"]
    return from_rows(rows), f"Friday snapshot {FRIDAY.relative_to(REPO_ROOT)} ({len(rows)} dealer threads)"


def _caps(what_if: list[str]) -> dict[tuple[str, str], int]:
    out = {}
    for spec in what_if:
        try:
            target, value = spec.rsplit("=", 1)
            dealer, cls = target.split(":", 1)
            out[(dealer, cls)] = int(value)
        except ValueError as e:
            raise typer.BadParameter(
                f"--what-if-cap {spec!r}: use dealer:class=price, e.g. chato:card:uncommon=31"
            ) from e
    return out


def _quotas(path: Path) -> dict[str, Any]:
    """A saved `GET /api/dealers` (the body, or a fixture with the body under "body") → quotas."""
    try:
        body: Any = json.loads(path.read_text())
        body = body.get("body", body) if isinstance(body, dict) else body
        dealers = body if isinstance(body, list) else body.get("dealers") or body.get("personas") or []
        if not all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in dealers):
            raise ValueError("every dealer needs a string id")
        return quotas_from_dealers(dealers)
    except (OSError, ValueError, TypeError, AttributeError) as e:
        raise typer.BadParameter(f"--dealers {path}: not a GET /api/dealers body ({e})") from e


@app.command("floors")
def floors(
    source: str = typer.Option("auto", help=SOURCE_HELP),
    since_tick: int = typer.Option(0, help="Ignore threads opened before this tick"),
) -> None:
    """Each dealer × price class × opening ask: the limits its conversations closed at (every team)."""
    convs, label = _conversations(source)
    table = Table(title=f"Dealer floors · {label}")
    for col in ("dealer", "class", "open", "threads", "closed", "p10", "p25", "p50", "p75", "p90", "patience"):
        table.add_column(col, justify="left" if col in ("dealer", "class") else "right")
    for r in floor_table(convs, since_tick=since_tick):
        d = r.as_dict()
        table.add_row(
            r.dealer,
            r.price_class,
            str(r.opening),
            str(r.conversations),
            str(r.closed),
            *(str(d[f"floor_p{q}"]) if d[f"floor_p{q}"] is not None else "-" for q in (10, 25, 50, 75, 90)),
            f"{r.patience:g}" if r.patience is not None else "-",
        )
    console.print(table)


@app.command("plan")
def plan(
    cash: int = typer.Option(..., help="Our cash when the window opens (GET /api/me)"),
    out: str = typer.Option("ladder_plan.json", help="Where to write the plan"),
    source: str = typer.Option("auto", help=SOURCE_HELP),
    wall_start: str = typer.Option("09:00", help="Madrid time of the window's first tick"),
    t_start: float = typer.Option(4.0, help="Game hours at the window's first tick (Saturday 09:00 = 4.0)"),
    minutes: int = typer.Option(90, help="Window length"),
    tick_seconds: float = typer.Option(30.0, help="Seconds per tick (Saturday 30, Sunday 15)"),
    grant: int = typer.Option(150, help="Cash granted to every team during the window (0: none)"),
    grant_tick: int = typer.Option(6, help="Window tick when the grant lands (Saturday: t 4.05 = tick 6)"),
    refs: str = typer.Option("", help="Card refs to buy, in priority order (e.g. from `bazaar strategy`)"),
    dealers_json: str | None = typer.Option(None, "--dealers", help="A saved GET /api/dealers body for quotas"),
    what_if_cap: list[str] | None = typer.Option(  # noqa: B008 (typer reads its options from defaults)
        None, help="What-if only (GUARDRAILS.md still binds): dealer:class=price, e.g. chato:card:uncommon=31"
    ),
    runs: int = typer.Option(2000, help="Backtest conversations per price class"),
    since_tick: int = typer.Option(0, help="Fit on threads opened at or after this tick (e.g. Saturday only)"),
) -> None:
    """Write ladder_plan.json: floors, a BidPlan and backtest per class, and the window's schedule."""
    from bazaar_agent.evals.dealers import card_rarity
    from bazaar_agent.guardrails import load_guardrails

    convs, label = _conversations(source)
    convs = [c for c in convs if c.opened_tick >= since_tick]
    label += f", threads opened at tick {since_tick} or later" if since_tick else ""
    quotas = DEFAULT_QUOTAS
    if dealers_json is not None:
        quotas = _quotas(Path(dealers_json))
    ref_list = [(r, card_rarity(r) or "") for r in refs.split(",") if r.strip()]
    doc = plan_document(
        convs,
        floor_table(convs),
        load_guardrails().rules,
        cash=cash,
        grants=(Grant(grant_tick, grant),) if grant else (),
        window=Window(t_start, minutes, tick_seconds, wall_start),
        quotas=quotas,
        refs=ref_list,
        caps=_caps(what_if_cap or []) or None,
        source=label,
        runs=runs,
    )
    Path(out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    table = Table(title=f"Ladder plan {wall_start} +{minutes} min · {label}")
    for col in ("wall", "dealer", "class", "ref", "plan", "share", "price"):
        table.add_column(col)
    for s in doc["schedule"]:
        p, e = s["plan"], s["expected"]
        table.add_row(
            s["wall"],
            s["dealer"],
            s["price_class"],
            s["ref"] or "-",
            f"{p['start']}→{p['max']}",
            str(e["share"]),
            str(e["price"]),
        )
    console.print(table)
    for b in doc["blocked"]:
        console.print(f"[yellow]blocked[/yellow] {b['dealer']} {b['price_class']}: {b['why']}")
    for n in doc["notes"]:
        console.print(f"[dim]{n}[/dim]")
    console.print(f"wrote {out}")
