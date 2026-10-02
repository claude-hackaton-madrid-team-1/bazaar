"""`bazaar` — Team 1's command line. Thin wrappers: logic lives in the modules it calls."""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Any

import typer
from rich.console import Console

from bazaar_agent import intel, render
from bazaar_agent.config import REPO_ROOT, ConfigError, load_settings
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore, load_events
from bazaar_agent.sdk import BazaarError, public_client, team_client
from bazaar_agent.ticks import Clock, run_per_tick

app = typer.Typer(no_args_is_help=True, help="Team 1 · The Bazaar · tick-driven trading agent")
feed_app = typer.Typer(no_args_is_help=True, help="Capture and inspect the public feed")
db_app = typer.Typer(no_args_is_help=True, help="Local Postgres + pgvector memory")
app.add_typer(feed_app, name="feed")
app.add_typer(db_app, name="db")
dealer_app = typer.Typer(no_args_is_help=True, help="Negotiate with dealers (one move per tick)")
app.add_typer(dealer_app, name="dealer")
duel_app = typer.Typer(no_args_is_help=True, help="Duels: log every response; play inside our limit")
app.add_typer(duel_app, name="duel")
console = Console()

LIVE_HELP = "Merge the live feed window into the captured history"


def _events(live: bool) -> list[Event]:
    settings = load_settings()
    store = FeedStore(settings.feed_dir)
    window = public_client(settings).feed_window(DEFAULT_WINDOW) if live else None
    events = load_events(store, window)
    if not events:
        console.print("[yellow]no captured feed yet: reading the live window[/yellow]")
        events = load_events(store, public_client(settings).feed_window(DEFAULT_WINDOW))
    return events


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


# ---------------------------------------------------------------- public views (no key)


@app.command()
def clock() -> None:
    """Current tick, pace, doors, per-tick limits and the action budget left in this tick."""
    console.print(render.clock_table(Clock.model_validate(public_client(load_settings()).clock())))


@app.command()
def dealers() -> None:
    """Dealers in play: traits, menu, list prices, hourly quotas."""
    data = public_client(load_settings()).dealers()
    console.print(render.dealers_table(data.get("personas") or data.get("dealers") or []))


@app.command("tape")
def tape_cmd(
    limit: int = typer.Option(30, help="Rows to show"),
    item: str | None = typer.Option(None, help="Filter by card or pack ref, e.g. sobre_barrio"),
    live: bool = typer.Option(False, help=LIVE_HELP),
) -> None:
    """Every settlement (trade print): who bought what from whom, at what price."""
    prints = intel.tape(_events(live))
    if item:
        prints = [p for p in prints if p.ref == item]
    console.print(render.tape_table(prints, limit))


@app.command()
def curves(
    dealer: str | None = typer.Option(None, help="Filter by dealer id, e.g. abuela"),
    item: str | None = typer.Option(None, help="Filter by item, e.g. sobre_barrio"),
    threads: int = typer.Option(0, help="Also list the last N threads with every price"),
    live: bool = typer.Option(False, help=LIVE_HELP),
) -> None:
    """Dealer concession curves rebuilt from every team's public threads."""
    rows = intel.dealer_threads(_events(live))
    rows = [t for t in rows if (not dealer or t.dealer == dealer) and (not item or t.item == item)]
    console.print(render.curves_table(intel.curve_summary(rows)))
    if threads:
        console.print(render.threads_table(rows, threads))


@app.command()
def teams(live: bool = typer.Option(False, help=LIVE_HELP)) -> None:
    """The competition: each team's flow (dealer bids, buys, sells, listings, inferred ×1.6 set)."""
    console.print(render.teams_table(intel.team_flows(_events(live))))


@app.command()
def book(
    venue: str = typer.Option("rastro", help="Venue id"),
    card: str | None = typer.Option(None, help="Filter by card ref, e.g. LAV-04"),
) -> None:
    """Live order book of a venue, with board pseudonyms resolved to team ids from the feed."""
    client = public_client(load_settings())
    board = client.board(venue).get("offers") or []
    lines = intel.order_book(board, intel.listed_makers(_events(live=True)))
    if card:
        lines = [b for b in lines if b.card == card]
    console.print(render.book_table(lines, venue))


# ---------------------------------------------------------------- our team (needs BAZAAR_KEY)


