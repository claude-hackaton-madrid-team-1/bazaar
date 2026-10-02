# MEMORY — Bazaar (shared team working log)

Shared, **committed** working log for every teammate and every agent (Claude Code, Codex, Gemini,
opencode). Protocol: see the "Memory protocol" section of `.ai/context.md`.

Append only, newest at the bottom of `## Log`, terse. The latest headings are mirrored into the
README status block on every commit.
**NEVER write secrets here**: no team key, broker keys, `TYPESAFE_API_KEY`, `.env` values or tokens.

## Log

### [2026-10-02] gotcha — the public feed is capped at 500 events and has no cursor
`GET /api/feed?limit=1000` returns at most 500 events (verified tick 31). Older events are gone
for good once the history outgrows the window → keep `uv run bazaar feed capture` running all
game; it reads once per tick and flags `GAP POSSIBLE` if a full window stops reaching our history.
Our capture starts at event id 15 (ids 1–14 were `team.joined`), so Friday is effectively complete.

### [2026-10-02] gotcha — keyless reads must not send an empty or wrong X-Team-Key
Wrong keys count toward `too_many_failures` (20 per burst per address). `sdk.PublicBazaar` reuses
the vendored SDK without the header for the public routes.

### [2026-10-02] finding — Abuela's floor for `sobre_barrio` looks like 17 P (ticks 0–31)
31 pack threads from every team in the feed: 13 filled, min 17 / median 17 / max 24 (list 26,
opening ask 30). Teams that bid below 17 never filled: thread 12 (t15) bid 6→12 and she repeated
17 seven times. Bidding up to 17 quickly seems to close in ~2 steps. Evidence: `uv run bazaar curves --dealer abuela --threads 20`.

### [2026-10-02] finding — the feed is an order book: dealer text, real team ids, fill prices
`thread.message` carries the structured offer, plus Abuela's text (a team's text is null).
`offer.listed` carries the real team id; the venue board shows only a pseudonym, resolvable by
offer id (`uv run bazaar book`). `settlement` carries parties, items and price (`uv run bazaar tape`).
Ask the desk before relying on the pseudonym mapping for trading decisions.

### [2026-10-02] build-error — DB test overwrote real dealer_curves rows
symptom: rows for threads 10–12 changed after `pytest` → root cause: synthetic test ids collide
with real ids, and `load_feed` upserts → fix: `tests/test_db.py` runs in a throwaway schema
(`bazaar_pytest`) and drops it; real rows reloaded with `uv run bazaar db load`.

### [2026-10-02] gotcha — zsh treats `echo ====` as a path expansion
`=word` expands to a command path in zsh, so `echo ===` fails with "= not found" and aborts a
`&&` chain. Use `echo "---"` in shell one-liners.

### [2026-10-02] gotcha — `.env` has `TYPESAFE_API_KEY` but no `BAZAAR_KEY` yet
Public commands work without the team key; `uv run bazaar status` needs `BAZAAR_KEY=tk-...` in `.env`.

### [2026-10-02] finding — Jev runs in Python now; a thin state gets `undecided`, not yes
`uv run python -m bazaar_agent.jev judge --state - --questions questions/negotiation.json --log`
(~270–310 ms, model jev-1.13.0). With only offer + cash in the state, `offer_is_worth_accepting`
came back undecided (0.59 vs the 0.75 bar). Feed Jev the album need, `your_value` and the learned
fill prices, or it will rarely decide. Decision logs: `.local/jev-decisions/` (masked, local).

### [2026-10-02] build-error — dealer loop re-handled one tick 14 times (thread 85 wasted)
symptom: `bazaar dealer buy LAV-03 --live` sent 1 bid, then 13 `wait_for_tick` refusals and a
timeout close, all inside tick 48 → root cause: `run_per_tick(max_ticks=1)` called in a loop, and
each call forgets the last tick → fix: one `run_per_tick(..., stop=...)` owns the tick bookkeeping;
regression test `test_negotiate_sends_one_message_per_tick_even_when_the_clock_is_read_many_times`.

### [2026-10-02] finding — first ladder deal: LAV-03 from Abuela at 7 P (thread 99, tick 55)
Our bid 6 → her ask 7 → accepted (3 ticks, `dealer buy LAV-03 --start 6 --max 10`). Commons open at
12, so 7 captures most of her range, and LAV-03 is worth 16 to us. Run by the Orca worker; LAV-04 next.

### [2026-10-02] finding — LAV-04 bought at 9 (thread 101, 5 ticks); Abuela accepted OUR bid
Bids 6→7→8→9, she accepted our 9 (no accept from us), so the old runner printed `price None`.
Fixed: the deal hook falls back to our last bid and records spend in `.local/ledger.jsonl`.

