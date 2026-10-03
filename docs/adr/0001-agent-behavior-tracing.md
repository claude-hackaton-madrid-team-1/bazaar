# ADR 0001: Trace agent behavior in Phoenix: one trace per turn, typed spans, sessions

## Status

Proposed. Merging this ADR is the team's approval of three things:

1. The shared, authenticated Phoenix on Railway (PR #42) is the single trace backend. Local `bazaar obs up` stays for
   development. `BAZAAR_TRACING=1` and the `PHOENIX_*` settings are unchanged.
2. The agent span model is reshaped: a `session.id` per negotiation, one trace per agent turn, typed child spans.
3. No new dependency and no second exporter. `.ai/context.md` (Stack, LOCKED) now records the OpenTelemetry packages
   that PR #34 already added to `pyproject.toml`.

## Date

2026-10-02. Code facts below were checked against `main` at `b414ee5`.

## Context

- **Audit trail.** Local JSONL (`.local/jev-decisions/`, `.local/ledger.jsonl`, `.local/duels/duels.jsonl`, alerts) plus
  the Postgres `decisions` and `executions` tables (`src/bazaar_agent/sql/schema.sql`, spec section 8). It records what
  happened. It does not show, turn by turn and live, why an agent acted.
- **Tracing and sharing exist.** PR #34 and #35 added `src/bazaar_agent/telemetry.py` and `traces.py` (OTLP/HTTP to
  Phoenix, off unless `BAZAAR_TRACING=1`, bounded `BatchSpanProcessor`, `never_raise`, `scrub`) and the dealer `Observer`
  hook. PR #42 and #44 deployed Railway project `heartfelt-warmth` (europe-west4): `phoenix` (auth on, public HTTPS URL
  in the README "Services and public URLs"), `Postgres`, `bazaar-duels` (`duel run --play`, the team's one live duel
  player, exporting spans over the private network) and `bazaar-monitor` (scaled to 0). `bazaar-taker` and
  `bazaar-maker` are coming. Sharing is solved. What is left is the shape of what we send.
