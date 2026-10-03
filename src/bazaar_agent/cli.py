"""`bazaar` — Team 1's command line. Thin wrappers: logic lives in the modules it calls."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

import typer
from rich.console import Console

from bazaar_agent import intel, render, traces
from bazaar_agent import telemetry as tm
from bazaar_agent.config import REPO_ROOT, ConfigError, load_settings
from bazaar_agent.evals import cli as evals_cli
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore, load_events
from bazaar_agent.llm import cli as llm_cli
from bazaar_agent.runtime import cli as runtime_cli
from bazaar_agent.sdk import BazaarError, public_client, team_client
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
console = Console()
err_console = Console(stderr=True)

LIVE_HELP = "Merge the live feed window into the captured history"


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
    # Warnings (e.g. Phoenix unreachable) go to stdout with the console lines: a container platform
    # such as Railway files stderr as errors. A no-op when logging is already configured.
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
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
        console.print("[yellow]no captured feed yet: reading the live window[/yellow]")
        events = load_events(store, public_client(settings).feed_window(DEFAULT_WINDOW))
    return events


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _ledger(source: str) -> Any:
    """The guardrail ledger every process shares: Postgres when DATABASE_URL answers, else the JSONL file."""
    from rich.markup import escape

    from bazaar_agent.ledger_pg import open_ledger

    # stderr: a command's stdout may be JSON (`strategy --json`), and this line is only context
    return open_ledger(
        load_settings().data_dir, source=source, log=lambda m: err_console.print(f"[dim]{escape(m)}[/dim]")
    )


# ---------------------------------------------------------------- public views (no key)


def _tl_fixture(kind: str) -> Any:
    from bazaar_agent import timeline as tl

    return REPO_ROOT / (tl.SCHEDULE_FIXTURE if kind == "schedule" else tl.CLOCK_FIXTURE)


@app.command()
def clock() -> None:
    """Current tick, pace, doors, per-tick limits and the action budget left in this tick."""
    console.print(render.clock_table(Clock.model_validate(public_client(load_settings()).clock())))


@app.command("timeline")
def timeline_cmd(
    schedule: str = typer.Option(str(_tl_fixture("schedule")), "--schedule", help="A /api/schedule JSON file"),
    clock_file: str = typer.Option(str(_tl_fixture("clock")), "--clock", help="A /api/clock JSON file"),
    from_api: bool = typer.Option(False, "--from-api", help="Read /api/clock and /api/schedule keyless instead"),
    frozen_at: float | None = typer.Option(None, "--frozen-at", help="Treat the clock as closed at this game hour"),
    at: str | None = typer.Option(None, "--at", help="The wall time to plan from (ISO 8601; default: now)"),
    teams: int = typer.Option(18, "--teams", help="Teams in the duel round-robin"),
    plays: str | None = typer.Option(None, "--plays", help="A plays JSON to attach (docs/night/saturday-plays.json)"),
    compare: str | None = typer.Option(
        None, "--compare", help="A committed timeline JSON (docs/night/saturday-schedule.json): list what moved"
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the timeline as JSON"),
) -> None:
    """Every scheduled event in game hours and Madrid time: `resume` and `jump` columns while closed, `live` open.

    Read-only: files by default; `--from-api` makes two keyless GETs. No team key, nothing written.
    """
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from bazaar_agent import timeline as tl

    madrid = ZoneInfo("Europe/Madrid")
    now = datetime.fromisoformat(at) if at else datetime.now(madrid)
    if now.tzinfo is None:  # a wall time without an offset is Madrid time, like the calendar
        now = now.replace(tzinfo=madrid)
    clock_doc: Any
    sched_doc: Any
    if from_api:
        api = public_client(load_settings())
        clock_doc, sched_doc, source = api.clock(), api.schedule(), "api"
    else:
        clock_doc, sched_doc = tl.load(Path(clock_file)), tl.load(Path(schedule))
        source = f"{Path(schedule).name} + {Path(clock_file).name}"
    if frozen_at is not None:
        clock_doc = tl.frozen(clock_doc, frozen_at, now)
    events, days = tl.parse_events(sched_doc), tl.parse_days(clock_doc)
    found = tl.anchors(clock_doc, events, now)
    rows = tl.timeline(events, days, found, teams)
    if compare:
        committed = json.loads(Path(compare).read_text(encoding="utf-8"))
        now_hours = float(tl.body(clock_doc).get("t_hours") or 0.0)  # /api/schedule lists only what is upcoming
        changes = tl.schedule_changes(committed, tl.as_dict(rows, found, ""), now_hours)
        typer.echo("\n".join(changes) if changes else f"no event added, removed or re-timed against {compare}")
        return
    if as_json:
        doc = tl.as_dict(rows, found, source)
        if plays:
            doc = tl.with_plays(doc, json.loads(Path(plays).read_text(encoding="utf-8")))
        typer.echo(json.dumps(doc, indent=2, ensure_ascii=False))
        return
    anchored = ", ".join(f"{a.name}: h{a.t_hours:g} = {a.wall:%a %H:%M}" for a in found)
    typer.echo(f"{source} · {anchored}")
    for line in tl.render(rows, found):
        typer.echo(line)


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
def status(cards: bool = typer.Option(True, help="Also list our cards with your_value")) -> None:
    """Our cash, level, score, album pages with missing cards, and cards (GET /api/me)."""
    try:
        me: dict[str, Any] = team_client(load_settings()).me()
    except ConfigError as e:
        _fail(str(e))
    except BazaarError as e:
        _fail(f"/api/me refused: {e.code} ({e.status})")
    console.print(render.status_table(me, load_settings().target_line()))
    from bazaar_agent.album import album_view

    console.print(render.album_table(album_view(me, public_client(load_settings()).catalog())))
    if cards:
        console.print(render.cards_table(me))


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
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.dealer import BidPlan, bid_schedule, negotiate, template_words

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
            f"[yellow]dry run[/yellow] {dealer} {topic}: bids {schedule}, accept any ask ≤ next bid, "
            f"walk above {max_price}. Add --live to trade."
        )
        return
    settings = load_settings()
    client = team_client(settings)
    ledger = _ledger("dealer-buy")
    clock_now = Clock.model_validate(client.clock())
    pre = gr.check(
        gr.Action("buy", item, rarity, start),
        gr.context_from(client.me(), clock_now.tick, clock_now.t_hours, ledger, rules),
        rules,
    )
    if not pre.allowed:
        tm.guardrail_refusal("dealer.open", item, pre.violations)
        _fail(f"guardrails refuse to open this thread: {pre}")

    def guard(move: Any) -> str | None:
        c = Clock.model_validate(client.clock())
        ctx = gr.context_from(client.me(), c.tick, c.t_hours, ledger, rules)
        kind: gr.ActionKind = "accept_buy" if move.kind == "accept" else "bid"
        verdict = gr.check(gr.Action(kind, item, rarity, move.price), ctx, rules)
        if verdict.allowed and move.kind == "accept":
            limit = min(rules.max_accepts_per_tick, c.limits.accepts_per_team_per_tick)
            if not ledger.reserve_accept(c.tick, c.t_hours, int(move.price or 0), item, limit):
                return "another process took the team's accept this tick (shared ledger)"
            tm.event("ledger", {"kind": "accept", "tick": c.tick, "price": move.price, "item": item})
        return None if verdict.allowed else "; ".join(verdict.violations)

    def on_deal(price: int, tick: int, t_hours: float) -> None:
        ledger.record("spend", tick, t_hours, price, item)
        tm.event("ledger", {"kind": "spend", "tick": tick, "price": price, "item": item})

    advisor = _jev_advisor(item, settings, rules.jev_timeout_s) if jev and rules.jev_can_accept_early else None
    with traces.trace_negotiation(dealer, topic, plan) as observer:
        out = negotiate(
            client,
            dealer,
            topic,
            plan,
            log=console.print,
            advisor=advisor,
            guard=guard,
            on_deal=on_deal,
            max_ticks=rules.dealer_max_ticks_per_thread,
            observer=observer,
            words_fn=llm_cli.words_for(settings, rules, template_words),
        )
    colour = "green" if out.status == "deal" else "red"
    console.print(
        f"[{colour}]{out.status}[/{colour}] thread {out.thread} price {out.price} bids {list(out.bids)} "
        f"in {out.ticks} ticks"
    )


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
) -> None:
    """Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit."""
    from rich.markup import escape

    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.duel_jev import DuelPick
    from bazaar_agent.agents.duelist import (
        DuelMove,
        append_jsonl,
        duel_deadline,
        duel_id,
        duel_move,
        rival_text,
        template_duel_words,
    )
    from bazaar_agent.agents.runtime import Recorder
    from bazaar_agent.agents.words import WordsRequest
    from bazaar_agent.decisions import DecisionLog, Status
    from bazaar_agent.duel_store import DuelStore, duel_list
    from bazaar_agent.llm.steering import STEERING_FILE, steered_duel_params

    rules = _rules().rules
    settings = load_settings()
    client = team_client(settings)
    ledger = _ledger("duels")
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
    first_seen: dict[int, int] = {}
    sent: dict[int, int] = {}  # messages we sent per duel (the words' `step`)
    duel_traces = traces.DuelTraces()
    duel_words = llm_cli.words_for(settings, rules, template_duel_words)

    def send(d: dict[str, Any], did: int, move: DuelMove, c: Clock, send_by: float) -> Status:
        said: str | None = None
        try:
            if move.kind == "accept":
                client.duel_accept(did)
                if duel_jev is not None:
                    duel_jev.outcomes.accepted(did, int(move.price or 0))
            elif move.price is not None:
                budget = max(0.0, send_by - time.monotonic())
                request = WordsRequest(f"duel:{did}", move.price, sent.get(did, 0), None, rival_text(d), budget)
                said = duel_words(replace(request, tick=c.tick, tick_seconds=c.tick_seconds))
                if time.monotonic() > send_by:
                    console.print(f"  duel {did}: the words took the rest of the tick, offering next tick")
                    return "expired"
                client.duel_say(did, said, price=move.price, days=move.days)
                sent[did] = sent.get(did, 0) + 1
            duel_traces.sent(did, move, said)
            append_jsonl(log_path, {"tick": c.tick, "duel": did, "move": move.__dict__})
            return "done"
        except BazaarError as e:
            console.print(f"  duel {did}: refused {e.code} ({e.message[:80]})")
            duel_traces.refused(did, e)
            append_jsonl(log_path, {"tick": c.tick, "duel": did, "refused": e.code})
            return "failed"

    def record(
        d: dict[str, Any], move: DuelMove, pick: DuelPick | None, tick: int, status: Status, guardrail: str = "allowed"
    ) -> None:
        """One `decisions` row per duel per tick: the state Jev read, its verdict and floats, what we did."""
        offer = d.get("rival_offer")
        rival = offer if isinstance(offer, dict) else {}
        inputs = pick.state.get("duel", {}) if pick is not None else {}
        inputs = inputs or {"role": d.get("role"), "our_limit": d.get("your_limit"), "rival_price": rival.get("price")}
        if pick is not None and pick.days is not None:
            inputs = {**inputs, "jev_days": pick.days.as_dict()}
        rec.decide(
            tick,
            f"duel_{move.kind}",
            f"duel {duel_id(d)} {move.kind} {move.price or ''}",
            inputs=inputs,
            reason=move.reason + (f"; {pick.why}" if pick is not None else ""),
            guardrail=guardrail,
            chosen=move.kind != "hold" and status in ("approved", "done"),
            status=status,
            jev=pick.advice if pick is not None else None,
            move={"duel": duel_id(d), "kind": move.kind, "price": move.price, "days": move.days},
        )

    def save_finished(tick: int) -> None:
        """One `?done=true` read on a tick where a duel left the live list: its price, rounds and result."""
        try:
            data = client.duels(done=True)
        except BazaarError as e:
            console.print(f"tick {tick}: /api/duels?done=true refused {e.code}")
            return
        append_jsonl(log_path, {"tick": tick, "response": data, "done": True})
        store.save(tick, [d for d in duel_list(data) if d.get("status") != "live"])

    def on_tick(c: Clock) -> None:
        send_by = time.monotonic() + action_budget_s(c)
        decisions.begin_tick(c.tick)
        anchor, floor = steered_duel_params(rules, settings.data_dir / STEERING_FILE, c.tick)
        try:
            data = client.duels()
        except BazaarError as e:
            console.print(f"tick {c.tick}: /api/duels refused {e.code}")
            duel_traces.read_failed(c.tick, e)
            return
        append_jsonl(log_path, {"tick": c.tick, "response": data})
        duels = duel_list(data)
        console.print(f"tick {c.tick}: {len(duels)} live duel(s) logged")
        live_ids = [did for did in map(duel_id, duels) if did is not None]
        for live_id in live_ids:
            first_seen.setdefault(live_id, c.tick)
        picks: dict[int, DuelPick] = {}
        if duel_jev is not None:  # every live duel at once, so a duel accept still lands early in the tick
            endgame = rules.duel_endgame_ticks
            left = lambda: send_by - time.monotonic()  # noqa: E731
            try:
                picks = duel_jev.pick(
                    duels, c.tick, first_seen, anchor=anchor, floor=floor, endgame_ticks=endgame, left=left
                )
            except Exception as e:  # a bug in the Jev layer must never cost a duel its move
                console.print(f"  duel jev failed ({type(e).__name__}): today's moves this tick")
        for d in duels:
            did = duel_id(d)
            if did is None:
                continue
            pick = picks.get(did)
            move = (
                pick.move
                if pick is not None
                else duel_move(
                    d, c.tick, first_seen[did], anchor=anchor, floor=floor, endgame_ticks=rules.duel_endgame_ticks
                )
            )
            duel_traces.seen(d, c.tick, move)
            if pick is not None:
                duel_traces.jev(did, pick)
            if play and move.kind in ("accept", "offer") and time.monotonic() >= send_by:
                console.print(f"  duel {did}: no time left in tick {c.tick}, {move.kind} next tick")
                record(d, move, pick, c.tick, "expired")
                continue
            if play and move.kind in ("accept", "offer"):
                kind: gr.ActionKind = "duel_accept" if move.kind == "accept" else "duel_offer"
                ctx = gr.Context(
                    cash=0,
                    held={},
                    tick=c.tick,
                    t_hours=c.t_hours,
                    accepts_this_tick=ledger.accepts_in_tick(c.tick),
                    paused=(REPO_ROOT / rules.pause_file).exists(),
                )
                verdict = gr.check(gr.Action(kind, str(did), None, None), ctx, rules)
                duel_traces.guardrail(did, verdict.allowed, verdict.violations)
                if not verdict.allowed:
                    console.print(f"  duel {did}: GUARDRAIL {verdict}")
                    record(d, move, pick, c.tick, "rejected", str(verdict))
                    continue
                limit = min(rules.max_accepts_per_tick, c.limits.accepts_per_team_per_tick)
                if move.kind == "accept" and not ledger.reserve_accept(c.tick, c.t_hours, 0, f"duel:{did}", limit):
                    console.print(f"  duel {did}: another process took the team's accept this tick")
                    record(d, move, pick, c.tick, "rejected", "accept slot taken by another process")
                    continue
            # The rival's offer may carry text: escaped, so a stray "[/red]" cannot crash the loop.
            console.print(
                f"  duel {did} {d.get('role')} limit {d.get('your_limit')} rival {escape(str(d.get('rival_offer')))} "
                f"deadline {duel_deadline(d)} -> {move.kind} {move.price or ''} ({escape(move.reason)})"
                + (f" · {escape(pick.why)}" if pick is not None else "")
            )
            status: Status = send(d, did, move, c, send_by) if play and move.kind != "hold" else "approved"
            record(d, move, pick, c.tick, status)
        duel_traces.end_tick(duel_id(d) for d in duels)
        if duel_jev is not None:
            try:
                for line in duel_jev.outcomes.settle(live_ids, c.tick):
                    console.print(f"  {escape(line)}")
            except Exception as e:  # calibration is a side record: it never breaks the loop
                console.print(f"  duel jev outcomes failed ({type(e).__name__})")

        store.save(c.tick, duels)  # after the sends: the evals read duels from Postgres, never the API
        if store.read_finished(duels):
            save_finished(c.tick)

    mode = f"{'PLAYING' if play else 'log only'}{', Jev duel_move' if jev else ''}"
    console.print(f"duels → {log_path} + Postgres duels ({mode})")
    try:
        run_per_tick(client.clock, traces.per_tick("duels tick", on_tick), max_ticks=max_ticks or None)
    finally:
        duel_traces.close("stopped")
        decisions.close()
        store.close()


def _db_connect(app: str) -> Callable[[], Any]:
    from bazaar_agent import db

    return lambda: db.connect(app=app)


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


def _duel_jev(settings: Any, rules: Any) -> Any:
    """Jev `duel_move` + `rival_cares_about_days` (questions/duels.json) as the duel player's decision model."""
    from bazaar_agent.agents.duel_jev import DAYS_QUESTION, MOVE_QUESTION, DuelJev

    journal = _jev_journal(settings)
    move_fn, days_fn = _jev_fns(settings, rules, journal, "duels.json", MOVE_QUESTION, DAYS_QUESTION)
    return DuelJev(move_fn, days_fn, can_accept_early=rules.jev_can_accept_early, journal=journal)


