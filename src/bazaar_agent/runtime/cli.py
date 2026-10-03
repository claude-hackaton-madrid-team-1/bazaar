"""CLI for the agent runtime: `bazaar agent chat`, `bazaar agent tools`, `bazaar mcp serve`, and the
desk route `bazaar ask` takes when the Claude subscription is configured.

Thin wrappers: the logic lives in `runtime.*`. Every line printed from a model or a tool is scrubbed
(`tools.safe_text`) and escaped for rich: no token, key or URL, and no counterparty text as markup.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.config import Settings, env_file_path, load_settings, read_env_file
from bazaar_agent.guardrails import Guardrails, GuardrailsError, load_guardrails
from bazaar_agent.jev import JudgeResult
from bazaar_agent.llm.config import RuntimeConfig, RuntimeConfigError, load_runtime
from bazaar_agent.llm.models import UnknownModelError
from bazaar_agent.runtime.agents import who_may_call
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.runtime.tools import TOOLS, safe_text, secrets_of

console = Console()
EXIT_WORDS = frozenset({"exit", "quit", "salir", ":q"})
DEFAULT_MCP_PORT = 8765
GUARDS = {
    "dealer_buy": "buy at max_price: rarity cap, cash floor, spend per game hour, packs per hour, held card",
    "sell_list": "sell: never below your_value, never an asset already listed; listings per tick",
    "sell_bid": "bid: rarity cap, cash floor counting open offers, spend per hour; listings per tick",
    "sell_cancel": "kill switch; only our own open offers",
    "duel_move": "duel_offer / duel_accept: kill switch, one accept per tick (shared ledger)",
    "steer": "steer_max_change, steer_max_ttl_ticks (clamped, expires at a tick)",
}
# Tests replace these: the desk's SDK client, Jev's judge and the backend (no network, no CLI process).
CLIENT_FACTORY: Callable[..., Any] | None = None
JUDGE: Callable[..., JudgeResult] | None = None


def _fail(message: str) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(1)


def _load() -> tuple[Settings, Guardrails, RuntimeConfig]:
    try:
        rules = load_guardrails().rules
        config = load_runtime().config
    except (GuardrailsError, RuntimeConfigError) as e:
        _fail(f"refusing to start the runtime: {e}")
    return load_settings(), rules, config


def make_backend(
    settings: Settings, rules: Guardrails, log: Callable[[str], None], *, live: bool | None = None, server: bool = False
) -> Backend:
    """`live` None: BAZAAR_LIVE=1 in the environment decides. `server`: the remote MCP server."""
    return Backend(settings, rules, live=live, server=server, log=log)


def _say(secrets: tuple[str, ...]) -> Callable[[str], None]:
    def say(line: str) -> None:
        console.print(escape(safe_text(line, secrets)), soft_wrap=True, highlight=False)

    return say


def _show_event(say: Callable[[str], None]) -> Callable[[Any], None]:
    def show(event: Any) -> None:
        if event.kind == "models":
            say(f"  models: {event.detail}")
        elif event.kind == "call":
            say(f"  {event.agent} {event.name} {event.detail}")
        elif event.kind == "answer":
            say(f"  {event.agent} ← {event.name}: {event.detail}")

    return show


def build_desk(
    settings: Settings,
    rules: Guardrails,
    config: RuntimeConfig,
    cli_pin: str | None,
    model: str | None = None,
    *,
    live: bool | None = None,
) -> tuple[Any, Backend, Any, tuple[str, ...]]:
    """(desk, backend, its model picker, secrets). Raises `UnknownModelError` for a bad model name.

    The picker chooses the orchestrator's and each subagent's model before every request (Jev, cached
    per role on the game tick, or the pin); the session starts on the pin or the role defaults."""
    from bazaar_agent.runtime.agents import allow_lists
    from bazaar_agent.runtime.desk import Desk, DeskConfig, desk_options, scratch_dir
    from bazaar_agent.runtime.desk_models import build_picker, family_env
    from bazaar_agent.runtime.hooks import Guard
    from bazaar_agent.runtime.tools import sdk_server

    secrets = secrets_of(settings)
    say = _say(secrets)
    backend = make_backend(settings, rules, say, live=live)
    guard = Guard(
        backend,
        allow_lists(),
        secrets,
        log=lambda line: console.print(f"[red]{escape(safe_text(line, secrets))}[/red]"),
    )
    try:
        picker = build_picker(
            settings, config, rules, cli_pin=cli_pin, override=model, clock=backend.clock, log=say, judge_fn=JUDGE
        )
    except UnknownModelError:
        raise
    except Exception as e:  # the choice log or the question pack is unreadable: never stop the desk for it
        say(f"desk models: Jev off ({type(e).__name__}), role defaults")
        picker = build_picker(settings, config, rules, cli_pin=cli_pin, override=model, log=say, log_path=None)
    desk_config = DeskConfig(config.desk_max_turns, config.desk_timeout_s)
    token = settings.claude_code_oauth_token.get_secret_value() if settings.claude_code_oauth_token else None
    models, families = picker.initial(), family_env(picker.model_ids())
    options = desk_options(guard, sdk_server(backend, secrets), token, desk_config, scratch_dir(), models, families)
    factory = {"client_factory": CLIENT_FACTORY} if CLIENT_FACTORY is not None else {}
    desk = Desk(
        options,
        timeout_s=desk_config.timeout_s,
        emit=_show_event(say),
        plan=picker.pick,
        models=models,
        families=families,
        on_aliases=guard.use_aliases,
        **factory,
    )
    return desk, backend, picker, secrets


def _auth_line(settings: Settings) -> str:
    if settings.claude_code_oauth_token:
        return "Claude subscription (CLAUDE_CODE_OAUTH_TOKEN)"
    return "this machine's Claude Code login (CLAUDE_CODE_OAUTH_TOKEN not set)"


def _report(reply: Any, say: Callable[[str], None]) -> None:
    if reply.error is not None:
        console.print(f"[yellow]desk unavailable ({escape(reply.error.reason)}): {escape(str(reply.error))}[/yellow]")
        return
    console.print("[bold]desk:[/bold]")
    say(reply.text or "(no answer)")
    if reply.turns is not None:
        console.print(f"[dim]{reply.turns} turns[/dim]")
    if reply.ran_on:
        say("ran on: " + " · ".join(f"{agent} {model}" for agent, model in reply.ran_on.items()))


OFFLINE_HELP = (
    "The deterministic commands still work: `uv run bazaar status`, `bazaar strategy`, `bazaar ask --no-desk`, "
    "`bazaar dealer buy|sell list|sell bid` (dry run unless --live)."
)


async def _chat_loop(desk: Any, first: str | None, say: Callable[[str], None]) -> int:
    from bazaar_agent.runtime.desk import OFFLINE

    try:
        while True:
            text = first if first is not None else await asyncio.to_thread(_read_line)
            if text is None or text.strip().lower() in EXIT_WORDS:
                return 0
            if not text.strip():
                continue
            reply = await desk.ask(text)
            _report(reply, say)
            if reply.error is not None and reply.error.reason in OFFLINE:
                console.print(f"[yellow]{OFFLINE_HELP}[/yellow]")
                return 1
            if first is not None:
                return 1 if reply.error is not None else 0
    finally:
        await desk.close()


def _read_line() -> str | None:
    try:
        return console.input("[bold]you›[/bold] ")
    except (EOFError, KeyboardInterrupt):
        return None


def chat(
    once: str | None = typer.Option(None, "--once", help="Send one request, print the transcript, exit"),
    model: str | None = typer.Option(
        None, help="Pin the desk and every subagent to one Claude model (alias or claude-* id); default: Jev picks"
    ),
) -> None:
    """Talk to the desk: it routes to the strategist, buyer, seller or duelist. Dry run unless BAZAAR_LIVE=1."""
    from bazaar_agent.llm import cli as llm_cli

    settings, rules, config = _load()
    try:
        desk, backend, picker, secrets = build_desk(settings, rules, config, llm_cli.STATE["pin"], model)
    except UnknownModelError as e:
        _fail(f"cannot pick the desk model: {e}")
    mode = "[red]LIVE: write tools send[/red]" if backend.live else "[yellow]DRY RUN: nothing is sent[/yellow]"
    console.print(
        f"[bold]desk[/bold] · models: {escape(picker.describe())} · {mode} · Claude: {_auth_line(settings)} · "
        f"subagents strategist, buyer, seller, duelist · type 'exit' to leave"
    )
    code = asyncio.run(_chat_loop(desk, once, _say(secrets)))
    if code:
        raise typer.Exit(code)


def ask_desk(text: str, settings: Settings, rules: Guardrails, config: RuntimeConfig, cli_pin: str | None) -> bool:
    """`bazaar ask` through the desk, ALWAYS a dry run (`ask` never trades, BAZAAR_LIVE or not). False
    (after saying why) when the desk is unavailable: the caller falls back to the intent parser."""
    try:
        desk, backend, picker, secrets = build_desk(settings, rules, config, cli_pin, live=False)
    except UnknownModelError as e:
        console.print(f"[yellow]desk off ({escape(str(e))})[/yellow]")
        return False
    console.print(f"[dim]desk · models: {escape(picker.describe())} · dry run · Claude: {_auth_line(settings)}[/dim]")

    async def once() -> Any:
        try:
            return await desk.ask(text)
        finally:
            await desk.close()

    reply = asyncio.run(once())
    _report(reply, _say(secrets))
    return reply.error is None


def tools_table() -> None:
    """Every runtime tool, read or write, which agents may call it, and the guardrails it meets."""
    t = Table(title=f"Agent runtime tools · {len(TOOLS)} (in-process `mcp__bazaar__*`, remote `bazaar mcp serve`)")
    for col in ("tool", "kind", "agents", "guardrails", "what it does"):
        t.add_column(col)
    callers = who_may_call()
    for spec in TOOLS:
        kind = "[red]write[/red]" if spec.write else "read"
        t.add_row(spec.name, kind, ", ".join(callers[spec.name]) or "-", GUARDS.get(spec.name, "-"), spec.description)
    console.print(t)
    console.print("Writes are DRY RUN unless BAZAAR_LIVE=1; the tool checks the guardrails, and the desk's hook again.")


# ---------------------------------------------------------------- bazaar mcp serve


def _mcp_token() -> str | None:
    """BAZAAR_MCP_TOKEN from the environment (Railway), else `.env`. Never printed."""
    from bazaar_agent.runtime.mcp_server import TOKEN_VARIABLE

    return os.environ.get(TOKEN_VARIABLE) or read_env_file(env_file_path()).get(TOKEN_VARIABLE)


def mcp_serve(
    host: str = typer.Option("127.0.0.1", help="Interface (Railway: 0.0.0.0)"),
    port: int | None = typer.Option(None, help=f"Port (default $PORT, else {DEFAULT_MCP_PORT})"),
) -> None:
    """Serve the runtime tools over MCP Streamable HTTP at /mcp (bearer token, rate limits, dry run by default)."""
    from bazaar_agent.runtime.mcp_server import MCP_PATH, TokenError, build_app, require_token, serve

    settings, rules, config = _load()
    try:
        token = require_token(_mcp_token())
    except TokenError as e:
        _fail(str(e))
    secrets = secrets_of(settings, [token])
    backend = make_backend(settings, rules, _say(secrets), server=True)
    app = build_app(backend, token, config.mcp_calls_per_minute, secrets, host)
    bound = port if port is not None else int(os.environ.get("PORT") or DEFAULT_MCP_PORT)
    mode = "LIVE: write tools send" if backend.live else "DRY RUN: write tools send nothing"
    console.print(
        f"bazaar-mcp: {len(TOOLS)} tools on {host}:{bound}{MCP_PATH} · {mode} · bearer token required · "
        f"{config.mcp_calls_per_minute} tool calls/min per token · GET /health is public"
    )
    serve(app, host, bound)


def register(agent_app: typer.Typer, app: typer.Typer) -> None:
    agent_app.command("chat")(chat)
    agent_app.command("tools")(tools_table)
    mcp_app = typer.Typer(no_args_is_help=True, help="The runtime tools as a remote MCP server (Streamable HTTP)")
    mcp_app.command("serve")(mcp_serve)
    app.add_typer(mcp_app, name="mcp")
