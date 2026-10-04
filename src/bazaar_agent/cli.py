"""`bazaar` — Team 1's command line. Thin wrappers: logic lives in the modules it calls."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Annotated, Any

import typer
from rich.console import Console

from bazaar_agent import (
    approval_cli,
    breaker_cli,
    deploy_guard,
    flags_cli,
    impact_cli,
    injection_cli,
    intel,
    persona_cli,
    render,
    supply_cli,
    traces,
)
from bazaar_agent import telemetry as tm
from bazaar_agent.agents import dealer_finals
from bazaar_agent.config import REPO_ROOT, ConfigError, Settings, load_settings
from bazaar_agent.evals import cli as evals_cli
from bazaar_agent.evals.model import EVERY_TICKS
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore, load_events
from bazaar_agent.identity import remember_team_id, resolve_team_id
from bazaar_agent.learn import cli as learn_cli
from bazaar_agent.llm import cli as llm_cli
from bazaar_agent.official_values import OfficialValues
from bazaar_agent.runtime import cli as runtime_cli
from bazaar_agent.sdk import BazaarError, public_client, read_once_more_after_429, team_client
from bazaar_agent.ticks import Clock, action_budget_s, run_per_tick

app = typer.Typer(no_args_is_help=True, help="Team 1 · The Bazaar · tick-driven trading agent")
feed_app = typer.Typer(no_args_is_help=True, help="Capture and inspect the public feed")
db_app = typer.Typer(no_args_is_help=True, help="Postgres memory: local docker or the shared Railway DB")
app.add_typer(feed_app, name="feed")
app.add_typer(db_app, name="db")
dealer_app = typer.Typer(no_args_is_help=True, help="Negotiate with dealers (one move per tick)")
app.add_typer(dealer_app, name="dealer")
duel_app = typer.Typer(no_args_is_help=True, help="Duels: log every response; play inside our limit")
app.add_typer(duel_app, name="duel")
rules_app = typer.Typer(help="Guardrails from GUARDRAILS.md: show them, or check an action against live /me")
app.add_typer(rules_app, name="rules")
obs_app = typer.Typer(no_args_is_help=True, help="Observability: OpenTelemetry traces in Arize Phoenix")
app.add_typer(obs_app, name="obs")
agent_app = typer.Typer(
    no_args_is_help=True,
    help="Autonomous agents: taker and maker every tick; the desk (chat) on the Claude Agent SDK. Dry run by default",
)
app.add_typer(agent_app, name="agent")
venue_app = typer.Typer(
    no_args_is_help=True,
    help="Our own market: open, close, re-fee, announce, status. Dry run; build only (GUARDRAILS.md)",
)
app.add_typer(venue_app, name="venue")
broker_app = typer.Typer(no_args_is_help=True, help="Our venue's broker: match crossing offers every tick. Dry run")
app.add_typer(broker_app, name="broker")
console = Console()
err_console = Console(stderr=True)

LIVE_HELP = "Merge the live feed window into the captured history"


def evals_default(asked: int | None, trading: bool) -> int:
    """`--evals-every`, else every EVERY_TICKS ticks for a process that trades (live, or duel --play) and off
    for a dry run: a laptop dry run must not write the team's scores."""
    if asked is not None:
        return asked
    return EVERY_TICKS if trading else 0


EVALS_EVERY_HELP = (
    "Score this agent's settled decisions every N ticks in the background (default: 6 if it trades, else off)"
)


@app.callback()
def _root(ctx: typer.Context, llm_runtime: str | None = llm_cli.LLM_RUNTIME_OPTION) -> None:
    """Global options. `--llm-runtime` pins the runtime LLM for this run (see RUNTIME.md).

    With BAZAAR_TRACING=1: one root span per command, every console line mirrored into spans.
    """
    llm_cli.pin_runtime(llm_runtime)
    try:
        settings = load_settings()  # BAZAAR_URL set, or a bad BAZAAR_SIM: fail fast, before any command runs
    except ConfigError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    # The banner: every command says which Bazaar it talks to. On a laptop it goes to stderr, so a
    # `--json` stdout stays clean; on Railway (which files stderr as errors) to stdout, where no
    # service command's output is parsed.
    style = "bold yellow" if settings.simulator else "dim"
    banner = console if os.environ.get("RAILWAY_ENVIRONMENT") else err_console
    banner.print(f"[{style}]{settings.target_line()}[/{style}]", highlight=False)
    # Warnings (e.g. Phoenix or Postgres unreachable) follow the banner: stdout on Railway, which files stderr
    # as errors, stderr elsewhere so `--json` stdout stays JSON. A no-op when logging is already configured.
    logging.basicConfig(stream=log_stream(), level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if tm.init_tracing("bazaar"):
        command = ctx.invoked_subcommand or "bazaar"
        ctx.with_resource(tm.command_span(command))
        tm.capture_console(console, command)


def _events(live: bool) -> list[Event]:
    settings = load_settings()
    store = FeedStore(settings.feed_dir)
    window = public_client(settings).feed_window(DEFAULT_WINDOW) if live else None
    events = load_events(store, window)
    if not events:
        err_console.print("[yellow]no captured feed yet: reading the live window[/yellow]")  # stdout stays JSON
        events = load_events(store, public_client(settings).feed_window(DEFAULT_WINDOW))
    return events


def log_stream(env: Any = None) -> Any:
    """Where warnings go: stdout on Railway (it files stderr as errors), stderr elsewhere (`--json` stays JSON)."""
    return sys.stdout if (os.environ if env is None else env).get("RAILWAY_ENVIRONMENT") else sys.stderr


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _ledger(source: str, live: bool = False) -> Any:
    """The guardrail ledger every process shares. Live: the shared Postgres one or exit (`open_ledger`)."""
    from rich.markup import escape

    from bazaar_agent.ledger_pg import LedgerNotShared, open_ledger

    settings = load_settings()
    try:
        # stderr: a command's stdout may be JSON (`strategy --json`), and this line is only context
        return open_ledger(
            settings.data_dir,
            source=source,
            live=live,
            database_url=settings.database_url.get_secret_value(),
            game_url=settings.bazaar_url,
            log=lambda m: err_console.print(f"[dim]{escape(m)}[/dim]"),
        )
    except LedgerNotShared as e:
        _fail(f"refusing to trade: {e}")


def _tactic_book(
    rules: Any, store: Any, us: str | None, log: Callable[[str], None], off_reason: str | None = None
) -> Any:
    """The words' bluff tactics (N16), learned per counterparty in the N3 store (`store` None: memory only).
    Tactic lessons are read once here, before the first tick; later reads happen after each tick's sends. The
    tie-break seed is secret per process unless BAZAAR_BLUFF_SEED fixes it (a reproducible simulator run)."""
    from bazaar_agent.agents.bluff import TacticBook, default_seed

    book = TacticBook(store=store, us=us, rules=rules, log=log, seed=default_seed(), off_reason=off_reason)
    on, why = book.enabled()
    loaded = book.load()
    log(f"bluff tactics {'on' if on else 'OFF (' + why + ')'} · {loaded} tactic lesson(s) loaded")
    return book


FEED_READ_TIMEOUT_S = 2.0  # the bluff book's keyless feed read: short, never retried, it must not stall a tick


def _feed_reader(settings: Any) -> Callable[[int], list[Event]]:
    """`feed_window(limit)` without a key, a 2 s timeout and no retry: for the loops without a feed of their own."""
    from bazaar_agent.sdk import PublicBazaar

    return PublicBazaar(settings.bazaar_url, timeout=FEED_READ_TIMEOUT_S, retries=0).feed_window


def _learning_store(app: str, log: Callable[[str], None]) -> Any:
    """A `LearningStore` on the shared Postgres (short connect timeout), connected now, never inside a tick."""
    from bazaar_agent import db
    from bazaar_agent.learn.store import LearningStore

    store = LearningStore(lambda: db.connect(app=app, connect_timeout_s=3), log)
    store.open()
    return store


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


def _our_team(settings: Any = None) -> str | None:
    """Our team id (BAZAAR_TEAM_ID, the `.local/team_id` cache, else /api/me once): tags us in every view."""
    from bazaar_agent.identity import resolve_team_id

    settings = settings or load_settings()
    read_me = (lambda: team_client(settings).me()) if settings.bazaar_key else None
    team = resolve_team_id(
        settings.team_id, settings.data_dir, read_me, lambda m: console.print(f"[yellow]{m}[/yellow]")
    )
    if team is None:
        console.print("[yellow]our team id is unknown: we show as competition (set BAZAAR_TEAM_ID)[/yellow]")
    return team


@app.command()
def curves(
    dealer: str | None = typer.Option(None, help="Filter by dealer id, e.g. abuela"),
    item: str | None = typer.Option(None, help="Filter by item, e.g. sobre_barrio"),
    threads: int = typer.Option(0, help="Also list the last N threads with every price"),
    live: bool = typer.Option(False, help=LIVE_HELP),
    ours: bool = typer.Option(False, "--ours", help="Only our own threads"),
    theirs: bool = typer.Option(False, "--theirs", help="Only the other teams' threads"),
    every: bool = typer.Option(False, "--all", help="Every team's threads, ours counted in 'ours' (default)"),
) -> None:
    """Dealer concession curves rebuilt from every team's public threads; ours are tagged."""
    if ours + theirs + every > 1:
        _fail("pick one of --ours, --theirs, --all")
    us = _our_team()
    if (ours or theirs) and us is None:
        _fail("--ours/--theirs need our team id: set BAZAAR_TEAM_ID (or BAZAAR_KEY)")
    rows = [
        t
        for t in intel.dealer_threads(_events(live), us)
        if (not dealer or t.dealer == dealer)
        and (not item or t.item == item)
        and (not ours or t.ours)
        and (not theirs or not t.ours)
    ]
    title = "our threads" if ours else "the other teams' threads" if theirs else "every team's threads"
    console.print(render.curves_table(intel.curve_summary(rows), title))
    if threads:
        console.print(render.threads_table(rows, threads))


@app.command()
def teams(
    live: bool = typer.Option(False, help=LIVE_HELP),
    include_us: bool = typer.Option(False, "--include-us", help="Mix our own row into the competition table"),
) -> None:
    """The competition: each team's flow (dealer bids, buys, sells, listings, inferred ×1.6 set). Us apart."""
    us = _our_team()
    flows = intel.team_flows(_events(live))
    if include_us or us is None:
        console.print(render.teams_table(flows, us=us))
        return
    theirs, ours = intel.split_us(flows, us, lambda f: f.team)
    console.print(render.teams_table(theirs))
    console.print(render.teams_table(ours, f"Us · {us} (not counted as competition)", us=us))


def _payload_file(path: str) -> Any:
    """A captured payload: a bare body, a fixture (`{"body": ...}`) or a feed event (`{"payload": ...}`)."""
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("body"), dict):
        return data["body"]
    if isinstance(data, dict) and isinstance(data.get("payload"), dict) and "type" in data:
        return data["payload"]
    return data


def _history(events_file: str | None, live: bool) -> list[Event]:
    """The whole feed history, as the agents read it: the shared `feed_events` table when Postgres answers
    (the monitor writes it), else this machine's capture, merged with the live window when `live`. A
    `--events` JSONL file wins. A fresh worktree has no capture: the DB is what holds Friday."""
    if events_file:
        return _events_file(events_file)
    from bazaar_agent.agents.runtime import MarketFeed

    settings = load_settings()
    window = public_client(settings).feed_window if live else (lambda limit: [])
    feed = MarketFeed(window, FeedStore(settings.feed_dir), _db_connect("bazaar-intel"), err_console.print)
    events = feed.events()
    if not events:  # notes go to stderr: `affinity --json` / `trade-plan --json` keep stdout pure JSON
        err_console.print("[yellow]no feed history (no DB, no capture): reading the live window[/yellow]")
        return _events(live=True)
    return events


def _events_file(path: str) -> list[Event]:
    """Feed events from a JSONL file (one event per line, e.g. a `feed_events` export)."""
    from pathlib import Path

    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


@app.command()
def affinity(
    live: bool = typer.Option(False, help=LIVE_HELP),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file (the multiset); else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    beta: float = typer.Option(0.5, help="Weight of one unit of (damped) interest per sd of the multiplier"),
    as_json: bool = typer.Option(False, "--json", help="Print the map as JSON"),
    teams: bool = typer.Option(
        False, "--teams", help="Read-only: the stored team_affinity rows (said in a thread vs inferred)"
    ),
) -> None:
    """Rival affinity map: P(each set holds each team's top multiplier), from the public feed alone."""
    from dataclasses import asdict

    from bazaar_agent import affinity as af

    if teams:
        _team_affinity(as_json)
        return

    me = _payload_file(me_file) if me_file else _team_me()[1]
    catalog = _payload_file(catalog_file) if catalog_file else public_client(load_settings()).catalog()
    events = _history(events_file, live)
    us = str(me.get("id") or "") or None
    amap = af.affinity_map(
        events, af.catalog_sets(catalog), af.multipliers_from(me), catalog, af.ModelParams(beta=beta), [us or ""]
    )
    if as_json:
        typer.echo(json.dumps({t: asdict(a) for t, a in amap.teams.items()}, indent=2, default=str))
        return
    console.print(render.affinity_table(amap))
    for s in af.catalog_sets(catalog):
        console.print(f"{s}: chased by {', '.join(amap.chasers(s, 0.5)) or 'nobody at P >= 0.5'}")


def _team_affinity(as_json: bool) -> None:
    """`bazaar affinity --teams`: the `team_affinity` table as stored (a select, nothing written)."""
    from bazaar_agent import db
    from bazaar_agent import team_affinity as ta

    try:
        with db.connect(app="bazaar-affinity", connect_timeout_s=5) as conn:
            conn.read_only = True
            rows = ta.read(conn)
    except Exception as e:  # noqa: BLE001 — a read-only report: say why and stop
        err_console.print(f"team_affinity unreadable: {db.redact(str(e))}")
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    console.print(render.team_affinity_table(rows))
    if not rows:
        console.print("no rows yet: the taker's team desk writes them (inferred every 10 ticks, said on a reply)")


@app.command("trade-plan")
def trade_plan(
    live: bool = typer.Option(False, help=LIVE_HELP),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file; else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    venues_file: str | None = typer.Option(None, "--venues", help="/api/venues from a file; else the API"),
    venue: str = typer.Option("rastro", help="Venue to post on (its fee prices every trade)"),
    share: float = typer.Option(0.25, min=0.01, max=1.0, help="No counterparty above this share of planned volume"),
    listings: int = typer.Option(12, min=0, help="Listings (one tick: offers_per_team_per_tick)"),
    threads: int = typer.Option(3, min=0, help="Direct proposals (swaps in a team thread)"),
    split: float = typer.Option(0.5, min=0.05, max=1.0, help="The most of the expected pie we ask for"),
    page_set: str = typer.Option("LAV", help="The set whose page buy list is drawn up"),
    cash_budget: int | None = typer.Option(
        None, min=0, help="The most bids and cash legs may promise (default: all the cash above cash_floor)"
    ),
    out: str = typer.Option(".local/night", help="Where trade-plan.json and trade-plan.md are written"),
) -> None:
    """Dry-run trade plan for the next opening, fair by construction; sends nothing.

    Listings and direct proposals priced on the rival affinity map, every one with surplus for us, no
    counterparty above `--share` of the planned volume, checked through the guardrails."""
    from pathlib import Path

    from bazaar_agent import affinity as af
    from bazaar_agent import trade_desk as td
    from bazaar_agent.agents.market import venues_from

    me = _payload_file(me_file) if me_file else _team_me()[1]
    public = None if (catalog_file and venues_file) else public_client(load_settings())
    catalog = _payload_file(catalog_file) if catalog_file else public.catalog()  # type: ignore[union-attr]
    venues = venues_from(_payload_file(venues_file) if venues_file else public.venues())  # type: ignore[union-attr]
    where = next((v for v in venues if v.id == venue), None)
    if where is None:
        _fail(f"venue {venue!r} is not in /api/venues")
    events = _history(events_file, live)
    us = str(me.get("id") or "")
    amap = af.affinity_map(events, af.catalog_sets(catalog), af.multipliers_from(me), catalog, exclude=[us])
    pp = td.PlanParams(listings, threads, share, split, page_set=page_set, cash_budget=cash_budget)
    rules = _rules().rules
    offers: list[dict[str, Any]] = []
    spent = 0
    if not me_file:  # live inputs: what our open offers and this game hour's spend already promise
        now = Clock.model_validate(public_client(load_settings()).clock())
        offers = _my_offers(_team_client())
        spent = _ledger("trade-plan").spent_since(now.t_hours - 1.0)
    plan = td.build_plan(me, catalog, events, amap, _strategy().params, rules, pp, where, offers, spent)
    folder = Path(out) if Path(out).is_absolute() else REPO_ROOT / out
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trade-plan.json").write_text(json.dumps(td.plan_dict(plan, venue), indent=2, default=str) + "\n")
    (folder / "trade-plan.md").write_text(td.plan_markdown(plan, pp))
    floor = plan.expected_at(pp.friday_public_fill, pp.friday_addressed_fill)
    console.print(
        f"{len(plan.listings)} listing(s) + {len(plan.threads)} proposal(s), expected {plan.expected:+.1f} P "
        f"if each fills when its counterparty values it ({floor:+.1f} P at Friday's fill rates; without the "
        f"share rule {plan.unconstrained:+.1f} P), largest share {max(plan.shares.values(), default=0):.0%} "
        f"(worst case {plan.worst_share:.0%}), {len(plan.checks)} check(s) failing · written to {folder}"
    )
    for check_line in plan.checks:
        console.print(f"[red]{check_line}[/red]")
    for line in plan.what_if:
        console.print(f"[yellow]if the cap were on: {line}[/yellow]")


@app.command()
def swaps(
    live: bool = typer.Option(False, help=LIVE_HELP),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file; else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    venues_file: str | None = typer.Option(None, "--venues", help="/api/venues from a file; else the API"),
    threads: int = typer.Option(4, min=1, help="Swaps to plan"),
    as_json: bool = typer.Option(False, "--json", help="Print the swaps as JSON"),
) -> None:
    """Read-only: the swaps the taker's team desk would propose in team threads (N17), sends nothing.

    Each planned swap (the trade desk's, on the rival affinity map) with its ladder from the anchor to the
    even split, our gain and theirs at each step, and the fairness verdict (`swaps.judge`)."""
    from bazaar_agent import affinity as af
    from bazaar_agent import swaps as sw
    from bazaar_agent import trade_desk as td
    from bazaar_agent.agents.team_desk import spare_copy

    me, catalog, venues = _offline_inputs(me_file, catalog_file, venues_file)
    events, us, rules = _history(events_file, live), str(me.get("id") or ""), _rules().rules
    amap = af.affinity_map(events, af.catalog_sets(catalog), af.multipliers_from(me), catalog, exclude=[us])
    rastro = next((v for v in venues if v.id == "rastro"), None)
    pp = td.PlanParams(listings=0, threads=threads, max_share=1.0)  # as the desk plans (team_desk._trades)
    offers = _my_offers(_team_client()) if not me_file else []  # what our open offers already promise
    plan = td.build_plan(me, catalog, events, amap, _strategy().params, rules, pp, rastro, offers)
    ladder, rows = sw.Ladder(), []
    for t in plan.threads:
        if spare_copy(me, offers, us, t.refs[0]) is None:
            continue  # the desk gives only a free duplicate (team_desk.spare_copy)
        steps = [sw.cash_at(t, k, ladder) for k in range(ladder.steps)]
        verdicts = [sw.judge(t, c, 0, rules) for c in steps]
        rows.append(
            {
                "team": t.counterparty,
                "give": t.refs[0],
                "asset": t.asset_id,
                "want": t.refs[1],
                "cash_steps": steps,  # + they add, - we add
                "ours": [round(v.ours, 1) for v in verdicts],
                "theirs": [round(v.theirs, 1) for v in verdicts],
                "fair": [v.ok for v in verdicts],
                "p_fill": t.p_fill,
                "first_offer": sw.offer_terms(t, steps[0]),
            }
        )
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        console.print("no swap planned (no team known to hold a card we miss and to want one of our copies)")
    for r in rows:
        console.print(
            f"{r['team']}: our {r['give']} (#{r['asset']}) for their {r['want']} · cash {r['cash_steps']} · "
            f"ours {r['ours']} · theirs {r['theirs']} · fair {r['fair']} · P(fill) {r['p_fill']:.0%}"
        )