def _maker_jev(settings: Any, rules: Any) -> Any:
    """Jev `list_price_choice` + `reprice_or_hold` (questions/maker.json) as the maker's decision model."""
    from bazaar_agent.agents.maker_jev import PRICE_QUESTION, REPRICE_QUESTION, MakerJev

    journal = _jev_journal(settings)
    price_fn, reprice_fn = _jev_fns(settings, rules, journal, "maker.json", PRICE_QUESTION, REPRICE_QUESTION)
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

    from bazaar_agent.guardrails import ENFORCED_BY

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
    pause = REPO_ROOT / loaded.rules.pause_file
    state = (
        "[red]PAUSED[/red]" if pause.exists() or not loaded.rules.trading_enabled else "[green]trading enabled[/green]"
    )
    console.print(f"Kill switch: {state} (touch {loaded.rules.pause_file} to stop the writes run from this checkout)")


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
    ctx = gr.context_from(client.me(), c.tick, c.t_hours, _ledger("rules-check"), rules)
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


def _team_me() -> tuple[Any, dict[str, Any]]:
    client = _team_client()
    try:
        return client, client.me()
    except BazaarError as e:
        console.print(f"[red]/api/me refused: {e.code} ({e.status})[/red]")
    raise typer.Exit(1)


def _open_commitments(client: Any, me: dict[str, Any]) -> Any:
    """Our open offers as commitments; exits when they cannot be read (guardrails never check blind)."""
    from bazaar_agent.agents.seller import offers_in, open_commitments

    try:
        return open_commitments(offers_in(client.my_offers()), str(me.get("id") or ""))
    except BazaarError as e:
        console.print(f"[red]/api/me/offers refused: {e.code} ({e.status}); not checking guardrails blind[/red]")
    raise typer.Exit(1)


