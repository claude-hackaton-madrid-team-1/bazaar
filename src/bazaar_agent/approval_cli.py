"""`bazaar approve` and `bazaar approvals`: human approvals for big trades (`approvals.py`, shared Postgres).

An approval lets every process trade one card on one side at a price at or above `human_approval_above`, up to
`--max` (buy) or down to `--min` (sell), for `--ttl-ticks`. The agents read approvals once per tick, so it applies
from their next tick. Every approve and revoke writes a `decisions` row (agent `guard`).
"""

from __future__ import annotations

import getpass

import psycopg
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent import approvals, pgconn
from bazaar_agent.breaker_cli import Connect, _open, _tick
from bazaar_agent.breakers import CONNECT_TIMEOUT_S

console = Console()
err_console = Console(stderr=True)
PENDING_TICKS = approvals.PENDING_TICKS


def _connect() -> psycopg.Connection:
    return pgconn.connect(app="bazaar-approval-cli", connect_timeout_s=CONNECT_TIMEOUT_S)


def _side(buy: bool, sell: bool) -> str:
    if buy == sell:
        err_console.print("say --buy or --sell (one of them)")
        raise typer.Exit(2)
    return "buy" if buy else "sell"


def approve_cmd(
    card: str,
    side: str,
    price: int | None,
    ttl_ticks: int,
    reason: str,
    by: str,
    tick: int | None,
    connect: Connect = _connect,
) -> None:
    if price is None:
        err_console.print("a buy needs --max P, a sell --min P")
        raise typer.Exit(2)
    if ttl_ticks <= 0:
        err_console.print("--ttl-ticks must be positive")
        raise typer.Exit(2)
    now = _tick(tick)
    with _open(connect) as conn:
        try:
            a = approvals.approve(conn, card, side, price, now + ttl_ticks, by, reason)
        except approvals.ApprovalError as e:
            err_console.print(escape(str(e)))
            raise typer.Exit(2) from e
        approvals.record(
            conn,
            "approval_granted",
            now,
            {
                "card": a.card,
                "side": side,
                "price": price,
                "until_tick": a.until_tick,
                "by": by,
                "reason": reason or "approved by hand",
            },
        )
    limit = f"up to {price}" if side == "buy" else f"down to {price}"
    console.print(f"approved: {a.card} {side} {limit} until tick {a.until_tick} (now {now}), by {escape(by)}")


def revoke_cmd(card: str, side: str, by: str, tick: int | None, connect: Connect = _connect) -> None:
    now = _tick(tick)
    with _open(connect) as conn:
        try:
            was = approvals.revoke(conn, card, side)
        except approvals.ApprovalError as e:
            err_console.print(escape(str(e)))
            raise typer.Exit(2) from e
        if was:
            approvals.record(
                conn,
                "approval_revoked",
                now,
                {"card": card.upper(), "side": side, "by": by, "reason": "revoked by hand"},
            )
    console.print(f"{card.upper()} {side}: {'revoked' if was else 'had no approval'} at tick {now}")


def list_cmd(tick: int | None, connect: Connect = _connect) -> None:
    now = _tick(tick)
    with _open(connect) as conn:
        active = approvals.active(conn, now)
        asked = approvals.pending(conn, now - PENDING_TICKS)
    covered = {(a.card, a.side) for a in active}
    table = Table("tick", "card", "side", "price", "counterparty", "official", "ours", "state", title="Asked")
    for r in asked:
        state = "approved" if (r.get("card"), r.get("side")) in covered else "[yellow]WAITING[/yellow]"
        table.add_row(
            *(
                escape(str(r.get(k) if r.get(k) is not None else ""))
                for k in ("tick", "card", "side", "price", "counterparty", "official_value", "our_value")
            ),
            state,
        )
    console.print(table)
    granted = Table("card", "side", "limit", "until tick", "by", "reason", title="Active approvals")
    for a in active:
        limit = f"max {a.max_price}" if a.side == "buy" else f"min {a.min_price}"
        granted.add_row(a.card, a.side, limit, str(a.until_tick), escape(a.by), escape(a.reason))
    console.print(granted)
    console.print(f"tick {now}. Approve: `uv run bazaar approve <card> --buy --max P` (or --sell --min P).")


def approve(
    card: str = typer.Argument(..., help="the card ref, e.g. LAV-09"),
    buy: bool = typer.Option(False, "--buy", help="approve buying it"),
    sell: bool = typer.Option(False, "--sell", help="approve selling it"),
    max_price: int | None = typer.Option(None, "--max", help="buy: the most to pay, fee included"),
    min_price: int | None = typer.Option(None, "--min", help="sell: the least to take"),
    ttl_ticks: int = typer.Option(approvals.DEFAULT_TTL_TICKS, "--ttl-ticks", help="ticks the approval lasts"),
    reason: str = typer.Option("", "--reason", help="why (stored with it)"),
    revoke: bool = typer.Option(False, "--revoke", help="remove the approval of CARD on that side"),
    by: str = typer.Option("", "--by", help="who approves (default: this machine's user)"),
    tick: int | None = typer.Option(None, "--tick", help="the game tick (default: the public clock)"),
) -> None:
    """Let the agents trade CARD at or above human_approval_above: --buy --max P, or --sell --min P."""
    side = _side(buy, sell)
    who = by or getpass.getuser()
    if revoke:
        revoke_cmd(card, side, who, tick)
        return
    approve_cmd(card, side, max_price if side == "buy" else min_price, ttl_ticks, reason, who, tick)


def approvals_list(
    tick: int | None = typer.Option(None, "--tick", help="the game tick (default: the public clock)"),
) -> None:
    """Big trades waiting for a human (the last 2 game hours) and the active approvals."""
    list_cmd(tick)
