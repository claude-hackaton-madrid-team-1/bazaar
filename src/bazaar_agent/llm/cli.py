"""CLI for the runtime LLM: `bazaar llm`, `bazaar ask`, `bazaar steer`, and the `--llm-runtime` option.

Thin wrappers registered onto the main app by `register()`; the logic lives in the llm modules.
Nothing here trades: `ask` prints the guardrail verdict and the command, `steer` writes only
`.local/steering.json`. Keys are reported as set or not set, never printed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.agents.words import WordsFn
from bazaar_agent.config import Settings, load_settings
from bazaar_agent.guardrails import Guardrails, GuardrailsError, load_guardrails
from bazaar_agent.jev import JevUsageError, load_questions
from bazaar_agent.llm.chooser import QUESTION_FILE, ModelChoice, model_question, read_choices
from bazaar_agent.llm.config import LoadedRuntime, RuntimeConfigError, load_runtime
from bazaar_agent.llm.intent import Clarification, Intent, command_for, guardrail_action, parse_request, rarity_of
from bazaar_agent.llm.models import ALIASES, UnknownModelError, pinned_model, resolve
from bazaar_agent.llm.providers import KEY_VARIABLES, LLMError, api_key_for
from bazaar_agent.llm.runtime import CHOICE_LOG, LLMRuntime, build_runtime
from bazaar_agent.llm.steering import (
    STEERABLE,
    STEERING_FILE,
    Steering,
    apply_steering,
    base_params,
    clear_steering,
    load_steering,
    save_steering,
    steer_request,
)
from bazaar_agent.llm.words import llm_words
from bazaar_agent.ticks import Clock

console = Console()
STATE: dict[str, str | None] = {"pin": None}
RUNTIME_HELP = "Pin the runtime LLM for this run: an alias (opus-5-5, sonnet-5-5, haiku-4-5, fable-5-1, gpt-6-1-sol) or a model id. Wins over BAZAAR_LLM_RUNTIME and RUNTIME.md; skips the Jev model choice."  # noqa: E501


def _fail(message: str) -> NoReturn:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


# The main app's root callback declares this option and passes its value to `pin_runtime()`
# (Typer keeps a single root callback, which also starts tracing).
LLM_RUNTIME_OPTION: Any = typer.Option(None, "--llm-runtime", metavar="ALIAS|MODEL_ID", help=RUNTIME_HELP)


def pin_runtime(llm_runtime: str | None) -> None:
    STATE["pin"] = llm_runtime


def _load() -> tuple[Settings, LoadedRuntime, Guardrails]:
    try:
        loaded = load_runtime()
    except RuntimeConfigError as e:
        _fail(f"RUNTIME.md is invalid: {e}")
    try:
        rules = load_guardrails().rules
    except GuardrailsError as e:
        _fail(f"GUARDRAILS.md is invalid: {e}")
    return load_settings(), loaded, rules


def _runtime(settings: Settings, loaded: LoadedRuntime, rules: Guardrails) -> LLMRuntime:
    try:
        return build_runtime(settings, loaded.config, rules, cli_pin=STATE["pin"])
    except UnknownModelError as e:
        _fail(f"cannot pin the runtime LLM: {e}")


def _public_clock(settings: Settings) -> Clock | None:
    from bazaar_agent.sdk import public_client

    try:
        return Clock.model_validate(public_client(settings).clock())
    except Exception as e:  # the clock is advisory here: a missing tick only disables the cache
        console.print(f"[yellow]game clock unavailable ({type(e).__name__}): no tick for the model cache[/yellow]")
        return None


def words_for(settings: Settings, rules: Guardrails, fallback: WordsFn) -> WordsFn:
    """The words writer for a trading loop: the runtime LLM when RUNTIME.md `llm_words` = true, else `fallback`.

    Providers are built here, before the first tick, so no SDK import or client setup eats a tick.
    """
    try:
        loaded = load_runtime()
        if not loaded.config.llm_words:
            return fallback
        runtime = build_runtime(settings, loaded.config, rules, cli_pin=STATE["pin"])
        runtime.warm()
    except (RuntimeConfigError, UnknownModelError, LLMError) as e:
        console.print(f"[yellow]runtime LLM off ({escape(str(e))}): template words[/yellow]")
        return fallback
    console.print("words: runtime LLM (llm_words = true), templates on any failure")
    return llm_words(runtime, fallback, log=lambda line: console.print(escape(line)))


def _floats(probabilities: Any) -> str:
    ordered = sorted(dict(probabilities or {}).items(), key=lambda kv: -float(kv[1]))
    return " · ".join(f"{name} {float(p):.3f}" for name, p in ordered) or "-"


def _print_choice(choice: ModelChoice | None, model: str | None) -> None:
    if choice is None:
        return
    cached = " (cached)" if choice.cached else ""
    console.print(f"model [bold]{model or choice.alias}[/bold] ← {choice.source}{cached}: {escape(choice.reason)}")
    if choice.probabilities:
        console.print(f"  jev floats: {_floats(choice.probabilities)}")


# ---------------------------------------------------------------- bazaar llm


def llm_show(last: int = typer.Option(10, help="How many logged model choices to show")) -> None:
    """Runtime LLM config (RUNTIME.md), pinned model, which keys are set (never values), and Jev's last choices."""
    settings, loaded, rules = _load()
    config = loaded.config
    t = Table(title=f"Runtime LLM · {loaded.path.name}{'' if loaded.lines else ' (missing: defaults)'}")
    for col in ("param", "value", "why", "line"):
        t.add_column(col, justify="right" if col == "line" else "left")
    for line in loaded.lines:
        t.add_row(line.rule_id, line.raw_value, line.why, str(line.line))
    console.print(t)
    try:
        pin = pinned_model(STATE["pin"], settings.llm_runtime, config.llm_runtime)
    except UnknownModelError as e:
        _fail(f"pinned model is unknown: {e}")
    if pin is not None:
        console.print(f"Model: [bold]{pin.model.alias}[/bold] ({pin.model.model_id}) pinned by {pin.source}")
    else:
        console.print(
            f"Model: Jev chooses among {', '.join(config.runtime_models)} (design bar 0.75); "
            f"default [bold]{config.runtime_model_default}[/bold]"
        )
    _print_models(settings, config.runtime_models, config.runtime_model_default)
    jev = "set" if settings.typesafe_api_key else "[red]not set[/red] (Jev undecided → default model)"
    console.print(f"TYPESAFE_API_KEY (Jev model choice): {jev}")
    try:
        model_question(load_questions(QUESTION_FILE), config.runtime_models)
        console.print(f"Question pack {QUESTION_FILE.name}: [green]criteria for every candidate[/green]")
    except (JevUsageError, RuntimeConfigError) as e:
        console.print(f"[red]Question pack: {e}[/red]")
    steering = load_steering(settings.data_dir / STEERING_FILE)
    console.print(
        f"Steering: {steering.summary} (ticks {steering.created_tick}..{steering.expires_tick})"
        if steering
        else "Steering: none"
    )
    _print_choices(read_choices(settings.data_dir / CHOICE_LOG, last))


