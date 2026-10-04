# Q1 — Remember dealer conversation quotas through the real game hour

Source: local backlog, Omar asks to record Abuela's limit and stop repeating quota failures.

Evidence: Railway execution1884 returned `persona_quota`: “at most 10 conversations per hour with abuela”. The shared learning correctly persisted, but as an item-only sobre_barrio block ending1944. The next game hour was around2014 at15-second ticks. A generic60-tick cap caused the early expiry; startup also recalled persisted blockers only after its first trading tick.

## Acceptance

1. Trusted structured server refusals retain the explicit until_tick, or the next game-hour boundary calculated from the observed clock pace. Other legacy/LLM caps stay unchanged. The observed fixed server conversation-limit message records10 and blocks every topic with that dealer; pack purchase quota3/hour is separate.
2. A fresh process reads shared rule blockers for its team before its first opening, then uses the existing memory/off-tick refresh path. No additional game API calls.
3. Regression evidence for15-second boundary, server deadline, untrusted/future/other-team exclusions, and a restarted taker refusing its first open; review/full gate before coordinator rollout.

Implementation: learn/reader.py emits a quota expiry marker only for server refusals; blockers.py honors that marker only for rules/refusal rows and matching expiry. live.py bootstraps one bounded shared-rules read per team.

Evidence: `51 passed, 2 deselected in 1.12s` in learn_reader/learn_store/taker_learn (integration excluded while coordinator gate runs). Mypy: `Success: no issues found in 3 source files`. Existing persisted1884 row is intentionally preserved; a separately recorded corrected typed fact may supersede it operationally after deployment, using the coordinator's verified error and fresh clock.

Honest report:2/3 criteria verified (67%); review/full gate still partial. Unverified: deployed restart and post-hour retry. Could-not-do: no live database writes or deployment performed by author.
