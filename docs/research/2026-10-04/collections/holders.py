"""Who holds the cards we miss: replay of the public feed (settlements, gifts, taller crafts, pack 'best').

Read-only. LOWER BOUND: commons/uncommons pulled from packs are invisible in the feed until they trade.
Per card: known team holders (copy count) at the end of the feed, dealer stock is not modelled.
Usage (DATABASE_URL in env): uv run python holders.py LAT-04 LAT-05 ...
"""
import os
import sys
from collections import Counter, defaultdict

import psycopg

want = set(sys.argv[1:])
DEALERS = {"abuela", "chato", "pilar", "picaros", "banco"}
with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    names = {n: i for i, n in conn.execute("select id, name from cards")}
    ev = conn.execute(
        "select tick, type, payload from feed_events where type in ('settlement','gift.given','taller.crafted','pack.opened','egg.given') order by tick, id"
    ).fetchall()

hold: dict[str, Counter] = defaultdict(Counter)  # ref -> team -> copies
asset_owner: dict[int, str] = {}
for tick, typ, p in ev:
    if typ == "settlement":
        for it in p.get("items", []):
            if it.get("kind") != "card":
                continue
            ref, frm, to, aid = it.get("ref"), it.get("frm"), it.get("to"), it.get("id")
            if frm and frm not in DEALERS:
                hold[ref][frm] -= 1
            if to and to not in DEALERS:
                hold[ref][to] += 1
            asset_owner[aid] = to
    elif typ == "gift.given":
        for ref in p.get("cards", []):
            hold[ref][p["team"]] += 1
    elif typ == "taller.crafted":
        ref = names.get(p.get("card"))
        if ref:
            hold[ref][p["team"]] += 1
    elif typ == "pack.opened":
        b = p.get("best")
        if b and b.get("id") not in asset_owner:
            hold[b["ref"]][p["team"]] += 1
            asset_owner[b["id"]] = p["team"]

for ref in sorted(want):
    teams = {t: n for t, n in hold[ref].items() if n > 0 and t != "t01"}
    neg = {t: n for t, n in hold[ref].items() if n < 0 and t != "t01"}
    print(ref, "known holders:", " ".join(f"{t}x{n}" if n > 1 else t for t, n in sorted(teams.items())) or "-",
          "| net-negative (pulled unseen, then sold):", " ".join(sorted(neg)) or "-")
