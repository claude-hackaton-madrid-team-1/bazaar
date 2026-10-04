"""Optional manual end-to-end diagnostic on a local simulator; not a CI or merge gate.

    uv run python scripts/sim_smoke.py

It starts `bazaar-sim serve` on 127.0.0.1:8765 (BAZAAR_SIM=local's address; BAZAAR_SIM_PORT moves both) with an
in-memory world, then drives our real CLI against it: status, one dealer buy with negotiation,
two ticks of the taker and of the maker in --live, duel moves, the monitor's live stream, and the
two key guards. No secrets, no database, no network beyond localhost: the repo's `.env` is never read
(BAZAAR_ENV_FILE points at an empty file), children inherit only an allow-listed environment, and
`scripts/sim_guard/sitecustomize.py` (first on every child's PYTHONPATH) raises on any non-loopback
connect or DNS lookup, with a dead proxy behind it. A call to the real game (or to any API) fails the
smoke instead of happening. A step also fails on a `Traceback`, a swallowed `tick loop:` error, or a
` refused ` line (a write the simulator refused, or the socket guard's own refusal).
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
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

KEY = "sim-team1"
STEP_TIMEOUT_S = 180
# BAZAAR_SIM=local's address (src/bazaar_agent/config.py LOCAL_SIM_URL); BAZAAR_SIM_PORT moves it (and this smoke)
# to another loopback port when several workers' simulators share one laptop.
PORT = int(os.environ.get("BAZAAR_SIM_PORT") or 8765)
SIM = f"http://127.0.0.1:{PORT}"
# Duel budget: the session opens at tick 2 and lasts SIM_DUEL_TICKS ticks of 2 s, and the duel step plays it to
# its deadline. duel_policy v2 holds while the sim's rival concedes and accepts at D - 3 / D - 2, so the step
# lasts until then. It starts ~10 s in (tick ~5): 24 ticks (deadline 26) leaves ~40 s for steps added before it,
# and a step that starts too late fails with "raise DUEL_TICKS" instead of a missing move.
DUEL_TICKS = "24"
DUEL_MIN_TICKS_LEFT = 5  # v2 needs D - 3 and D - 2 for the session's two accepts, plus a tick to start
CRASH_MARKERS = ("Traceback (most recent call last)", "tick loop:", " refused ", "SmokeNetworkError")
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens there: any non-local request fails at once
NOWHERE_DB = "postgresql://smoke:smoke@127.0.0.1:9/bazaar_sim_smoke"  # unreachable: ledgers fall back to JSONL
GUARD = Path(__file__).resolve().parent / "sim_guard"  # sitecustomize: loopback-only sockets in every child
# An allow-list, not a deny-list: a child process inherits only these, so no token in the caller's
# environment (MCP, Phoenix, GitHub, Railway, ...) can reach the smoke whatever its name.
INHERITED = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "VIRTUAL_ENV")
INHERITED_PREFIXES = ("LC_",)  # not UV_*: the children run the venv's python directly, and UV_* can hold tokens


def inherited() -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k in INHERITED or k.startswith(INHERITED_PREFIXES)}
    return {**keep, "PYTHONPATH": str(GUARD), "PYTHONDONTWRITEBYTECODE": "1"}


def base_env(data_dir: Path, env_file: Path) -> dict[str, str]:
    env = inherited()
    env.update(
        {
            "BAZAAR_ENV_FILE": str(env_file),  # an empty file: a laptop's real .env never loads
            "BAZAAR_SIM": "local",
            "BAZAAR_SIM_PORT": str(PORT),
            "BAZAAR_SIM_KEY": KEY,
            "BAZAAR_TEAM_ID": "t01",
            "BAZAAR_DATA_DIR": str(data_dir),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
            "BAZAAR_TRACING": os.environ.get("SMOKE_TRACING", "0"),  # N18: run it with 1 to prove tracing moves nothing
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
    env = inherited()
    env.update(
        {
            "SIM_TICK_SECONDS": "2",
            "SIM_DATABASE_URL": "memory",
            "SIM_ADMIN_TOKEN": secrets.token_urlsafe(24),
            "SIM_DUEL_FIRST_TICK": "2",
            "SIM_DUEL_TICKS": DUEL_TICKS,
            "PORT": str(PORT),
        }
    )
    return env


def get(path: str, keyed: bool = False) -> dict:
    req = urllib.request.Request(SIM + path, headers={"X-Team-Key": KEY} if keyed else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost, never a proxy
    with opener.open(req, timeout=10) as resp:
        data: dict = json.loads(resp.read())
    return data


def port_busy() -> bool:
    """Anything that answers, even with an HTTP error (404, a 429), holds the port: only a refused connect is free."""
    try:
        get("/api/health")
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def wait_for_sim(server: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            fail(f"bazaar-sim exited with code {server.returncode} before answering", log.read_text(errors="replace"))
        try:
            if get("/api/health").get("ok"):
                time.sleep(0.5)  # ours, not another simulator that won the port: ours must still be running
                if server.poll() is not None:
                    fail(f"bazaar-sim exited with code {server.returncode}: 127.0.0.1:{PORT} answers for someone else")
                return
        except OSError:
            pass
        time.sleep(0.5)
    fail("the simulator did not answer /api/health within 60 s", log.read_text(errors="replace"))


def fail(why: str, output: str = "") -> NoReturn:
    print(f"\nSMOKE FAILED: {why}\n{output[-4000:]}", flush=True)
    raise SystemExit(1)


def bazaar(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "bazaar_agent.cli", *args]
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=STEP_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        streams = [s.decode(errors="replace") if isinstance(s, bytes) else (s or "") for s in (e.stdout, e.stderr)]
        fail(f"`bazaar {' '.join(args)}` ran past {STEP_TIMEOUT_S} s", "".join(streams))


def step(name: str, run: Callable[[], subprocess.CompletedProcess[str]], check: Callable[[str], bool]) -> str:
    started = time.monotonic()
    result = run()
    output = result.stdout + result.stderr
    crashed = next((m for m in CRASH_MARKERS if m in output), None)
    if crashed:
        fail(f"{name}: {crashed!r} in its output (an error the tick loop swallowed)", output)
    if result.returncode != 0 or not check(output):
        fail(f"{name} (exit {result.returncode})", output)
    print(f"ok  {name:<44} {time.monotonic() - started:5.1f} s", flush=True)
    return output


def missing_common(max_price: int) -> str:
    """A common we miss whose official value (`GET /api/me/value`) covers `max_price`: every buy is capped at it,
    so a card of a set we care little about would walk at the dealer's floor. One read per released set."""
    me, catalog = get("/api/me", keyed=True), get("/api/catalog")
    held = {a["ref"] for a in me["assets"] if a["kind"] == "card"}
    firsts = [
        next((str(c["id"]) for c in s["cards"] if c["rarity"] == "common" and c["id"] not in held), None)
        for s in catalog["sets"]
        if s.get("released")
    ]
    valued = [(float(get(f"/api/me/value?card={ref}", keyed=True)["your_value"]), ref) for ref in firsts if ref]
    best = max(valued, default=None)
    if best is None:
        fail("team t01 already holds every common")
    if best[0] < max_price:
        fail(f"no missing common worth {max_price} to team t01 (best {best[1]} at {best[0]:g})")
    return best[1]