@app.command("team-checks")
def team_checks(as_json: bool = typer.Option(False, "--json", help="Print the answers as JSON")) -> None:
    """Read-only: the N17 spec's Q1-Q6 answered from the shared DB (the feed, our refused sends, thread offers)
    and the go/no-go for team_threads_enabled. SELECTs in a read-only transaction; nothing is sent."""
    from dataclasses import asdict

    from rich.markup import escape

    from bazaar_agent import db
    from bazaar_agent import n17_checks as nc

    events = _history(None, False)  # the shared DB first, as the agents read it
    us = _our_team() or ""
    refusals: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    try:
        with db.connect(app="bazaar-team-checks") as conn:
            conn.read_only = True  # SELECTs only: any write raises
            cur = conn.execute("select sdk_method, error_code, tick from executions where error_code is not null")
            refusals = [{"sdk_method": m, "error_code": c, "tick": t} for m, c, t in cur.fetchall()]
            # Our thread offers as #148's thread store keeps them (the `offers` table is not written by the agents).
            cur = conn.execute(
                "select (m.offer->>'id')::bigint, m.thread_id, m.offer->>'maker', m.offer->>'status' from messages m"
                " join threads t on t.id = m.thread_id where t.ours and m.offer is not null"
            )
            offers = [{"id": i, "thread_id": t, "maker": m, "status": st} for i, t, m, st in cur.fetchall()]
    except Exception as e:  # noqa: BLE001 — never the URL: a connect error can echo it (.ai/memory.md)
        err_console.print(f"[yellow]no database ({type(e).__name__}): the feed alone answers[/yellow]")
    found = nc.answers(events, us, refusals, offers)
    verdict, why = nc.go_no_go(found)
    if as_json:
        typer.echo(json.dumps({"answers": [asdict(a) for a in found], "go_no_go": verdict, "why": why}, indent=2))
        return
    for a in found:
        console.print(f"{a.q} · {a.verdict} · {a.question} · {escape(a.evidence)}", highlight=False)  # no [..]: markup
    console.print(f"team_threads_enabled: {verdict} · {why}")


def _offline_inputs(me_file: str | None, catalog_file: str | None, venues_file: str | None) -> tuple[Any, Any, Any]:
    """(/api/me, /api/catalog, venues): each from its file when given, else from the API (reads only)."""
    from bazaar_agent.agents.market import venues_from

    me = _payload_file(me_file) if me_file else _team_me()[1]
    public = None if (catalog_file and venues_file) else public_client(load_settings())
    catalog = _payload_file(catalog_file) if catalog_file else public.catalog()  # type: ignore[union-attr]
    venues = venues_from(_payload_file(venues_file) if venues_file else public.venues())  # type: ignore[union-attr]
    return me, catalog, venues


@app.command()
def rivals(
    live: bool = typer.Option(False, help=LIVE_HELP),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file (the multiset); else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    as_json: bool = typer.Option(False, "--json", help="Print the profiles as JSON"),
) -> None:
    """Rival behaviour profiles: pricing against the tape and own value, fills, takes, reprices."""
    from dataclasses import asdict

    from bazaar_agent import affinity as af
    from bazaar_agent import rivals as rv

    me = _payload_file(me_file) if me_file else _team_me()[1]
    catalog = _payload_file(catalog_file) if catalog_file else public_client(load_settings()).catalog()
    events = _history(events_file, live)
    us = str(me.get("id") or "")
    amap = af.affinity_map(events, af.catalog_sets(catalog), af.multipliers_from(me), catalog, exclude=[us])
    found = rv.profiles(rv.listings(events), events, amap, catalog, exclude=[us])
    if as_json:
        rows = {t: {**asdict(p), "tags": p.tags} for t, p in found.items()}
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    console.print(render.rivals_table(list(found.values())))


@app.command()
def buyers(
    cards: Annotated[
        list[str] | None, typer.Option("--card", help="Rank buyers for this card (repeat); default: our duplicates")
    ] = None,
    live: bool = typer.Option(False, help=LIVE_HELP),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file; else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    board_file: str | None = typer.Option(None, "--leaderboard", help="/api/leaderboard from a file; else the API"),
    scan: bool = typer.Option(True, help="Read the stored card scan from Postgres (holders of each card)"),
    save: bool = typer.Option(False, "--save", help="Store the ranking in Postgres (team_buyer_rank)"),
    as_json: bool = typer.Option(False, "--json", help="Print card -> ranked rows as JSON"),
) -> None:
    """Read-only: the other teams ranked as buyers of each card (willingness, interest, need, rivals, blocks)."""
    from dataclasses import asdict

    from rich.markup import escape

    from bazaar_agent import buyers_db as bd
    from bazaar_agent import db

    me = _payload_file(me_file) if me_file else _team_me()[1]
    public = None if (catalog_file and board_file) else public_client(load_settings())
    catalog = _payload_file(catalog_file) if catalog_file else public.catalog()  # type: ignore[union-attr]
    board = _payload_file(board_file) if board_file else public.leaderboard()  # type: ignore[union-attr]
    events = _history(events_file, live)
    stored, note = bd.stored_scan(lambda: db.connect(app="bazaar-buyers", connect_timeout_s=3)) if scan else ([], None)
    if note:
        err_console.print(f"[yellow]{escape(note)}[/yellow]")
    picked = cards or bd.duplicates(me)
    if not picked:
        err_console.print("no card to rank (no duplicates in /api/me; pass --card)")
    ranked = bd.rank_cards(picked, me=me, catalog=catalog, events=events, board=board, scan=stored)
    if as_json:
        typer.echo(json.dumps({c: [asdict(r) for r in rows] for c, rows in ranked.items()}, indent=2))
    else:
        for card, rows in ranked.items():
            console.print(bd.table(card, rows) if rows else f"{escape(card)}: not in the catalog")
    if save:
        tick = int(me.get("tick") or max((int(e.get("tick") or 0) for e in events), default=0))
        try:
            with db.connect_ready("bazaar-buyers") as conn:
                n = bd.save(conn, ranked, tick)
        except Exception as e:  # noqa: BLE001 (printed redacted: a connect error can echo the password)
            err_console.print(f"[red]not saved: {escape(bd.safe_error(e))}[/red]")
            raise typer.Exit(1) from None
        err_console.print(f"saved {n} rows for {len(ranked)} cards to Postgres (team_buyer_rank)")


@app.command()
def opportunities(
    live: bool = typer.Option(
        False, help="Read the venue boards now (public reads); else the board rebuilt from the feed"
    ),
    events_file: str | None = typer.Option(None, "--events", help="Read the feed from this JSONL file instead"),
    me_file: str | None = typer.Option(None, "--me", help="Our /api/me from a file; else the API"),
    catalog_file: str | None = typer.Option(None, "--catalog", help="The catalog from a file; else the API"),
    venues_file: str | None = typer.Option(None, "--venues", help="/api/venues from a file; else the API"),
    replay_day: bool = typer.Option(False, "--replay", help="Also score every offer the feed ever showed"),
    min_surplus: float = typer.Option(0.0, help="Only offers worth more than this to us"),
    as_json: bool = typer.Option(False, "--json", help="Print the opportunities as JSON"),
) -> None:
    """Read-only scanner: standing offers ranked by what accepting them gains us, guardrails checked."""
    from dataclasses import asdict

    from bazaar_agent import affinity as af
    from bazaar_agent import guardrails as gr
    from bazaar_agent import opportunities as op
    from bazaar_agent import rivals as rv
    from bazaar_agent import trade_desk as td
    from bazaar_agent.agents.market import board_offers, tradable_venues
    from bazaar_agent.agents.seller import committed_context, open_commitments, trade_book
    from bazaar_agent.strategy import build_market

    me, catalog, venues = _offline_inputs(me_file, catalog_file, venues_file)
    events = _history(events_file, live)
    us = str(me.get("id") or "")
    rules, params = _rules().rules, _strategy().params
    amap = af.affinity_map(events, af.catalog_sets(catalog), af.multipliers_from(me), catalog, exclude=[us])
    m = build_market(me, catalog, events, [])
    rows = rv.listings(events)
    last = max((int(e.get("tick") or 0) for e in events), default=0)
    if live:
        public, makers = public_client(load_settings()), intel.listed_makers(events)
        offers = []
        for v in tradable_venues(venues, us):
            offers += [replace(o, maker=makers.get(o.id, o.maker)) for o in board_offers(public.board(v.id), v.id, us)]
    else:
        offers = op.offers_from(rv.board_at(rows, last), us)
    # Our open offers (their cash, cards and exposure) whenever /me comes from the API: never check blind.
    mine = _my_offers(_team_client()) if not me_file else []
    held: dict[str, int] = {}
    for a in me.get("assets") or []:
        if a.get("kind") == "card":
            held[str(a.get("ref"))] = held.get(str(a.get("ref")), 0) + 1
    base = gr.Context(int(me.get("cash") or 0), held, last, 0.0)
    if not me_file:  # with our key: the shared ledger's spend this game hour, as the taker sees it
        now = Clock.model_validate(public_client(load_settings()).clock())
        base = gr.context_from(me, now.tick, now.t_hours, _ledger("opportunities"), rules)
    book = intel.book_values(catalog)
    ctx = replace(
        committed_context(base, open_commitments(mine, us)),
        trades=trade_book(mine, us, intel.settled_volume(events, us, book), book),
    )
    by_id = {v.id: v for v in venues}
    rastro = by_id.get("rastro")
    plan = td.build_plan(me, catalog, events, amap, params, rules, td.PlanParams(), rastro, mine)
    found = op.scan(
        offers,
        m,
        me,
        params,
        rules,
        amap,
        by_id,
        ctx,
        events,
        catalog,
        [*plan.listings, *plan.threads],
        min_surplus,
        unavailable=open_commitments(mine, us).listed,
    )
    if as_json:
        typer.echo(json.dumps([asdict(o) for o in found], indent=2, default=str))
    else:
        where = "the live boards" if live else f"the board rebuilt from the feed at tick {last}"
        console.print(render.opportunities_table(found, f"Opportunities on {where} ({len(offers)} offers)"))
    if replay_day:
        rp = op.replay(rows, m, me, params, rules, amap, by_id, ctx, events, min_surplus)
        latency = sorted(rp.latency)
        console.print(
            f"replay: {rp.offers} offers by other teams, {len(rp.worth_it)} worth it to us (+{rp.surplus} P, one"
            f" per offer); {rp.taken_by_others} taken by others (ticks {latency}, by {rp.takers}); "
            f"{rp.left_open} left untaken (+{rp.left_surplus} P)"
        )


@app.command()
def book(
    venue: str = typer.Option("rastro", help="Venue id"),
    card: str | None = typer.Option(None, help="Filter by card ref, e.g. LAV-04"),
    include_us: bool = typer.Option(False, "--include-us", help="Mix our own offers into the book"),
) -> None:
    """Live order book of a venue, with board pseudonyms resolved to team ids from the feed. Ours apart."""
    client = public_client(load_settings())
    board = client.board(venue).get("offers") or []
    lines = intel.order_book(board, intel.listed_makers(_events(live=True)))
    if card:
        lines = [b for b in lines if b.card == card]
    us = _our_team()
    if include_us:
        console.print(render.book_table(lines, venue, us=us))
        return
    theirs, ours = intel.split_us(lines, us, lambda b: b.maker)
    console.print(render.book_table(theirs, venue))
    if ours:
        console.print(render.book_table(ours, venue, "Our offers", us=us))


# ---------------------------------------------------------------- our team (needs BAZAAR_KEY)


@app.command()
def status(
    cards: bool = typer.Option(True, help="Also list our cards with your_value"),
    db_snapshot: bool = typer.Option(
        True, "--db/--no-db", help="Answer from the shared Postgres snapshot when current"
    ),
) -> None:
    """Our cash, level, score, album pages with missing cards, and cards (GET /api/me, or its current snapshot)."""
    settings = load_settings()
    try:
        read = _holdings_read(settings, db_snapshot)
    except ConfigError as e:
        _fail(str(e))
    except BazaarError as e:
        _fail(f"/api/me refused: {e.code} ({e.status})")
    me: dict[str, Any] = read.me
    console.print(render.status_table(me, settings.target_line(), snapshot=read.line()))
    from bazaar_agent.album import album_view

    console.print(render.album_table(album_view(me, public_client(settings).catalog())))
    if cards:
        console.print(render.cards_table(me))


def _holdings_read(settings: Settings, from_db: bool = True) -> Any:
    """Album first through the shared holdings: the Postgres snapshot while provably current, else /api/me."""
    from bazaar_agent import holdings as hd
    from bazaar_agent.holdings import SharedDb

    hd.name_process("cli")
    team = team_client(settings) if from_db else team_client(settings, track=False)  # --no-db: no connection
    try:
        clock: Clock | None = Clock.model_validate(public_client(settings).clock())
    except BazaarError:
        clock = None  # unknown tick: a live read
    team_id = resolve_team_id(settings.team_id, settings.data_dir, None)
    rules = _rules().rules
    if not from_db:
        return hd.Holdings(team.me, SharedDb(None), reader="cli", rules=rules, scope=hd.scope_of(settings)).me(clock)
    remember = partial(remember_team_id, settings.data_dir)
    return hd.for_process(team.me, rules, settings, team=team_id, on_team=remember).me(clock)


def _team_read(read: Callable[[Any], Any]) -> Any:
    """One read with our team key; a missing key or a refusal ends the command with its reason."""
    try:
        return read(team_client(load_settings()))
    except ConfigError as e:
        _fail(str(e))
    except BazaarError as e:
        _fail(f"refused: {e.code} ({e.status})")


@app.command("threads")
def threads_cmd(
    status: str | None = typer.Option(None, help="Only this status: open | deal | walked | closed | cooloff"),
    as_json: bool = typer.Option(False, "--json", help="JSON for the UI team instead of a table"),
) -> None:
    """Our negotiation threads (GET /api/me/threads): who, what, status and the last message."""
    from pydantic import ValidationError

    from bazaar_agent.conversation import Thread, summary_json

    data = _team_read(lambda c: c.my_threads(status))
    try:
        views = [Thread.model_validate(t) for t in data.get("threads") or []]
    except ValidationError as e:
        _fail(f"/api/me/threads has an unexpected shape: {e.error_count()} problem(s)")
    if as_json:
        typer.echo(json.dumps({"threads": [summary_json(v) for v in views]}, ensure_ascii=False, indent=2))
        return
    console.print(render.threads_list_table(views))


@app.command("thread")
def thread_cmd(
    thread_id: int = typer.Argument(help="Thread id, e.g. 115"),
    as_json: bool = typer.Option(False, "--json", help="JSON for the UI team instead of a table"),
) -> None:
    """One whole conversation (GET /api/threads/{id}): every message with sender, text and price."""
    from pydantic import ValidationError

    from bazaar_agent.conversation import Thread, conversation_json

    try:
        view = Thread.model_validate(_team_read(lambda c: c.thread(thread_id)))
    except ValidationError as e:
        _fail(f"thread {thread_id} has an unexpected shape: {e.error_count()} problem(s)")
    traces.trace_thread(view)
    if as_json:
        typer.echo(json.dumps(conversation_json(view), ensure_ascii=False, indent=2))
        return
    console.print(render.thread_table(view))


# ---------------------------------------------------------------- dealers (writes: needs BAZAAR_KEY)


DEALER_REOPENS = 1  # new threads after she held her opening ask (each with a lower first bid)


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
    """Buy one card or pack from a dealer: rising distinct bids, hard max, never at her opening ask."""
    from rich.markup import escape

    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.dealer import BidPlan, Hold, bid_schedule, negotiate, template_words
    from bazaar_agent.agents.seller import committed_context, offers_in, open_commitments
    from bazaar_agent.ledger_pg import LedgerUnavailable

    rules = _rules().rules
    plan = BidPlan(start, step, max_price)
    topic = {"buy": {"pack": item}} if "-" not in item else {"buy": {"card": item}}
    rarity = _rarity_of(item)
    cap = rules.max_price_for(rarity)
    if cap is not None and max_price > cap:
        _fail(f"--max {max_price} is above max_price_{rarity} = {cap} in GUARDRAILS.md")
    if not live:
        schedule = bid_schedule(plan)
        console.print(
            f"[yellow]dry run[/yellow] {dealer} {topic}: bids {schedule}; take her ask only once she came down "
            f"from her opening (≤ our next bid), counter below an opening ask, walk above {max_price}; if she "
            f"holds her opening, walk and reopen once lower. Add --live to trade."
        )
        return
    settings = load_settings()
    client = team_client(settings)
    ledger = _ledger("dealer-buy", live=True)
    values = OfficialValues.of(client)  # every bid and accept capped at GET /api/me/value (Day-2 hint 1)

    def committed(c: Clock, thread_id: int | None = None) -> gr.Context:
        """/me + the shared ledger + every open offer of ours (the maker's bids, the taker's dealer threads),
        except this command's own thread, whose bid the next move replaces."""
        me = client.me()
        offers = [o for o in offers_in(client.my_offers()) if thread_id is None or o.get("thread") != thread_id]
        base = gr.context_from(me, c.tick, c.t_hours, ledger, rules, values)
        return committed_context(base, open_commitments(offers, str(me.get("id") or "")))

    clock_now = Clock.model_validate(client.clock())
    try:
        pre = gr.check(gr.Action("buy", item, rarity, start), committed(clock_now), rules)
    except LedgerUnavailable as e:
        _fail(f"refusing to trade: {e}; no write without the shared ledger (fail closed)")
    if not pre.allowed:
        tm.guardrail_refusal("dealer.open", item, pre.violations)
        _fail(f"guardrails refuse to open this thread: {pre}")
    plan = _forgiving_plan(settings, rules, dealer, item, rarity, plan, client)  # a trickster's FINAL is not its limit

    def guard(move: Any, thread_id: int) -> str | None:
        """A ledger failure holds the move (nothing sent, the thread stays open, next tick decides again):
        no write without the shared ledger, and no walk because it blinked."""
        try:
            return checked(move, thread_id)
        except LedgerUnavailable as e:
            raise Hold(f"{e}; no write without the shared ledger (fail closed)") from None

    def checked(move: Any, thread_id: int) -> str | None:
        # The accept quota is no reason to walk: `reserve` claims it atomically on the tick the accept
        # is sent, and a full quota makes the accept wait for the next tick.
        ctx = replace(committed(Clock.model_validate(client.clock()), thread_id), accepts_this_tick=0)
        kind: gr.ActionKind = "accept_buy" if move.kind == "accept" else "bid"
        verdict = gr.check(gr.Action(kind, item, rarity, move.price), ctx, rules)
        return None if verdict.allowed else "; ".join(verdict.violations)

    def reserve(move: Any, c: Clock) -> bool:
        return _reserve_accept(ledger, rules, item, move, c)

    def on_deal(price: int, tick: int, t_hours: float) -> None:
        try:
            ledger.record("spend", tick, t_hours, price, item)
        except LedgerUnavailable as e:  # the deal is done: say what the team-wide spend cap misses
            console.print(f"[red]deal at {price} P done, but the shared ledger did not record its spend ({e})[/red]")
            return
        tm.event("ledger", {"kind": "spend", "tick": tick, "price": price, "item": item})

    advisor = _jev_advisor(item, settings, rules.jev_timeout_s) if jev and rules.jev_can_accept_early else None
    us = _our_team_id(client)
    shared = ledger.where.startswith("postgres")
    store = _learning_store("bazaar-dealer-buy", console.print) if shared else None
    # Without our team id a strike for us cannot be recognised: no tactics then, today's words only.
    bluff = _tactic_book(rules, store, us, console.print, None if us else "our team id is unknown")
    inspector = _offer_inspector(settings, dealer, topic, rules)  # S1: one flag book across reopens
    for attempt in range(1 + DEALER_REOPENS):
        with traces.trace_negotiation(dealer, topic, plan) as observer:
            out = negotiate(
                client,
                dealer,
                topic,
                plan,
                log=lambda line: console.print(escape(line)),  # server and counterparty words: never markup
                advisor=advisor,
                guard=guard,
                on_deal=on_deal,
                max_ticks=rules.dealer_max_ticks_per_thread,
                observer=observer,
                words_fn=llm_cli.words_for(settings, rules, template_words),
                reserve=reserve,
                kill_switch=lambda: gr.kill_switch(rules),
                **inspector,
                bluff=bluff,
                events=_feed_reader(settings),
                jev_min_share=rules.jev_accept_min_share,
            )
        if out.reopen_start is None or attempt == DEALER_REOPENS:
            break
        if stops := gr.kill_switch(rules):  # opening a thread is a write: no reopen while the switch is on
            console.print(f"she held her opening ask; not reopening: kill switch on ({'; '.join(stops)})")
            break
        console.print(f"she held her opening ask on thread {out.thread}: reopening lower, first bid {out.reopen_start}")
        plan = replace(plan, start=out.reopen_start)
    colour = "green" if out.status == "deal" else "red"
    console.print(
        f"[{colour}]{out.status}[/{colour}] thread {out.thread} price {out.price} bids {list(out.bids)} "
        f"in {out.ticks} ticks"
    )


