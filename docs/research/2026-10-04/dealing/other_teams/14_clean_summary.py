"""Summarise 13_clean_attribution.sql (TSV on stdin).

Per kind x via x rarity class: n, mean/median of (d_neg - field_med).
"""

import csv
import statistics as st
import sys
from collections import defaultdict

rows = list(csv.DictReader(sys.stdin, delimiter="\t"))
g = defaultdict(list)
for r in rows:
    via = r["via"] if r["kind"].startswith("dealer") else ("rastro" if r["via"] == "rastro" else "team venue")
    cls = r["items"].split("+")[0].split("/")[-1]  # c/u/r/e/p
    if "+" in r["items"]:
        cls = "multi"
    g[(r["kind"], via, cls)].append(float(r["d_neg"]) - float(r["field_med"]))
print("kind\tvia\tclass\tn\tmean_excess\tmedian_excess\tmin\tmax\tshare_positive")
for k in sorted(g):
    v = g[k]
    print(
        "\t".join(
            map(
                str,
                [
                    *k,
                    len(v),
                    round(st.mean(v), 2),
                    round(st.median(v), 2),
                    round(min(v), 2),
                    round(max(v), 2),
                    round(sum(x > 0.05 for x in v) / len(v), 2),
                ],
            )
        )
    )
