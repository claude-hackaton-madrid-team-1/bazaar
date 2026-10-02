"""`bazaar evals run | report | import-duels`: Team 1's online-outcome evals (questions/evals.json).

Postgres in, Postgres and Phoenix out. No command here calls the game API, so the evals add nothing
to the team key's 5 req/s budget. `run --every N` is the always-on loop of the `bazaar-evals` Railway
service: it scores again only when the game has moved (a new tick in Postgres), and backs off while
Postgres is unreachable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import psycopg
import typer
from rich.console import Console
from rich.markup import escape

from bazaar_agent import telemetry as tm
from bazaar_agent.config import load_settings

evals_app = typer.Typer(
    no_args_is_help=True, help="Evals: score settled duels, dealer deals, trades (Postgres + Phoenix)"
)
console = Console()
BACKOFF_MAX_S = 600.0


def register(app: typer.Typer) -> None:
    app.add_typer(evals_app, name="evals")


def _warn(message: str) -> None:
    console.print(f"[yellow]{escape(message)}[/yellow]")


def _connect() -> psycopg.Connection:
    from bazaar_agent import db

    return db.connect_ready("bazaar-evals")


def _team(conn: psycopg.Connection) -> str | None:
    """BAZAAR_TEAM_ID, else the cached id, else the newest /me snapshot in Postgres (never the API)."""
    from bazaar_agent.evals.inputs import team_from_snapshots
    from bazaar_agent.identity import resolve_team_id

    settings = load_settings()
    return resolve_team_id(settings.team_id, settings.data_dir, None) or team_from_snapshots(conn)


def _annotator(phoenix: bool) -> Any:
    from bazaar_agent.evals.phoenix import annotator_from

    if not phoenix:
        return None
    cfg = tm.tracing_config()
    return annotator_from(cfg.ui_url, cfg.api_key, cfg.project, _warn)


def _latest_tick(conn: psycopg.Connection) -> int | None:
    row = conn.execute("select max(tick) from feed_events").fetchone()
    conn.commit()
    return int(row[0]) if row and row[0] is not None else None


def _pass(conn: psycopg.Connection, since_tick: int | None, phoenix: bool, as_json: bool) -> None:
    from bazaar_agent.evals.run import run_once

    annotator = _annotator(phoenix)
    try:
        summary = run_once(conn, _team(conn), since_tick=since_tick, annotator=annotator, warn=_warn)
    finally:
        if annotator is not None:
            annotator.close()
    if as_json:
        console.print_json(json.dumps(summary.__dict__, default=list))
        return
    scored = ", ".join(f"{k} {v}" for k, v in sorted(summary.scored.items())) or "nothing settled yet"
    console.print(
        f"evals · team {summary.team or '?'} · scored {scored} · {summary.changed} new/changed in Postgres · "
        f"phoenix {summary.phoenix}: {summary.annotated} annotated, {summary.no_span} without a span"
    )
    for note in summary.notes:
        console.print(f"  [dim]{escape(note)}[/dim]")


def run_forever(every: float, step: Callable[[], None], sleep: Callable[[float], None] = time.sleep) -> None:
    """Call `step` every `every` s; while Postgres fails, back off (every × 2^n, at most 10 min)."""
    failures = 0
    while True:
        try:
            step()
            failures = 0
            wait = every
        except psycopg.Error as e:
            failures += 1
            wait = min(BACKOFF_MAX_S, every * 2 ** (failures - 1))
            _warn(f"evals: Postgres error ({type(e).__name__}), retrying in {wait:.0f} s")
        sleep(wait)


@evals_app.command("run")
def evals_run(
    since_tick: int | None = typer.Option(None, help="Score only what settled at or after this tick"),
    every: float = typer.Option(0.0, help="Keep running: look for new ticks every N seconds (0 = one pass)"),
    phoenix: bool = typer.Option(True, help="Attach outcomes to their Phoenix traces as annotations"),
    as_json: bool = typer.Option(False, "--json", help="The pass summary as JSON"),
) -> None:
    """Score every settled duel, dealer thread, team trade and Market Test; upsert into `outcomes`."""
    if every <= 0:
        with _connect() as conn:
            _pass(conn, since_tick, phoenix, as_json)
        return
    state: dict[str, Any] = {"conn": None, "seen": None}

    def step() -> None:
        """One pass when the game moved: a tick in Postgres that the last pass did not see."""
        conn = state["conn"]
        if conn is None or conn.closed:
            conn = state["conn"] = _connect()
        try:
            tick = _latest_tick(conn)
            if tick is None or tick != state["seen"]:
                _pass(conn, since_tick, phoenix, as_json)
                state["seen"] = tick
        except psycopg.Error:
            conn.close()
            state["conn"] = None
            raise

    console.print(f"evals: every {every:.0f} s, scoring when the game tick moves (Ctrl-C to stop)")
    run_forever(every, step)


@evals_app.command("report")
def evals_report(as_json: bool = typer.Option(False, "--json", help="Everything as JSON (the dashboard)")) -> None:
    """The scorecard per target and day, the dealer ladder, the worst 5 per target, Jev calibration."""
    from bazaar_agent.evals.report import build, render

    with _connect() as conn:
        report = build(conn)
    if as_json:
        print(json.dumps(report.as_dict(), default=str, indent=2))  # plain stdout: a pipe gets pure JSON
        return
    console.print(render(report))


@evals_app.command("import-duels")
def evals_import_duels(
    paths: Annotated[list[Path], typer.Argument(help="duels.jsonl logs from `bazaar duel run`")],
) -> None:
    """Load duel runner logs (`.local/duels/duels.jsonl`) into the Postgres `duels` table. No API call."""
    from bazaar_agent.duel_store import jsonl_duels, save_duels

    with _connect() as conn:
        total = 0
        for path in paths:
            if not path.is_file():
                _warn(f"{path}: no such file")
                continue
            n = sum(save_duels(conn, duels, tick) for tick, duels in jsonl_duels(path))
            console.print(f"{path}: {n} duel snapshot(s) upserted")
            total += n
    console.print(f"imported {total} duel snapshot(s); the newest per duel is kept")