@dealer_app.command("sell")
def dealer_sell(
    ref: str = typer.Argument(help="Card ref we sell, e.g. MAL-02 (the copy we lose least by selling)"),
    floor: int = typer.Option(..., "--min", min=1, help="Hard floor: never sell below this (≥ the copy's your_value)"),
    start: int = typer.Option(..., help="Opening ask"),
    step: int = typer.Option(1, min=1, help="Drop per tick (small steps earn small steps)"),
    dealer: str = typer.Option(
        "abuela", help="Dealer id: abuela, chato, pilar, ... (its menu must buy this rarity and set)"
    ),
    live: bool = typer.Option(False, help="Actually trade. Without it: dry run, nothing is sent"),
) -> None:
    """Sell one duplicate to a dealer (a ladder deal): falling distinct asks, hard floor, never at her opening bid."""
    from rich.markup import escape

    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import publication
    from bazaar_agent.agents.dealer import Hold
    from bazaar_agent.agents.dealer_sell import (
        AskPlan,
        SellRefused,
        ask_schedule,
        check_floor,
        copy_to_sell,
        dealer_refusal,
        negotiate_sell,
        only_copy,
        sell_topic,
    )
    from bazaar_agent.agents.runtime import Recorder
    from bazaar_agent.agents.seller import committed_context, offers_in, open_commitments
    from bazaar_agent.decisions import DecisionLog, Status
    from bazaar_agent.ledger_pg import LedgerUnavailable, trade_lock
    from bazaar_agent.official_values import unread_only

    rules = _rules().rules
    settings = load_settings()
    client, me = _team_me()  # album first: the copy, its your_value and how many we hold, from /api/me
    mine = open_commitments(_my_offers(client), str(me.get("id") or ""))  # copies our asks give
    try:
        asset = copy_to_sell(me, ref, mine.listed, mine.unnamed_listed)
        your_value = float(asset["your_value"])
        check_floor(floor, your_value)
        plan = AskPlan(start, step, floor)
    except (SellRefused, ValueError) as e:
        _fail(str(e))
    rarity, asset_id = asset.get("rarity"), int(asset["id"])
    personas = public_client(settings).dealers().get("personas") or []
    if refusal := dealer_refusal(dealer, personas, me, asset):
        _fail(refusal)
    topic = sell_topic(asset_id)
    if not live:
        console.print(
            f"[yellow]dry run[/yellow] {dealer} {topic} ({ref}, your_value {your_value:g}): asks "
            f"{ask_schedule(plan)}; take her bid once she came up from her opening and it meets our next ask, "
            f"never below {floor}. Add --live to trade."
        )
        return
    ledger = _ledger("dealer-sell", live=True)
    reservation: str | None = None
    unavailable_asset = False

    def committed(c: Clock, thread_id: int | None = None) -> gr.Context:
        """/me + the shared ledger + every open offer of ours except this thread's own ask."""
        nonlocal your_value, unavailable_asset
        me_now = client.me()
        current = next((a for a in me_now.get("assets", []) if a.get("id") == asset_id), None)
        if current is not None:
            your_value = float(current["your_value"])
        offers = publication.with_pending(
            ledger, me_now, offers_in(client.my_offers()), str(me_now.get("id") or ""), c.tick, c.t_hours
        )
        offers = [
            o
            for o in offers
            if (thread_id is None or o.get("thread") != thread_id)
            and (reservation is None or o.get("token") != reservation)
        ]
        base = gr.context_from(me_now, c.tick, c.t_hours, ledger, rules)
        commitments = open_commitments(offers, str(me_now.get("id") or ""))
        unavailable_asset = current is None or asset_id in commitments.listed
        return committed_context(base, commitments)

    def action(kind: gr.ActionKind, price: int | None) -> gr.Action:
        # a dealer sell thread; `asset`: the score impact rule prices this copy
        return gr.Action(kind, ref, rarity, price, your_value=your_value, scope="dealer_sell", asset=asset_id)

    def checked(kind: gr.ActionKind, price: int | None, ctx: gr.Context) -> gr.Verdict:
        """guardrails.check plus the last uncommitted copy of a page card (any page, not only new ones)."""
        verdict = gr.check(action(kind, price), ctx, rules)
        if unavailable_asset:
            return gr.Verdict(False, (*verdict.violations, "selected copy is missing or already promised"))
        if only_copy(ref, rarity, (ctx.sellable or {}).get(ref, 0)):
            why = f"{ref}: the last copy not on an open offer of ours (sellable {(ctx.sellable or {}).get(ref, 0)})"
            return gr.Verdict(False, (*verdict.violations, why))
        return verdict

    try:
        with trade_lock(ledger):
            current_clock = Clock.model_validate(client.clock())
            pre = checked("sell", floor, committed(current_clock))
            if pre.allowed:
                reservation = publication.reserve(
                    ledger,
                    current_clock.tick,
                    current_clock.t_hours,
                    str(me.get("id") or ""),
                    {"assets": [asset_id]},
                    {"cash": floor},
                )
    except LedgerUnavailable as e:
        _fail(f"refusing to trade: {e}; no write without the shared ledger (fail closed)")
    if not pre.allowed:
        tm.guardrail_refusal("dealer.open", ref, pre.violations)
        _fail(f"guardrails refuse to open this thread: {pre}")

    def guard(move: Any, thread_id: int) -> str | None:
        """A ledger failure holds the move (nothing sent, decided again next tick), never a walk."""
        try:
            with trade_lock(ledger):
                ctx = replace(committed(Clock.model_validate(client.clock()), thread_id), accepts_this_tick=0)
                verdict = checked("accept_sell" if move.kind == "accept" else "sell", move.price, ctx)
        except LedgerUnavailable as e:
            raise Hold(f"{e}; no write without the shared ledger (fail closed)") from None
        if not verdict.allowed and unread_only(verdict.violations):  # approvals unreadable: hold, never walk
            raise Hold("; ".join(verdict.violations))
        return None if verdict.allowed else "; ".join(verdict.violations)

    decisions = DecisionLog(
        settings.data_dir, _db_connect("bazaar-dealer-sell") if ledger.where.startswith("postgres") else None
    )
    rec = Recorder("dealer-sell", decisions, True, lambda line: None)  # negotiate_sell prints its own lines

    def on_move(move: Any, tick: int, outcome: str) -> None:
        """One decision row per move we decided to send (or that a guard stopped)."""
        kind = {"bid": "dealer_ask", "accept": "dealer_accept", "walk": "dealer_walk"}.get(move.kind, "dealer_wait")
        denied = outcome.startswith("denied")
        status: Status = {"sent": "done", "held": "approved"}.get(outcome, "rejected" if denied else "failed")  # type: ignore[assignment]
        decisions.begin_tick(tick)
        rec.decide(
            tick,
            kind,
            f"{dealer} {ref} {move.kind} {move.price or ''}",
            inputs={"dealer": dealer, "ref": ref, "asset": asset_id, "floor": floor, "your_value": your_value},
            reason=move.reason,
            guardrail=outcome if denied else "allowed",
            chosen=True,
            status=status,
            move={"kind": move.kind, "price": move.price, "offer": move.offer_id},
        )

    def on_deal(price: int, tick: int, t_hours: float) -> None:
        tm.event("dealer.sold", {"dealer": dealer, "ref": ref, "price": price, "tick": tick})

    try:
        out = negotiate_sell(
            client,
            dealer,
            asset_id,
            plan,
            log=lambda line: console.print(escape(line)),  # server and counterparty words: never markup
            max_ticks=rules.dealer_max_ticks_per_thread,
            guard=guard,
            reserve=lambda move, c: _reserve_accept(ledger, rules, ref, move, c),
            kill_switch=lambda: gr.kill_switch(rules),
            on_deal=on_deal,
            on_move=on_move,
            kind=_dealer_kind(settings, dealer),
            **_offer_inspector(settings, dealer, topic, rules),
        )
    finally:
        decisions.close()
    if reservation is not None and (
        out.status in {"closed", "walked", "timeout"} or (out.status == "held" and out.thread is None)
    ):
        with trade_lock(ledger):
            c = Clock.model_validate(client.clock())
            publication.release(ledger, reservation, c.tick, c.t_hours)
    colour = "green" if out.status == "deal" else "red"
    console.print(
        f"[{colour}]{out.status}[/{colour}] thread {out.thread} price {out.price} asks {list(out.bids)} "
        f"in {out.ticks} ticks"
    )
    if out.status == "deal":  # album first: re-read what we hold after every deal
        console.print(f"cash now {client.me().get('cash')} P")


def _offer_inspector(settings: Any, dealer: str, topic: dict[str, Any], rules: Any) -> dict[str, Any]:
    """`negotiate`'s offer inspector (S1): the would-flag log on every thread read and the accept gate.
    No flag is sent from here; with `inspect_accepts` false only the older structure check runs."""
    from rich.markup import escape

    from bazaar_agent.agents.accept_gate import dealer_gate
    from bazaar_agent.agents.injection_tags import INJECTIONS_FILE, InjectionTags, latest_message
    from bazaar_agent.agents.inspector import CardIndex, FlagBook, flag_step

    try:
        cards = CardIndex.from_catalog(public_client(settings).catalog())
    except BazaarError as e:  # without names the gate still refuses a swap; it only cannot grade it a flag
        console.print(f"[yellow]catalog refused {e.code}: the inspector reads structure only[/yellow]")
        cards = CardIndex.from_catalog({})
    book = FlagBook.from_rules(rules)
    tags = InjectionTags(settings.data_dir / "agents" / INJECTIONS_FILE)

    def log(line: str) -> None:
        console.print(escape(f"inspector: {line}"))

    def on_thread(thread: dict[str, Any]) -> None:
        guard = lambda _: None if rules.allow_flags else "allow_flags = false"  # noqa: E731
        flag_step(thread, dealer, cards, book, guard=guard, send=None, log=log, topic=topic)
        mid, text = latest_message(thread, dealer)
        tags.tag(dealer, mid, text, None, log)  # `negotiate` keeps the tick; the row needs no more

    def inspect(thread: dict[str, Any], move: Any) -> str | None:
        gate = dealer_gate(thread, dealer, move.offer_id, move.price, topic, cards)
        return None if gate.allowed else f"{gate.verdict}: {gate.reason}"  # `negotiate`'s log escapes it

    return {"on_thread": on_thread, "inspect": inspect if rules.inspect_accepts else None}


def _dealer_personas(settings: Any) -> list[dict[str, Any]]:
    """`GET /api/dealers` (keyless): every dealer as it publishes itself (kind, traits, menu)."""
    body = public_client(settings).dealers()
    return [d for d in body.get("personas") or body.get("dealers") or [] if isinstance(d, dict)]


def _forgiving_plan(
    settings: Any, rules: Any, dealer: str, item: str, rarity: str | None, plan: Any, client: Any
) -> Any:
    """`dealer buy`'s plan against a forgiving dealer (agents/trickster.py): its FINAL is not its limit. Its persona
    comes from `/api/dealers`: unreadable, or the dealer not listed there, and nothing is opened (fail closed: a fake
    final could be taken as a limit). Its fills come from the feed history the agents read (`_history`), OTHER teams'
    only, as in the taker: our team id comes from BAZAAR_TEAM_ID, `.local/team_id` or one /me read, and unknown means
    nothing is opened. No fill known: its asks are never taken (we only bid). Every other dealer's plan is unchanged."""
    from rich.markup import escape

    from bazaar_agent.agents.trickster import forgiving_plan, is_forgiving, note
    from bazaar_agent.intel import tape
    from bazaar_agent.persona_model import parse_personas

    try:
        persona = parse_personas(_dealer_personas(settings)).get(dealer)
    except Exception as e:  # noqa: BLE001 — whatever failed, we cannot tell whether its final binds
        _fail(f"refusing to trade: /api/dealers unreadable ({type(e).__name__}): is {dealer}'s FINAL its limit?")
    if persona is None:
        _fail(f"refusing to trade: {dealer} is not listed in /api/dealers: is its FINAL its limit?")
        return plan  # not reached: `_fail` exits
    if not is_forgiving(persona, rules):
        return plan
    us = resolve_team_id(settings.team_id, settings.data_dir, client.me, lambda m: console.print(escape(m)))
    if us is None:  # our own buys would count in its range (#228 review: our 63 made 63 acceptable)
        _fail(f"refusing to trade: our team id is unknown, so our own fills cannot be left out of {dealer}'s range")
    try:
        events = _history(None, live=True)
    except Exception as e:  # noqa: BLE001 — no fill known: its asks are never taken
        console.print(escape(f"feed unreadable ({type(e).__name__}): no fill known for {dealer}, we only bid"))
        events = []
    shaped = forgiving_plan(plan, persona, item, rarity, tape(events), rules, us)
    console.print(escape(f"{dealer} forgives (kind {persona.kind}): {note(shaped)}"))
    return shaped


def _rules() -> Any:
    from bazaar_agent.guardrails import GuardrailsError, load_guardrails

    try:
        return load_guardrails()
    except GuardrailsError as e:
        _fail(f"GUARDRAILS.md is invalid, refusing to trade: {e}")


def _rarity_of(item: str) -> str | None:
    if "-" not in item:
        return "pack"
    for s in public_client(load_settings()).catalog().get("sets") or []:
        for c in s.get("cards") or []:
            if c.get("id") == item:
                return str(c.get("rarity"))
    return None


def _jev_advisor(item: str, settings: Any, timeout_s: float = 3.0) -> Any:
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
        result = judge(state, move_q, api_key=key, timeout_s=timeout_s)
        tm.record_jev(result, "negotiation_move")
        verdict = result.verdicts["negotiation_move"]
        console.print(f"  jev: {verdict.verdict} ({verdict.value:.2f})")
        return verdict.verdict if verdict.decided else None

    return advise


# ---------------------------------------------------------------- duels (needs BAZAAR_KEY)


