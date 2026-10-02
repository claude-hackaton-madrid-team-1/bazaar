"""`bazaar` — Team 1's command line. Thin wrappers: logic lives in the modules it calls."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import typer
from rich.console import Console

from bazaar_agent import intel, render, traces
from bazaar_agent import telemetry as tm
from bazaar_agent.config import REPO_ROOT, ConfigError, load_settings
from bazaar_agent.feed import DEFAULT_WINDOW, Event, FeedStore, load_events
from bazaar_agent.llm import cli as llm_cli
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
console = Console()

LIVE_HELP = "Merge the live feed window into the captured history"


@app.callback()
def _root(ctx: typer.Context, llm_runtime: str | None = llm_cli.LLM_RUNTIME_OPTION) -> None:
    """Global options. `--llm-runtime` pins the runtime LLM for this run (see RUNTIME.md).

    With BAZAAR_TRACING=1: one root span per command, every console line mirrored into spans.
    """
    llm_cli.pin_runtime(llm_runtime)
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
    from bazaar_agent.agents.dealer import BidPlan, Negotiation, decide, negotiate, template_words

    rules = _rules().rules
    plan = BidPlan(start, step, max_price)
    topic = {"buy": {"pack": item}} if "-" not in item else {"buy": {"card": item}}
    rarity = _rarity_of(item)
    cap = rules.max_price_for(rarity)
    if cap is not None and max_price > cap:
        _fail(f"--max {max_price} is above max_price_{rarity} = {cap} in GUARDRAILS.md")
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
    ledger = gr.Ledger(settings.data_dir / "ledger.jsonl")
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
            ledger.record("accept", c.tick, c.t_hours, int(move.price or 0), item)
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
) -> None:
    """Every tick: log raw /api/duels to .local/duels; with --play, offer/accept inside our limit."""
    from bazaar_agent import guardrails as gr
    from bazaar_agent.agents.duelist import append_jsonl, duel_move, rival_text, template_duel_words
    from bazaar_agent.agents.words import WordsRequest

    rules = _rules().rules
    settings = load_settings()
    client = team_client(settings)
    ledger = gr.Ledger(settings.data_dir / "ledger.jsonl")
    log_path = settings.data_dir / "duels" / "duels.jsonl"
    first_seen: dict[int, int] = {}
    duel_traces = traces.DuelTraces()
    duel_words = llm_cli.words_for(settings, rules, template_duel_words)

    def on_tick(c: Clock) -> None:
        started = time.monotonic()
        try:
            data = client.duels()
        except BazaarError as e:
            console.print(f"tick {c.tick}: /api/duels refused {e.code}")
            duel_traces.read_failed(c.tick, e)
            return
        append_jsonl(log_path, {"tick": c.tick, "response": data})
        duels = [d for d in data.get("duels") or [] if isinstance(d, dict)]
        console.print(f"tick {c.tick}: {len(duels)} live duel(s) logged")
        for d in duels:
            did = d.get("id")
            if not isinstance(did, int):
                continue
            first_seen.setdefault(did, c.tick)
            move = duel_move(
                d,
                c.tick,
                first_seen[did],
                anchor=rules.duel_anchor,
                floor=rules.duel_floor_margin,
                endgame_ticks=rules.duel_endgame_ticks,
            )
            duel_traces.seen(d, c.tick, move)
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
                    continue
                if move.kind == "accept":
                    ledger.record("accept", c.tick, c.t_hours, 0, f"duel:{did}")
            console.print(
                f"  duel {did} {d.get('role')} limit {d.get('your_limit')} rival {d.get('rival_offer')} "
                f"deadline {d.get('deadline')} -> {move.kind} {move.price or ''} ({move.reason})"
            )
            if not play or move.kind == "hold":
                continue
            said: str | None = None
            try:
                if move.kind == "accept":
                    client.duel_accept(did)
                elif move.price is not None:
                    request = WordsRequest(
                        counterparty=f"duel:{did}",
                        price=move.price,
                        step=c.tick - first_seen[did],
                        their_text=rival_text(d),
                        budget_s=max(0.0, action_budget_s(c) - (time.monotonic() - started)),
                        tick=c.tick,
                        tick_seconds=c.tick_seconds,
                    )
                    said = duel_words(request)
                    client.duel_say(did, said, price=move.price, days=move.days)
                duel_traces.sent(did, move, said)
                append_jsonl(log_path, {"tick": c.tick, "duel": did, "move": move.__dict__})
            except BazaarError as e:
                console.print(f"  duel {did}: refused {e.code} ({e.message[:80]})")
                duel_traces.refused(did, e)
                append_jsonl(log_path, {"tick": c.tick, "duel": did, "refused": e.code})
        duel_traces.end_tick(d.get("id") for d in duels)

    console.print(f"duels → {log_path} ({'PLAYING' if play else 'log only'})")
    try:
        run_per_tick(client.clock, traces.per_tick("duels tick", on_tick), max_ticks=max_ticks or None)
    finally:
        duel_traces.close("stopped")


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
    console.print(f"Kill switch: {state} (touch {loaded.rules.pause_file} to stop every write)")


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
    ctx = gr.context_from(client.me(), c.tick, c.t_hours, gr.Ledger(settings.data_dir / "ledger.jsonl"), rules)
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


@app.command()
def monitor(
    db_enabled: bool = typer.Option(True, "--db/--no-db", help="Write to Postgres (JSONL capture always runs)"),
    notify: bool = typer.Option(False, help="macOS notification on every alert"),
    refresh_every: int = typer.Option(5, help="Rebuild dealer curves and competitor profiles every N ticks"),
    max_ticks: int = typer.Option(0, help="Stop after N ticks (0 = run until Ctrl-C)"),
) -> None:
    """The monitoring agent: per tick feed → JSONL + Postgres, traders sync, /me snapshot, new-trader alerts."""
    from bazaar_agent import db
    from bazaar_agent import monitor as mon
    from bazaar_agent.pgconn import Reconnector

    settings = load_settings()
    public, store = public_client(settings), FeedStore(settings.feed_dir)
    team = team_client(settings) if settings.bazaar_key else None
    alerts_path = settings.data_dir / "alerts.jsonl"
    state: dict[str, Any] = {"dealers": {}, "teams": {}, "levels": [], "ticks": 0}

    def open_pg() -> Any:
        try:
            return db.connect_ready("bazaar-monitor")
        except Exception as e:  # recorded on the tick span; Reconnector keeps the tick going on JSONL
            tm.fail_current(e)
            raise

    pg = Reconnector(open_pg, lambda m: console.print(f"[yellow]{m}[/yellow]"))

    def raise_alerts(alerts: list[Any]) -> None:
        mon.append_alerts(alerts_path, alerts)
        traces.alert_events(alerts)
        for a in alerts:
            console.print(f"[bold red]ALERT[/bold red] tick {a.tick} {a.kind} {a.subject}: {a.detail}")
            if notify:
                subprocess.run(
                    ["osascript", "-e", f'display notification "{a.subject}: {a.kind}" with title "Bazaar"'],
                    check=False,
                    capture_output=True,
                )

    def on_tick(c: Clock) -> None:
        state["ticks"] += 1
        newest_before = store.newest_id()
        try:
            window = public.feed_window(DEFAULT_WINDOW)
        except BazaarError as e:
            console.print(f"tick {c.tick}: feed refused {e.code}")
            tm.fail_current(e)
            window = []
        result = store.append(window, DEFAULT_WINDOW)
        traces.feed_capture(result)
        new_events = [e for e in window if newest_before is None or e["id"] > newest_before]
        first = state["ticks"] == 1
        history = list(store.events()) if first or state["ticks"] % refresh_every == 0 else None
        try:
            dealers_after = mon.dealer_snapshots(public.dealers())
            levels_after = public.levels().get("levels") or []
        except BazaarError as e:
            console.print(f"tick {c.tick}: dealers/levels refused {e.code}")
            tm.fail_current(e)
            dealers_after, levels_after = state["dealers"], state["levels"]
        # Known teams win: most feed events carry no level, so a new snapshot must not overwrite one.
        teams_after = {**mon.team_snapshots(history if first and history else new_events), **state["teams"]}
        alerts = (
            mon.detect_changes(
                c.tick,
                {**state["dealers"], **state["teams"]},
                {**dealers_after, **teams_after},
                state["levels"],
                levels_after,
            )
            if not first
            else []
        )
        alerts += mon.event_alerts(new_events)
        traces.trader_changes({**state["dealers"], **state["teams"]}, {**dealers_after, **teams_after})
        state["dealers"], state["teams"], state["levels"] = dealers_after, teams_after, levels_after
        me = None
        if team is not None:
            try:
                me = team.me()
            except BazaarError as e:
                console.print(f"tick {c.tick}: /me refused {e.code}")
                tm.fail_current(e)
        cx = pg.get() if db_enabled else None
        if cx is not None:
            try:
                db.load_events(cx, new_events)
                db.upsert_traders(cx, list(dealers_after.values()) + list(teams_after.values()), c.tick)
                if me is not None:
                    db.save_snapshot(cx, c.tick, me)
                if alerts:
                    db.insert_alerts(cx, alerts)
                if history is not None and not db.load_history(cx, history, c.tick):
                    console.print(f"tick {c.tick}: curves/competitors left to the monitor with older history")
            except Exception as e:
                console.print(f"[yellow]tick {c.tick}: DB write failed ({type(e).__name__}: {str(e)[:80]})[/yellow]")
                tm.fail_current(e)
                pg.drop()
        raise_alerts(alerts)
        traces.monitor_summary(len(new_events), len(dealers_after), len(teams_after), len(levels_after), me)
        gap = " [red]GAP POSSIBLE[/red]" if result.gap_possible else ""
        cash = (
            f" · cash {me.get('cash')} lvl {me.get('level')} score {(me.get('score') or {}).get('score')}" if me else ""
        )
        console.print(
            f"{datetime.now():%H:%M:%S} tick {c.tick}: +{result.new} events (id {result.newest_id}){gap} · "
            f"{len(dealers_after)} dealers, {len(teams_after)} teams, {len(levels_after)} levels{cash}"
        )

    console.print(f"monitor: feed → {store.path}, alerts → {alerts_path}, db {'on' if db_enabled else 'off'}")
    run_per_tick(public.clock, traces.per_tick("monitor tick", on_tick), max_ticks=max_ticks or None)


@app.command()
def traders() -> None:
    """Every trader we know (dealers and teams) from the monitor's Postgres table, with status and level."""
    from rich.table import Table

    from bazaar_agent import db

    with db.connect(load_settings().database_url.get_secret_value()) as cx:
        rows = cx.execute(
            "select id, kind, name, status, level, first_seen_tick, last_seen_tick from traders order by kind, id"
        ).fetchall()
    t = Table(title=f"Traders · {len(rows)} (kept current by `bazaar monitor`)")
    for col in ("id", "kind", "name", "status", "level", "first seen", "last seen"):
        t.add_column(col)
    for r in rows:
        t.add_row(*["-" if v is None else str(v) for v in r])
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


