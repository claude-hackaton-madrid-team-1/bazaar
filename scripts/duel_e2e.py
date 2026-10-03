#!/usr/bin/env python3
"""End-to-end duels against a LOCAL simulator (B27): `bazaar duel run --play` and the taker side by side, sharing
the team's one accept per tick through the ledger, against zoo and exploiter rivals. Never the real game.

    uv run python scripts/duel_e2e.py --tick-seconds 15 --ticks 80 --out .local/e2e-15s.json

It starts `bazaar-sim serve` on 127.0.0.1:8765 with 3 seller/buyer pairs per team per session (6 duels on one
deadline), runs both agents with BAZAAR_SIM=local and a `sim-` key only, waits, then reads the finished duels and
the ledger. The duel settings are whatever GUARDRAILS.md says in this checkout: run it from a scratch worktree
whose (uncommitted) GUARDRAILS.md holds the settings under test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SIM_URL = "http://127.0.0.1:8765"
STYLES = "linear,convex,one_shot,tit_for_tat,no_show,holdout,squeezer,oracle_squeezer"
FORBIDDEN_ENV = ("BAZAAR_KEY", "BAZAAR_LIVE", "DATABASE_URL", "BAZAAR_SIM_DATABASE_URL")
DEAD_DB = "postgresql://nobody@127.0.0.1:1/none"  # nothing listens on port 1: every process falls back to JSONL


def agent_env() -> dict[str, str]:
    """The agents' environment: the local simulator, a sim key, no real key, no live flag, no shared database."""
    env = {k: v for k, v in os.environ.items() if k not in FORBIDDEN_ENV}
    env |= {"BAZAAR_SIM": "local", "BAZAAR_SIM_KEY": "sim-team1"}
    env |= {"DATABASE_URL": DEAD_DB, "BAZAAR_SIM_DATABASE_URL": DEAD_DB}  # the JSONL ledger, never a database
    return env


