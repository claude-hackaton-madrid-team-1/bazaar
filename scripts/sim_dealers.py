"""Simulator proof per dealer (N14a): our live taker against a local simulator, Abuela and El Chato side by side.

    BAZAAR_SIM_PORT=8818 uv run python scripts/sim_dealers.py --lift 0 --lift 0.15 --lift 0.25

For each `dealer_final_lift` this script does four things:
1. It starts a fresh in-memory `bazaar-sim serve` on 127.0.0.1:<BAZAAR_SIM_PORT>, with El Chato open to
   everyone from tick 1 and 2 s ticks.
2. It runs our real `Taker` in --live against that simulator, as a child process. GUARDRAILS.md and
   STRATEGY.md are loaded as committed, and only `dealer_final_lift` is replaced.
3. It prints each dealer thread: what was opened (ladder, final_max, changed_by), our bids, how the thread
   ended and at what price, then our cash.
4. It confirms the simulator never holds a bid of ours above the rarity cap.

The run uses no database, no .env and no network beyond loopback. The child runs under `sim_guard`'s
sitecustomize, so any other connect fails the run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = Path(__file__).resolve().parent / "sim_guard"
PORT = int(os.environ.get("BAZAAR_SIM_PORT") or 8818)
SIM = f"http://127.0.0.1:{PORT}"
KEY = "sim-team1"
NOWHERE_DB = "postgresql://proof:proof@127.0.0.1:9/bazaar_sim_proof"  # unreachable: JSONL ledgers and logs
INHERITED = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "VIRTUAL_ENV")

CHILD = r"""
import json, sys
from pathlib import Path
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.config import load_settings
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Ledger, load_guardrails
from bazaar_agent.sdk import public_client, team_client
from bazaar_agent.strategy import load_strategy
from bazaar_agent.ticks import run_per_tick

lift, ticks, out, DEALERS = float(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), set(sys.argv[4].split(","))
settings = load_settings()
base = load_guardrails().rules
rules = type(base).model_validate({**base.model_dump(), "dealer_final_lift": lift})  # 0-0.5, as GUARDRAILS.md
params = load_strategy().params
team, public = team_client(settings), public_client(settings)


class OnlyThese:
    # The public client with /api/dealers narrowed to the dealers under test: a proof per dealer.
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def dealers(self):
        raw = self.inner.dealers()
        key = "personas" if "personas" in raw else "dealers"
        return {key: [d for d in raw.get(key) or [] if d.get("id") in DEALERS]}


lines = []
decisions = DecisionLog(out)
taker = Taker(
    team, OnlyThese(public), rules=rules, params=lambda tick: params, ledger=Ledger(out / "ledger.jsonl"),
    decisions=decisions, feed=MarketFeed(public.feed_window), live=True, log=lines.append,
    config=TakerConfig(max_dealer_threads=3),
)
try:
    run_per_tick(team.clock, taker.on_tick, max_ticks=ticks)
finally:
    decisions.close()
