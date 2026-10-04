# DOC1: repository documentation cleanup

- Backlog: local `.ai/specs/02-plan.md`.
- Requested by Omar, 2026-10-04: make the README readable and the architecture page reflect the implementation near the end of the hackathon.
- Vendor rules, README and Bazaar operator skill reviewed. No game requests, trades or deployment changes are required.

## Scope

Make the README the entry point for setup, development and navigation. Keep detailed operator
instructions in the documentation. Replace the old architecture roadmap with an implementation
map backed by current source files and deployment configuration. Describe configured services
without claiming current uptime. Keep generated documents reproducible and prevent automated
README refreshes from bringing back the backlog, memory log and activity feed.

Preserve runtime behavior, safety rules, vendor files and historical evidence. Update references
affected by moving documentation. Use the existing Python standard-library generators and no
new dependencies. Keep the four-check Depot CI established in CI1.

## Plan

1. Audit the implementation and documentation in parallel with the README and architecture work.
2. Rewrite the README and architecture sources; reconcile references and generator contracts.
3. Check generation, links, focused regressions and the repository gate; inspect the rendered page
   at desktop and mobile sizes.
4. Record evidence and open a separate documentation PR on the CI1 branch while its PR is pending.

## Acceptance and Honest Implementation Report

| Criterion | Status | Evidence |
|---|---|---|
| README is a concise setup/navigation entry point; detailed operations remain discoverable | Verified | `wc -l README.md`: `191`; `docs/operations.md` retains simulator, database, modes, pause, models, tracing and deployment procedures. Local simulator `status` and `clock` exited 0 and printed `target: SIMULATOR http://127.0.0.1:18765`. |
| Architecture maps implemented components and configured services with source links | Verified | Independent source audit checked agent policies, config, runtime/MCP, evals and `.railway/railway.py`; seven declared services are mapped. No unsupported uptime claims. |
| Generators are deterministic, escaped and checked; references resolve | Verified | Generator suite: `20 passed in 0.08s`; both `--check` commands exited 0; `74 local Markdown links checked; 0 errors`; browser: `localLinks: 50, failures: []`. |
| Rendered architecture is usable on desktop and mobile; repository checks pass | Verified | Browser inspected at 1440×1000 and 390×844: `pageWidth: 390, viewport: 390`, service table scroll/focus worked, no console errors. Ruff: `All checks passed!`; format: `680 files already formatted`; Black: `446 files would be left unchanged.`; mypy: `Success: no issues found in 206 source files`; isolated integration: `146 passed, 5210 deselected in 32.47s`. Unit suite: `5208 passed, 1 skipped, 146 deselected, 2 xfailed, 42 subtests passed in 107.94s`; `Required test coverage of 80% reached. Total coverage: 91.96%`. |

Honest Implementation Metric: 4/4 verified, 100%.

Unverified: the unit suite retains one skipped test and two expected failures. Deployed service availability and authenticated Phoenix
behavior were not probed; this page inventories the checked-in implementation. Browser screenshots
were saved outside the repository as `/tmp/bazaar-architecture-{desktop,mobile,mobile-services,full}.png`.

Could-not-do: none identified. Merge, deployment and publishing the hosted architecture artifact
are outside this change; the existing post-merge publication rule still applies.

Independent code and security reviews found no P0/P1 issues. Their P2 findings were fixed:
the full coverage command explicitly selects the local database, and the operations guide retains
the exact shared-Phoenix environment variable names and the MCP connection contract.
