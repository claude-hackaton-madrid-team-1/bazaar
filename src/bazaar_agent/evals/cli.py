"""`bazaar evals run | report | import-duels`: Team 1's online-outcome evals (questions/evals.json).

Postgres in, Postgres and Phoenix out. No command here uses the team key, so the evals add nothing to
its 5 req/s budget. `run --every-ticks N` is the always-on loop of the `bazaar-evals` Railway service.
Like every loop here it is driven by the game clock (tick discipline), read keyless from the public
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


@evals_app.command("score-sim")
def evals_score_sim(
    data: Annotated[Path, typer.Option(help="Calibration fixture")] = Path("tests/fixtures/evals/friday_score.json"),
    feed: Annotated[Path | None, typer.Option(help="Rebuild the dealer deals from a feed capture (JSONL)")] = None,
    fit: bool = typer.Option(False, help="Refit the level-2 weight on our official series first"),
    as_json: bool = typer.Option(False, "--json", help="Calibration and marginals as JSON"),
) -> None:
    """The board-formula model vs Friday's official numbers, and what one more dealer deal is worth. Offline."""
    from dataclasses import asdict, replace

    from rich.table import Table

    from bazaar_agent.evals import score_sim as ss

    d = ss.load_data(data, feed)
    model = ss.ScoreModel()
    if fit:
        w2, _ = ss.fit_level2_weight(d.deals, d.ours, d.team, model)
        model = replace(model, level_weights={**model.level_weights, 2: w2})
    cal = ss.calibrate(d.deals, d.board30, d.ours, d.team, model)
    last = max(d.ours)
    raw = ss.ladder_raw(d.deals, ss.snapshot_tick(last, model), model, ss.learned_ranges(d.deals), teams=[d.team])
    top = ss.top_mean(raw.values())
    marginals = ss.ladder_marginals(raw[d.team], top, model)
    if as_json:
        out = {
            "model": asdict(model),
            "level2_weight": cal.level2_weight,
            "ours": cal.ours,
            "rmse_ours": round(cal.rmse_ours, 3),
            "max_err_ours": round(cal.max_err_ours, 2),
            "board30": cal.board,
            "board30_mae": round(cal.board_mae, 2),
            "ladder_raw": {"ours": round(raw[d.team], 4), "top3_mean": round(top, 4), "tick": last},
            "marginals": [asdict(m) for m in marginals],
        }
        print(json.dumps(out, indent=2))
        return
    ours = Table(title=f"Our negotiating: official vs model (level-2 weight {cal.level2_weight:g})")
    for col in ("tick", "official", "model", "error"):
        ours.add_column(col, justify="right")
    for tick, official, modelled in cal.ours:
        if tick % model.refresh_ticks == 0 or tick in (min(d.ours), last):
            ours.add_row(str(tick), f"{official:.2f}", f"{modelled:.2f}", f"{modelled - official:+.2f}")
    console.print(ours)
    console.print(f"RMSE {cal.rmse_ours:.2f} over {len(cal.ours)} snapshots, worst {cal.max_err_ours:.2f}")
    board = Table(title="Public board at tick 30 (ladder only): official vs model")
    for col in ("team", "official", "model"):
        board.add_column(col, justify="right")
    for team, official, modelled in cal.board:
        board.add_row(team, f"{official:.2f}", f"{modelled:.2f}")
    console.print(board)
    console.print(f"board MAE {cal.board_mae:.2f} over {len(cal.board)} teams")
    table = Table(title=f"One more dealer deal, at tick {last}'s top-3 mean ({top:.3f}; ours {raw[d.team]:.3f})")
    for col in ("move", "raw +", "round points +"):
        table.add_column(col, justify="right")
    for m in marginals:
        table.add_row(m.move, f"{m.raw_delta:.3f}", f"{m.points:+.2f}")
    console.print(table)
