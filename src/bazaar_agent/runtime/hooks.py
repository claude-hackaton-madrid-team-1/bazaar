"""The desk's hooks: the second line of defense around every tool call (the tool code is the first).

PreToolUse (code.claude.com/docs/en/agent-sdk/hooks), for every call from the desk or a subagent:
1. the caller's allow-list (`agents.allow_lists()`): `agent_type` names the subagent, and is absent on
   the desk's own thread. A tool outside the caller's list is denied, whatever the permission rules say.
2. `Agent` may only start one of our subagents, and in the foreground (`run_in_background: false`
   through `updatedInput`), so the desk reports an answer instead of a task id.
3. every write tool runs `actions.check_write()` with the live /me, clock, open offers and shared
   ledger, and is denied with the violated rules. A read failure denies too: never trade blind.
A hook deny wins over every allow rule and permission mode (code.claude.com/docs/en/agent-sdk/permissions).

PostToolUse writes one `decisions` row per write call and one `executions` row per request that
reached the game (`runtime.journal`). A call the hook denied is recorded in PreToolUse, because
PostToolUse never fires for it. Every row and span is scrubbed: no token, key or URL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import HookEvent

from bazaar_agent import telemetry as tm
from bazaar_agent.runtime.actions import check_write
from bazaar_agent.runtime.agents import AGENT_TOOL, DESK
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.runtime.journal import record_denial, record_write
from bazaar_agent.runtime.tools import BY_MCP_NAME, ToolSpec, answer

PRE, POST = "PreToolUse", "PostToolUse"


def caller(input_data: Mapping[str, Any]) -> str:
    """Which agent made the call: the subagent's name, or the desk on the main thread."""
    return str(input_data.get("agent_type") or DESK)


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "deny", "permissionDecisionReason": reason}
    }


class Guard:
    """Allow-lists, the guardrail gate, and the decision log, for one desk session."""

    def __init__(
        self,
        backend: Backend,
        allow: Mapping[str, frozenset[str]],
        secrets: Iterable[str] = (),
        log: Callable[[str], None] = lambda line: None,
    ) -> None:
        self.b, self.allow, self.log = backend, dict(allow), log
        self.secrets = tuple(secrets)
        self.subagents = frozenset(name for name in self.allow if name != DESK)
        self._verdicts: dict[str, str] = {}  # tool_use_id -> the PreToolUse verdict, read by PostToolUse

    def hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """`ClaudeAgentOptions(hooks=...)`: both callbacks on every tool (no matcher)."""
        return {
            "PreToolUse": [HookMatcher(hooks=[self.pre_tool_use])],
            "PostToolUse": [HookMatcher(hooks=[self.post_tool_use])],
        }

    async def pre_tool_use(self, input_data: Any, tool_use_id: str | None, context: Any) -> Any:
        agent, tool_name = caller(input_data), str(input_data.get("tool_name") or "")
        tool_input = dict(input_data.get("tool_input") or {})
        if tool_name not in self.allow.get(agent, frozenset()):
            self.log(f"hook: DENIED {agent} → {tool_name.removeprefix('mcp__bazaar__')}: not on its allow-list")
            tm.guardrail_refusal(f"runtime.{agent}", tool_name, ("not on the allow-list",))
            return deny(f"{tool_name} is not on {agent}'s allow-list")
        if tool_name == AGENT_TOOL:
            return self._subagent(tool_input)
        spec = BY_MCP_NAME.get(tool_name)
        if spec is None or not spec.write:
            return {}
        allowed, verdict, tick = await asyncio.to_thread(self._check, spec, tool_input)
        if allowed:
            self._verdicts[tool_use_id or ""] = verdict
            return {}
        self.log(f"hook: DENIED {agent} → {spec.name}: {verdict}")
        tm.guardrail_refusal(f"runtime.{spec.name}", str(tool_input), (verdict,))
        await asyncio.to_thread(
            record_denial, self.b, f"desk/{agent}", spec.name, tool_input, verdict, tick, self.secrets
        )
        return deny(f"guardrails: {verdict}")

    def _subagent(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        kind = tool_input.get("subagent_type")
        if kind not in self.subagents:
            return deny(f"subagent {kind!r} is not one of ours: {', '.join(sorted(self.subagents))}")
        updated = {**tool_input, "run_in_background": False}
        return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "allow", "updatedInput": updated}}

    def _check(self, spec: ToolSpec, tool_input: dict[str, Any]) -> tuple[bool, str, int]:
        """(allowed, verdict text, tick). Invalid arguments or an unreadable live state deny."""
        try:
            args = spec.args.model_validate(tool_input)
        except ValueError as e:
            return False, f"denied: invalid arguments ({type(e).__name__})", -1
        try:
            with self.b.write_lock:
                planned = check_write(self.b, spec.name, args)
        except Exception as e:  # /me, the clock or the ledger did not answer: fail closed
            return False, f"denied: cannot read the live state ({type(e).__name__}), not trading blind", -1
        return planned.verdict.allowed, str(planned.verdict), planned.tick

    async def post_tool_use(self, input_data: Any, tool_use_id: str | None, context: Any) -> Any:
        agent, tool_name = caller(input_data), str(input_data.get("tool_name") or "")
        spec = BY_MCP_NAME.get(tool_name)
        payload = answer(input_data.get("tool_response"))
        values = {"bazaar.agent": agent, "bazaar.tool": tool_name, "bazaar.status": (payload or {}).get("status")}
        with tm.span("runtime.tool_call", tm.AGENT, values):
            if spec is not None and spec.write:
                verdict = self._verdicts.pop(tool_use_id or "", "allowed")
                tool_input = dict(input_data.get("tool_input") or {})
                await asyncio.to_thread(
                    record_write, self.b, f"desk/{agent}", spec.name, tool_input, payload, verdict, self.secrets
                )
        return {}
