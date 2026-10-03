"""Parse Railway deployment logs (taker / maker / duels) into per-tick coverage and blocker counts.

Input: a directory of files named <service>.<HHMMSS>.<deploy8>.log, fetched read-only with
`railway logs -s <service> --lines 5000 <deployment-id>` (one file per Saturday deployment).
Output: per-service JSON summary on stdout. Game ticks of the real game only (lines after a
`target: SIMULATOR` banner are counted apart).
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TICK = re.compile(r"^tick (\d+) (\S+?):? (.*)$")
SUMMARY = re.compile(r"(\d+) accept candidate\(s\), (\d+) taken, (\d+) dealer thread\(s\)")
NUM = re.compile(r"\d+(?:\.\d+)?")


def shape(text: str) -> str:
    text = re.sub(r"[A-Z]{3}-\d+", "CARD", text)
    text = re.sub(r"\bt\d\d\b", "tNN", text)
    return NUM.sub("N", text)[:140]


def main(root: str) -> None:
    out: dict = {}
    for f in sorted(Path(root).glob("*.log")):
        service, hhmmss, dep = f.name.split(".")[:3]
        s = out.setdefault(service, {"deploys": [], "ticks": defaultdict(Counter), "sim_lines": 0,
                                     "shapes": Counter(), "errors": Counter()})
        sim = False
        first = last = None
        lines = f.read_text(errors="replace").splitlines()
        for line in lines:
            if line.startswith("target: SIMULATOR"):
                sim = True
            if sim:
                s["sim_lines"] += 1
                continue
            if "Traceback" in line or re.search(r"\b(Error|Exception)\b", line):
                s["errors"][shape(line)] += 1
            m = TICK.match(line)
            if not m:
                continue
            tick, who, rest = int(m.group(1)), m.group(2), m.group(3)
            first = tick if first is None else min(first, tick)
            last = tick if last is None else max(last, tick)
            c = s["ticks"][tick]
            c["lines"] += 1
            sm = SUMMARY.search(rest)
            if sm:
                c["summary"] += 1
                c["cands"] = max(c["cands"], int(sm.group(1)))
                c["taken"] = max(c["taken"], int(sm.group(2)))
                c["threads"] = max(c["threads"], int(sm.group(3)))
            else:
                s["shapes"][f"{who} {shape(rest)}"] += 1
        s["deploys"].append({"at": hhmmss, "id": dep, "lines": len(lines), "first": first, "last": last, "sim": sim})
    for service, s in out.items():
        s["ticks"] = {t: dict(c) for t, c in sorted(s["ticks"].items())}
        s["shapes"] = s["shapes"].most_common(80)
        s["errors"] = s["errors"].most_common(30)
    json.dump(out, sys.stdout, indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
