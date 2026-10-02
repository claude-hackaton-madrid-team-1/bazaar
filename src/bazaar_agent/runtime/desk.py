"""The desk session: Claude Agent SDK options that wire desk → subagents → tools → hooks → guardrails.

Locked like the one-shot provider in `runtime.claude`: no settings files, no CLAUDE.md, no claude.ai
connectors, no session files, the Claude subscription token (never an API key). The only built-in tool
is `Agent`; every other tool is ours (`mcp__bazaar__*`). `dontAsk` denies anything not pre-approved,
the built-in general-purpose subagent and nested subagents are off, and every call meets the hooks
(code.claude.com/docs/en/agent-sdk/permissions, .../subagents, .../hooks).

`Desk.ask()` sends one operator turn and returns the answer, every tool call (who called what), and any
failure as an `LLMError`: no CLI, a rejected token, the plan's usage limit, a rate limit, a timeout.
`bazaar ask` then falls back to its intent parser, and `bazaar agent chat` says the desk is offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from bazaar_agent.llm.models import ModelRef, Pin, resolve
from bazaar_agent.llm.providers import LLMError
from bazaar_agent.runtime.agents import AGENT_TOOL, DESK, DESK_SPEC, agent_definitions
from bazaar_agent.runtime.claude import LOCKED_ENV, TOKEN_VARIABLE, Outcome, RedactedEnv, outcome_error
from bazaar_agent.runtime.hooks import Guard
from bazaar_agent.runtime.tools import SERVER, TOOLS, answer

# Built-ins no desk session may see even if a future CLI adds them to the default set.
BLOCKED_BUILTINS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit", "WebFetch", "WebSearch")
OFFLINE = frozenset({"auth", "usage_limit", "cli_missing", "key_missing", "unknown_model"})  # retrying will not help
API_ATTEMPT_CAP_S = 120.0


def _drop(_line: str) -> None:
    """CLI stderr goes nowhere: nothing it prints may reach a log."""


@dataclass(frozen=True)
class DeskConfig:
    model: ModelRef
    max_turns: int
    timeout_s: float


def desk_model(pin: Pin | None, default: str) -> ModelRef:
    """A pinned Claude model (flag, BAZAAR_LLM_RUNTIME, RUNTIME.md), else RUNTIME.md `desk_model`. The
    desk runs on the Claude Code CLI, so a pinned OpenAI model is ignored here."""
    if pin is not None and pin.model.provider == "anthropic":
        return pin.model
    return resolve(default)


def desk_env(token: str | None, timeout_s: float) -> RedactedEnv:
    """The locked CLI environment, plus the subagent caps (code.claude.com/docs/en/env-vars)."""
    env = RedactedEnv(LOCKED_ENV)
    env["API_TIMEOUT_MS"] = str(round(min(timeout_s, API_ATTEMPT_CAP_S) * 1000))
    env["CLAUDE_CODE_MAX_RETRIES"] = "1"
    env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "1"  # the desk's subagents spawn nothing of their own
    env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = "2"
    env["CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS"] = "1"  # no general-purpose agent with every tool
    if token:
        env[TOKEN_VARIABLE] = token
    return env


def desk_options(guard: Guard, server: Any, token: str | None, config: DeskConfig, cwd: Path) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=DESK_SPEC.prompt,
        model=config.model.model_id,
        tools=[AGENT_TOOL],
        allowed_tools=[AGENT_TOOL, *(spec.mcp_name for spec in TOOLS)],
        disallowed_tools=list(BLOCKED_BUILTINS),
        permission_mode="dontAsk",
        mcp_servers={SERVER: server},
        hooks=guard.hooks(),
        agents=agent_definitions(),
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=config.max_turns,
        cwd=cwd,
        env=desk_env(token, config.timeout_s),
        stderr=_drop,
        extra_args={"no-session-persistence": None},
    )


@dataclass(frozen=True)
class DeskEvent:
    """One line of the transcript: a tool call, its answer, or the desk's text."""

    kind: str  # "call" | "answer" | "text"
    agent: str
    name: str = ""
    detail: str = ""


@dataclass
class DeskReply:
    text: str = ""
    events: list[DeskEvent] = field(default_factory=list)
    error: LLMError | None = None
    turns: int | None = None
    cost_usd: float | None = None


