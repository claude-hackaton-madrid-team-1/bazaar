"""The desk session: Claude Agent SDK options that wire desk → subagents → tools → hooks → guardrails.

Locked like the one-shot provider in `runtime.claude`: no settings files, no CLAUDE.md, no claude.ai
connectors, no session files, the Claude subscription token (never an API key). The only built-in tool
is `Agent`; every other tool is ours (`mcp__bazaar__*`). `dontAsk` denies anything not pre-approved,
the built-in general-purpose subagent and nested subagents are off, and every call meets the hooks
(code.claude.com/docs/en/agent-sdk/permissions, .../subagents, .../hooks).

`Desk.ask()` sends one operator turn and returns the answer, every tool call (who called what), and any
failure as an `LLMError`: no CLI, a rejected token, the plan's usage limit, a rate limit, a timeout.
`bazaar ask` then falls back to its intent parser, and `bazaar agent chat` says the desk is offline.

Before each turn the desk asks its planner (`runtime.desk_models`: Jev, cached, or the pin) which model
runs the orchestrator and each subagent, in the same session (the conversation is kept): the orchestrator
switches in place (`ClaudeSDKClient.set_model`), and each subagent's family alias goes on its `Agent`
calls through the hook (`Guard.use_aliases`), resolved to our exact id by the session's
ANTHROPIC_DEFAULT_<FAMILY>_MODEL pins. The `AgentDefinition`s carry the models the session started with.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import tempfile
from collections.abc import Callable, Mapping
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

from bazaar_agent.llm.providers import LLMError
from bazaar_agent.runtime.agents import AGENT_TOOL, DESK, DESK_SPEC, agent_definitions
from bazaar_agent.runtime.claude import LOCKED_ENV, TOKEN_VARIABLE, Outcome, RedactedEnv, outcome_error
from bazaar_agent.runtime.desk_models import DeskModels
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
    max_turns: int
    timeout_s: float


def desk_env(token: str | None, timeout_s: float, families: Mapping[str, str] | None = None) -> RedactedEnv:
    """The locked CLI environment, plus the subagent caps (code.claude.com/docs/en/env-vars) and the family
    alias pins (`desk_models.family_env`). A stray CLAUDE_CODE_SUBAGENT_MODEL(_FORCE) in the shell is blanked:
    it would put every subagent on one model."""
    env = RedactedEnv(LOCKED_ENV)
    env.update(families or {})
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = ""
    env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] = ""
    env["API_TIMEOUT_MS"] = str(round(min(timeout_s, API_ATTEMPT_CAP_S) * 1000))
    env["CLAUDE_CODE_MAX_RETRIES"] = "1"
    env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "1"  # the desk's subagents spawn nothing of their own
    env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = "2"
    env["CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS"] = "1"  # no general-purpose agent with every tool
    if token:
        env[TOKEN_VARIABLE] = token
    return env


def desk_options(
    guard: Guard,
    server: Any,
    token: str | None,
    config: DeskConfig,
    cwd: Path,
    models: DeskModels,
    families: Mapping[str, str] | None = None,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=DESK_SPEC.prompt,
        model=models.orchestrator.model_id,
        tools=[AGENT_TOOL],
        allowed_tools=[AGENT_TOOL, *(spec.mcp_name for spec in TOOLS)],
        disallowed_tools=list(BLOCKED_BUILTINS),
        permission_mode="dontAsk",
        mcp_servers={SERVER: server},
        hooks=guard.hooks(),
        agents=agent_definitions(models.subagent_ids()),
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=config.max_turns,
        cwd=cwd,
        env=desk_env(token, config.timeout_s, families),
        stderr=_drop,
        extra_args={"no-session-persistence": None},
    )


def with_models(options: ClaudeAgentOptions, models: DeskModels) -> ClaudeAgentOptions:
    """The same locked session with another orchestrator model and other subagent models."""
    return dataclasses.replace(
        options, model=models.orchestrator.model_id, agents=agent_definitions(models.subagent_ids())
    )


@dataclass(frozen=True)
class DeskEvent:
    """One line of the transcript: the models chosen, a tool call, its answer, or the desk's text."""

    kind: str  # "models" | "call" | "answer" | "text"
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
    ran_on: dict[str, str] = field(default_factory=dict)  # agent -> the model id its replies came from


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

    def event(self, event: DeskEvent) -> None:
        self.reply.events.append(event)
        self.emit(event)

    def take(self, message: Any) -> None:
        if isinstance(message, AssistantMessage):
            if message.error:
                self.error = str(message.error)
            agent = self.subagents.get(message.parent_tool_use_id or "", DESK)
            if message.model:
                self.reply.ran_on.setdefault(agent, message.model)
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
                self.event(DeskEvent("call", agent, f"→ {self.subagents[block.id]}", detail))
            else:
                self.event(DeskEvent("call", agent, block.name.removeprefix(f"mcp__{SERVER}__"), _short(block.input)))
        elif isinstance(block, TextBlock) and agent == DESK and block.text.strip():
            self.event(DeskEvent("text", agent, "", block.text.strip()))

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
        self.event(DeskEvent("answer", agent, name.removeprefix(f"mcp__{SERVER}__"), detail or "ok"))