def duel_ticks_left() -> tuple[list[int], int]:
    """The live session's duels and the ticks left before its earliest deadline (fails when too few remain)."""
    live = get("/api/duels", keyed=True)["duels"]
    if not live:
        fail("no live duel when the duel step starts: raise DUEL_TICKS")
    deadline, tick = min(d["deadline_tick"] for d in live), get("/api/clock")["tick"]
    if deadline - tick < DUEL_MIN_TICKS_LEFT:
        fail(
            f"the duel session ends at tick {deadline}, {deadline - tick} tick(s) after the duel step: raise DUEL_TICKS"
        )
    return [d["duel"] for d in live], deadline - tick


def closed_duels(ids: list[int]) -> list[dict]:
    """Our duels in `ids` once none is live any more (an accept settles at the next tick: wait up to 3 ticks)."""
    deadline = time.monotonic() + 6
    while True:
        duels = [d for d in get("/api/duels?done=true", keyed=True)["duels"] if d["duel"] in ids]
        if all(d["status"] != "live" for d in duels) or time.monotonic() > deadline:
            return duels
        time.sleep(0.5)


def check_duel_deals(ids: list[int], output: str) -> None:
    """Every duel of the session closed as a deal strictly inside our limit. A deal needs a move of ours (our accept,
    or our priced offer the rival took), so this also proves duel run --play sent one, under either duel_policy."""
    duels = closed_duels(ids)
    if len(duels) != len(ids):
        fail(f"duel run --play: {len(ids)} duel(s) in the session, {len(duels)} found after it", output)
    for d in duels:
        gain = (d.get("result") or {}).get("your_gain")
        if d["status"] != "deal" or not isinstance(gain, int | float) or gain <= 0:
            fail(
                f"duel run --play: duel {d['duel']} ended {d['status']} (gain {gain}), not a deal inside our limit",
                output,
            )
        ours = sum(1 for m in d["messages"] if m["from"] == "you")
        print(
            f"ok  duel {d['duel']} {d['role']} limit {d['your_limit']}: deal at {d['price']}, gain {gain:g}, "
            f"rounds {d['rounds']}, {ours} priced message(s) of ours",
            flush=True,
        )


