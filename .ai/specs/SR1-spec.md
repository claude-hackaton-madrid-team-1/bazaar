# SR1 — Sunday scoring and operator control

Source: Omar's accepted plan, 4 October 2026. Backlog: local.

## Outcome

Convert safe positive-surplus opportunities, preserve the existing duel policy and model/auth setup,
measure Market Test settlements, and give operators a truthful view of decisions and failures.
Presentation work was withdrawn; the existing presentation is outside this implementation.

## Constraints

- Game ticks and the shared key budget govern all workers. No live writes during development.
- Never trade on our own venue; invite other teams to trade there with each other.
- Never sell a sole page copy, violate the sell floor, or bypass human approval at 60 P.
- Reuse existing SDK, ledger, guardrails, memory and observability. No provider or credential changes.
- Exact bench matching remains baseline; only the existing durable one-shot experiment is retained.
- Runtime and UI changes ship on feature branches. The coordinator owns deployment and safe merge timing.

## Acceptance

1. Eligible bid and team-cash opportunities use guarded paths; active commitments prevent double promises.
2. Deadline/undecided handling does not strand strategies; existing duel behavior passes regression checks.
3. Bench requests, responses and actual settlement/terminal evidence are distinguishable and correlated.
4. Operator snapshot exposes freshness, world, score, inactivity/blockers and pending outcomes.
5. MCP proposals require exact-term approval, fresh validation and safe handling of uncertain submissions.
6. Bazaar Live supports reviewed operator input, truthful character narration and bounded speech backlog.
7. Five minimalist slides explain hybrid retrieval, state, Jev/LLM specialists, guardrails, learning,
   Railway operation and observability. The failed SAL-07 sale is attributed accurately.
8. Editable PPTX, PDF, browser deck, English speech and recorded fallback share the same five-slide narrative.

## Verification

Focused unit/integration tests per slice; full Python/static gate once after integration; Live tests/build.
No simulator smoke CI gate. Presentation verification is withdrawn with criteria 7–8.
Record criterion-level evidence and limitations in the implementation report rather than claim live score gains.

## Scope update from Omar

Presentation work was stopped at the user’s request on 4 October: an existing presentation already covers that need. Criteria 7–8 are withdrawn from this implementation’s acceptance and PR. Existing local presentation files are preserved without further work. Continue criteria 1–6 and the existing CI/documentation PRs.
