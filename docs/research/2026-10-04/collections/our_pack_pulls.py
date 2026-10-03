"""Our own pack pulls: diff /me card asset ids across each tick where a sealed pack disappeared.

Read-only. Usage (DATABASE_URL in env): uv run python our_pack_pulls.py
Prints per opened pack: pack type, the new assets (ref, rarity, serial), and luck before/after.
Values are NOT printed (private).
"""
import json
import os

import psycopg

with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
    rows = conn.execute(
        "select tick, packs, cards, score from me_snapshots where team='t01' order by tick"
    ).fetchall()

prev = None
for tick, packs, cards, score in rows:
    if prev is not None:
        ptick, ppacks, pcards, pscore = prev
        gone = {p["asset"]: p["pack"] for p in ppacks} .keys() - {p["asset"] for p in packs}
        if gone:
            before = {c["asset"] for c in pcards}
            new = [c for c in cards if c["asset"] not in before]
            for a in gone:
                kind = next(p["pack"] for p in ppacks if p["asset"] == a)
                print(json.dumps({
                    "tick": tick, "pack": kind, "pack_asset": a,
                    "new_cards": [(c["ref"], c["rarity"], c["serial"]) for c in sorted(new, key=lambda c: c["asset"])],
                    "luck": [pscore.get("luck"), score.get("luck")],
                }))
    prev = (tick, packs, cards, score)
