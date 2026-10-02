"""Every write call of the runtime's tools in `decisions` / `executions`, whoever made it.

The desk's hooks record their subagents' calls (`desk/buyer`, ...) and the remote MCP server records
its clients' calls (`mcp`), with the same rows as the taker and maker: one `decisions` row per write
(dry runs too, `dry_run = true`) and one `executions` row per request that reached the game. Every
value is scrubbed first (`tools.safe_value`): no token, key or URL lands in a row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from bazaar_agent.decisions import Decision, Status
from bazaar_agent.runtime.backend import Backend
from bazaar_agent.runtime.tools import safe_value

STATUSES: frozenset[str] = frozenset({"approved", "rejected", "expired", "done", "failed"})


def clean(value: Any, secrets: Iterable[str]) -> Any:
    return safe_value(json.loads(json.dumps(value, default=str)), secrets)


def status_of(value: str) -> Status:
    """The decisions-table status for a tool answer; a duel `hold` sent nothing and counts as approved."""
    return value if value in STATUSES else "approved"  # type: ignore[return-value]


def record_denial(
    b: Backend, agent: str, tool: str, tool_input: dict[str, Any], verdict: str, tick: int, secrets: Iterable[str]
) -> None:
    """A call refused before the tool ran (the desk's PreToolUse hook)."""
    b.decisions.begin_tick(tick)
    b.decisions.decide(
        Decision(
            agent=agent,
            tick=tick,
            kind=tool,
            inputs=clean(tool_input, secrets),
            reason="denied by the PreToolUse hook",
            guardrail=verdict,
            chosen=False,
            status="rejected",
            dry_run=not b.live,
        )
    )


def record_write(
    b: Backend,
    agent: str,
    tool: str,
    tool_input: dict[str, Any],
    payload: dict[str, Any] | None,
    verdict: str,
    secrets: Iterable[str],
) -> None:
    """One `decisions` row for the write, and one `executions` row when a request reached the game."""
    held = tuple(secrets)
    body = payload or {"status": "failed", "reason": "the tool returned no answer"}
    status = str(body.get("status"))
    tick = int(body.get("tick") or -1)
    b.decisions.begin_tick(tick)
    decision_id = b.decisions.decide(
        Decision(
            agent=agent,
            tick=tick,
            kind=tool,
            inputs=clean(tool_input, held),
            reason=str(body.get("reason") or body.get("would") or tool),
            guardrail=str(body.get("guardrail") or verdict),
            chosen=status in ("approved", "done", "failed"),
            status=status_of(status),
            dry_run=not body.get("sent") and not body.get("method"),
            move=clean(body.get("request") or {}, held),
        )
    )
    if body.get("method"):
        b.decisions.executed(
            decision_id,
            tick,
            str(body["method"]),
            clean(body.get("request") or {}, held),
            clean(body.get("response"), held) if body.get("response") is not None else None,
            body.get("error_code"),
        )