def _pack_judge(settings: Any, timeout_s: float) -> Any:
    """Jev `spend_pack_slot_now` (questions/packs.json): (verdict, probability of yes) for one pack state."""
    from bazaar_agent.pack_gate import jev_pack_judge

    return jev_pack_judge(settings, timeout_s)


def _print_playbook(book: Any, loaded: Any, rules: Any, ctx: Any, commitments: Any) -> None:
    slots = book.pack_slots
    quotas = ", ".join(f"{pack} dealer quota {n}" for pack, n in book.pack_quotas.items()) or "no dealer quota"
    console.print(
        f"tick {book.tick} · cash {book.cash} ({commitments.cash} promised by our open offers), "
        f"{max(0, ctx.cash - rules.cash_floor)} above cash_floor {rules.cash_floor} · spent last game hour "
        f"{ctx.spent_last_hour}/{rules.max_spend_per_game_hour} · pack slots this game hour: used {slots.used}, "
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


# ---------------------------------------------------------------- our offers: sell list / bid / offers / cancel

sell_app = typer.Typer(no_args_is_help=True, help="Our offers on a venue: list a card, bid for one, see or cancel ours")
app.add_typer(sell_app, name="sell")
EXPIRES_HELP = "Ticks the offer stays open"
POST_HELP = "Actually post. Without it: dry run, nothing is sent"


def _post_offer(client: Any, me: dict[str, Any], listing: Any, live: bool, expires: int) -> None:
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.seller import post

    rules = _rules().rules
    ledger = _ledger("sell")
    now = Clock.model_validate(client.clock())
    ctx = gr.context_from(me, now.tick, now.t_hours, ledger, rules)
    commitments = _open_commitments(client, me)
    try:
        out = post(
            client, listing, ctx, rules, live=live, expires_in_ticks=expires, ledger=ledger, commitments=commitments
        )
    except BazaarError as e:
        _fail(f"offer refused: {e.code} ({e.message[:80]})")
        return
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
    live: bool = typer.Option(False, help=POST_HELP),
) -> None:
    """List one card for cash (give the asset, want cash), never below its your_value (GUARDRAILS.md)."""
    from bazaar_agent.agents.seller import OfferError, sell_listing

    client, me = _team_me()
    try:
        listing = sell_listing(me, target, price, venue)
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
    live: bool = typer.Option(False, help=POST_HELP),
) -> None:
    """Bid cash for any copy of a card (give cash, want the card): how we buy rares only teams hold."""
    from bazaar_agent.agents.seller import OfferError, bid_listing

    client, me = _team_me()
    try:
        listing = bid_listing(ref, _rarity_of(ref), price, venue)
    except OfferError as e:
        _fail(str(e))
        return
    _post_offer(client, me, listing, live, expires)


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
    """Withdraw one of our open offers."""
    if not live:
        console.print(f"[yellow]dry run[/yellow] would cancel offer {offer_id}. Add --live to cancel.")
        return
    try:
        _team_client().cancel(offer_id)
    except BazaarError as e:
        _fail(f"cancel refused: {e.code} ({e.message[:80]})")
        return
    console.print(f"[green]cancelled offer {offer_id}[/green]")


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


