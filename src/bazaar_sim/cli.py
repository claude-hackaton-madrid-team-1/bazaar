"""`bazaar-sim` — run the simulated Bazaar, reset it, list its team keys.

uv run bazaar-sim serve                      # http://127.0.0.1:8765, one tick every 10 s
SIM_TICK_SECONDS=2 uv run bazaar-sim serve   # faster
BAZAAR_URL=http://127.0.0.1:8765 BAZAAR_KEY=sim-team1 uv run bazaar status
SIM_ADMIN_TOKEN=... uv run bazaar-sim reset --url https://<sim host>
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

import typer

from bazaar_sim.store import StoreRefused, open_store

app = typer.Typer(no_args_is_help=True, help="The simulated Bazaar: an HTTP API that behaves like the real game")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / ".local" / "sim" / "world.sqlite"


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (0.0.0.0 in a container)"),
    port: int = typer.Option(int(os.environ.get("PORT") or 8765), help="Port (Railway sets PORT)"),
) -> None:
    """Serve the simulator. Store: SIM_DATABASE_URL (sqlite/memory/postgres to bazaar_sim), else .local/sim."""
    import uvicorn

    from bazaar_sim.app import build

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        store = open_store(os.environ.get("SIM_DATABASE_URL"), DEFAULT_DB)
    except StoreRefused as e:
        typer.echo(f"refusing to start: {e}", err=True)
        raise typer.Exit(2) from None
    application, sim = build(store=store)
    admin = "set" if sim.admin_token else "NOT set (reset disabled)"
    typer.echo(
        f"bazaar-sim on http://{host}:{port} · tick {sim.world.tick} every {sim.world.config.tick_seconds:g} s · "
        f"store {store.describe()} · admin token {admin} · keys sim-team1..sim-team{sim.world.config.player_teams}"
    )
    uvicorn.run(application, host=host, port=port, log_level="warning", access_log=False, proxy_headers=True)


@app.command()
def reset(
    url: str = typer.Option(os.environ.get("BAZAAR_URL") or "http://127.0.0.1:8765", help="Simulator URL"),
    seed: int | None = typer.Option(None, help="A new world seed (default: keep the current one)"),
) -> None:
    """Reset the world to tick 0 (POST /sim/reset). Reads the token from SIM_ADMIN_TOKEN, never a flag."""
    token = os.environ.get("SIM_ADMIN_TOKEN")
    if not token:
        typer.echo("SIM_ADMIN_TOKEN is not set", err=True)
        raise typer.Exit(2)
    body = json.dumps({"seed": seed} if seed is not None else {}).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/sim/reset",
        data=body,
        method="POST",
        headers={"X-Admin-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            typer.echo(resp.read().decode())
    except urllib.error.HTTPError as e:
        typer.echo(f"reset refused: {e.code} {e.read().decode()[:200]}", err=True)
        raise typer.Exit(1) from None


@app.command()
def keys(teams: int = typer.Option(8, help="How many player teams the simulator runs (SIM_PLAYER_TEAMS)")) -> None:
    """The simulator's team keys (not secrets: it is a simulator)."""
    for n in range(1, teams + 1):
        typer.echo(f"sim-team{n}  → t{n:02d} (Team {n})")


if __name__ == "__main__":
    app()
