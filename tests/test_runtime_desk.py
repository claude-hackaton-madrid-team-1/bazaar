"""The desk end to end with a fake SDK client: it replays a session through the REAL hooks and tools.

No CLI process, no network: the fake plays the model's moves (start a subagent, call a tool), runs the
options' PreToolUse / PostToolUse callbacks around each call like Claude Code does, and calls the tool
code for the answer.
"""

import asyncio

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLINotFoundError,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from typer.testing import CliRunner

from bazaar_agent.agents.runtime import live_mode
from bazaar_agent.cli import app
from bazaar_agent.llm import cli as llm_cli
from bazaar_agent.runtime import cli as rt_cli
from bazaar_agent.runtime import desk as dk
from bazaar_agent.runtime import tools as tl
from tests.agent_fakes import rows
from tests.runtime_fakes import TEAM_KEY, TOKEN, Team, backend
from tests.test_llm import FakeProvider
from tests.test_llm_cli import fake_runtime

runner = CliRunner()
MODEL = "claude-sonnet-5-5"


def result(text="ok", **kw):
    base = {
        "subtype": "success",
        "duration_ms": 900,
        "duration_api_ms": 800,
        "is_error": False,
        "num_turns": 6,
        "session_id": "s",
        "stop_reason": "end_turn",
        "total_cost_usd": 0.0,
        "result": text,
    }
    return ResultMessage(**{**base, **kw})


class ScriptedClient:
    """A `ClaudeSDKClient` stand-in. `script` is a list of moves:
    ("agent", subagent, prompt) · ("tool", subagent, tool name, args) · ("say", text) · ("message", sdk message)."""

    def __init__(self, options, script, backend_):
        self.options, self.script, self.b = options, script, backend_
        self.connected = False
        self.queries: list[str] = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, text):
        self.queries.append(text)

    async def _hook(self, event, data, use_id):
        out = {}
        for matcher in self.options.hooks[event]:
            for callback in matcher.hooks:
                out = await callback(data, use_id, None) or out
        return out

    async def receive_response(self):
        ids = iter(range(1, 100))
        parent = None
        for move in self.script:
            if move[0] == "agent":
                use_id = f"a{next(ids)}"
                args = {"subagent_type": move[1], "prompt": move[2]}
                decision = await self._hook("PreToolUse", {"tool_name": "Agent", "tool_input": args}, use_id)
                assert decision["hookSpecificOutput"]["updatedInput"]["run_in_background"] is False
                yield AssistantMessage([ToolUseBlock(use_id, "Agent", args)], MODEL)
                parent = use_id
            elif move[0] == "tool":
                async for message in self._tool(move[1], move[2], move[3], f"t{next(ids)}", parent):
                    yield message
            elif move[0] == "say":
                yield AssistantMessage([TextBlock(move[1])], MODEL)
                yield result(move[1])
            else:
                yield move[1]

    async def _tool(self, agent, name, args, use_id, parent):
        spec = tl.BY_NAME[name]
        data = {"tool_name": spec.mcp_name, "tool_input": args, "agent_type": agent}
        yield AssistantMessage([ToolUseBlock(use_id, spec.mcp_name, args)], MODEL, parent_tool_use_id=parent)
        decision = (await self._hook("PreToolUse", data, use_id)).get("hookSpecificOutput") or {}
        if decision.get("permissionDecision") == "deny":
            block = ToolResultBlock(use_id, decision["permissionDecisionReason"], is_error=True)
        else:
            text, failed = tl.call(spec, self.b, args, tl.secrets_of(self.b.settings))
            content = [{"type": "text", "text": text}]
            await self._hook("PostToolUse", {**data, "tool_response": content}, use_id)
            block = ToolResultBlock(use_id, content, is_error=failed)
        yield UserMessage([block], parent_tool_use_id=parent)


BUY_SCRIPT = [
    ("agent", "buyer", "buy LAV-09 under 90"),
    ("tool", "buyer", "status", {}),
    ("tool", "buyer", "sell_bid", {"ref": "LAV-09", "price": 500}),  # over the rare cap: the hook denies
    ("tool", "buyer", "sell_bid", {"ref": "LAV-09", "price": 60}),
    ("tool", "buyer", "sell_list", {"target": "LAT-03", "price": 5}),  # not the buyer's tool
    ("say", f"Dry run: would bid 60 P for any LAV-09 on rastro (allowed). Token {TOKEN} stays secret."),
]


