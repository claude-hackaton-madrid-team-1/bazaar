# P1 — Sunday presentation (40% of judging) + decision log  (per-task spec)

- Task id: P1 (migrated from GitHub issue(s) #16)
- Priority: P0
- Status: ⬜ Sunday. Material: Jev decision logs with floats, evals, the architecture artifact, Bazaar Live, docs/pitch-notes.md (N15).
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
A presentation that wins the judges' 40%: strategy, how we built it (harness, Jev, Railway, learner), what we learned, with live evidence.

## Acceptance criteria (each MUST be testable)
- [ ] 1. A deck (Slides) with the strategy, the architecture, three decisions Jev made with their floats, the scores over time, and lessons.
- [ ] 2. A live demo path: Bazaar Live + a Phoenix replay of one negotiation (N18).
- [ ] 3. Rehearsed once before Sunday 13:30.

## Source (the original issue text, verbatim)

### #16 — [pitch] Sunday presentation (40% of judging) + decision log

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/16 on 2026-10-03 (closed there).

## Context
From the kickoff (PR #6, `docs/transcripts/2026-10-02-hackathon-kickoff.md`):
- *"So that's the 40% for us"*: **40% of the judging is Sunday's presentation**. What strategy we follow, how we developed it, what we learned.
- *"we will also take a more promising look at the draft itself. How have we solved the problems?"*: they also look at **how** it's built.

It's not just the leaderboard.

## What to do
- **Decision log** starting today: every strategy change with date, reason and data (e.g. duel share before and after a policy change).
- Save leaderboard snapshots and our per-round metrics to show the evolution (Friday as learning with weight 0.5, Saturday and Sunday as execution).
- Prepare the narrative:
  1. How we discovered the rules (reverse-engineering of bundles and API).
  2. The architecture: deterministic code for whatever is binding, LLM only for the text.
  3. The key learnings.
  4. What we would do differently.
- Save charts: share per duel, bench efficiency versus the auto stall, and ladder per level.
- Demo slot: the live bot + the dashboard (#15).

## Acceptance criteria
- [ ] `docs/decisions.md` (or equivalent) updated on every strategy change.
- [ ] Deck ready before Sunday's close (h22.8 announces the end).
- [ ] One run-through rehearsal with the team.

**Comment by serban-marius:**

**Pitch angle, taken from the kickoff audio and slides:**
- Causa Prima is *"an agent-to-agent network for finance teams"* focused on **invoices**: *"as soon as it's going to be agent to agent, we can solve these problems [...] we want to learn with what you guys come up with"*. The CEO (Max) speaks on Saturday and will probably judge.
- The judges value *"your ideas and your craft"* and *"how have we solved the problems"*, not just the score.
- **Proposal:** present our architecture as a pattern for agent-to-agent invoice negotiation:
  1. structure is binding and the LLM only drafts;
  2. private limits that are never crossed;
  3. distrust of the counterparty (#24);
  4. offer inspector (#10);
  5. auditing and rate limits.
- Material we already have for the story: the reverse-engineering of the bundles (#17), the feed that exposes other teams' offers (#21), the scarcity map (#22) and the verified value formula (#23).

Full reference in `docs/briefing.md` (PR #25).

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, most of what's left is not code: the decision log, the deck and the rehearsal.

- ✅ Done:
  - Per-round snapshots exist: the `snapshots` table, written on every monitor tick.
- ❌ Missing:
  - `docs/decisions.md`, or an equivalent kept up to date on every strategy change.
  - Leaderboard history: nothing reads `/api/leaderboard`.
  - The deck and a rehearsal.
- Finding: in the `decisions` table, reason strings contain `[redacted]` in place of unrounded floats. The scrubber is producing false positives (`src/bazaar_agent/decisions.py:46`, which runs `telemetry.scrub` and the Jev masking). Fix it before the decision log goes on a slide.
- Material for the story:
  - The b0f630c fix: before it, 12 practice duels ended with no offer from us.
  - Four rival venues opened before ours.
  - No injection attempts in 3,436 public events.
  - #58 (open) gives us evidence of outcomes per decision: Friday duels mean 0.279 (20 scored), dealer mean 0.464, ladder L1 0.733 / L2 0, with Jev calibration per question.

## Draft scope (2026-10-03, coordinator brief — writing only)
Docs only, in `docs/pitch/`: `outline.md` (5-7 min, slide by slide, rehearsal Sunday 12:15 Madrid), `script.md` (speaker notes, ES + EN),
`claims.md` (every claim tagged REAL / SIMULATED / PENDING / UNVERIFIED), `demo.md` (90 s live path + backup recordings),
`evidence.md` (what to capture before scores freeze, with commands). No source code, no Railway, no merges, no new builder work.
Reuses Marius's kit (`story.md`, `qa.md`, `charts/`, the old demo moved to `demo-inventory.md`; stacked on PR #154),
`docs/pitch-notes.md`, the architecture artifact, README, `.ai/memory.md`, `bazaar evals`, Jev decision logs, `docs/observability.md` (PR #139).
One story: language negotiates, verifiable agreements execute, outcomes teach, Jev decides when to act. Three proofs: a real deal,
a deceptive offer stopped, a measured improvement. Omar presents, Marius backs up.

### Plan (steps, in order)
1. Research the evidence in parallel (proof 2 controls, proof 3 measurements + learner, capture commands) — sub-agents.
2. Write `claims.md` first (the ledger), then `outline.md` and `script.md` strictly from it.
3. Write `demo.md` and `evidence.md` from commands verified against the code.
4. Review: pr-reviewer (fresh context) + a claims audit (every number in script/outline appears in `claims.md` with a tag).
5. Saturday 12:00 first reviewable draft; after Saturday's evidence, replace every PENDING row and re-check Sunday morning.
