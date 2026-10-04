"""Count blocker log lines per Saturday window (real-game lines only; simulator deployments skipped).

Usage: python windows.py <dir with <service>.<HHMMSS>.<id>.log files> <service-prefix>
Each distinct (tick, line) is counted once, so overlapping containers during a redeploy do not double count.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

WINDOWS = [("A 09:29-10:20", 159, 261), ("B 10:20-11:49", 262, 440), ("C 11:49-13:25", 441, 630),
           ("D 15:29-17:34", 631, 897), ("E 17:34-20:15", 898, 1201), ("F 20:58-23:00", 1202, 1445)]
RULES = [
    ("board skip: cash_floor", r"skip .* from board: denied:.*cash_floor"),
    ("board skip: spend cap", r"skip .* from board: denied:.*max_spend_per_game_hour"),
    ("board skip: price cap", r"skip .* from board: denied:.*max_price_"),
    ("board skip: accept quota 1/tick", r"skip .* from board: accept quota"),
    ("board accept sent", r"taker: accept .* on \S+ for"),
    ("dealer: skip every buy (none affordable)", r"skip every dealer buy"),
    ("dealer: open allowed", r"open thread with \S+ for .*guardrails allowed"),
    ("dealer: open denied (approval)", r"open thread with .*needs human approval"),
    ("dealer: walk at official value", r"walk .*official value"),
    ("dealer: pícaros switched card (ignored)", r"picaros offer ignored"),
    ("swap: jev not yes", r"swap with t\d+ not sent: jev"),
    ("swap: proposal sent", r"swap proposal \d+ to t\d+"),
    ("swap: cancel loop", r"cancel of offer \d+ refused"),
    ("swap: walk no deal", r"close thread \d+ with t\d+: no deal"),
    ("swap: blocklist walk", r"team_desk_never_trade"),
    ("kill switch hold", r"kill switch on"),
    ("taker started", r"taker: taker started"),
]


def main(root: str, prefix: str) -> None:
    seen = set()
    counts: dict[str, Counter] = defaultdict(Counter)
    for f in sorted(Path(root).glob(f"{prefix}.*.log")):
        sim = False
        for line in f.read_text(errors="replace").splitlines():
            if line.startswith("target: SIMULATOR"):
                sim = True
            m = re.match(r"^tick (\d+) ", line)
            if sim or not m or (line in seen):
                continue
            seen.add(line)
            tick = int(m.group(1))
            w = next((n for n, a, b in WINDOWS if a <= tick <= b), None)
            if w is None:
                continue
            for name, pat in RULES:
                if re.search(pat, line):
                    counts[name][w] += 1
    names = [n for n, _, _ in WINDOWS]
    print("| line | " + " | ".join(n.split()[0] for n in names) + " | total |")
    print("|---" * (len(names) + 2) + "|")
    for name, _ in RULES:
        row = [counts[name][n] for n in names]
        print(f"| {name} | " + " | ".join(str(x) for x in row) + f" | {sum(row)} |")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
