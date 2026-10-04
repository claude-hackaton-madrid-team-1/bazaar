# TT2: Execute profitable asks despite an unfilled lower bid

Source: Omar requests autonomous profitable trades. At live ticks 1813–1814, RET-07 asks at 14 P were rejected solely because our unfilled bid was 11–12 P. Local backlog.

## Acceptance

1. An economically valid public or addressed ask remains a candidate even when our own bid is cheaper. Fees, official value, minimum surplus, shared accept quota and existing guards remain enforced.
2. The bid's cash and counterparty exposure remain committed until cancellation succeeds. Only the expected card of that exact still-open bid is excluded from synthetic holdings; actual holdings and accepted/queued purchases still block duplicates.
3. A confirmed ask acceptance uses the existing guarded bid cancellation. An uncertain cancellation never refunds spend. No extra game reads or model calls.
4. Focused worker-path tests cover public/addressed execution, 15-second ticks, constrained cash and a bid that starts settling between reads. Independent review and green CI precede coordinator-controlled rollout.

## Implementation

Remove the own-bid price veto in `ask_candidates`. Build the accept guard context with all commitments, then remove only the still-open replacement bid's expected card from synthetic holdings. Keep the cancellation after confirmed acceptance.

## Verification

Focused tests and static checks are recorded in the PR. Full integration, CI and deployment remain coordinator-owned. No live operation or fill is claimed by this implementation.
