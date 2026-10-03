# Observability: replaying a negotiation in Phoenix

Spec of record: [ADR 0001](adr/0001-agent-behavior-tracing.md) (Jhonny) and `.ai/specs/N18-spec.md` (what the lean
take-over changed). Tracing is off unless `BAZAAR_TRACING=1`; `BAZAAR_TRACING=0` is the kill flag. Phoenix is the
shared one on Railway (see README "Services and public URLs"), project `bazaar`.

## What a span means

| Span | Kind | `session.id` | Carries |
|---|---|---|---|
| `negotiation` + `tick N` | AGENT + CHAIN | `dealer:{dealer}:thread:{id}` | the dealer thread, tick by tick: messages, her ask, our move, guardrail |
| `duel` + `duel tick N` | AGENT + CHAIN | `duel:{id}` | rival offer, our move, Jev's choice, refusals |
| `jev <question>` | EVALUATOR | the session it ran in | question, verdict, value, threshold, probabilities, latency, model |
| `tool <method>` | TOOL | the session it ran in | one request sent to the game: `tool.name`, `bazaar.ok`, `bazaar.error.code` |
| `llm <model>` | LLM | the session it ran in | model, provider, route, purpose, latency; text only for `words` |
| `taker tick N`, `maker tick N`, `duels tick N` | AGENT | `tick:{n}` | one agent tick; its Jev, TOOL and LLM spans are children |
| `monitor tick N`, `feed.capture` | CHAIN | `tick:{n}` | the watchers |

What a span never carries: our limits, ceilings, values or bid ladder (`scrub_for_span` cuts a number named like a
limit, cost, value or floor; the plan and `your_limit` are not attributes at all), request bodies and prices of the
requests we send (those are in the `decisions` and `executions` tables), prompts of any LLM call except `words`
(that text is sent to the counterparty anyway), keys and tokens.

## Pitch replay: one dealer negotiation, turn by turn

1. Phoenix → project `bazaar` → **Sessions**. Open `dealer:abuela:thread:<id>` (the id is in `bazaar tape` or in the
   `thread opened` console line). Phoenix groups every trace of the session in order.
2. Each `tick N` span shows, as events: `message` (both sides), `dealer_offer`, `guardrail`, `our_move`. The `jev
   negotiation_move` child shows Jev's verdict and floats for that step; the `tool say` child shows whether the bid
   went through.
3. To watch live, keep the Sessions page open: tick spans land about 2 s after the tick ends. The `negotiation` root
   (with the full transcript) lands when the negotiation ends.

## Pitch replay: one duel, turn by turn

1. **Sessions** → `duel:<id>`. Each `duel tick N` is one round: `rival_offer`, `our_move`, `jev_choice`
   (default vs chosen move and why), `move_sent`.
2. Its `jev duel_move` / `jev rival_cares_about_days` children carry the verdict per round, and `tool duel_say` /
   `tool duel_accept` whether the game took it.

## Saved filters (Spans tab)

Phoenix's span filter box takes a Python-like expression. These are the intended filters; the exact attribute
spelling is **unverified against the live Phoenix** (the key is on Railway only), so check it once and fix this table:

| Goal | Filter |
|---|---|
| every Jev verdict | `span_kind == 'EVALUATOR'` |
| Jev undecided | `span_kind == 'EVALUATOR' and attributes['bazaar.jev.decided'] == False` |
| refused game requests | `span_kind == 'TOOL' and attributes['bazaar.ok'] == False` |
| LLM calls that failed | `span_kind == 'LLM' and status_code == 'ERROR'` |
| one session | `attributes['session.id'] == 'duel:131'` |

## Not done yet

- Evals (#91) as Phoenix annotations on the decision's span.
- LLM token counts: our providers do not return usage, so none is recorded.
- `strategy.py` and `agents/seller.py` spans, and team-to-team threads (`team:{team}:thread:{id}`, N17).
- A negotiation root still lands only when the negotiation ends; a hard kill loses it (the tick spans are live).