mine = team.my_threads().get("threads") or []
summary = {
    "lines": lines,
    "threads": [team.thread(int(t["id"])) for t in mine if isinstance(t, dict) and t.get("with") in DEALERS],
    "cash": team.me().get("cash"),
}
(out / "summary.json").write_text(json.dumps(summary, default=str))
"""


def get(path: str) -> dict:
    req = urllib.request.Request(SIM + path, headers={"X-Team-Key": KEY})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def child_env(data_dir: Path, env_file: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in INHERITED or k.startswith("LC_")}
    env.update(
        {
            "PYTHONPATH": str(GUARD),
            "BAZAAR_ENV_FILE": str(env_file),
            "BAZAAR_SIM": "local",
            "BAZAAR_SIM_PORT": str(PORT),
            "BAZAAR_SIM_KEY": KEY,
            "BAZAAR_DATA_DIR": str(data_dir),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return env


def port_busy() -> bool:
    """Anything that answers, even with an HTTP error (404, a 429), holds the port: only a refused connect is free."""
    try:
        get("/api/health")
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def serve(log: Path) -> subprocess.Popen[bytes]:
    """A fresh simulator on our port, or exit: another worker's simulator there must never get our moves."""
    if port_busy():
        raise SystemExit(f"something already answers on {SIM}: pick a free BAZAAR_SIM_PORT")
    env = {k: v for k, v in os.environ.items() if k in INHERITED}
    env.update(
        {
            "PORT": str(PORT),
            "SIM_DATABASE_URL": "memory",  # a fresh world each run (without it the world persists in .local/sim)
            "SIM_TICK_SECONDS": "2",
            "SIM_CHATO_OPEN_TICKS": "1",
            "SIM_SEED": "11",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "bazaar_sim", "serve", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(150):
        if server.poll() is not None:
            raise SystemExit(f"bazaar-sim exited with code {server.returncode}: see {log}")
        try:
            if get("/api/clock").get("tick", -1) >= 1:  # Chato opens at tick 1
                time.sleep(0.5)  # ours, not another worker's that won the port: ours must still be running
                if server.poll() is not None:
                    raise SystemExit(f"bazaar-sim exited with code {server.returncode}: {SIM} is someone else's")
                return server
        except OSError:
            pass
        time.sleep(0.2)
    stop(server)
    raise SystemExit(f"the simulator did not start on {SIM}: see {log}")


def stop(server: subprocess.Popen[bytes]) -> None:
    """Stop our simulator and wait until the port is free again (the next lift needs a fresh world there)."""
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=10)
    for _ in range(50):
        if not port_busy():
            return
        time.sleep(0.2)
    raise SystemExit(f"{SIM} still answers after our simulator stopped")


def describe(lift: float, out: Path, caps: dict[str, int]) -> None:
    summary = json.loads((out / "summary.json").read_text())
    log = out / "agents" / "decisions.jsonl"
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.is_file() else []
    print(f"\n=== dealer_final_lift {lift:g} · cash at the end {summary['cash']} (started with 400)")
    for r in rows:
        if r.get("kind") == "dealer_open":
            i = r["inputs"]
            print(f"open {i['dealer']} {i['item']} ({i['rarity']}) ladder {i['plan']} final_max {i['final_max']}")
            for note in i.get("changed_by") or []:
                print(f"    changed_by: {note}")
    for th in summary["threads"]:
        offers = th.get("standing_offers") or []
        msgs = [(m.get("sender"), m.get("offer")) for m in th.get("messages") or []]
        print(
            f"thread {th.get('id')} with {th.get('with')}: {th.get('status')} ({th.get('closed_reason') or '-'})"
            f" · {len(msgs)} messages · open offers {[(o.get('maker'), o.get('want')) for o in offers]}"
        )
    for line in summary["lines"]:
        if "accept candidate(s)" not in line:  # the per-tick summary line: noise here
            print(f"    {line}")
    rarity_of = {r["inputs"]["item"]: r["inputs"]["rarity"] for r in rows if r.get("kind") == "dealer_open"}
    above = [
        (r["inputs"]["item"], r["move"]["price"])
        for r in rows
        if r.get("kind") == "dealer_bid"
        and r.get("status") == "approved"
        and r["move"]["price"] > caps.get(rarity_of.get(r["inputs"]["item"], ""), 10**6)
        and r["inputs"].get("her_ask") != r["move"]["price"]
    ]
    print(f"bids above the cap (other than meeting a final): {above or 'none'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lift", type=float, action="append", default=None)
    ap.add_argument("--ticks", type=int, default=30)
    ap.add_argument("--dealer", action="append", default=None, help="Only these dealers (repeat; default both)")
    args = ap.parse_args()
    lifts = args.lift or [0.0, 0.15]
    dealers = args.dealer or ["abuela", "chato"]
    sys.path.insert(0, str(ROOT / "src"))
    from bazaar_agent.guardrails import load_guardrails

    rules = load_guardrails().rules
    caps = {"common": rules.max_price_common, "uncommon": rules.max_price_uncommon, "rare": rules.max_price_rare}
    with tempfile.TemporaryDirectory(prefix="bazaar-sim-dealers-") as tmp:
        base = Path(tmp)
        env_file = base / "empty.env"
        env_file.write_text("")
        for lift in lifts:
            out = base / f"lift-{lift:g}"
            out.mkdir()
            server = serve(base / f"sim-{lift:g}.log")
            try:
                run = subprocess.run(
                    [sys.executable, "-c", CHILD, str(lift), str(args.ticks), str(out), ",".join(dealers)],
                    cwd=ROOT,
                    env=child_env(out, env_file),
                    capture_output=True,
                    text=True,
                    timeout=args.ticks * 4 + 60,
                )
                if run.returncode != 0:
                    print(run.stdout[-3000:], run.stderr[-3000:])
                    return 1
                describe(lift, out, caps)
            finally:
                stop(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
