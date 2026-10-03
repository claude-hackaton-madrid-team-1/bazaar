"""Run our real taker + maker + duels against the calibrated Sunday scenario of `bazaar-sim` (SIM_SCENARIO=sunday).

    uv run python scripts/sim_sunday.py --port 8981 --ticks 600 --tick-seconds 2     # compressed: 15 s of game per tick
    uv run python scripts/sim_sunday.py --port 8982 --ticks 80 --tick-seconds 15     # the real pace, a sample of it
    uv run python scripts/sim_sunday.py report .local/sim-sunday/<run>               # reprint a finished run's report

The simulator is a fresh in-memory world on a private port (8980-8989; a busy port is refused: never share a
simulator, see .ai/memory.md). The three agents run as sim-team1 under `scripts/tick_profile.py` (which times every
request and tick), so they share ONE key's 5 req/s and its limits, like the real game. Their memory is a throwaway
database on the laptop's docker Postgres (`bazaar_sim_sunday_<port>`, dropped at the end). An explicit
BAZAAR_SIM_DATABASE_URL may select an existing local `bazaar_sim` / `bazaar_sim_*` database, or
`postgresql://nobody@127.0.0.1:1/none` for file fallback; existing databases are never dropped.
SIM_SUNDAY_PG_ADMIN_URL must name loopback `postgres`, without libpq overrides. Nothing here touches the
real game, the shared Postgres or Railway. Jev is off: every child is restricted to loopback connections.

The report prints, per agent: writes sent per tick, ticks with no write and the top reasons, the tick wall time
p50/p95 against its budget, 429s; then the simulator's own count of accepted writes and refusals by code, the
decisions our agents refused by guardrail, the deals and the estimated score. Tick durations at 2 s ticks are
compressed: a 15 s tick leaves far more budget, so read the p95 against the budget column, not the wall clock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import tick_profile  # noqa: E402  (a sibling script: the per-request and per-tick recorder)

PORTS = range(8980, 8990)
KEY = "sim-team1"
TEAM = "t01"
AGENTS = {
    "taker": ["--live", "--no-learn", "--no-llm-read", "--evals-every", "0"],
    "maker": ["--live", "--no-learn", "--evals-every", "0"],
    "duels": ["--play", "--evals-every", "0"],
}
WRITES = ("POST", "DELETE")
LOCAL_ADMIN = "postgresql://bazaar:bazaar@127.0.0.1:5433/postgres"  # the docker-compose Postgres (README)
NOWHERE_DB = "postgresql://nobody@127.0.0.1:1/none"
INHERITED = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "VIRTUAL_ENV")
GUARD = HERE / "sim_guard"  # sitecustomize: loopback-only sockets in every child


def base_env(env_file: Path) -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k in INHERITED or k.startswith("LC_")}
    return {
        **keep,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(GUARD),
        "BAZAAR_ENV_FILE": str(env_file),
        "BAZAAR_SIM": "local",
        "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
        "SIM_DATABASE_URL": "memory",
    }


def local_database_url(url: str, *, admin: bool = False) -> str:
    """Validate before libpq connects; neither URL nor parser errors may expose credentials."""
    import psycopg

    allowed = {"host", "port", "user", "password", "dbname", "sslmode", "connect_timeout"}
    try:
        info = psycopg.conninfo.conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        raise SystemExit("invalid simulator database URL") from None
    name = info.get("dbname", "")
    valid_name = name == "postgres" if admin else name.startswith("bazaar_sim_") or name == "bazaar_sim"
    if url == NOWHERE_DB and not admin:
        valid_name = True
    if (
        set(info) - allowed
        or info.get("host") not in {"127.0.0.1", "::1", "localhost"}
        or not valid_name
        or "railway" in name.lower()
        or any(key.startswith("PG") for key in os.environ)
    ):
        raise SystemExit("refusing unsafe simulator database URL or libpq environment override")
    # Pin localhost to an address: libpq bypasses the Python socket guard.
    info["host"] = "::1" if info["host"] == "::1" else "127.0.0.1"
    return psycopg.conninfo.make_conninfo(**info)


def get(base: str, path: str, keyed: bool = False) -> dict[str, Any]:
    req = urllib.request.Request(base + path, headers={"X-Team-Key": KEY} if keyed else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        data: dict[str, Any] = json.loads(resp.read())
    return data


def port_busy(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def database(port: int, create: bool) -> str:
    """A throwaway database on the laptop's docker Postgres; its URL goes only to our children's environment."""
    import psycopg

    if port not in PORTS:
        raise SystemExit("invalid simulator database port")
    admin = local_database_url(os.environ.get("SIM_SUNDAY_PG_ADMIN_URL", LOCAL_ADMIN), admin=True)
    name = f"bazaar_sim_sunday_{port}"
    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn:
        conn.execute(f'drop database if exists "{name}" with (force)')
        if create:
            conn.execute(f'create database "{name}"')
    return psycopg.conninfo.make_conninfo(admin, dbname=name)


def start_sim(port: int, tick_seconds: float, latency_scale: float, out: Path) -> subprocess.Popen[bytes]:
    env = base_env(out / "empty.env")
    env.update(
        {
            "SIM_SCENARIO": "sunday",
            "SIM_TICK_SECONDS": str(tick_seconds),
            "SIM_LATENCY_SCALE": str(latency_scale),
            "SIM_DATABASE_URL": "memory",
            "SIM_ADMIN_TOKEN": secrets.token_urlsafe(24),
            "PORT": str(port),
        }
    )
    log = (out / "sim.log").open("wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "bazaar_sim", "serve", "--port", str(port)], env=env, stdout=log, stderr=log
    )
    tick_profile._sim_pid_file(port).write_text(str(server.pid), encoding="utf-8")  # `tick_profile run` insists on it
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"bazaar-sim exited ({server.returncode}) before answering: see {out / 'sim.log'}")
        try:
            if get(base, "/api/health").get("ok"):
                return server
        except OSError:
            pass
        time.sleep(0.5)
    server.terminate()
    raise SystemExit("the simulator did not answer /api/health within 60 s")


def agent_env(port: int, db_url: str, data_dir: Path, env_file: Path) -> dict[str, str]:
    db_url = local_database_url(db_url)
    env = base_env(env_file)
    env.update(
        {
            "PYTHONPATH": str(GUARD),
            "BAZAAR_ENV_FILE": str(env_file),
            "BAZAAR_SIM": "local",
            "BAZAAR_SIM_PORT": str(port),
            "BAZAAR_SIM_KEY": KEY,
            "BAZAAR_TEAM_ID": TEAM,
            "BAZAAR_DATA_DIR": str(data_dir),
            "BAZAAR_SIM_DATABASE_URL": db_url,
            "BAZAAR_TRACING": "0",
            "COLUMNS": "200",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return env


def run(args: argparse.Namespace) -> Path:
    if args.port not in PORTS:
        raise SystemExit(f"--port must be one of {PORTS.start}-{PORTS.stop - 1}")
    if port_busy(args.port):
        raise SystemExit(
            f"127.0.0.1:{args.port} is busy: another worker's simulator? pick a free port, never share one"
        )
    out = Path(args.out) if args.out else REPO / ".local" / "sim-sunday" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    env_file = out / "empty.env"
    env_file.write_text("")
    db_url = os.environ.get("BAZAAR_SIM_DATABASE_URL")
    if db_url:
        local_database_url(db_url)
    else:
        db_url = database(args.port, create=True)
    server = start_sim(args.port, args.tick_seconds, args.latency_scale, out)
    base = f"http://127.0.0.1:{args.port}"
    procs: dict[str, subprocess.Popen[bytes]] = {}
    try:
        start_tick = get(base, "/api/clock")["tick"]
        before = get(base, "/api/me", keyed=True)
        for name in args.agents.split(","):
            if name not in AGENTS:
                raise SystemExit(f"unknown agent {name!r} (one of {sorted(AGENTS)})")
            data_dir = out / f"data-{name}"
            data_dir.mkdir(exist_ok=True)
            cmd = [
                sys.executable,
                str(HERE / "tick_profile.py"),
                "run",
                name,
                "--port",
                str(args.port),
                "--out",
                str(out),
                "--",
            ]
            cmd += [*AGENTS[name], "--max-ticks", str(args.ticks), "--no-jev"]
            log = (out / f"{name}.log").open("wb")
            procs[name] = subprocess.Popen(
                cmd, env=agent_env(args.port, db_url, data_dir, env_file), stdout=log, stderr=log
            )
        for name, p in procs.items():
            p.wait()
            print(f"{name} exited {p.returncode}", flush=True)
        final = {
            "start_tick": start_tick,
            "end_tick": get(base, "/api/clock")["tick"],
            "me_before": before,
            "me_after": get(base, "/api/me", keyed=True),
            "activity": get(base, "/sim/activity"),
            "feed": get(base, "/api/feed?limit=500"),
            "args": {k: v for k, v in vars(args).items()},
        }
        final["decisions"] = decision_rows(db_url)
        (out / "final.json").write_text(json.dumps(final, default=str), encoding="utf-8")
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        server.terminate()
        server.wait(timeout=15)
        tick_profile._sim_pid_file(args.port).unlink(missing_ok=True)
        if not args.keep_db and not os.environ.get("BAZAAR_SIM_DATABASE_URL"):
            database(args.port, create=False)
    return out


def decision_rows(db_url: str) -> list[dict[str, Any]]:
    import psycopg

    db_url = local_database_url(db_url)
    try:
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            cur = conn.execute("select tick, status, reason, policy_checks->>'guardrail' as guardrail from decisions")
            return [dict(zip([d.name for d in cur.description or []], row, strict=True)) for row in cur.fetchall()]
    except Exception as e:  # the report works without them
        return [{"error": type(e).__name__}]


# ------------------------------------------------------------------------------------------ report


_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CARD = re.compile(r"[A-Z]{3}-N")


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else 0.0


def sends_per_tick(out: Path, agent: str) -> tuple[list[int], Counter[str]]:
    """Writes the agent sent in each of its ticks (from its recorded requests), and the routes they went to."""
    ticks: dict[int, int] = defaultdict(int)
    seen: set[int] = set()
    routes: Counter[str] = Counter()
    for line in (out / f"{agent}.requests.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["tick"] is None:
            continue
        seen.add(r["tick"])
        if r["method"] in WRITES and r["status"] == "ok":
            ticks[r["tick"]] += 1
            routes[f"{r['method']} {r['path']}"] += 1
    return [ticks.get(t, 0) for t in sorted(seen)], routes


def refusal_reasons(final: dict[str, Any], limit: int = 8) -> list[tuple[str, int]]:
    rows = final.get("decisions") or []
    why: Counter[str] = Counter()
    for d in rows:
        if d.get("status") == "rejected" or str(d.get("guardrail") or "").startswith("denied"):
            denied = str(d.get("guardrail") or "")
            reason = (
                denied.removeprefix("denied:").strip()
                if denied.startswith("denied")
                else f"strategy: {d.get('reason')}"
            )
            why[_CARD.sub("CARD", _NUMBER.sub("N", reason))[:90]] += 1
    return why.most_common(limit)


def report(out: Path) -> None:
    final = json.loads((out / "final.json").read_text())
    summary = tick_profile.report(out)
    ticks = final["end_tick"] - final["start_tick"]
    args = final["args"]
    compression = max(1.0, 15.0 / float(args["tick_seconds"]))
    print(
        f"\nSUNDAY SCENARIO · {ticks} ticks of {args['tick_seconds']} s "
        f"(game clock 15 s/tick, x{compression:.1f} compressed) · agents {args['agents']}"
    )
    for agent, a in summary["agents"].items():
        sends, routes = sends_per_tick(out, agent)
        idle = sum(1 for s in sends if s == 0)
        mean = statistics.fmean(sends) if sends else 0.0
        print(
            f"\n{agent}: {a['ticks']} ticks · wall p50 {a['wall_p50']:.2f} s, p95 {a['wall_p95']:.2f} s "
            f"(budget p50 {a['budget_p50']:.2f} s) · over budget {a['over_budget']} · dropped {a['expired']} "
            f"· 429 {a['rate_limited']}"
        )
        print(
            f"  writes/tick {mean:.2f} · ticks with no write {idle}/{len(sends)} · keyed req/tick {a['keyed_per_tick']}"
        )
        for route, n in routes.most_common(6):
            print(f"    {n:>5} {route}")
    sim = final["activity"]
    sent: Counter[str] = Counter()
    for label, n in sim["sends"].get(TEAM, {}).items():
        sent[label.split(" ", 1)[1]] += n
    print("\nsimulator, accepted writes by sim-team1 (all agents):")
    for label, n in sent.most_common(8):
        print(f"  {n:>5} {label}")
    print("simulator refusals (route + code):")
    for label, n in sorted(sim["refusals"].get(TEAM, {}).items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {n:>5} {label}")
    print("our guardrails and strategy refused (decisions, no request sent):")
    for reason, n in refusal_reasons(final):
        print(f"  {n:>5} {reason}")
    me0, me1 = final["me_before"], final["me_after"]
    print(f"\ncash {me0.get('cash')} -> {me1.get('cash')} · deals {(me1.get('score') or {}).get('deals')}")
    print(f"score {score(me0)} -> {score(me1)}: " + ", ".join(f"{k} {v}" for k, v in score_legs(me1).items()))
    if "key" in summary:
        k = summary["key"]
        print(
            f"key budget (all agents): max {k['max_in_1s']} requests in any 1 s (limit {20 * compression:.0f}) · "
            f"mean {k['mean_per_s']}/s = {k['mean_per_s'] / compression:.2f}/s at the real pace (limit 5/s)"
        )


def score_legs(me: dict[str, Any]) -> dict[str, Any]:
    raw = me.get("score")
    s: dict[str, Any] = raw if isinstance(raw, dict) else {}
    keys = ("ladder_points", "neg_points", "duel_points", "mm_points", "bench_points", "pages_complete")
    return {k: s.get(k) for k in keys}


def score(me: dict[str, Any]) -> Any:
    s = me.get("score")
    return s.get("score") if isinstance(s, dict) else s


def main(argv: list[str]) -> None:
    if len(argv) >= 2 and argv[0] == "report":
        report(Path(argv[1]))
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8981)
    ap.add_argument("--ticks", type=int, default=600, help="ticks each agent runs (the sim starts at tick 0)")
    ap.add_argument("--tick-seconds", type=float, default=2.0, help="real seconds per tick (the game clock keeps 15 s)")
    ap.add_argument("--latency-scale", type=float, default=1.0, help="scale the measured request latency (0: none)")
    ap.add_argument("--agents", default="taker,maker,duels")
    ap.add_argument("--keep-db", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    out = run(args)
    report(out)
    print(f"\nfiles: {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
