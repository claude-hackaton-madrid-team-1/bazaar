# TR1 — Prioritize negotiation surplus over album proximity

Source: local backlog. Omar asks agents to acquire inputs for profitable team negotiation and swaps, rather than collect pages as the objective. Completing a page alone never scores; complete-page protections and price floors remain mandatory.

## Plan and acceptance

1. Search all eligible swaps once and rank their expected exchange surplus before album proximity or rival rank. Existing card/cash/fee valuation, freshness, fairness, blocklist and guards remain authoritative.
2. After accepting and protecting Workshop inputs, run team conversations before dealer counter-messages; drop remaining dealer work when the tick expires. Dealer opening order remains unchanged to preserve slot accounting.
3. Verify actor behavior and safety with focused tests, then independent review and the coordinator-owned full gate/CI.

## Evidence

Real database snapshots: at tick1824 neg_points increased 0→3.5. Trade outcome settlement1301 identifies RET-07 bought from t02 for14 against card value17.5. Its local evaluator `score=0.2` is a surplus ratio, not the official scoreboard delta. The earlier observed −100.2 at1466 is the new-round reset, not a trade loss.

Implementation: TeamDesk._trades uses one unrestricted-page plan; _priority puts Trade.expected first. Page information remains valuation context and equal-gain tie-breaking. Taker retains accepts first and Workshop-before-team-promises, then gives team messages priority over dealer messages. No new API calls or model calls.

Verified focused output: `158 passed in 1.08s` (taker, team_desk, team_desk_jev, team_desk_partners, team_desk_blocklist). Actor regression opens higher expected-gain t10 even when t08 answered before and ranks weaker; another regression exhausts the tick during team negotiation and verifies dealer_bid is expired with no send.

Honest implementation: 2/3 acceptance criteria verified (67%); criterion3 partial until independent review and full gate/CI. Unverified: production fill rate, additional negotiation points and rollout. Could-not-do: no live writes/deployment performed; coordinator owns rollout.