@app.command()
def status(cards: bool = typer.Option(True, help="Also list our cards with your_value")) -> None:
    """Our cash, level, score, album pages with missing cards, and cards (GET /api/me)."""
    try:
        me: dict[str, Any] = team_client(load_settings()).me()
    except ConfigError as e:
        _fail(str(e))
    except BazaarError as e:
        _fail(f"/api/me refused: {e.code} ({e.status})")
    console.print(render.status_table(me))
    from bazaar_agent.album import album_view

    console.print(render.album_table(album_view(me, public_client(load_settings()).catalog())))
    if cards:
        console.print(render.cards_table(me))


# ---------------------------------------------------------------- dealers (writes: needs BAZAAR_KEY)


@dealer_app.command("buy")
def dealer_buy(
    item: str = typer.Argument(help="Card ref (LAV-03) or pack id (sobre_barrio)"),
    max_price: int = typer.Option(..., "--max", help="Hard limit: never pay above this"),
    start: int = typer.Option(..., help="Opening bid"),
    step: int = typer.Option(1, help="Raise per tick (small steps earn small steps)"),
    dealer: str = typer.Option("abuela", help="Dealer id"),
    live: bool = typer.Option(False, help="Actually trade. Without it: dry run, nothing is sent"),
    jev: bool = typer.Option(False, help="Ask Jev negotiation_move each tick (advisory, inside the limit)"),
) -> None:
    """Buy one card or pack from a dealer: rising distinct bids, accept at our next bid, hard max."""
    from bazaar_agent.agents.dealer import BidPlan, Negotiation, decide, negotiate

    plan = BidPlan(start, step, max_price)
    topic = {"buy": {"pack": item}} if "-" not in item else {"buy": {"card": item}}
    if not live:
        neg, schedule = Negotiation(plan), []
        while (move := decide(neg, None, None, False)).kind == "bid" and move.price is not None:
            neg.bids.append(move.price)
            schedule.append(move.price)
        console.print(
            f"[yellow]dry run[/yellow] {dealer} {topic}: bids {schedule}, accept any ask ≤ next bid, "
            f"walk above {max_price}. Add --live to trade."
        )
        return
    settings = load_settings()
    client = team_client(settings)
    advisor = _jev_advisor(item, settings) if jev else None
    out = negotiate(client, dealer, topic, plan, log=console.print, advisor=advisor)
    colour = "green" if out.status == "deal" else "red"
    console.print(
        f"[{colour}]{out.status}[/{colour}] thread {out.thread} price {out.price} bids {list(out.bids)} "
        f"in {out.ticks} ticks"
    )


def _jev_advisor(item: str, settings: Any) -> Any:
    from bazaar_agent.jev import judge, load_questions

    questions = load_questions(REPO_ROOT / "questions" / "negotiation.json")
    move_q = {"negotiation_move": questions["negotiation_move"]}

    def advise(neg: Any, ask: int | None, final: bool) -> str | None:
        state = {
            "item": item,
            "our_bids": neg.bids,
            "our_limit": neg.plan.max_price,
            "her_ask": ask,
            "her_offer_is_final": final,
            "learned": "Abuela usually fills commons at 9 and packs at 17",
        }
        key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
        verdict = judge(state, move_q, api_key=key, timeout_s=3.0).verdicts["negotiation_move"]
        console.print(f"  jev: {verdict.verdict} ({verdict.value:.2f})")
        return verdict.verdict if verdict.decided else None

    return advise


# ---------------------------------------------------------------- duels (needs BAZAAR_KEY)


