"""End-to-end smoke on a local simulator: the merge gate every PR passes (`.github/workflows/sim-smoke.yml`).

    uv run python scripts/sim_smoke.py

It starts `bazaar-sim serve` on 127.0.0.1:8765 (BAZAAR_SIM=local's hardcoded address) with an
in-memory world, then drives our real CLI against it: status, one dealer buy with negotiation,
two ticks of the taker and of the maker in --live, duel moves, the monitor's live stream, and the
two key guards. No secrets, no database, no network beyond localhost: every other host goes through
a dead proxy, so a call to the real game (or to any API) fails the smoke instead of happening.
Exit 0 when every step passes; 1 on the first failure, with the step's output and the sim's log.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

SIM = "http://127.0.0.1:8765"
KEY = "sim-team1"
STEP_TIMEOUT_S = 180
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens there: any non-local request fails at once
NOWHERE_DB = "postgresql://smoke:smoke@127.0.0.1:9/bazaar_sim_smoke"  # unreachable: ledgers fall back to JSONL
SECRETS = (
    "BAZAAR_URL",
    "BAZAAR_KEY",
    "TYPESAFE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def base_env(data_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    env.update(
        {
            "BAZAAR_SIM": "local",
            "BAZAAR_SIM_KEY": KEY,
            "BAZAAR_TEAM_ID": "t01",
            "BAZAAR_DATA_DIR": str(data_dir),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
            "BAZAAR_TRACING": "0",
            "COLUMNS": "200",
            "HTTP_PROXY": DEAD_PROXY,
            "HTTPS_PROXY": DEAD_PROXY,
            "http_proxy": DEAD_PROXY,
            "https_proxy": DEAD_PROXY,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    return env


def sim_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    env.update(
        {
            "SIM_TICK_SECONDS": "2",
            "SIM_DATABASE_URL": "memory",
            "SIM_ADMIN_TOKEN": secrets.token_urlsafe(24),
            "SIM_DUEL_FIRST_TICK": "2",
            "SIM_DUEL_TICKS": "20",
            "PORT": "8765",
        }
    )
    return env


def get(path: str, keyed: bool = False) -> dict:
    req = urllib.request.Request(SIM + path, headers={"X-Team-Key": KEY} if keyed else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost, never a proxy
    with opener.open(req, timeout=10) as resp:
        data: dict = json.loads(resp.read())
    return data


def wait_for_sim(log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if get("/api/health").get("ok"):
                return
        except OSError:
            pass
        time.sleep(0.5)
    fail("the simulator did not answer /api/health within 60 s", log.read_text(errors="replace"))


def fail(why: str, output: str = "") -> None:
    print(f"\nSMOKE FAILED: {why}\n{output[-4000:]}", flush=True)
    raise SystemExit(1)


def bazaar(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "bazaar_agent.cli", *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=STEP_TIMEOUT_S, check=False)


def step(name: str, run: Callable[[], subprocess.CompletedProcess[str]], check: Callable[[str], bool]) -> str:
    started = time.monotonic()
    result = run()
    output = result.stdout + result.stderr
    if result.returncode != 0 or not check(output):
        fail(f"{name} (exit {result.returncode})", output)
    print(f"ok  {name:<44} {time.monotonic() - started:5.1f} s", flush=True)
    return output


def missing_common() -> str:
    me, catalog = get("/api/me", keyed=True), get("/api/catalog")
    held = {a["ref"] for a in me["assets"] if a["kind"] == "card"}
    for s in catalog["sets"]:
        for c in s["cards"]:
            if s.get("released") and c["rarity"] == "common" and c["id"] not in held:
                return str(c["id"])
    fail("team t01 already holds every common")
    return ""


def our_duel_moves() -> int:
    duels = get("/api/duels?done=true", keyed=True)["duels"]
    return sum(1 for d in duels for m in d["messages"] if m["from"] == "you")


def run_smoke(env: dict[str, str]) -> None:
    out = step(
        "status (target is the simulator)", lambda: bazaar(env, "status", "--no-cards"), lambda o: "SIMULATOR" in o
    )
    assert "Team 1" in out or "t01" in out
    ref = missing_common()
    step(
        f"dealer buy {ref} from Abuela, negotiated",
        lambda: bazaar(env, "dealer", "buy", ref, "--start", "6", "--max", "10", "--live"),
        lambda o: any(line.startswith("deal thread") for line in o.splitlines()),
    )
    # Two ticks each: a run that starts late in a tick drops that tick's moves (tick discipline).
    step(
        "agent taker --live, two ticks",
        lambda: bazaar(env, "agent", "taker", "--live", "--no-jev", "--max-ticks", "2"),
        lambda o: "target: SIMULATOR" in o and o.count("accept candidate(s)") == 2 and "· LIVE" in o,
    )
    step(
        "agent maker --live, two ticks",
        lambda: bazaar(env, "agent", "maker", "--live", "--no-jev", "--max-ticks", "2"),
        lambda o: "target: SIMULATOR" in o and o.count("action(s)") == 2 and "· LIVE" in o,
    )
    step(
        "duel run --play, three ticks", lambda: bazaar(env, "duel", "run", "--play", "--max-ticks", "3"), lambda o: True
    )
    if our_duel_moves() < 1:
        fail("duel run --play sent no duel move")
    print("ok  the simulator holds our duel moves", flush=True)
    step(
        "monitor with the live SSE stream, one tick",
        lambda: bazaar(env, "monitor", "--no-db", "--max-ticks", "1"),
        lambda o: "stream on" in o,
    )
    real_key = {**env, "BAZAAR_SIM_KEY": "tk-real-0042"}
    refused = bazaar(real_key, "status")
    if refused.returncode == 0 or "only a simulator key" not in refused.stdout + refused.stderr:
        fail("a real-looking key was not refused against the simulator", refused.stdout + refused.stderr)
    stale = bazaar({**env, "BAZAAR_URL": "https://bazaar.causaprima.ai"}, "clock")
    if stale.returncode != 2 or "BAZAAR_URL is no longer read" not in stale.stdout + stale.stderr:
        fail("BAZAAR_URL did not fail fast", stale.stdout + stale.stderr)
    print("ok  key guard and BAZAAR_URL fail-fast", flush=True)


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sim-smoke-") as tmp:
        log = Path(tmp) / "sim.log"
        with log.open("w") as sink:
            server = subprocess.Popen(
                [sys.executable, "-m", "bazaar_sim", "serve", "--port", "8765"],
                env=sim_env(),
                stdout=sink,
                stderr=subprocess.STDOUT,
            )
        try:
            wait_for_sim(log)
            print(f"simulator up: tick {get('/api/clock')['tick']}", flush=True)
            run_smoke(base_env(Path(tmp) / "client"))
        except SystemExit:
            print("\n--- simulator log ---\n" + log.read_text(errors="replace")[-3000:], flush=True)
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\nSMOKE PASSED in {time.monotonic() - started:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
