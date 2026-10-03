# N15 — Jev picks the desk's model per request (orchestrator + each subagent)  (per-task spec)

- Task id: N15 (local, `.ai/specs/02-plan.md`)
- Status: approved by Omar through the Orca coordinator brief (task_2c897e85d8a3, 2026-10-03)
- Backlog source: local (.ai/specs) — the brief is the ticket
- Traces up to: [`01-spec.md`](./01-spec.md) (runtime LLM, N8) · Indexed in: [`02-plan.md`](./02-plan.md)
- Sources: Omar's brief ("Jev must orchestrate which LLM is best for EVERY operation"), `RUNTIME.md`
  (`llm_runtime` = auto, `model_choice_cache_ticks`, `desk_model`), `questions/runtime_model.json`
  (`model_for_move`), Agent SDK docs (code.claude.com/docs/en/agent-sdk/subagents: `AgentDefinition.model`
  takes an alias or a full model id; code.claude.com/docs/en/sub-agents#choose-a-model: a per-invocation
  `model` on the Agent call wins over the definition's).

## Goal
The desk (Claude Agent SDK: desk + strategist/buyer/seller/duelist) stops being pinned to Sonnet 5.5.
Before each desk request, ONE Jev call (or none, from the choice cache) picks the model for the
orchestrator and for each subagent; undecided, slow or keyless Jev falls back to a documented default
per role, and a pinned model still wins.

## Scope / non-goals
- In: RUNTIME.md `desk_model` = auto (default) + `desk_role_defaults`; a `model_for_desk_role`
  question in `questions/runtime_model.json`; one batched Jev call per request for the roles the cache
  does not hold; per-subagent `AgentDefinition.model`; the hook strips a per-invocation `model` from
  `Agent` calls so the desk's LLM cannot override Jev; choices logged to `.local/llm/model-choices.jsonl`
  and shown by `bazaar llm`; docs + diagram.
- Out: `llm_words` stays false. The tick-loop runtime (`model_for_move`) is unchanged. bazaar-mcp
  serves tools only (no LLM of ours behind it): nothing to choose there. No OpenAI model on the desk.

## Acceptance criteria (each MUST be testable)
1. `desk_model` = auto: Jev's decided verdict picks each role's model; the orchestrator runs it and each
   subagent's `AgentDefinition.model` carries its own full model id.
2. Undecided, timed-out or keyless Jev → each role's `desk_role_defaults` model (logged with the reason).
3. A pin wins: `bazaar agent chat --model` > `--llm-runtime` / `BAZAAR_LLM_RUNTIME` / RUNTIME.md
   `llm_runtime` (Claude only) > RUNTIME.md `desk_model` (not auto) > Jev; no Jev call when pinned.
4. One request costs at most ONE Jev call (all uncached roles in one request); a second request inside
   `model_choice_cache_ticks` with the same situation costs none.
5. Only Claude candidates are offered for the desk; a role default or pinned `desk_model` that is not a
   Claude model fails RUNTIME.md validation.
6. Every choice (role, model, source, floats) lands in `.local/llm/model-choices.jsonl` and `bazaar llm`.
7. The PreToolUse hook removes a `model` (and any unexpected key) the desk's LLM puts on an `Agent` call and
   sets this request's choice for that subagent; one `agent chat` conversation keeps one session.
8. Docs: RUNTIME.md, README "Runtime LLM" + "Agent runtime", architecture boxes `llm_proposer` /
   `llm_runtime` ("LLM → Jev picks per move"), regenerated `docs/architecture.html`.
9. Gate green (black, ruff, format, mypy, pytest) + `scripts/sim_smoke.py`; a dry desk run on the
   local simulator shows the chosen models.

## Interfaces / data touched
`src/bazaar_agent/llm/{config,chooser,cli}.py`, `src/bazaar_agent/runtime/{desk_models,desk,agents,hooks,cli}.py`,
`questions/runtime_model.json`, `RUNTIME.md`, README, `docs/architecture.status.json` (+ generated html),
tests `tests/test_desk_models.py` + updates to the desk/hook/llm tests.

## Risks & assumptions
- Subagent definitions are fixed when the SDK session starts, and a new session would forget the
  conversation (the #108 review's P1), so `agent chat` keeps ONE session: the orchestrator switches with
  `ClaudeSDKClient.set_model()`, and the hook puts each subagent's family alias on its `Agent` call, which
  the session pins to our exact id with ANTHROPIC_DEFAULT_<FAMILY>_MODEL (code.claude.com/docs/en/model-config).
- The Agent tool's own `model` input only takes family aliases (sonnet/opus/haiku/fable, read from the
  bundled CLI 0.2.163 schema): a family with two of our models cannot be named, and keeps the session's model.
- Value at risk for a desk request is read from the operator's text (the largest price-like number,
  card codes excluded); it only buckets the cache and informs Jev, it never sets a price.
