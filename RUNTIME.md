# Runtime · Team 1 LLM layer

The runtime reads this file. `src/bazaar_agent/llm/config.py` parses every
`` - `param` = value — why `` line (same format as `GUARDRAILS.md`). `uv run bazaar llm` prints what
is loaded, which credentials are set and which route Claude uses (never a value), and Jev's last model choices.

The LLM phrases and parses; it never sets a price or authorizes a trade. Prices are structured and
set by code, every write still passes `guardrails.check()`, and every LLM failure (no key, timeout,
refusal, bad output, a used-up subscription window) falls back to the existing path: template words,
the default model, or a clear "set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN" message from `ask` and
`steer`. Claude models use ANTHROPIC_API_KEY when it is set, else the Claude subscription
(CLAUDE_CODE_OAUTH_TOKEN, README "LLM on the Claude subscription").

## Which model
- `llm_runtime` = auto — `auto` lets Jev choose; an alias or model id pins one. The `--llm-runtime` flag and `BAZAAR_LLM_RUNTIME` win over this line.
- `runtime_models` = haiku-4-5, sonnet-5-5, opus-5-5, gpt-6-1-sol — candidates for Jev's `model_for_move` choice; each needs criteria in `questions/runtime_model.json`.
- `runtime_model_default` = haiku-4-5 — used when Jev is undecided, slow or keyless; the fastest candidate, so a fallback still fits a 15 s tick.
- `model_choice_cache_ticks` = 5 — reuse a choice for the same move kind, tick length, injection flags and stakes for this many ticks, so a 15 s tick never pays two model-choice Jev calls.

## Words (negotiation messages)
- `llm_words` = false — true lets the chosen model write dealer and duel messages; off until ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN is in `.env` and a dry run looks right.
- `words_timeout_s` = 2.5 — hard limit for one message; a slower reply is dropped and the template is sent.
- `subscription_words_timeout_s` = 6 — the same limit when Claude runs on the subscription (CLAUDE_CODE_OAUTH_TOKEN): each call starts a Claude Code CLI process, measured 1.6–2.0 s on Haiku, 2.4–4 s on Sonnet and 3.1–3.8 s on Opus; still cut to the time left in the tick.
- `words_max_chars` = 300 — longest message we send; a longer reply is cut at a sentence end or replaced by the template.

## Talk and steer (outside the tick loop)
- `ask_timeout_s` = 30 — limit for `bazaar ask` to turn a sentence into an intent.
- `steer_timeout_s` = 30 — limit for `bazaar steer` to map an instruction to parameter deltas.

Model aliases (`src/bazaar_agent/llm/models.py`): `opus-5-5` → `claude-opus-5-5`, `sonnet-5-5` →
`claude-sonnet-5-5`, `haiku-4-5` → `claude-haiku-4-5-20251001`, `fable-5-1` → `claude-fable-5-1`,
`gpt-6-1-sol` → `gpt-6.1-sol`. Any other `claude-*` id goes to Anthropic and any `gpt-*` / `o<digit>`
id goes to OpenAI unchanged. Steering limits (`steer_max_change`, `steer_max_ttl_ticks`) live in
`GUARDRAILS.md`, because they fence what an LLM may change.