@duel_app.command("run")
def duel_run(
    play: bool = typer.Option(False, help="Send offers/accepts. Without it: log only"),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    jev: bool = typer.Option(True, help="Jev duel_move picks among the legal moves (undecided: today's move)"),
    evals_every: int | None = typer.Option(None, "--evals-every", min=0, help=EVALS_EVERY_HELP),
) -> None:
    """Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit."""
    from rich.markup import escape

    from bazaar_agent import db as _db
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.accept_gate import DuelRereads, Gate, duel_accept_check
    from bazaar_agent.agents.bluff import message_id
    from bazaar_agent.agents.duel_days import effective_rules, latch, reads_done, real_game
    from bazaar_agent.agents.duel_jev import DuelPick, forced_pick
    from bazaar_agent.agents.duel_v2 import V2Params, first_offer_wait, payload_start, plan_moves
    from bazaar_agent.agents.duelist import (
        DuelMove,
        append_jsonl,
        duel_action,
        duel_choice,
        duel_deadline,
        duel_id,
        duel_move,
        observe_duel,
        our_duel_messages,
        rival_offer,
        rival_text,
        spoke_this_tick,
        template_duel_words,
    )
    from bazaar_agent.agents.injection_tags import INJECTIONS_FILE, InjectionTags
    from bazaar_agent.agents.runtime import Recorder, release_refused_accept
    from bazaar_agent.agents.words import WordsRequest
    from bazaar_agent.decisions import DecisionLog, Status
    from bazaar_agent.duel_store import DuelStore, duel_list
    from bazaar_agent.ledger_pg import LedgerUnavailable
    from bazaar_agent.llm.steering import STEERING_FILE, steered_duel_params

    rules = _rules().rules
    settings = load_settings()
    client = team_client(settings)
    ledger = _ledger("duels", live=play)
    duel_jev = _duel_jev(settings, rules) if jev else None
    # Decision rows go to Postgres only when the ledger reached it: a dead host must not stall a duel tick.
    decisions = DecisionLog(
        settings.data_dir, _db_connect("bazaar-duels") if ledger.where.startswith("postgres") else None
    )
    rec = Recorder("duels", decisions, play, lambda line: None)  # the duel loop prints its own lines
    log_path = settings.data_dir / "duels" / "duels.jsonl"
    store = DuelStore(
        _db_connect("bazaar-duels") if ledger.where.startswith("postgres") else None,
        lambda m: console.print(f"[dim]{escape(m)}[/dim]"),
    )
    days_switch = latch(settings.data_dir)  # the sign of your_days_weight, from the first real payload (B8)
    done_every_ticks = 10  # while the sign is open, read the finished duels this often (one extra GET)
    days_failed: list[int] = []  # the last tick the latch failed: its line is printed once per tick
    real = real_game(settings.bazaar_url)  # from the base URL: the simulator's days_meaning is never evidence
    first_seen: dict[int, int] = {}
    sent: dict[int, int] = {}  # messages we sent per duel (the words' `step`)
    handled: list[int] = []  # the last tick this loop handled (v2 widens its accept margin after a gap)
    duel_traces = traces.DuelTraces()
    v2 = rules.duel_policy == "v2"
    if v2 and rules.duel_endgame_min_share > 0 and rules.duel_endgame_ticks != 1:
        console.print(
            f"[yellow]duel_endgame_min_share {rules.duel_endgame_min_share} with duel_endgame_ticks "
            f"{rules.duel_endgame_ticks}: B11 measured it with 1 (2 lets squeezes through, 0 loses deals)[/yellow]"
        )
    # v2 sends few priced messages and none of them is persuasion: the LLM words stay off for duels.
    duel_words = template_duel_words if v2 else llm_cli.words_for(settings, rules, template_duel_words)
    rereads = DuelRereads(client.duels)  # S1: a fresh re-read before each accept; a failed one fails its tick
    injections = InjectionTags(settings.data_dir / "agents" / INJECTIONS_FILE)  # S1: tagged, never obeyed
    injection_log = (  # the same words kept as proofs in Postgres, after the sends (`bazaar injections`)
        _injection_log(
            settings,
            lambda: _db.connect(app="bazaar-duels", connect_timeout_s=3),
            lambda m: console.print(f"[dim]{escape(m)}[/dim]"),
        )
        if ledger.where.startswith("postgres")
        else None
    )
    us = _our_team_id(client)
    shared = ledger.where.startswith("postgres")
    say = lambda m: console.print(escape(m))  # noqa: E731
    # v2's words are plain templates (no persuasion, as D1 measured it): tactics only under v1.
    off = "duel_policy v2 sends template words only" if v2 else None
    book = _tactic_book(rules, _learning_store("bazaar-duels", say) if shared else None, us, say, off)
    chosen: dict[int, Any] = {}  # duel id -> the tactic its offer carried this tick (for its decision row)
    refused: dict[int, str] = {}  # duel id -> the server's refusal code this tick (for its decision row)
    feed = _feed_reader(settings)  # keyless, short: a flag on one of our duel tactics

    def send(d: dict[str, Any], did: int, move: DuelMove, c: Clock, send_by: float) -> Status:
        said: str | None = None
        try:
            if move.kind == "accept":
                with duel_traces.tool(did, "duel_accept"):
                    client.duel_accept(did)
                if duel_jev is not None:
                    duel_jev.outcomes.accepted(did, int(move.price or 0))
            elif move.price is not None:
                step = max(sent.get(did, 0), our_duel_messages(d))  # a restarted runner still knows the step
                choice = duel_choice(book, d, did, move, step)  # the text only (N16)
                if choice is not None:
                    chosen[did] = choice
                budget = max(0.0, send_by - time.monotonic())
                request = WordsRequest(f"duel:{did}", move.price, sent.get(did, 0), None, rival_text(d), budget)
                words = choice.words(duel_words) if choice is not None else duel_words
                said = words(replace(request, tick=c.tick, tick_seconds=c.tick_seconds))
                if time.monotonic() > send_by:
                    console.print(f"  duel {did}: the words took the rest of the tick, offering next tick")
                    return "expired"
                with duel_traces.tool(did, "duel_say"):
                    body = client.duel_say(did, said, price=move.price, days=move.days)
                sent[did] = sent.get(did, 0) + 1
                if choice is not None:
                    price, offer = rival_offer(d)
                    book.sent(choice, their_price=price, their_offer=offer, tick=c.tick, message=message_id(body))
                    console.print(f"  duel {did}: words tactic {choice.tactic or 'none'} ({escape(choice.reason)})")
            duel_traces.sent(did, move, said)
            append_jsonl(log_path, {"tick": c.tick, "duel": did, "move": move.__dict__})
            return "done"
        except BazaarError as e:
            console.print(f"  duel {did}: refused {e.code} ({e.message[:80]})")
            refused[did] = str(e.code)
            if move.kind == "accept":  # a 4xx cost nothing (RULES.md): the slot is the team's again
                release_refused_accept(ledger, c.tick, f"duel:{did}", e.code, e.status)
            duel_traces.refused(did, e)
            append_jsonl(log_path, {"tick": c.tick, "duel": did, "refused": e.code})
            return "failed"

    def record(
        d: dict[str, Any],
        move: DuelMove,
        pick: DuelPick | None,
        tick: int,
        status: Status,
        guardrail: str = "allowed",
        gate: Gate | None = None,
    ) -> None:
        """One `decisions` row per duel per tick: the state Jev read, its verdict and floats, what we did."""
        offer = d.get("rival_offer")
        rival = offer if isinstance(offer, dict) else {}
        inputs = pick.state.get("duel", {}) if pick is not None else {}
        inputs = inputs or {"role": d.get("role"), "our_limit": d.get("your_limit"), "rival_price": rival.get("price")}
        if gate is not None:
            inputs = {**inputs, "inspector": gate.as_inputs()}
        if pick is not None and pick.days is not None:
            inputs = {**inputs, "jev_days": pick.days.as_dict()}
        row_id = duel_id(d)
        tactic = chosen.pop(row_id, None) if row_id is not None else None
        if tactic is not None:
            inputs = {**inputs, **tactic.inputs()}  # private keys (N16)
        rec.decide(
            tick,
            f"duel_{move.kind}",
            f"duel {duel_id(d)} {move.kind} {move.price or ''}",
            inputs=inputs,
            reason=move.reason
            + (f"; {pick.why}" if pick is not None else "")
            + (f"; refused {code}" if row_id is not None and (code := refused.pop(row_id, None)) else ""),
            guardrail=guardrail,
            chosen=move.kind != "hold" and status in ("approved", "done"),
            status=status,
            jev=pick.advice if pick is not None else None,
            move={"duel": duel_id(d), "kind": move.kind, "price": move.price, "days": move.days},
        )

    def latch_failed(tick: int, e: Exception) -> None:
        """This tick's session is unknown (#165 r1 P2): no sign from an older one; one dim line per tick says why."""
        days_switch.session = None
        if days_failed[-1:] != [tick]:
            days_failed[:] = [tick]
            why = f"{type(e).__name__}: {str(e)[:80]}"
            console.print(f"[dim]  duel days sign unchanged: the latch failed ({escape(ascii(why)[1:-1])})[/dim]")

    def observe_days(tick: int, rows: list[dict[str, Any]]) -> None:
        """Feed the days-sign latch. It never costs a tick its moves (#150 security r3): when it raises (a malformed
        server field, a latch file that cannot be written) the latch keeps the verdict it had before the call, one
        dim line per tick says why, and the tick goes on. The rollback copy and the verdict line sit inside the
        protection too (#165 security P3-1, P3-2): a value `deepcopy` cannot copy skips this tick's observe, and a
        server text is printed through `ascii()`, so a lone surrogate never fails the stdout write."""
        nonlocal days_switch
        try:
            kept = deepcopy(days_switch)
        except Exception as e:  # noqa: BLE001 - RecursionError on a pathological server value: keep the switch as is
            latch_failed(tick, e)
            return
        try:
            days_switch.observe(rows, real)
            if days_switch.verdict != kept.verdict:
                console.print(
                    f"  duel days sign: {days_switch.verdict} (session {days_switch.session}; "
                    f"{escape(days_switch.describe())})"
                )
        except Exception as e:  # noqa: BLE001 - bookkeeping: the duels play this tick with the previous verdict
            days_switch.keep_safer(kept)  # per role: a safer verdict found stays, never a half-merged `signed`
            latch_failed(tick, e)

    def read_done_days(tick: int) -> None:
        """Scored evidence for the days sign, after the tick's sends: v2 with duel_days_auto, on the real game,
        while the verdict is unknown or signed (r1: a text latch keeps its cross-check against the score)."""
        if tick % done_every_ticks or not reads_done(rules, days_switch, real):
            return
        try:
            observe_days(tick, [d for d in client.duels(done=True).get("duels") or [] if isinstance(d, dict)])
        except BazaarError as e:
            console.print(f"  /api/duels?done=true refused {e.code}: the days sign waits")
        except Exception as e:  # noqa: BLE001 - bookkeeping after the tick's sends: it never breaks the loop
            console.print(f"  /api/duels?done=true failed ({type(e).__name__}): the days sign waits")

    def save_finished(tick: int) -> bool:
        """One `?done=true` read on a tick where a duel left the live list: its price, rounds and result.
        True when the read was answered or refused (the days latch then needs no read of its own this tick: a
        refusal such as a 429 waits for a later tick, never a second try in this one)."""
        try:
            data = client.duels(done=True)
        except BazaarError as e:
            console.print(f"tick {tick}: /api/duels?done=true refused {e.code}")
            return True
        except Exception as e:  # noqa: BLE001 - bookkeeping after the tick's sends: it never breaks the loop
            console.print(f"tick {tick}: /api/duels?done=true failed ({type(e).__name__})")
            return False
        append_jsonl(log_path, {"tick": tick, "response": data, "done": True})
        finished = [d for d in duel_list(data) if d.get("status") != "live"]
        for d in finished:
            if (did := duel_id(d)) is not None:
                observe_duel(book, d, did, tick)  # a deal or no deal scores the last tactic of that duel
        store.save(tick, finished)
        observe_days(tick, duel_list(data))  # free scored evidence for the days sign: this read happens anyway
        return True

    def on_tick(c: Clock) -> None:
        send_by = time.monotonic() + action_budget_s(c)
        rereads.new_tick()
        decisions.begin_tick(c.tick)
        book.begin_tick(c.tick, c.round, us)
        anchor, floor = steered_duel_params(rules, settings.data_dir / STEERING_FILE, c.tick)
        try:  # a 429 at the tick boundary would cost every duel its move: one re-read if the tick has room
            data = read_once_more_after_429(
                client.duels,
                lambda: send_by - time.monotonic(),
                on_retry=lambda e, wait: console.print(
                    f"tick {c.tick}: /api/duels refused {e.code}, re-read in {wait:g} s"
                ),
            )
        except BazaarError as e:
            console.print(f"tick {c.tick}: /api/duels refused {e.code}")
            duel_traces.read_failed(c.tick, e)
            return
        append_jsonl(log_path, {"tick": c.tick, "response": data})
        duels = duel_list(data)
        console.print(f"tick {c.tick}: {len(duels)} live duel(s) logged")
        observe_days(c.tick, duels)
        rules_t = effective_rules(rules, days_switch)  # one rules object for the policy and the guard
        live_ids = [did for did in map(duel_id, duels) if did is not None]
        wait = first_offer_wait(V2Params.from_rules(rules_t)) if v2 else 0
        for d in duels:  # v2: after a restart, the earliest message is a better start than now (v1 as #60)
            if (live_id := duel_id(d)) is not None:
                first_seen.setdefault(live_id, payload_start(d, c.tick, wait) if v2 else c.tick)
        for d in duels:  # memory only: the rival's new offer scores our last tactic message
            if (seen_id := duel_id(d)) is not None:
                observe_duel(book, d, seen_id, c.tick)
        picks: dict[int, DuelPick] = {}
        limit = min(rules.max_accepts_per_tick, c.limits.accepts_per_team_per_tick)
        try:  # another process may have taken it already
            slots: int | None = max(0, limit - ledger.accepts_in_tick(c.tick))
        except Exception as e:  # a ledger outage (#62's LedgerUnavailable): fail closed, every duel holds
            console.print(f"  ledger unreadable ({type(e).__name__}): every duel holds this tick (no accept, no offer)")
            slots = None
        params = V2Params.from_rules(rules_t, anchor, floor) if v2 else None
        gap = c.tick - handled[-1] if handled else 1
        handled[:] = [c.tick]
        if params is not None and gap > 1:  # we missed ticks: the next ones may go too, so accept earlier (r2 B4)
            # capped (r1): ten failed reads must not turn every duel into "accept the first offer inside"
            params = replace(params, missed=min(gap - 1, MISSED_TICKS_CAP))
        planned: dict[int, DuelMove] = {}
        if params is not None and slots is None:
            planned = {did: DuelMove("hold", reason="ledger unreadable: no accept this tick") for did in live_ids}
        elif params is not None:
            try:
                planned = plan_moves(duels, c.tick, first_seen, params, slots or 0)
            except Exception as e:  # a v2 bug holds every duel this tick: never a silent switch back to v1
                console.print(f"  duel v2 planner failed ({type(e).__name__}): holding every duel this tick")
                planned = {did: DuelMove("hold", reason="v2 planner failed") for did in live_ids}

        def play_one(d: dict[str, Any]) -> None:
            did = duel_id(d)
            if did is None:
                return
            gate: Gate | None = None
            try:  # S1: tagged, never obeyed; a tagger bug never costs a duel its move
                offer = d.get("rival_offer")
                key = f"{did}:{offer.get('id') or offer.get('tick')}" if isinstance(offer, dict) else did
                injections.tag("duel", key, rival_text(d), c.tick, lambda m: console.print(f"  {escape(m)}"))
            except Exception as e:  # noqa: BLE001 - calibration only
                console.print(f"  duel {did}: injection tagging failed ({type(e).__name__}); the move goes on")
            pick = picks.get(did)
            if did in forced:  # v1: today's accept is the only legal move, played before Jev was asked
                pick = forced[did] if duel_jev is not None else None  # --no-jev rows carry no Jev context
                move = forced[did].move
            elif pick is not None:
                move = pick.move
            elif did in planned:
                move = planned[did]
            else:
                endgame = rules.duel_endgame_ticks
                move = duel_move(d, c.tick, first_seen[did], anchor=anchor, floor=floor, endgame_ticks=endgame)
            if move.kind == "offer" and spoke_this_tick(d, c.tick):  # a restart mid-tick: the game would refuse it
                move = DuelMove("hold", reason="we already offered this tick: one message per side per tick")
            duel_traces.seen(d, c.tick, move)
            if pick is not None:
                duel_traces.jev(did, pick)
            if play and move.kind in ("accept", "offer") and time.monotonic() >= send_by:
                console.print(f"  duel {did}: no time left in tick {c.tick}, {move.kind} next tick")
                record(d, move, pick, c.tick, "expired")
                return
            if play and move.kind in ("accept", "offer"):
                try:
                    accepts_this_tick = ledger.accepts_in_tick(c.tick)
                except LedgerUnavailable as e:  # fail closed for this duel; the tick and the loop go on
                    console.print(f"  duel {did}: {escape(str(e))}; no {move.kind} this tick (fail closed)")
                    record(d, move, pick, c.tick, "rejected", f"ledger unavailable: {e}")
                    return
                ctx = gr.Context(
                    cash=0,
                    held={},
                    tick=c.tick,
                    t_hours=c.t_hours,
                    accepts_this_tick=accepts_this_tick,
                    stops=gr.kill_switch(rules),  # read live: a GUARDRAILS.md edit counts without a restart
                )
                verdict = gr.check(duel_action(d, move), ctx, rules_t)  # the price and days we would agree to
                duel_traces.guardrail(did, verdict.allowed, verdict.violations)
                if not verdict.allowed:
                    console.print(f"  duel {did}: GUARDRAIL {verdict}")
                    record(d, move, pick, c.tick, "rejected", str(verdict))
                    return
                if move.kind == "accept" and rules.inspect_accepts:  # S1: before the accept slot is claimed
                    gate = duel_accept_check(rereads.for_tick(c.tick), d, did, move)
                    if not gate.allowed:
                        console.print(f"  duel {did}: INSPECTOR {gate.verdict}: {escape(gate.reason)}")
                        record(d, move, pick, c.tick, "rejected", f"inspector {gate.verdict}: {gate.reason}", gate)
                        return
                    if time.monotonic() >= send_by:  # the re-read took the rest of the tick: never send late
                        console.print(f"  duel {did}: the re-read took the rest of tick {c.tick}, accept next tick")
                        record(d, move, pick, c.tick, "expired", gate=gate)
                        return
                limit = min(rules.max_accepts_per_tick, c.limits.accepts_per_team_per_tick)
                try:
                    reserved = move.kind != "accept" or ledger.reserve_accept(
                        c.tick, c.t_hours, 0, f"duel:{did}", limit
                    )
                except LedgerUnavailable as e:
                    console.print(f"  duel {did}: {escape(str(e))}; no accept this tick (fail closed)")
                    record(d, move, pick, c.tick, "rejected", f"ledger unavailable: {e}")
                    return
                if not reserved:
                    console.print(f"  duel {did}: another process took the team's accept this tick")
                    record(d, move, pick, c.tick, "rejected", "accept slot taken by another process")
                    return
            # Server fields may carry text: escaped, so a stray "[/red]" cannot crash the loop after the accept
            # slot was booked.
            console.print(
                f"  duel {did} {escape(str(d.get('role')))} limit {escape(str(d.get('your_limit')))} "
                f"rival {escape(str(d.get('rival_offer')))} "
                f"deadline {duel_deadline(d)} -> {move.kind} {move.price or ''} ({escape(move.reason)})"
                + (f" · {escape(pick.why)}" if pick is not None else "")
            )
            status: Status = send(d, did, move, c, send_by) if play and move.kind != "hold" else "approved"
            record(d, move, pick, c.tick, status, gate=gate)

        def play_safely(d: dict[str, Any]) -> None:
            try:
                play_one(d)
            except Exception as e:  # one malformed row must not cost the other duels their move (r2 bite B2b)
                console.print(f"  duel {duel_id(d)}: skipped this tick ({type(e).__name__})")

        # v1 (r2 X17): in the endgame an inside-limit offer is the only legal move and Jev is never asked about
        # it, so it is booked AND sent now, nearest deadline first, before Jev answers about the other duels and
        # before the taker's duel grace (2 s) ends. v2 sends its planner's accepts in the same early pass.
        forced: dict[int, DuelPick] = {}
        for d in duels if play and params is None else ():
            if (fid := duel_id(d)) is None:
                continue
            try:
                endgame = rules.duel_endgame_ticks
                if (fp := forced_pick(d, c.tick, first_seen[fid], anchor, floor, endgame)) is not None:
                    forced[fid] = fp
            except Exception as e:  # a malformed row goes the usual way below
                console.print(f"  duel {fid}: forced-accept check failed ({type(e).__name__}): today's path")

        # v2: the planner's accepts are booked AND sent now, before Jev is asked about the other duels (r2 X17, as
        # B15 does for v1's forced accepts): the taker claims the team's accept 2 s into the tick, and a slow Jev
        # can no longer strand a booked slot. Jev's only legal move for such a duel is that accept anyway.
        # One early pass (B7 + B15): v2's planned accepts, or v1's forced endgame accepts, nearest deadline first.
        early = sorted(
            (
                d
                for d in duels
                if duel_id(d) in forced or planned.get(duel_id(d) or -1, DuelMove("hold")).kind == "accept"
            ),
            key=lambda d: duel_deadline(d) or 0,
        )
        for d in early:
            play_safely(d)
        done = {duel_id(d) for d in early}
        if duel_jev is not None:  # every live duel at once, so a duel accept still lands early in the tick
            endgame = rules.duel_endgame_ticks
            left = lambda: send_by - time.monotonic()  # noqa: E731
            try:
                picks = duel_jev.pick(
                    duels,
                    c.tick,
                    first_seen,
                    anchor=anchor,
                    floor=floor,
                    endgame_ticks=endgame,
                    left=left,
                    v2=params,
                    slots=slots or 0,
                )
            except Exception as e:  # a bug in the Jev layer must never cost a duel its move
                console.print(f"  duel jev failed ({type(e).__name__}): today's moves this tick")

        for d in duels:
            if duel_id(d) not in done:
                play_safely(d)
        duel_traces.end_tick(duel_id(d) for d in duels)
        if duel_jev is not None:
            try:
                for line in duel_jev.outcomes.settle(live_ids, c.tick):
                    console.print(f"  {escape(line)}")
            except Exception as e:  # calibration is a side record: it never breaks the loop
                console.print(f"  duel jev outcomes failed ({type(e).__name__})")

        store.save(c.tick, duels)  # after the sends: the evals read duels from Postgres, never the API
        if injection_log is not None:  # the rivals' words with an injection shape, kept with their duel id
            injection_log.note_duels(duels)  # never raises; each message is scanned once
            injection_log.flush(c.tick)  # bounded; a failure only logs and retries in a few ticks
        read = store.read_finished(duels) and save_finished(c.tick)
        if not read:  # one ?done=true read per tick at most (r1, #159): the days latch reuses the store's
            read_done_days(c.tick)  # after every send of the tick: a slow read never costs a deadline accept
        if book.messages:  # a flag needs our message id; without one there is nothing to match, so no read
            book.read_events(feed, c.tick)  # 2 s at most, backs off after a failure, never raises
        book.flush()  # after the sends: this tick's tactic lessons out, the other processes' in
        evals.after_tick(c.tick)  # last: a background pass every N ticks, never on the tick's path

    every = evals_default(evals_every, play)
    evals = _tick_evals("duels", every, lambda m: console.print(f"[dim]{escape(m)}[/dim]"))
    mode = f"{'PLAYING' if play else 'log only'}{', Jev duel_move' if jev else ''}"
    console.print(f"duels → {log_path} + Postgres duels ({mode}) · evals every {every or '-'} ticks")
    try:
        run_per_tick(client.clock, traces.per_tick("duels tick", on_tick, agent=True), max_ticks=max_ticks or None)
    finally:
        duel_traces.close("stopped")
        decisions.close()
        store.close()


MISSED_TICKS_CAP = 2  # v2 accepts at most this many ticks earlier after a gap in the duel loop


def _our_team_id(client: Any) -> str | None:
    """Our team id for the tactic lessons (one /me read at start); None when it cannot be read: the lessons then
    bind no team, and the duel loop never waits on it."""
    try:
        return str(client.me().get("id") or "") or None
    except Exception:
        return None


def _injection_log(settings: Settings, connect: Callable[[], Any] | None, log: Callable[[str], None]) -> Any:
    """The injection-attempt recorder (`injection_log.py`): buffered in the tick, written after the sends."""
    from bazaar_agent.holdings import scope_of
    from bazaar_agent.injection_log import InjectionLog
    from bazaar_agent.runtime.tools import secrets_of

    recorder = InjectionLog(connect, scope_of(settings).world, secrets_of(settings), log)
    if connect is not None:
        recorder.open()  # connect and create the table now, never inside a tick
    return recorder


