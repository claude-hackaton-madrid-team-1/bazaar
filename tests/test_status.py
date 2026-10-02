"""The agents' read-only status server: HTTP contract, CORS, scrubbing, WebSocket late-join replay, that
publishing from the tick loop never waits on the server or a client, and that only the allow-listed public
view of a decision is served (never our values, limits or reasons). Local sockets only (127.0.0.1)."""

import asyncio
import json
import re
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect

from bazaar_agent.agents.runtime import JevAdvice, Recorder, watched_clock
from bazaar_agent.agents.status import REPLAY, StatusHub, start_status_server
from bazaar_agent.decisions import DecisionLog


@pytest.fixture
def served():
    hub = StatusHub("taker", live=False, wall=lambda: 1_790_000_000.0)
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
        "inputs": {"offer_id": i, "ref": "LAV-02", "venue": "rastro", "ask": 9},
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
    assert (d["kind"], d["inputs"]["ref"], d["move"], d["jev"], d["sent"]) == (
        "accept_ask",
        "LAV-02",
        {"accept": 1},
        {"verdict": "undecided"},
        "would-send",
    )
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
    leak = "key tk-abcd-efgh-1234 and postgresql://bazaar:hunter2@db.internal:5432/x"
    hub.decision(decision(inputs={"ref": leak}))  # an allow-listed field, so the scrubber is what removes it
    hub.view(threads=[{"dealer": "tk-zzzz-yyyy-9999"}])
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
                hub.decision(decision(i, inputs={"ref": "x" * 2000}))
            return time.perf_counter() - started

    elapsed = asyncio.run(stuck_client_then_publish())
    assert elapsed < 2.0  # ~2000 publishes: each is an append and a scheduled broadcast, never a send
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


# ---------------------------------------------------------------- the public view (#24): no private numbers

# A real taker row (tick 155): our card value, ladder, max, surplus, score and the affinity behind them.
PRIVATE = {56.1, 26.0, 34.1, 40.9, 25.0, 1.6, 16.1, 17.0, 4.0}
OPEN_INPUTS = {
    "dealer": "abuela",
    "item": "LAV-08",
    "rarity": "uncommon",
    "value": 56.1,
    "plan": "17→26 step 4",
    "score": 40.9,
    "surplus": 34.1,
}
OPEN_REASON = "worth 25×1.6 + bonus share 16.1 = 56.1; ladder 17→26"
BID_INPUTS = {
    "dealer": "abuela",
    "thread": 812,
    "item": "LAV-08",
    "her_ask": 30,
    "final": False,
    "our_bids": [17],
    "max": 26,
}


def numbers(payload):
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", json.dumps(payload, ensure_ascii=False))}


def taker_rows(tmp_path, hub):
    """A dealer opening and a bid through the real Recorder: the same calls the taker makes."""
    log = DecisionLog(tmp_path)
    rec = Recorder("taker", log, live=False, log=lambda line: None, hub=hub)
    rec.decide(
        155,
        "dealer_open",
        "open thread with abuela for LAV-08 (ladder 17→26 step 4, worth 56.1) · guardrails allowed",
        inputs=OPEN_INPUTS,
        reason=OPEN_REASON,
        guardrail="allowed",
        chosen=True,
        status="approved",
        move={"open_thread": "abuela", "topic": {"buy": "LAV-08"}},
    )
    rec.decide(
        155,
        "dealer_bid",
        "bid 21 to abuela on thread 812 for LAV-08 (step up from 17, max 26) · guardrails allowed",
        inputs=BID_INPUTS,
        reason="step up from 17, max 26",
        guardrail="allowed",
        chosen=True,
        status="approved",
        jev=JevAdvice("yes", 0.934, {"yes": 0.934}, None, "d1g35t"),
        thread_id=812,
        move={"kind": "bid", "price": 21},
    )
    return log


def test_a_published_decision_carries_no_private_value_limit_or_reason(served, tmp_path):
    hub, port = served
    hub.tick(155, 2.5, "t01")
    taker_rows(tmp_path, hub)
    opened, bid = get(port, "/state")[2]["decisions"]
    events = [e for e in asyncio.run(_drain(port)) if e["type"] == "agent.decision"]
    assert [e["payload"] for e in events] == [opened, bid]  # HTTP and WS show the same public view
    for d in (opened, bid):
        assert not {"line", "reason", "strategy"} & set(d)
        assert not {"value", "max", "surplus", "score", "plan", "our_bids"} & set(d["inputs"])
        assert numbers(d).isdisjoint(PRIVATE), numbers(d) & PRIVATE
    assert opened["inputs"] == {"dealer": "abuela", "item": "LAV-08", "rarity": "uncommon"}
    assert opened["move"] == {"open_thread": "abuela", "topic": {"buy": "LAV-08"}}
    assert bid["inputs"] == {"dealer": "abuela", "thread": 812, "item": "LAV-08", "her_ask": 30, "final": False}
    assert (bid["move"], bid["jev"], bid["guardrail"], bid["thread_id"]) == (
        {"kind": "bid", "price": 21},
        {"verdict": "yes"},
        "allowed",
        812,
    )


