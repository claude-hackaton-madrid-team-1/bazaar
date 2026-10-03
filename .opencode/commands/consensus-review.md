---
model: openrouter/openai/gpt-5.5
description: "Parallel 2-of-3 reviewer fan-out (code-reviewer + security-auditor + performance-reviewer), structured as a Six-Hats deliberation; writes .ai/specs/03-review.md"
---

# /consensus-review — project-level finish gate

Run the parallel fan-out review panel over the completed feature (pipeline step 6). This is
the "parallel fan-out" stage — it lives here, not in `/ship`. Structure the panel and the
synthesis with the `six-thinking-hats` skill: the reviewers wear the ⚫ Black / ⚪ White lenses on
their axis; the lead adds 🟡 Yellow + 🟢 Green + 🔵 Blue in synthesis.

## Steps
1. 🔵 **Blue (frame).** State what's under review and the pass/fail criteria. Gather ground truth:
   the diff/branch, the relevant `.ai/specs/` sections, and the actual source files. Brief each
   reviewer with verified context — never guesses.
2. ⚫⚪ **Black + White (audit), in parallel.** Launch three reviewers **concurrently** (an Agent
   Team in Claude Code, or sub-agents in other tools), each in its own context, each judging its
   axis against evidence — not opinion:
   - `code-reviewer` — five-axis: correctness, readability, architecture, security, performance
   - `security-auditor` — vulnerability / OWASP-style audit
   - `performance-reviewer` — hot paths, allocations, I/O & query cost, throughput
   Reviewers do not call each other; each returns a verdict + findings (every risk with a concrete
   reason).
3. 🟡🟢 **Yellow + Green (synthesis).** Before gating, name what's *good and worth preserving*
   (Yellow — the Chesterton's-Fence handoff to `/code-simplify`: don't strip load-bearing
   complexity) and the alternatives / simplifications the panel surfaced (Green).
4. 🔵 **Blue (decide) — Gate: require ≥ 2-of-3 APPROVE** to pass. Any Critical finding blocks
   regardless of count. Consolidate into `.ai/specs/03-review.md`: the verdict table, the hat
   summary, per-finding disposition (`✅ FIXED` now vs `📋 DEFERRED` to `98-nice-to-haves.md` with
   rationale), evidence (gate-command output), and the next step. If the gate fails, remediate
   (often via `/code-simplify`) and re-run.

## Notes
- Use a team / sub-agents only here and for genuinely independent work — not for trivial diffs.
- Each reviewer is read-only; the lead applies fixes and re-runs the integrated checks.
- For the full hat method (sequence, AI adaptations, graceful degradation), see the
  `six-thinking-hats` skill.
