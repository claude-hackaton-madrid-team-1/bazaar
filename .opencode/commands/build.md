---
model: openrouter/openai/gpt-5.4
description: Implement ONE task incrementally — build, test, verify, then an honest report
---

Invoke the agent-skills:incremental-implementation skill alongside agent-skills:test-driven-development.

Pick the task (by id) whose spec you opened in `/spec`. For each step in its `02-plan.md` entry:

1. Read the step's acceptance criteria.
2. Load relevant context (existing code, patterns, types).
3. Write a failing test for the expected behavior (RED).
4. Implement the minimum code to pass the test (GREEN).
5. Run the full test suite to check for regressions.
6. Run the build/typecheck to verify compilation.
7. Commit with a descriptive message that references the task id.
8. Mark the step complete in `.ai/specs/02-plan.md` and move to the next one.

If any step fails, follow the agent-skills:debugging-and-error-recovery skill and append the
`symptom → root cause → fix` to `.ai/memory.md`.

## Honest Implementation Report (MANDATORY — see "Honesty protocol" in the contract)
Close the task with this report. **Over-claiming is a contract violation; under-claiming is
fine.** Build the table from the task's acceptance criteria:

| # | Acceptance criterion | Status | Evidence (pasted command output / `file:line`) |
|---|----------------------|--------|-------------------------------------------------|
| 1 | …                    | ✅/⚠️/🔧/❌/🚫 | … |

- **Status legend:** ✅ verified · ⚠️ partial · 🔧 stubbed/mocked · ❌ not done · 🚫 blocked.
  A row is ✅ ONLY when the actual output proving it is pasted — no evidence means NOT ✅.
- **Honest Implementation Metric:** `verified ✅ ÷ total = NN%`. A task is done only at 100%
  with evidence on every row.
- **Unverified:** list anything you asserted but did not actually run/prove here.
- **Could-not-do:** list anything you could not perform (missing creds, no network, env limits,
  ambiguous spec). State it plainly — NEVER fabricate output or claim a run you did not execute.
