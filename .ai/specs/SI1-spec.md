# SI1 — Proactive supply follows the complete-page policy

Local backlog task. Omar requested selling eligible inventory across team markets while protecting complete pages. The tick1943 inventory audit found49 copies:40 retained for four complete pages, eight incomplete-page singletons, and one duplicate already promised. This is eligibility evidence, not proof of a sale.

The strategy and maker's second supply filter were calling `Guardrails.protects` without album context, so they retained incomplete singletons even though the configured guard permits their sale. Pass the existing `our_cards` complete-page context through both. Preserve prices, spare slots, fresh publication validation, pending promises, deadlines, and venue routing. An absent album retains the conservative policy. No game calls or policy values added.

## Acceptance evidence

- Verified eligible supply and protected inventory: captured-shape fake regression asserts9 candidate copies/40 retained; removing the already-promised spare leaves8 eligible singletons.
- Verified actor path and fresh safety: actor posts eligible singletons; existing manual singleton offers are not reused; newly complete pages at fresh validation reject publication.
- Verified compatibility: disabled incomplete-page policy and missing album keep singletons protected; existing maker/strategy/counter/publication regressions pass.

Focused output: `79 passed in 0.50s` (test_maker_supply, test_strategy, test_maker, test_counter_bids, test_publication). Mypy: `Success: no issues found in 2 source files`.

Implementation metric:3/3 local behavior criteria verified (100%). Unverified: production rollout/fills, full-suite gate and CI remain coordinator/review stages. Could-not-do: none for the bounded implementation; no production writes attempted.
