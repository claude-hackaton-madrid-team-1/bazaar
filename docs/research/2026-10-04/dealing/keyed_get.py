"""One keyed, read-only GET to the game, rate-limited to 1 req/s and logged (method, path, Madrid time).

The key is read from the lets-start .env and never printed. The raw body goes to the given file
(outside the repo: team threads hold other teams' words).

    uv run python docs/research/2026-10-04/dealing/keyed_get.py /api/me/threads OUT.json LOG.txt
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ENV = Path(
    "/Users/mariusserban/orca/workspaces/bazaar/"
    "lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.env"
)
key = next(
    line.split("=", 1)[1].strip().strip("'\"")
    for line in ENV.read_text().splitlines()
    if line.startswith("BAZAAR_KEY=")
)
path, out, log = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
time.sleep(1.0)  # never more than 1 req/s from this helper
r = httpx.get("https://bazaar.causaprima.ai" + path, headers={"X-Team-Key": key}, timeout=20)
stamp = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d %H:%M:%S")
with log.open("a") as f:
    f.write(f"{stamp} GET {path} -> {r.status_code}\n")
out.write_text(
    json.dumps(r.json(), indent=1) if r.headers.get("content-type", "").startswith("application/json") else r.text
)
print(r.status_code, len(r.content))
