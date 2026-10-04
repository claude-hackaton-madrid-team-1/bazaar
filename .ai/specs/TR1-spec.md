# TR1 — Prioritize negotiation surplus over album proximity

Source: local backlog. Omar asks agents to acquire inputs for profitable team negotiation and swaps, rather than collect pages as the objective. Completing a page alone never scores; complete-page protections and price floors remain mandatory.

## Plan and acceptance

1. Search all eligible swaps once and rank their expected exchange surplus before album proximity or rival rank. Existing card/cash/fee valuation, freshness, fairness, blocklist and guards remain authoritative.
2. Preserve the existing dealer/team ordering and fresh commitment checks; no additional API/model calls. Message reordering is deferred because the later dealer context would need safe cash-promise reconciliation.
3. Replenishment cap30 permits the observed negotiated24 P final; pack checks must retain durable promised assets without reconciling them from stale snapshots.
4. Verify actor behavior and safety with focused tests, then independent review and the coordinator-owned full gate/CI.

## Evidence

Real database snapshots: at tick1824 neg_points increased 0→3.5. Trade outcome settlement1301 identifies RET-07 bought from t02 for14 against card value17.5. Its local evaluator `score=0.2` is a surplus ratio, not the official scoreboard delta. The earlier observed −100.2 at1466 is the new-round reset, not a trade loss.

Implementation: TeamDesk._trades uses one unrestricted-page plan; _priority puts Trade.expected first. Page information remains valuation context and equal-gain tie-breaking. Taker ordering is unchanged. No new API calls or model calls.

Verified focused output: `107 passed in 0.71s` (team_desk, team_desk_jev, team_desk_partners, team_desk_blocklist), after reverting message reordering. Actor regression opens higher expected-gain t10 even when t08 answered before and ranks weaker.

Honest implementation: 3/4 acceptance criteria verified (75%); criterion4 partial until independent review and full gate/CI. Unverified: production fill rate, additional negotiation points and rollout. Could-not-do: no live writes/deployment performed; coordinator owns rollout.

Pack slice evidence: `50 passed in 0.44s` (pack/supply), including a negotiated24 P acceptance under cap30 and unchanged durable reservations for stale/missing or already-synthetic assets. Config readiness: `12 passed in 0.60s`. Mypy2 source files clean; Ruff/Black/format7 files pass. Pack opening now folds pending asset IDs read-only; no reconciliation or extra API calls.