def run_smoke(env: dict[str, str]) -> None:
    out = step(
        "status (target is the simulator)", lambda: bazaar(env, "status", "--no-cards"), lambda o: "SIMULATOR" in o
    )
    if "Team 1" not in out:
        fail("status did not show Team 1", out)
    ref = missing_common(10)
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
    step(  # N17: the team desk's plan over HTTP, read-only (the desk itself is off: team_threads_enabled)
        "swaps --json (team-thread plan, read-only)",
        lambda: bazaar(env, "swaps", "--json"),
        lambda o: o.lstrip().startswith("["),  # stdout first: pure JSON, notes on stderr
    )
    ids, left = duel_ticks_left()
    duel_out = step(
        f"duel run --play, {left} ticks to the deadline",
        lambda: bazaar(env, "duel", "run", "--play", "--max-ticks", str(left)),
        lambda o: True,
    )
    check_duel_deals(ids, duel_out)
    step(
        "monitor with the live SSE stream, one tick",
        lambda: bazaar(env, "monitor", "--no-db", "--max-ticks", "1"),
        lambda o: "stream live (scope team:t01" in o,  # the stream's hello, not the startup banner
    )
    real_key = {**env, "BAZAAR_SIM_KEY": "tk-real-0042"}
    refused = bazaar(real_key, "status")
    if refused.returncode == 0 or "only a simulator key" not in refused.stdout + refused.stderr:
        fail("a real-looking key was not refused against the simulator", refused.stdout + refused.stderr)
    stale = bazaar({**env, "BAZAAR_URL": "https://bazaar.causaprima.ai"}, "clock")
    if stale.returncode != 2 or "BAZAAR_URL is no longer read" not in stale.stdout + stale.stderr:
        fail("BAZAAR_URL did not fail fast", stale.stdout + stale.stderr)
    print("ok  key guard and BAZAAR_URL fail-fast", flush=True)


def dump_requests() -> None:
    """SMOKE_DUMP=<file>: what the simulator recorded as OUR team's sent requests, for an on/off tracing diff.
    Ids, ticks and timestamps are dropped (the world's clock runs on wall time); types, prices and words stay."""
    target = os.environ.get("SMOKE_DUMP")
    if not target:
        return
    events = get("/api/feed?limit=500", keyed=True).get("events") or []
    keep = [{"type": e.get("type"), "payload": e.get("payload")} for e in events if isinstance(e, dict)]
    Path(target).write_text(json.dumps(keep, indent=1, sort_keys=True, default=str))


def main() -> int:
    started = time.monotonic()
    if port_busy():
        fail(f"something already answers on 127.0.0.1:{PORT} (a bazaar-sim serve, or the MCP server): stop it first")
    with tempfile.TemporaryDirectory(prefix="sim-smoke-") as tmp:
        log = Path(tmp) / "sim.log"
        empty_env = Path(tmp) / "empty.env"
        empty_env.write_text("")
        with log.open("w") as sink:
            server = subprocess.Popen(
                [sys.executable, "-m", "bazaar_sim", "serve", "--port", str(PORT)],
                env=sim_env(),
                stdout=sink,
                stderr=subprocess.STDOUT,
            )
        try:
            wait_for_sim(server, log)
            print(f"simulator up: tick {get('/api/clock')['tick']}", flush=True)
            run_smoke(base_env(Path(tmp) / "client", empty_env))
            dump_requests()
            if server.poll() is not None:
                fail(
                    f"bazaar-sim exited with code {server.returncode} during the smoke", log.read_text(errors="replace")
                )
        except SystemExit:
            print("\n--- simulator log ---\n" + log.read_text(errors="replace")[-3000:], flush=True)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    print(f"\nSMOKE PASSED in {time.monotonic() - started:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
