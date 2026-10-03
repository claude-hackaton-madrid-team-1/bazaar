"""The simulator's HTTP API: the routes of https://bazaar.causaprima.ai that the SDK and our agents use.

Every refusal is `{"error": "<code>", "message": "<why>"}` with a 4xx, like the real server; an
unknown route is 404 `not_found`; a body that is not the right shape is 422 (`{"detail": [...]}`).
Simulator-only routes live under `/sim/`: `POST /sim/reset` and `POST /sim/tick` need
`X-Admin-Token` (= `SIM_ADMIN_TOKEN`), `GET /sim/state` is a read-only summary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bazaar_sim import broker, duels, market, scoring, threads, views
from bazaar_sim.auth import Gate, admin_ok, looks_like_broker_key, looks_like_team_key
from bazaar_sim.errors import SimError, invalid, not_found
from bazaar_sim.models import Venue, WorldState
from bazaar_sim.store import MemoryStore, Store
from bazaar_sim.validate import parse_body
from bazaar_sim.world import SimConfig, World

log = logging.getLogger("bazaar_sim")
STREAMS_PER_KEY = 6
STREAMS_TOTAL = 400
INDEX_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bazaar simulator</title></head>
<body style="font-family:system-ui;background:#0B1020;color:#E6E9F2;padding:2rem">
<h1>The Bazaar · simulator</h1><p>A simulated market for testing agents. Not the real game.</p>
<p>API: <code>/api/clock</code>, <code>/api/catalog</code>, <code>/api/me</code>
(header <code>X-Team-Key: sim-team1</code>).</p>
</body></html>"""


@dataclass
class Sim:
    """What the routes share: the world (replaced on reset), its store, the throttles."""

    world: World
    store: Store
    gate: Gate
    admin_token: str | None
    keepalive_s: float = 15.0
    streams: dict[str, int] = field(default_factory=dict)
    on_reset: Callable[[World], None] | None = None

    generation: int = 0  # bumped by every reset: a save from before a reset never lands after it
    saved: tuple[int, int] = (-1, -1)  # (generation, sequence) of the last snapshot written
    sequence: int = 0
    save_lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> tuple[str, int, tuple[int, int]]:
        with self.world.lock:
            self.sequence += 1
            return self.world.state.model_dump_json(), self.world.tick, (self.generation, self.sequence)

    def persist(self) -> None:
        data, tick, stamp = self.snapshot()
        with self.save_lock:
            if stamp <= self.saved:
                return  # a newer snapshot (or a reset) was written meanwhile
            try:
                self.store.save(data, tick)
                self.saved = stamp
            except Exception as e:  # a failed save must never stop the clock
                log.warning("snapshot save failed at tick %s: %s", tick, type(e).__name__)

    def reset(self, seed: int | None) -> World:
        """A new world in the SAME `World` object, under its lock: a request either finishes on the old
        state before the reset or runs on the new one, never on a discarded copy."""
        world = self.world
        with world.lock:
            config = world.config if seed is None else SimConfig(**{**world.config.__dict__, "seed": seed})
            fresh = World.create(config, world.now)
            world.state, world.config, world.keys = fresh.state, fresh.config, fresh.keys
            world.stream_only = []
            self.generation += 1
        self.persist()
        if self.on_reset is not None:
            self.on_reset(world)
        return world


def load_world(store: Store, config: SimConfig) -> World:
    raw = store.load()
    if raw:
        try:
            world = World(WorldState.model_validate_json(raw), config)
            world.resume_after_restart()
            return world
        except ValueError as e:
            log.warning("stored world unreadable (%s): starting a new one", type(e).__name__)
    return World.create(config)


def client_address(request: Request) -> str:
    """The address the throttles count. A client-sent header is never trusted: only the one header a
    trusted proxy sets, named by SIM_CLIENT_IP_HEADER (Railway's edge sets `X-Real-IP`), else the peer."""
    header = os.environ.get("SIM_CLIENT_IP_HEADER", "").strip().lower()
    if header:
        value = request.headers.get(header)
        if value:
            return value.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "unknown"


def create_app(sim: Sim, *, run_clock: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_clock_loop(sim)) if run_clock else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await asyncio.to_thread(sim.persist)

    app = FastAPI(title="The Bazaar · simulator", version="0.1-sim", lifespan=lifespan)
    app.state.sim = sim
    _errors(app)
    _public_routes(app, sim)
    _team_routes(app, sim)
    _broker_routes(app, sim)
    _sim_routes(app, sim)
    _stream_route(app, sim)
    _fallback_routes(app)
    return app


