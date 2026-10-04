# AT1: Remove amount approval and hourly trading ceilings

Source: Omar's explicit request on 4 October to trade without a human amount-approval blocker and remove the 250 P/hour cap. Local backlog. This overrides the previous 60 P amount approval requirement.

## Acceptance

1. `GUARDRAILS.md` sets `human_approval_above = 0` and `max_spend_per_game_hour = 0`, with documented disabled semantics.
2. Zero hourly cap permits eligible purchases and plans above the old ceiling without producing zero/negative planning room. Positive configured caps continue to work.
3. Disabled amount approval does not access the approval service. Cash, pending commitments, inventory, value/impact, tick and shared request limits remain effective. Explicit operator review and human-created buy targets are separate workflows.
4. Update current operating instructions, run focused/full tests and independent review, and create a separate PR.

No market manipulation, sole-page-copy sale override, model/provider change, or removal of the separate team-swap budget is requested. No schema change.

## Plan

Parallel runtime implementation, independent budget-path audit and safety review. Coordinator owns rule values/docs, integrated validation and PR. LF1 deployment verification continues separately. Use the deploy guard if the previously authorized coordinator rollout proceeds.
