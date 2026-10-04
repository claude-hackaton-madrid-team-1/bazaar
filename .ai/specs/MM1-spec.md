# MM1: Autonomous profitable market routing and visible trade destinations

Source: Omar sees sales without a clear buyer or market in Bazaar Live and requests autonomous profitable trading across available markets, including Team10. Local backlog.

## Acceptance

1. Determine current routing behavior and verified Team10 venue identity, fees and opportunities; distinguish posted offers from accepted and settled trades.
2. Autonomous workers consider usable non-owned markets by expected net benefit and executable liquidity. Avoid needless market concentration caused by routing defaults, but never distribute blindly or promise one asset twice. Preserve guards and shared15-second tick/request budgets.
3. Live UI shows actual available counterparty, market name/id/owner, card, price and execution status. Missing counterparty for public listings is shown honestly as public/unknown, not invented. No private decision limits or credentials leak through public routes.
4. Cover actual worker routing and UI event parsing/rendering with focused regressions; run integrated checks, independent review and browser QA.
5. Ship needed changes through PRs and safe deployments, verify production data/UI and15-second ticks. Do not claim fills or profits not observed.

## Parallel ownership

Backend worker routing; Live UI/data adapters; read-only current-game/projection audit. Coordinator owns backend event projection only if needed, specs/docs, integration and rollout. No presentation changes or new provider work.
