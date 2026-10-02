import asyncio
import json
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tui"))

try:
    import websockets
except ImportError:
    websockets = None


@unittest.skipIf(websockets is None, "websockets not installed")
class ServeTest(unittest.TestCase):
    def test_serves_the_page_and_streams_hello_first_to_a_late_client(self):
        from serve import start
        from websockets.asyncio.client import connect

        async def run():
            server, hub = await start(port=0, seed=3, speed=0.005)
            port = server.sockets[0].getsockname()[1]
            try:
                await asyncio.sleep(0.4)
                page = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/").read())
                self.assertIn(b"<!doctype html>", page.lower())

                def fetch_js():
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/state.mjs") as r:
                        return r.headers["Content-Type"], r.read()

                content_type, body = await asyncio.to_thread(fetch_js)
                self.assertIn("javascript", content_type)
                self.assertIn(b"export function apply", body)
                with self.assertRaises(urllib.error.HTTPError):
                    await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/../tui/serve.py"))
                async with connect(f"ws://127.0.0.1:{port}/events", max_queue=None) as ws:
                    first = [json.loads(await ws.recv()) for _ in range(5)]
                self.assertEqual(first[0]["type"], "agent.hello")
                self.assertIn("agent.me", [e["type"] for e in first])
            finally:
                hub.stop()
                server.close()
                await server.wait_closed()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
