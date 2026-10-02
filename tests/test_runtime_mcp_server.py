"""The remote MCP server over its real Streamable HTTP app (Starlette TestClient: no socket, no network).

Bearer token or 401, dry run by default, the guardrail check inside the server, rate limits per token,
no secret in any answer.
"""

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from typer.testing import CliRunner

from bazaar_agent.cli import app as cli_app
from bazaar_agent.runtime import mcp_server as ms
from tests.agent_fakes import clock, rows
from tests.runtime_fakes import TEAM_KEY, TOKEN, Public, Team, backend

MCP_TOKEN = "mcp-0123456789abcdef0123456789abcdef-ok"
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def client(b, calls_per_minute=30, now=None):
    return TestClient(ms.build_app(b, MCP_TOKEN, calls_per_minute, host="0.0.0.0", now=now or Clock()))


def rpc(c, method, params=None, token=MCP_TOKEN, rid=1):
    headers = {**HEADERS, "mcp-protocol-version": "2025-06-18"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return c.post(
        ms.MCP_PATH, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=headers
    )


def tool(c, name, arguments, rid=1):
    reply = rpc(c, "tools/call", {"name": name, "arguments": arguments}, rid=rid)
    assert reply.status_code == 200, reply.text
    result = reply.json()["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def test_every_request_but_health_needs_the_bearer_token(tmp_path):
    with client(backend(tmp_path)) as c:
        assert c.get(ms.HEALTH_PATH).json() == {"ok": True, "server": "bazaar", "tools": 18}  # no mode, no state
        for token in (None, "", "wrong-token", MCP_TOKEN[:-1], MCP_TOKEN + "x"):
            reply = rpc(c, "tools/list", token=token)
            assert reply.status_code == 401 and reply.json() == {"error": "unauthorized"}
            assert reply.headers["www-authenticate"].startswith("Bearer")
        assert rpc(c, "tools/list", token=f"bearer  {MCP_TOKEN}").status_code == 401  # one exact token only
        bearer = {"authorization": f"Bearer {MCP_TOKEN}"}
        with pytest.raises(WebSocketDisconnect), c.websocket_connect(ms.MCP_PATH, headers=bearer):
            pass
        listed = rpc(c, "tools/list").json()["result"]["tools"]
        assert {t["name"] for t in listed} >= {"status", "sell_bid", "dealer_buy", "duel_move"}
        assert next(t for t in listed if t["name"] == "status")["annotations"]["readOnlyHint"] is True


def test_writes_are_a_dry_run_by_default_and_the_guardrails_run_inside_the_server(tmp_path):
    team = Team()
    with client(backend(tmp_path, team=team)) as c:
        ok, failed = tool(c, "sell_bid", {"ref": "LAV-09", "price": 60})
        assert not failed and ok["status"] == "approved" and ok["sent"] is False and ok["guardrail"] == "allowed"
        refused, _ = tool(c, "sell_bid", {"ref": "LAV-09", "price": 500}, rid=2)
        assert refused["status"] == "rejected" and "price 500 > max_price_rare 80" in refused["guardrail"]
        held, _ = tool(c, "dealer_buy", {"item": "LAV-01", "max_price": 12, "start": 6}, rid=3)
        assert held["status"] == "rejected" and "we already hold LAV-01" in held["guardrail"]
        bad, failed = tool(c, "dealer_buy", {"item": "LAV-01", "max_price": 12, "start": 6, "dealer": "--x"}, rid=4)
        assert failed and bad.startswith("invalid arguments")
    assert team.sent == []
    decided = [(r["agent"], r["kind"], r["status"], r["dry_run"]) for r in rows(tmp_path)]
    assert decided[:3] == [
        ("mcp", "sell_bid", "approved", True),
        ("mcp", "sell_bid", "rejected", True),
        ("mcp", "dealer_buy", "rejected", True),
    ]


def test_tool_calls_are_rate_limited_per_token(tmp_path):
    now = Clock()
    with client(backend(tmp_path), calls_per_minute=2, now=now) as c:
        assert not tool(c, "clock", {})[1] and not tool(c, "clock", {}, rid=2)[1]
        text, failed = tool(c, "clock", {}, rid=3)
        assert failed and text.startswith("rate limited: 2 tool calls per minute")
        now.t += 31.0  # one call's worth refills at 2 per minute
        assert not tool(c, "clock", {}, rid=4)[1]


def test_http_requests_are_rate_limited_per_token_before_any_tool_runs(tmp_path):
    with client(backend(tmp_path)) as c:
        codes = [rpc(c, "tools/list", rid=i).status_code for i in range(ms.HTTP_BURST + 1)]
        assert codes[:-1] == [200] * ms.HTTP_BURST and codes[-1] == 429
        assert rpc(c, "tools/list", token="wrong").status_code == 401  # a bad token never touches the bucket


def test_no_answer_carries_a_key_a_token_or_a_url(tmp_path):
    class Leaky(Team):
        def me(self):
            raise RuntimeError(f"{TEAM_KEY} {TOKEN} {MCP_TOKEN} https://bazaar.example/api/me")

    with client(backend(tmp_path, team=Leaky(), public=Public(now=clock()))) as c:
        text, failed = tool(c, "status", {})
        raw = rpc(c, "tools/call", {"name": "rules", "arguments": {}}, rid=2).text
    assert failed and text == "RuntimeError: the tool failed"
    for secret in (TEAM_KEY, TOKEN, MCP_TOKEN):
        assert secret not in raw and secret not in text


def test_a_failed_audit_row_never_turns_a_write_into_an_error(tmp_path):
    class DiskFull:
        def begin_tick(self, tick):
            pass

        def decide(self, decision):
            raise OSError("No space left on device")

    b = backend(tmp_path)
    b._decisions = DiskFull()
    with client(b) as c:
        answer, failed = tool(c, "sell_bid", {"ref": "LAV-09", "price": 60})
    assert not failed and answer["status"] == "approved"


def test_the_server_will_not_start_without_a_long_token(tmp_path, monkeypatch):
    for value in (None, "", "short", "a" * 64, "ab" * 32):  # too short, or not random
        with pytest.raises(ms.TokenError):
            ms.require_token(value)
    monkeypatch.setenv(ms.TOKEN_VARIABLE, "short")
    monkeypatch.setattr("bazaar_agent.config.read_env_file", lambda path: {})
    out = CliRunner().invoke(cli_app, ["mcp", "serve"])
    assert out.exit_code == 1 and "32+ characters" in out.output and "short" not in out.output
