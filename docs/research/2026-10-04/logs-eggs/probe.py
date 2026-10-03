"""Keyless GET probe of the public Bazaar surface (sat-logs-eggs research).

Scope (RULES.md "Fair play" and "Limits every request meets"): GET only, NO key header, one request at a time,
>= 1.1 s apart (well under 60 req/s per address). Never /api/admin/* (organiser-only), never a write.
Every request is appended to requests.log (UTC time, method, path, status, bytes, sha256-12, content-type).
Bodies go to OUT (scratchpad, not committed). Usage: uv run python probe.py OUTDIR path [path ...]
"""
import hashlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://bazaar.causaprima.ai"
LOG = Path(__file__).with_name("requests.log")
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for path in sys.argv[2:]:
    assert path.startswith("/") and not path.startswith("/api/admin"), path
    req = urllib.request.Request(BASE + path, method="GET", headers={"User-Agent": "t01-readonly-probe"})
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, body, hdrs = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, body, hdrs = e.code, e.read(), e.headers
    except Exception as e:  # noqa: BLE001
        status, body, hdrs = 0, repr(e).encode(), {}
    name = path.strip("/").replace("/", "_").replace("?", "_") or "root"
    (out / f"{name}.body").write_bytes(body)
    (out / f"{name}.headers").write_text("".join(f"{k}: {v}\n" for k, v in (hdrs.items() if hdrs else [])))
    line = (f"{when}\tGET\t{path}\t{status}\t{len(body)}\t{hashlib.sha256(body).hexdigest()[:12]}\t"
            f"{(hdrs.get('content-type') if hdrs else '') or ''}\tkey=none")
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line)
    time.sleep(1.1)