def _status_port(port: int | None) -> int:
    """`--port`, else Railway's PORT, else 0 (no status server on a laptop unless asked)."""
    import os

    if port is not None:
        return port
    raw = os.environ.get("PORT", "").strip()
    return int(raw) if raw.isdigit() else 0


def _run_agent(
    name: str, live: bool, max_ticks: int, build: Callable[..., Any], port: int | None = None, host: str | None = None
) -> None:
    """Shared wiring: settings, guardrails, strategy, the shared ledger, the decision log, the feed, the
    read-only status server, the loop."""
    from rich.markup import escape

    from bazaar_agent import db
    from bazaar_agent.agents.runtime import MarketFeed, live_mode, watched_clock
    from bazaar_agent.agents.status import StatusHub, start_status_server
    from bazaar_agent.decisions import DecisionLog
    from bazaar_agent.ledger_pg import open_ledger
    from bazaar_agent.llm.steering import STEERING_FILE, steered_strategy_params

    loaded, rules = _strategy(), _rules().rules
    settings = load_settings()
    team, public = _team_client(), public_client(settings)
    is_live = live_mode(live)

    def log(line: str) -> None:
        console.print(escape(line), soft_wrap=True, highlight=False)

    def connect() -> Any:
        return db.connect(app=f"bazaar-{name}")

    mode = "LIVE: trades are sent" if is_live else "DRY RUN: nothing is sent (add --live, or BAZAAR_LIVE=1)"
    console.print(f"[bold]{name}[/bold] · {mode} · {settings.target_line()}")
    ledger = open_ledger(settings.data_dir, source=name, log=log)
    decisions = DecisionLog(settings.data_dir, connect, log)
    feed = MarketFeed(public.feed_window, FeedStore(settings.feed_dir), connect, log)

    def params(tick: int) -> Any:
        return steered_strategy_params(loaded.params, rules, settings.data_dir / STEERING_FILE, tick)

    hub = StatusHub(name, is_live, target=settings.target)
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
    )
    log(f"{name}: ledger {ledger.where} · decisions {decisions.where}")
    try:
        read_clock = watched_clock(team.clock, name, log, hub)
        run_per_tick(read_clock, traces.per_tick(f"{name} tick", agent.on_tick), max_ticks=max_ticks or None)
    finally:
        decisions.close()