def _print_models(settings: Settings, candidates: tuple[str, ...], default: str) -> None:
    t = Table(title="Models")
    for col in ("alias", "model id", "provider", "key", "role"):
        t.add_column(col)
    names = list(dict.fromkeys([*candidates, default, *ALIASES]))
    for name in names:
        ref = resolve(name)
        has_key = api_key_for(ref.provider, settings) is not None
        key = f"{KEY_VARIABLES[ref.provider]} {'set' if has_key else '[red]not set[/red]'}"
        role = "default" if name == default else ("candidate" if name in candidates else "alias")
        t.add_row(ref.alias, ref.model_id, ref.provider, key, role)
    console.print(t)


def _print_choices(rows: list[dict[str, Any]]) -> None:
    if not rows:
        console.print("No model choices logged yet (.local/llm/model-choices.jsonl).")
        return
    t = Table(title=f"Last {len(rows)} model choices (full Jev float map)")
    for col in ("tick", "kind", "bucket", "model", "source", "conf", "floats", "reason"):
        t.add_column(col)
    for r in rows:
        conf = r.get("confidence")
        t.add_row(
            str(r.get("tick")),
            str(r.get("kind")),
            str(r.get("bucket")),
            str(r.get("model")),
            str(r.get("source")),
            "-" if conf is None else f"{float(conf):.3f}",
            _floats(r.get("probabilities")),
            escape(str(r.get("reason"))),
        )
    console.print(t)


# ---------------------------------------------------------------- bazaar ask


def ask(text: str = typer.Argument(help='What you want, e.g. "buy LAV-09 under 90"')) -> None:
    """Talk to the agent: sentence → strict intent → guardrail verdict → exact command. Dry run: never trades."""
    settings, loaded, rules = _load()
    runtime = _runtime(settings, loaded, rules)
    clock = _public_clock(settings)
    result = parse_request(
        text, runtime, tick=clock.tick if clock else None, tick_seconds=clock.tick_seconds if clock else 60.0
    )
    _print_choice(result.choice, result.model)
    if result.error:
        _fail(escape(result.error))
    if isinstance(result.outcome, Clarification):
        console.print(f"[yellow]Need one detail first:[/yellow] {escape(result.outcome.question)}")
        return
    if not isinstance(result.outcome, Intent):
        _fail("no intent came back")
    intent = result.outcome
    console.print(
        f"intent [bold]{intent.kind}[/bold] item {intent.item or '-'} max {intent.max_price or '-'} "
        f"min {intent.min_price or '-'} counterparty {intent.counterparty or '-'} "
        f"constraints {escape(str(list(intent.constraints) or '-'))}"
    )
    _print_verdict(intent, settings, rules)
    command = command_for(intent, text)
    console.print(f"[yellow]dry run: nothing was sent.[/yellow] Command:\n  {escape(command)}")
    if intent.kind in ("buy", "sell"):
        console.print(f"  to trade (you run it): {escape(command)} --live")


