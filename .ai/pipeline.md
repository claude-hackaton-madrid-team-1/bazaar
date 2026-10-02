# Agent lifecycle (GENERIC — reusable across projects)

> The generic addyosmani/agent-skills lifecycle. Project-specific contract lives in
> `.ai/context.md`. `sh scripts/sync-ai-docs.sh` assembles both into the generated entry files.

## Per-task pipeline (MANDATORY)
The lifecycle runs once PER TASK — one task = one vertical slice = one trip through these steps.
First fix the task's identity, then run the loop (skills from addyosmani/agent-skills):
0. **Identify** — fix the task id and its spec source (see `.ai/context.md` → "Task identity &
               spec source"). External backlog present (Linear / GitHub Projects, detected via
               `gh project list` or a Linear MCP / `LINEAR_API_KEY`) → the task id is the
               EXTERNAL id and the ticket is the spec. Otherwise → a local `A1/A2/…` id with the
               spec saved at `.ai/specs/<id>-spec.md`.
1. `/spec`   — write the per-task spec to `.ai/specs/<id>-spec.md` (local id), OR read & cite the
               external ticket (skill: spec-driven-development). If the task is already fully
               covered by the approved `.ai/specs/01-spec.md`, cite the section instead.
2. `/plan`   — decompose the task into dependency-ordered steps (skill: planning-and-task-breakdown);
               record them under the task's id in the `.ai/specs/02-plan.md` backlog/index.
3. `/build`  — implement ONE thin step at a time (skill: incremental-implementation). Append
               every error hit to `.ai/memory.md` as `symptom → root cause → fix`. Close the task
               with an **Honest Implementation Report** (see `.ai/context.md` → "Honesty
               protocol"): per-criterion status + PASTED evidence, the Honest Implementation
               Metric %, and the Unverified / Could-not-do lists. Over-claiming is a violation.
4. `/test`   — red-green-refactor, prove with evidence, with the project's test runner
               (skill: test-driven-development). Reuse fixtures; no dup tests.
               Record out-of-scope items in `.ai/specs/98-nice-to-haves.md`.
5. `/review` — self-review against the five-axis checklist
               (skill: code-review-and-quality). Push back on nested ifs >2,
               functions >50 lines, duplication, over-abstraction, bad names.
Repeat per task until the backlog is clear.

## Project-level finish (run ONCE, after all slices)
6. `/consensus-review` — parallel fan-out: code-reviewer + security-auditor +
   performance-reviewer; require 2-of-3 APPROVE; write `.ai/specs/03-review.md`.
   (This is the "parallel fan-out" stage — it lives here, not in `/ship`.) Run the three
   reviewers as parallel sub-agents — an Agent Team in Claude Code, the tool's native
   sub-agents elsewhere (see "Parallel work" below). Structure the panel + synthesis with the
   `six-thinking-hats` skill (⚫/⚪ Black+White audit → 🟡/🟢 Yellow+Green synthesis → 🔵 Blue verdict).
7. `/code-simplify` — apply reviewer findings (Chesterton's Fence).
8. `/ship` — Conventional, atomic commits ONLY. NEVER push, NEVER open a PR,
   NEVER touch a remote. Print `git log`, clean tree, and a ready-to-paste PR body.
9. `/acceptance` (custom) — write `.ai/specs/99-acceptance.md`: per-requirement
   traceability matrix (requirement → file:line → test → ✅/⚠️/❌), aggregating each task's
   Honest Implementation Report (per the Honesty protocol), evidence, gaps, one-line verdict.
   ✅ requires BOTH implementation AND a passing test — with the proving output pasted.
10. `/goal` (built-in completion loop — the closer) — keep working across turns until
    acceptance holds; do NOT author a `goal.md`. Completion is local commits + green
    acceptance, never a push.

## Continuous evolution (OPTIONAL — all CLIs)
11. `/evolve` (skill: evolve) — periodically, or after a big feature, scan the repo and
    keep the `.ai/` source-of-truth honest. It diffs **code-reality vs `.ai/context.md` +
    `.ai/specs/`** across five dimensions and writes proposed patches to
    `.ai/specs/97-evolution.md`. It PROPOSES, never applies — a human reviews and applies,
    then re-runs `sh scripts/sync-ai-docs.sh`. On **Claude Code** it accelerates with a `/graphify`
    knowledge graph; **other CLIs** (and Claude Code without graphify) fall back to a direct scan.
    Either way the per-dimension drift checks fan out across native sub-agents. Same report.

## Parallel work — Agent Teams (Claude Code) / sub-agents (generic tools)
Same idea, named per tool, and **language-agnostic** — no committed scripts, just each tool's
native parallelism. In **Claude Code**, launch an **Agent Team** — parallel teammates
coordinating via the shared task list / SendMessage. In **generic agents** (Codex, Gemini,
opencode) launch the tool's **sub-agents** — same intent. USE any of these for:
- a concurrency-sensitive module (one teammate proves ordering/correctness, one hunts races);
- the `/consensus-review` 2-of-3 panel (code-reviewer + security-auditor + performance-reviewer);
- independent, file-disjoint slices built in parallel (e.g. app/routes vs core vs workers vs db).
ALWAYS brief each teammate/sub-agent with FRESH, verified context — the relevant
`.ai/specs/` section, the actual source files to read, and confirmed library APIs (not
guesses). Each writes ONLY its own files; the lead then runs the integrated lint + typecheck +
test suite and a `/review` pass before any commit. NO stale info — make them read ground truth.
DON'T use a team/Workflow for single-file edits, trivial changes, or doc tweaks — the 3–5×
token cost isn't worth it; one focused agent is better there.
