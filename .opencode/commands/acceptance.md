---
model: openrouter/openai/gpt-5.4
description: "Write .ai/specs/99-acceptance.md — per-requirement traceability matrix (requirement → file:line → test → ✅/⚠️/❌)"
---

# /acceptance — traceability & gate (pipeline step 9)

Produce `.ai/specs/99-acceptance.md`: prove every requirement in `00-requirements.md` is both
implemented and tested. This is the project-level roll-up of each task's Honest Implementation
Report (see `.ai/context.md` → "Honesty protocol"). ✅ requires BOTH a `file:line` implementation
pointer AND a passing test — with the proving output PASTED, not merely asserted.

## Steps
1. Run the gate commands and capture their output — tests, coverage vs threshold, typecheck,
   lint (the exact commands from `.ai/context.md`'s Definition of Done). Paste the output.
2. For each requirement in `00-requirements.md`, fill a row:
   `| # | requirement | implementation (file:line) | proving test (+ pasted result) | ✅ / ⚠️ / 🔧 / ❌ / 🚫 |`.
   Reuse the per-task Honest Implementation Metrics — a requirement is ✅ only if its backing
   task reported ✅ with evidence. Carry the honest status through; do not upgrade it here.
3. Report the aggregate **Honest Implementation Metric**: `verified ✅ requirements ÷ total = NN%`.
4. List honest gaps & limitations (and every Unverified / Could-not-do item), each pointing to
   `98-nice-to-haves.md`.
5. End with a one-line verdict (GOAL MET / NOT YET).

## Notes
- Status legend: ✅ done & tested (evidence pasted) · ⚠️ done with a documented limitation ·
  🔧 stubbed/mocked · ❌ not done · 🚫 blocked.
- **Over-claiming is a contract violation.** Under-claiming (⚠️/❌/🚫 with a reason) is correct;
  never mark ✅ without pasted proof, and never invent output to make the matrix look green.
- This is the evidence step before `/goal` (the built-in completion loop) closes the work.
  `/goal` keeps iterating until this acceptance file is green — it does not author its own file.