def _print_verdict(intent: Intent, settings: Settings, rules: Guardrails) -> None:
    if intent.kind not in ("buy", "sell") or intent.item is None:
        return
    if settings.bazaar_key is None:
        console.print("[yellow]guardrail check skipped: BAZAAR_KEY is not set (needs live /me)[/yellow]")
        return
    from bazaar_agent import guardrails as gr
    from bazaar_agent.sdk import BazaarError, public_client, team_client

    try:
        client = team_client(settings)
        me = client.me()
        clock = Clock.model_validate(client.clock())
        rarity = rarity_of(public_client(settings).catalog(), intent.item)
    except BazaarError as e:
        console.print(f"[yellow]guardrail check skipped: /me refused {e.code}[/yellow]")
        return
    action = guardrail_action(intent, rarity, me)
    if action is None:
        return
    from bazaar_agent.ledger_pg import open_ledger

    ctx = gr.context_from(me, clock.tick, clock.t_hours, open_ledger(settings.data_dir, source="ask"), rules)
    verdict = gr.check(action, ctx, rules)
    colour = "green" if verdict.allowed else "red"
    console.print(
        f"guardrails: [{colour}]{verdict}[/{colour}] · {action.kind} {action.item} ({rarity or '?'}) at "
        f"{action.price} · cash {ctx.cash}, spent last game hour {ctx.spent_last_hour}"
    )


# ---------------------------------------------------------------- bazaar steer


def steer(
    text: str | None = typer.Argument(None, help='Style instruction, e.g. "be more aggressive with rares tonight"'),
    show: bool = typer.Option(False, "--show", help="Show the stored steering and the values it yields now"),
    clear: bool = typer.Option(False, "--clear", help="Remove the stored steering"),
) -> None:
    """Steer the style: instruction → bounded parameter deltas, clamped to GUARDRAILS.md, expiring at a tick."""
    settings, loaded, rules = _load()
    path = settings.data_dir / STEERING_FILE
    if clear:
        console.print("steering cleared" if clear_steering(path) else "no steering to clear")
        return
    clock = _public_clock(settings)
    if show or text is None:
        _show_steering(load_steering(path), rules, clock)
        return
    if clock is None:
        _fail("steering expires at a game tick, so it needs the game clock: try again when it answers")
    runtime = _runtime(settings, loaded, rules)
    result = steer_request(
        text,
        runtime,
        rules,
        base_params(rules),
        tick=clock.tick,
        tick_seconds=clock.tick_seconds,
        now_label=datetime.now().strftime("%H:%M"),
    )
    _print_choice(result.choice, result.model)
    if result.error:
        _fail(escape(result.error))
    if isinstance(result.outcome, Clarification):
        console.print(f"[yellow]Need one detail first:[/yellow] {escape(result.outcome.question)}")
        return
    if not isinstance(result.outcome, Steering):
        _fail("no steering came back")
    save_steering(path, result.outcome)
    console.print(f"[green]steering saved[/green] → {path}")
    _show_steering(result.outcome, rules, clock)


def _show_steering(steering: Steering | None, rules: Guardrails, clock: Clock | None) -> None:
    if steering is None:
        console.print("no steering: RUNTIME.md / STRATEGY.md / GUARDRAILS.md values apply as written")
        return
    tick = clock.tick if clock else steering.created_tick
    state = "active" if steering.active(tick) else "[red]expired[/red]"
    console.print(
        f"steering ({state}, ticks {steering.created_tick}..{steering.expires_tick}, by {steering.model}): "
        f"{escape(steering.summary)}\n  instruction: {escape(steering.text)}"
    )
    base = base_params(rules)
    applied = apply_steering(base, steering, tick, rules)
    t = Table(title=f"Parameters at tick {tick}")
    for col in ("param", "base", "delta asked", "applied", "clamped", "used by"):
        t.add_column(col)
    for param, delta in steering.deltas.items():
        if param not in base:
            continue
        clamped = steering.active(tick) and abs(applied[param] - (base[param] + delta)) > 1e-9
        used_by = STEERABLE[param].used_by
        t.add_row(param, f"{base[param]:g}", f"{delta:+g}", f"{applied[param]:g}", "yes" if clamped else "", used_by)
    console.print(t)


def register(app: typer.Typer) -> None:
    app.command("llm")(llm_show)
    app.command("ask")(ask)
    app.command("steer")(steer)
