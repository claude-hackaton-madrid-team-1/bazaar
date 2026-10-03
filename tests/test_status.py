"""The agents' read-only status server: HTTP contract, CORS, scrubbing, WebSocket late-join replay, and that
publishing from the tick loop never waits on the server or a client. Local sockets only (127.0.0.1)."""

import asyncio
import json
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect

from bazaar_agent.agents.runtime import watched_clock
from bazaar_agent.agents.status import REPLAY, StatusHub, start_status_server


@pytest.fixture
def served():
    target = {"mode": "real", "url": "https://bazaar.causaprima.ai"}
    hub = StatusHub("taker", live=False, wall=lambda: 1_790_000_000.0, target=target)
    port = start_status_server(hub, "127.0.0.1", 0)
    return hub, port


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def decision(i=1, **extra):
    return {
        "decision_id": i,
        "tick": 100,
        "kind": "accept_ask",
        "line": f"accept LAV-02 #{i}",
        "move": {"accept": i},
        "reason": "worth 20.8",
        "strategy": "complete_pages",
        "guardrail": "allowed",
        "jev": {"verdict": "undecided", "value": 0.5},
        "chosen": True,
        "status": "approved",
        "sent": "would-send",
        **extra,
    }


def test_health_and_state_serve_the_contract_with_open_cors(served):
    hub, port = served
    hub.clock({"tick": 159, "doors": "closed", "paused": True, "next_opens": "2026-10-03T09:00:00+02:00"})
    status, headers, health = get(port, "/health")
    assert status == 200 and headers["Access-Control-Allow-Origin"] == "*"
    assert health == {
        "ok": True,
        "agent": "taker",
        "mode": "dry",
        "target": {"mode": "real", "url": "https://bazaar.causaprima.ai"},  # where its requests go
        "tick": None,
        "last_tick_at": None,
        "doors": "closed",
        "paused": True,
        "next_opens": "2026-10-03T09:00:00+02:00",
        "tick_seconds": None,
        "server_tick": 159,
    }
    hub.tick(160, 2.7, "t01")
    hub.view(threads=[{"dealer": "abuela", "thread": 5000, "item": "LAV-08"}])
    hub.decision(decision())
    _, _, state = get(port, "/state")
    assert (state["mode"], state["tick"], state["team"], state["threads"][0]["dealer"]) == ("dry", 160, "t01", "abuela")
    (d,) = state["decisions"]
    assert d["line"] == "accept LAV-02 #1" and d["jev"]["verdict"] == "undecided" and d["sent"] == "would-send"
    assert get(port, "/health")[2]["last_tick_at"] == "2026-09-21T14:13:20+00:00"
    assert get(port, "/nope")[0] == 404


def test_only_the_last_50_decisions_are_kept_in_state(served):
    hub, port = served
    for i in range(70):
        hub.decision(decision(i))
    decisions = get(port, "/state")[2]["decisions"]
    assert len(decisions) == 50 and decisions[-1]["decision_id"] == 69


def test_every_string_is_scrubbed_before_it_is_served(served):
    hub, port = served
    hub.decision(decision(reason="key tk-abcd-efgh-1234 and postgresql://bazaar:hunter2@db.internal:5432/x"))
    hub.view(threads=[{"note": "tk-zzzz-yyyy-9999"}])
    body = json.dumps(get(port, "/state")[2])
    assert "tk-abcd-efgh-1234" not in body and "hunter2" not in body and "tk-zzzz-yyyy-9999" not in body


async def _drain(port, until_quiet=0.5):
    got = []
    async with connect(f"ws://127.0.0.1:{port}/events") as ws:
        try:
            while True:
                got.append(json.loads(await asyncio.wait_for(ws.recv(), until_quiet)))
        except TimeoutError:
            return got


def test_a_late_websocket_client_first_gets_the_last_200_events_in_the_web_envelope(served):
    hub, port = served
    hub.tick(100, 1.5, "t01")
    for i in range(REPLAY + 50):
        hub.decision(decision(i))
    got = asyncio.run(_drain(port))
    assert len(got) == REPLAY and got[-1]["payload"]["decision_id"] == REPLAY + 49
    e = got[-1]
    assert set(e) == {"id", "tick", "t", "type", "scope", "actor", "agent", "payload"}
    assert (e["type"], e["scope"], e["actor"], e["agent"], e["tick"], e["t"]) == (
        "agent.decision",
        "team",
        "t01",
        "taker",
        100,
        1.5,
    )
    assert e["id"] < 0 and len({x["id"] for x in got}) == REPLAY  # made-up ids: negative, unique


def test_events_published_after_a_client_joined_reach_it_live(served):
    hub, port = served

    async def scenario():
        async with connect(f"ws://127.0.0.1:{port}/events") as ws:
            await asyncio.sleep(0.2)  # joined (no backlog yet)
            hub.execution({"decision_id": 7, "method": "accept", "request": {"offer": 1}, "response": {"ok": True}})
            return json.loads(await asyncio.wait_for(ws.recv(), 3))

    e = asyncio.run(scenario())
    assert e["type"] == "agent.execution" and e["payload"]["method"] == "accept"


def test_publishing_never_waits_on_the_server_or_a_stuck_client(served):
    hub, port = served

    async def stuck_client_then_publish():
        async with connect(f"ws://127.0.0.1:{port}/events", max_size=None, close_timeout=0.1):  # never reads
            await asyncio.sleep(0.2)
            started = time.perf_counter()
            for i in range(2000):
                hub.decision(decision(i, inputs={"blob": "x" * 2000}))
            return time.perf_counter() - started

    elapsed = asyncio.run(stuck_client_then_publish())
    # ~2000 publishes: each is an append and a scheduled broadcast, never a send. A send to the stuck
    # client would block for good; the bound only absorbs slow CI runners (2.00-2.08 s seen there).
    assert elapsed < 6.0
    assert get(port, "/health")[0] == 200  # and the server still answers


def test_a_hub_without_a_server_still_records_and_never_raises():
    hub = StatusHub("maker", live=True)
    hub.tick(5, 0.1, "t01")
    hub.decision(decision())
    assert hub.health()["mode"] == "live" and len(hub.replay()) == 2


def test_the_clock_watcher_logs_waiting_once_and_tells_the_hub():
    hub, lines = StatusHub("maker", live=False), []
    payloads = iter([{"tick": 159, "doors": "closed", "paused": True, "next_opens": "09:00"}] * 3 + [{"tick": 160}])
    read = watched_clock(lambda: next(payloads), "maker", lines.append, hub)
    for _ in range(4):
        read()
    assert lines == [
        "maker: waiting, doors closed, paused; next opening 09:00",
        "maker: the game is ticking again (tick 160)",
    ]
    assert hub.health()["server_tick"] == 160