@pytest.fixture
def desk_env(tmp_path, monkeypatch):
    for name in ("BAZAAR_KEY", "OPENAI_API_KEY", "BAZAAR_LLM_RUNTIME", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("bazaar_agent.config.read_env_file", lambda path: {})
    monkeypatch.setattr(rt_cli.console, "width", 240)
    monkeypatch.setattr(llm_cli.console, "width", 240)
    team, built = Team(), []

    def make(settings, rules, log, *, live=None, server=False):
        built.append(backend(tmp_path, team=team, live=live_mode(False) if live is None else live))
        return built[-1]

    monkeypatch.setattr(rt_cli, "make_backend", make)
    return built


def use_script(monkeypatch, built, script):
    clients = []

    def factory(options):
        clients.append(ScriptedClient(options, script, built[-1]))
        return clients[-1]

    monkeypatch.setattr(rt_cli, "CLIENT_FACTORY", factory)
    return clients


def test_chat_routes_desk_to_buyer_through_hooks_and_tools_as_a_dry_run(desk_env, monkeypatch, tmp_path):
    clients = use_script(monkeypatch, desk_env, BUY_SCRIPT)
    out = runner.invoke(app, ["agent", "chat", "--once", "buy LAV-09 under 90"])
    assert out.exit_code == 0, out.output
    text = out.output
    assert "DRY RUN: nothing is sent" in text and "Claude subscription (CLAUDE_CODE_OAUTH_TOKEN)" in text
    assert "desk → buyer buy LAV-09 under 90" in text
    assert "DENIED buyer → sell_bid: denied: price 500 > max_price_rare 80" in text
    assert "buyer ← sell_bid: approved · allowed" in text and "uv run bazaar sell bid LAV-09 --price 60" in text
    assert "DENIED buyer → sell_list: not on its allow-list" in text
    assert TOKEN not in text and TEAM_KEY not in text and "[redacted] stays secret" in text
    assert clients[0].queries == ["buy LAV-09 under 90"] and not clients[0].connected
    assert desk_env[-1].team.sent == []  # nothing reached the game
    decided = [(r["kind"], r["status"], r["dry_run"]) for r in rows(tmp_path)]
    assert decided == [("sell_bid", "rejected", True), ("sell_bid", "approved", True)]


def test_ask_never_trades_even_with_bazaar_live_set(desk_env, monkeypatch):
    monkeypatch.setenv("BAZAAR_LIVE", "1")
    use_script(monkeypatch, desk_env, BUY_SCRIPT)
    out = runner.invoke(app, ["ask", "buy LAV-09 under 90"])
    assert out.exit_code == 0, out.output
    assert desk_env[-1].live is False and desk_env[-1].team.sent == [] and "dry run" in out.output
    assert "buyer ← sell_bid: approved" in out.output


def test_desk_failures_become_llm_errors_the_callers_fall_back_on(tmp_path):
    b = backend(tmp_path)

    def ask(script, timeout_s=5.0, factory=None):
        made = factory or (lambda options: ScriptedClient(options, script, b))
        options = dk.ClaudeAgentOptions()
        return asyncio.run(dk.Desk(options, timeout_s=timeout_s, client_factory=made).ask("hola")).error

    limit = RateLimitEvent(RateLimitInfo(status="rejected", resets_at=None, rate_limit_type="five_hour"), "u", "s")
    assert ask([("message", limit), ("message", result(is_error=True))]).reason == "usage_limit"
    auth = AssistantMessage([], MODEL, error="authentication_failed")
    assert ask([("message", auth), ("message", result(is_error=True, subtype="error"))]).reason == "auth"
    assert ask([("say", "hola")]) is None

    class Missing(ScriptedClient):
        async def connect(self):
            raise CLINotFoundError("no claude")

    assert ask([], factory=lambda options: Missing(options, [], b)).reason == "cli_missing"

    class Slow(ScriptedClient):
        async def query(self, text):
            await asyncio.sleep(1.0)

    assert ask([], timeout_s=0.05, factory=lambda options: Slow(options, [], b)).reason == "timeout"


def test_chat_says_the_desk_is_offline_on_a_usage_limit(desk_env, monkeypatch):
    limit = RateLimitEvent(RateLimitInfo(status="rejected", resets_at=None, rate_limit_type="seven_day"), "u", "s")
    use_script(monkeypatch, desk_env, [("message", limit), ("message", result(is_error=True))])
    out = runner.invoke(app, ["agent", "chat", "--once", "status?"])
    assert out.exit_code == 1 and "desk unavailable (usage_limit)" in out.output
    assert "seven-day limit" in out.output and "bazaar ask --no-desk" in out.output


def test_ask_uses_the_desk_on_the_subscription_and_falls_back_to_the_intent_parser(desk_env, monkeypatch):
    use_script(monkeypatch, desk_env, BUY_SCRIPT)
    out = runner.invoke(app, ["ask", "buy LAV-09 under 90"])
    assert out.exit_code == 0, out.output
    assert "desk → buyer" in out.output and "falling back" not in out.output
    draft = {
        "kind": "buy",
        "item": "LAV-09",
        "max_price": 90,
        "min_price": None,
        "counterparty": None,
        "constraints": [],
        "question": None,
    }
    monkeypatch.setattr(llm_cli, "_public_clock", lambda settings: None)
    fake_runtime(monkeypatch, FakeProvider(draft=draft))
    limit = RateLimitEvent(RateLimitInfo(status="rejected", resets_at=None, rate_limit_type="five_hour"), "u", "s")
    use_script(monkeypatch, desk_env, [("message", limit), ("message", result(is_error=True))])
    fallback = runner.invoke(app, ["ask", "buy LAV-09 under 90"])
    assert fallback.exit_code == 0, fallback.output
    assert (
        "desk unavailable (usage_limit)" in fallback.output and "falling back to the intent parser" in fallback.output
    )
    assert "uv run bazaar dealer buy LAV-09 --max 90 --start 45" in fallback.output
    skipped = runner.invoke(app, ["ask", "--no-desk", "buy LAV-09 under 90"])
    assert "desk ·" not in skipped.output and "uv run bazaar dealer buy LAV-09" in skipped.output