def test_the_decisions_table_still_gets_the_full_private_row(served, tmp_path):
    hub, _ = served
    log = taker_rows(tmp_path, hub)
    rows = [json.loads(line) for line in (log.dir / "decisions.jsonl").read_text().splitlines()]
    assert [r["inputs"] for r in rows] == [OPEN_INPUTS, BID_INPUTS]
    assert rows[0]["reason"] == OPEN_REASON and rows[1]["jev"]["value"] == 0.934


def test_an_input_nobody_allow_listed_stays_private(served):
    hub, port = served
    hub.decision(decision(inputs={"ref": "LAV-02", "affinity": 1.6, "brand_new_field": "secret plan", "ask": 9}))
    (d,) = get(port, "/state")[2]["decisions"]
    assert d["inputs"] == {"ref": "LAV-02", "ask": 9}
    assert "secret plan" not in json.dumps(d) and "1.6" not in json.dumps(d)


def test_guardrail_and_jev_publish_labels_not_our_cash_or_limits(served):
    hub, port = served
    denied = "denied: cash 301 - 40 < cash_floor 270; price 40 > max_price_uncommon 26"
    hub.decision(decision(guardrail=denied, status="rejected", chosen=False))
    hub.decision(decision(guardrail="-", status="rejected", chosen=False, jev={"verdict": "no", "value": 0.12}))
    first, second = get(port, "/state")[2]["decisions"]
    assert (first["guardrail"], second["guardrail"], second["jev"]) == ("denied", "-", {"verdict": "no"})
    assert numbers(first).isdisjoint({301.0, 270.0, 26.0}) and "0.12" not in json.dumps(second)


def test_a_price_we_never_sent_is_not_published(served):
    hub, port = served
    unsent = {"side": "ask", "ref": "LAT-09", "price": 68, "value": 35, "venue": "rastro"}
    hub.decision(decision(kind="post_ask", inputs=unsent, status="expired", move={"want": {"cash": 68}}))
    hub.decision(decision(kind="post_ask", inputs=unsent, status="approved", move={"want": {"cash": 68}}))
    expired, posted = get(port, "/state")[2]["decisions"]
    assert expired["inputs"] == {"side": "ask", "ref": "LAT-09", "venue": "rastro"} and expired["move"] == {}
    assert posted["inputs"]["price"] == 68 and posted["move"] == {"want": {"cash": 68}}


def test_a_maker_jev_state_shows_the_card_not_our_cash_or_value(served):
    hub, port = served
    state = {
        "offer": {"side": "ask", "card": "LAT-09", "price": 70, "age_ticks": 5, "venue": "rastro"},
        "new_price": 62,
        "value_to_us": 35.5,
        "strategy_reason": "ours 35 + page bonus 10.0",
        "cash": 412,
        "cash_floor": 270,
        "cash_above_floor": 142,
    }
    hub.decision(decision(kind="hold_ask", inputs=state, move={"hold": 77}))
    (d,) = get(port, "/state")[2]["decisions"]
    assert d["inputs"] == {"side": "ask", "card": "LAT-09", "price": 70, "venue": "rastro"}
    assert numbers(d).isdisjoint({62.0, 35.5, 35.0, 10.0, 412.0, 270.0, 142.0})


def test_executions_publish_the_request_sent_not_the_games_answer(served):
    hub, port = served

    async def scenario():
        async with connect(f"ws://127.0.0.1:{port}/events") as ws:
            await asyncio.sleep(0.2)
            hub.execution(
                {
                    "decision_id": 7,
                    "tick": 155,
                    "method": "say",
                    "request": {"thread": 812, "price": 21, "note": "max 26"},
                    "response": {"id": 9001, "your_value": 56.1, "cash": 380},
                    "error_code": None,
                }
            )
            return json.loads(await asyncio.wait_for(ws.recv(), 3))["payload"]

    assert asyncio.run(scenario()) == {
        "agent": "taker",
        "decision_id": 7,
        "tick": 155,
        "method": "say",
        "request": {"thread": 812, "price": 21},
        "ok": True,
        "error_code": None,
        "created_id": 9001,
    }


def test_the_threads_view_hides_our_bids_max_and_value(served):
    hub, port = served
    thread = {"dealer": "abuela", "thread": 812, "item": "LAV-08", "our_bids": [17, 21], "max": 26, "value": 56.1}
    hub.view(threads=[{**thread, "ticks": 3, "opened_tick": 152, "accepted_price": None}], debug={"cash": 380})
    state = get(port, "/state")[2]
    assert state["threads"] == [
        {"dealer": "abuela", "thread": 812, "item": "LAV-08", "ticks": 3, "opened_tick": 152, "accepted_price": None}
    ]
    assert "debug" not in state