def _db_connect(app: str) -> Callable[[], Any]:
    from bazaar_agent import db

    return lambda: db.connect(app=app)


def _tick_evals(agent: str, every: int, log: Callable[[str], None]) -> Any:
    """The agent's in-loop evals (evals/inline.py). A missing Phoenix key is said once, not every pass."""
    from bazaar_agent.evals.inline import TickEvals
    from bazaar_agent.evals.phoenix import annotator_from

    said: set[str] = set()

    def once(message: str) -> None:
        if message not in said:
            said.add(message)
            log(message)

    def annotator() -> Any:
        if load_settings().simulator:  # simulated duel ids collide with real ones: never annotate real traces
            once(f"evals ({agent}): simulator, scores stay in Postgres only")
            return None
        cfg = tm.tracing_config()
        return annotator_from(cfg.ui_url, cfg.api_key, cfg.project, once)

    return TickEvals(agent, every, _db_connect(f"bazaar-evals-{agent}"), evals_cli.team_id, annotator, log)


def _jev_journal(settings: Any) -> Any:
    """Every Jev call of the duel player and the maker, and its outcome, in `<data_dir>/jev-decisions/`."""
    from rich.markup import escape

    from bazaar_agent.agents.jev_journal import JOURNAL_DIRECTORY, JevJournal

    return JevJournal(settings.data_dir / JOURNAL_DIRECTORY, lambda m: err_console.print(f"[dim]{escape(m)}[/dim]"))


def _jev_fns(settings: Any, rules: Any, journal: Any, pack: str, *questions: str) -> list[Any]:
    """One `JevFn` per question of `questions/<pack>`, sharing one journal and `jev_timeout_s`."""
    from bazaar_agent.agents.jev_journal import question_fn

    # Settings already read $TYPESAFE_API_KEY (env, then .env); "" never falls back to the environment again.
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else ""
    path = REPO_ROOT / "questions" / pack
    return [question_fn(path, q, api_key=key, timeout_s=rules.jev_timeout_s, journal=journal) for q in questions]


_LESSONS: Any = None


def _lessons(log: Callable[[str], None] | None = None) -> Any:
    """This process's lessons for Jev and the words (N3): the hybrid recall over the shared `learnings`.
    The models load in the background; until then (and on any error) every call answers no lessons."""
    global _LESSONS
    if _LESSONS is None:
        from bazaar_agent import db
        from bazaar_agent.learn.embed import shared_models
        from bazaar_agent.learn.recall import HybridRecall, Lessons
        from bazaar_agent.learn.store import LearningStore

        models = shared_models(log or (lambda line: None))
        models.warm()
        _LESSONS = Lessons(HybridRecall(LearningStore(lambda: db.connect(app="bazaar-lessons")), models))
    return _LESSONS


def _duel_jev(settings: Any, rules: Any) -> Any:
    """Jev `duel_move` + `rival_cares_about_days` (questions/duels.json) as the duel player's decision model."""
    from bazaar_agent.agents.duel_jev import DAYS_QUESTION, MOVE_QUESTION, DuelJev

    journal = _jev_journal(settings)
    from bazaar_agent.learn.jev_context import duel_situation, with_lessons

    move_fn, days_fn = _jev_fns(settings, rules, journal, "duels.json", MOVE_QUESTION, DAYS_QUESTION)
    move_fn = with_lessons(move_fn, _lessons(), duel_situation)
    return DuelJev(move_fn, days_fn, can_accept_early=rules.jev_can_accept_early, journal=journal)


def _maker_jev(settings: Any, rules: Any) -> Any:
    """Jev `list_price_choice` + `reprice_or_hold` (questions/maker.json) as the maker's decision model."""
    from bazaar_agent.agents.maker_jev import PRICE_QUESTION, REPRICE_QUESTION, MakerJev

    journal = _jev_journal(settings)
    from bazaar_agent.learn.jev_context import listing_situation, with_lessons

    price_fn, reprice_fn = _jev_fns(settings, rules, journal, "maker.json", PRICE_QUESTION, REPRICE_QUESTION)
    price_fn = with_lessons(price_fn, _lessons(), listing_situation)
    return MakerJev(price_fn, reprice_fn, journal=journal)


@duel_app.command("done")
def duel_done() -> None:
    """Read our finished duels once (`/api/duels?done=true`, one request) and store them for the evals."""
    from bazaar_agent import db
    from bazaar_agent.duel_store import duel_list, save_duels

    try:
        data = team_client(load_settings()).duels(done=True)
    except BazaarError as e:
        _fail(f"/api/duels?done=true refused: {e.code} ({e.message[:80]})")
        return
    listed = duel_list(data)
    with db.connect_ready("bazaar-duels") as conn:
        saved = save_duels(conn, [d for d in listed if d.get("status") != "live"], None)
    console.print(f"stored {saved} finished duel(s) in Postgres duels ({len(listed)} listed)")


# ---------------------------------------------------------------- guardrails


@rules_app.callback(invoke_without_command=True)
def rules_root(ctx: typer.Context) -> None:
    """Every guardrail from GUARDRAILS.md, its value, and the code that enforces it."""
    if ctx.invoked_subcommand is None:
        rules_show()


@rules_app.command("show")
def rules_show() -> None:
    """Every guardrail from GUARDRAILS.md, its value, and the code that enforces it."""
    from rich.table import Table

    from bazaar_agent.guardrails import ENFORCED_BY, kill_switch

    loaded = _rules()
    t = Table(title=f"Guardrails · {loaded.path.name} (edit it, then rerun this to validate)")
    for col in ("rule", "value", "enforced by", "why", "line"):
        t.add_column(col, justify="right" if col == "line" else "left")
    for r in loaded.lines:
        t.add_row(r.rule_id, r.raw_value, ENFORCED_BY.get(r.rule_id, "[red]not enforced[/red]"), r.why, str(r.line))
    console.print(t)
    if loaded.principles:
        console.print("[bold]Principles[/bold] (read by agents, not enforced in code):")
        for line in loaded.principles:
            console.print(f"  • {line}")
    stops = kill_switch(loaded.rules, loaded.path)
    state = f"[red]ON, holding[/red] ({'; '.join(stops)})" if stops else "[green]off, trading enabled[/green]"
    console.print(
        f"Kill switch: {state}. touch {loaded.rules.pause_file} to hold the processes run from this checkout: "
        "nothing is sent, not even cancels or closes"
    )


@rules_app.command("check")
def rules_check(
    kind: str = typer.Argument(help="buy | bid | accept_buy | sell | accept_sell | duel_accept | flag"),
    item: str = typer.Argument(help="Card ref (LAV-05) or pack id"),
    price: int = typer.Option(..., help="Price in primas"),
    your_value: float | None = typer.Option(None, help="For sells: what we lose by selling that copy"),
) -> None:
    """Dry-run one action against the guardrails with our live /me, clock and ledger."""
    from bazaar_agent import guardrails as gr

    rules = _rules().rules
    settings = load_settings()
    client = team_client(settings)
    c = Clock.model_validate(client.clock())
    ctx = gr.context_from(client.me(), c.tick, c.t_hours, _ledger("rules-check"), rules, OfficialValues.of(client))
    try:
        action = gr.Action(gr.action_kind(kind), item, _rarity_of(item), price, your_value)
    except ValueError as e:
        _fail(str(e))
    verdict = gr.check(action, ctx, rules)
    colour = "green" if verdict.allowed else "red"
    console.print(
        f"[{colour}]{verdict}[/{colour}] · cash {ctx.cash}, spent last game hour {ctx.spent_last_hour}, "
        f"accepts this tick {ctx.accepts_this_tick}"
    )


# ---------------------------------------------------------------- monitoring agent


TEAM_EVENTS_FILE = "team_events.jsonl"  # stream events scoped to our team: never mixed into the public feed


def open_stream(settings: Any, emit: Callable[[Any], None]) -> Any:
    """The monitor's live feed: ONE SSE connection with our key (tests replace this factory)."""
    from bazaar_agent.stream import EventStream

    key = settings.team_key()  # the same sim-/real guard as every team request
    return EventStream(settings.bazaar_url, key, emit)


@app.command()
def monitor(
    db_enabled: bool = typer.Option(True, "--db/--no-db", help="Write to Postgres (JSONL capture always runs)"),
    notify: bool = typer.Option(False, help="macOS notification on every alert"),
    refresh_every: int = typer.Option(5, help="Rebuild dealer curves and competitor profiles every N ticks"),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    stream: bool = typer.Option(
        True, "--stream/--no-stream", help="Hold ONE live SSE stream (6 per team key, shared by laptops and tabs)"
    ),
    show_events: bool = typer.Option(False, help="Print every streamed event as it lands, timestamped"),
) -> None:
    """The monitoring agent: live stream + per-tick feed poll → JSONL + Postgres, traders, /me snapshot, alerts."""
    from bazaar_agent.agents.monitoring import MonitorLoop, Options
    from bazaar_agent.monitor import Watcher
    from bazaar_agent.stream import Inbox

    settings = load_settings()
    public = public_client(settings)
    team = team_client(settings) if settings.bazaar_key else None
    ours = _our_team(settings)
    if notify and sys.platform != "darwin":
        console.print("[yellow]--notify needs macOS (osascript): alerts go to the log and alerts.jsonl only[/yellow]")
        notify = False
    store = FeedStore(settings.feed_dir)
    watcher = Watcher(store, ours, FeedStore(settings.feed_dir, TEAM_EVENTS_FILE))
    options = Options(db_enabled, notify, refresh_every, show_events)
    loop = MonitorLoop(public, team, watcher, settings.data_dir, options, console.print)
    inbox = Inbox()
    loop.stream = open_stream(settings, inbox.put) if stream else None
    console.print(
        f"monitor: feed → {store.path}, alerts → {loop.alerts_path}, db {'on' if db_enabled else 'off'}, "
        f"stream {'on (1 of the 6 per team key)' if stream else 'off: poll only'}, "
        f"us = {ours or 'unknown (set BAZAAR_TEAM_ID or BAZAAR_KEY)'}"
    )
    try:
        if loop.stream is not None:
            loop.stream.start()
        run_per_tick(
            public.clock,
            traces.per_tick("monitor tick", loop.on_tick),
            max_ticks=max_ticks or None,
            sleep=lambda seconds: inbox.wait(seconds, loop.on_stream),
        )
    finally:
        if loop.stream is not None:
            loop.stream.stop()
        inbox.flush(loop.on_stream)


@app.command()
def traders() -> None:
    """Every trader we know (dealers and teams) from the monitor's Postgres table, with status and level."""
    from rich.table import Table

    from bazaar_agent import db

    with db.connect(load_settings().require_database_url()) as cx:
        rows = db.trader_rows(cx)
    t = Table(title=f"Traders · {len(rows)} (kept current by `bazaar monitor`)")
    for col in ("id", "kind", "name", "status", "level", "first seen", "last seen"):
        t.add_column(col)
    for r in rows:
        t.add_row(*["-" if v is None else str(v) for v in r.values()])
    console.print(t)


@app.command()
def alerts(limit: int = typer.Option(20, help="How many of the latest alerts")) -> None:
    """The latest alerts raised by the monitor: new dealers, level changes, announcements."""
    from bazaar_agent.monitor import read_alerts

    rows = read_alerts(load_settings().data_dir / "alerts.jsonl", limit)
    if not rows:
        console.print("no alerts yet")
    for a in rows:
        console.print(f"tick {a['tick']} [bold]{a['kind']}[/bold] {a['subject']}: {a['detail']}")


@app.command()
def budget(
    tick_seconds: float = typer.Option(30.0, help="Tick length: 30 Saturday, 15 Sunday"),
    ceiling: bool = typer.Option(False, help="Every loop at its ceiling instead of a steady busy tick"),
    dealer_children: int = typer.Option(0, help="`bazaar dealer buy` processes running besides the taker"),
    laptops: int = typer.Option(1, help="Copies of taker and maker (each laptop running them)"),
    stagger: bool = typer.Option(False, help="Model the proposed stagger (opt-in per service: BAZAAR_TICK_OFFSET_S)"),
    operator_rps: float = typer.Option(
        0.0, min=0.0, help="MCP tools, desk, `bazaar ask`/`status`: average req/s (0 = not counted)"
    ),
    flatten: bool = typer.Option(False, help="Add one `bazaar flatten` (32 calls) at the tick edge"),
) -> None:
    """Requests per tick per loop against the 5 req/s per key (bursts of 20). Offline: no call is made."""
    from rich.table import Table

    from bazaar_agent import rate_budget as rb

    plan = rb.saturday_plan(dealer_children=dealer_children, tick_seconds=tick_seconds) if ceiling else rb.steady_plan()
    if not ceiling and dealer_children:
        plan.append(rb.dealer_child().times(dealer_children))
    plan = rb.with_copies(plan, {"taker": laptops, "maker": laptops})
    if operator_rps:
        plan.append(rb.operator(operator_rps, tick_seconds))
    if flatten:
        plan.append(rb.flatten())
    offsets = rb.PROPOSED_STAGGER if stagger else None
    t = Table(title=f"Calls per tick · {tick_seconds:g} s ticks · {'ceiling' if ceiling else 'steady'}")
    for col in ("loop", "copies", "team", "team/s", "broker", "broker/s", "keyless", "keyless/s"):
        t.add_column(col)
    for row in rb.describe(rb.budget_table(tick_seconds, plan)):
        t.add_row(*row)
    console.print(t)
    edge = rb.burst(plan, offsets=offsets, retries=rb.TEAM_RESENDS)
    console.print(
        f"tick boundary: {edge.calls} team-key calls → {edge.sent} requests (the team client never re-sends a 429), "
        f"{edge.refused} refused 429, {edge.failed} lost (over {edge.seconds:.1f} s)"
    )
    verdict = rb.check(plan, tick_seconds, offsets=offsets)
    problems = "\n".join(f"[red]{p}[/red]" for p in verdict.problems)
    console.print("[green]fits the key[/green]" if verdict.ok else problems)


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
        with tm.span("feed.capture", tm.CHAIN, {"bazaar.tick": c.tick if c else None}, root=True):
            try:
                result = store.append(client.feed_window(window), window)
            except BazaarError as e:
                console.print(f"[red]feed read refused ({e.code}); next tick[/red]")
                tm.fail_current(e)
                return
            traces.feed_capture(result)
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


# ---------------------------------------------------------------- observability


def _phoenix_health(ui_url: str) -> tuple[bool, str]:
    """(answers 2xx, what to show) for GET <ui_url>/healthz: public even when Phoenix auth is on."""
    import httpx

    try:
        reply = httpx.get(f"{ui_url}/healthz", timeout=2.0)
    except httpx.HTTPError as e:
        return False, f"unreachable ({type(e).__name__}): `uv run bazaar obs up`"
    return (
        reply.is_success,
        f"up (HTTP {reply.status_code})" if reply.is_success else f"answers HTTP {reply.status_code}",
    )


@obs_app.command("up")
def obs_up() -> None:
    """Start Arize Phoenix (docker compose): UI and OTLP/HTTP on 127.0.0.1:6006, OTLP/gRPC on :4317.

    A Phoenix that already answers at the configured endpoint (ours on Railway, a teammate's, or the
    local one) counts as up: nothing is started."""
    cfg = tm.tracing_config()
    hint = "" if cfg.enabled else " · tracing is OFF: export BAZAAR_TRACING=1 (or add it to .env)"
    up, _ = _phoenix_health(cfg.ui_url)
    if up:
        console.print(f"[green]Phoenix is already up[/green] at {cfg.ui_url}: nothing to start{hint}")
        return
    try:
        subprocess.run(["docker", "compose", "up", "-d", "--wait", "phoenix"], cwd=REPO_ROOT, check=True)
    except FileNotFoundError:
        _fail(
            f"no Phoenix answers at {cfg.ui_url} and docker is not installed here: start Docker, or point "
            'PHOENIX_COLLECTOR_ENDPOINT at a running Phoenix (README "Production on Railway")'
        )
    console.print(f"[green]Phoenix is up[/green]: open {tm.DEFAULT_PHOENIX_URL}{hint}")


@obs_app.command("status")
def obs_status() -> None:
    """Whether tracing is on, where spans go, the Phoenix UI, and whether Phoenix answers."""
    cfg = tm.tracing_config()
    _, health = _phoenix_health(cfg.ui_url)
    console.print(render.obs_table(cfg.enabled, cfg.endpoint, cfg.ui_url, cfg.project, cfg.api_key is not None, health))


@obs_app.command("bootstrap")
def obs_bootstrap(
    url: str = typer.Option(..., help="The Phoenix base URL, e.g. https://phoenix-production-6aa3.up.railway.app"),
    key_name: str = typer.Option("railway-ingest", help="Name of the system API key to create"),
) -> None:
    """Once per new Phoenix with auth: clear the admin's forced reset, mint a system API key for spans.

    Reads the admin password from PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD (environment only). Writes the
    new key to stdout and refuses to when stdout is a terminal: pipe it into Railway instead (README)."""
    import os

    import httpx

    from bazaar_agent import phoenix_admin as pa

    password = os.environ.get("PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD", "")
    if not password:
        _fail("PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD is not set in the environment")
    if sys.stdout.isatty():
        _fail(
            "refusing to print an API key to a terminal: "
            "pipe stdout into `railway variable set PHOENIX_API_KEY --stdin`"
        )
    err = Console(stderr=True)
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=15.0) as client:
            created = pa.bootstrap(client, password, key_name, "OTLP span ingestion for Team 1's Railway services")
    except (pa.PhoenixAdminError, httpx.HTTPError) as e:
        err.print(f"[red]bootstrap failed: {e if isinstance(e, pa.PhoenixAdminError) else type(e).__name__}[/red]")
        raise typer.Exit(1) from None
    sys.stdout.write(created.key)  # not through `console`: its tracing hook would copy it into a span
    sys.stdout.flush()
    err.print(f"[green]system API key '{created.name}' created[/green] (id {created.id}); admin reset cleared")


@obs_app.command("spans")
def obs_spans(limit: int = typer.Option(500, help="How many of the newest spans to count (max 1000)")) -> None:
    """The newest spans in our Phoenix project, counted by name: proves the runtime's spans arrive."""
    import httpx

    from bazaar_agent import phoenix_admin as pa

    cfg = tm.tracing_config()
    headers = {"authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
    try:
        with httpx.Client(base_url=cfg.ui_url, headers=headers, timeout=15.0) as client:
            summary = pa.span_summary(client, cfg.project, limit)
    except (pa.PhoenixAdminError, httpx.HTTPError) as e:
        _fail(f"cannot read spans from {cfg.ui_url}: {e if isinstance(e, pa.PhoenixAdminError) else type(e).__name__}")
    console.print(
        f"project {cfg.project} at {cfg.ui_url}: {summary.total} spans, newest start {summary.newest_start or '-'}"
    )
    for name, count in summary.by_name.items():
        console.print(f"  {count:>5}  {name}")


# ---------------------------------------------------------------- database


@db_app.command("up")
def db_up() -> None:
    """Start Postgres + pgvector (docker compose, localhost:5433)."""
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "db"], cwd=REPO_ROOT, check=True)


@db_app.command("check")
def db_check() -> None:
    """Reach DATABASE_URL: host (never the password), version, latency, ssl, pgvector, row counts."""
    from bazaar_agent import db

    ok, lines = db.run_check()
    for line in lines:
        console.print(line, highlight=False, markup=False, soft_wrap=True)
    if not ok:
        raise typer.Exit(1)


@db_app.command("init")
def db_init() -> None:
    """Create every table (idempotent, safe while other processes are connected)."""
    from bazaar_agent import db

    with db.connect() as conn:
        vector = db.init_schema(conn)
    console.print("[green]schema applied[/green] · pgvector " + ("on" if vector else "off: embedding columns skipped"))
    db_tables()


