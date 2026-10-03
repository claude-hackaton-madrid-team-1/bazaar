"""`bazaar learnings`: what the live-feed reader learned, and what `recall()` returns at a tick.

Reads the captured feed (Postgres `feed_events` when DATABASE_URL answers, else `.local/feed/`), runs
it through the deterministic reader and prints the learnings in force (`--all` for every one) and the
dealer blockers for our team. `--save` writes them to the shared `learnings` table. No team key and no
game call: the feed is what the monitor and the taker already captured.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.config import load_settings
from bazaar_agent.learn.blockers import blocks_for
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.reader import FeedReader, GameHour
from bazaar_agent.learn.store import LearningStore, matches

KIND_HELP = "Only these kinds (repeat): cooloff, quota, sold_out, blocker, fee_change, announcement, ..."
console = Console()
err_console = Console(stderr=True)
Event = dict[str, Any]


def register(app: typer.Typer) -> None:
    app.command("learnings")(learnings)


def _feed(conn: Any) -> list[Event]:
    """The captured feed: Postgres when it answers, else this machine's JSONL."""
    from bazaar_agent.agents.runtime import _row_event
    from bazaar_agent.feed import FeedStore

    if conn is not None:
        rows = conn.execute("select id, tick, type, actor, payload from feed_events order by id").fetchall()
        return [_row_event(r) for r in rows]
    return sorted(FeedStore(load_settings().feed_dir).events(), key=lambda e: int(e["id"]))


def hour_from(events: list[Event]) -> GameHour | None:
    """The game hour at the newest event that carries `t` (JSONL does; Postgres rows do not)."""
    timed = [e for e in events if isinstance(e.get("t"), int | float) and isinstance(e.get("tick"), int)]
    if not timed:
        return None
    newest = timed[-1]
    paced = [e for e in events if e.get("type") in ("clock.changed", "day.opened")]
    tick_seconds = float((paced[-1].get("payload") or {}).get("tick_seconds") or 60.0) if paced else 60.0
    return GameHour(int(newest["tick"]), float(newest["t"]), tick_seconds)


def _connect() -> Any:
    from bazaar_agent import db

    try:
        conn = db.connect(app="bazaar-learnings")
    except Exception as e:  # unreachable or unset: the JSONL capture still answers
        err_console.print(f"[yellow]Postgres unavailable ({type(e).__name__}): reading .local/feed[/yellow]")
        return None
    conn.autocommit = True
    return conn


def _table(rows: list[Learning], tick: int | None) -> Table:
    table = Table(title=f"learnings in force at tick {tick}" if tick is not None else "every learning")
    for col in ("tick", "subject", "kind", "until", "team", "conf", "text", "evidence"):
        table.add_column(col, overflow="fold")
    for lr in rows:
        table.add_row(
            str(lr.tick),
            f"{lr.subject_kind}:{lr.subject}",
            lr.kind,
            "-" if lr.until_tick is None else f"T{lr.until_tick}",
            lr.team or "all",
            f"{lr.confidence:.2f}",
            escape(lr.text),
            ",".join(map(str, lr.evidence[-3:])),
        )
    return table


def learnings(
    subject: str | None = typer.Option(None, help="Only this subject (dealer, venue or team id, organiser)"),
    kind: Annotated[list[str] | None, typer.Option("--kind", help=KIND_HELP)] = None,
    tick: int | None = typer.Option(None, help="Recall at this tick (default: the newest captured tick)"),
    every: bool = typer.Option(False, "--all", help="Every learning, expired ones included"),
    save: bool = typer.Option(False, help="Also write them to the shared learnings table (Postgres)"),
    limit: int = typer.Option(40, help="Rows to print"),
    as_json: bool = typer.Option(False, "--json", help="JSON instead of tables"),
) -> None:
    """What the live-feed reader learned from the captured feed, and the dealer blockers for us."""
    from bazaar_agent import db
    from bazaar_agent.identity import resolve_team_id

    settings = load_settings()
    us = resolve_team_id(settings.team_id, settings.data_dir, None)
    conn = _connect()
    events = _feed(conn)
    hour = hour_from(events)
    learned = FeedReader(us).read(events, hour)
    now = tick if tick is not None else max((int(e.get("tick") or 0) for e in events), default=0)
    if save:
        store = LearningStore((lambda: conn) if conn is not None else None, err_console.print, db.init_schema)
        store.record(learned)
        err_console.print(f"[dim]learnings: {len(learned)} upserted ({store.where})[/dim]")
    at = None if every else now
    kinds = set(kind or ()) or None
    rows = [lr for lr in learned if matches(lr, subject=subject, kinds=kinds, subject_kind=None, tick=at, team=None)]
    rows = sorted({lr.key(): lr for lr in rows}.values(), key=lambda lr: (-lr.tick, lr.key()))
    blocks = blocks_for(learned, us, now)
    if as_json:
        out = {
            "tick": now,
            "us": us,
            "learnings": [lr.model_dump() for lr in rows[:limit]],
            "blockers": blocks.describe(),
        }
        console.print_json(json.dumps(out, default=str))
        return
    console.print(f"{len(events)} feed events read · {len(learned)} learnings · us {us or 'unknown'}")
    console.print(_table(rows[:limit], at))
    lines = blocks.describe()
    console.print(f"dealer blockers for us at tick {now}: " + (escape("; ".join(lines)) if lines else "none"))
