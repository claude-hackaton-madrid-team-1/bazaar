"""The desk's hooks and wiring: allow-lists per agent, the guardrail gate, decision rows, no token logged."""

import asyncio
import json

from bazaar_agent.llm.chooser import ModelChoice
from bazaar_agent.runtime import agents as ag
from bazaar_agent.runtime import desk as dk
from bazaar_agent.runtime import tools as tl
from bazaar_agent.runtime.desk_models import ROLES, DeskModels
from bazaar_agent.runtime.hooks import Guard
from tests.agent_fakes import our_ask, rows
from tests.runtime_fakes import DUEL, TEAM_KEY, TOKEN, Team, backend

BID_OK = {"ref": "LAV-09", "price": 60}
BID_TOO_HIGH = {"ref": "LAV-09", "price": 500}


def models_of(alias, **per_role):
    """DeskModels with `alias` for every role, except the roles named in `per_role`."""
    return DeskModels({role: ModelChoice(per_role.get(role, alias), "default", "test") for role in ROLES})


def guard(b, lines=None):
    return Guard(b, ag.allow_lists(), tl.secrets_of(b.settings), log=(lines.append if lines is not None else print))


def pre(g, tool, args=None, agent=None, use_id="tu-1"):
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args or {}, "session_id": "s", "cwd": "/"}
    if agent:
        data["agent_type"] = agent
    return asyncio.run(g.pre_tool_use(data, use_id, None))


