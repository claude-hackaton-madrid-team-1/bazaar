"""`bazaar breaker list|trip|reset`: the circuit breakers in the shared Postgres (`breakers.py`).

A trip stops one kind of write in every process at its next tick (the board is read once per tick); a reset lets
it through again. Both write a `decisions` row (agent `guard`), and a trip a `guard_trip` learning. The tick comes
from the public clock (no key), or `--tick`.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent import breakers, pgconn
from bazaar_agent.config import load_settings

breaker_app = typer.Typer(help="Circuit breakers: stop one kind of write in every process (shared Postgres).")
console = Console()
err_console = Console(stderr=True)

Connect = Callable[[], psycopg.Connection]


def _connect() -> psycopg.Connection:
    return pgconn.connect(app="bazaar-breaker-cli", connect_timeout_s=breakers.CONNECT_TIMEOUT_S)


def _tick(tick: int | None) -> int:
    if tick is not None:
        return tick
    from bazaar_agent.sdk import public_client

    try:
        return int(public_client(load_settings()).clock()["tick"])
    except Exception as e:  # noqa: BLE001 — the CLI says why and stops
        err_console.print(f"could not read the clock ({type(e).__name__}): pass --tick")
        raise typer.Exit(2) from e


def _open(connect: Connect) -> psycopg.Connection:
    try:
        return connect()
    except Exception as e:  # noqa: BLE001
        err_console.print(f"Postgres unreachable: {escape(pgconn.redact(str(e)))}")
        raise typer.Exit(2) from e


def _scope(scope: str) -> str:
    try:
        return breakers.known_scope(scope)
    except breakers.BreakerError as e:
        err_console.print(escape(str(e)))
        raise typer.Exit(2) from e


def list_cmd(connect: Connect = _connect) -> None:
    with _open(connect) as conn:
        rows = {r.scope: r for r in breakers.rows(conn)}
    table = Table("scope", "state", "since tick", "until tick", "by", "reason")
    for scope in breakers.SCOPES:
        r = rows.get(scope)
        if r is None or not r.tripped:
            table.add_row(scope, "ok", "", "", "", "")
            continue
        table.add_row(
            scope,
            "[red]TRIPPED[/red]",
            str(r.tick if r.tick is not None else ""),
            str(r.until_tick if r.until_tick is not None else "until reset"),
            escape(r.source),
            escape(r.reason),
        )
    console.print(table)
    console.print("A timed trip lapses by itself at its until tick; the board is read once per tick per process.")


def trip_cmd(scope: str, reason: str, tick: int | None, connect: Connect = _connect) -> None:
    scope = _scope(scope)
    if not reason.strip():
        err_console.print("a trip needs --reason")
        raise typer.Exit(2)
    now = _tick(tick)
    with _open(connect) as conn:
        new = breakers.trip_and_record(conn, scope, reason, now, source="manual")
    console.print(f"breaker {scope}: {'TRIPPED' if new else 'already tripped (unchanged)'} at tick {now}")


def reset_cmd(scope: str, tick: int | None, connect: Connect = _connect) -> None:
    scope = _scope(scope)
    now = _tick(tick)
    with _open(connect) as conn:
        was = breakers.reset(conn, scope, now, source="manual")
        if was:
            breakers.record(conn, "breaker_reset", scope, "reset by hand", now)
    console.print(f"breaker {scope}: {'reset' if was else 'was not tripped'} at tick {now}")


@breaker_app.command("list")
def breaker_list() -> None:
    """Every scope and whether it is tripped."""
    list_cmd()


@breaker_app.command("trip")
def breaker_trip(
    scope: str = typer.Argument(..., help=f"one of {', '.join(breakers.SCOPES)}"),
    reason: str = typer.Option(..., "--reason", help="why (stored and shown to every process's log)"),
    tick: int | None = typer.Option(None, "--tick", help="the game tick (default: the public clock)"),
) -> None:
    """Stop every write of SCOPE in every process (until `breaker reset`)."""
    trip_cmd(scope, reason, tick)


@breaker_app.command("reset")
def breaker_reset(
    scope: str = typer.Argument(..., help=f"one of {', '.join(breakers.SCOPES)}"),
    tick: int | None = typer.Option(None, "--tick", help="the game tick (default: the public clock)"),
) -> None:
    """Let SCOPE's writes through again."""
    reset_cmd(scope, tick)