@db_app.command("load")
def db_load(live: bool = typer.Option(True, help=LIVE_HELP)) -> None:
    """Load the captured feed into feed_events, tape and dealer_curves (idempotent)."""
    from bazaar_agent import db

    with db.connect() as conn:
        counts = db.load_feed(conn, _events(live), _our_team())
    console.print(f"[green]loaded[/green] {counts}")


@db_app.command("readonly-user")
def db_readonly_user(
    password_stdin: bool = typer.Option(False, "--password-stdin", help="Read the password from stdin (one line)"),
) -> None:
    """Create or rotate the teammates' read-only login (SELECT only) with the admin DATABASE_URL.

    Generates a strong password unless --password-stdin; prints its connection URL once."""
    import getpass

    import psycopg
    from rich.markup import escape

    from bazaar_agent import pgconn
    from bazaar_agent import readonly_user as ro

    raw = ro.generate_password()
    if password_stdin:  # a terminal gets a prompt that does not echo; a pipe is read as one line
        raw = getpass.getpass("password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
    try:
        password = ro.check_password(raw)
        url = load_settings().require_database_url()
        target = pgconn.describe(url)
    except (ro.PasswordError, ConfigError, pgconn.DatabaseUrlError) as e:
        _fail(escape(str(e)))
    if target.host.endswith(".railway.internal"):
        err_console.print("DATABASE_URL is Railway's private host: teammates need the public proxy URL", markup=False)
    try:
        with pgconn.connect(url, app="bazaar-readonly-user") as conn:
            ro.apply(conn, password)
    except psycopg.Error as e:
        detail = (pgconn.redact(str(e), url).strip().splitlines() or ["?"])[0]  # first line: never the CONTEXT
        _fail(escape(f"cannot apply {ro.ROLE} on {target}: {detail}"))
    err_console.print(f"{ro.ROLE} ready on {target.host}:{target.port}/{target.dbname} (SELECT only)", markup=False)
    err_console.print("connection URL (shown once; share it privately, never in git or chat):", markup=False)
    console.print(ro.connection_url(target, password), markup=False, highlight=False, soft_wrap=True)


@db_app.command("tables")
def db_tables() -> None:
    """Every table with its row count."""
    from rich.table import Table

    from bazaar_agent import db

    with db.connect() as conn:
        counts = db.table_counts(conn)
    t = Table(title="bazaar db")
    t.add_column("table")
    t.add_column("rows", justify="right")
    for name, n in counts:
        t.add_row(name, str(n))
    console.print(t)


# ---------------------------------------------------------------- strategy engine (needs BAZAAR_KEY)


def _strategy() -> Any:
    from bazaar_agent.guardrails import GuardrailsError
    from bazaar_agent.strategy import load_strategy

    try:
        return load_strategy()
    except GuardrailsError as e:
        _fail(f"STRATEGY.md is invalid, refusing to rank moves: {e}")


def _team_client() -> Any:
    """Our team client; exits naming the missing setting, never printing the key."""
    try:
        return team_client(load_settings())
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
    raise typer.Exit(1)


def _dealer_kind(settings: Any, dealer: str) -> str:
    """The dealer's published kind (one keyless `/api/dealers` read): a trickster's FINAL is no limit (SA1). An
    unreadable answer is "dealer", today's reading of a final."""
    from bazaar_agent.agents.dealer_sell_desk import dealer_kind

    try:
        answer = public_client(settings).dealers()
    except BazaarError:
        return "dealer"
    rows = answer.get("personas") or answer.get("dealers") if isinstance(answer, dict) else answer
    return dealer_kind(rows if isinstance(rows, list) else [], dealer)


def _team_me() -> tuple[Any, dict[str, Any]]:
    client = _team_client()
    try:
        return client, client.me()
    except BazaarError as e:
        console.print(f"[red]/api/me refused: {e.code} ({e.status})[/red]")
    raise typer.Exit(1)


def _my_offers(client: Any) -> list[dict[str, Any]]:
    """Our open offers; exits when they cannot be read (guardrails never check blind)."""
    from bazaar_agent.agents.seller import offers_in

    try:
        return offers_in(client.my_offers())
    except BazaarError as e:
        console.print(f"[red]/api/me/offers refused: {e.code} ({e.status}); not checking guardrails blind[/red]")
    raise typer.Exit(1)


def _open_commitments(client: Any, me: dict[str, Any], offers: list[dict[str, Any]] | None = None) -> Any:
    """Our open offers as commitments; exits when they cannot be read (guardrails never check blind)."""
    from bazaar_agent.agents.seller import open_commitments

    return open_commitments(_my_offers(client) if offers is None else offers, str(me.get("id") or ""))


def _pack_judge(settings: Any, timeout_s: float, cache_ticks: int = 0) -> Any:
    """Jev `spend_pack_slot_now` (questions/packs.json): (verdict, probability of yes) for one pack state."""
    from bazaar_agent.pack_gate import jev_pack_judge

    return jev_pack_judge(settings, timeout_s, cache_ticks)


def _print_playbook(book: Any, loaded: Any, rules: Any, ctx: Any, commitments: Any) -> None:
    from bazaar_agent import guardrails as gr

    slots = book.pack_slots
    quotas = ", ".join(f"{pack} dealer quota {n}" for pack, n in book.pack_quotas.items()) or "no dealer quota"
    console.print(
        f"tick {book.tick} · cash {book.cash} ({commitments.cash} promised by our open offers), "
        f"{max(0, ctx.cash - gr.effective_cash_floor(rules, ctx))} above {gr.floor_text(rules, ctx)} · "
        f"spent last game hour {ctx.spent_last_hour}/{rules.max_spend_per_game_hour or 'no hourly cap'} · "
        f"pack slots this game hour: used {slots.used}, "
        f"left {slots.left} of {slots.limit} ({quotas}) · each move is checked alone: all of them may not fit"
    )
    console.print(render.scarce_supply_table(list(book.supply)))
    for title, moves in (
        ("Buys · complete_pages, scarcity_first, dealer_floor (ranked)", book.buys),
        ("Sells · sell_to_need (ranked)", book.sells),
        ("Packs · pack_value, gated by Jev spend_pack_slot_now", book.packs),
    ):
        console.print(render.moves_table(title, list(moves)))
        for line in render.move_commands(list(moves)):
            console.print(line, soft_wrap=True, markup=False, highlight=False)
    if book.skipped:
        console.print("[bold]Not proposed[/bold]:")
        for line in book.skipped:
            console.print(f"  • {line}")
    console.print(render.params_table(list(loaded.lines)))
    console.print("[yellow]Every command is a dry run: add --live to trade. Guardrails re-check each write.[/yellow]")


@app.command()
def strategy(
    as_json: bool = typer.Option(False, "--json", help="Print the playbook as JSON"),
    live: bool = typer.Option(True, "--live/--no-live", help=LIVE_HELP),
) -> None:
    """Ranked playbook from STRATEGY.md: buys, sells and packs, each with its command and guardrail verdict."""
    import json

    from bazaar_agent import strategy as st
    from bazaar_agent.runtime.backend import playbook_now

    loaded, rules = _strategy(), _rules().rules
    settings = load_settings()
    client, me = _team_me()
    commitments = _open_commitments(client, me)
    public = public_client(settings)
    judge = _pack_judge(settings, rules.jev_timeout_s)
    book, ctx = playbook_now(
        me, commitments, public, _events(live), settings, rules, loaded, _ledger("strategy"), judge
    )
    if as_json:
        typer.echo(json.dumps(st.playbook_dict(book, loaded), indent=2, ensure_ascii=False))
        return
    _print_playbook(book, loaded, rules, ctx, commitments)


@app.command("taller")
def taller_cmd(
    assets: Annotated[
        list[int] | None, typer.Argument(help="Three asset ids of one rarity; none: ranked triples")
    ] = None,
    live: bool = typer.Option(False, "--live", help="Actually craft. Without it: dry run, nothing is sent"),
) -> None:
    """The Workshop (SA1): three spare copies of one rarity into one card of the next (`POST /api/taller`).

    The same guardrails as the taker's step: `taller_enabled`, one free copy of each card kept, the kill switch, the
    hourly cap shared with every process (the shared ledger, booked before the send) and the hold on an accept still
    settling that cannot name its copy. Unlike the taker it does not wait for a duel deadline or a Market Test
    (`bazaar deploy-guard` says when). Dry run by default."""
    from rich.markup import escape

    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents import taller as tl

    client, me = _team_me()
    from bazaar_agent.ledger_pg import LedgerUnavailable

    rules, ledger, ctx, commitments = _sell_context(client, me, live)
    try:  # the taker's busy set: offers, sell threads, accepts of this and the last tick (no team-desk memory here)
        threads = (client.my_threads("open") or {}).get("threads") or []
        busy = set(commitments.listed) | tl.busy_copies(me, _my_offers(client), threads, ledger, ctx.tick)
    except LedgerUnavailable as e:
        _fail(f"the shared ledger is down, nothing sent: {escape(str(e))}")
    except BazaarError as e:
        _fail(f"our offers or threads could not be read ({escape(str(e.code))}): nothing sent")
    public = public_client(load_settings())
    catalog = public.catalog()
    if not assets:
        dealers = public.dealers()
        rows = dealers.get("personas") if isinstance(dealers, dict) else dealers
        for t in tl.rank_triples(me, catalog, rows or [], busy):
            console.print(f"{' '.join(str(a) for a in t.asset_ids)}  {', '.join(t.refs)}  {escape(t.reason())}")
        return
    ours = {int(a["id"]): a for a in me.get("assets") or [] if a.get("kind") == "card" and isinstance(a.get("id"), int)}
    if len(set(assets)) != tl.INPUTS or any(a not in ours or a in busy for a in assets):
        _fail(f"give {tl.INPUTS} different free copies of ours (not in an open offer): {assets}")
    refs = [str(ours[a]["ref"]) for a in assets]
    rarities = {str((tl.cards_of(catalog).get(ref) or {}).get("rarity")) for ref in refs}
    if len(rarities) != 1:
        _fail(f"the Workshop takes three copies of ONE rarity: {', '.join(refs)}")
    action = gr.Action("taller", ",".join(refs), rarities.pop(), assets=tuple(assets))
    try:  # the shared ledger: every process's crafts this hour, and accepts still settling (fail closed)
        done, hold = tl.crafts_last_hour(ledger, ctx.t_hours), tl.unnamed_settling(ledger, ctx.tick)
    except LedgerUnavailable as e:
        _fail(f"the shared ledger is down, nothing sent: {escape(str(e))}")
    verdict = gr.check(
        action, replace(ctx, sellable=tl.free_counts(me, busy), taller_last_hour=done, taller_hold=hold), rules
    )
    console.print(f"Workshop {', '.join(refs)} · guardrails {escape(str(verdict))}")
    if not verdict.allowed or not live:
        if verdict.allowed:
            console.print("[dim]dry run: nothing sent (add --live)[/dim]")
        return
    try:
        tl.book_craft(ledger, ctx.tick, ctx.t_hours, refs)  # before the send: the shared hourly cap
        answer = tl.craft(client, assets)
    except LedgerUnavailable as e:
        _fail(f"the shared ledger is down, nothing sent: {escape(str(e))}")
    except BazaarError as e:
        _fail(f"refused: {escape(str(e.code))} ({escape(tl.pulled({'card': str(e.message)[:80]}))})")
    console.print(f"crafted: {escape(tl.pulled(answer))}")


# ---------------------------------------------------------------- our offers: sell list / bid / offers / cancel

sell_app = typer.Typer(no_args_is_help=True, help="Our offers on a venue: list a card, bid for one, see or cancel ours")
app.add_typer(sell_app, name="sell")
app.add_typer(flags_cli.flags_app, name="flags")
app.add_typer(breaker_cli.breaker_app, name="breaker")
app.command("approve")(approval_cli.approve)
app.command("approvals")(approval_cli.approvals_list)
app.command("impact")(impact_cli.impact)
app.command("injections")(injection_cli.injections)
app.command("deploy-guard", help="Is it safe to merge to main (which redeploys the duels)? Exit 1 = no.")(
    deploy_guard.deploy_guard_cmd
)
EXPIRES_HELP = "Ticks the offer stays open"
POST_HELP = "Actually post. Without it: dry run, nothing is sent"
TO_HELP = "Address the offer to one team (t05): only it may accept. Default: anyone on the venue"


def _team_to(to: str | None) -> str | None:
    if to is not None and not intel.TEAM_ID.match(to):
        _fail(f"--to takes a team id like t05, not {to!r}")
    return to


def _reserve_accept(ledger: Any, rules: Any, item: str, move: Any, c: Clock) -> bool:
    """Claim the team's accept slot for a dealer accept. False: the slot is taken this tick (the dealer bids
    her ask instead). The shared ledger cannot answer: `Hold`, so the dealer holds the tick and sends nothing
    (fail closed: never a bid whose spend the ledger could not book, never a walk)."""
    from bazaar_agent.agents.dealer import Hold
    from bazaar_agent.ledger_pg import LedgerUnavailable

    limit = min(rules.max_accepts_per_tick, c.limits.accepts_per_team_per_tick)
    try:
        if not ledger.reserve_accept(c.tick, c.t_hours, int(move.price or 0), item, limit):
            return False
    except LedgerUnavailable as e:  # no slot without the shared ledger: hold, never accept or walk
        raise Hold(f"{e}; no write without the shared ledger (fail closed)") from None
    tm.event("ledger", {"kind": "accept", "tick": c.tick, "price": move.price, "item": item})
    return True


def _sell_context(
    client: Any, me: dict[str, Any], live: bool, *, ledger: Any = None, clock: Clock | None = None
) -> tuple[Any, Any, Any, Any]:
    """(rules, ledger, guardrail context, commitments) for a write from the CLI: /me, the shared ledger, our
    open offers and, with `max_counterparty_share` on, our team-to-team volume from the whole feed history
    (`_history`: the shared DB first, as the maker and the taker read it; the live window too when `live`)."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.ledger_pg import LedgerUnavailable

    rules = _rules().rules
    ledger = ledger if ledger is not None else _ledger("sell", live=live)
    now = clock or Clock.model_validate(client.clock())
    try:
        ctx = gr.context_from(me, now.tick, now.t_hours, ledger, rules, OfficialValues.of(client))
    except LedgerUnavailable as e:
        _fail(f"refusing to trade: {e}; no write without the shared ledger (fail closed)")
    from bazaar_agent.agents import publication

    offers = publication.with_pending(ledger, me, _my_offers(client), str(me.get("id")), now.tick, now.t_hours)
    commitments = _open_commitments(client, me, offers)
    if rules.max_counterparty_share < 1:  # the share counts what we settled with each team and still offer
        from bazaar_agent.agents.seller import trade_book

        us = str(me.get("id") or "")
        try:
            book = intel.book_values(public_client(load_settings()).catalog())
            settled = intel.settled_volume(_history(None, live=live), us, book)
        except BazaarError as e:
            _fail(f"max_counterparty_share is on and the feed or catalog read failed ({e.code}): not checking blind")
        ctx = replace(ctx, trades=trade_book(offers, us, settled, book))
    return rules, ledger, ctx, commitments


def _fresh_sale(listing: Any, me: dict[str, Any]) -> Any:
    """Keep the selected asset/terms, refresh the values the guards depend on."""
    from bazaar_agent.agents.seller import OfferError, Swap, sell_listing
    from bazaar_agent.strategy import build_market, buy_case

    if listing.asset_id is None:
        return listing
    target = sell_listing(me, str(listing.asset_id), 1, listing.venue, to=listing.to)
    if not isinstance(listing, Swap):
        return replace(listing, rarity=target.rarity, your_value=target.your_value)
    market = build_market(me, public_client(load_settings()).catalog(), [], [])
    card = market.cards.get(listing.want_ref)
    if card is None:
        raise OfferError(f"unknown card {listing.want_ref!r}")
    assert target.your_value is not None  # sell_listing fails closed without a value
    return replace(
        listing,
        give_rarity=target.rarity,
        your_value=target.your_value,
        want_rarity=card.rarity,
        worth=buy_case(market, card, _strategy().params).value,
    )


def _post_offer(client: Any, me: dict[str, Any], listing: Any, live: bool, expires: int) -> None:
    from bazaar_agent.agents import publication
    from bazaar_agent.agents.market import venues_from
    from bazaar_agent.agents.seller import OfferError, Swap, post, post_swap
    from bazaar_agent.ledger_pg import LedgerUnavailable, trade_lock

    ledger = _ledger("sell", live=live)
    reservation = None
    try:
        with trade_lock(ledger) if live else contextlib.nullcontext():
            clock = Clock.model_validate(client.clock())
            deadline = time.monotonic() + action_budget_s(clock)
            if live:
                me = client.me()
                listing = _fresh_sale(listing, me)
                venue = next(
                    (
                        v
                        for v in venues_from(public_client(load_settings()).venues(), clock.tick)
                        if v.id == listing.venue
                    ),
                    None,
                )
                if venue is None or venue.status != "open" or venue.owner == str(me.get("id")):
                    _fail("venue is unavailable or owned by us")
            rules, _, ctx, commitments = _sell_context(client, me, live, ledger=ledger, clock=clock)
            send = post_swap if isinstance(listing, Swap) else post
            out = send(client, listing, ctx, rules, live=False, commitments=commitments)
            if live and out.verdict.allowed:
                if not clock.is_live or time.monotonic() >= deadline:
                    _fail("tick action budget ended or game is not live; nothing sent")
                if ledger.count_in_tick("listing", clock.tick) >= clock.limits.offers_per_team_per_tick:
                    _fail("listing limit reached this tick; nothing sent")
                reservation = publication.reserve(
                    ledger, ctx.tick, ctx.t_hours, str(me.get("id")), listing.give, listing.want, to=listing.to
                )
                out = send(
                    client,
                    listing,
                    ctx,
                    rules,
                    live=True,
                    expires_in_ticks=expires,
                    ledger=ledger,
                    commitments=commitments,
                )
                if not out.sent:
                    publication.release(ledger, reservation, ctx.tick, ctx.t_hours)
                elif isinstance((out.offer or {}).get("id"), int):
                    publication.confirm(ledger, reservation, (out.offer or {})["id"], ctx.tick, ctx.t_hours)
            price = listing.give_cash or listing.want_cash if isinstance(listing, Swap) else listing.price
            _hands_off(ledger, ctx, out, price)
    except BazaarError as e:
        if reservation is not None and 400 <= e.status < 500 and e.status != 408:
            publication.release(ledger, reservation, ctx.tick, ctx.t_hours)
        _fail(f"offer refused or outcome unknown: {e.code} ({e.message[:80]})")
    except OfferError as e:
        _fail(str(e))
    except LedgerUnavailable as e:
        _fail(f"shared ledger unavailable; any dispatched offer remains reserved ({e})")
    _report_post(out)


def _hands_off(ledger: Any, ctx: Any, out: Any, price: int) -> None:
    """A live hand post is booked as a `HANDS_OFF` listing in the shared ledger: the maker (which owns our
    board offers, on any machine) never cancels or reprices it, and the listing counts toward this tick's."""
    from bazaar_agent.guardrails import HANDS_OFF

    offer_id = (out.offer or {}).get("id") if out.sent else None
    if not isinstance(offer_id, int):
        return
    try:
        ledger.record("listing", ctx.tick, ctx.t_hours, price, f"{HANDS_OFF}{offer_id}")
    except Exception as e:  # the offer is out: say so, never fail the command after the send
        console.print(
            f"[red]offer {offer_id} is posted but NOT booked hands-off ({type(e).__name__}): "
            f"the maker may cancel it; pause the maker or repost[/red]"
        )
        return
    if str(getattr(ledger, "where", "")).startswith("file"):
        console.print(
            f"[yellow]offer {offer_id} booked hands-off in this machine's ledger only ({ledger.where}): "
            f"a maker on another machine does not see it[/yellow]"
        )
    else:
        console.print(f"[dim]offer {offer_id} booked hands-off in the shared ledger: the maker leaves it alone[/dim]")


def _report_post(out: Any) -> None:
    colour = "green" if out.verdict.allowed else "red"
    console.print(f"[{colour}]{out.message}[/{colour}]")
    if not out.verdict.allowed:
        raise typer.Exit(1)


@sell_app.command("list")
def sell_list(
    target: str = typer.Argument(help="Asset id (15) or card ref (LAT-09: the copy we lose least by selling)"),
    price: int = typer.Option(..., min=1, help="Cash we want for it"),
    venue: str = typer.Option("rastro", help="Venue id"),
    expires: int = typer.Option(40, min=1, help=EXPIRES_HELP),
    to: str | None = typer.Option(None, "--to", help=TO_HELP),
    live: bool = typer.Option(False, help=POST_HELP),
) -> None:
    """List one card for cash (give the asset, want cash), never below its your_value (GUARDRAILS.md)."""
    from bazaar_agent.agents.seller import OfferError, sell_listing

    client, me = _team_me()
    try:
        listing = sell_listing(me, target, price, venue, to=_team_to(to))
    except OfferError as e:
        _fail(str(e))
        return
    _post_offer(client, me, listing, live, expires)


@sell_app.command("bid")
def sell_bid(
    ref: str = typer.Argument(help="Card ref we want, e.g. LAV-09 (any copy)"),
    price: int = typer.Option(..., min=1, help="Cash we offer"),
    venue: str = typer.Option("rastro", help="Venue id"),
    expires: int = typer.Option(40, min=1, help=EXPIRES_HELP),
    to: str | None = typer.Option(None, "--to", help=TO_HELP),
    live: bool = typer.Option(False, help=POST_HELP),
) -> None:
    """Bid cash for any copy of a card (give cash, want the card): how we buy rares only teams hold."""
    from bazaar_agent.agents.seller import OfferError, bid_listing

    client, me = _team_me()
    try:
        listing = bid_listing(ref, _rarity_of(ref), price, venue, to=_team_to(to))
    except OfferError as e:
        _fail(str(e))
        return
    _post_offer(client, me, listing, live, expires)


@sell_app.command("swap")
def sell_swap(
    target: str = typer.Argument(help="The copy we give: asset id (15) or card ref (the copy we lose least by)"),
    want: str = typer.Option(..., "--for", help="The card we want for it, any copy (e.g. MAL-08)"),
    to: str = typer.Option(..., "--to", help="The team we propose it to (t08): only it may accept"),
    give_cash: int = typer.Option(0, min=0, help="Cash we add"),
    want_cash: int = typer.Option(0, min=0, help="Cash we ask them to add"),
    venue: str = typer.Option("rastro", help="Venue id"),
    expires: int = typer.Option(40, min=1, help=EXPIRES_HELP),
    live: bool = typer.Option(False, help=POST_HELP),
) -> None:
    """Propose a swap to one team: our copy (+ cash) for any copy of a card (+ cash), guardrails checked.

    The copy leaves at what we receive (the card's worth to us plus their cash), never below its
    your_value; cash we add is checked as a bid (its price cap, the cash floor, the spend cap); both count
    toward the team's share when the counterparty cap is on."""
    from bazaar_agent.agents.seller import OfferError, Swap, find_copy
    from bazaar_agent.strategy import build_market, buy_case

    client, me = _team_me()
    team_to = _team_to(to)
    try:
        asset = find_copy(me, target)
    except OfferError as e:
        _fail(str(e))
        return
    if not isinstance(asset.get("your_value"), int | float):
        _fail(f"asset {asset['id']} has no your_value in /api/me: not pricing it blind")
    catalog = public_client(load_settings()).catalog()
    m = build_market(me, catalog, [], [])
    card = m.cards.get(want)
    if card is None or team_to is None:
        _fail(f"unknown card {want!r}")
        return
    book = intel.book_values(catalog)
    swap = Swap(
        int(asset["id"]),
        str(asset.get("ref")),
        asset.get("rarity"),
        float(asset["your_value"]),
        want,
        card.rarity,
        buy_case(m, card, _strategy().params).value,
        venue,
        team_to,
        give_cash,
        want_cash,
        max(give_cash + want_cash, round(book.get(str(asset.get("ref")), 0.0) + book.get(want, 0.0))),
    )
    _post_offer(client, me, swap, live, expires)


@sell_app.command("offers")
def sell_offers() -> None:
    """Our open and queued offers, and open offers addressed to us (GET /api/me/offers)."""
    try:
        data = _team_client().my_offers()
    except BazaarError as e:
        _fail(f"/api/me/offers refused: {e.code} ({e.status})")
        return
    for key, rows in data.items():
        if isinstance(rows, list):
            console.print(render.offers_table([r for r in rows if isinstance(r, dict)], key))


@sell_app.command("cancel")
def sell_cancel(
    offer_id: int = typer.Argument(help="Offer id, from `bazaar sell offers`"),
    live: bool = typer.Option(False, help="Actually cancel. Without it: dry run, nothing is sent"),
) -> None:
    """Withdraw one of our open offers (refused while the kill switch is on: open offers stay open)."""
    from bazaar_agent import guardrails as gr

    stops = gr.kill_switch(_rules().rules)
    if stops:
        _fail(f"kill switch on, nothing is sent (open offers stay open): {'; '.join(stops)}")
    if not live:
        console.print(f"[yellow]dry run[/yellow] would cancel offer {offer_id}. Add --live to cancel.")
        return
    try:
        _team_client().cancel(offer_id)
    except BazaarError as e:
        _fail(f"cancel refused: {e.code} ({e.message[:80]})")
        return
    console.print(f"[green]cancelled offer {offer_id}[/green]")


@app.command("flatten")
def flatten_cmd(
    live: bool = typer.Option(False, help="Actually cancel (and close). Without it: dry run, nothing is sent"),
    threads: bool = typer.Option(False, help="Also close our open threads (a dealer remembers a walk)"),
) -> None:
    """Cancel every open offer of ours (--threads: also close our threads); works while the kill switch holds."""
    from bazaar_agent import db
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.flatten import flatten, offers_to_cancel, threads_to_close
    from bazaar_agent.agents.runtime import Recorder
    from bazaar_agent.decisions import DecisionLog

    rules = _rules().rules
    settings = load_settings()
    client, me = _team_me()
    try:
        now = Clock.model_validate(client.clock())
        items = offers_to_cancel(client.my_offers(), str(me.get("id") or ""))
        items += threads_to_close(client.my_threads("open")) if threads else []
    except BazaarError as e:
        _fail(f"read refused: {e.code} ({e.status}); nothing sent")
        return
    stops = gr.kill_switch(rules)
    if stops:
        console.print(f"kill switch on ({'; '.join(stops)}): agents hold; only this flatten's cancels/closes go out")
    else:
        console.print(f"[yellow]kill switch off: the maker may post again; touch {rules.pause_file} first[/yellow]")
    decisions = DecisionLog(settings.data_dir, lambda: db.connect(app="bazaar-flatten"), console.print)
    decisions.begin_tick(now.tick)
    rec = Recorder("flatten", decisions, live, lambda line: console.print(line, highlight=False))
    try:
        ledger = _ledger("flatten") if live else None
        out = flatten(
            client,
            items,
            rec=rec,
            tick=now.tick,
            t_hours=now.t_hours,
            ledger=ledger,
            live=live,
            kill_switch=stops,
            max_tick_seconds=now.max_tick_seconds,
        )
    finally:
        decisions.close()
    offers = sum(1 for i in items if i.kind == "cancel")
    plan = f"{offers} offer(s) to cancel" + (f", {len(items) - offers} thread(s) to close" if threads else "")
    if not live:
        console.print(f"[yellow]dry run[/yellow] {plan}. Add --live to send.")
        return
    console.print(f"flatten: {plan}; {len(out.done)} done, {len(out.failed)} refused, {len(out.left)} left")
    for item, code in out.failed:
        console.print(f"  refused {item.kind} {item.id} ({item.what}): {code}")
    if out.left:
        _fail(f"stopped by {out.stopped}: {len(out.left)} left; run `bazaar flatten --live` again next tick")
    if out.failed:  # a refused cancel or close may have left an offer or a thread open: never report success
        _fail(f"{len(out.failed)} refused; check `bazaar sell offers` / `bazaar threads` and run it again")


# ---------------------------------------------------------------- autonomous agents (needs BAZAAR_KEY)

PORT_HELP = "Serve the read-only status (GET /health, /state, WS /events) on this port; default $PORT, else off"
HOST_HELP = "Interface for the status server (default 0.0.0.0)"
AGENT_LIVE_HELP = "Actually trade. Without it (and without BAZAAR_LIVE=1 in the environment): dry run, nothing is sent"


def _offer_jev(settings: Any, timeout_s: float) -> Any:
    """Jev `offer_is_worth_accepting` (questions/negotiation.json) as the agents' advisory `JevFn`."""
    from bazaar_agent.agents.runtime import JevAdvice
    from bazaar_agent.jev import judge, load_questions

    questions = load_questions(REPO_ROOT / "questions" / "negotiation.json")
    question = {"offer_is_worth_accepting": questions["offer_is_worth_accepting"]}
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None

    def ask(state: dict[str, Any]) -> JevAdvice:
        result = judge(state, question, api_key=key, timeout_s=timeout_s)
        tm.record_jev(result, "offer_is_worth_accepting")
        verdict = result.verdicts["offer_is_worth_accepting"]
        return JevAdvice(verdict.verdict, verdict.value, verdict.probabilities, verdict.reason)

    return ask


def _swap_jev(settings: Any, rules: Any) -> Any:
    """Jev `team_swap_worth_it` (questions/team_swaps.json) as the team desk's gate: decided at
    `team_swap_jev_min_confidence`, and `undecided` past `jev_timeout_s` (the desk then sends nothing)."""
    from bazaar_agent.agents.runtime import JevAdvice
    from bazaar_agent.jev import judge, load_questions

    name = "team_swap_worth_it"
    question = {name: load_questions(REPO_ROOT / "questions" / "team_swaps.json")[name]}
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
    bar = {name: rules.team_swap_jev_min_confidence}

    def ask(state: dict[str, Any]) -> JevAdvice:
        result = judge(state, question, api_key=key, timeout_s=rules.jev_timeout_s, thresholds=bar)
        tm.record_jev(result, name)
        verdict = result.verdicts[name]
        return JevAdvice(verdict.verdict, verdict.value, verdict.probabilities, verdict.reason)

    return ask


def _strategy_jev(settings: Any, rules: Any) -> Any:
    """Jev on `questions/strategies.json` (SG1) as `strategy_gate.AskFn`: each question's own bar (design,
    0.75) and `jev_timeout_s`; anything but a decided yes keeps that strategy off."""
    from bazaar_agent.agents.runtime import JevAdvice
    from bazaar_agent.agents.strategy_gate import QUESTIONS_FILE
    from bazaar_agent.jev import judge, load_questions

    questions = load_questions(REPO_ROOT / "questions" / QUESTIONS_FILE)
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
    timeout_s = rules.jev_timeout_s

    def ask(name: str, state: dict[str, Any]) -> JevAdvice:
        result = judge(state, {name: questions[name]}, api_key=key, timeout_s=timeout_s)
        tm.record_jev(result, name)
        verdict = result.verdicts[name]
        return JevAdvice(verdict.verdict, verdict.value, verdict.probabilities, verdict.reason)

    return ask


def _status_port(port: int | None) -> int:
    """`--port`, else Railway's PORT, else 0 (no status server on a laptop unless asked)."""
    import os

    if port is not None:
        return port
    raw = os.environ.get("PORT", "").strip()
    return int(raw) if raw.isdigit() else 0


def _feed_interpreter(settings: Any, rules: Any, log: Callable[[str], None]) -> Any:
    """The LLM pass over the feed's free text (N12): only with RUNTIME.md `llm_read_feed = true` and a
    runtime LLM credential; at most one call per `read_feed_every_ticks`, never while the kill switch is on."""
    from bazaar_agent.learn.interpret import FeedInterpreter
    from bazaar_agent.llm.config import RuntimeConfigError, load_runtime

    try:
        config = load_runtime().config
    except RuntimeConfigError as e:
        log(f"feed reader: LLM pass off ({e})")
        return None
    if not config.llm_read_feed:
        log("feed reader: LLM pass off (RUNTIME.md llm_read_feed = false); structure only")
        return None
    runtime = llm_cli.runtime_for(settings, rules, "feed reader")
    if runtime is None:
        return None
    pause = REPO_ROOT / rules.pause_file
    return FeedInterpreter(runtime, log, every_ticks=config.read_feed_every_ticks, paused=pause.exists)


def _run_agent(
    name: str,
    live: bool,
    max_ticks: int,
    build: Callable[..., Any],
    port: int | None = None,
    host: str | None = None,
    evals_every: int | None = None,
    learn: bool = False,
    llm_read: bool = False,
) -> None:
    """Shared wiring: settings, guardrails, strategy, the shared ledger, the decision log, the feed, the
    read-only status server, the loop. `learn`: this agent owns the live-feed reader (N12): it archives
    the feed window into `feed_events` and gets a `learner` (blockers recalled before dealer threads)."""
    from rich.markup import escape

    from bazaar_agent import db
    from bazaar_agent import holdings as hd
    from bazaar_agent.agents.runtime import MarketFeed, live_mode, watched_clock
    from bazaar_agent.agents.status import StatusHub, start_status_server
    from bazaar_agent.decisions import DecisionLog
    from bazaar_agent.ledger_pg import LedgerNotShared, ledger_health, open_ledger
    from bazaar_agent.llm.steering import STEERING_FILE, steered_strategy_params
    from bazaar_agent.supply_db import ScanStore

    loaded, rules = _strategy(), _rules().rules
    settings = load_settings()
    hd.name_process(name)  # how its sends and /me reads are tagged in the shared holdings
    team, public = _team_client(), public_client(settings)
    is_live = live_mode(live)

    def log(line: str) -> None:
        console.print(escape(line), soft_wrap=True, highlight=False)

    def connect() -> Any:
        return db.connect(app=f"bazaar-{name}")

    mode = "LIVE: trades are sent" if is_live else "DRY RUN: nothing is sent (add --live, or BAZAAR_LIVE=1)"
    console.print(f"[bold]{name}[/bold] · {mode} · {settings.target_line()}")
    try:
        url, game = settings.database_url.get_secret_value(), settings.bazaar_url
        ledger = open_ledger(settings.data_dir, source=name, live=is_live, database_url=url, game_url=game, log=log)
    except LedgerNotShared as e:
        _fail(f"{name}: refusing to trade: {e}")
    decisions = DecisionLog(settings.data_dir, connect, log, game_url=settings.bazaar_url)
    scans = ScanStore(settings.data_dir / "supply", connect, log)
    feed = MarketFeed(public.feed_window, FeedStore(settings.feed_dir), connect, log, scans, archive=learn)
    extra: dict[str, Any] = {}
    if learn:
        from bazaar_agent.learn.live import LiveLearner
        from bazaar_agent.learn.store import LearningStore

        def connect_learnings() -> Any:  # a short timeout: a reconnect after the sends must not eat the next tick
            return db.connect(app=f"bazaar-{name}", connect_timeout_s=3)

        store = LearningStore(connect_learnings, log)  # the ledger's `connect_ready` applied the schema already
        log(f"{name}: learnings {store.open()}")  # connect now, never inside a tick
        extra["learner"] = LiveLearner(store, log, _feed_interpreter(settings, rules, log) if llm_read else None)
        if name == "taker":  # one outcome learner per team: lessons + embeddings every few ticks (N3)
            from bazaar_agent.learn.embed import shared_models
            from bazaar_agent.learn.outcomes import OutcomeLearner

            models = shared_models(log)
            models.warm()  # background download/load: lessons start once the models are ready
            outcome_store = LearningStore(connect, log)
            outcome_store.open()  # connect now, never inside a tick
            extra["outcome_learner"] = OutcomeLearner(connect, outcome_store, models, log, rules=rules)
        from bazaar_agent.learn.threads import ThreadStore

        extra["thread_store"] = ThreadStore(connect_learnings, log)  # our dealer threads: threads + messages
        extra["thread_store"].open()  # connect now, never inside a tick
        if name == "taker":  # injection attempts in the words the taker already reads (feed, team and dealer threads)
            extra["injection_log"] = _injection_log(settings, connect_learnings, log)

    def params(tick: int) -> Any:
        return steered_strategy_params(loaded.params, rules, settings.data_dir / STEERING_FILE, tick)

    hub = StatusHub(name, is_live, target=settings.target, ledger=lambda: ledger_health(ledger))
    serve_on = _status_port(port)
    if serve_on:
        bind = host or "0.0.0.0"  # read-only public status (Railway routes PORT to it)
        log(f"{name}: status on http://{bind}:{start_status_server(hub, bind, serve_on)} (/health /state, WS /events)")
    agent = build(
        team,
        public,
        rules=rules,
        params=params,
        ledger=ledger,
        decisions=decisions,
        feed=feed,
        live=is_live,
        log=log,
        settings=settings,
        hub=hub,
        holdings=hd.for_process(
            team.me,
            rules,
            settings,
            team=resolve_team_id(settings.team_id, settings.data_dir, None),
            on_team=partial(remember_team_id, settings.data_dir),
        ),
        **extra,
    )
    every = evals_default(evals_every, is_live)
    evals = _tick_evals(name, every, log)
    log(f"{name}: ledger {ledger.where} · decisions {decisions.where} · evals every {every or '-'} ticks")

    def on_tick(clock: Clock) -> None:
        agent.on_tick(clock)
        evals.after_tick(clock.tick)  # after every send of the tick; the pass runs in the background

    try:
        read_clock = watched_clock(team.clock, name, log, hub)
        run_per_tick(read_clock, traces.per_tick(f"{name} tick", on_tick, agent=True), max_ticks=max_ticks or None)
    finally:
        decisions.close()


def _cards_heartbeat(kw: dict[str, Any], settings: Any) -> Any:
    """New cards in the catalog the taker already reads: stored in the feed reader's learnings store (Postgres +
    memory) when it runs, else in memory only; ranked up per GUARDRAILS `card_release_boost_enabled`."""
    from bazaar_agent.cards_heartbeat import CardsHeartbeat
    from bazaar_agent.learn.store import LearningStore

    learner = kw.get("learner")
    store = learner.store if learner is not None else LearningStore(None, kw["log"])
    return CardsHeartbeat(kw["rules"], store.record, kw["log"], settings.data_dir / "agents")


def _news_sentinel(kw: dict[str, Any], settings: Any) -> Any:
    """Radio Rastro and the schedule, read by the taker after its sends on its own keyless client (2 s, never
    retried: a hung or rate-limited read costs one attempt, never the next tick): stored in the feed reader's
    learnings store (Postgres + memory) when it runs, else in memory only; logging and storage only (news.py)."""
    from bazaar_agent.learn.store import LearningStore
    from bazaar_agent.news import READ_TIMEOUT_S, NewsSentinel
    from bazaar_agent.sdk import PublicBazaar

    learner = kw.get("learner")
    store = learner.store if learner is not None else LearningStore(None, kw["log"])
    reader = PublicBazaar(settings.bazaar_url, timeout=READ_TIMEOUT_S, retries=0)
    return NewsSentinel(
        reader,
        store.record,
        kw["log"],
        settings.data_dir / "agents",
        history=_rank_history(kw, settings),
        matrix_store=_matrix_store(kw, settings),
    )


def _matrix_store(kw: dict[str, Any], settings: Any) -> Any:
    """The team matrix's tables in the shared Postgres when the ledger is there (its world: real or sim:<host>)."""
    ledger = kw.get("ledger")
    if ledger is None or not ledger.where.startswith("postgres"):
        return None
    from bazaar_agent import db
    from bazaar_agent.holdings import scope_of
    from bazaar_agent.team_matrix_store import TeamMatrixStore

    return TeamMatrixStore(lambda: db.connect(app="bazaar-team-matrix", connect_timeout_s=3), kw["log"],
                           scope_of(settings).world)  # fmt: skip


def _latest_matrix(kw: dict[str, Any], settings: Any) -> Any:
    """The maker reads the matrix the taker stores, at most every 10 ticks after its sends."""
    from bazaar_agent.team_matrix_store import LatestMatrix

    store = _matrix_store(kw, settings)
    return LatestMatrix(store) if store is not None else None


def _rank_history(kw: dict[str, Any], settings: Any) -> Any:
    """Leaderboard snapshots in the shared Postgres when the ledger is there (its world: real or sim:<host>)."""
    ledger = kw.get("ledger")
    if ledger is None or not ledger.where.startswith("postgres"):
        return None
    from bazaar_agent import db
    from bazaar_agent.holdings import scope_of
    from bazaar_agent.leaderboard_store import LeaderboardStore

    return LeaderboardStore(lambda: db.connect(app="bazaar-leaderboard", connect_timeout_s=3), kw["log"],
                            scope_of(settings).world)  # fmt: skip


def _persona_book(kw: dict[str, Any], shared: bool) -> Any:
    """The taker's persona book: the /api/dealers personas it reads every tick, stored in the shared Postgres
    `traders` table when they change (off the tick; nothing stored without the shared database)."""
    from bazaar_agent import db
    from bazaar_agent.agents.persona_book import PersonaBook

    def write(snaps: list[Any], tick: int) -> None:
        with db.connect(app="bazaar-taker-personas", connect_timeout_s=3) as conn:
            db.upsert_traders(conn, snaps, tick)

    return PersonaBook(write if shared else None, kw["log"])


def _affinity_book(kw: dict[str, Any], shared: bool) -> Any:
    """Other teams' multipliers (AF1), said in a team thread or inferred from the feed, stored in the shared
    Postgres `team_affinity` table off the tick. Without the shared database: nothing stored, nothing asked."""
    from bazaar_agent import db
    from bazaar_agent import team_affinity as ta

    def write(rows: list[Any]) -> int:
        with db.connect(app="bazaar-taker-affinity", connect_timeout_s=3) as conn:
            return ta.save(conn, rows)

    def told() -> set[str]:
        with db.connect(app="bazaar-taker-affinity", connect_timeout_s=3) as conn:
            conn.read_only = True
            return ta.said_teams(conn)

    return ta.AffinityBook(write, kw["log"], told) if shared else None


def _egg_hunter(kw: dict[str, Any], shared: bool) -> Any:
    """The easter-egg hunt (`agents/egg_hunt.py`): its tried set in the shared Postgres (`egg_hunt_tried`, read
    and written on a background thread) when there is one, else a JSONL file next to the decisions. Built always;
    GUARDRAILS.md `egg_hunt_enabled` turns it on; env BAZAAR_EGG_HUNT only overrides that (0: off, dry: log only)."""
    from bazaar_agent import db
    from bazaar_agent.agents import egg_hunt

    log = kw["log"]
    store: Any
    if shared:
        pg = egg_hunt.PgStore(lambda: db.connect(app="bazaar-taker-eggs", connect_timeout_s=3), log)
        store = egg_hunt.BackgroundStore(pg, log)  # the Postgres reads and writes never run inside a 15 s tick
    else:
        store = egg_hunt.FileStore(kw["decisions"].dir / egg_hunt.TRIED_FILE)
    return egg_hunt.EggHunter(store, log)


@agent_app.command("taker")
def agent_taker(
    live: bool = typer.Option(False, help=AGENT_LIVE_HELP),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    threads: int = typer.Option(3, min=0, max=6, help="Dealer conversations at once (one per dealer)"),
    jev: bool = typer.Option(True, help="Ask Jev offer_is_worth_accepting (advisory) and spend_pack_slot_now"),
    accept_bids: bool = typer.Option(
        True, "--accept-bids/--no-accept-bids", help="Sell into guarded standing bids above our value"
    ),
    addressed: str = typer.Option(
        "asks",
        "--addressed",
        envvar="BAZAAR_ADDRESSED_OFFERS",
        help="Offers other teams address to us (read from /api/me/offers, no extra request): asks (default: take "
        "their asks like a board ask), all (their bids too, through the sell guards), off. Any other value: off",
    ),
    port: int | None = typer.Option(None, help=PORT_HELP),
    host: str | None = typer.Option(None, help=HOST_HELP),
    evals_every: int | None = typer.Option(None, "--evals-every", min=0, help=EVALS_EVERY_HELP),
    learn: bool = typer.Option(
        True,
        envvar="BAZAAR_LEARN",
        help="Read the live feed into learnings, skip dealers under a learned blocker, archive the feed window "
        "(BAZAAR_LEARN=0 turns it off on a service)",
    ),
    llm_read: bool = typer.Option(
        True,
        envvar="BAZAAR_LLM_READ",
        help="Also read the feed's free text (dealer words, notices) with the runtime LLM, off the tick loop",
    ),
) -> None:
    """Every tick: accept standing asks below their value to us (fee included) and run dealer threads."""
    from bazaar_agent.agents.dealer import template_words
    from bazaar_agent.agents.runtime import no_jev
    from bazaar_agent.agents.taker import Taker, TakerConfig, addressed_mode
    from bazaar_agent.learn.jev_context import offer_situation, with_lessons

    mode, note = addressed_mode(addressed)
    if note:
        err_console.print(f"[yellow]taker: {note}[/yellow]")

    def build(team: Any, public: Any, *, settings: Any, **kw: Any) -> Any:
        rules, log = kw["rules"], kw["log"]
        # Its own store (own memory and connection): thousands of tactic lessons must never trim the feed
        # reader's blockers out of the LiveLearner's memory, which the taker reads before its sends.
        shared = kw["ledger"].where.startswith("postgres")
        bluff = _tactic_book(rules, _learning_store("bazaar-taker-bluff", log) if shared else None, None, log)
        return Taker(
            team,
            public,
            bluff=bluff,
            jev=with_lessons(_offer_jev(settings, rules.jev_timeout_s), _lessons(), offer_situation) if jev else no_jev,
            lessons=_lessons(),
            pack_judge=_pack_judge(settings, rules.jev_timeout_s, rules.jev_cache_ticks) if jev else None,
            swap_jev=_swap_jev(settings, rules) if jev else no_jev,  # no Jev: the team desk sends no swap
            strategy_jev=_strategy_jev(settings, rules) if jev else None,  # no Jev: no ladder probe (SG1)
            words_fn=llm_cli.words_for(settings, rules, template_words),
            config=TakerConfig(max_dealer_threads=threads, accept_bids=accept_bids, addressed=mode),
            cards=_cards_heartbeat(kw, settings),
            news=_news_sentinel(kw, settings),
            personas=_persona_book(kw, shared),
            affinity=_affinity_book(kw, shared),
            eggs=_egg_hunter(kw, shared),
            **kw,
        )

    _run_agent("taker", live, max_ticks, build, port, host, evals_every, learn=learn, llm_read=learn and llm_read)


@agent_app.command("maker")
def agent_maker(
    live: bool = typer.Option(False, help=AGENT_LIVE_HELP),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    jev: bool = typer.Option(True, help="Jev list_price_choice / reprice_or_hold pick among legal prices"),
    venue: bool = typer.Option(True, help="Run our venue: open it once (GUARDRAILS.md), then broker its book"),
    port: int | None = typer.Option(None, help=PORT_HELP),
    host: str | None = typer.Option(None, help=HOST_HELP),
    evals_every: int | None = typer.Option(None, "--evals-every", min=0, help=EVALS_EVERY_HELP),
    learn: bool = typer.Option(
        True,
        envvar="BAZAAR_LEARN",
        help="Score venues at fees announced in the feed for later in a listing's life (BAZAAR_LEARN=0: off)",
    ),
    counter_bids: bool = typer.Option(
        True,
        "--counter-bids/--no-counter-bids",
        envvar="BAZAAR_COUNTER_BIDS",
        help="Answer a team's bid to us below our floor with an ask addressed to it, stepping down to our floor "
        "(BAZAAR_COUNTER_BIDS=0: off)",
    ),
) -> None:
    """Every tick: our venue's broker (and its one opening), then asks for sell candidates and bids for missing
    cards; reprice or cancel stale offers."""
    from bazaar_agent.agents.maker import Maker, MakerConfig
    from bazaar_agent.learn.venues import VenueNotices

    def build(team: Any, public: Any, *, settings: Any, **kw: Any) -> Any:
        matrix = _latest_matrix(kw, settings)  # one read of the taker's matrix for the maker and our notice
        market = _venue_keeper(team, settings, kw, matrix) if venue else None
        notices = VenueNotices(kw["log"]) if learn else None
        jev_ = _maker_jev(settings, kw["rules"]) if jev else None
        sell_market = None  # the dealer sell desk's dealers and curves: Postgres when shared, else API + feed
        if kw["ledger"].where.startswith("postgres"):
            from bazaar_agent import db
            from bazaar_agent.agents.dealer_sell_data import db_loader

            sell_market = db_loader(lambda: db.connect(app="bazaar-maker-sell", connect_timeout_s=3), kw["log"])
        return Maker(
            team,
            public,
            jev=jev_,
            market=market,
            notices=notices,
            sell_market=sell_market,
            strategy_jev=_strategy_jev(settings, kw["rules"]) if jev else None,  # no Jev: no new dealer sell thread
            latest_matrix=matrix,
            config=MakerConfig(counter_bids=counter_bids),
            **kw,
        )

    _run_agent("maker", live, max_ticks, build, port, host, evals_every)


# ---------------------------------------------------------------- our venue and its broker (#11, #12)


def _venue_keeper(team: Any, settings: Any, kw: dict[str, Any], matrix: Any = None) -> Any:
    """Our venue inside the maker: the key vault on the shared Postgres (a redeploy keeps the key); its notice
    names the cards in the team matrix the maker reads (`matrix`: a LatestMatrix, or None)."""
    from bazaar_agent import db
    from bazaar_agent import venue as vn
    from bazaar_agent.agents.venue_keeper import ANNOUNCE_EVERY_TICKS, VenueKeeper

    return VenueKeeper(
        team,
        settings=settings,
        rules=kw["rules"],
        # 3 s, not 10: the vault runs before the maker's own offers, inside its tick window
        vault=vn.KeyVault.from_settings(settings, lambda: db.connect(app="bazaar-maker-venue", connect_timeout_s=3)),
        decisions=kw["decisions"],
        live=kw["live"],
        log=kw["log"],
        hub=kw.get("hub"),
        stats_dir=settings.data_dir / "agents",
        announce_every_ticks=ANNOUNCE_EVERY_TICKS,
        matrix=matrix.current if matrix is not None else None,
    )


VENUE_LIVE_HELP = "Actually send it. Without it: dry run. Live also needs allow_venue_open = true in GUARDRAILS.md"


def _venue_outcome(outcome: Any) -> None:
    colour = "green" if outcome.sent else "yellow" if outcome.verdict.allowed else "red"
    console.print(f"[{colour}]{outcome.message}[/{colour}]")
    if not outcome.verdict.allowed:
        raise typer.Exit(1)


def _our_venue_id(venue: str | None) -> str:
    """The argument, else BAZAAR_VENUE (env, .env, or what a live open saved)."""
    found = venue or load_settings().venue_id
    if not found:
        _fail("which venue? pass it, or set BAZAAR_VENUE (a live `bazaar venue open` saves it)")
    return str(found)


def _venue_write(write: Callable[[], Any]) -> None:
    """Validate, check the guardrails, send only when live: one coloured line, exit 1 when refused."""
    from pydantic import ValidationError

    try:
        _venue_outcome(write())
    except ValidationError as e:
        _fail(f"invalid: {'; '.join(str(err['msg']) for err in e.errors())}")
    except BazaarError as e:
        _fail(f"refused: {e.code} ({e.message[:80]})")
    except ConfigError as e:
        _fail(str(e))


@venue_app.command("open")
def venue_open(
    name: str = typer.Option("Team 1 market", help="Venue name (at most 40 characters)"),
    fee_bps: int = typer.Option(0, help="Fee in basis points (0-1000, i.e. at most 10 %)"),
    fee_per_card: int = typer.Option(0, help="Fee per card in P (0-5)"),
    mechanism: str = typer.Option("board", help="board (our broker matches) or auto (the engine crosses first)"),
    description: str = typer.Option("", help="Public description (at most 280 characters)"),
    live: bool = typer.Option(False, help=VENUE_LIVE_HELP),
) -> None:
    """Open our venue: 250 P bond + 20 P; saves the broker key (Postgres + 0600 file), never prints it.
    On Railway the maker opens it on its own (GUARDRAILS.md `venue_open_after_game_hours`)."""
    from bazaar_agent import venue as vn

    settings = load_settings()
    rules = _rules().rules

    def write() -> Any:
        spec = vn.VenueSpec.model_validate(
            {
                "name": name,
                "fee_bps": fee_bps,
                "fee_per_card": fee_per_card,
                "mechanism": mechanism,
                "description": description,
            }
        )
        vault = vn.KeyVault.from_settings(settings, _db_connect("bazaar-venue"))
        client = _team_client()
        me = client.me()  # the bond is judged like a purchase: on the cash our open offers do not already promise
        me = {**me, "cash": int(me.get("cash") or 0) - _open_commitments(client, me).cash}
        outcome, opened = vn.open_venue(client, spec, rules, live=live, vault=vault, me=me)
        if opened is not None and not opened.saved:  # the key exists only in this process, which now ends
            raise ConfigError(
                f"venue {opened.venue} is OPEN but its broker key could not be saved. "
                f"Close it (`bazaar venue close {opened.venue} --live`, the bond comes back) and open it again."
            )
        return outcome

    _venue_write(write)


@venue_app.command("close")
def venue_close(
    venue: str | None = typer.Argument(None, help="Venue id (default BAZAAR_VENUE)"),
    live: bool = typer.Option(False, help="Actually close it. Without it: dry run"),
) -> None:
    """Close our venue; the bond comes back after a cooldown (a session counts the best venue open in it)."""
    from bazaar_agent import venue as vn

    vid = _our_venue_id(venue)
    _venue_write(lambda: vn.close_venue(_team_client(), vid, _rules().rules, live=live))


@venue_app.command("fee")
def venue_fee(
    fee_bps: int = typer.Argument(help="New fee in basis points (0-1000)"),
    fee_per_card: int | None = typer.Option(None, help="New fee per card in P (0-5); unchanged if left out"),
    venue: str | None = typer.Option(None, help="Venue id (default BAZAAR_VENUE)"),
    live: bool = typer.Option(False, help=VENUE_LIVE_HELP),
) -> None:
    """Announce new fees on our venue; they take effect after the public notice."""
    from bazaar_agent import venue as vn

    vid = _our_venue_id(venue)

    def write() -> Any:
        fee = vn.FeeSpec(fee_bps=fee_bps, fee_per_card=fee_per_card)
        return vn.set_fee(_team_client(), vid, fee, _rules().rules, live=live)

    _venue_write(write)


@venue_app.command("announce")
def venue_announce(
    text: str = typer.Argument(help="The notice (at most 280 characters)"),
    live: bool = typer.Option(False, help=VENUE_LIVE_HELP),
) -> None:
    """Post a notice on our venue with the broker key."""
    from bazaar_agent import venue as vn

    def write() -> Any:
        note = vn.Announcement(text=text)
        broker = vn.broker_client(load_settings())
        return vn.announce(broker, broker.clock(), note, _rules().rules, live=live)

    _venue_write(write)


@venue_app.command("status")
def venue_status() -> None:
    """Read only: the build-only switch, our venue on the public list, what the broker would match now."""
    from bazaar_agent import venue as vn
    from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from

    settings, rules = load_settings(), _rules().rules
    switch = "[green]true[/green]" if rules.allow_venue_open else "[yellow]false (build only)[/yellow]"
    console.print(f"allow_venue_open = {switch} · trading_enabled = {rules.trading_enabled}")
    key = "set" if settings.broker_key else "not set"
    console.print(f"venue id: {settings.venue_id or '-'} · broker key: {key} (never shown)")
    us = _our_team(settings)
    try:
        venues = public_client(settings).venues().get("venues") or []
    except BazaarError as e:
        _fail(f"/api/venues refused: {e.code}")
        return
    ours = [v for v in venues if isinstance(v, dict) and (v.get("owner") == us or v.get("venue") == settings.venue_id)]
    for v in ours:
        mechanism = (v.get("rules") or {}).get("mechanism", "?")
        console.print(
            f"{v.get('venue')} {v.get('name')!r} · {v.get('status')} · {mechanism} · "
            f"{v.get('fee_bps')} bps + {v.get('fee_per_card')} P/card · {v.get('trades')} trades, "
            f"{v.get('pairs')} pairs, {v.get('traders')} traders · pending fee {v.get('pending_fee')}"
        )
    if not ours:
        console.print("we run no venue (the free starter stall is `auto`: a broker cannot act there)")
    if not settings.broker_key:
        return
    try:
        book = BrokerBook.model_validate(vn.broker_client(settings).book())
    except BazaarError as e:
        _fail(f"broker book refused: {e.code}")
        return
    except ConfigError as e:
        _fail(str(e))
        return
    plan = plan_matches(quotes_from(book).quotes, Fee(book.fee_bps, book.fee_per_card))
    console.print(
        f"book: {len(book.offers)} offer(s), {len(book.bench_offers)} bench offer(s) · would match {len(plan)} "
        f"pair(s), quoted surplus {sum(m.surplus for m in plan)} (our own offers not yet excluded)"
    )


@broker_app.command("run")
def broker_run(
    live: bool = typer.Option(False, help=AGENT_LIVE_HELP + "; also needs allow_venue_open = true"),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    feed: bool = typer.Option(True, help="Read bench.started / bench.finished from the public feed"),
) -> None:
    """Every tick: read our venue's book and send the maximum-surplus matches (bench first)."""
    from rich.markup import escape

    from bazaar_agent import venue as vn
    from bazaar_agent.agents.broker import BrokerAgent
    from bazaar_agent.agents.runtime import live_mode, watched_clock
    from bazaar_agent.decisions import DecisionLog

    settings, rules = load_settings(), _rules().rules
    is_live = live_mode(live)

    def log(line: str) -> None:
        console.print(escape(line), soft_wrap=True, highlight=False)

    try:
        broker = vn.broker_client(settings)
    except ConfigError as e:
        _fail(str(e))
        return
    team = team_client(settings) if settings.bazaar_key else None
    if team is None:
        log("broker: BAZAAR_KEY is not set, our own offers cannot be read: bench offers only")
    mode = "LIVE: matches are sent" if is_live else "DRY RUN: nothing is sent (add --live, or BAZAAR_LIVE=1)"
    if is_live and not rules.allow_venue_open:
        mode = "LIVE requested, but allow_venue_open = false: every match is refused (build only)"
    console.print(f"[bold]broker[/bold] · {mode}")
    public = public_client(settings)
    decisions = DecisionLog(settings.data_dir, _db_connect("bazaar-broker"), log, game_url=settings.bazaar_url)
    agent = BrokerAgent(
        broker,
        team,
        us=_our_team(settings),
        rules=rules,
        decisions=decisions,
        live=is_live,
        log=log,
        events=(lambda: public.feed_window(DEFAULT_WINDOW)) if feed else None,
        stats_dir=settings.data_dir / "agents",
    )
    log(f"broker: decisions {decisions.where} · stats {settings.data_dir / 'agents'}/broker_*.jsonl")
    try:
        read_clock = watched_clock(broker.clock, "broker", log)
        run_per_tick(read_clock, traces.per_tick("broker tick", agent.on_tick, agent=True), max_ticks=max_ticks or None)
    finally:
        decisions.close()


# ---------------------------------------------------------------- runtime LLM (RUNTIME.md)
# `bazaar llm`, `bazaar ask`, `bazaar steer` (and `--llm-runtime` in `_root`): see bazaar_agent/llm/cli.py.

llm_cli.register(app)
evals_cli.register(app)
supply_cli.register(app)
learn_cli.register(app)
dealer_finals.register(dealer_app)
persona_cli.register(dealer_app)

# ---------------------------------------------------------------- agent runtime (Claude Agent SDK, README)
# `bazaar agent chat`, `bazaar agent tools`, `bazaar mcp serve`: see bazaar_agent/runtime/cli.py.

runtime_cli.register(agent_app, app)


if __name__ == "__main__":
    app()
