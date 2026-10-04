# CI1: four Depot checks

- Backlog source: local `.ai/specs/02-plan.md`.
- Requested by Omar, 2026-10-04: CI runs unit tests, integration tests, formatter and linter only.
- Vendor rules and README reviewed. This task makes no game requests or runtime changes.

## Scope

Use `.depot/workflows/tests.yml` as the sole PR test workflow. Split pytest through the
`integration` marker; integration tests use an isolated Postgres 17 + pgvector service.
Use Black for the formatter check and Ruff for the linter check. Remove automated simulator
smoke; keep its script for optional manual diagnosis. Remove all GitHub workflow duplicates; retain
the Depot README writer only after main pushes or manual dispatch. Local typechecking, coverage and generated
document requirements stay unchanged.

## Acceptance and Honest Implementation Report

| Criterion | Status | Evidence |
|---|---|---|
| Depot runs exactly unit tests, integration tests, formatter and linter | Verified | `.depot/workflows/tests.yml`; config assertion: `CI1 config: four checks; no GitHub duplicates, sim-smoke, PR writer or Blacksmith` |
| Both pytest suites execute; integration tests use isolated Postgres | Verified | Unit: `5205 passed, 1 skipped, 146 deselected, 2 xfailed, 42 subtests passed in 105.28s`; integration: `146 passed, 5207 deselected in 33.95s` ([Depot](https://depot.dev/orgs/hx8hrw3646/workflows/25xp4cng8m)); final four holdings synchronization fixes: `26 passed in 12.07s` on private Postgres |
| Automated sim smoke and duplicate GitHub workflows are removed | Verified | Config assertion above; no `.github/workflows/*.yml` or `.depot/workflows/sim-smoke.yml`; README writer triggers only on main pushes/manual dispatch |
| Documentation states the four checks and optional manual smoke | Verified | `.ai/context.md`, README and reviewer contract updated; `sync-ai-docs: regenerated`; `git diff --check` exit 0 |

Local gates: `All checks passed!`; `677 files already formatted`; `445 files would be left unchanged.`;
`Success: no issues found in 206 source files`; `Required test coverage of 80% reached. Total coverage: 92.33%`.

Review: independent code and security reviews found no remaining P0/P1 issues after fixes. The first
integration run exposed password-auth configuration and two test timing/output assumptions; fixes
are recorded in `.ai/memory.md`. No runtime behavior was changed.

Honest Implementation Metric: 4/4 criteria verified, 100%.

Unverified: the unit suite's one skipped live-thread subtest and two expected failures; automatic
execution after merge. Final PR checks are reported on the PR itself.

Could-not-do: none within the implementation scope. Deployment/merge is not part of this change.