ClientFactory = Callable[[ClaudeAgentOptions], Any]
Planner = Callable[[str], DeskModels]


class Desk:
    """One conversation with the desk (`ClaudeSDKClient` keeps it across turns). With a `plan`, every
    turn first picks the models (`runtime.desk_models.DeskModelPicker.pick`, which never raises)."""

    def __init__(
        self,
        options: ClaudeAgentOptions,
        *,
        timeout_s: float,
        client_factory: ClientFactory = ClaudeSDKClient,
        emit: Callable[[DeskEvent], None] = lambda event: None,
        plan: Planner | None = None,
        models: DeskModels | None = None,
        families: Mapping[str, str] | None = None,
        on_aliases: Callable[[Mapping[str, str]], None] = lambda aliases: None,
    ) -> None:
        self.options, self.timeout_s, self.emit = options, timeout_s, emit
        self._factory = client_factory
        self._client: Any = None
        self._plan = plan
        self.models = models
        self.families = dict(families or {})  # the ANTHROPIC_DEFAULT_<FAMILY>_MODEL pins in the session's env
        self._on_aliases = on_aliases
        self._session: dict[str, str] = {}  # subagent -> the definition's model in the running session

    async def _connected(self) -> Any:
        if self._client is None:
            client = self._factory(self.options)
            await client.connect()
            self._client = client
            self._session = self.models.subagent_ids() if self.models is not None else {}
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
        if self._plan is not None:
            await self._use(await asyncio.to_thread(self._plan, text), transcript)
        client = await self._connected()
        await client.query(text)
        async for message in client.receive_response():
            transcript.take(message)

    async def _use(self, models: DeskModels, transcript: _Transcript) -> None:
        """Run this turn on `models` in the same session: the hook carries each subagent's alias, and the
        orchestrator switches in place. A switch that fails, or a subagent model no alias names, keeps the
        session's model and says so (a new session would forget the conversation)."""
        previous, self.models = self.models, models
        self.options = with_models(self.options, models)  # the definitions of the next NEW session
        aliases = models.invocation_aliases(self.families)
        self._on_aliases(aliases)
        transcript.event(DeskEvent("models", DESK, "", models.summary()))
        if self._client is None or previous is None:
            return
        kept = [r for r, mid in models.subagent_ids().items() if r not in aliases and mid != self._session.get(r)]
        if kept:
            note = f"{', '.join(kept)} keep this conversation's model (no family alias names the new one)"
            transcript.event(DeskEvent("models", DESK, "", note))
        if models.orchestrator == previous.orchestrator:
            return
        try:
            await self._client.set_model(models.orchestrator.model_id)
        except Exception as e:  # the session goes on with the model it had
            self.models = DeskModels({**models.choices, DESK: previous.choices[DESK]})
            note = f"desk stays on {previous.orchestrator.alias} (switch failed: {type(e).__name__})"
            transcript.event(DeskEvent("models", DESK, "", note))

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):  # the CLI may already be gone: nothing left to close
                await client.disconnect()


def scratch_dir() -> Path:
    """An empty working directory: the desk has no file tools, and nothing there is ours to read."""
    return Path(tempfile.mkdtemp(prefix="bazaar-desk-"))
