"""`bazaar supply` (the supply map) and `bazaar supply scan` (the card scan it reads, N14b).

`supply` reads `/api/me` (album first), the keyless catalog and feed, and the stored scan; it prints who
holds what and how many complete pages fit, and `--save` writes `supply_cards` / `supply_sets`.
`supply scan` reads `GET /api/cards/{id}` with our key, at most `--rate` requests a second, from id 1 (or
`--from-id`) until a run of unknown ids past the starting 270. It shares the key's 5 req/s with every
other process: run it while the doors are closed, or slowly. The first refusal ends the scan (a 429 is
never retried in a loop); what was read is still saved.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.config import load_settings
from bazaar_agent.sdk import BazaarError, public_client, team_client
from bazaar_agent.supply import START_ASSETS, SupplyMap, scan_diff, supply_map, valid_scan
from bazaar_agent.supply_db import SCAN_FILE, ScanStore, read_scan_file, save_map, save_scan, write_scan_file

supply_app = typer.Typer(help="Supply map: who holds each card, and how many complete pages can exist")
console = Console()
UNKNOWN = ("unknown_asset", "not_found")  # an id beyond the last asset
MAX_RATE = 2.0  # requests a second: the taker, maker and duels share the key's 5 req/s while the doors are open


def register(app: typer.Typer) -> None:
    app.add_typer(supply_app, name="supply")


def _connect() -> Callable[[], Any] | None:
    from bazaar_agent import db

    return lambda: db.connect(app="bazaar-supply")


def sets_table(sm: SupplyMap) -> Table:
    t = Table(title="Pages · how many complete pages can exist (the fewest copies of any page card)")
    for col in ("set", "released", "pages possible", "bottleneck", "ours"):
        t.add_column(col)
    for s in sorted(sm.sets.values(), key=lambda s: s.set_code):
        t.add_row(
            s.set_code,
            "yes" if s.released else "no",
            str(s.pages_possible),
            ", ".join(s.bottleneck[:4]),
            f"{s.our_have}/{s.page_cards}",
        )
    return t


def cards_table(sm: SupplyMap, scarce_max: int) -> Table:
    rows = sorted(
        (c for c in sm.cards.values() if c.page and c.minted <= scarce_max and c.minted > 0),
        key=lambda c: (c.minted, c.ref),
    )
    t = Table(title=f"Scarce page cards · at most {scarce_max} copies · holders from the scan + the feed")
    for col in ("card", "rarity", "minted", "ours", "placed with", "unplaced"):
        t.add_column(col)
    for c in rows:
        holders = ", ".join(f"{team}×{n}" if n > 1 else team for team, n in c.holders) or "-"
        t.add_row(c.ref, c.rarity, f"{c.minted}/{c.print_run}", str(c.ours), holders, str(c.unplaced))
    return t


@supply_app.callback(invoke_without_command=True)
def supply(
    ctx: typer.Context,
    save: bool = typer.Option(False, "--save", help="Write supply_cards / supply_sets to Postgres"),
    as_json: bool = typer.Option(False, "--json", help="Print the map as JSON"),
    scarce_max: int = typer.Option(5, help="List page cards with at most this many copies"),
) -> None:
    """The supply map from /api/me, the catalog, the feed and the stored scan."""
    if ctx.invoked_subcommand is not None:
        return
    from bazaar_agent.cli import _events, _team_me

    settings = load_settings()
    _, me = _team_me()
    scan = ScanStore(settings.data_dir / "supply", _connect(), lambda m: console.print(f"[dim]{escape(m)}[/dim]"))
    sm = supply_map(public_client(settings).catalog(), me, _events(True), scan.rows(0))
    if as_json:
        typer.echo(json.dumps({"tick": sm.tick, "sets": _plain(sm.sets), "cards": _plain(sm.cards)}, indent=2))
    else:
        opened = sum(sm.packs_opened.values())
        console.print(
            f"tick {sm.tick} · {sm.assets} assets placed ({sm.scanned} from the scan) · {opened} packs opened"
        )
        console.print(sets_table(sm))
        console.print(cards_table(sm, scarce_max))
    if save:
        from bazaar_agent import db

        with db.connect_ready("bazaar-supply") as conn:
            n = save_map(conn, sm, int(sm.tick or 0))
        console.print(f"saved {n} cards and {len(sm.sets)} sets to Postgres (supply_cards, supply_sets)")


def _plain(rows: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import asdict

    return {k: asdict(v) for k, v in rows.items()}


def scan_ids(
    read: Callable[[int], dict[str, Any]],
    start: int,
    *,
    max_id: int,
    gap: int,
    pause_s: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str]:
    """Read ids from `start` until `gap` unknown ids in a row past the starting assets (or `max_id`).
    Returns the rows and why it stopped; the first refusal other than an unknown id stops it."""
    rows: list[dict[str, Any]] = []
    misses = 0
    for aid in range(start, max_id + 1):
        if aid > start:
            sleep(pause_s)
        try:
            body = read(aid)
        except BazaarError as e:
            if e.code in UNKNOWN or e.status == 404:
                misses += 1
                if aid > START_ASSETS and misses >= gap:
                    return rows, f"{gap} unknown ids in a row after id {aid - gap}"
                continue
            return rows, f"refused at id {aid}: {e.code} ({e.status}); not retried"
        misses = 0
        rows += valid_scan([body])
    return rows, f"reached --max-id {max_id}"


@supply_app.command("scan")
def scan_cmd(
    rate: float = typer.Option(1.0, help="Requests per second, at most 2 (the key's 5 req/s is shared by all of us)"),
    from_id: int = typer.Option(1, help="First id; every id not read again is kept from the previous scan"),
    max_id: int = typer.Option(3000, help="Never read past this id"),
    gap: int = typer.Option(5, help="Stop after this many unknown ids in a row past id 270"),
    save: bool = typer.Option(False, "--save", help="Also store the scan in Postgres (supply_assets)"),
) -> None:
    """Scan GET /api/cards/{id}: every card's ref and history (the supply map's starting hands)."""
    if not 0 < rate <= MAX_RATE:
        console.print(f"[red]--rate must be in (0, {MAX_RATE:g}]: the key's 5 req/s is shared[/red]")
        raise typer.Exit(2)
    settings = load_settings()
    folder = settings.data_dir / "supply"
    previous = read_scan_file(folder / SCAN_FILE)
    console.print(f"scanning from id {from_id} at {rate:g} req/s (~{START_ASSETS / rate:.0f} s for 270 ids)")
    team = team_client(settings, retries=0)  # a 429 stops the scan: the SDK must not retry it either
    try:
        tick = int(team.clock().get("tick") or 0)
    except BazaarError as e:
        console.print(f"[red]/api/clock refused: {escape(e.code)} ({e.status}); nothing scanned[/red]")
        raise typer.Exit(1) from None
    rows, why = scan_ids(team.card, from_id, max_id=max_id, gap=gap, pause_s=1.0 / rate, sleep=time.sleep)
    if not rows:
        console.print(f"nothing read ({escape(why)}): {SCAN_FILE} and Postgres are left as they were")
        raise typer.Exit(1)
    merged = {int(r["id"]): r for r in previous} | {int(r["id"]): r for r in rows}  # a partial scan loses nothing
    scan = [merged[i] for i in sorted(merged)]
    path = write_scan_file(folder, scan)
    diff = scan_diff(previous, scan)
    console.print(
        f"{len(rows)} assets read ({escape(why)}); {len(scan)} in {path.name}; "
        f"{len(diff['added'])} new ids, {len(diff['moved'])} changed hands since the last scan"
    )
    if save:  # only what this run read: older rows from this laptop's file never overwrite a newer scan
        from bazaar_agent import db

        with db.connect_ready("bazaar-supply") as conn:
            n = save_scan(conn, rows, tick)
        console.print(f"stored {n} assets in Postgres (supply_assets)")
