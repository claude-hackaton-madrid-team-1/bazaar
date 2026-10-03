---
description: "Review one pull request with the pr-reviewer sub-agent (Greptile replacement); fix every P0/P1 and re-run before asking for a merge"
---

# /pr-review <PR number> — the merge gate for every PR

Greptile is disabled. Every PR gets this review before its author asks for a merge.

1. Push your branch and open the PR first (the reviewer reads it from GitHub). Rebase on `origin/main`
   and resolve your own conflicts before you start.
2. Launch the **`pr-reviewer`** sub-agent in a FRESH context (never review your own diff in the context
   that wrote it): Agent tool with `subagent_type: pr-reviewer` in Claude Code, the tool's native
   sub-agent elsewhere. Brief it with the PR number and, when you have one, the task's spec.
3. When the diff touches money, keys or public surfaces (`src/bazaar_agent/agents/`, `guardrails*`,
   `ledger*`, `runtime/`, `sdk*`, `config*`, `status*`, `.railway/`, the simulator's key guard), launch
   **`security-auditor`** in parallel on the same PR. Merge both outputs: union the findings, keep the
   higher severity on a disagreement, and say which findings only one reviewer raised.
4. The reviewer posts its verdict on the PR. Fix every P0 and P1, push, and run `/pr-review` again until
   it answers APPROVE. P2: fix when cheap, else reply on the PR with the reason.
5. Ask for the merge (`orca orchestration ask` for workers) with the final verdict link and the list of
   P0/P1 findings with the commit that fixed each.

The reviewer is read-only: it never pushes, merges or changes Railway. Only the coordinator merges.
