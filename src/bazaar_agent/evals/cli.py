"""`bazaar evals run | report | import-duels`: Team 1's online-outcome evals (questions/evals.json).

Postgres in, Postgres and Phoenix out. No command here uses the team key, so the evals add nothing to
its 5 req/s budget. The agents run the evals themselves (evals/inline.py); `run --every-ticks N` is the
laptop loop. Like every loop here it is driven by the game clock (tick discipline), read keyless from the public
`/api/clock` (the shared `ticks.run_per_tick`: doors closed = no pass, clock errors back off). Every N
ticks it scores again when an input moved (a tick, duel, snapshot or decision in Postgres, or an outcome
still waiting for its Phoenix span); a Postgres outage is retried at the next due tick.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import psycopg
import typer
from rich.console import Console
from rich.markup import escape

from bazaar_agent import telemetry as tm
from bazaar_agent.config import load_settings
from bazaar_agent.ticks import Clock

evals_app = typer.Typer(
    no_args_is_help=True, help="Evals: score settled duels, dealer deals, trades (Postgres + Phoenix)"
)
console = Console()
err_console = Console(stderr=True)  # warnings: `--json` keeps stdout pure JSON


def register(app: typer.Typer) -> None:
    app.add_typer(evals_app, name="evals")


def _warn(message: str) -> None:
    err_console.print(f"[yellow]{escape(message)}[/yellow]")


def _connect() -> psycopg.Connection:
    from bazaar_agent import db

    return db.connect_ready("bazaar-evals")


def team_id(conn: psycopg.Connection) -> str | None:
    """BAZAAR_TEAM_ID, else the cached id, else the newest /me snapshot in Postgres (never the API)."""
    from bazaar_agent.evals.inputs import team_from_snapshots
    from bazaar_agent.identity import resolve_team_id

    settings = load_settings()
    return resolve_team_id(settings.team_id, settings.data_dir, None) or team_from_snapshots(conn)


def _annotator(phoenix: bool) -> Any:
    from bazaar_agent.evals.phoenix import annotator_from

    if not phoenix:
        return None
    if load_settings().simulator:  # simulated duel ids collide with real ones: never annotate the real traces
        _warn("phoenix: simulator, scores stay in Postgres only")
        return None
    cfg = tm.tracing_config()
    return annotator_from(cfg.ui_url, cfg.api_key, cfg.project, _warn)


_INPUTS_STATE = (
    "select (select max(tick) from feed_events), (select max(updated_at) from duels), "
    "(select max(tick) from snapshots), (select max(id) from decisions)"
)
_PENDING = (
    "select count(*) from outcomes where target is not null and annotated_at is null "
    "and coalesce(annotation_tries, 0) < %s"
)


def _inputs_state(conn: psycopg.Connection) -> tuple[Any, ...]:
    """What a pass reads: the newest game tick, a duel written by `duel done` or `import-duels`, a /me
    snapshot, a decision. Unchanged since the last pass = nothing new to score."""
    row = conn.execute(_INPUTS_STATE).fetchone()
    conn.commit()
    return tuple(row) if row else ()


def _pending(conn: psycopg.Connection) -> int:
    """Outcomes still waiting for their Phoenix span (each is retried at most MAX_ANNOTATION_TRIES times)."""
    from bazaar_agent.evals.store import MAX_ANNOTATION_TRIES

    row = conn.execute(_PENDING, (MAX_ANNOTATION_TRIES,)).fetchone()
    conn.commit()
    return int(row[0]) if row else 0


def _held_targets(conn: psycopg.Connection) -> set[str]:
    """The targets of every agent kind whose evals lock this session could take (the others are being
    scored by that agent right now: two writers would double-count Phoenix misses)."""
    from bazaar_agent.evals.inline import TARGETS_BY_AGENT, lock_key

    held: set[str] = set()
    for agent, targets in TARGETS_BY_AGENT.items():
        row = conn.execute("select pg_try_advisory_lock(hashtext(%s))", (lock_key(agent),)).fetchone()
        if row and row[0]:
            held |= targets
        else:
            _warn(f"evals: the {agent} agent is scoring {', '.join(sorted(targets))} right now; skipped here")
    conn.commit()
    return held


def _pass(conn: psycopg.Connection, since_tick: int | None, phoenix: bool, as_json: bool) -> None:
    from bazaar_agent.evals.inline import IDLE_SESSION_TIMEOUT
    from bazaar_agent.evals.run import run_once

    # Like the agents: a laptop that sleeps mid-pass loses its session, and the locks with it.
    conn.execute(f"set idle_session_timeout = '{IDLE_SESSION_TIMEOUT}'")
    conn.execute(f"set idle_in_transaction_session_timeout = '{IDLE_SESSION_TIMEOUT}'")
    conn.commit()
    annotator = None
    try:
        targets = _held_targets(conn)
        if not targets:
            _warn("evals: every agent kind is scoring right now (their locks are held); nothing to do")
            return
        annotator = _annotator(phoenix)
        summary = run_once(conn, team_id(conn), since_tick=since_tick, annotator=annotator, warn=_warn, targets=targets)
    finally:
        if annotator is not None:
            annotator.close()
        conn.execute("select pg_advisory_unlock_all()")
        conn.execute("reset idle_session_timeout")  # the --every-ticks connection idles between passes
        conn.execute("reset idle_in_transaction_session_timeout")
        conn.commit()
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


class TickGate:
    """The always-on loop's step, driven by the game clock: every `every_ticks` ticks, one pass when an
    input moved since the last one (or an outcome still waits for its Phoenix span). A Postgres error
    drops the connection and is retried at the next due tick, so an outage costs one attempt per window."""

    def __init__(self, every_ticks: int, run_pass: Callable[[psycopg.Connection], None], phoenix: bool) -> None:
        self.every_ticks, self.run_pass, self.phoenix = every_ticks, run_pass, phoenix
        self.conn: psycopg.Connection | None = None
        self.seen: tuple[Any, ...] | None = None
        self.last_tick: int | None = None

    def __call__(self, clock: Clock) -> None:
        if self.last_tick is not None and clock.tick - self.last_tick < self.every_ticks:
            return
        self.last_tick = clock.tick
        if self.conn is None or self.conn.closed:
            self.conn = _connect()
        try:
            seen = _inputs_state(self.conn)
            if seen != self.seen or (self.phoenix and _pending(self.conn) > 0):
                self.run_pass(self.conn)
                self.seen = seen
        except psycopg.Error:
            self.conn.close()
            self.conn = None
            raise


@evals_app.command("run")
def evals_run(
    since_tick: int | None = typer.Option(None, help="Score only what settled at or after this tick"),
    every_ticks: int = typer.Option(
        0, min=0, help="Keep running on the game clock: a pass every N ticks (keyless /api/clock; 0 = one pass)"
    ),
    phoenix: bool = typer.Option(True, help="Attach outcomes to their Phoenix traces as annotations"),
    as_json: bool = typer.Option(False, "--json", help="The pass summary as JSON"),
) -> None:
    """Score every settled duel, dealer thread, team trade and Market Test; upsert into `outcomes`."""
    if every_ticks <= 0:
        with _connect() as conn:
            _pass(conn, since_tick, phoenix, as_json)
        return
    from bazaar_agent.sdk import public_client
    from bazaar_agent.ticks import run_per_tick

    gate = TickGate(every_ticks, lambda conn: _pass(conn, since_tick, phoenix, as_json), phoenix)
    console.print(f"evals: every {every_ticks} game ticks (keyless /api/clock), when an input moved (Ctrl-C to stop)")
    run_per_tick(public_client(load_settings()).clock, gate)


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
