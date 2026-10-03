"""Claude on the operator's Claude subscription, through the Claude Agent SDK for Python.

`claude setup-token` prints a one-year OAuth token for a Pro, Max, Team or Enterprise plan. With it in
CLAUDE_CODE_OAUTH_TOKEN, the SDK's Claude Code CLI signs in without an API key
(code.claude.com/docs/en/authentication#generate-a-long-lived-token). The `claude-agent-sdk` wheel
bundles that CLI for macOS and manylinux, so Railway's Railpack build needs no separate install
(code.claude.com/docs/en/agent-sdk/quickstart, .../agent-sdk/hosting).

This module is the one place that turns our settings into `ClaudeAgentOptions`: the model id from the
alias registry, a hard deadline inside the tick budget, the CLI's retries and output cap, and a locked
CLI (no tools, no settings files, no CLAUDE.md or auto memory, no MCP servers, no session files). Every
SDK failure becomes an `LLMError`, so the tick loop falls back to its template or default model. A
usage-limit rejection (the plan's 5-hour or weekly window) or a rejected token stops further calls
until the window resets, so a tick never waits for a CLI start that is bound to fail.

Today it serves one-shot calls (`SubscriptionProvider`, which `llm/providers.py` routes to). The agent
runtime grows here: an in-process SDK MCP server with our bazaar tools (`mcp_servers`), a PreToolUse
hook that runs `guardrails.check()` (`hooks`), and subagents for strategist, taker, maker and duelist
(`agents`), each added through `agent_options()`.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
)
from claude_agent_sdk import query as sdk_query
from pydantic import BaseModel, ValidationError

from bazaar_agent.llm.providers import NO_EFFORT_PREFIXES, LLMError, TextRequest, attempt_timeout_s, run_with_deadline

T = TypeVar("T", bound=BaseModel)

NAME = "claude-subscription"
TOKEN_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"
TEXT_TURNS = 1
# The CLI returns structured output through one tool turn, then ends: 2 turns, plus 1 for a re-prompt.
STRUCTURED_TURNS = 3
AUTH_PAUSE_S = 600.0  # a rejected token does not fix itself: stop spawning the CLI for 10 minutes
LIMIT_PAUSE_S = 300.0  # a usage limit without a reset time
# What the CLI process sees on top of our environment (code.claude.com/docs/en/env-vars).
LOCKED_ENV: Mapping[str, str] = {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",  # a stray base URL cannot send the token elsewhere
    "ANTHROPIC_API_KEY": "",  # the subscription route runs only without an API key
    "ANTHROPIC_AUTH_TOKEN": "",  # outranks CLAUDE_CODE_OAUTH_TOKEN in the CLI: never let a stray one win
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",  # no repo or home CLAUDE.md in our prompts
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # no auto-update, telemetry or release notes in a tick
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    "CLAUDE_AGENT_SDK_CLIENT_APP": "bazaar-agent",
    # The CLI reads hostile text (the feed reader) and needs none of our other secrets: blank them.
    "BAZAAR_KEY": "",
    "BAZAAR_SIM_KEY": "",
    "DATABASE_URL": "",
    "BAZAAR_SIM_DATABASE_URL": "",
    "TYPESAFE_API_KEY": "",
    "BAZAAR_MCP_TOKEN": "",
    "OPENAI_API_KEY": "",
}
AUTH_ERRORS = frozenset({"authentication_failed", "oauth_org_not_allowed", "account_on_hold"})

QueryFn = Callable[..., AsyncIterator[Any]]


class RedactedEnv(dict[str, str]):
    """The CLI's extra environment. `repr()` names the variables and hides every value, so logging the
    options, or an error that carries them, never prints the token."""

    def __repr__(self) -> str:
        return "{" + ", ".join(f"{name!r}: '***'" for name in self) + "}"

    __str__ = __repr__


def cli_env(request: TextRequest, token: str | None) -> RedactedEnv:
    """Locked CLI settings, the request's deadline per attempt, retries and output cap, and the token.

    No token (a smoke test on a laptop) leaves the CLI on its own `claude` login.
    """
    env = RedactedEnv(LOCKED_ENV)
    env["API_TIMEOUT_MS"] = str(max(1, round(attempt_timeout_s(request) * 1000)))
    env["CLAUDE_CODE_MAX_RETRIES"] = str(request.retries)
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(request.max_tokens)
    if token:
        env[TOKEN_VARIABLE] = token
    return env


def _drop(_line: str) -> None:
    """CLI stderr goes nowhere: it is diagnostics we do not show, and nothing secret may reach a log."""


def agent_options(
    request: TextRequest, token: str | None, cwd: Path, schema: type[BaseModel] | None = None
) -> ClaudeAgentOptions:
    """Options for one locked, one-shot call. Haiku 4.5 runs without thinking (as on the API, where it
    takes no `effort`); every other model gets the request's effort."""
    no_effort = request.model_id.startswith(NO_EFFORT_PREFIXES)
    return ClaudeAgentOptions(
        system_prompt=request.system,
        model=request.model_id,
        tools=[],
        max_turns=STRUCTURED_TURNS if schema is not None else TEXT_TURNS,
        setting_sources=[],
        strict_mcp_config=True,
        cwd=cwd,
        env=cli_env(request, token),
        output_format={"type": "json_schema", "schema": schema.model_json_schema()} if schema is not None else None,
        thinking={"type": "disabled"} if no_effort else None,
        effort=None if no_effort else request.effort,
        stderr=_drop,
        extra_args={"no-session-persistence": None},
    )


