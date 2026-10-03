"""`bazaar injections`: the prompt-injection attempts we recorded, with their proofs (`injection_log.py`).

`--backfill` scans what we already stored (feed_events, our threads and messages, the duels) and inserts what it
finds: read-only on the game, no request is sent. Hostile words are printed with every control or invisible
character shown as ⟨U+XXXX⟩ and rich markup escaped, so a terminal never runs them; `--json` is ASCII-escaped.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from typing import Any

import psycopg
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from bazaar_agent import injection_log as il
from bazaar_agent import pgconn
from bazaar_agent.breaker_cli import Connect, _open
from bazaar_agent.breakers import CONNECT_TIMEOUT_S
from bazaar_agent.flags_cli import MISMEASURED
from bazaar_agent.llm.chooser import HIDING_MARKS

console = Console()
err_console = Console(stderr=True)
PREVIEW_CHARS = 160
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})


def _connect() -> psycopg.Connection:
    return pgconn.connect(app="bazaar-injections-cli", connect_timeout_s=CONNECT_TIMEOUT_S)


def _hidden(ch: str) -> bool:
    """Control, format, private, line-separator and filler characters, and the ones rich measures at 0 columns
    that terminals draw at 2: each is shown, never drawn."""
    return (
        unicodedata.category(ch) in HIDDEN_CATEGORIES
        or ch in HIDING_MARKS
        or any(low <= ord(ch) <= high for low, high in MISMEASURED)
    )


def visible(text: str) -> str:
    """Hidden characters as ⟨U+XXXX⟩: the hiding stays visible and a terminal never acts on it."""
    return "".join(f"⟨U+{ord(ch):04X}⟩" if _hidden(ch) else ch for ch in text)


def _world_and_us(us: str | None) -> tuple[str, str | None, tuple[str, ...]]:
    from bazaar_agent.config import load_settings
    from bazaar_agent.holdings import scope_of
    from bazaar_agent.identity import resolve_team_id
    from bazaar_agent.runtime.tools import secrets_of

    settings = load_settings()
    team = resolve_team_id(us or settings.team_id, settings.data_dir, None)
    return scope_of(settings).world, team, secrets_of(settings)


def run(
    backfill: bool, as_json: bool, weak: bool, limit: int, us: str | None, connect: Connect = _connect
) -> list[dict[str, Any]]:
    world, team, secrets = _world_and_us(us)
    if backfill and team is None:
        err_console.print("our team id is unknown (set BAZAAR_TEAM_ID or pass --us): our own words would count")
        raise typer.Exit(2)
    with _open(connect) as conn:
        if backfill:
            found = il.backfill(conn, team)
            added = il.store(conn, il.InjectionLog(None, world, secrets), found)
            by = Counter((a.from_team, a.severity) for a in found)
            err_console.print(f"backfill ({world}, us {team}): {len(found)} tagged texts, {added} new rows")
            for (who, severity), n in sorted(by.items(), key=lambda kv: (-kv[1], str(kv[0]))):
                err_console.print(escape(f"  {who}: {n} {severity}"))
        rows = il.recent(conn, world, limit, weak)
    if as_json:
        print(json.dumps(rows, default=str, ensure_ascii=True, indent=1))
    else:
        console.print(_table(rows, world))
    return rows


def _table(rows: list[dict[str, Any]], world: str) -> Table:
    table = Table(title=f"Injection attempts ({world}, newest first)", show_lines=True)
    for col in ("tick", "from", "channel", "tags", "words (raw)", "proof", "we did"):
        table.add_column(col, overflow="fold")
    for r in rows:
        words = visible(str(r["raw"]))
        if len(words) > PREVIEW_CHARS:
            words = words[:PREVIEW_CHARS] + "…"
        table.add_row(  # literal Text cells: hostile words never become markup or an emoji code
            Text(str(r["tick"])),
            Text(visible(str(r["from_team"]))),
            Text(str(r["source"])),
            Text(", ".join(r["tags"]) + ("" if r["severity"] == "attempt" else " (weak)")),
            Text(words),
            Text(visible(str(r["proof"]))),
            Text(str(r["our_response"])),
        )
    return table


def injections(
    backfill: bool = typer.Option(False, "--backfill", help="Scan stored feed, threads and duels first (no game call)"),
    as_json: bool = typer.Option(False, "--json", help="ASCII-escaped JSON on stdout"),
    weak: bool = typer.Option(False, "--weak", help="Also the weak tags (JSON, a URL or a priced verb alone)"),
    limit: int = typer.Option(50, min=1, max=1000, help="Rows to show"),
    us: str | None = typer.Option(None, help="Our team id (default BAZAAR_TEAM_ID or the cached id)"),
) -> None:
    """Prompt-injection attempts other agents sent us, with the raw words and the endpoint that proves them."""
    run(backfill, as_json, weak, limit, us)
