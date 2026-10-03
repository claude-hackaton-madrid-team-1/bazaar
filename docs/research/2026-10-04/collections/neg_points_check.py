"""Reconcile /me neg_points changes with each of our settlements (team and dealer).

Read-only. For every settlement with t01 as a party: the traded asset's your_value in the /me snapshot just
before (a sale) or just after (a buy), price, fee, and the neg_points change across the settlement tick.
Prints the residual  dNeg - (value - price [- fee])  for a buy, dNeg - (price - value) for a sale.
Usage (DATABASE_URL in env): uv run python neg_points_check.py [--private]   (values printed only with --private)
"""
import os
import sys

import psycopg

private = "--private" in sys.argv
with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    snaps = conn.execute(
        "select tick, cards, (score->>'neg_points')::float from me_snapshots where team='t01' order by tick"
    ).fetchall()
    sets = conn.execute(
        "select tick, payload from feed_events where type='settlement' and payload->'parties' ? 't01' order by tick"
    ).fetchall()


def snap_at(tick, after):
    rows = [s for s in snaps if (s[0] >= tick if after else s[0] < tick)]
    return (rows[0] if after else rows[-1]) if rows else None


for tick, p in sets:
    for it in p.get("items", []):
        if it.get("kind") != "card":
            continue
        buy = it.get("to") == "t01"
        before, after = snap_at(tick, False), snap_at(tick, True)
        if not before or not after:
            continue
        src = after if buy else before
        val = next((c.get("your_value") for c in src[1] if c["asset"] == it["id"]), None)
        price, fee = p.get("price", 0), p.get("fee", 0)
        dneg = round(after[2] - before[2], 2)
        cp = p.get("persona") or [x for x in p["parties"] if x != "t01"][0]
        if val is None:
            res = "value n/a"
        elif buy:
            res = f"resid(no fee) {dneg - (val - price):+.1f} resid(fee) {dneg - (val - price - fee):+.1f}"
        else:
            res = f"resid {dneg - (price - val):+.1f}"
        v = f"value {val}" if private else ""
        print(f"t{tick} {'BUY ' if buy else 'SELL'} {it['ref']:7} {cp:8} price {price:3} fee {fee} dNeg {dneg:+7.1f} {v} {res}"
              f" (snaps {before[0]}->{after[0]})")