def _short(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


class _Transcript:
    """Turns the SDK's message stream into `DeskEvent`s, naming the subagent behind every call."""

    def __init__(self, emit: Callable[[DeskEvent], None]) -> None:
        self.emit = emit
        self.reply = DeskReply()
        self.names: dict[str, str] = {}  # tool_use_id -> tool name
        self.subagents: dict[str, str] = {}  # the Agent call's tool_use_id -> subagent name
        self.result: ResultMessage | None = None
        self.error: str | None = None
        self.limit: RateLimitInfo | None = None

    def _event(self, event: DeskEvent) -> None:
        self.reply.events.append(event)
        self.emit(event)

    def take(self, message: Any) -> None:
        if isinstance(message, AssistantMessage):
            if message.error:
                self.error = str(message.error)
            agent = self.subagents.get(message.parent_tool_use_id or "", DESK)
            for block in message.content:
                self._block(agent, block)
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            agent = self.subagents.get(message.parent_tool_use_id or "", DESK)
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    self._result(agent, block)
        elif isinstance(message, RateLimitEvent) and message.rate_limit_info.status == "rejected":
            self.limit = message.rate_limit_info
        elif isinstance(message, ResultMessage):
            self.result = message
            self.reply.text = (message.result or "").strip()
            self.reply.turns, self.reply.cost_usd = message.num_turns, message.total_cost_usd

    def _block(self, agent: str, block: Any) -> None:
        if isinstance(block, ToolUseBlock):
            self.names[block.id] = block.name
            if block.name in (AGENT_TOOL, "Task"):
                self.subagents[block.id] = str(block.input.get("subagent_type") or "?")
                detail = _short(block.input.get("prompt") or "")
                self._event(DeskEvent("call", agent, f"→ {self.subagents[block.id]}", detail))
            else:
                self._event(DeskEvent("call", agent, block.name.removeprefix(f"mcp__{SERVER}__"), _short(block.input)))
        elif isinstance(block, TextBlock) and agent == DESK and block.text.strip():
            self._event(DeskEvent("text", agent, "", block.text.strip()))

    def _result(self, agent: str, block: ToolResultBlock) -> None:
        name = self.names.get(block.tool_use_id, "?")
        if name in (AGENT_TOOL, "Task"):
            return  # the subagent's report: the desk quotes what matters in its answer
        payload = answer({"content": block.content} if isinstance(block.content, list) else str(block.content or ""))
        if payload is not None:
            detail = " · ".join(
                str(payload[k]) for k in ("status", "guardrail", "reason", "command") if payload.get(k) is not None
            )
        else:
            detail = ("error: " if block.is_error else "") + _short(str(block.content or ""))
        self._event(DeskEvent("answer", agent, name.removeprefix(f"mcp__{SERVER}__"), detail or "ok"))


ClientFactory = Callable[[ClaudeAgentOptions], Any]


class Desk:
    """One conversation with the desk (`ClaudeSDKClient` keeps it across turns)."""

    def __init__(
        self,
        options: ClaudeAgentOptions,
        *,
        timeout_s: float,
        client_factory: ClientFactory = ClaudeSDKClient,
        emit: Callable[[DeskEvent], None] = lambda event: None,
    ) -> None:
        self.options, self.timeout_s, self.emit = options, timeout_s, emit
        self._factory = client_factory
        self._client: Any = None

    async def _connected(self) -> Any:
        if self._client is None:
            client = self._factory(self.options)
            await client.connect()
            self._client = client
        return self._client

    async def ask(self, text: str) -> DeskReply:
        transcript = _Transcript(self.emit)
        failure: ClaudeSDKError | None = None
        try:
            await asyncio.wait_for(self._turn(text, transcript), self.timeout_s)
        except TimeoutError:
            await self.close()
            transcript.reply.error = LLMError("timeout", f"the desk did not answer within {self.timeout_s:.0f}s")
            return transcript.reply
        except ClaudeSDKError as e:
            failure = e
            await self.close()
        outcome = Outcome(transcript.result, transcript.error, transcript.limit, failure)
        transcript.reply.error = outcome_error(outcome)
        return transcript.reply

    async def _turn(self, text: str, transcript: _Transcript) -> None:
        client = await self._connected()
        await client.query(text)
        async for message in client.receive_response():
            transcript.take(message)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):  # the CLI may already be gone: nothing left to close
                await client.disconnect()


def scratch_dir() -> Path:
    """An empty working directory: the desk has no file tools, and nothing there is ours to read."""
    return Path(tempfile.mkdtemp(prefix="bazaar-desk-"))
