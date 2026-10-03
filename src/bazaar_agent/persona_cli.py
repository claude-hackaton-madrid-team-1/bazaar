"""`bazaar dealer personas`: the negotiation params the persona model derives from each dealer's published
traits and menu (`persona_model.trait_prior`), one row per persona and sell line. One keyless public read
(`GET /api/dealers`); no key, no write."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.config import load_settings
from bazaar_agent.persona_model import parse_personas, trait_prior
from bazaar_agent.sdk import BazaarError, public_client

console = Console()
err_console = Console(stderr=True)


def persona_rows(payload: Any) -> list[dict[str, Any]]:
    """`trait_prior(...).as_row()` per persona and sell line, from a `/api/dealers` payload (untrusted)."""
    raw = (payload.get("personas") or payload.get("dealers") or []) if isinstance(payload, dict) else []
    personas = parse_personas(raw if isinstance(raw, list) else [])
    return [trait_prior(p, line.item).as_row() for p in personas.values() for line in p.sells]


def personas(
    as_json: Annotated[bool, typer.Option("--json", help="Print the rows as JSON (stdout only).")] = False,
) -> None:
    """Per dealer and item: the ladder, expected limit and tone the persona's traits imply (no learned curve)."""
    try:
        payload = public_client(load_settings()).dealers()
    except BazaarError as e:
        err_console.print(f"[red]could not read /api/dealers: {escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    rows = persona_rows(payload)
    if as_json:
        print(json.dumps(rows))
        return
    if not rows:
        err_console.print("no persona with a sell line in /api/dealers")
        return
    table = Table(title="Persona trait prior (GET /api/dealers)")
    for column in rows[0]:
        table.add_column(column)
    for row in rows:
        table.add_row(*(escape("-" if v is None else str(v)) for v in row.values()))
    console.print(table)


def register(app: typer.Typer) -> None:
    app.command("personas")(personas)
