# LF1 — Close live-readiness gaps during Sunday play

Source: Omar's request to finish the remaining plan after the guarded SR1 rollout.
Backlog is local. Presentation work remains withdrawn.

## Confirmed gaps

- At ticks 1522–1537, the taker negotiated two dealer purchases before rejecting their final prices because hourly spend was already 232 P against the 250 P limit.
- The scoring skill still described the obsolete 250 P human-approval threshold and old page-card exceptions. Runtime enforces 60 P and protects all page-card single copies.
- SR1 and Live reports still described deployment as pending. Both deployments completed and writers resumed successfully.
- After the MAL-11 bid expired, the restart refund timestamp was estimated using the maximum tick length and fell outside the current spend window. Match the original ledger reservation when refunding; do not raise the cap or issue unproven credits.
- Production event delivery and voice playback need bounded verification; previous checks covered local fixtures and deployed health/auth boundaries.

## Acceptance

1. Dealer planning respects the remaining shared hourly spend budget before starting or continuing an unaffordable negotiation, without raising limits or discarding affordable concessions merely because an opening ask is high.
2. Regression tests cover insufficient budget, an affordable alternative, budget recovery and exact reservation/refund accounting across a restart and tick-length change. Existing duel policy is unchanged.
3. Reports and operational skills distinguish verified deployment from remaining limitations and use current hard guardrails.
4. Record actual current scoring/activity blockers and production voice validation results. Do not claim a trade, score gain, microphone input or event category that was not observed.

## Execution

Parallel owners: runtime diagnosis and budget fix; live scoring audit and independent review; production voice validation. Root integrates documentation and runs the final gate. No hand trade or new model/provider/credential change. Any code rollout uses green CI, independent review and the live deploy guard.