@agent_app.command("taker")
def agent_taker(
    live: bool = typer.Option(False, help=AGENT_LIVE_HELP),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    threads: int = typer.Option(3, min=0, max=6, help="Dealer conversations at once (one per dealer)"),
    jev: bool = typer.Option(True, help="Ask Jev offer_is_worth_accepting (advisory) and spend_pack_slot_now"),
    port: int | None = typer.Option(None, help=PORT_HELP),
    host: str | None = typer.Option(None, help=HOST_HELP),
) -> None:
    """Every tick: accept standing asks below their value to us (fee included) and run dealer threads."""
    from bazaar_agent.agents.dealer import template_words
    from bazaar_agent.agents.runtime import no_jev
    from bazaar_agent.agents.taker import Taker, TakerConfig

    def build(team: Any, public: Any, *, settings: Any, **kw: Any) -> Any:
        rules = kw["rules"]
        return Taker(
            team,
            public,
            jev=_offer_jev(settings, rules.jev_timeout_s) if jev else no_jev,
            pack_judge=_pack_judge(settings, rules.jev_timeout_s) if jev else None,
            words_fn=llm_cli.words_for(settings, rules, template_words),
            config=TakerConfig(max_dealer_threads=threads),
            **kw,
        )

    _run_agent("taker", live, max_ticks, build, port, host)


