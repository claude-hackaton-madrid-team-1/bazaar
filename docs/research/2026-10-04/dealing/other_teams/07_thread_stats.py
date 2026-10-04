"""Aggregate 06_dealer_threads_clean.sql output (TSV on stdin).

Deal rate and price gain per dealer x side x class, per team.
"""

import csv
import statistics as st
import sys
from collections import defaultdict

rows = list(csv.DictReader(sys.stdin, delimiter="\t"))


def num(x):
    return int(x) if x not in ("", None) else None


for r in rows:
    r["fill"], r["d_open"], r["d_last"], r["t_steps"] = (
        num(r["fill"]),
        num(r["d_open"]),
        num(r["d_last"]),
        num(r["t_steps"]),
    )
    if r["fill"] is not None and r["d_open"]:
        r["gain"] = (1 - r["fill"] / r["d_open"]) if r["side"] == "buy" else (r["fill"] / r["d_open"] - 1)
    else:
        r["gain"] = None
    # silent = the team never priced
    r["silent"] = (r["t_steps"] or 0) == 0


def summary(group):
    n = len(group)
    deals = [g for g in group if g["fill"] is not None]
    gains = [g["gain"] for g in deals if g["gain"] is not None]
    steps = [g["t_steps"] for g in deals if g["t_steps"] is not None]
    return (
        n,
        len(deals),
        round(100 * len(deals) / n) if n else 0,
        round(100 * st.mean(gains), 1) if gains else "",
        round(100 * st.median(gains), 1) if gains else "",
        round(st.mean(steps), 1) if steps else "",
        sum(1 for g in group if g["silent"]),
    )


mode = sys.argv[1] if len(sys.argv) > 1 else "dealer"
key = {
    "dealer": lambda r: (r["dealer"], r["side"]),
    "dealer_cls": lambda r: (r["dealer"], r["side"], r["cls"] or "-"),
    "team": lambda r: (r["team"],),
    "team_dealer": lambda r: (r["dealer"], r["side"], r["team"]),
}[mode]
groups = defaultdict(list)
for r in rows:
    groups[key(r)].append(r)
print(
    "\t".join(
        [
            "key",
            "threads",
            "deals",
            "deal_pct",
            "mean_gain_pct",
            "median_gain_pct",
            "mean_team_steps_in_deals",
            "threads_team_never_priced",
        ]
    )
)
for k in sorted(groups):
    print("\t".join([" ".join(k)] + [str(x) for x in summary(groups[k])]))