def post(g, tool, args, response, agent=None, use_id="tu-1"):
    data = {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": args, "tool_response": response}
    if agent:
        data["agent_type"] = agent
    return asyncio.run(g.post_tool_use(data, use_id, None))


def denied(out):
    spec = out.get("hookSpecificOutput") or {}
    return spec.get("permissionDecision") == "deny", spec.get("permissionDecisionReason", "")


def mcp(name):
    return tl.BY_NAME[name].mcp_name


def test_the_hook_denies_a_guardrail_violation_and_allows_a_valid_write(tmp_path):
    b = backend(tmp_path)
    g = guard(b, lines := [])
    is_denied, why = denied(pre(g, mcp("sell_bid"), BID_TOO_HIGH, agent="buyer"))
    assert is_denied and "price 500 > max_price_rare 80" in why and "cash_floor" in why
    assert pre(g, mcp("sell_bid"), BID_OK, agent="buyer") == {}
    assert any("DENIED buyer → sell_bid" in line for line in lines)
    (row,) = rows(tmp_path)
    assert row["agent"] == "desk/buyer" and row["status"] == "rejected" and row["chosen"] is False
    assert "max_price_rare" in row["guardrail"] and row["inputs"] == BID_TOO_HIGH


def test_each_agent_may_call_only_its_own_tools(tmp_path):
    g = guard(backend(tmp_path, team=Team(duels=[DUEL])))
    allowed = {
        ("buyer", "dealer_buy"),
        ("buyer", "sell_bid"),
        ("seller", "sell_list"),
        ("seller", "sell_cancel"),
        ("duelist", "duel_move"),
        ("strategist", "steer"),
    }
    for agent in ("strategist", "buyer", "seller", "duelist", None):
        for name in tl.WRITE_TOOLS:
            is_denied, why = denied(pre(g, mcp(name), {}, agent=agent))
            if (agent, name) in allowed:
                assert "allow-list" not in why, (agent, name)  # it reached the guardrail check
            else:
                assert is_denied and "allow-list" in why, (agent, name)
    for agent in ("strategist", "buyer", "seller", "duelist", None):
        assert pre(g, mcp("status"), {}, agent=agent) == {}
    assert denied(pre(g, mcp("strategy"), {}))[0]  # the desk routes analysis to the strategist
    assert denied(pre(g, mcp("status"), {}, agent="general-purpose"))[0]
    assert denied(pre(g, "Bash", {"command": "env"}, agent="buyer"))[0]


def test_the_desk_starts_only_our_subagents_and_in_the_foreground(tmp_path):
    g = guard(backend(tmp_path))
    out = pre(g, ag.AGENT_TOOL, {"subagent_type": "buyer", "prompt": "buy LAV-09 under 90", "run_in_background": True})
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "allow" and spec["updatedInput"]["run_in_background"] is False
    # a per-call `model` would beat the subagent's definition (Jev's choice): the hook drops it
    overridden = pre(g, ag.AGENT_TOOL, {"subagent_type": "buyer", "prompt": "buy", "model": "haiku"})
    assert overridden["hookSpecificOutput"]["updatedInput"] == {
        "subagent_type": "buyer",
        "prompt": "buy",
        "run_in_background": False,
    }
    assert denied(pre(g, ag.AGENT_TOOL, {"subagent_type": "general-purpose", "prompt": "x"}))[0]
    assert denied(pre(g, ag.AGENT_TOOL, {"subagent_type": "buyer"}, agent="buyer"))[0]  # no nested subagents


def test_an_unreadable_live_state_denies_the_write(tmp_path):
    class Down(Team):
        def me(self):
            raise ConnectionError("network down")

    is_denied, why = denied(pre(guard(backend(tmp_path, team=Down())), mcp("sell_bid"), BID_OK, agent="buyer"))
    assert is_denied and "cannot read the live state (ConnectionError)" in why and "blind" in why


def test_post_tool_use_writes_a_decision_per_write_and_an_execution_per_send(tmp_path):
    dry = backend(tmp_path / "dry")
    g = guard(dry)
    assert pre(g, mcp("sell_bid"), BID_OK, agent="buyer") == {}
    text, _ = tl.call(tl.BY_NAME["sell_bid"], dry, BID_OK)
    post(g, mcp("sell_bid"), BID_OK, [{"type": "text", "text": text}], agent="buyer")
    post(g, mcp("status"), {}, [{"type": "text", "text": "{}"}], agent="buyer")  # reads write no row
    (row,) = rows(tmp_path / "dry")
    assert row["status"] == "approved" and row["dry_run"] is True and row["chosen"] is True
    assert row["move"]["give"] == {"cash": 60} and rows(tmp_path / "dry", "executions.jsonl") == []

    live = backend(tmp_path / "live", live=True, team=Team(offers=[our_ask(77, 3, "LAT-03", 5)]))
    g = guard(live)
    text, _ = tl.call(tl.BY_NAME["sell_cancel"], live, {"offer_id": 77})
    post(g, mcp("sell_cancel"), {"offer_id": 77}, {"content": [{"type": "text", "text": text}]}, agent="seller")
    (row,) = rows(tmp_path / "live")
    (execution,) = rows(tmp_path / "live", "executions.jsonl")
    assert row["status"] == "done" and row["dry_run"] is False and row["agent"] == "desk/seller"
    assert execution["sdk_method"] == "cancel" and execution["request"] == {"offer_id": 77}


def test_tokens_and_keys_never_reach_a_row_a_log_line_or_the_options_repr(tmp_path):
    b = backend(tmp_path)
    g = guard(b, lines := [])
    sneaky = {"ref": "LAV-09", "price": 500, "note": f"{TOKEN} {TEAM_KEY}"}
    pre(g, mcp("sell_bid"), sneaky, agent="buyer")  # invalid (extra field) and denied: still recorded
    pre(g, mcp("dealer_buy"), {"item": "LAV-08", "max_price": 20, "start": 10}, agent="seller")
    post(g, mcp("sell_bid"), sneaky, f'{{"status":"failed","reason":"{TOKEN}"}}', agent="buyer")
    stored = (tmp_path / "agents" / "decisions.jsonl").read_text()
    assert TOKEN not in stored and TEAM_KEY not in stored and TOKEN not in "".join(lines)
    options = dk.desk_options(g, tl.sdk_server(b), TOKEN, dk.DeskConfig(8, 30.0), tmp_path, models_of("sonnet-5-5"))
    assert TOKEN not in repr(options) and TOKEN not in str(options.env)
    assert options.env[dk.TOKEN_VARIABLE] == TOKEN  # it reaches the CLI process, and only there


def test_the_desk_options_lock_the_session_down(tmp_path):
    b = backend(tmp_path)
    chosen = models_of("haiku-4-5", buyer="opus-5-5", duelist="sonnet-5-5")
    options = dk.desk_options(guard(b), tl.sdk_server(b), None, dk.DeskConfig(8, 30.0), tmp_path, chosen)
    assert options.tools == ["Agent"] and options.permission_mode == "dontAsk" and options.setting_sources == []
    assert set(options.allowed_tools) == {"Agent", *(s.mcp_name for s in tl.TOOLS)}
    assert {"Bash", "WebFetch"} <= set(options.disallowed_tools)
    assert set(options.hooks) == {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
    assert options.env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"
    assert options.env["CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS"] == "1"
    assert options.env["ANTHROPIC_API_KEY"] == "" and dk.TOKEN_VARIABLE not in options.env
    assert options.model == "claude-haiku-4-5-20251001" and options.mcp_servers["bazaar"]["type"] == "sdk"
    definitions = options.agents
    assert set(definitions) == {"strategist", "buyer", "seller", "duelist"}
    # each subagent runs the model chosen for it (a full id), not the desk's
    assert {name: d.model for name, d in definitions.items()} == {
        "strategist": "claude-haiku-4-5-20251001",
        "buyer": "claude-opus-5-5",
        "seller": "claude-haiku-4-5-20251001",
        "duelist": "claude-sonnet-5-5",
    }
    for name, definition in definitions.items():
        assert set(definition.tools) == ag.AGENTS[name].allowed() and "Agent" not in definition.tools
        assert "untrusted data" in definition.prompt
    assert set(definitions["buyer"].tools) - {tl.BY_NAME[n].mcp_name for n in tl.READ_TOOLS} == {
        mcp("dealer_buy"),
        mcp("sell_bid"),
    }
    assert "untrusted data" in options.system_prompt


def test_a_failing_hook_denies_and_a_failed_tool_call_is_still_recorded(tmp_path, monkeypatch):
    b = backend(tmp_path, live=True)
    g = guard(b, [])

    def boom(spec, tool_input):
        raise RuntimeError("bug")

    monkeypatch.setattr(g, "_check", boom)
    is_denied, why = denied(pre(g, mcp("sell_bid"), BID_OK, agent="buyer"))
    assert is_denied and "the guardrail hook failed (RuntimeError)" in why
    data = {"tool_name": mcp("sell_bid"), "tool_input": BID_OK, "agent_type": "buyer", "error": "boom"}
    asyncio.run(g.post_tool_use_failure(data, "tu-9", None))
    (row,) = rows(tmp_path)
    assert row["status"] == "failed" and row["dry_run"] is False and row["agent"] == "desk/buyer"


def test_a_live_rejection_is_recorded_as_live(tmp_path):
    b = backend(tmp_path, live=True, team=Team(threads=[{"id": 31, "with": "abuela", "status": "open"}]))
    args = {"item": "LAV-08", "max_price": 20, "start": 10}
    text, _ = tl.call(tl.BY_NAME["dealer_buy"], b, args)
    post(guard(b), mcp("dealer_buy"), args, [{"type": "text", "text": text}], agent="buyer")
    (row,) = rows(tmp_path)
    assert row["status"] == "rejected" and row["dry_run"] is False


def test_who_may_call_lists_every_tool_and_writes_have_one_owner():
    callers = ag.who_may_call()
    assert set(callers) == set(tl.BY_NAME)
    assert {name: callers[name] for name in tl.WRITE_TOOLS} == {
        "dealer_buy": ("buyer",),
        "sell_list": ("seller",),
        "sell_bid": ("buyer",),
        "sell_cancel": ("seller",),
        "duel_move": ("duelist",),
        "steer": ("strategist",),
    }
    assert json.dumps(sorted(callers["status"])) == json.dumps(sorted(ag.AGENTS))