def preflight(env: dict[str, str]) -> None:
    """Refuse to run unless the agent's resolved target is the local simulator (settings.simulator, 127.0.0.1)."""
    code = (
        "from bazaar_agent.config import load_settings; s = load_settings(); "
        "print(s.bazaar_url, s.simulator, s.require_team_key()[:4])"
    )
    out = subprocess.run(["uv", "run", "python", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    url, simulator, key = (out.stdout.strip().split() + ["", "", ""])[:3]
    if url != SIM_URL or simulator != "True" or key != "sim-":
        raise SystemExit(f"preflight refused: target {url!r} simulator {simulator!r} key {key!r} ({out.stderr[-300:]})")
    if (ROOT / ".env").exists():
        raise SystemExit("preflight refused: a .env in this checkout could carry real keys or DATABASE_URL")


def wait_for_sim(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(SIM_URL + "/api/clock", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("the simulator did not come up")


def finished_duels(env: dict[str, str]) -> list[dict[str, Any]]:
    code = (
        "import json; from bazaar_agent.config import load_settings; from bazaar_agent.sdk import team_client; "
        "print(json.dumps(team_client(load_settings()).duels(done=True)))"
    )
    out = subprocess.run(["uv", "run", "python", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    return [d for d in json.loads(out.stdout or "{}").get("duels") or [] if isinstance(d, dict)]


def summarize(duels: list[dict[str, Any]], duel_log: str, taker_log: str, ledger: Path) -> dict[str, Any]:
    scored = [d for d in duels if isinstance(d.get("result"), dict) and not d["result"].get("practice")]
    deals = [d for d in scored if d["status"] == "deal"]
    gains = [float(d["result"].get("your_gain") or 0) for d in deals]
    accepts = Counter()
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            item = str(row.get("item", ""))
            accepts["duel" if item.startswith("duel:") else "taker"] += 1
    return {
        "duels_scored": len(scored),
        "deals": len(deals),
        "deal_rate": round(len(deals) / len(scored), 3) if scored else None,
        "points": round(sum(float(d["result"].get("points") or 0) for d in scored), 2),
        "mean_gain_per_deal": round(sum(gains) / len(gains), 2) if gains else None,
        "outside_limit": sum(1 for g in gains if g < 0),
        "two_issue_duels": sum(1 for d in scored if "days" in (d.get("issues") or [])),
        "mean_rounds_per_deal": round(sum(int(d.get("rounds") or 0) for d in deals) / len(deals), 2) if deals else None,
        "ledger_accepts": dict(accepts),
        "duel_log": {
            "ticks": len(re.findall(r"^tick \d+:", duel_log, re.M)),
            "slot_taken": duel_log.count("another process took the team's accept"),
            "guardrail_rejections": duel_log.count("GUARDRAIL"),
            "no_time_left": duel_log.count("no time left in tick"),
            "skipped": duel_log.count("skipped this tick"),
            "refused": duel_log.count("refused"),
        },
        "taker_log_lines": len(taker_log.splitlines()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tick-seconds", type=float, default=15.0)
    ap.add_argument("--ticks", type=int, default=80, help="ticks the agents run (sessions every 13 ticks from 2)")
    ap.add_argument("--decay", type=float, default=0.08)
    ap.add_argument("--styles", default=STYLES)
    ap.add_argument("--taker", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    env = agent_env()
    preflight(env)
    work = args.out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    sim_env = {k: v for k, v in os.environ.items() if not k.startswith("BAZAAR")} | {
        "SIM_TICK_SECONDS": str(args.tick_seconds),
        "SIM_DUEL_FIRST_TICK": "2",
        "SIM_DUEL_EVERY_TICKS": "13",
        "SIM_DUEL_TICKS": "12",
        "SIM_DUEL_PAIRS": "3",
        "SIM_DUEL_STYLES": args.styles,
        "SIM_DUEL_DECAY": str(args.decay),
        "SIM_DATABASE_URL": "memory",
    }
    sim = subprocess.Popen(
        ["uv", "run", "bazaar-sim", "serve", "--port", "8765"],
        cwd=ROOT,
        env=sim_env,
        stdout=(work / "sim.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    procs: list[subprocess.Popen[bytes]] = []
    try:
        wait_for_sim()
        ticks = str(args.ticks)
        duel_cmd = ["uv", "run", "bazaar", "duel", "run", "--play", "--no-jev", "--max-ticks", ticks]
        procs.append(subprocess.Popen(duel_cmd, cwd=ROOT, env=env, stdout=(work / "duels.log").open("w"),
                                      stderr=subprocess.STDOUT))  # fmt: skip
        if args.taker:
            taker_cmd = ["uv", "run", "bazaar", "agent", "taker", "--live", "--no-jev", "--max-ticks", ticks]
            procs.append(subprocess.Popen(taker_cmd, cwd=ROOT, env=env, stdout=(work / "taker.log").open("w"),
                                          stderr=subprocess.STDOUT))  # fmt: skip
        for p in procs:
            p.wait(timeout=args.ticks * args.tick_seconds + 300)
        time.sleep(args.tick_seconds * 2)  # let the last accepts settle
        duels = finished_duels(env)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        sim.terminate()
    ledger = ROOT / ".local" / "sim-client" / "ledger.jsonl"
    taker_log = (work / "taker.log").read_text() if (work / "taker.log").exists() else ""
    result = {
        "tick_seconds": args.tick_seconds,
        "ticks": args.ticks,
        "decay": args.decay,
        "styles": args.styles,
        "taker": args.taker,
        "guardrails": {
            line.split("`")[1]: line.split(" = ")[1].split(" — ")[0]
            for line in (ROOT / "GUARDRAILS.md").read_text().splitlines()
            if line.startswith("- `duel_")
        },
        **summarize(duels, (work / "duels.log").read_text(), taker_log, ledger),
    }
    args.out.write_text(json.dumps(result, indent=1))
    (work / "duels_done.json").write_text(json.dumps(duels))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
