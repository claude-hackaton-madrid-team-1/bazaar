"""Replay Saturday's real Duels II payloads (session 3) through the per-role, per-session days latch (read-only).

Feeds each payload in the order the runner first saw it (deadline - 16), first as a live row, then as a finished
row, and prints every verdict change; then shows that a session-4 live duel starts fresh.

    DATABASE_URL=... uv run python docs/research/2026-10-04/duel-days-fix/replay_by_role.py
"""

import os
import tempfile
from pathlib import Path

import psycopg

from bazaar_agent import guardrails as gr
from bazaar_agent.agents import duel_days as dd

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.read_only = True
    rows = [p for (p,) in conn.execute("select payload from duels where payload->>'session' = '3'").fetchall()]

rules = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
with tempfile.TemporaryDirectory() as tmp:
    latch = dd.latch(Path(tmp))
    last, armed = latch.verdict, None
    for p in sorted(rows, key=lambda p: (p.get("deadline_tick", 0), p.get("duel", 0))):
        appeared = p.get("deadline_tick", 0) - 16
        latch.observe([{**p, "status": "live"}], True)
        latch.observe([{**p, "status": "live"}, p], True)
        if latch.verdict != last:
            print(f"duel {p['duel']} ({p['role']}, appeared tick {appeared}): {last} -> {latch.verdict}")
            last = latch.verdict
        if armed is None and gr.days_signed_for(dd.effective_rules(rules, latch), "seller"):
            armed = (p["duel"], p.get("deadline_tick"))
    print("final:", latch.verdict, "| seller signed (auto) after duel/deadline:", armed)
    sunday = dd.latch(Path(tmp))
    sunday.observe([{**rows[0], "session": 4, "status": "live", "days_meaning": None}], True)
    print("session 4, first live duel (no text):", sunday.verdict)
