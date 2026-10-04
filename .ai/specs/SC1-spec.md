# SC1: score-first strategy guidance

Source: local backlog, operator request on 2026-10-04 to correct the strategy screen's album-first objective.

## Acceptance
- Runtime advisers prioritise profitable team trades and distinguish dealer ladder, duels and market-making. Preserve private-value economics and all trading guardrails.
- Strategy documentation describes the card surplus/urgency rank as an opportunity estimate, not leaderboard points.
- Live strategy screen shows scoring mechanisms before inventory context in English and Spanish. Holdings, completed pages, activity, fee revenue and pack luck do not score.

## Plan
Change shared runtime guidance and STRATEGY.md. Independently change the Live strategy panel and bilingual strings. Run focused backend tests, frontend strategy tests, lint and build. Coordinator owns integration, review and deployment.

## Honest implementation report
- Verified runtime guidance and card-ranking explanation: `src/bazaar_agent/runtime/agents.py:35`, `STRATEGY.md:37`; focused tests output `48 passed in 2.98s`, Ruff `All checks passed!`, Black `1 file would be left unchanged.`
- Verified frontend compiles with scoring first: `src/game/ui/StrategyScreen.tsx:44` in bazaar-live; `npm run build`: `built in 181ms`, `tsc -b` exited 0; focused strategy tests `17 passed (17)`; focused ESLint exited 0.

Metric: 3/3 criteria verified in source and focused checks, 100%. Unverified: rendered browser appearance, production deployment, integration gate pending. Could-not-do: deployment belongs to coordinator; no deployment performed.
