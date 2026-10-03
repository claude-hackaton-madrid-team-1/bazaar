"""Replay the duel days latch's evidence functions on Saturday's real Duels II payloads (read-only).

Asks agents.duel_days.evidence (the days_meaning text) and scored_evidence (how the game scored a deal)
what each real session-3 payload says about the sign of `your_days_weight`, per role.

    uv run python docs/research/2026-10-04/dealing/days_latch_replay.py
"""

import sys
from collections import Counter
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from q import url  # noqa: E402

from bazaar_agent.agents.duel_days import evidence, scored_evidence  # noqa: E402

with psycopg.connect(url()) as conn:
    conn.read_only = True
    rows = conn.execute("select payload from duels where session = 3 order by duel").fetchall()
text, scored = Counter(), Counter()
for (p,) in rows:
    text[(p.get("role"), evidence(p, real_game=True))] += 1
    scored[(p.get("role"), scored_evidence(p, real_game=True))] += 1
print("days_meaning texts:", sorted({p.get("days_meaning") for (p,) in rows}))
print("text evidence  (role, verdict): n", dict(text))
print("scored evidence (role, verdict): n", dict(scored))

# Replay the latch itself: feed the payloads to DaysSwitch.observe in the order the runner first saw them
# (deadline - 16 = the tick each duel appeared), as finished rows, and print every verdict change.
from bazaar_agent.agents.duel_days import DaysSwitch  # noqa: E402

switch = DaysSwitch(path=None)
ordered = sorted((p for (p,) in rows), key=lambda p: (p.get("deadline_tick", 0), p.get("duel", 0)))
last = switch.verdict
for p in ordered:
    switch.observe([p], real_game=True)
    if switch.verdict != last:
        print(f"duel {p['duel']} ({p['role']}, appeared tick {p['deadline_tick'] - 16}): {last} -> {switch.verdict}")
        last = switch.verdict
switch.session = 3
print(
    "final verdict:",
    switch.verdict,
    "| corroborated:",
    switch.corroborated,
    "| signed(allowed=True):",
    switch.signed(True),
)