### [2026-10-02] finding — El Chato announced (next dealer), seen by the monitor at tick 76
`/api/levels`: `{"id": "chato", "kind": "persona", "state": "announced", "teaser": "«Better packs,
friendly prices. If I like you.»"}`. Abuela ladder so far: LAV-03 7, LAV-04 9, LAV-05 9, LAV-06 22
(4 negotiated deals; score 11.31, rank 10). `bazaar monitor --notify` alerts when it goes active.

### [2026-10-02] gotcha — `python -m bazaar_agent.jev` reads TYPESAFE_API_KEY only from the environment
By design (upstream parity) it does not load `.env`: run `set -a; . ./.env; set +a` first, or it
returns `undecided (typesafe_api_key_missing)`. `bazaar dealer buy --jev` loads `.env` itself.

### [2026-10-02] finding — Railway's default Postgres image ships pgvector, despite its docs
docs.railway.com/databases/postgresql says the default template adds no extensions, but the image
(`ghcr.io/railwayapp-templates/postgres-ssl:17`) installs `postgresql-17-pgvector` since 2026-03-14
(verified locally: `create extension vector` → 0.8.6; SSL on with `sslmode=require`). An older
service may still lack it: the schema now works either way, and `uv run bazaar db check` says which.

### [2026-10-02] gotcha — libpq echoes the password when it cannot parse DATABASE_URL
A bad percent-escape in the password gives `invalid percent-encoded token: "<password...>"`. Print
DB errors only through `pgconn.redact()` (as `bazaar db check` does), never `str(e)` of a connect error.

### [2026-10-02] gotcha — Phoenix's hosted cloud is gone; share a self-hosted Phoenix instead
`app.phoenix.arize.com` answers HTTP 410 and the Phoenix docs now say Phoenix is self-hosted only
(the managed SaaS is Arize AX). For teammates on other laptops: one host runs
`PHOENIX_BIND=0.0.0.0 uv run bazaar obs up`, the others set `PHOENIX_COLLECTOR_ENDPOINT=http://<host>:6006`.
Phoenix ingests traces only (no OTLP logs), so console lines are span events.

### [2026-10-02] build-error — a CLI test with a frozen fake clock hung forever
symptom: `pytest tests/test_telemetry_cli.py` never returned → root cause: `--max-ticks 2` with a
fake clock stuck on one tick, so `run_per_tick` slept (real `time.sleep`) waiting for tick 2 → fix:
CLI tests with a fixed clock use `--max-ticks 1`.

### [2026-10-02] gotcha — typer 0.27 vendors click: `import click` fails
Use `typer.Context` and `typer.main.get_current_context(silent=True)` (its `command_path` names the
running subcommand). In a group callback, `ctx.invoked_subcommand` is only the first level.

### [2026-10-02] finding — strategy engine, first live ranking (tick 95): rares first, LAT-09 is our best sell
`uv run bazaar strategy`: top buys are public bids for LAV-09 / LAV-10 at 70 (worth 157 each with the
page bonus share; LAV-09 has 1 minted copy, holder unknown; t10 holds LAV-10 and chases LAV itself),
then MAL-10 53, SAL-09 74, SAL-10 80. Top sell: LAT-09 (ours 35, LAT ×0.5) at 68 to t07/t15/t18.
A `sobre_barrio` is worth ~19 to us vs 17: a thin edge. Cash 353 leaves 83 above the floor: one rare.

### [2026-10-02] finding — Jev picks the runtime LLM decisively when the state has stakes and time
`questions/runtime_model.json` (`model_for_move`, design bar 0.75), live call: a buy with 75 P at risk
and 38 s left → `opus-5-5` 0.96 (sonnet 0.02, gpt-6-1-sol 0.02, haiku 0.00); a sell in a 15 s tick
with 9 s left and injection flags → `sonnet-5-5` 0.95. The next tick reused the cached choice.
Floats for every fresh choice: `.local/llm/model-choices.jsonl` and `uv run bazaar llm`.

### [2026-10-02] gotcha — OpenAI's id is `gpt-6.1-sol` (dot), not `gpt-6-1-sol`
Confirmed on developers.openai.com (latest-model guide). `gpt-6-1-sol` is our alias for it; other
`gpt-*` ids pass through unchanged and a wrong one fails at call time (`unknown_model`), then falls back.

### [2026-10-02] build-error — a rival's text with `[/red]` would crash `duel run --play`
symptom: `MarkupError` from `console.print(f"... rival {d.get('rival_offer')} ...")` (found by the
security review of the runtime LLM PR) → root cause: rich parses `[...]` in untrusted text as markup,
and `run_per_tick` has no try/except → fix: `rich.markup.escape()` on every counterparty or model string.

