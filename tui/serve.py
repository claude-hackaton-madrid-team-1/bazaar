import argparse
import asyncio
import json
import sys
from collections import deque
from http import HTTPStatus
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from mock import MockGame

WEB = Path(__file__).resolve().parent.parent / "web"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".mjs": "text/javascript; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".svg": "image/svg+xml"}
STICKY = ("agent.hello", "agent.me", "clock", "agent.phase")


class Hub:
    def __init__(self, game, speed, keep=500):
        self.game, self.speed = game, speed
        self.backlog = deque(maxlen=keep)
        self.sticky = {}
        self.clients = set()
        self.task = None

    async def handler(self, ws):
        kept = {e["id"] for e in self.backlog}
        replay = sorted((e for e in self.sticky.values() if e["id"] not in kept), key=lambda e: e["id"])
        for e in replay + list(self.backlog):
            await ws.send(json.dumps(e))
        self.clients.add(ws)
        try:
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    async def run(self):
        while True:
            for e in self.game.step():
                self.backlog.append(e)
                if e["type"] in STICKY:
                    self.sticky[e["type"]] = e
                broadcast(self.clients, json.dumps(e))
            await asyncio.sleep(self.speed)

    def stop(self):
        if self.task:
            self.task.cancel()


def static(connection, request):
    path = request.path.split("?", 1)[0]
    if path == "/events":
        return None
    target = (WEB / (path.lstrip("/") or "index.html")).resolve()
    if not target.is_file() or WEB.resolve() not in target.parents:
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
    body = target.read_bytes()
    headers = Headers({"Content-Type": CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
                       "Content-Length": str(len(body)), "Cache-Control": "no-store"})
    return Response(HTTPStatus.OK, "OK", headers, body)


async def start(port=8777, seed=None, speed=0.35, host="127.0.0.1"):
    hub = Hub(MockGame(seed=seed), speed)
    server = await serve(hub.handler, host, port, process_request=static)
    hub.task = asyncio.create_task(hub.run())
    return server, hub


async def main():
    ap = argparse.ArgumentParser(description="Serve the web view and stream a mock Bazaar game on /events")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--speed", type=float, default=0.35, help="seconds per mock step")
    args = ap.parse_args()
    server, hub = await start(args.port, args.seed, args.speed)
    print(f"http://localhost:{args.port}   (events on ws://localhost:{args.port}/events)")
    await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