@dataclass(frozen=True)
class Outcome:
    """What one CLI run produced: its result, the last assistant error code, a usage-limit rejection,
    and the SDK exception that ended the stream (the SDK raises one after an error result)."""

    result: ResultMessage | None
    error: str | None = None
    limit: RateLimitInfo | None = None
    failure: ClaudeSDKError | None = None


async def collect(query_fn: QueryFn, prompt: str, options: ClaudeAgentOptions) -> Outcome:
    result: ResultMessage | None = None
    error: str | None = None
    limit: RateLimitInfo | None = None
    stream = query_fn(prompt=prompt, options=options)
    try:
        async for message in stream:
            if isinstance(message, ResultMessage):
                result = message
            elif isinstance(message, AssistantMessage) and message.error:
                error = str(message.error)
            elif isinstance(message, RateLimitEvent) and message.rate_limit_info.status == "rejected":
                limit = message.rate_limit_info
    except ClaudeSDKError as failure:
        return Outcome(result, error, limit, failure)
    finally:
        close = getattr(stream, "aclose", None)  # on a timeout too: closing ends the CLI process
        if close is not None:
            await close()
    return Outcome(result, error, limit)


def _limit_error(limit: RateLimitInfo) -> LLMError:
    window = (limit.rate_limit_type or "usage").replace("_", "-")
    when = f"; resets {datetime.fromtimestamp(limit.resets_at, UTC):%H:%M} UTC" if limit.resets_at is not None else ""
    return LLMError("usage_limit", f"Claude subscription {window} limit reached{when}")


def _status_error(code: str | None, status: int | None) -> LLMError | None:
    if code in AUTH_ERRORS or status in (401, 403):
        return LLMError("auth", f"the Claude subscription rejected {TOKEN_VARIABLE} (run `claude setup-token` again)")
    if code == "rate_limit" or status == 429:
        return LLMError("rate_limited", "Claude subscription rate limit")
    if code == "billing_error" or status == 402:
        return LLMError("usage_limit", "Claude subscription has no usage left")
    if code == "model_not_found" or status == 404:
        return LLMError("unknown_model", "Claude Code does not know this model id")
    if code == "max_output_tokens":
        return LLMError("bad_output", "the answer hit the output cap and is incomplete")
    if code is not None or status is not None:
        return LLMError("api_error", f"Claude Code API error {status or code}")
    return None