@obs_app.command("up")
def obs_up() -> None:
    """Start Arize Phoenix (docker compose): UI and OTLP/HTTP on 127.0.0.1:6006, OTLP/gRPC on :4317."""
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "phoenix"], cwd=REPO_ROOT, check=True)
    hint = "" if tm.tracing_config().enabled else " · tracing is OFF: export BAZAAR_TRACING=1 (or add it to .env)"
    console.print(f"[green]Phoenix is up[/green]: open {tm.DEFAULT_PHOENIX_URL}{hint}")


@obs_app.command("status")
def obs_status() -> None:
    """Whether tracing is on, where spans go, the Phoenix UI, and whether Phoenix answers."""
    import httpx

    cfg = tm.tracing_config()
    try:
        reply = httpx.get(f"{cfg.ui_url}/healthz", timeout=2.0)
        health = f"up (HTTP {reply.status_code})" if reply.is_success else f"answers HTTP {reply.status_code}"
    except httpx.HTTPError as e:
        health = f"unreachable ({type(e).__name__}): `uv run bazaar obs up`"
    console.print(render.obs_table(cfg.enabled, cfg.endpoint, cfg.ui_url, cfg.project, cfg.api_key is not None, health))


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
        counts = db.load_feed(conn, _events(live))
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
    from bazaar_agent.jev import judge, load_questions

    questions = load_questions(REPO_ROOT / "questions" / "packs.json")
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None

    def ask(state: dict[str, Any]) -> tuple[str, float]:
        verdict = judge(state, questions, api_key=key, timeout_s=timeout_s).verdicts["spend_pack_slot_now"]
        return verdict.verdict, verdict.value

    return ask


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

    from bazaar_agent import guardrails as gr
    from bazaar_agent import strategy as st
    from bazaar_agent.agents.seller import committed_context
    from bazaar_agent.pack_gate import gate_packs

    loaded, rules = _strategy(), _rules().rules
    settings = load_settings()
    client, me = _team_me()
    commitments = _open_commitments(client, me)
    public = public_client(settings)
    now = Clock.model_validate(public.clock())
    personas = public.dealers()
    dealers_now = personas.get("personas") or personas.get("dealers") or []
    book = st.build_playbook(me, public.catalog(), _events(live), dealers_now, loaded.params, rules)
    ledger = gr.Ledger(settings.data_dir / "ledger.jsonl")
    ctx = committed_context(gr.context_from(me, now.tick, now.t_hours, ledger, rules), commitments)
    used = ledger.packs_since(now.t_hours - 1.0)
    slots = st.PackSlots(sum(used.values()), rules.max_packs_per_game_hour)
    book = gate_packs(book, _pack_judge(settings, rules.jev_timeout_s), slots, used, rules, now.t_hours)
    book = st.guarded(book, ctx, rules, commitments.listed)
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
    ledger = gr.Ledger(load_settings().data_dir / "ledger.jsonl")
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


# ---------------------------------------------------------------- runtime LLM (RUNTIME.md)
# `bazaar llm`, `bazaar ask`, `bazaar steer` (and `--llm-runtime` in `_root`): see bazaar_agent/llm/cli.py.

llm_cli.register(app)


if __name__ == "__main__":
    app()