@agent_app.command("maker")
def agent_maker(
    live: bool = typer.Option(False, help=AGENT_LIVE_HELP),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
    jev: bool = typer.Option(True, help="Jev list_price_choice / reprice_or_hold pick among legal prices"),
    port: int | None = typer.Option(None, help=PORT_HELP),
    host: str | None = typer.Option(None, help=HOST_HELP),
) -> None:
    """Every tick: post asks for sell candidates and bids for missing cards; reprice or cancel stale offers."""
    from bazaar_agent.agents.maker import Maker

    def build(team: Any, public: Any, *, settings: Any, **kw: Any) -> Any:
        return Maker(team, public, jev=_maker_jev(settings, kw["rules"]) if jev else None, **kw)

    _run_agent("maker", live, max_ticks, build, port, host)


# ---------------------------------------------------------------- runtime LLM (RUNTIME.md)
# `bazaar llm`, `bazaar ask`, `bazaar steer` (and `--llm-runtime` in `_root`): see bazaar_agent/llm/cli.py.

llm_cli.register(app)
evals_cli.register(app)

# ---------------------------------------------------------------- agent runtime (Claude Agent SDK, README)
# `bazaar agent chat`, `bazaar agent tools`, `bazaar mcp serve`: see bazaar_agent/runtime/cli.py.

runtime_cli.register(agent_app, app)


if __name__ == "__main__":
    app()