@duel_app.command("run")
def duel_run(
    play: bool = typer.Option(False, help="Send offers/accepts. Without it: log only"),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
) -> None:
    """Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit."""
    from bazaar_agent.agents.duelist import append_jsonl, duel_move

    settings = load_settings()
    client = team_client(settings)
    log_path = settings.data_dir / "duels" / "duels.jsonl"
    first_seen: dict[int, int] = {}

    def on_tick(c: Clock) -> None:
        try:
            data = client.duels()
        except BazaarError as e:
            console.print(f"tick {c.tick}: /api/duels refused {e.code}")
            return
        append_jsonl(log_path, {"tick": c.tick, "response": data})
        duels = [d for d in data.get("duels") or [] if isinstance(d, dict)]
        console.print(f"tick {c.tick}: {len(duels)} live duel(s) logged")
        for d in duels:
            did = d.get("id")
            if not isinstance(did, int):
                continue
            first_seen.setdefault(did, c.tick)
            move = duel_move(d, c.tick, first_seen[did])
            console.print(
                f"  duel {did} {d.get('role')} limit {d.get('your_limit')} rival {d.get('rival_offer')} "
                f"deadline {d.get('deadline')} -> {move.kind} {move.price or ''} ({move.reason})"
            )
            if not play or move.kind == "hold":
                continue
            try:
                if move.kind == "accept":
                    client.duel_accept(did)
                elif move.price is not None:
                    client.duel_say(
                        did, "Propongo este precio, creo que es justo para los dos.", price=move.price, days=move.days
                    )
                append_jsonl(log_path, {"tick": c.tick, "duel": did, "move": move.__dict__})
            except BazaarError as e:
                console.print(f"  duel {did}: refused {e.code} ({e.message[:80]})")
                append_jsonl(log_path, {"tick": c.tick, "duel": did, "refused": e.code})

    console.print(f"duels → {log_path} ({'PLAYING' if play else 'log only'})")
    run_per_tick(client.clock, on_tick, max_ticks=max_ticks or None)


# ---------------------------------------------------------------- feed capture


@feed_app.command("capture")
def feed_capture(
    once: bool = typer.Option(False, help="Capture one window and exit"),
    window: int = typer.Option(DEFAULT_WINDOW, help="Events per read (server cap: 500)"),
) -> None:
    """Append the public feed to .local/feed/feed.jsonl once per tick. Ctrl-C to stop."""
    settings = load_settings()
    client, store = public_client(settings), FeedStore(settings.feed_dir)

    def capture(c: Clock | None = None) -> None:
        try:
            result = store.append(client.feed_window(window), window)
        except BazaarError as e:
            console.print(f"[red]feed read refused ({e.code}); next tick[/red]")
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        warn = " [red]GAP POSSIBLE: window overran our history[/red]" if result.gap_possible else ""
        console.print(
            f"{stamp} tick {c.tick if c else '-'}: fetched {result.fetched}, new {result.new}, "
            f"newest id {result.newest_id}{warn}"
        )

    if once:
        capture()
        return
    console.print(f"capturing to {store.path} (one read per tick)")
    capture()
    run_per_tick(client.clock, capture)


@feed_app.command("stats")
def feed_stats() -> None:
    """How much feed history we hold, and the event mix."""
    events = list(FeedStore(load_settings().feed_dir).events())
    if not events:
        _fail("no captured feed: run `bazaar feed capture`")
    counts: dict[str, int] = {}
    for e in events:
        counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
    console.print(
        f"{len(events)} events · ids {events[0]['id']}..{events[-1]['id']} · "
        f"ticks {events[0].get('tick')}..{events[-1].get('tick')}"
    )
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        console.print(f"  {kind:<20} {n}")


# ---------------------------------------------------------------- database


@db_app.command("up")
def db_up() -> None:
    """Start Postgres + pgvector (docker compose, localhost:5433)."""
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "db"], cwd=REPO_ROOT, check=True)


@db_app.command("init")
def db_init() -> None:
    """Create every table (idempotent)."""
    from bazaar_agent import db

    with db.connect(load_settings().database_url.get_secret_value()) as conn:
        db.init_schema(conn)
    console.print("[green]schema applied[/green]")
    db_tables()


@db_app.command("load")
def db_load(live: bool = typer.Option(True, help=LIVE_HELP)) -> None:
    """Load the captured feed into feed_events, tape and dealer_curves (idempotent)."""
    from bazaar_agent import db

    with db.connect(load_settings().database_url.get_secret_value()) as conn:
        counts = db.load_feed(conn, _events(live))
    console.print(f"[green]loaded[/green] {counts}")


@db_app.command("tables")
def db_tables() -> None:
    """Every table with its row count."""
    from rich.table import Table

    from bazaar_agent import db

    with db.connect(load_settings().database_url.get_secret_value()) as conn:
        counts = db.table_counts(conn)
    t = Table(title="bazaar db")
    t.add_column("table")
    t.add_column("rows", justify="right")
    for name, n in counts:
        t.add_row(name, str(n))
    console.print(t)


if __name__ == "__main__":
    app()
