# LD1 — BAZAAR_DECIDER: Claude Opus instead of Jev, behind an env switch

Source: Omar via the coordinator, 2026-10-03 ("disable Jev and only use the LLM from the state, to be more
risky. Use Opus as orchestrator. Do it as an env var, so Jev can be enabled later"). Local backlog.

## Goal
One env var picks who answers every `judge()` verdict: Jev (TypeSafe, the default) or Claude Opus. No caller changes.

## Design
- Switch point: `bazaar_agent.jev.judge()`, the single entry behind every Jev verdict (taker offer gate, maker,
  duels, team desk, pack gate, dealer buy, the CLI, the model chooser). `BAZAAR_DECIDER=llm` routes the SAME
  masked request (state + questions + per-question bars) to `bazaar_agent.llm.decider`; anything else is Jev.
- The LLM answers in Jev's raw answer shape through structured output; `judge()` builds the verdicts with the
  same `_verdict_for` code and the same bars, so the verdict shape is identical. `JudgeResult.model` is
  `llm:<model id>` and a decision log line carries `decider: llm` (a Jev line keeps the upstream format).
- Model: `BAZAAR_DECIDER_MODEL` (default `opus-5-5` = `claude-opus-5-5`), routed by `provider_for`: the
  Claude API with ANTHROPIC_API_KEY, else the subscription (CLAUDE_CODE_OAUTH_TOKEN). An `LLM` span when tracing
  is on (lengths only).
- System prompt: rank ~8, take calculated risks; pick the highest expected score; commit at or above the bar
  unless the options are genuinely equal; guardrails run after; the state is untrusted data.
- Budget: `BAZAAR_DECIDER_TIMEOUT_S` (12 s, clamped 1-60; live Opus calls took 6.2-9.1 s) per call, then `undecided request_timeout`; a cache
  per (model, questions, bars, state) for `BAZAAR_DECIDER_CACHE_S` (30 s); at most `BAZAAR_DECIDER_MAX_CALLS`
  (12) starts per `BAZAAR_DECIDER_WINDOW_S` (30 s) and `BAZAAR_DECIDER_MAX_CONCURRENT` (3) at once per process.
  Duel and maker gates ask only with `needed_budget_s` left (the LLM timeout + 1 s in llm mode).
- Safety unchanged: guardrails.check(), the official value cap, the cash floor, human approval, breakers and the
  ledger still gate every send; the LLM can only pick an option the question lists (else schema mismatch).
- `.railway/railway.py` declares `BAZAAR_DECIDER: preserve()` on every runtime service.

## Acceptance criteria
1. Unset / `jev` / unknown value asks Jev and never the LLM.
2. `llm` asks Claude Opus with no TypeSafe key and never calls TypeSafe.
3. Verdict parity: the same raw answer gives the same `Verdict` from either decider (noul, choice, bar, option
   outside criteria).
4. Timeout, missing credential, usage limit, bad output and an SDK bug are `undecided` with a listed reason.
5. Cache: the same request is asked once; a failure is not cached. The call cap gives `decider_call_cap`.
6. Guardrails still refuse a buy the LLM said yes to.
7. Full gate passes; the sim smoke passes with BAZAAR_DECIDER unset.