def outcome_error(outcome: Outcome) -> LLMError | None:
    """The `LLMError` an outcome stands for, or None when its result is usable. Messages are fixed
    strings and codes: nothing the CLI printed is passed on."""
    if outcome.limit is not None:
        return _limit_error(outcome.limit)
    if isinstance(outcome.failure, CLINotFoundError):
        return LLMError("cli_missing", "the Claude Code CLI is missing: reinstall claude-agent-sdk")
    result = outcome.result
    status = result.api_error_status if result is not None else None
    failed = result is not None and result.is_error
    known = _status_error(outcome.error, status) if (outcome.error or failed) else None
    if known is not None:
        return known
    if result is None and outcome.failure is None:
        return LLMError("bad_output", "Claude Code returned no result")
    if result is None:
        exit_code = getattr(outcome.failure, "exit_code", None)
        return LLMError("api_error", f"the Claude Code CLI failed (exit {exit_code})")
    if result.stop_reason == "refusal":
        return LLMError("refused", "the model declined (stop_reason refusal)")
    if result.subtype != "success":
        return LLMError("bad_output", f"Claude Code ended with {result.subtype}")
    if failed or outcome.failure is not None:
        return LLMError("api_error", "Claude Code returned an error result")
    return None


class SubscriptionProvider:
    """One-shot Claude calls on the Claude subscription: the `LLMProvider` behind the `claude-subscription` route."""

    name = NAME

    def __init__(
        self,
        token: str | None,
        *,
        query_fn: QueryFn = sdk_query,
        clock: Callable[[], float] = time.time,
        cwd: Path | None = None,
    ) -> None:
        self._token = token
        self._query = query_fn
        self._clock = clock  # unix time: usage windows reset at a unix timestamp, not at a game tick
        self._cwd = cwd or Path(tempfile.mkdtemp(prefix="bazaar-claude-"))  # empty: nothing to read there
        self._paused: tuple[float, LLMError] | None = None

    def __repr__(self) -> str:
        return f"SubscriptionProvider(token={'set' if self._token else 'login'})"

    def complete(self, request: TextRequest) -> str:
        text = (self._call(request, None).result or "").strip()
        if not text:
            raise LLMError("bad_output", "the answer has no text")
        return text

    def structured(self, request: TextRequest, schema: type[T]) -> T:
        result = self._call(request, schema)
        if result.structured_output is None:
            raise LLMError("bad_output", f"no structured output for {schema.__name__}")
        try:
            return schema.model_validate(result.structured_output)
        except ValidationError:
            raise LLMError("bad_output", f"the answer does not match {schema.__name__}") from None

    def _call(self, request: TextRequest, schema: type[BaseModel] | None) -> ResultMessage:
        if self._paused is not None:
            until, paused = self._paused
            if self._clock() < until:
                raise LLMError(paused.reason, str(paused))
            self._paused = None
        options = agent_options(request, self._token, self._cwd, schema)
        deadline = request.timeout_s

        def run() -> Outcome:
            return asyncio.run(asyncio.wait_for(collect(self._query, request.user, options), deadline))

        try:
            outcome: Outcome = run_with_deadline(run, deadline)
        except TimeoutError:
            raise LLMError("timeout", f"no answer within {deadline:.1f}s") from None
        found = outcome_error(outcome)
        if found is None and outcome.result is not None:  # outcome_error() covers a missing result
            return outcome.result
        error = found or LLMError("bad_output", "Claude Code returned no result")
        self._pause_after(error, outcome.limit)
        raise error

    def _pause_after(self, error: LLMError, limit: RateLimitInfo | None) -> None:
        now = self._clock()
        if error.reason == "auth":
            self._paused = (now + AUTH_PAUSE_S, error)
        elif error.reason == "usage_limit":
            resets = limit.resets_at if limit is not None and limit.resets_at is not None else None
            self._paused = (float(resets) if resets is not None else now + LIMIT_PAUSE_S, error)