async def _clock_loop(sim: Sim) -> None:
    """Advance the shared world on its clock. A tick or a save that fails is logged and the loop goes on: the tick
    counter moves first, so a failure is not retried in a hot loop, and /api/health never says ok over a dead clock."""
    while True:
        world = sim.world
        await asyncio.sleep(max(0.05, min(0.5, world.next_tick_in())))
        try:
            if world.advance_if_due():
                await asyncio.to_thread(sim.persist)
        except Exception:  # noqa: BLE001 - one bad tick must never stop the simulator's clock (#178 review)
            log.exception("sim clock: a tick or its save failed; the clock keeps going")


def _errors(app: FastAPI) -> None:
    @app.exception_handler(SimError)
    async def sim_error(_: Request, e: SimError) -> JSONResponse:
        return JSONResponse(e.body(), status_code=e.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if e.status_code == 404 else "method_not_allowed" if e.status_code == 405 else "invalid"
        return JSONResponse(
            {"error": code, "message": f"{request.method} {request.url.path}"}, status_code=e.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
        detail = [
            {"loc": list(err.get("loc", [])), "msg": str(err.get("msg")), "type": str(err.get("type"))}
            for err in e.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)


# ---------------------------------------------------------------- auth helpers


def _team(sim: Sim, request: Request) -> str:
    key = request.headers.get("x-team-key")
    address = client_address(request)
    team = sim.world.team_for_key(key) if key and looks_like_team_key(key) else None
    if team is None:
        sim.gate.failed(address)
        raise SimError("bad_key", "unknown team key (header X-Team-Key)", 401)
    sim.gate.rate(f"team:{team}", address)
    return team


def _broker(sim: Sim, request: Request) -> Venue:
    key = request.headers.get("x-broker-key")
    address = client_address(request)
    venue = broker.venue_for_broker(sim.world, key) if key and looks_like_broker_key(key) else None
    if venue is None:
        sim.gate.failed(address)
        raise SimError("bad_key", "unknown broker key (header X-Broker-Key)", 401)
    sim.gate.rate(f"broker:{venue.venue}", address)
    return venue


def _public(sim: Sim, request: Request) -> None:
    sim.gate.rate(None, client_address(request))


def _admin(sim: Sim, request: Request) -> None:
    if not admin_ok(sim.admin_token, request.headers.get("x-admin-token")):
        sim.gate.failed(client_address(request))
        raise SimError("bad_token", "admin token required (header X-Admin-Token)", 401)


async def _body(request: Request) -> dict[str, Any]:
    return parse_body(await request.body())


# ---------------------------------------------------------------- routes


def _public_routes(app: FastAPI, sim: Sim) -> None:
    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.health_view(sim.world)

    @app.get("/api/clock")
    async def clock(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.clock_view(sim.world)

    @app.get("/api/catalog")
    async def catalog_route(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.catalog_view(sim.world)

    @app.get("/api/leaderboard")
    async def leaderboard(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return scoring.leaderboard_view(sim.world)

    @app.get("/api/feed")
    async def feed(request: Request, limit: int = 150) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.feed_view(sim.world, limit)

    @app.get("/api/schedule")
    async def schedule(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.schedule_view(sim.world)

    @app.get("/api/dealers")
    @app.get("/api/personas")
    async def dealers(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.dealers_view(sim.world)

    @app.get("/api/dealers/{pid}")
    @app.get("/api/personas/{pid}")
    async def dealer(request: Request, pid: str) -> dict[str, Any]:
        _public(sim, request)
        from bazaar_sim.catalog import raw_dealers

        if pid not in raw_dealers():
            raise not_found(f"dealer {pid}")
        with sim.world.lock:
            return views.dealer_view(sim.world, pid)

    @app.get("/api/levels")
    async def levels(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.levels_view(sim.world)

    @app.get("/api/venues")
    async def venues(request: Request) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            return views.venues_view(sim.world)

    @app.get("/api/venues/{vid}/offers")
    async def board(request: Request, vid: str) -> dict[str, Any]:
        _public(sim, request)
        with sim.world.lock:
            if vid not in sim.world.state.venues:
                raise not_found(f"venue {vid}")
            return views.board_view(sim.world, vid)


def _team_routes(app: FastAPI, sim: Sim) -> None:
    @app.get("/api/me")
    async def me(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return scoring.me_view(sim.world, team)

    @app.get("/api/me/value")
    async def my_value(request: Request, card: str) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return scoring.value_view(sim.world, team, card)

    @app.get("/api/me/threads")
    async def my_threads(request: Request, status: str | None = None) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            w = sim.world
            rows = [t for t in w.state.threads.values() if team in (t.team, t.with_) and status in (None, t.status)]
            return {"threads": [views.thread_view(w, t) for t in sorted(rows, key=lambda t: t.id)]}

    @app.get("/api/me/offers")
    async def my_offers(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return market.my_offers(sim.world, team)

    @app.get("/api/cards/{asset_id}")
    async def card(request: Request, asset_id: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return views.card_view(sim.world, sim.world.asset(asset_id), team)

    @app.get("/api/threads/{tid}")
    async def thread(request: Request, tid: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return views.thread_view(sim.world, threads.participant_thread(sim.world, team, tid))

    @app.post("/api/threads")
    async def open_thread(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return views.thread_view(sim.world, threads.open_thread(sim.world, team, body))

    @app.post("/api/threads/{tid}/messages")
    async def say(request: Request, tid: int) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return threads.say(sim.world, team, tid, body)

    @app.post("/api/threads/{tid}/close")
    async def close_thread(request: Request, tid: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return threads.close_thread(sim.world, team, tid)

    @app.post("/api/offers")
    async def new_offer(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return views.offer_view(sim.world, market.offer_from_input(sim.world, team, body))

    @app.delete("/api/offers/{oid}")
    async def cancel(request: Request, oid: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return market.cancel(sim.world, team, oid)

    @app.post("/api/offers/{oid}/accept")
    async def accept(request: Request, oid: int) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return market.accept(sim.world, team, oid, body)

    @app.post("/api/packs/{aid}/open")
    async def open_pack(request: Request, aid: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return market.open_pack(sim.world, team, aid)

    @app.post("/api/flags")
    async def flag(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        message_id = body.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            raise invalid("message_id must be a message id")
        with sim.world.lock:
            sim.world.emit("flag.raised", {"team": team, "message": message_id}, actor=team)
        return {"ok": True, "message_id": message_id}

    @app.post("/api/venues")
    async def open_venue(request: Request) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return broker.open_venue(sim.world, team, body)

    @app.patch("/api/venues/{vid}")
    async def set_fee(request: Request, vid: str) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return broker.set_fee(sim.world, team, vid, body)

    @app.post("/api/venues/{vid}/close")
    async def close_venue(request: Request, vid: str) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return broker.close_venue(sim.world, team, vid)

    @app.get("/api/duels")
    async def my_duels(request: Request, done: bool = False) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return duels.duels_view(sim.world, team, done)

    @app.post("/api/duels/{did}/messages")
    async def duel_say(request: Request, did: int) -> dict[str, Any]:
        team = _team(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return duels.say(sim.world, team, did, body)

    @app.post("/api/duels/{did}/accept")
    async def duel_accept(request: Request, did: int) -> dict[str, Any]:
        team = _team(sim, request)
        with sim.world.lock:
            return duels.accept(sim.world, team, did)


def _broker_routes(app: FastAPI, sim: Sim) -> None:
    @app.get("/api/broker/book")
    async def book(request: Request) -> dict[str, Any]:
        venue = _broker(sim, request)
        with sim.world.lock:
            return broker.book(sim.world, venue)

    @app.post("/api/broker/matches")
    async def match(request: Request) -> dict[str, Any]:
        venue = _broker(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return broker.match(sim.world, venue, body)

    @app.post("/api/broker/announce")
    async def announce(request: Request) -> dict[str, Any]:
        venue = _broker(sim, request)
        body = await _body(request)
        with sim.world.lock:
            return broker.announce(sim.world, venue, body)


def _sim_routes(app: FastAPI, sim: Sim) -> None:
    @app.post("/sim/reset")
    async def reset(request: Request) -> dict[str, Any]:
        _admin(sim, request)
        body = await _body(request)
        seed = body.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise invalid("seed must be an integer")
        world = await asyncio.to_thread(sim.reset, seed)
        return {"ok": True, "tick": world.tick, "seed": world.state.seed, "teams": len(world.state.teams)}

    @app.post("/sim/tick")
    async def tick(request: Request) -> dict[str, Any]:
        _admin(sim, request)
        sim.world.advance()
        await asyncio.to_thread(sim.persist)
        return {"ok": True, "tick": sim.world.tick}

    @app.get("/sim/state")
    async def state(request: Request) -> Response:
        _public(sim, request)
        if request.query_params.get("full") in ("1", "true"):
            _admin(sim, request)
            data, _, _ = sim.snapshot()
            return Response(data, media_type="application/json")
        with sim.world.lock:
            return JSONResponse(_summary(sim))


def _summary(sim: Sim) -> dict[str, Any]:
    w = sim.world
    teams = [
        {
            "team": t.id,
            "name": t.name,
            "bot": t.bot,
            "cash": t.cash,
            "cards": sum(w.held_counts(t.id).values()),
            "level": len(t.unlocked),
            "deals": len(t.deals),
        }
        for t in w.state.teams.values()
    ]
    return {
        "simulator": True,
        "tick": w.tick,
        "tick_seconds": w.state.clock.tick_seconds,
        "seed": w.state.seed,
        "store": sim.store.describe(),
        "teams": teams,
        "open_threads": sum(1 for t in w.state.threads.values() if t.status == "open"),
        "open_offers": sum(1 for o in w.state.offers.values() if o.status == "open"),
        "live_duels": sum(1 for d in w.state.duels.values() if d.status == "live"),
        "events": len(w.state.events),
        "player_keys": [f"sim-team{n}" for n in range(1, w.config.player_teams + 1)],
    }


def _stream_route(app: FastAPI, sim: Sim) -> None:
    @app.get("/api/events/stream")
    async def stream(request: Request, scope: str = "public") -> Response:
        key = request.headers.get("x-team-key")
        team = _team(sim, request) if key else None
        if not key:
            _public(sim, request)
        holder = f"team:{team}" if team else f"addr:{client_address(request)}"
        if sim.streams.get(holder, 0) >= STREAMS_PER_KEY:
            raise SimError("too_many_streams", f"{STREAMS_PER_KEY} open streams per key", 429)
        if sum(sim.streams.values()) >= STREAMS_TOTAL:
            raise SimError("too_many_streams", "the server holds as many streams as it can: poll /api/feed", 503)
        label = f"team:{team}" if team and scope == "team" else "public"
        sim.streams[holder] = sim.streams.get(holder, 0) + 1
        return StreamingResponse(
            _events(sim, request, holder, label),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


async def _events(sim: Sim, request: Request, holder: str, label: str) -> AsyncIterator[str]:
    try:
        world, generation = sim.world, sim.generation
        with world.lock:
            last_id, tick = world.state.counters.get("event", 0), world.tick
        yield f"event: hello\ndata: {json.dumps({'tick': tick, 'scope': label})}\n\n"
        loop = asyncio.get_running_loop()
        last_keepalive = loop.time()
        while not await request.is_disconnected():
            if sim.generation != generation:
                return  # the world was reset: the client reconnects to the new one
            with world.lock:
                fresh = _since(world, last_id, label)
            for event in fresh:
                last_id = max(last_id, event["id"])
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if loop.time() - last_keepalive >= sim.keepalive_s:  # like the real stream: about every 15 s
                last_keepalive = loop.time()
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)
    finally:
        sim.streams[holder] = max(0, sim.streams.get(holder, 1) - 1)


def _since(world: World, last_id: int, label: str) -> list[dict[str, Any]]:
    out = []
    for source in (world.state.events, world.stream_only):
        for e in reversed(source):
            if e.id <= last_id:
                break
            if e.scope == "public" or e.scope == label:
                out.append(e.model_dump())
    return sorted(out, key=lambda e: e["id"])


def _fallback_routes(app: FastAPI) -> None:
    @app.api_route("/api/admin/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def organiser_admin(rest: str) -> JSONResponse:
        return JSONResponse(
            {"error": "bad_token", "message": "admin token required (header X-Admin-Token)"}, status_code=401
        )

    @app.get("/{path:path}", response_model=None)
    async def spa(path: str) -> Response:
        if path.startswith("api/") or path.startswith("sim/"):
            raise not_found(f"route /{path}")
        return HTMLResponse(INDEX_HTML)


def server_config(app: FastAPI, host: str, port: int) -> Any:
    """uvicorn as the simulator runs everywhere (CLI and tests). `proxy_headers=False`: uvicorn must
    not rewrite the client address from a client-sent X-Forwarded-For (see `client_address`)."""
    import uvicorn

    return uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, proxy_headers=False)


def build(config: SimConfig | None = None, store: Store | None = None) -> tuple[FastAPI, Sim]:
    """The app as `bazaar-sim serve` runs it: config and admin token from the environment."""
    config = config or SimConfig.from_env()
    store = store or MemoryStore()
    world = load_world(store, config)
    rate = float(os.environ.get("SIM_RATE_PER_S") or 5.0)
    burst = float(os.environ.get("SIM_RATE_BURST") or 20.0)
    sim = Sim(
        world=world,
        store=store,
        gate=Gate.from_rates(rate, burst),
        admin_token=os.environ.get("SIM_ADMIN_TOKEN") or None,
        keepalive_s=float(os.environ.get("SIM_KEEPALIVE_S") or 15.0),
    )
    sim.persist()
    return create_app(sim), sim
