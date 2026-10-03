# N18 — Lean agent-behaviour tracing in Phoenix  (per-task spec)

- Task id: N18
- Status: approved by the coordinator brief (2026-10-03 04:45); Jev `obs_plan` = `lean_adr_after_window` (0.96)
- Backlog source: local (.ai/specs)
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)
- Spec of record: ADR 0001 `docs/adr/0001-agent-behavior-tracing.md` (PR #46, Jhonny, carried into this PR).
  This file records only what DIFFERS from the ADR.

## Differences from the ADR
- **Lean, one PR** instead of the 5-PR stack. Order: session.id → Jev EVALUATOR → AGENT/TOOL → LLM → docs.
- **No trace-per-turn rewrite.** `NegotiationTrace` keeps its root + tick children; `session.id` groups them in
  Phoenix. The root still lands when the negotiation ends (hard-kill loses it; the tick spans are already live).
- **Jev spans are emitted by `telemetry.record_jev`** (one place, backdated by `latency_ms`), replacing the
  `jev_verdict` event; `DuelTraces.jev` no longer repeats them.
- **LLM spans wrap `provider_for`'s result** (`llm/traced.py`), so every route is covered once. Token counts are NOT
  exposed by our providers today: omitted, not guessed.
- **Prompt/completion text is recorded only for `purpose="words"`** (text we send to a counterparty anyway); other
  purposes (intent, steering, model choice) carry lengths only, because their prompts hold our parameters.
- **Private numbers never reach a span.** `scrub()` masks `limit|max|max_price|cost|value|worth|floor` followed by a
  number, and the span attributes `bazaar.duel.limit`, `bazaar.plan.*` and the plan inside the root input are removed.
- **TOOL spans carry method, ok, error code** only: no request body, no price.
- Evals (#91) as Phoenix annotations: NOT in this PR (#91 merges at 09:30, after this branch was cut).

## Acceptance criteria
1. Every negotiation span (dealer root + ticks, duel root + ticks, their Jev/TOOL children) has `session.id`
   `dealer:{dealer}:thread:{id}` / `duel:{id}`; loop spans `tick:{n}`.
2. Every Jev call is an EVALUATOR span with question, verdict, value, threshold, latency, model.
3. Each agent tick (taker, maker, duels) is one AGENT span; each game request sent is a TOOL child span.
4. Each LLM call is an LLM span (model, provider, route, purpose, latency; text only for `words`).
5. Moves are byte-identical with tracing on and off (fake-client test; simulator run twice).
6. Tracing never blocks a tick: a dead exporter costs no error and no delay.
7. Property test: no private limit/value in any attribute or event of any span.
8. `docs/observability.md` has a pitch-replay recipe.

## Risks
- `src/**` merge redeploys `bazaar-duels`/taker/maker: merge only in the afternoon window after Duels I.
- Extra spans: ~4 per agent tick; Phoenix volume is small.
