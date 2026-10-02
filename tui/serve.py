import argparse
import asyncio
import contextlib
import json
import sys
from collections import deque
from functools import partial
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from mock import MockGame

REPO = Path(__file__).resolve().parent.parent
WEB_OUT = REPO / "web" / "out"
STREAM = REPO / ".local" / "stream.jsonl"
BUILD = "cd web && npm ci && npm run build"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ico": "image/x-icon",
    ".png": "image/png",
}
STICKY = ("agent.hello", "agent.me", "clock", "agent.phase")


class StreamFile:
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0

    def step(self):
        try:
            with self.path.open("rb") as f:
                if f.seek(0, 2) < self.offset:
                    self.offset = 0
                f.seek(self.offset)
                chunk = f.read()
        except FileNotFoundError:
            return []
        complete = chunk[: chunk.rfind(b"\n") + 1]
        self.offset += len(complete)
        out = []
        for line in complete.splitlines():
            with contextlib.suppress(ValueError):
                e = json.loads(line)
                if isinstance(e, dict) and isinstance(e.get("type"), str):
                    out.append(e)
        return out


class Hub:
    def __init__(self, game, speed, keep=5000):
        self.game, self.speed = game, speed
        self.backlog = deque(maxlen=keep)
        self.sticky = {}
        self.clients = set()
        self.task = None

    async def handler(self, ws):
        first = list(self.sticky.values())
        sent = {id(e) for e in first}
        for e in first + [e for e in self.backlog if id(e) not in sent]:
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
                    self.sticky.pop(e["type"], None)
                    self.sticky[e["type"]] = e
                broadcast(self.clients, json.dumps(e))
            await asyncio.sleep(self.speed)

    def stop(self):
        if self.task:
            self.task.cancel()


def locate(root, raw_path):
    path = unquote(raw_path.split("?", 1)[0].split("#", 1)[0])
    if "\0" in path:
        return None
    base = root.resolve()
    target = (base / path.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        return None
    if target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


def file_response(status, target):
    body = target.read_bytes()
    headers = Headers(
        {
            "Content-Type": CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        }
    )
    return Response(status, status.phrase, headers, body)


def static(root, connection, request):
    if request.path.split("?", 1)[0] == "/events":
        return None
    target = locate(root, request.path)
    if target is not None:
        return file_response(HTTPStatus.OK, target)
    missing = Path(root) / "404.html"
    if missing.is_file():
        return file_response(HTTPStatus.NOT_FOUND, missing)
    return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")


def missing_hint(root):
    if (Path(root) / "index.html").is_file():
        return None
    try:
        shown = Path(root).resolve().relative_to(REPO)
    except ValueError:
        shown = Path(root)
    return f"{shown} not found — run: {BUILD}"


async def start(source, port=8777, speed=0.5, host="127.0.0.1", root=WEB_OUT):
    hub = Hub(source, speed)
    server = await serve(hub.handler, host, port, process_request=partial(static, Path(root)))
    hub.task = asyncio.create_task(hub.run())
    return server, hub


async def main():
    ap = argparse.ArgumentParser(description="Serve the web view and stream the game on /events")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--stream", type=Path, default=STREAM, help="the file `bazaar monitor` writes every tick")
    ap.add_argument("--mock", action="store_true", help="play a mock game instead of the real one")
    ap.add_argument("--seed", type=int, help="mock only")
    ap.add_argument("--speed", type=float, help="seconds per step (mock 0.35, file poll 0.5)")
    args = ap.parse_args()
    hint = missing_hint(WEB_OUT)
    if hint:
        print(hint, file=sys.stderr)
    if args.mock:
        source, speed, origin = MockGame(seed=args.seed), args.speed or 0.35, "mock game"
    else:
        source, speed, origin = StreamFile(args.stream), args.speed or 0.5, f"{args.stream} (run `uv run bazaar monitor`)"
    server, hub = await start(source, args.port, speed)
    print(f"http://localhost:{args.port}   (events on ws://localhost:{args.port}/events from {origin})")
    await server.serve_forever()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