- **Gaps verified on `main`:**
  1. The LLM layer `src/bazaar_agent/llm/` (PR #39) emits no spans: model choice, prompts and completions are invisible.
  2. `telemetry.py` uses only the OpenInference kinds AGENT, CHAIN and GUARDRAIL. There is no TOOL, EVALUATOR or LLM.
     Jev verdicts (`telemetry.record_jev`) and our moves (`Observer.move`) are span *events*, so Phoenix cannot filter
     them, annotate them or run evals on them.
  3. There is no `session.id` anywhere, and a negotiation or duel root is exported only when it ends (`traces.py`
     docstring). While it runs the UI shows orphan tick spans, and a hard kill loses the root.
  4. `strategy.py` and `agents/seller.py` (PR #37) are untraced.
- **A live service depends on `src/`.** `.railway/railway.py` has `src/**` in its `watchPatterns`, so any merge to `main`
  that touches `src/` redeploys `bazaar-duels`, our one live duel player.
- Judging (`docs/briefing.md`, `vendor/bazaar-kit/RULES.md`): Judges 40 (the Sunday pitch), Negotiating 30,
  Market-making 30. Replaying a negotiation turn by turn is both a debugging tool and a pitch asset.

## Scope

Agent behavior only: what each agent observed, decided, why, who intervened (Jev, guardrails, an LLM), what it did and
the outcome. Out of scope: HTTP and DB auto-instrumentation, logs and infra metrics. The existing monitor, console and
`thread.view` hooks are left as they are.

## Decision

### Backend

Phoenix on Railway is the single shared backend, with local `bazaar obs up` for development. Reuse `telemetry.py`,
`traces.py` and the dealer `Observer`. No second tracing module, no second exporter, no new dependency.

### Trace model

- **Session** = one negotiation, as OpenInference `session.id`: `dealer:{dealer}:thread:{thread_id}`, `duel:{duel_id}`,
  later the seller, taker and maker.
- **Trace** = one agent turn (observe, decide, act). It is visible live and a crash loses nothing.
- **Outcome trace** at close, carrying the transcript that the root span holds today. Dealers: `Outcome.status` (`deal`,
  `walked`, `timeout`, `accepted_pending`, or the server's `closed` / `cooloff`), price, bids, ticks. Duels: role, our
  limit, last rival offer, deadline, `done`. We see only our own limit (RULES.md), so we cannot compute the share of the
  pie. An optional score is `score.duel_points` from `/api/me`.
- **Typed child spans** inside a turn (OpenInference kinds, all present in the installed
  `openinference-semantic-conventions`):

| Span kind | Carries | Emitted from |
|---|---|---|
| AGENT (the turn) | input = observation, output = action | `Observer.wrap_tick` in `dealer.negotiate`; `DuelTraces` per duel per tick |
| CHAIN (policy) | the decision and its reason | `dealer.decide`, `duelist.duel_move`, the `strategy.py` engine |
| LLM | model, provider, masked and truncated prompt and response, latency, token counts where the provider exposes them | `LLMProvider.complete` / `structured` in `llm/providers.py` |
| EVALUATOR (Jev) | question id, verdict, value, threshold, decided; masked state only | every `jev.judge` call: `cli._jev_advisor`, `cli._pack_judge`, `llm/chooser.py` |
| GUARDRAIL | allowed, violations | the `guardrails.check` result (`Observer.guardrail`, `DuelTraces.guardrail`) |
| TOOL (game action) | `say`, `accept`, `close_thread`, `duel_say`, `duel_accept`; ok or refused code | the SDK calls in `negotiate` and `duel_run` |

- Today's events (`message`, `our_move`, `guardrail`, `jev_verdict`) become these spans. Phoenix can then filter on
  kind, annotate a span, and run evals on Jev and LLM spans.
- Attributes stay OpenInference-compatible (`openinference.span.kind`, `session.id`, `llm.model_name`, `llm.provider`,
  `llm.token_count.*`, `tool.name`, `input.value`, `output.value`), so another OTLP backend can read them later.

### Safety invariants (unchanged)

- **Off unless `BAZAAR_TRACING=1`.** CI and teammates without it are unaffected.
- **Never blocks a tick.** Bounded queue, short export timeout, errors swallowed (`never_raise`, `QuietExporter`).
- **Redaction by value.** `telemetry.scrub` cuts out the value of every `*_KEY`, `*_TOKEN`, `*_SECRET` and `*_PASSWORD`
  variable of 8+ characters and then applies the Jev masking. LLM prompts and responses go through it too.
- **Counterparty text is untrusted data.** Truncated to 512 chars, never interpreted, never rendered as markup (aligns
  with issue #24). Jev spans carry only the masked state Jev already sends.

## Alternatives Considered

Facts checked 2026-10-02. Prices and limits change, so re-check before relying on them.

| Option | Free tier and limits | Fit for us | Verdict |
|---|---|---|---|
| **Phoenix on Railway** | Self-hosted, no cap | Already shared (PR #42), auth on, OTLP-native, evals and annotations, no new system | Chosen |
| Langfuse Cloud | Hobby: 50k units/month, 2 users, 30 days. Core: $29/month, 100k units, unlimited users, 90 days | Best prompt management and cost per model, native observation types, sessions, scores. A second system with a unit cap and 2 seats | Rejected for now |
| Arize AX Free | 25k spans/month, unlimited users, 15 days retention, 1 GB | Hosted Phoenix lineage. A second system, and a span cap | Rejected |
| Pydantic Logfire | Personal: 10M records/month, 1 seat + 2 read-only guests, 30 days. Team: $49/month, 5 seats | Generic tracing UI, not agent-specific | Rejected |
| Jaeger | Self-hosted container | Generic tracing, no agent or eval features | Rejected |
| JSONL + own dashboard | Zero dependencies | We build the UI ourselves during a 48 h hackathon | Rejected |

Sources: https://langfuse.com/pricing , https://langfuse.com/integrations/native/opentelemetry ,
https://arize.com/pricing/ , https://pydantic.dev/pricing ,
https://pydantic.dev/docs/logfire/guides/alternative-clients/

Langfuse can be added later as a second exporter without changing the span model, because the attributes stay
OpenInference-compatible. That is a new decision, not part of this one.

## Consequences

- One backend, no new dependency, no new infrastructure.
- Trace-per-turn replaces the single root that held the whole transcript (`traces.NegotiationTrace`). The outcome trace
  carries the transcript instead, and Phoenix's session view should group a negotiation's turns [INFERRED: PR 1 checks it
  on the live Phoenix].
- More spans per turn (turn, policy, guardrail, tool, plus Jev and LLM calls). ESTIMATE, not measured: about 1.9k turns
  over the weekend x about 6 spans, 12-15k spans, small for Phoenix. Watch the size of the `phoenix-data` volume.
- Prompts and completions are stored in our Phoenix (auth on): masked, truncated, and untrusted where they quote a
  counterparty.
- Every PR of the stack that touches `src/` would redeploy `bazaar-duels` if merged to `main` on its own, so the stack is
  merged once (see Rollout), outside a duel session.
- The JSONL and Postgres audit trail stays the source of truth. Tracing is additive and lossy by design.

## Rollout

A stack of PRs merged into `main` once. Each PR targets the previous branch, stays at most 1000 changed lines
(`uv.lock` included, untouched because there is no new dependency) and is opened by a human.

| PR | Branch (base) | Content |
|---|---|---|
| 0 | `docs/adr-agent-tracing` (`main`) | This ADR, spec section 8.1, plan N10, the stack and tracing lines in `.ai/context.md`. Docs only |
| 1 | `feat/tracing-agent-turns` (PR 0) | Typed-span helpers in `telemetry.py`, `session.id`, trace per turn. The dealer negotiator is migrated, with the transcript kept in the outcome trace |
| 2 | `feat/tracing-llm-layer` (PR 1) | LLM spans for `src/bazaar_agent/llm` and EVALUATOR spans for every Jev call |
| 3 | `feat/tracing-duel-turns` (PR 2) | The duelist (`DuelTraces`, `cli.duel_run`). It is the production service, so moves must be identical with tracing on and off |
| 4 | `feat/tracing-strategy-seller` (PR 3) | `strategy.py` and `agents/seller.py`, the `.ai/skills/agent-tracing/SKILL.md` contract for the taker and maker, and the README "Observability" update |

**Single merge.** When all five are approved, merge top-down, each PR into the base it targets: PR 4 into PR 3's
branch, then PR 3 into PR 2's, PR 2 into PR 1's, PR 1 into PR 0's. PR 0, now carrying everything, is merged into `main`
in one merge. That is one redeploy of `bazaar-duels`: do it outside a duel session (Duels I is Saturday h6.5). A review
fix on PR n is committed to its branch and merged forward into the branches above it.
