"""D1 proof: our real `bazaar duel run --play` over HTTP against a local bazaar-sim, scored from /api/duels.

    uv run python scripts/duel_sim_proof.py run --label v2 --decay 0.08 --sessions 16 --out .local/duel-proof
    uv run python scripts/duel_sim_proof.py table .local/duel-proof

`run` serves bazaar-sim on 127.0.0.1:8765 (BAZAAR_SIM=local's address), plays `duel run --play --no-jev` from `--repo`
(default: this checkout; its GUARDRAILS.md picks the policy) for `--sessions` duel sessions of `--pairs` seller/buyer
pairs on one deadline, then saves every duel to <out>/<label>.json. To compare policies side by side, give each one a
scratch `git worktree` with its own GUARDRAILS.md (and, to run them at once, its own port patched into config.py's
LOCAL_SIM_URL, passed here as --port; never commit that patch). Isolation as scripts/sim_smoke.py: an allow-listed env,
an empty env file, the sim_guard sitecustomize (loopback only), a dead proxy and an unreachable DB (JSONL ledger).

`table` prints, per run and split by issues and role, over the FINISHED duels (the last session is cut off): deal
rate, deals outside our limit, mean score (share × (1 − decay) ** rounds for a deal, 0 for none: the real result
rule, with the share at the simulator's true signed days weight), share per deal, rounds per deal, and the no-deal
duels where a rival offer was strictly inside our limit at the worst-case valuation our policy uses (D1 criterion 2).
Needs the simulator's SIM_DUEL_STYLES and SIM_DUEL_PAIRS; without them the sim plays its own bot, one pair.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
KEY = "sim-team1"
DEAD_PROXY = "http://127.0.0.1:9"
NOWHERE_DB = "postgresql://proof:proof@127.0.0.1:9/bazaar_sim_proof"
INHERITED = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "TEMP", "TMP", "VIRTUAL_ENV")
HONEST = "linear,convex,one_shot,tit_for_tat,no_show,sim,holdout"
CRASH_MARKERS = ("Traceback (most recent call last)", "tick loop:")


def inherited(repo: Path) -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k in INHERITED or k.startswith("LC_")}
    return {**keep, "PYTHONPATH": str(repo / "scripts" / "sim_guard"), "PYTHONDONTWRITEBYTECODE": "1"}


def get(url: str, keyed: bool = False) -> Any:
    req = urllib.request.Request(url, headers={"X-Team-Key": KEY} if keyed else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost, never a proxy
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read())


def sim_env(a: argparse.Namespace, repo: Path) -> dict[str, str]:
    return {
        **inherited(repo),
        "SIM_TICK_SECONDS": a.tick_seconds,
        "SIM_DATABASE_URL": "memory",
        "SIM_ADMIN_TOKEN": secrets.token_urlsafe(24),
        "SIM_SEED": a.seed,
        "SIM_PLAYER_TEAMS": "1",
        "SIM_RIVALS": "0",
        "SIM_DUEL_FIRST_TICK": "2",
        "SIM_DUEL_EVERY_TICKS": str(a.duel_ticks + 2),
        "SIM_DUEL_TICKS": str(a.duel_ticks),
        "SIM_DUEL_STYLES": a.styles,
        "SIM_DUEL_DECAY": a.decay,
        "SIM_DUEL_PAIRS": a.pairs,
        "PORT": str(a.port),
    }


def cli_env(work: Path, repo: Path) -> dict[str, str]:
    env_file = work / "empty.env"
    env_file.write_text("")
    return {
        **inherited(repo),
        "BAZAAR_ENV_FILE": str(env_file),  # an empty file: a laptop's real .env never loads
        "BAZAAR_SIM": "local",
        "BAZAAR_SIM_KEY": KEY,
        "BAZAAR_TEAM_ID": "t01",
        "BAZAAR_DATA_DIR": str(work / "data"),
        "DATABASE_URL": NOWHERE_DB,
        "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
        "BAZAAR_TRACING": "0",
        "COLUMNS": "220",
        "HTTP_PROXY": DEAD_PROXY,
        "HTTPS_PROXY": DEAD_PROXY,
        "http_proxy": DEAD_PROXY,
        "https_proxy": DEAD_PROXY,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


def wait_for(url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"bazaar-sim exited with code {server.returncode}")
        try:
            if get(url + "/api/health").get("ok"):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("bazaar-sim did not answer /api/health within 60 s")


def run(a: argparse.Namespace) -> None:
    repo = a.repo.resolve()
    guard = repo / "scripts" / "sim_guard" / "sitecustomize.py"
    if not guard.is_file():  # without it the children would run with no network guard
        raise SystemExit(f"{guard} is missing: refusing to run the children without the loopback-only guard")
    py = str(repo / ".venv" / "bin" / "python")
    url = f"http://127.0.0.1:{a.port}"
    work = a.out / a.label
    work.mkdir(parents=True, exist_ok=True)
    max_ticks = 2 + a.sessions * (a.duel_ticks + 2) + 2
    cmd = [py, "-m", "bazaar_agent.cli", "duel", "run", "--play", "--max-ticks", str(max_ticks), "--no-jev"]
    with (work / "sim.log").open("w") as sim_log:
        server = subprocess.Popen(
            [py, "-m", "bazaar_sim", "serve", "--port", str(a.port)],
            env=sim_env(a, repo),
            cwd=repo,
            stdout=sim_log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for(url, server)
            timeout = max_ticks * float(a.tick_seconds) * 3 + 120
            with (work / "duel_run.log").open("w") as log:
                done = subprocess.run(
                    cmd, env=cli_env(work, repo), cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=timeout
                )
            time.sleep(float(a.tick_seconds) * 2)  # let the last accepts settle
            duels = get(url + "/api/duels?done=true", keyed=True)["duels"]
        finally:
            server.terminate()
            server.wait(timeout=10)
    text = (work / "duel_run.log").read_text(errors="replace")
    crashes = sum(text.count(m) for m in CRASH_MARKERS)
    record = {"label": a.label, "exit": done.returncode, "crashes": crashes, "duels": duels}
    record["settings"] = {k: str(v) for k, v in vars(a).items() if k not in ("func",)}
    (a.out / f"{a.label}.json").write_text(json.dumps(record))
    print(f"{a.label}: exit {done.returncode}, {len(duels)} duels, crash markers {crashes}")


# ---------------------------------------------------------------- the table


def worst_value(d: dict[str, Any], price: float, days: float | None) -> float:
    weight = abs(d.get("your_days_weight") or 0.0) if "days" in d["issues"] else 0.0
    return price - weight * (days or 0) if d["role"] == "seller" else price + weight * (days or 0)


def inside(d: dict[str, Any], value: float) -> bool:
    return value > d["your_limit"] if d["role"] == "seller" else value < d["your_limit"]


def score(d: dict[str, Any]) -> float:
    if d["status"] != "deal":
        return 0.0
    return float((d.get("result") or {}).get("share", 0.0)) * (1 - d["decay_per_round"]) ** d["rounds"]


def left_inside(d: dict[str, Any]) -> bool:
    rival = d.get("rival")
    offers = [m for m in d.get("messages") or [] if m.get("from") == rival and m.get("price") is not None]
    return d["status"] != "deal" and any(inside(d, worst_value(d, m["price"], m.get("days"))) for m in offers)


def row(label: str, duels: list[dict[str, Any]]) -> str:
    if not duels:
        return f"| {label} | 0 | | | | | | |"
    deals = [d for d in duels if d["status"] == "deal"]
    outside = sum(1 for d in deals if (d.get("result") or {}).get("your_gain", 0) < 0)
    shares = [float((d.get("result") or {}).get("share", 0.0)) for d in deals]
    rounds = [d["rounds"] for d in deals]
    return (
        f"| {label} | {len(duels)} | {len(deals) / len(duels):.2f} | {outside} | "
        f"{statistics.mean(score(d) for d in duels):.3f} | {statistics.mean(shares) if shares else 0:.3f} | "
        f"{statistics.mean(rounds) if rounds else 0:.2f} | {sum(left_inside(d) for d in duels)} |"
    )


def table(a: argparse.Namespace) -> None:
    print("| run | duels | deal rate | outside limit | mean score | share per deal | rounds/deal | no deal, inside |")
    print("|---|---|---|---|---|---|---|---|")
    for path in sorted(a.out.glob("*.json")):
        data = json.loads(path.read_text())
        duels = [d for d in data["duels"] if d["status"] != "live"]
        label = data["label"]
        print(row(f"{label} (exit {data['exit']}, crash markers {data['crashes']})", duels))
        for name, two in (("price only", False), ("two-issue", True)):
            print(row(f"{label} {name}", [d for d in duels if ("days" in d["issues"]) == two]))
        for role in ("seller", "buyer"):
            print(row(f"{label} {role}", [d for d in duels if d["role"] == role]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--repo", type=Path, default=REPO)
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--decay", default="0.08")
    r.add_argument("--styles", default=HONEST)
    r.add_argument("--pairs", default="3")
    r.add_argument("--sessions", type=int, default=16)
    r.add_argument("--duel-ticks", type=int, default=12)
    r.add_argument("--seed", default="7")
    r.add_argument("--tick-seconds", default="2")
    r.add_argument("--out", type=Path, default=REPO / ".local" / "duel-proof")
    r.set_defaults(func=run)
    t = sub.add_parser("table")
    t.add_argument("out", type=Path, nargs="?", default=REPO / ".local" / "duel-proof")
    t.set_defaults(func=table)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
