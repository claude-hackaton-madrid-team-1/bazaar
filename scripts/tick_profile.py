"""Tick profiler (SP1): run one agent against a LOCAL simulator and log where each tick's time goes.

    uv run python scripts/tick_profile.py sim --port 8915 --tick-seconds 15     # its own simulator, refuses a busy port
    uv run python scripts/tick_profile.py run taker --port 8915 --out .local/sp1 -- --live --max-ticks 40
    uv run python scripts/tick_profile.py run maker --port 8915 --out .local/sp1 -- --live --max-ticks 40
    uv run python scripts/tick_profile.py run duels --port 8915 --out .local/sp1 -- --play --max-ticks 40
    uv run python scripts/tick_profile.py report .local/sp1

SP1_LATENCY_MS=120 adds that much to every simulator request (a real request's cost; the local sim answers
in ~1 ms), and SP1_JEV_LATENCY_MS to every Jev call (a slow Jev API). SP1_GUARDRAILS=<file> reads that rule
book instead of GUARDRAILS.md (a scenario, e.g. Sunday with the venue open and no bond reserve).

Run the agents with BAZAAR_SIM=local, against a simulator `sim` started (it refuses any other), with
BAZAAR_SIM_DATABASE_URL set to a database of its own on this machine (it refuses a remote one or the laptop's
real `bazaar`); tracing is forced off.
`run` patches the process before the CLI starts, so the agent runs unchanged: it only ever talks to
`http://127.0.0.1:<port>` (BAZAAR_SIM=local, with the local address moved to `--port`), and it refuses to
start against anything else. One JSONL row per tick (`<agent>.ticks.jsonl`): wall time against the tick's
budget, keyed and public requests, Jev calls, recall, Postgres statements, the /me source, and the decisions
the loop dropped as `expired`. One row per HTTP request (`<agent>.requests.jsonl`), so `report` can check
the key's 5 req/s across all the agents of one run. Nothing here is imported by the agents.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

AGENTS = {"taker": ["agent", "taker"], "maker": ["agent", "maker"], "duels": ["duel", "run"]}
KEY_RATE_PER_S = 5.0  # RULES.md: 5 requests per second per key (bursts of 20)
_ID = re.compile(r"/\d+")


class _Recorder:
    """Per-tick counters. One agent runs one tick loop, so the row is process-wide: work a tick hands to a
    pool (duel Jev calls) or a timeout thread (Jev's HTTP) counts toward it; outside a tick nothing does."""

    def __init__(self, agent: str, out: Path) -> None:
        self.agent, self.out = agent, out
        self.row: dict[str, Any] | None = None
        self.t0 = 0.0
        self.lock = threading.Lock()
        out.mkdir(parents=True, exist_ok=True)
        self.ticks = (out / f"{agent}.ticks.jsonl").open("a", encoding="utf-8")
        self.requests = (out / f"{agent}.requests.jsonl").open("a", encoding="utf-8")

    def current(self) -> dict[str, Any] | None:
        return self.row

    def add(self, stage: str, seconds: float) -> None:
        row = self.current()
        if row is None:
            return
        with self.lock:
            row["stages"].setdefault(stage, []).append(round(seconds, 4))

    def count(self, name: str, n: int = 1) -> None:
        row = self.current()
        if row is not None:
            with self.lock:
                row[name] = row.get(name, 0) + n

    def request(self, keyed: bool, method: str, path: str, seconds: float, status: str) -> None:
        row = self.current()
        line = {
            "agent": self.agent,
            "t": round(time.time(), 4),
            "keyed": keyed,
            "method": method,
            "path": path,
            "s": round(seconds, 4),
            "status": status,
            "in_tick": row is not None,
            "tick": row["tick"] if row is not None else None,
        }
        with self.lock:
            self.requests.write(json.dumps(line) + "\n")
            self.requests.flush()
            if row is not None:
                row["stages"].setdefault("http_keyed" if keyed else "http_public", []).append(round(seconds, 4))
                if status == "rate_limited":
                    row["rate_limited"] = row.get("rate_limited", 0) + 1

    def begin(self, clock: Any) -> None:
        from bazaar_agent.ticks import action_budget_s

        self.row = {
            "agent": self.agent,
            "tick": clock.tick,
            "tick_seconds": clock.tick_seconds,
            "next_tick_in": round(clock.next_tick_in, 3),
            "budget_s": round(action_budget_s(clock), 3),
            "started": round(time.time(), 4),
            "stages": {},
        }
        self.t0 = time.monotonic()

    def end(self, error: str | None) -> None:
        row = self.current()
        if row is None:
            return
        row["wall_s"] = round(time.monotonic() - self.t0, 4)
        row["error"] = error
        with self.lock:
            self.row = None
            self.ticks.write(json.dumps(row) + "\n")
            self.ticks.flush()


def _timed(rec: _Recorder, stage: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        t0 = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            rec.add(stage, time.monotonic() - t0)

    wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrapper


def _patch_http(rec: _Recorder, base: str, latency_s: float) -> None:
    """Every request to the simulator is timed; `latency_s` is added to each one, so a local run pays what a
    real request costs (the SDK opens a new TLS connection per request: ~25-30 ms from Madrid, more from
    Railway's europe-west4)."""
    import urllib.error
    import urllib.request

    real = urllib.request.urlopen

    def urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if not url.startswith(base + "/"):  # exactly this host and port (":881" never matches ":8815")
            return real(req, *args, **kwargs)
        keyed = isinstance(req, urllib.request.Request) and req.has_header("X-team-key")
        method = req.get_method() if isinstance(req, urllib.request.Request) else "GET"
        path = _ID.sub("/{id}", url[len(base) :].split("?")[0])
        t0, status = time.monotonic(), "ok"
        try:
            if latency_s > 0:
                time.sleep(latency_s)
            return real(req, *args, **kwargs)
        except urllib.error.HTTPError as e:
            status = "rate_limited" if e.code == 429 else str(e.code)
            raise
        except Exception as e:
            status = type(e).__name__
            raise
        finally:
            rec.request(keyed, method, path, time.monotonic() - t0, status)

    urllib.request.urlopen = urlopen  # type: ignore[assignment]


def _patch_stages(rec: _Recorder) -> None:
    import importlib

    import psycopg

    judge = importlib.import_module("bazaar_agent.jev.judge")
    judge._reply_within = _timed(rec, "jev", judge._reply_within)  # type: ignore[attr-defined]
    jev_latency_s = float(os.environ.get("SP1_JEV_LATENCY_MS") or 0) / 1000.0
    if jev_latency_s > 0:  # a slow Jev API: every call waits this much longer (still bounded by jev_timeout_s)
        real_post = judge._post  # type: ignore[attr-defined]

        def slow_post(*args: Any, **kwargs: Any) -> Any:
            time.sleep(jev_latency_s)
            return real_post(*args, **kwargs)

        judge._post = slow_post  # type: ignore[attr-defined]
    psycopg.Cursor.execute = _timed(rec, "db", psycopg.Cursor.execute)  # type: ignore[method-assign]
    psycopg.Cursor.executemany = _timed(rec, "db", psycopg.Cursor.executemany)  # type: ignore[method-assign]
    psycopg.connect = _timed(rec, "db_connect", psycopg.connect)  # type: ignore[assignment]
    for module, attr, stage in (
        ("bazaar_agent.learn.recall", "HybridRecall.recall", "recall"),
        ("bazaar_agent.holdings", "Holdings.me", "holdings_me"),
        ("bazaar_agent.agents.runtime", "MarketFeed.events", "feed_events"),
        ("bazaar_agent.strategy", "build_playbook", "playbook"),
        ("bazaar_agent.strategy", "build_market", "market"),
    ):
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue  # a stage this checkout does not have yet
        owner_name, _, name = attr.rpartition(".")
        owner = getattr(mod, owner_name) if owner_name else mod
        if hasattr(owner, name):
            setattr(owner, name, _timed(rec, stage, getattr(owner, name)))
    decisions = importlib.import_module("bazaar_agent.decisions")
    real_decide, real_settle = decisions.DecisionLog.decide, decisions.DecisionLog.settle

    def decide(self: Any, decision: Any) -> Any:
        if getattr(decision, "status", None) == "expired":
            rec.count("expired")
        return real_decide(self, decision)

    def settle(self: Any, decision_id: int, status: str, *args: Any, **kwargs: Any) -> Any:
        if status == "expired":
            rec.count("expired")
        return real_settle(self, decision_id, status, *args, **kwargs)

    decisions.DecisionLog.decide = decide  # type: ignore[method-assign]
    decisions.DecisionLog.settle = settle  # type: ignore[method-assign]


def _patch_ticks(rec: _Recorder) -> None:
    from bazaar_agent import traces

    real = traces.per_tick

    def per_tick(name: str, on_tick: Callable[[Any], None], **kwargs: Any) -> Callable[[Any], None]:
        inner = real(name, on_tick, **kwargs)  # `agent=True` for the agent loops (traces.per_tick)

        def measured(clock: Any) -> None:
            rec.begin(clock)
            error = None
            try:
                inner(clock)
            except BaseException as e:
                error = type(e).__name__
                raise
            finally:
                rec.end(error)

        return measured

    traces.per_tick = per_tick  # type: ignore[assignment]


LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
NOT_FOR_PROFILING = frozenset({"bazaar", "railway"})  # the laptop's real-game database, the team's shared one


def _local_database(settings: Any) -> bool:
    """True when the agents' Postgres is a database of its own on this machine: BAZAAR_SIM_DATABASE_URL set in
    the environment (never the DATABASE_URL fallback), every host and hostaddr (query overrides included)
    loopback or a local socket, and not the laptop's real `bazaar` database. Its URL is never printed."""
    import psycopg

    explicit = os.environ.get("BAZAAR_SIM_DATABASE_URL", "").strip()
    url = settings.database_url.get_secret_value()
    if not explicit or url != explicit:
        return False
    try:
        info = psycopg.conninfo.conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return False
    hosts = [h.strip() for key in ("host", "hostaddr") for h in str(info.get(key) or "").split(",") if h.strip()]
    local = bool(hosts) and all(h in LOOPBACK or h.startswith("/") for h in hosts)
    dbname = str(info.get("dbname") or "")  # none named: Postgres picks the user's name, often `bazaar`
    return local and bool(dbname) and dbname not in NOT_FOR_PROFILING


def _sim_pid_file(port: int) -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / f"bazaar-tick-profile-sim-{port}.pid"


def _our_sim(port: int) -> bool:
    """True when `tick_profile.py sim` started the simulator on `port` and it still runs."""
    try:
        pid = int(_sim_pid_file(port).read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def serve_sim(port: int, tick_seconds: float) -> None:
    """A fresh in-memory simulator on `port`, refusing a port something already answers on (another worker's
    simulator: our agents would trade in its world). Runs until Ctrl-C."""
    import socket
    import subprocess

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"127.0.0.1:{port} is busy: pick a free port (8900+), never share a simulator")
    env = {**os.environ, "SIM_DATABASE_URL": "memory", "SIM_TICK_SECONDS": str(tick_seconds), "PORT": str(port)}
    env.setdefault("SIM_DUEL_FIRST_TICK", "3")
    env.setdefault("SIM_DUEL_TICKS", "40")
    server = subprocess.Popen([sys.executable, "-m", "bazaar_sim", "serve", "--port", str(port)], env=env)
    pid_file = _sim_pid_file(port)
    pid_file.write_text(str(server.pid), encoding="utf-8")  # `run` profiles only against this simulator
    try:
        server.wait()
    finally:
        pid_file.unlink(missing_ok=True)


def run(agent: str, port: int, out: Path, args: list[str], latency_ms: float = 0.0) -> None:
    if agent not in AGENTS:
        raise SystemExit(f"agent must be one of {sorted(AGENTS)}")
    if os.environ.get("BAZAAR_SIM") != "local":
        raise SystemExit("tick_profile runs only against a local simulator: set BAZAAR_SIM=local")
    if not _our_sim(port):
        raise SystemExit(f"no simulator of ours on {port}: start one with `tick_profile.py sim --port {port}`")
    os.environ["BAZAAR_TRACING"] = "0"  # profiled ticks never reach the shared Phoenix, whatever .env says
    base = f"http://127.0.0.1:{port}"
    from bazaar_agent import config

    config.LOCAL_SIM_URL = base  # the simulator on --port, never the real game
    settings = config.load_settings()
    if settings.bazaar_url != base:
        raise SystemExit("refusing to profile: the target is not the local simulator")
    if not _local_database(settings):
        raise SystemExit(
            "refusing to profile: the agents write ledger and decision rows; set BAZAAR_SIM_DATABASE_URL in the "
            "environment to a database of its own on this machine (not `bazaar`, never a shared one)"
        )
    rules_file = os.environ.get("SP1_GUARDRAILS")
    if rules_file:  # a scenario's own rule book (e.g. Sunday: the venue is open, no bond reserve)
        from bazaar_agent import guardrails

        guardrails.GUARDRAILS_FILE = Path(rules_file)
        guardrails.load_guardrails.__defaults__ = (Path(rules_file),)
    rec = _Recorder(agent, out)
    _patch_http(rec, base, latency_ms / 1000.0)
    _patch_stages(rec)
    _patch_ticks(rec)
    from bazaar_agent.cli import app

    sys.argv = ["bazaar", *AGENTS[agent], *args]
    app()


# ------------------------------------------------------------------------------------------ report


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _max_window(stamps: Iterable[float], width: float = 1.0) -> int:
    ordered, best, lo = sorted(stamps), 0, 0
    for hi, t in enumerate(ordered):
        while ordered[lo] <= t - width:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def report(out: Path) -> dict[str, Any]:
    """Per agent: tick wall p50/p95/max against the budget, late and dropped ticks, requests and stages;
    then the key's budget across every agent of the run (max requests in any 1 s window, mean per tick)."""
    summary: dict[str, Any] = {"agents": {}}
    stamps: list[float] = []
    for agent in AGENTS:
        ticks, reqs = _rows(out / f"{agent}.ticks.jsonl"), _rows(out / f"{agent}.requests.jsonl")
        if not ticks:
            continue
        walls = [t["wall_s"] for t in ticks]
        stages: dict[str, list[float]] = defaultdict(list)
        per_tick: dict[str, list[float]] = defaultdict(list)
        for t in ticks:
            for stage, values in t["stages"].items():
                stages[stage] += values
                per_tick[stage].append(sum(values))
        keyed = [r for r in reqs if r["keyed"]]
        stamps += [r["t"] for r in keyed]
        summary["agents"][agent] = {
            "ticks": len(ticks),
            "tick_seconds": ticks[-1]["tick_seconds"],
            "wall_p50": _pct(walls, 0.5),
            "wall_p95": _pct(walls, 0.95),
            "wall_max": max(walls),
            "budget_p50": _pct([t["budget_s"] for t in ticks], 0.5),
            "over_budget": sum(1 for t in ticks if t["wall_s"] > t["budget_s"]),
            "late": sum(1 for t in ticks if t["wall_s"] > t["next_tick_in"]),
            "expired": sum(t.get("expired", 0) for t in ticks),
            "rate_limited": sum(1 for r in reqs if r["status"] == "rate_limited"),
            "keyed_per_tick": round(len([r for r in keyed if r["in_tick"]]) / len(ticks), 2),
            "keyed_bg": len([r for r in keyed if not r["in_tick"]]),
            "public_per_tick": round(len([r for r in reqs if not r["keyed"] and r["in_tick"]]) / len(ticks), 2),
            "stages": {
                s: {
                    "calls": len(v),
                    "p50": _pct(v, 0.5),
                    "p95": _pct(v, 0.95),
                    "max": max(v),
                    "per_tick_p95": _pct(per_tick[s], 0.95),
                }
                for s, v in sorted(stages.items())
            },
            "routes": dict(
                sorted(
                    ((f"{'K' if r['keyed'] else 'P'} {r['method']} {r['path']}", 0) for r in reqs),
                    key=lambda kv: kv[0],
                )
            ),
        }
        routes = summary["agents"][agent]["routes"]
        for r in reqs:
            routes[f"{'K' if r['keyed'] else 'P'} {r['method']} {r['path']}"] += 1
    if stamps:
        span = max(stamps) - min(stamps) or 1.0
        summary["key"] = {
            "requests": len(stamps),
            "max_in_1s": _max_window(stamps),
            "mean_per_s": round(len(stamps) / span, 2),
            "limit_per_s": KEY_RATE_PER_S,
        }
    return summary


def _print(summary: dict[str, Any]) -> None:
    for agent, a in summary["agents"].items():
        print(
            f"\n{agent}: {a['ticks']} ticks of {a['tick_seconds']} s · wall p50 {a['wall_p50']:.2f} s, "
            f"p95 {a['wall_p95']:.2f} s, max {a['wall_max']:.2f} s · budget p50 {a['budget_p50']:.2f} s · "
            f"over budget {a['over_budget']} · late {a['late']} · dropped (expired) {a['expired']} · "
            f"429 {a['rate_limited']}"
        )
        print(
            f"  keyed req/tick {a['keyed_per_tick']} (+{a['keyed_bg']} outside ticks) · public req/tick "
            f"{a['public_per_tick']}"
        )
        for stage, s in a["stages"].items():
            print(
                f"  {stage:<12} calls {s['calls']:>5} · p50 {s['p50'] * 1000:7.1f} ms · p95 {s['p95'] * 1000:7.1f} ms"
                f" · max {s['max'] * 1000:7.1f} ms · per tick p95 {s['per_tick_p95'] * 1000:7.1f} ms"
            )
        for route, n in sorted(a["routes"].items(), key=lambda kv: -kv[1])[:12]:
            print(f"    {n:>5}  {route}")
    if "key" in summary:
        k = summary["key"]
        print(
            f"\nkey budget (all agents): {k['requests']} keyed requests · max {k['max_in_1s']} in any 1 s · "
            f"mean {k['mean_per_s']}/s · limit {k['limit_per_s']}/s (bursts of 20)"
        )


def main(argv: list[str]) -> None:
    if len(argv) >= 2 and argv[0] == "report":
        summary = report(Path(argv[1]))
        if "--json" in argv:
            print(json.dumps(summary, indent=2))
        else:
            _print(summary)
        return
    if len(argv) >= 3 and argv[0] == "sim" and argv[1] == "--port":
        serve_sim(int(argv[2]), float(argv[4]) if argv[3:4] == ["--tick-seconds"] and len(argv) > 4 else 15.0)
        return
    if len(argv) >= 6 and argv[0] == "run" and argv[2] == "--port" and argv[4] == "--out":
        rest = argv[6:]
        latency = float(os.environ.get("SP1_LATENCY_MS") or 0)
        run(argv[1], int(argv[3]), Path(argv[5]), rest[1:] if rest[:1] == ["--"] else rest, latency)
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
