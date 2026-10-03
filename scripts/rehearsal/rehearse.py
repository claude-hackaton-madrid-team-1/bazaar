"""Night B5 dress rehearsal: taker + maker (with its venue keeper and broker) + duel player, all LIVE, together,
against a LOCAL bazaar_sim on a free port. Never the real game, never Railway: children get an allow-listed
environment, an empty env file, dead proxies, and scripts/sim_guard/sitecustomize.py (loopback-only sockets).
The shared ledger, decisions and venue vault go to a throwaway local Postgres (or JSONL with --no-db).

    docker run -d --rm --name b5-rehearsal-pg -p "[::1]:55491:5432" -e POSTGRES_USER=b5 \
        -e POSTGRES_PASSWORD="$REHEARSAL_PG_PASSWORD" -e POSTGRES_DB=b5sim_a pgvector/pgvector:pg16
    uv run python scripts/rehearsal/rehearse.py --name A --tick-seconds 4 --ticks 300 --db b5sim_a \
        --venue-open-after 0.06 --sim-env SIM_CHATO_OPEN_TICKS=40 --sim-env SIM_BENCH_FIRST_TICK=70 ...
    uv run python scripts/rehearsal/analyze.py A b5sim_a

Runs and their logs go to .local/rehearsal/run-<name>/ (git-ignored), or REHEARSAL_DIR.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(os.environ.get("REHEARSAL_DIR") or Path(__file__).resolve().parents[2] / ".local" / "rehearsal")
REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "sim_guard"
KEY = "sim-team1"
DEAD_PROXY = "http://127.0.0.1:9"
INHERITED = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR")


def free_port() -> int:
    with socket.socket(socket.AF_INET6) as s:  # ::1: tonight 127.0.0.1's ephemeral ports are exhausted (TIME_WAIT)
        s.bind(("::1", 0))
        return int(s.getsockname()[1])


def base_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in INHERITED or k.startswith("LC_")}
    env.update(
        {
            "PYTHONPATH": str(GUARD),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": DEAD_PROXY,
            "HTTPS_PROXY": DEAD_PROXY,
            "http_proxy": DEAD_PROXY,
            "https_proxy": DEAD_PROXY,
            "NO_PROXY": "127.0.0.1,localhost,::1,[::1]",
            "no_proxy": "127.0.0.1,localhost,::1,[::1]",
        }
    )
    return env


def get(url: str, path: str, keyed: bool = False) -> dict:
    req = urllib.request.Request(url + path, headers={"X-Team-Key": KEY} if keyed else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--tick-seconds", type=float, default=4.0)
    ap.add_argument("--ticks", type=int, default=240)
    ap.add_argument("--db", default="", help="database on the throwaway Postgres; empty: JSONL only")
    ap.add_argument("--pg-port", type=int, default=55491)
    ap.add_argument("--venue-open-after", type=float, default=0.06, help="this run's venue_open_after_game_hours")
    ap.add_argument("--sim-env", action="append", default=[], help="KEY=VALUE for the simulator")
    ap.add_argument("--agent-env", action="append", default=[], help="KEY=VALUE for every agent")
    ap.add_argument("--rule", action="append", default=[], help="id=value: this run's GUARDRAILS.md copy only")
    ap.add_argument("--sample-every", type=int, default=3, help="ticks between /api/me samples")
    args = ap.parse_args()

    run = HERE / f"run-{args.name}"
    run.mkdir(parents=True, exist_ok=True)
    data = run / "data"
    data.mkdir(exist_ok=True)
    empty = run / "empty.env"
    empty.write_text("")
    rules = (REPO / "GUARDRAILS.md").read_text(encoding="utf-8")
    rules, n = re.subn(
        r"^- `venue_open_after_game_hours` = [0-9.]+",
        f"- `venue_open_after_game_hours` = {args.venue_open_after}",
        rules,
        flags=re.M,
    )
    assert n == 1, "venue_open_after_game_hours not found"
    for kv in args.rule:
        rid, value = kv.split("=", 1)
        rules, n = re.subn(rf"^- `{rid}` = \S+", f"- `{rid}` = {value}", rules, flags=re.M)
        assert n == 1, f"{rid} not found"
    (run / "GUARDRAILS.md").write_text(rules, encoding="utf-8")

    port = free_port()
    url = f"http://[::1]:{port}"
    sim_env = base_env()
    sim_env.update(
        {
            "SIM_TICK_SECONDS": str(args.tick_seconds),
            "SIM_DATABASE_URL": "memory",
            "SIM_ADMIN_TOKEN": secrets.token_urlsafe(24),
        }
    )
    for kv in args.sim_env:
        k, v = kv.split("=", 1)
        sim_env[k] = v
    sim_log = (run / "sim.log").open("w")
    sim = subprocess.Popen(
        [sys.executable, "-m", "bazaar_sim", "serve", "--host", "::1", "--port", str(port)],
        env=sim_env,
        stdout=sim_log,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(120):
            try:
                if get(url, "/api/health").get("ok"):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("simulator did not start")

        db_url = ""
        if args.db:
            pw = os.environ["REHEARSAL_PG_PASSWORD"]  # the throwaway container's, never a real one
            db_url = f"postgresql://b5:{pw}@[::1]:{args.pg_port}/{args.db}"
        agent_env = base_env()
        agent_env.update(
            {
                "BAZAAR_ENV_FILE": str(empty),
                "BAZAAR_SIM": "local",
                "BAZAAR_SIM_KEY": KEY,
                "BAZAAR_TEAM_ID": "t01",
                "BAZAAR_DATA_DIR": str(data),
                "BAZAAR_TRACING": "0",
                "COLUMNS": "220",
                "REHEARSAL_SIM_URL": url,
                "REHEARSAL_GUARDRAILS": str(run / "GUARDRAILS.md"),
                # a dead address when no DB: the ledgers fall back to JSONL, nothing else is reached
                "DATABASE_URL": db_url or "postgresql://nobody@127.0.0.1:9/none",
                "BAZAAR_SIM_DATABASE_URL": db_url or "postgresql://nobody@127.0.0.1:9/none",
            }
        )
        for kv in args.agent_env:
            k, v = kv.split("=", 1)
            agent_env[k] = v
        wrapper = [sys.executable, str(Path(__file__).resolve().parent / "run_agent.py")]
        if args.db:
            init = subprocess.run([*wrapper, "db", "init"], env=agent_env, capture_output=True, text=True, timeout=180)
            (run / "db-init.log").write_text(init.stdout + init.stderr)
            if init.returncode != 0:
                raise SystemExit(f"db init failed:\n{init.stdout}{init.stderr}")

        n = str(args.ticks)
        cmds = {
            "taker": ["agent", "taker", "--live", "--no-jev", "--max-ticks", n],
            "maker": ["agent", "maker", "--live", "--no-jev", "--max-ticks", n],
            "duels": ["duel", "run", "--play", "--no-jev", "--max-ticks", n],
        }
        procs = {}
        for name, cmd in cmds.items():
            out = (run / f"{name}.log").open("w")
            procs[name] = subprocess.Popen([*wrapper, *cmd], env=agent_env, stdout=out, stderr=subprocess.STDOUT)

        samples = run / "me.jsonl"
        stop = threading.Event()

        def sample() -> None:
            last = -1
            with samples.open("w") as f:
                while not stop.is_set():
                    try:
                        clock = get(url, "/api/clock")
                        if clock["tick"] - last >= args.sample_every:
                            me = get(url, "/api/me", keyed=True)
                            last = clock["tick"]
                            f.write(
                                json.dumps({"tick": clock["tick"], "t_hours": clock.get("t_hours"), "me": me}) + "\n"
                            )
                            f.flush()
                    except Exception as e:  # sampling is best effort
                        f.write(json.dumps({"error": repr(e)}) + "\n")
                    stop.wait(args.tick_seconds / 2)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        started = time.monotonic()
        deadline = started + args.ticks * args.tick_seconds * 1.5 + 120
        while any(p.poll() is None for p in procs.values()) and time.monotonic() < deadline:
            time.sleep(2)
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        stop.set()
        sampler.join(timeout=10)
        final = {
            "clock": get(url, "/api/clock"),
            "me": get(url, "/api/me", keyed=True),
            "duels_done": get(url, "/api/duels?done=true", keyed=True),
            "offers": get(url, "/api/me/offers", keyed=True),
            "venues": get(url, "/api/venues"),
            "sim_state": get(url, "/sim/state"),
            "exit": {name: p.returncode for name, p in procs.items()},
            "wall_s": round(time.monotonic() - started),
        }
        (run / "final.json").write_text(json.dumps(final, indent=1))
        print(json.dumps({k: final[k] for k in ("exit", "wall_s")}), flush=True)
    finally:
        sim.terminate()
        try:
            sim.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sim.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
