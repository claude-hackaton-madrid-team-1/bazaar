import asyncio
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tui"))

try:
    import websockets
except ImportError:
    websockets = None

FILES = {
    "index.html": "<!doctype html><title>agent</title>",
    "index.txt": "0:rsc-agent",
    "404.html": "<!doctype html><title>lost</title>",
    "negotiations/index.html": "<!doctype html><title>negotiations</title>",
    "_next/static/chunks/a.js": "console.log('chunk')",
}


def fake_export(base):
    root = Path(base) / "out"
    for name, body in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)
    (Path(base) / "serve.py").write_text("SECRET = 1\n")
    return root


def fetch(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


@unittest.skipIf(websockets is None, "websockets not installed")
class ServeTest(unittest.TestCase):
    def serving(self, check, settle=0.0):
        from serve import start

        async def run():
            with tempfile.TemporaryDirectory() as base:
                server, hub = await start(port=0, seed=3, speed=0.005, root=fake_export(base))
                port = server.sockets[0].getsockname()[1]
                try:
                    await asyncio.sleep(settle)
                    await check(port)
                finally:
                    hub.stop()
                    server.close()
                    await server.wait_closed()

        asyncio.run(run())

    def get(self, port, path):
        return asyncio.to_thread(fetch, port, path)

    def test_serves_the_root_index_with_or_without_a_query(self):
        async def check(port):
            for path in ("/", "/?ws=x", "/?ws=wss://example.org/events"):
                status, headers, body = await self.get(port, path)
                self.assertEqual(status, 200, path)
                self.assertIn(b"<!doctype html><title>agent", body.lower(), path)
                self.assertIn("text/html", headers["Content-Type"])
                self.assertEqual(headers["Cache-Control"], "no-store")

        self.serving(check)

    def test_serves_the_nested_index_with_and_without_the_trailing_slash(self):
        async def check(port):
            for path in ("/negotiations/", "/negotiations", "/negotiations/?id=t1"):
                status, _, body = await self.get(port, path)
                self.assertEqual(status, 200, path)
                self.assertIn(b"<title>negotiations", body, path)

        self.serving(check)

    def test_content_types_for_scripts_and_rsc_payloads(self):
        async def check(port):
            status, headers, body = await self.get(port, "/_next/static/chunks/a.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", headers["Content-Type"])
            self.assertEqual(body, b"console.log('chunk')")
            status, headers, body = await self.get(port, "/index.txt?_rsc=abc")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertEqual(body, b"0:rsc-agent")

        self.serving(check)

    def test_unknown_path_serves_the_404_page(self):
        async def check(port):
            for path in ("/nope/", "/nope", "/_next/static/missing.js"):
                status, headers, body = await self.get(port, path)
                self.assertEqual(status, 404, path)
                self.assertIn(b"<title>lost", body, path)
                self.assertIn("text/html", headers["Content-Type"])

        self.serving(check)

    def test_path_traversal_is_refused(self):
        async def check(port):
            for path in ("/../serve.py", "/%2e%2e/serve.py", "/%2E%2E%2Fserve.py", "/..%2fserve.py"):
                status, _, body = await self.get(port, path)
                self.assertEqual(status, 404, path)
                self.assertNotIn(b"SECRET", body, path)

        self.serving(check)

    def test_a_late_client_gets_hello_first_and_me_early(self):
        from websockets.asyncio.client import connect

        async def check(port):
            async with connect(f"ws://127.0.0.1:{port}/events", max_queue=None) as ws:
                first = [json.loads(await ws.recv()) for _ in range(5)]
            self.assertEqual(first[0]["type"], "agent.hello")
            self.assertIn("agent.me", [e["type"] for e in first])

        self.serving(check, settle=0.4)


class MissingExportTest(unittest.TestCase):
    def test_hint_names_the_build_command_when_the_export_is_missing(self):
        if websockets is None:
            self.skipTest("websockets not installed")
        from serve import missing_hint

        with tempfile.TemporaryDirectory() as base:
            hint = missing_hint(Path(base) / "out")
            self.assertIn("not found", hint)
            self.assertIn("cd web && npm ci && npm run build", hint)
            self.assertIsNone(missing_hint(fake_export(base)))


if __name__ == "__main__":
    unittest.main()
