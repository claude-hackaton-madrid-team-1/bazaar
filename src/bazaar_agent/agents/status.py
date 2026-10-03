"""Read-only status of one agent (taker or maker) over HTTP and WebSocket, beside its tick loop.

    GET /health  {ok, agent, mode: dry|live, target: {mode: real|simulator, url}, tick, last_tick_at}
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
# Our venue's broker sees a private book (pseudonyms, the Market Test's bench): its rows show only that a match
# or an opening happened and how it ended, never an offer id, a quote, a maker or a price.
PRIVATE_KINDS = frozenset({"broker_match", "venue_open"})
PRIVATE_METHODS = frozenset({"broker_match", "open_venue"})
VIEW_FIELDS: dict[str, frozenset[str] | None] = {  # None: a list of plain values (card refs)
    "threads": frozenset({"dealer", "thread", "item", "ticks", "opened_tick", "accepted_price"}),
    "open_offers": frozenset({"id", "side", "ref", "price", "venue", "expires_tick", "created_tick"}),
    "posted_this_tick": None,
}
SCALAR = (str, int, float, bool, type(None))


def _pick(source: object, fields: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in source.items() if k in fields} if isinstance(source, dict) else {}


def _guardrail(verdict: object) -> str:
    """`allowed`, `denied` or `-`: the rule text names our cash and limits."""
    text = str(verdict or "")
    return "allowed" if text == "allowed" else "denied" if text.startswith("denied") else "-"


def public_decision(row: dict[str, Any]) -> dict[str, Any]:
    """What /state and /events show of one decision: what the agent did, never its private numbers.
    A row that was not sent (rejected, skipped, expired, or any dry-run row) shows only the card, where and
    its status: a rival who lists a card and sees our `skip ... accept quota` or `would accept` row for its
    offer and price would learn that its ask sat below our value. A missing `dry_run` counts as a dry run."""
    sent = row.get("status") == "approved" and row.get("dry_run") is False
    if row.get("kind") in PRIVATE_KINDS:
        return {
            **_pick(row, DECISION_FIELDS if sent else UNSENT_FIELDS),
            "guardrail": _guardrail(row.get("guardrail")),
            "jev": None,
            "inputs": {},
            "move": {},
        }
    fields = INPUT_FIELDS | {SENT_PRICE} if sent else UNSENT_INPUT_FIELDS
    raw = row.get("inputs")
    inputs: dict[str, Any] = {}
    for group in (raw, *(raw.get(g) for g in INPUT_GROUPS)) if isinstance(raw, dict) else ():
        inputs.update({k: v for k, v in _pick(group, fields).items() if isinstance(v, SCALAR)})
    jev = row.get("jev")
    return {
        **_pick(row, DECISION_FIELDS if sent else UNSENT_FIELDS),
        "guardrail": _guardrail(row.get("guardrail")),
        "jev": {"verdict": jev.get("verdict")} if isinstance(jev, dict) and sent else None,
        "inputs": inputs,
        "move": _pick(row.get("move"), MOVE_FIELDS) if sent else {},
    }


def public_execution(row: dict[str, Any]) -> dict[str, Any]:
    """One request we sent: the request (public once sent) and how it ended, not the game's answer body."""
    response = row.get("response")
    created = response.get("id") if isinstance(response, dict) else None
    private = row.get("method") in PRIVATE_METHODS
    return {
        "decision_id": row.get("decision_id"),
        "tick": row.get("tick"),
        "method": row.get("method"),
        "request": {} if private else _pick(row.get("request"), REQUEST_FIELDS),
        "ok": row.get("error_code") is None,
        "error_code": row.get("error_code"),
        "created_id": created if isinstance(created, int) and not private else None,
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
        self, agent: str, live: bool, wall: Callable[[], float] = time.time, target: dict[str, str] | None = None
    ) -> None:
        self.agent, self.mode, self._wall = agent, "live" if live else "dry", wall
        self.target = dict(target or {})  # {"mode": "real" | "simulator", "url": ...}: where its requests go
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
        with self._lock:
            return {
                "ok": True,
                "agent": self.agent,
                "mode": self.mode,
                "target": self.target,
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
