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
        "inputs": {"offer_id": i, "ref": "LAV-02", "venue": "rastro", "ask": 9},
        "move": {"accept": i},
        "reason": "worth 20.8",
        "strategy": "complete_pages",
        "guardrail": "allowed",
        "jev": {"verdict": "undecided", "value": 0.5},
        "chosen": True,
        "status": "approved",
        "dry_run": False,
        "sent": "sending",
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
    assert (d["kind"], d["inputs"]["ref"], d["move"], d["jev"], d["sent"]) == (
        "accept_ask",
        "LAV-02",
        {"accept": 1},
        None,  # Jev's label beside a price marks our walk-away price: never published
        "sending",
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
    rec = Recorder("taker", log, live=True, log=lambda line: None, hub=hub)  # decide() itself never sends
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
        None,
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
    hub.decision(decision(kind="post_bid", guardrail=denied, status="rejected", chosen=False))
    hub.decision(decision(guardrail="-", jev={"verdict": "no", "value": 0.12}))
    first, second = get(port, "/state")[2]["decisions"]
    assert (first["guardrail"], second["guardrail"], first["jev"], second["jev"]) == ("-", "-", None, None)
    hub.decision(decision())  # a sent row was allowed by definition
    assert get(port, "/state")[2]["decisions"][-1]["guardrail"] == "allowed"
    assert numbers(first).isdisjoint({301.0, 270.0, 26.0}) and "0.12" not in json.dumps(second)


def test_an_unsent_accept_is_not_published_at_all(served):
    hub, port = served
    board = {"offer_id": 9137, "venue": "rastro", "maker": "t07", "ref": "LAV-02", "rarity": "common", "ask": 9}
    for kind in ("accept_ask", "accept_bid"):
        for extra in (
            {"status": "rejected", "chosen": False, "guardrail": "-"},  # a quota skip
            {"status": "rejected", "chosen": False, "guardrail": "denied: price 40 > max_price_uncommon 26"},
            {"status": "approved", "chosen": True, "dry_run": True, "sent": "would-send"},
            {"status": "expired", "chosen": False},
        ):
            hub.decision(decision(kind=kind, inputs=board, move={"accept": 9137, "price": 10}, **extra))
    assert get(port, "/state")[2]["decisions"] == []
    assert asyncio.run(_drain(port)) == []


@pytest.mark.parametrize("dry_run", [True, None])  # None: a row without the flag counts as a dry run
def test_a_dry_run_row_is_cut_down_to_card_and_venue(served, dry_run):
    hub, port = served
    row = {"side": "ask", "ref": "LAV-02", "venue": "rastro", "price": 40, "value": 35}
    hub.decision(decision(kind="post_ask", inputs=row, dry_run=dry_run, sent="would-send", move={"want": {"cash": 40}}))
    (d,) = get(port, "/state")[2]["decisions"]
    assert (d["status"], d["inputs"], d["move"], d["jev"], d["guardrail"]) == (
        "approved",
        {"side": "ask", "ref": "LAV-02", "venue": "rastro"},
        {},
        None,
        "-",
    )
    assert numbers(d) == {100.0, 2.0}


@pytest.mark.parametrize("kind", ["reprice_ask", "reprice_bid", "hold_ask"])
def test_a_maker_reprice_row_publishes_no_price(served, kind):
    """maker.py writes reprice rows approved + chosen=False with the strategy's TARGET as move.price: for a
    bid that target gives away our top bid (aggressive = 2*fair - quick)."""
    hub, port = served
    state = {"offer": {"side": "bid", "card": "LAT-09", "price": 27, "venue": "rastro"}, "new_price": 40}
    hub.decision(
        decision(
            kind=kind,
            chosen=False,
            inputs={**state, "side": "bid", "price": 27},
            move={"reprice": 77, "price": 40},
            jev={"verdict": "quick_sale", "value": 0.9},
        )
    )
    (d,) = get(port, "/state")[2]["decisions"]
    assert d["move"] == {} and d["jev"] is None and "price" not in d["inputs"]
    assert numbers(d).isdisjoint({40.0, 27.0, 77.0, 0.9}) and "quick_sale" not in json.dumps(d)


def test_nested_values_keep_only_card_and_cash_keys(served):
    hub, port = served
    probe = {"max": 26, "value": 56.1, "buy": {"pack": "sobre_barrio", "limit": 17, "ref": "LAV-08"}}
    hub.decision(decision(kind="dealer_open", move={"open_thread": "abuela", "topic": probe, "want": {"cash": 9}}))
    hub.execution({"decision_id": 1, "tick": 100, "method": "POST", "request": {"with": "abuela", "topic": probe}})
    (d,) = get(port, "/state")[2]["decisions"]
    topic = {"buy": {"pack": "sobre_barrio", "ref": "LAV-08"}}
    assert d["move"] == {"open_thread": "abuela", "topic": topic, "want": {"cash": 9}}
    body = json.dumps([d, *asyncio.run(_drain(port))])
    assert "56.1" not in body and '"max"' not in body and '"limit"' not in body


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


PRIVATE_KEYS = [
    "limit",
    "max",
    "value",
    "your_value",
    "value_to_us",
    "quick",
    "fair",
    "floor",
    "reason",
    "line",
    "jev",
    "budget",
    "cap",
    "cash_floor",
    "surplus",
    "score",
    "affinity",
    "strategy",
    "new_price",
    "target",
    "aggressive",
    "ladder",
    "probabilities",
    "digest",
    "line",
    "max_price",
]
SECRET_NUMBERS = (31337.25, 4242.5, 777.125, 90210.75)  # no game number looks like these


def _random_value(rng, private, depth=0):
    """Numbers under a private key are SECRET_NUMBERS; under a public key they are small game numbers."""
    kind = rng.choice(["num", "str", "bool", "none", "dict", "list"] if depth < 3 else ["num", "str"])
    if kind == "num":
        return rng.choice(SECRET_NUMBERS) if private else rng.randint(1, 50)
    if kind == "str":
        return rng.choice(["LAV-02", "abuela", "rastro", "ask", "bid"])
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "none":
        return None
    if kind == "list":
        return [_random_value(rng, private, depth + 1) for _ in range(rng.randint(0, 3))]
    keys = PRIVATE_KEYS + ["ref", "cash", "buy", "pack", "card", "side", "venue", "price", "ask", "give", "want"]
    picked = [rng.choice(keys) for _ in range(rng.randint(0, 4))]
    return {k: _random_value(rng, private or k in PRIVATE_KEYS, depth + 1) for k in picked}


def _fields(rng, names, n):
    return {k: _random_value(rng, k in PRIVATE_KEYS) for k in rng.sample(names, n)}


def _random_row(rng, i):
    kinds = ["accept_ask", "accept_bid", "dealer_open", "dealer_bid", "dealer_accept", "dealer_walk", "post_ask"]
    kinds += ["post_bid", "cancel_ask", "hold_bid", "reprice_ask", "reprice_bid"]
    row = _fields(rng, PRIVATE_KEYS, rng.randint(2, 8))
    row.update(
        decision_id=i,
        tick=100,
        kind=rng.choice(kinds),
        status=rng.choice(["approved", "rejected", "expired"]),
        chosen=rng.choice([True, False, None]),
        dry_run=rng.choice([True, False, None]),
        sent=rng.choice(["would-send", "sending", "not sent"]),
        guardrail=rng.choice(["allowed", "-", "denied: cash 31337.25 < cash_floor 4242.5"]),
        jev={"verdict": "quick_sale", "value": 777.125, "reason": "floor 90210.75"},
        reason="worth 31337.25",
        inputs=_fields(rng, PRIVATE_KEYS + ["ref", "venue", "side", "ask", "offer", "listing"], 6),
        move=_fields(rng, PRIVATE_KEYS + ["reprice", "hold", "topic", "want", "price"], 4),
    )
    return row


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


@pytest.mark.parametrize("seed", range(8))
def test_random_rows_never_publish_a_private_key_or_number(served, seed):
    """Property: whatever decision/execution rows come in, /state and /events carry no private key and none of
    the private numbers (they sit under private keys, in nested values, in the guardrail text and in jev)."""
    import random

    rng = random.Random(seed)
    hub, port = served
    for i in range(150):
        hub.decision(_random_row(rng, i))
        hub.execution(
            {
                "decision_id": i,
                "tick": 100,
                "method": "POST",
                "request": _fields(rng, PRIVATE_KEYS + ["price", "topic", "give"], 5),
                "response": {"id": 5, "value": 31337.25},
                "error_code": rng.choice([None, "429"]),
            }
        )
    state = get(port, "/state")[2]
    events = asyncio.run(_drain(port))
    for blob in (state, events):
        text = json.dumps(blob)
        leaked = set(_keys(blob)) & (set(PRIVATE_KEYS) - {"jev"})  # `jev` stays as a null for readers
        assert not leaked, leaked
        assert not any(str(n) in text for n in SECRET_NUMBERS)
        assert "quick_sale" not in text and "denied" not in text and "cash_floor" not in text
    assert all(d["jev"] is None for d in state["decisions"])
    assert not any(d["kind"].startswith("accept") and d["status"] != "approved" for d in state["decisions"])
