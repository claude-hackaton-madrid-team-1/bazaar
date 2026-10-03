"""Read-only status of one agent (taker or maker) over HTTP and WebSocket, beside its tick loop.

    GET /health  {ok, agent, mode: dry|live, target: {mode: real|simulator, url}, ledger, tick, last_tick_at}
                 ledger: shared | down (a live agent sends nothing until it answers) | local file
    GET /state   mode, tick, our open offers (maker) or dealer threads (taker), the last 50 decisions
    WS  /events  every decision and execution as it happens; a client joining late first gets the last 200

Events use the web view's envelope (spec 003 on feat/web-live): `{id, tick, t, type, scope, actor,
payload}` with negative made-up ids, plus an `agent` field; types `agent.decision`, `agent.execution`
and `agent.tick`. Read-only: nothing here can trade, change a parameter, or reveal a key, URL or
password (every string goes through the telemetry scrubber). CORS is open and there is no token (a
browser page reads it, so a token would ship in its JS): the data itself must be public.

So every decision, execution and view part goes through an allow-list before the hub keeps it: what the
agent DID (kind, card, venue, counterparty, the price it sent, status, guardrail and Jev labels), never
why in numbers (our card value, max price, bid ladder, surplus, score, cash, limits, and the reason and
console line that spell them out). A field nobody listed below stays private. The full decision still
goes to the `decisions` table and Phoenix.

The server runs on its own thread and event loop. The tick loop only appends to a bounded buffer under
a lock and schedules the broadcast on the server's loop, so a slow or stuck client never blocks a tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from bazaar_agent.decisions import scrubbed

REPLAY = 200
LAST_DECISIONS = 50
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

# The public view, as allow-lists: a field added to a decision later stays private until it is listed here.
DECISION_FIELDS = frozenset({"decision_id", "tick", "kind", "chosen", "status", "dry_run", "sent", "thread_id"})
INPUT_FIELDS = frozenset(  # the card, where, with whom, and the counterparty's public price
    {"dealer", "thread", "item", "ref", "card", "rarity", "side", "venue", "offer_id", "maker"}
    | {"ask", "her_ask", "fee", "final"}
)
UNSENT_FIELDS = frozenset({"tick", "kind", "status"})  # a row not sent: no counterparty, offer id or price
UNSENT_INPUT_FIELDS = frozenset({"item", "ref", "card", "venue", "side"})
INPUT_GROUPS = ("offer", "listing")  # maker_jev's states nest the card they are about one level down
SENT_PRICE = "price"  # our own price is public once posted: shown only on an approved row of a live agent
MOVE_FIELDS = frozenset(
    {"kind", "price", "accept", "open_thread", "topic", "cancel", "hold", "reprice", "give", "want", "venue"}
)
REQUEST_FIELDS = frozenset({"offer", "thread", "with", "topic", "price", "give", "want", "venue"})
VIEW_FIELDS: dict[str, frozenset[str] | None] = {  # None: a list of plain values (card refs)
    "threads": frozenset({"dealer", "thread", "item", "ticks", "opened_tick", "accepted_price"}),
    "open_offers": frozenset({"id", "side", "ref", "price", "venue", "expires_tick", "created_tick"}),
    "posted_this_tick": None,
}
SCALAR = (str, int, float, bool, type(None))
# Nested values (a topic, a give/want side) keep only keys that name a card, a pack or our cash: a probe once
# published topic {"max": 26, "value": 56.1}. Anything else, at any depth, is dropped.
NESTED_KEYS = frozenset(
    {"cash", "card", "cards", "ref", "pack", "buy", "sell", "item", "kind", "assets", "types", "id", "rarity"}
)
NESTED_DEPTH = 3


def _clean(value: object, depth: int = NESTED_DEPTH) -> tuple[bool, Any]:
    """(keep, value): a scalar, a list of scalars, or a dict cut down to NESTED_KEYS; else dropped."""
    if isinstance(value, SCALAR):
        return True, value
    if isinstance(value, list | tuple) and depth > 0:
        return True, [v for ok, v in (_clean(x, depth - 1) for x in value) if ok]
    if isinstance(value, dict) and depth > 0:
        kept = {k: v for k, (ok, v) in ((k, _clean(x, depth - 1)) for k, x in value.items()) if ok and k in NESTED_KEYS}
        return True, kept
    return False, None


def _pick(source: object, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in source.items():
        keep, clean = _clean(value) if key in fields else (False, None)
        if keep:
            out[key] = clean
    return out


def _is_sent(row: dict[str, Any]) -> bool:
    """Only a chosen, approved row of a live agent went to the game. A maker reprice row is approved but not
    chosen (its price is the strategy's target, not a posted price), and a missing flag counts as unsent."""
    return row.get("status") == "approved" and row.get("chosen") is True and row.get("dry_run") is False


def publishable(row: dict[str, Any]) -> bool:
    """Only a sent row is published. The existence, kind and status of an unsent one (a skipped accept, a
    rejected bid, an expired post) say which limit or quota bound us, so those never leave the process.
    A `hold_*` row sends nothing, so it is not published either, and neither is a bad-faith `flag` row (it
    is decided before its send, and a refused or retried flag must never read as sent)."""
    kind = str(row.get("kind") or "")
    return _is_sent(row) and not kind.startswith("hold") and kind != "flag"


def _guardrail(verdict: object, sent: bool) -> str:
    """`allowed` on a sent row, else `-`: a denial (or its absence) tells a rival which limit we hit."""
    return "allowed" if sent and str(verdict or "") == "allowed" else "-"


def public_decision(row: dict[str, Any]) -> dict[str, Any]:
    """What /state and /events show of one decision: what the agent did, never its private numbers.
    A row that was not sent (rejected, skipped, expired, or any dry-run row) shows only the card, where and
    its status: a rival who lists a card and sees our `skip ... accept quota` or `would accept` row for its
    offer and price would learn that its ask sat below our value. A missing `dry_run` counts as a dry run."""
    sent = _is_sent(row)
    fields = INPUT_FIELDS | {SENT_PRICE} if sent else UNSENT_INPUT_FIELDS
    raw = row.get("inputs")
    inputs: dict[str, Any] = {}
    for group in (raw, *(raw.get(g) for g in INPUT_GROUPS)) if isinstance(raw, dict) else ():
        inputs.update({k: v for k, v in _pick(group, fields).items() if isinstance(v, SCALAR)})
    return {
        **_pick(row, DECISION_FIELDS if sent else UNSENT_FIELDS),
        "guardrail": _guardrail(row.get("guardrail"), sent),
        "jev": None,  # its label beside a listed price marks our walk-away price; the key stays for readers
        "inputs": inputs,
        "move": _pick(row.get("move"), MOVE_FIELDS) if sent else {},
    }


def public_execution(row: dict[str, Any]) -> dict[str, Any]:
    """One request we sent: the request (public once sent) and how it ended, not the game's answer body."""
    response = row.get("response")
    created = response.get("id") if isinstance(response, dict) else None
    return {
        "decision_id": row.get("decision_id"),
        "tick": row.get("tick"),
        "method": row.get("method"),
        "request": _pick(row.get("request"), REQUEST_FIELDS),
        "ok": row.get("error_code") is None,
        "error_code": None if row.get("error_code") is None else "refused",  # the game's code names our cash/quota
        "created_id": created if isinstance(created, int) else None,
    }


def public_view(parts: dict[str, Any]) -> dict[str, Any]:
    """The live view (taker threads, maker offers) without our bids, max or value; unknown parts dropped."""
    out: dict[str, Any] = {}
    for name, fields in VIEW_FIELDS.items():
        items = parts.get(name)
        if not isinstance(items, list | tuple):
            continue
        out[name] = [v for v in items if isinstance(v, SCALAR)] if fields is None else [_pick(v, fields) for v in items]
    return out


class StatusHub:
    """What the server shows. Written by the tick loop (any thread), read by the server thread."""

    def __init__(
        self,
        agent: str,
        live: bool,
        wall: Callable[[], float] = time.time,
        target: dict[str, str] | None = None,
        ledger: Callable[[], str] | None = None,
    ) -> None:
        self.agent, self.mode, self._wall = agent, "live" if live else "dry", wall
        self.target = dict(target or {})  # {"mode": "real" | "simulator", "url": ...}: where its requests go
        self._ledger = ledger  # ledger_pg.ledger_health: shared | down | local file (flags only, no network)
        self._lock = threading.Lock()
        self._events: deque[str] = deque(maxlen=REPLAY)
        self._decisions: deque[dict[str, Any]] = deque(maxlen=LAST_DECISIONS)
        self._ids = itertools.count(1)
        self._tick: int | None = None
        self._t: float | None = None
        self._us: str | None = None
        self._last_tick_at: str | None = None
        self._view: dict[str, Any] = {}
        self._doors: dict[str, Any] = {}
        self._clients: set[ServerConnection] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ written by the tick loop

    def tick(self, tick: int, t_hours: float, us: str | None) -> None:
        with self._lock:
            self._tick, self._t, self._us = tick, t_hours, us or self._us
            self._last_tick_at = datetime.fromtimestamp(self._wall(), UTC).isoformat(timespec="seconds")
        self._publish("agent.tick", {"mode": self.mode})

    def clock(self, payload: dict[str, Any]) -> None:
        """Every clock read, live or not: /health says when the doors are closed and when they open."""
        with self._lock:
            self._doors = {k: payload.get(k) for k in ("doors", "paused", "next_opens", "tick_seconds")}
            self._doors["server_tick"] = payload.get("tick")

    def view(self, **parts: Any) -> None:
        """The agent's live view: `threads=[...]` (taker) or `open_offers=[...]` (maker), allow-listed."""
        clean = scrubbed(public_view(parts))
        with self._lock:
            self._view.update(clean if isinstance(clean, dict) else {})

    def decision(self, row: dict[str, Any]) -> None:
        if not publishable(row):
            return
        payload = self._publish("agent.decision", public_decision(row))
        with self._lock:
            self._decisions.append(payload)

    def execution(self, row: dict[str, Any]) -> None:
        self._publish("agent.execution", public_execution(row))

    def _publish(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The one way out to /state and /events: `payload` is already a public view."""
        clean = scrubbed({**payload, "agent": self.agent})
        body = clean if isinstance(clean, dict) else {}
        with self._lock:
            envelope = {
                "id": -next(self._ids),
                "tick": self._tick,
                "t": self._t,
                "type": kind,
                "scope": "team",
                "actor": self._us,
                "agent": self.agent,
                "payload": body,
            }
            text = json.dumps(envelope, default=str, ensure_ascii=False)
            self._events.append(text)
            loop, clients = self._loop, set(self._clients)
        if loop is not None and clients:
            # RuntimeError: the server loop is closing; the event stays in the replay buffer
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(broadcast, clients, text)
        return body

    # ------------------------------------------------------------ read by the server

    def health(self) -> dict[str, Any]:
        ledger = {"ledger": self._ledger()} if self._ledger is not None else {}
        with self._lock:
            return {
                "ok": True,
                "agent": self.agent,
                "mode": self.mode,
                "target": self.target,
                **ledger,
                "tick": self._tick,
                "last_tick_at": self._last_tick_at,
                **self._doors,
            }

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "agent": self.agent,
                "mode": self.mode,
                "tick": self._tick,
                "t_hours": self._t,
                "team": self._us,
                "last_tick_at": self._last_tick_at,
                **self._view,
                "decisions": list(self._decisions),
            }

    def replay(self) -> list[str]:
        with self._lock:
            return list(self._events)

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    def join(self, ws: ServerConnection) -> None:
        with self._lock:
            self._clients.add(ws)

    def leave(self, ws: ServerConnection) -> None:
        with self._lock:
            self._clients.discard(ws)


def _json(status: HTTPStatus, body: object) -> Response:
    data = json.dumps(body, default=str, ensure_ascii=False).encode()
    headers = Headers(
        {"Content-Type": "application/json", "Content-Length": str(len(data)), "Cache-Control": "no-store", **CORS}
    )
    return Response(status.value, status.phrase, headers, data)


def routes(hub: StatusHub) -> Callable[[ServerConnection, Request], Response | None]:
    def route(connection: ServerConnection, request: Request) -> Response | None:
        path = request.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/events":
            return None  # the WebSocket handshake
        if path in ("/", "/health"):
            return _json(HTTPStatus.OK, hub.health())
        if path == "/state":
            return _json(HTTPStatus.OK, hub.state())
        return _json(HTTPStatus.NOT_FOUND, {"error": "not_found", "paths": ["/health", "/state", "/events"]})

    return route


async def _serve(hub: StatusHub, host: str, port: int, ready: threading.Event, bound: list[int]) -> None:
    async def handler(ws: ServerConnection) -> None:
        for text in hub.replay():
            await ws.send(text)
        hub.join(ws)
        try:
            await ws.wait_closed()
        finally:
            hub.leave(ws)

    async with serve(handler, host, port, process_request=routes(hub)) as server:
        hub.attach(asyncio.get_running_loop())
        bound.append(int(next(iter(server.sockets)).getsockname()[1]))
        ready.set()
        await asyncio.Future()  # until the process exits (daemon thread)


def start_status_server(hub: StatusHub, host: str, port: int, timeout_s: float = 5.0) -> int:
    """Serve `hub` on host:port from a daemon thread; returns the bound port (0 asks the OS for one)."""
    ready = threading.Event()
    bound: list[int] = []
    thread = threading.Thread(
        target=lambda: asyncio.run(_serve(hub, host, port, ready, bound)), name=f"{hub.agent}-status", daemon=True
    )
    thread.start()
    if not ready.wait(timeout_s) or not bound:
        raise RuntimeError(f"status server for {hub.agent} did not start on {host}:{port}")
    return bound[0]