### [2026-10-02] gotcha — the SSE stream is the feed plus `tick` events, with no `id:` lines
`GET /api/events/stream?scope=team` (key in `X-Team-Key`) sends `event: hello` (`{"tick", "scope":
"team:t01"}`), then `event: <type>` + `data: <same object as /api/feed>`, and `: keep-alive` every
~15 s (verified tick 105). It also sends `type: tick` events the feed never has, and our team-scoped
events (`duel.message`, scope `team:t01`) that the public feed does not show. No `id:` lines, so a
reconnect cannot resume: keep the per-tick `/api/feed` poll as gap-filler. 6 streams per key across
all laptops and browser tabs.

### [2026-10-02] finding — the stream runs up to a tick ahead of the poll (ticks 123–129)
`bazaar monitor --show-events`: ticks 128→129, 34 events reached us by stream before the poll, median
52.0 s and max 58.7 s earlier (ticks 123→124: 9 events, max 54.5 s). A mid-tick message is otherwise
seen only at the next tick. Events emitted at the tick boundary (7, then 4) came by poll first.
Dedupe by id kept each event once in `feed.jsonl`.

### [2026-10-02] build-error — Railway build failed: "No start command detected"
symptom: the first `bazaar` service build failed in `railpack prepare` → root cause: Railpack only
guesses a start for FastAPI/Flask/Django or a root `main.py`/`app.py` → fix: `.railway/railway.py`
(Railway IaC, Python) sets build and start per service. Config as Code (`railway.toml`) is deprecated
and new services cannot opt into it (docs.railway.com/config-as-code, cutoff 2026-12-01).

### [2026-10-02] gotcha — Railpack's uv install is `--no-editable`, which breaks REPO_ROOT
Railpack runs `uv sync --locked --no-dev --no-editable` (railpack core/providers/python), so
`bazaar_agent` lands in `.venv/lib/.../site-packages` and `Path(__file__).parents[2]` no longer finds
`vendor/bazaar-kit`, `GUARDRAILS.md` or `questions/`. Build command `uv sync --locked --no-dev`
(editable) keeps REPO_ROOT at `/app` (verified in the container). Python defaults to 3.13.2 unless
`RAILPACK_PYTHON_VERSION` is set.

### [2026-10-02] gotcha — Phoenix forces an admin password reset even with an initial password set
`PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` still creates the admin with `reset_password=True`
(phoenix.db.facilitator, 20.19.0).
`patchViewer` accepts the same password, which clears the flag but ends every session: log in again.
A system API key then comes from `POST /v1/system/api_keys` with the admin session. All of it is
`bazaar obs bootstrap`. Phoenix's `/healthz` stays public with auth on; `/v1/*` and OTLP answer 401.

### [2026-10-02] gotcha — `railway variable set` has no shared-variable flag; use `--stdin` for secrets
Secrets go `printf %s "$V" | railway variable set NAME --stdin --service X`, never as `NAME=value` on
the command line. Railway's watch patterns turn README-bot pushes into SKIPPED deployments, and the
MCP `redeploy` then needs the last SUCCESS deployment id (it refuses a SKIPPED one).

### [2026-10-02] gotcha — Railway has no 0 replicas; `railway config apply` can fail with exit 0
`numReplicas: 0` is rejected ("Too small: expected number to be >=1"), yet `railway config apply`
(CLI 5.45.8) printed only its header and exited 0; the reason is in `--json` → `applyResult.status`
"failed" + `diagnostics`. Re-plan after every apply. A region set to null does not mean zero either:
Railway moved the service to its default region (us-west2) until the region map was set back. To
turn a service off, disconnect its source and `railway down` it (what we did to `bazaar-monitor`).

### [2026-10-02] build-error — one DNS failure killed the laptop monitor (Friday close, commuting)
symptom: `BazaarError: network: GET /api/clock: nodename nor servname provided` and the MONITOR
process exited → root cause: `run_per_tick` let a clock-read or tick exception escape → fix: the loop
reports and retries a failed clock read with backoff (1 s → 60 s) and a failed tick is reported and
not retried within that tick; only Ctrl-C/SystemExit stop it (tests in tests/test_ticks.py).

### [2026-10-02] finding — first autonomous dry runs (tick 155): the taker would buy MAL-02 for 5, the maker would list 3 asks
`uv run bazaar agent taker --max-ticks 1`: 8 board asks under value; WOULD accept MAL-02 on rastro for 5
(ask + fee, worth 14.1); LAV-08 at 33 and SAL-07 at 30 refused by `max_price_uncommon` 26; the rest lost
the 1-accept quota. WOULD open a thread with abuela for LAV-08 (ladder 17→26). `agent maker`: asks LAT-09
68, LAT-08 25, SAL-01 10 on rastro; no bids, because every missing rare routes to Chato (fills ~90 >
`max_price_rare` 80), and the maker only bids where the strategy proposes a team bid.

