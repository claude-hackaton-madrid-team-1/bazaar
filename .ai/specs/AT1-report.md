# AT1 implementation report

Omar explicitly requested removal of the amount-based human approval threshold and global 250 P/hour purchase ceiling. This overrides the earlier 60 P approval requirement. Scope: [AT1-spec.md](AT1-spec.md).

| Acceptance | Status | Evidence |
|---|---|---|
| Checked-in disabled limits | Verified | `GUARDRAILS.md` sets both values to 0. Independent final-config tests pass. |
| Zero-cap planning and positive-cap compatibility | Verified | `Guardrails.spend_room` bounds plans by available cash; positive caps additionally bound spend. Dealer openings, probes, continuation, trade plans and human-tool feasibility use these semantics. Independent changed-module run: `318 passed in 4.00s`. |
| Other trading safeguards and no amount-approval dependency | Verified | Tests cover cash commitments, protected copies, floors, positive configured thresholds and no approval-board access at threshold 0. Independent security suites: `216 passed`, final-config `65 passed`, approval/probe `72 passed`; these sets overlap. Operator proposal confirmation remains explicit. |
| Guidance and separate PR | Partial | Contract and operating skills updated and generated docs synced. Full integrated gate and separate PR are the coordinator's remaining publication steps, recorded in the PR. |

Implementation metric before publication: **3/4, 75%**. Publication and final full-gate evidence will be in the PR; no deployment is claimed here.

## Design

Use the existing zero/off convention for amount approval. Give the hourly cap the same explicit zero/off meaning, rather than an arbitrary large number. Cash and pending commitments always bound planning. Positive values still enable the former protections for other configurations.

The shared ledger still records spend and reservations. Disabling the hourly ceiling does not erase its audit trail or allow double commitments. Separate team-swap cash limits, score-impact checks, human-created buy targets and reviewed operator proposals are outside this request.

Runtime work, path audit and safety review ran in parallel. The integrated full suite runs alone to avoid interference with the local Postgres fixture.

## Unverified

- Final full-suite and hosted CI results until recorded in the PR.
- Production behavior until a guarded rollout. No score or fill-rate improvement is claimed.

## Could-not-do

- No implementation access limitation. Live event windows still govern any coordinator deployment.
