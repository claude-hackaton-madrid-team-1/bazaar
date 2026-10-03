"""`bazaar-sim` — run the simulated Bazaar, reset it, list its team keys.

uv run bazaar-sim serve                      # http://127.0.0.1:8765, one tick every 10 s
SIM_TICK_SECONDS=2 uv run bazaar-sim serve   # faster
BAZAAR_SIM=local uv run bazaar status       # our CLI against it (BAZAAR_SIM=1: the public one)
SIM_ADMIN_TOKEN=... uv run bazaar-sim reset --url https://<sim host>
uv run bazaar-sim bench --seeds 1000           # the Market Test offline: the stall and the oracle per preset
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

    from bazaar_sim.app import build, server_config

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
    uvicorn.Server(server_config(application, host, port)).run()


@app.command()
def reset(
    url: str = typer.Option("http://127.0.0.1:8765", help="Simulator URL (the public one is in the README)"),
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
def bench(
    seeds: int = typer.Option(1000, help="Seeded books per preset and rule"),
    presets: str = typer.Option("normal,hard", help="Comma-separated presets (normal, hard, static)"),
    rules: str = typer.Option("quote,limit", help="Comma-separated match rules (quote, limit)"),
    fee_bps: int = typer.Option(0, help="The venue fee the oracle must cover (the stall charges none)"),
    spread: int | None = typer.Option(None, help="Arrivals over ticks 0..spread (default: ticks − 6; 0: all at once)"),
    shade: float = typer.Option(1.0, help="Scale every quote's shade away from its limit (2.0: twice as wide)"),
    relax: str | None = typer.Option(None, help='The share of shade a relaxing trader gives up, "lo,hi" (0.5,1.0)'),
) -> None:
    """The Market Test offline: the free stall and the oracle (the best any broker could do) on seeded books."""
    import statistics

    from bazaar_sim import bench as b

    def q(xs: list[float]) -> str:
        deciles = statistics.quantiles(xs, n=10)
        return f"{deciles[0]:.3f} {statistics.median(xs):.3f} {statistics.fmean(xs):.3f}"

    typer.echo(f"{seeds} books each · efficiency = realised ÷ the possible gains at the true limits (p10 p50 mean)")
    typer.echo(
        f"{'preset':8} {'rule':6} {'stall':>19}   {'oracle':>19}   {'oracle - stall':>19}   "
        "oracle > stall · oracle points vs 2 stall-level rivals"
    )
    for name in presets.split(","):
        lo_hi = tuple(float(x) for x in relax.split(",")) if relax else None
        p = b.preset(name.strip()).variant(spread=spread, shade=shade, relax=lo_hi)  # type: ignore[arg-type]
        for rule in rules.split(","):
            stall, oracle, points = [], [], []
            for seed in range(seeds):
                r = b.simulate(lambda _book: [], p, seed, rule=rule.strip(), fee_bps=fee_bps)
                stall.append(r.stall)
                oracle.append(r.oracle)
                points.append(b.session_points(r.oracle, r.stall, [r.stall, r.stall]))
            gap = [o - s for o, s in zip(oracle, stall, strict=True)]
            wins = sum(g > 0 for g in gap) / seeds
            typer.echo(
                f"{p.name:8} {rule.strip():6} {q(stall)}   {q(oracle)}   {q(gap)}   "
                f"{wins:6.1%} · {statistics.fmean(points):.3f}"
            )


@app.command()
def keys(teams: int = typer.Option(8, help="How many player teams the simulator runs (SIM_PLAYER_TEAMS)")) -> None:
    """The simulator's team keys (not secrets: it is a simulator)."""
    for n in range(1, teams + 1):
        typer.echo(f"sim-team{n}  → t{n:02d} (Team {n})")


if __name__ == "__main__":
    app()