### [2026-10-02] gotcha — after 23:00 the doors close and every tick loop just waits
`/api/clock` → `doors: closed`, `paused: true` until `next_opens` (Sat 09:00): `run_per_tick` polls every
300 s and never calls `on_tick`, so a `--max-ticks 2` run looks hung. The agents log `waiting, doors
closed` once and their `/health` carries `doors`, `paused`, `next_opens`.

### [2026-10-02] build-error — a ledger note on stdout broke `bazaar strategy --json`
symptom: `json.load` failed on the first line → root cause: `open_ledger` logged "ledger: shared Postgres
table" through the stdout console before the JSON → fix: CLI ledger notes go to stderr (`err_console`).
Same trap: `_events` prints "no captured feed yet" on stdout when `.local/feed` is empty (pre-existing).

### [2026-10-03] gotcha — Agent SDK on the subscription: 4–7 s per call until MCP is off; structured output needs 2 turns
`query()` with the laptop's `claude` login loaded the claude.ai MCP connectors on every call (4–7 s for one
sentence). `strict_mcp_config=True` + `setting_sources=[]` + `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` brought
Haiku to ~0.9 s (bare prompt) and 1.6–2.0 s (our words prompt). `output_format` comes back through
a tool turn, so `max_turns=1` cannot return structured output: use ≥ 2 (we use 3). Bare mode (`--bare`)
does not read CLAUDE_CODE_OAUTH_TOKEN, and ANTHROPIC_AUTH_TOKEN outranks it: `runtime/claude.py` blanks both.

### [2026-10-03] gotcha — Railway IaC `preserve()` on a variable that does not exist yet is a no-op
`railway config plan` stays "already up to date" (changeSet empty, no diagnostics) with
`CLAUDE_CODE_OAUTH_TOKEN: preserve()` on 3 services and no value set: declare first, set later with
`railway variable set --stdin`, and the next apply keeps the value.

### [2026-10-03] finding — Jev on a real practice duel: leans accept, but under the design bar
Live `duel_move` (jev-1.13.0, 251–289 ms) on duel 131 at tick 141 (we sell at cost 40, rival bid 68 → 80,
rounds 0): accept 0.82 / counter 0.17 / hold 0.01, confidence 0.74 < 0.75 → `undecided` → the player kept
today's move (accept 80). A fixture `list_price_choice` (LAT-09 ask 68/59/50) came back `aggressive` 0.87.
Choice verdicts often land just under 0.75: watch `python -m bazaar_agent.jev report` before lowering a bar.

### [2026-10-03] build-error — rich swallowed "[jev accept (0.91)]" in a console line
symptom: the duel line printed without the Jev reason → root cause: rich parses `[...]` as a markup tag,
even around escaped text → fix: a ` · ` separator instead of brackets; `escape()` alone covers only the inside.

### [2026-10-03] gotcha — `tm.scrub` (Jev masking) breaks JSON and reads game numbers as hostnames
Scrubbing a serialized JSON payload with `telemetry.scrub` redacted `"price":10.0,"surplus":…` as an
"internal hostname" (10.x) and left invalid JSON. Tool answers now scrub per string value with
targeted patterns (`runtime.tools.safe_value`: our secret values, key/token shapes, bearer tokens, URLs).
Secret values shorter than 12 chars are skipped: the local default DB password is the word `bazaar`.

### [2026-10-03] gotcha — MCP Python SDK 2.x renamed FastMCP and moved low-level handlers to the constructor
`mcp>=2`: `from mcp.server.mcpserver import MCPServer` (FastMCP is gone), low-level
`Server(name, on_list_tools=..., on_call_tool=...)`, transport options on
`streamable_http_app(stateless_http=..., json_response=..., host=...)`; bound to localhost it turns on
DNS-rebinding protection (Host must be localhost/127.0.0.1), so Railway binds `0.0.0.0`.
py.sdk.modelcontextprotocol.io/v2/migration.

### [2026-10-03] finding — Agent SDK subagents run in the background by default
code.claude.com/docs/en/agent-sdk/subagents: an `Agent` call without `run_in_background` starts a
background subagent. The desk's PreToolUse hook rewrites every `Agent` call with
`run_in_background: false` (`updatedInput`) so the desk reports an answer, and denies any subagent
that is not ours (`CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1` removes general-purpose too).

