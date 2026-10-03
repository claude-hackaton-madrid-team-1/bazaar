# MEMORY — Bazaar (shared team working log)

Shared, **committed** working log for every teammate and every agent (Claude Code sessions
and sub-agents). Protocol: see the "Memory protocol" section of `.ai/context.md`.

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


### [2026-10-03] gotcha — `ruff format` output can fail `black --check`; format with black
black 26 wraps a split conditional expression in parentheses and joins implicit string concatenations
that fit in 120 columns; ruff 0.16 leaves both as written, so ruff-formatted code failed `black --check`
in 3 files (db.py, strategy.py, jev/mask.py). The reverse holds: ruff's format check accepts black's
output. Format with `uv run black src tests scripts`; CI and the pre-commit hook check both.

### [2026-10-03] finding — a finished duel's `result` is our surplus after decay; there is no pie or share
`GET /api/duels?done=true` (practice session 1, our 26 duels): `status` deal | no_deal (6 still `live`:
the doors closed mid-session), `price`, `rounds`, `result` = our surplus × (1 − decay_per_round) ** rounds
(duel 85: (138 − 109) × 0.94⁷ = 18.8). No `share`, no rival limit: `bazaar evals` bounds the pie with the
rival's best offer. Our 7 played deals took 1–9 rounds and kept 57–94 % of their value; 8 log-only duels
had the rival inside our limit (176.9 P kept in deals, up to 42 P left on the table in one walk).

### [2026-10-03] build-error — dealer fills went to an abandoned older thread
symptom: dealer_curves showed thread 85 (LAV-03, no answer) filled at 7 and thread 99 open → root cause:
`intel.dealer_threads` matched settlements to threads oldest first → fix: newest thread opened before the
fill owns it (one conversation per dealer); regression test in tests/test_intel.py. Rows already in
`dealer_curves` keep the old fill (its upsert never un-fills a row); the evals read our threads from
`feed_events`, so they are right either way.

### [2026-10-03] gotcha — `right` is a reserved word in Postgres
A view column `as right` is accepted, but `select right from eval_jev_calibration` is a syntax error
(RIGHT JOIN): the calibration view names them `n_right`, `n_wrong`, `n_unknown`.

### [2026-10-03] gotcha — `railway config apply` from main deletes bazaar-sim until PR #55 merges
`.railway/railway.py` is a named partial: a service it created and no longer declares is deleted.
`bazaar-sim` is declared only on PR #55's branch, so a plan from main (or a branch without it) shows
"- Delete service bazaar-sim". Never apply a plan with a destructive change nobody asked for.

### [2026-10-03] gotcha — how bazaar-mcp was applied while bazaar-sim lives only on PR #55
`railway config apply --file <scratch>/.railway/railway.py`, that file = main's railway.py + PR #55's
`simulator()` verbatim with #55's own BUILD (main's adds RUNTIME.md to watchPatterns, which would have
changed bazaar-sim). Plan first: "2 to add, 4 to change, 0 to destroy", bazaar-sim untouched; check
`applyResult.status` in `--json` (all "applied"); the re-plan said "already up to date".

### [2026-10-03] gotcha — `tests/test_status.py::test_publishing_never_waits…` flakes on CI runners
`assert elapsed < 2.0` failed at 2.065 s and 2.080 s on GitHub runners (PR #59 and docs-only PR #64); it
passes locally in 0.2 s and on a rerun. A slow runner, not a regression: rerun the failed job.

### [2026-10-03] finding — the real Claude Code CLI enforces our PreToolUse deny (subscription, dry run)
`bazaar agent chat --once` on the subscription token: desk → buyer (foreground), then the hook denied
`dealer_buy` (max 90 > `max_price_rare` 80) and `sell_bid 500`; the CLI hands the model
`PreToolUse:mcp__bazaar__<tool> hook error: <reason>`. Nothing was sent; the rows are in `decisions`
(`desk/buyer`, rejected). The desk answered in Spanish to an English request: tighten its language line.

### [2026-10-03] finding — a dealer's "Deal!" settles in the SAME tick as the message
13 of 13 Abuela deals where she accepted our bid (message with no offer, "Deal!"/"Venga") show the
`settlement` event in that same tick (feed, ticks 8–46). Our own accept of her offer settles at the
next tick. The simulator (`bazaar-sim`) does the same; settling a tick later made the taker walk.

### [2026-10-03] gotcha — Railway IaC cannot declare a generated `*.up.railway.app` domain
docs.railway.com/infrastructure-as-code/reference: "Generated Railway service domains are not included
in `.railway/railway.ts`" (custom domains only). `bazaar-sim`'s domain was made once with
`railway domain --service bazaar-sim`; `railway config plan` still reports up to date afterwards.

### [2026-10-03] gotcha — the simulator's database is `bazaar_sim`, beside `railway` on the same server
Created with `create database bazaar_sim` (connected to `postgres`, never `railway`); our schema is
applied there. Against a simulator our client refuses a database URL naming `railway`
(`BAZAAR_SIM_DATABASE_URL`), and its files default to `.local/sim-client/`, never the real `.local/`.

### [2026-10-03] build-error — a 64 KB pytest parametrize id killed the CI test step
symptom: PR #55's `test` job failed with no summary right after `test_bodies_are_strict_json` →
root cause: the 413 case's parameter (65 KB of "x") became the test id printed by `pytest -v`, and the
log/step died there; locally and in a Linux container the suite passed → fix: `ids=[...]` short names.

### [2026-10-03] gotcha — an undeclared hand-set variable is deleted by `railway config apply`
`railway config plan --file <main's railway.py>` (01:50): "Delete variable bazaar-taker.BAZAAR_LIVE",
"...bazaar-maker.BAZAAR_LIVE" and "Delete service bazaar-sim". The named partial owns those services, so
a variable set by hand but not declared is removed: the live agents would drop to dry run. Fix: declare
it `preserve()` (no value in the file). PR #55 does that for BAZAAR_LIVE and keeps bazaar-sim declared.

### [2026-10-03] finding — the target is now the flag BAZAAR_SIM, never a URL
`BAZAAR_SIM=1 uv run bazaar status` talks to the simulator with `BAZAAR_SIM_KEY` (default sim-team1);
unset is the real game with `BAZAAR_KEY`. `BAZAAR_URL` makes every command stop: delete it from `.env`.


### [2026-10-03] gotcha — Greptile hit its 50-credit trial limit; `/pr-review` is the gate now
From 2026-10-03 ~02:15 Greptile answered "reached the 50-credit limit for trial accounts" and stopped
reviewing new heads. Omar disabled it. Every PR now runs `/pr-review <n>` (`.ai/agents/pr-reviewer.md`):
a fresh-context sub-agent that merges the PR onto current main, runs the gate and posts a P0-P3 verdict.
Tonight's manual reviews in that shape caught a test that only failed after merging with main (#62) and
leaks of our limits on the public `/state` (#69).

### [2026-10-03] finding — the simulator smoke is the merge gate (`scripts/sim_smoke.py`, CI `sim-smoke`)
It serves `bazaar-sim` on 127.0.0.1:8765 (memory world, 2 s ticks) and runs our CLI with BAZAAR_SIM=local:
status, a negotiated dealer buy, two live ticks of taker and maker, duel moves, the monitor's SSE, the key
guard and the BAZAAR_URL fail-fast. `scripts/sim_guard/sitecustomize.py` (on every child's PYTHONPATH) raises
on any non-loopback connect or DNS lookup; a dead proxy backs it up; children get an allow-listed env and an
empty BAZAAR_ENV_FILE. A step fails on Traceback, "tick loop:" or " refused ". ~20 s locally.
Deployed sim verified 02:10: tick 12→13 in 11 s, store `bazaar_sim`; live buy LAV-03 at 8 (thread 7, 4 ticks).

### [2026-10-03] build-error — an apply revived the OFF bazaar-monitor from its old image
symptom: `bazaar-monitor` (no source, `enabled=False`) RUNNING since Fri 23:14 UTC, holding one of the
key's six SSE slots → root cause: Railway redeploys a service's last image whenever an apply changes its
config, source or not; the #59 apply added `RUNTIME.md` to the shared `BUILD` watch patterns
(deployment reason `redeploy`, patchId `iac-change-set/…`) → fix: `railway down --service bazaar-monitor`,
then the monitor left `.railway/railway.py` (Omar deletes its service and volume by hand) and so did
`bazaar-evals` (service deleted): the file declares no service we do not run, and
tests/test_railway_iac.py fails on a service without a source.


### [2026-10-03] gotcha — public /state: "sent" needs `chosen`, and only sent rows are published at all
symptom: maker reprice rows (approved, chosen=False, move.price = strategy target) published a price that
reveals our top bid (#69 review) → root cause: `sent` ignored `chosen`; unsent accept rows and refusal codes
(`insufficient_cash`, `persona_quota`) also said which limit bound us → fix (#121): `_is_sent` = approved + chosen
+ live, `publishable` = sent and not `hold_*`, `jev` always null, `error_code` coarse (`refused`).

### [2026-10-03] finding — the homepage's "On air · Live feed" is /api/feed + the public SSE stream, nothing more
Its bundle (`LiveFeed`, `EventLine`, `useEvents`) seeds from `GET /api/feed?limit=150` and follows
`/api/events/stream?scope=public` (limit 200), one line per event type. So our capture already sees it all.
Types that matter for blockers, not yet seen live: `persona.cooloff {persona, team, until_tick}` ("sent Team X
away until T…"), `persona.strike {persona, team, kinds, strikes}`, `day.closed {reopens}`. Organiser news
reaches teams as `announcement` (the `/api/admin/news` routes are admin-only). `bazaar learnings` reads them.

### [2026-10-03] gotcha — a simulator run with no BAZAAR_SIM_DATABASE_URL reads the default local docker DB
`BAZAAR_SIM=local` with `DATABASE_URL` unset still connects to `localhost:5433` (`bazaar-db`), which holds an
old copy of the REAL feed: the agents merge real `feed_events` with the simulator's window, and the ids
collide. For an end-to-end sim run, set `BAZAAR_SIM_DATABASE_URL` to a sim database or stop `bazaar-db`.

### [2026-10-03] finding — in the simulator a cooloff's `thread.closed` has no until_tick; the refusal does
Rude words drove sim Abuela to `cooloff` in 3 messages (tick 5 → `until_tick` 25). The thread shows
`closed_reason: cooloff`, `persona.cooloff` carries `until_tick: 25`, and a re-open is refused `cooloff` with
`extra.until_tick`. The live taker then logged `skip abuela for LAT-08: abuela cooloff with us until T25`
for ticks 6–8 instead of sending a refused `open_thread`.

### [2026-10-03] gotcha — `create index if not exists` takes a ShareLock even when the index exists
Found by the PR #89 review: running `init_schema` inside a tick waited the full 15 s `lock_timeout` while
another session wrote to the table. Apply the schema once at process start (the ledger's `connect_ready` does),
never in a tick loop.

### [2026-10-03] gotcha — jsonb rejects NUL and lone surrogates: one bad string fails the whole batch
`insert … on conflict do nothing` of a feed window failed with `UntranslatableCharacter` on one `\u0000`, and the
window was retried and failed every tick. `db.jsonb_safe` strips NUL and replaces lone surrogates before insert.

### [2026-10-03] finding — the LLM feed reader on real captured text: 24 texts → 15 learnings, subjects need a guard
`uv run bazaar learnings --llm 24` (subscription, Jev picked opus-5-5; an earlier run got haiku-4-5 as the
default on an undecided verdict): "Chato holds firm on price (13) and dislikes haggling", "Abuela responds well
to politeness", "Abuela offers 5 P for El Organillero". The model wrote subjects as `dealer:chato` (copying
the `from` field), which our validation first dropped: the prefix is now accepted only when it matches our own
record of that id. LLM learnings stay `source: llm`, confidence ≤ 0.7, bound to nobody, and never block.

### [2026-10-03] gotcha — a background LLM reader is a cost, not a free extra: opt-in and spaced
PR #111's review replayed Friday's feed through the reader: 1,100 free texts → 125 calls (one per tick) with
no spacing, on the same subscription as the desk and duels. Now RUNTIME.md `llm_read_feed = false` by default,
at most one call per `read_feed_every_ticks` (10), doubled per failure, never while `.local/PAUSE` exists, model
capped at Haiku/Sonnet. An LLM reading of a notice keeps its own `source: llm` row (the dedupe key includes the
source) and never enters the blocker recall window (`recall(source="rules")`).

### [2026-10-03] finding — the hybrid recall finds the right lesson on Friday's real outcomes (N3)
`bazaar learnings --lessons --save` on a copy of the shared DB (tick 159): 26 outcomes → 26 lessons + 9 dealer
curves + 1169 dealer moves. `--query "open a thread with chato to buy LAV-08; his opening ask 33"` → thread 187's
lesson first (rerank +6.41, BM25 #1, vector #3: "every chato uncommon fill is 28-32, above our top bid 24");
"accept her opening ask of 7?" → thread 99 first (+7.34: an opening-ask deal voids the unlock credit); an
unrelated query ("list LAT-09 on rastro") scores −4 to −10 and returns nothing. 75–112 ms per query on a laptop
(BM25 + pgvector + MiniLM-L-6 rerank of 12). Models: fastembed 0.8.1 `BAAI/bge-small-en-v1.5` (0.067 GB) and
`Xenova/ms-marco-MiniLM-L-6-v2` (0.08 GB), ~3 s cold download, then cached in `<data_dir>/models`.

### [2026-10-03] gotcha — a dealer thread's topic is chosen by the team that opened it (N3 security review)
The feed publishes `thread.opened.topic` as sent (t08 opened one with `topic: {}`), and `evals.dealers.price_class`
turns any colon-free non-card string into `pack:<string>`. A forged "pack" name could become a dealer curve and a
lesson's text, then reach Jev. Fix: `learn/curves.KNOWN_CLASS` allowlist (`card:<rarity>`, `pack:sobre_*`, `sell`)
and recall returns only `source = outcome` rows by default. Treat every feed string as hostile, even "structure".

### [2026-10-03] gotcha — zsh reads `$B:s...` as a history modifier
`git show "$B:src/file.py"` in zsh became `…feed-reader-ragn/file.py`: `:s` is zsh's substitute modifier. Write
`"${B}:src/file.py"` with braces in every shell one-liner.

### [2026-10-03] finding — today's Abuela ladder is already the best on replay; a bigger step loses (N3)
Replaying every team's real Abuela threads (each brackets its own limit: countered bid < limit ≤ price taken
or offered), uncommons: 17→26 step 1 = share 0.415 (50/58 deals); step 2 = 0.372, because her final sits near
her limit and a big step overshoots it. Held-out (learn on ticks < 84, test after): 0.352 both. The auto-evolve
keeps today's Abuela ladder and skips Chato (fills 28-32 vs cap 26; rares 82-93 vs 80). With cap 32 the replay
closes 11/12 Chato uncommons at a mean 30.45 (share 0.467 vs the teams' 0.35): a human cap decision.

### [2026-10-03] finding — the learner escapes the first-bid trap on the simulator: uncommons 25 → 20-22 (N3)
Local `bazaar-sim` (2 s ticks; cash floor and hourly spend cap raised in memory for the run only). With no fills
seen, the strategy's ladder is 25→25, Abuela takes the first bid, and those fills became "the floor" (learned
25→25): a fill at our first bid only bounds her limit from above. Fix: probe from 80 % of the lowest fill when half
the fills took the first bid. A second team in the same world then paid 25, 22, 20, 21, 22 as the ladder moved
20→25 → 17→25 → 16→25. Ports 8765/8799 were taken by other workers' simulators: run yours on another port.

### [2026-10-03] gotcha — a "free" simulator port may already be another worker's simulator: check before you run
An e2e taker patched to 127.0.0.1:8815 ran LIVE in another worktree's `bazaar-sim` (my own failed to bind,
"address already in use") and closed 4 Abuela deals as sim-team1 in that world. Before any sim run: check the
port with `lsof -nP -iTCP:<port> -sTCP:LISTEN`, start the simulator, confirm the listener's process is yours,
and abort otherwise. `scripts/sim_smoke.py` refuses a busy 8765 on its own.

### [2026-10-03] finding — the taker now keeps our dealer threads (N12 part 3), with zero extra requests
On the simulator the live taker stored 3 threads (`deal`, opened/closed ticks) and 6 messages (our bid 25 and
our Spanish words, Abuela's "Deal! … for 25 P") from the reads it already makes. A thread opened by ANOTHER
process (a laptop's `dealer buy`) that closes before the taker sees it is not stored: the taker lists only open
threads. Follow-up: list all our threads in the same request and keep only the ones that changed.

### [2026-10-03] finding — holdings in Postgres: 1 `/me` per tick for taker + maker (was 2)
`holdings.py` (N13): the first process that needs `/api/me` in a tick reads it and upserts `me_snapshots`;
the others use it only while current (same tick, same `holdings_state.epoch` = no send of ours since, no
thread message of ours this tick, younger than `holdings_max_age_s`), else read live. Counted server-side
on a local simulator (8 s ticks, dry run, 10 ticks): `GET /api/me` 20 → 11. Live on the sim (20 ticks,
7 dealer deals) 40 → 36, including 7 album-first re-reads after deals that main never made. Kill switch:
`holdings_from_db = false` in GUARDRAILS.md. `bazaar status` prints `read: /me from db (tick, age, epoch)`.

### [2026-10-03] gotcha — a /me snapshot can be stale without any send of ours
A dealer may answer our bid and accept it inside the tick (it settles at once), and our accept settles at the
next tick boundary. So the epoch (bumped by every send) is not enough: a tick with a thread message of ours
is never served from the database, and every snapshot expires after 5 s. Postgres `now()` is the
transaction start: freshness uses `clock_timestamp()`, or a reader that waited on the lock looks younger.

### [2026-10-03] gotcha — another worker's simulator may own 127.0.0.1:8765
`BAZAAR_SIM=local` hardcodes 8765, and a teammate's `bazaar-sim serve` may hold it. Never kill it: for a
private run, patch `bazaar_agent.config.LOCAL_SIM_URL` in a wrapper (`config.LOCAL_SIM_URL = ...` before
importing `bazaar_agent.cli`) and serve the sim elsewhere. `scripts/sim_smoke.py` refuses a busy 8765.

### [2026-10-03] gotcha — parallel worktrees running `scripts/sim_smoke.py` collide on 127.0.0.1:8765
Two smokes started together both see 8765 free; one sim fails to bind and that smoke's CLI steps talk to the
OTHER worktree's simulator with the same `sim-team1` key (seen: `thread_exists: one open conversation per
dealer` in the dealer-buy step). Not a code failure: rerun when `lsof -iTCP:8765 -sTCP:LISTEN` is empty.

### [2026-10-03] build-error — a reset simulator world's rows hid the current tick from the holdings
symptom: after a sim restart every `/me` read was `live (older than 5 s)` and the agents never shared one →
root cause: the freshness query took the newest row with `tick >= current`, and the previous world's tick-19
row (age minutes) won over the fresh tick-1 row → fix: match the reader's tick exactly
(`test_a_row_from_a_reset_world_never_hides_the_current_tick`). It failed safe (live), never stale.

### [2026-10-03] build-error — a one-shot `bazaar status` never answered from the holdings
symptom: `read: /me live (team id not known yet)` on every run → root cause: the CLI process learns our team
id from its own first `/me` and exits; nothing cached it → fix: a live read that names our team calls
`identity.remember_team_id` (`.local/team_id`, per target), and a live read that disagrees corrects it.

### [2026-10-03] build-error — the holdings write hook could hold a send for seconds (review of #105)
symptom: in bazaar-mcp an `accept()` waited 2.8 s behind another thread's slow `/me`, and a first send waited
15 s for `schema.sql`'s lock → root cause: the reader and the write tracker shared one connection and one
lock held across HTTP, and the hook connected inline with `connect_ready` → fix: the tracker has its own
connection (plain `db.connect`) opened by a background thread, a 0.2 s lock budget, and a lost bump sets
`missed` (this process reads live; the next bump catches up). Also: the taker books an accept's spend
BEFORE the `/me` re-read (a failed re-read once skipped the spend row), and snapshot rows carry their world.

### [2026-10-03] finding — a dealer's "Deal!" to a team bid lands at the next tick boundary (Friday feed)
pr-reviewer on #105: 27/27 replies to a team bid came one tick later, and 37/40 of those settlements landed
at the boundary, before her message. A tick-start `/me` already sees the deal; the holdings' calm rule is
conservative, not required.

### [2026-10-03] build-error — a lock timeout does not bound Postgres I/O (security re-audit of #105)
symptom: behind a black-holed TCP proxy an `accept()` stayed blocked 20 s and a tick-start `/me` read 15 s,
although the hook's lock wait was capped at 0.2 s → root cause: `statement_timeout` is server-side and TCP
keepalives see a proxy that ACKs but never answers as alive; nothing bounded the client's wait → fix: every
holdings Postgres call runs on a worker thread per connection (`SharedDb.call`), callers wait a deadline
(send 0.2 s, read 5 s) and then go live; a stuck worker makes later reads skip the database at once.
Second bug found by the test: the worker's starter took the lock the hung worker held (own lock now).

### [2026-10-03] build-error — "wait for the game's /me" became an unbounded wait (security audit round 3, #105)
symptom: behind a proxy that black-holed the link right after `/me` returned, the tick-start read stayed
blocked 20 s+ → root cause: the caller extended its wait with `done.wait()` (no timeout) once the worker had
asked the game, and the worker then hung on the store/COMMIT → fix: a `Ticket` per read: the worker hands
the game's answer to the caller BEFORE storing it, the caller waits at most `ME_BUDGET_S` (the SDK's own
budget) for that answer, and a caller that gave up first cancels the job so it never asks the game.

### [2026-10-03] gotcha — the Agent tool's own `model` beats a subagent's definition, and takes aliases only
code.claude.com/docs/en/sub-agents#choose-a-model: a per-invocation `model` on the Agent call wins over
`AgentDefinition.model`; the bundled CLI (claude-agent-sdk 0.2.163) types it as `sonnet|opus|haiku|fable`
only. Definitions are fixed when the CLI session starts, and a new session forgets the chat (#108 review
P1), so the desk pins each family to our exact id (ANTHROPIC_DEFAULT_<FAMILY>_MODEL in the CLI env) and its
hook replaces the call's `model` with the alias of this request's choice (`updatedInput` replaces the whole
input); `set_model()` switches the orchestrator. One conversation, one session, a model per request.

### [2026-10-03] finding — Jev's desk choices per role, one batched call (local sim, ticks 0–2)
`bazaar agent chat --once` (N15): "buy LAV-09 under 90" → strategist/buyer/seller opus-5-5 0.99, duelist
haiku-4-5 0.90, desk undecided 0.73 (opus 0.82 on top) → sonnet-5-5 default; "buy LAV-10 for at most 60"
→ desk/buyer/seller sonnet 0.90–0.95, strategist opus 0.77, duelist haiku 0.94. Five questions in one Jev
call stayed inside `jev_timeout_s` 3 s. A 90 P request reused the cache in a new process (0 Jev calls) and
`AssistantMessage.model` proved it: desk ran on claude-sonnet-5-5, buyer on claude-opus-5-5.

### [2026-10-03] gotcha — the architecture board's 30 px Kalam title fits about 18 characters in a 332 px box
"LLM → Jev picks per move ✓" ran 85 px past the `llm_proposer` box (measured with SVG getBBox in a
browser); "LLM → Jev picks ✓" fits both LLM boxes. Measure a new box title or line before committing it.
`scripts/sim_smoke.py` also needs port 8765 free: another worktree's smoke may hold it for ~30 s; wait,
never kill it.

### [2026-10-03] gotcha — simulated duel and thread ids collide with real ones
The simulator numbers duels and threads from 1 like the game, so sim duel 85 is not our duel 85. A
simulator run must never write scores onto the real Phoenix traces: with `BAZAAR_SIM`, the agents'
in-loop evals and `bazaar evals run` keep their outcomes in the simulator's Postgres (no annotation),
and every trace goes to the `<project>-sim` Phoenix project (telemetry.tracing_config).

### [2026-10-03] finding — a dealer thread's old bids read `cancelled`; the deal's offer reads `settled`
`GET /api/threads/101` (read at tick 159, doors closed): our bids 720 (6), 732 (7), 744 (8) are
`cancelled`, 759 (9) is `settled`; Abuela's asks 728/737/752 `cancelled`. Thread 99 (LAV-03): our 672 (6)
`cancelled`, her 681 (7) `settled` — her OPENING ask, so that deal scored nothing on the ladder. The deal
price is the `settled` offer in the messages (`dealer.settled_price`); `open_commitments` counts one offer
per thread (the most cash) in case an old bid still reads open mid-thread (not observed live yet).

### [2026-10-03] gotcha — a refund dated with the CURRENT tick length lands after its spend
`t_hours` is game time played (tick 159 → 2.65 h at 60 s ticks) and the pace changes (60 s Fri, 30 s Sat).
Back-dating a cancelled bid's refund by `ticks × tick_seconds` after a 60 → 30 s change dated it 5 min
after its spend (hour's spend read −40). `refund_row` now uses `/api/clock` `max_tick_seconds` (+1 tick
for the rounded `t_hours`); an unknown created tick books no refund in the window.

### [2026-10-03] gotcha — a sim run without BAZAAR_SIM_DATABASE_URL writes the LOCAL docker Postgres
`BAZAAR_SIM=local uv run bazaar agent taker --live` said "ledger: shared Postgres table": the default
`DATABASE_URL` is `localhost:5433/bazaar` (docker compose), not Railway. Sim ticks (1–20) never meet the
real game's (159+), but to keep sim rows out of it entirely point `BAZAAR_SIM_DATABASE_URL` at a dead
address (`postgresql://nobody@127.0.0.1:1/none`): the ledger falls back to `.local/sim-client/ledger.jsonl`.

### [2026-10-03] finding — a dealer's offer lapses 2 ticks after it is made; a hold then leaves us bidding blind
All 1,024 dealer offers in the captured feed have `expires_tick - created_tick = 2` (security audit of #72).
After a kill-switch hold of 2+ ticks there is no standing ask, and `decide()` bid up to her OPENING ask, which
she took (a deal that scores nothing). Fix: `Negotiation.bid_cap()` keeps a bid below her opening until she
came down; with no bid left below it, we walk and reopen lower.

### [2026-10-03] gotcha — refunds dated at `max_tick_seconds` over-count at 30 s / 15 s ticks
Fail safe but costly: at 30 s ticks a bid cancelled more than ~30 min after it was posted gets a refund dated
outside the hour while its spend still counts (at 15 s, after ~15 min), so repriced bids can eat the 150 cap.
The exact fix is to date the refund at the matching spend row's `t_hours` (a ledger lookup by offer id);
left for after #62's ledger rewrite lands.

### [2026-10-03] gotcha — BAZAAR_SIM=local talks to WHOEVER holds 127.0.0.1:8765
Several sessions run `scripts/sim_smoke.py` / `bazaar-sim serve` on this laptop, all on port 8765. If yours
fails to bind (`[Errno 48] address already in use` in its log), every `BAZAAR_SIM=local` command you run next
writes to another session's simulator (and can break its smoke). Before any write: check your server's log
says it is serving, or `lsof -iTCP:8765 -sTCP:LISTEN` shows a process whose cwd is your worktree.

### [2026-10-03] gotcha — `GET /api/threads/{id}` lists messages in arrival order, not by id
Real thread 187 (Chato): ids `1145 t01, 1159 t01, 1153 chato, 1169 chato, 1176 t01, …`, so a slow reply is listed
AFTER our next bid; the feed agrees (4509 ours before 4519 hers). Who spoke last must be read by message id
(`dealer.see_history` sorts by id when every message has one). And a close on an ended thread is answered
`200 {"status": "deal"}` by our simulator (the real answer is unverified): treat any status but closed/walked
as "re-read the thread" (`negotiate.close`, taker `_after_refused_walk`).

### [2026-10-03] gotcha — `scripts/sim_smoke.py` on a private port: patch PORT, SIM, GUARD and LOCAL_SIM_URL
The smoke and `BAZAAR_SIM=local` both hardcode 127.0.0.1:8765. A wrapper that imports `sim_smoke`, sets
`PORT`/`SIM` to another port and `GUARD` to a dir whose `sitecustomize.py` runs the repo's guard and then sets
`bazaar_agent.config.LOCAL_SIM_URL` runs the whole gate there (children get only `GUARD` on PYTHONPATH). N14b
used 8815: `SMOKE PASSED in 18 s`.

### [2026-10-03] finding — a new page needs no restart; the risk is selling its cards (N14b)
The taker and maker rebuild the playbook from `/api/me` + `/api/catalog` every tick, and "released" comes only
from `/me` album pages (B26, #129), so El Retiro is ranked the first tick it shows up. What was missing: the
maker would list our only copy of a RET card as soon as one team traded RET (chaser) and the tape paid above our
value. `protect_page_sets` (GUARDRAILS.md, RET,CHA) refuses it in `check()` for every writer.

### [2026-10-03] gotcha — your own simulator port, without touching 8765 (adds to the two entries above)
Run the smoke or a proof from a scratch `git worktree` whose `config.py` `LOCAL_SIM_URL` and `scripts/sim_smoke.py`
`SIM`/`PORT` are patched to your own port (D1: 8805 for the smoke, 8811-8824 for proofs). Never commit that patch.

### [2026-10-03] finding — D1 proof on the live simulator: v2 beats v1, 0 deals outside our limit (decay 0.08)
`duel run --play --no-jev` over HTTP against `bazaar-sim` (3 seller/buyer pairs per team on one deadline, 12-tick duels,
price-only and two-issue sessions, 96 finished duels per run). Mean score (share × kept): honest zoo v1 0.268, v2 0.364,
v2 + B11 (min share 0.3, endgame 1) 0.383, + `duel_days_signed` 0.419; exploiters v1 0.169, v2 0.259, v2 + B11 0.318.
Outside-limit closes: 0 of 776. Rounds per deal: v1 6.2, v2 1.1. Reproduce: `docs/night/d1-sim-proof.md`.

### [2026-10-03] finding — six duels on one deadline can run out of accept ticks
`plan_moves` counts only duels holding an acceptable offer; when more rivals cross into our limit on D − 3 than ticks are
left, one duel ends with an acceptable offer unanswered (sim duel 86: rival 81 vs our value 87, three accepts wanted on
D − 2). 1 of 96 duels for v2 and for v1 at decay 0.08. A planner that also counts converging duels would accept earlier.

### [2026-10-03] gotcha — the simulator refuses a duel message after the rival accepted in the same tick
`refused duel_closed (duel N is live)`: the rival accepted our previous offer earlier in the tick, the deal settles next
tick, and the payload has no `accepted` flag to tell us. The deal still closes at our earlier offer; nothing is lost.

### [2026-10-03] finding — a real-game live writer now has no per-process ledger at all (#156, takes over #62)
Offline repro (two temp dirs, connector raising ConnectionError, `reserve_accept(999999, limit=1)` each):
main gave `[True, True]` on two `ledger.jsonl` files; now `open_ledger(live=True)` on the real game returns the
reconnecting `PgLedger` → `['refused', 'refused']` and no file, and two processes on one Postgres → `[True, False]`.
A live taker/maker pings the ledger before its tick's first write (`ensure_writable`), `/health` carries
`ledger: shared|down|local file`, and `dealer buy` HOLDS on a ledger blip (no walk). DATABASE_URL must be the
shared Postgres on every live service, or the process exits at start ("refusing to trade").

### [2026-10-03] gotcha — a raw `@` or `/` in a Postgres password moves part of it into libpq's host
`postgresql://u:SEC@RETPW@x.proxy.rlwy.net:12345/railway` parses to host `RETPW@x.proxy.rlwy.net`, and
`u:SEC/RETPW@...` to host `u:SEC`: a `host:port` log label then prints a piece of the password (#162 reviews).
`ledger_pg._target` now labels only a plain host/IP/socket with a numeric port; anything else is "unparseable",
never shared (a live process refuses it). Percent-encode passwords. Also never shared: host lists, `hostaddr`,
`127.1`/`2130706433`/`0x7f000001`, `*.local`, single-label names (compose services).

### [2026-10-03] gotcha — `scripts/sim_smoke.py` can only serve on 127.0.0.1:8765
The port is hardcoded twice (`scripts/sim_smoke.py` PORT/SIM and `config.LOCAL_SIM_URL`, which the CLI children
use), and the smoke refuses a busy port. With several workers on one laptop: `git worktree add --detach <scratch>
HEAD`, `sed` both files to a free port (check with `lsof -iTCP:<port> -sTCP:LISTEN`), run the smoke there.

### [2026-10-03] build-error — the taker's fake board gave every copy the rarity "common"
symptom: the S1 accept gate refused LAV-08 in `test_live_accepts_one_offer...` → root cause: `tests/agent_fakes.ask()`
hardcoded `"rarity": "common"` on every asset (the server builds the asset with its catalog rarity) → fix: `ask()`
takes the catalog rarity (`catalog_rarity(ref)`), and a bait passes `rarity=` explicitly.

### [2026-10-03] build-error — a per-tick duel re-read cache let a stale offer be accepted (review r2 of #146)
symptom: duel B's accept went out at 90 against our limit 104 → root cause: the S1 accept gate cached the tick's
first successful `/api/duels` re-read and checked a later accept against it, while B's rival moved in between
→ fix: every duel accept re-reads; only a FAILED re-read is kept, for its own tick (no 429 retry burst);
test `test_each_duel_accept_re_reads_so_a_rival_that_moved_after_an_earlier_accept_is_caught`.

### [2026-10-03] finding — the exact broker equals the free stall on every modelled bench; only an edge beats it
On #77's realistic bench (1,000 books × normal/hard × quote/limit rule) the exact matcher's efficiency is
identical to the stall's on all 4,000 (0.793 / 0.791 mean, 0 better, 0 worse): 0.5 session points, what the
free stall earns. On main's static bench too (`scripts/sim_market_test.py`: b1 0.892 vs 0.892, b2 1.0 vs 1.0).
Ties must follow the book order (stable sort, as the stall): sorting by id lost 2 of 200 books to the stall.
Beating the stall needs #84's edge (limit estimates, probes): opening our board venue alone buys the hook.

### [2026-10-03] gotcha — another worker's simulator holds 127.0.0.1:8765 (BAZAAR_SIM=local)
`BAZAAR_SIM=local` is hardcoded to :8765, so two workers cannot each run their own local sim through it.
`scripts/sim_market_test.py` and `tests/test_sim_venue.py` serve `bazaar_sim` in-process on a free port instead.

### [2026-10-03] build-error — the exact matcher realised less than the stall on 2 of 200 sim benches
symptom: property test `ours >= stall` failed (173 < 183) → root cause: equal quotes (two asks of 56) were
sorted by id ("b1-10" < "b1-6"), so we matched a different seller than the stall at the same quoted surplus,
and the hidden limits differ → fix: stable sort by price + a book-order term in the assignment weights, so
at 0 bps we pick exactly the stall's traders (tests/test_matcher.py, 200 benches against `_auto_bench`).

### [2026-10-03] build-error — a sim venue test opened nothing: `locked` at tick 0
symptom: the keeper logged "opening refused locked" and retried 10 ticks later → root cause: the simulator
unlocks El Chato (level 2, needed for a venue) at `chato_open_ticks`, never at tick 0 → fix: advance one tick
first (`/sim/tick`). `locked` stays a retryable refusal in the keeper (a level can arrive later).

### [2026-10-03] build-error — one Postgres blip locked the broker-key vault out of Postgres for good
symptom: (review round 2) after one failed connect, `KeyVault.ready()` never succeeded again, so the h6.5
opening would never come → root cause: the backoff raised, every caller's `except` re-armed the backoff,
so it never ran out → fix: a call skipped by the backoff raises `_Skipped`, which never re-arms it
(`venue.KeyVault._failed`; test `test_one_postgres_blip_never_locks_the_vault_out_for_good`).

### [2026-10-03] gotcha — /api/me: a venue next to `starter_broker_key` is the free stall, not ours
The kit's `Bazaar.me()` docstring: /me carries `starter_broker_key` while we have the free starter stall;
opening our own venue replaces the stall (RULES.md). `guardrails.runs_venue` reads it that way (the bond
reserve stays, our opening is not blocked). Unverified live: if the key stays after we open, the floor stays
370 all game; set `venue_bond_reserve = 0` then. The broker-key table is `venue_broker_keys` (target, venue):
#84 still creates an older `venue_keys` shape, which nothing reads.

### [2026-10-03] gotcha — stored /me loses `starter_broker_key`: read `has_starter_stall`
`holdings.without_secrets` (#105) strips every key-named field from a stored or answered /me, so
`guardrails.runs_venue` would take the free stall for our venue (bond reserve gone, h6.5 opening refused).
It now keeps `has_starter_stall: true` in the key's place. A snapshot written by older code has neither:
deploy taker and maker together, and pull before a laptop uses the shared database.

### [2026-10-03] finding — #71 ships with our venue OFF (allow_venue_open = false), by team decision
Sat 06:08: opening our venue replaces the free stall (RULES.md "Your own market"), and our exact broker only
equals the stall (0.5 of the bench points) in every simulation, unverifiable live before opening. While the
switch is off no bond reserve is held (`effective_cash_floor` = `cash_floor` 100). Turn it on only in a
closed-door window with Omar, once the broker has an edge (#84) or organic trades to serve.

### [2026-10-03] gotcha — `telemetry.scrub` also feeds the audit tables: put new masking in `scrub_for_span`
symptom: masking private numbers inside `scrub()` turned `cash_floor 270` into `[redacted]` in the `decisions` row
(test_status) → root cause: `decisions.scrubbed` calls `scrub` too → fix: `scrub_for_span` (span attributes only)
cuts a number named like a limit/cost/value/floor; `scrub` keeps our numbers for Postgres and JSONL.

### [2026-10-03] finding — tracing on vs off: the simulator smoke records byte-identical requests (N18)
`SMOKE_TRACING=0|1 SMOKE_DUMP=<file> uv run python scripts/sim_smoke.py` dumps the sim feed (types and payloads,
ids and ticks dropped): the 58 events (settlements, offers, thread messages with our words and prices) are
identical, and the run passes with a dead Phoenix on 127.0.0.1:6006. A span used to carry `bazaar.duel.limit` and
`bazaar.plan.max` in clear: both are gone.

### [2026-10-03] finding — Chato's final is his limit, and a step-1 ladder from low gets it (N14a)
Friday's feed, Chato's uncommons: t03 started at 13, stepped by 1 and took finals of 28/29/29 (threads 253, 234,
275). The big steppers paid 31-32 (228, 268). His finals came after 4-8 team bids (median 6), Abuela's after 4-9.
Repeating our top price brought a final in only 1 of 11 threads, so the patience play makes the ladder long
enough (at least 9 distinct bids) instead of holding at the top. `bazaar dealer finals` replays it: lift 0.15
closes 4 of 12 Chato uncommon threads at 28-29 and 11 of 15 rares (mean 88); lift 0.25 closes 11 of 12 and 15 of 15.

### [2026-10-03] gotcha — `bazaar-sim serve` without SIM_DATABASE_URL persists its world in .local/sim
A run that restarts the simulator resumes the old world (tick 149, our cash at the floor), which looks like
someone else's server. Use `SIM_DATABASE_URL=memory` for a fresh world each time, as `scripts/sim_smoke.py` does.
Separately, the strategy offered only the cheapest dealer per rarity (`strategy.quote_for`), so Chato never got
an uncommon thread while Abuela sold the same rarity for less (fixed behind the lift: `level_ladder`).

### [2026-10-03] finding — organisers' Saturday opening (09:19): 17 teams played Friday, duels now score
Source: `docs/transcripts/2026-10-03-morning-voice-memo.md` § 2 (organisers' talk before the Saturday market). Friday had
17 teams, not 18 (one never showed up); four team markets opened. Today: another pack drop, team markets open, duels later
in the day and now scored, and new dealers may arrive during the day with cards nobody has seen yet; 2 deals per minute.
Their hints: some teams paid a first offer above the card's value to them (know `your_value` before buying); repeating the
same "last price" moves nothing (matches the N14a finding above: repeating our top price drew a final in 1 of 11 threads);
half of Friday's practice duels ended with no deal. The 03:22 memo in the same file reads back the night's docs: no new facts.

### [2026-10-03] build-error — W4 trade desk (#79): what its reviews caught before the takeover
From Marius's report (`docs/night/w4-trade-desk.md`): the exact plan search hit `RecursionError` on pools of
1,100+ candidates (capped at 120: 4 per copy or wanted card); swaps first counted 0 volume toward the
counterparty cap; the live maker cancelled hand-posted offers (now `hands-off:<id>` ledger rows it never
touches); Friday's addressed vs public fill rates were first miscounted (34 % / 7 %, really 20 % / 6 %).

### [2026-10-03] gotcha — CliRunner's `.output` includes stderr: parse `.stdout` in JSON CLI tests
The CLI prints its target banner (`target: real game …`) to stderr, and click 8.2's `Result.output` mixes
stderr in, so `json.loads(out.output)` fails once a branch meets main (#79's `test_affinity`, #98's
`test_rivals`). Parse `out.stdout`, and keep every CLI note on `err_console` so `--json` stays pure.

### [2026-10-03] build-error — a ledger outage made the dealer bid her ask instead of holding (#79 review)
symptom: `_reserve_accept` caught `LedgerUnavailable` and returned False ("slot taken"), so `negotiate` sent
`meet_ask` (a bid at her ask) whose spend the dead ledger could not book → root cause: one bool for two
answers → fix: `Reserve` returns `None` when the slot cannot be read, and the dealer holds the tick
(`test_an_unreadable_accept_slot_holds_the_tick_instead_of_bidding_her_ask`).

### [2026-10-03] build-error — merging main into the N17 stack: a new ledger method must reach FallbackLedger too
symptom: `mypy` after merging main into #137: `open_ledger` returns `PgLedger | FallbackLedger`, not `LedgerStore`
→ root cause: #79 added `hands_off_ids` to the `LedgerStore` protocol, and main's #162 `FallbackLedger` (dry run:
Postgres, else the file) did not have it → fix: `FallbackLedger.hands_off_ids` + test. Same merge: #79's
`_reserve_accept` returned None on a ledger outage, main's dealer raises `Hold`; `_reserve_accept` now raises
`Hold` (main's message), and `negotiate` still holds on a `None` too.

### [2026-10-03] build-error — B4 accept_bids (#98): two money bugs its reviews caught before the takeover
1. A sell was priced from the copies `/me` holds, which still counts a copy in our own ask (or sold last
   tick, settling next): the last FREE copy was sold as a duplicate (+6.8 shown, −3.2 real once the ask
   fills and the page loses its bonus) → `score_offer(..., unavailable=)` prices from free copies only.
2. `market.parse_offer`'s bid branch never checked `want.assets`: a bid for `card:X` that also wants the id
   of our rare read as plain → any side key outside cash/assets/types/cards with a value is not plain.
Also: the sell path must re-read the kill switch after the duel grace wait, as the buy path does.

### [2026-10-03] build-error — #138's `accept_bids` sold into a bid without main's S1 accept gate (#146)
symptom: after merging main into the N17 stack, a sell into a board bid sent `accept(offer, assets=[copy])` with
no inspector row → root cause: #146 gated `_accept_one` (dealer + board asks); #138's `_accept_bid` is a separate
accept path written before the gate existed, and `board_gate` refuses every bid → fix: `accept_gate.bid_gate`
(a bid, the ref and price we priced, any copy, and the copy we hand over is that card in /me), checked in
`_accept_bid` before the duel grace and the slot; a block never takes the accept slot (tests in test_rivals.py).

### [2026-10-03] gotcha — the trade desk's 25 % plan share rule plans no swaps for a single thread
`trade_desk.build_plan` refuses any plan where one team passes `max_share` (0.25) of the PLANNED volume, so a
swaps-only plan of fewer than four teams is empty (fixtures: 0 swaps at 0.25 and 0.5, 2 at 1.0). The team
desk (N17) plans with `max_share = 1.0` and keeps fairness per deal (`swaps.judge`: our gain >= 3 P, their
share <= 0.6) plus the cumulative `max_counterparty_share` guardrail.

### [2026-10-03] build-error — a team swap gave away our only rare (found in the simulator, N17)
symptom: the desk's end-to-end run settled LAV-09 (held 1) for LAT-02 + 62 P → root cause: the trade desk's
`our_copies` offers one copy of EVERY card we hold (its loss includes the page bonus) → fix: the desk gives a
card only while we hold two free copies (`team_desk.spare`), at opening, adoption and accept.

### [2026-10-03] gotcha — in a team thread, a rival's "Deal." is not a reply to concede to
After a rival accepted our swap offer, the desk read its message as a reply and tried to concede: the cancel
was refused `offer_accepted`. An offer of ours reading `accepted` in the thread's `standing_offers` now marks
the thread as waiting for its deal. The simulator's rivals also stack one counter per tick (old ones stay
open): read every standing offer, judge each, log a refusal once.

### [2026-10-03] build-error — `--json` stdout began with a WARNING line after #105 (holdings)
symptom: the sim smoke's `bazaar swaps --json` step failed: stdout started with `WARNING bazaar_agent.holdings:
... Postgres unavailable` → root cause: the CLI's `logging.basicConfig(stream=sys.stdout)` (stdout because
Railway files stderr as errors) → fix: `cli.log_stream()`: stdout only when RAILWAY_ENVIRONMENT is set, as the
target banner already does; stderr elsewhere, so every `--json` command stays pure JSON on a laptop.

### [2026-10-03] gotcha — closing a team thread cancels only OPEN offers; an accepted one still settles (N17)
A rival can accept our swap offer and close the thread in the same tick: the deal settles, the thread reads
`closed`. So the team desk never refunds a spend because a thread ended or a close answered 200: it books the
cash we add when the offer is POSTED and gives it back only when a read shows that offer `cancelled`,
`expired` or `failed` (a cancel's own answer, or the thread re-read for at most 10 ticks); otherwise it stays
booked (over-count, fail safe). Found by security-auditor rounds 2-5 on #123 against the in-process simulator.

### [2026-10-03] build-error — N17's team swap accept had no S1 accept gate either (merge with main)
symptom: `_accept_swap` sent `accept(their_offer, assets=pick)` with no inspector row once main's #146 gate was in
→ root cause: #123 checks the swap structure when it proposes (`read_offer` + `is_the_planned_swap`) but the accept
was a third path beside `_accept_one` and `_accept_bid` → fix: `accept_gate.swap_gate` reads the thread's standing
offer again (still open, from that team, to us, same cards and cash, our copy of the planned card in /me), before
the slot; kind `team`, kept off the public view by the status allow-list. Test in test_team_desk.py.

### [2026-10-03] finding — fee announcements come with 2 ticks' notice; the sim charges the OLD fee at settlement
Friday's four `venue.fee_announced` events (v03, ticks 134→136, 145→147, 154→156, 159→161) all gave exactly 2
ticks' notice. Friday had 0 settlements on team venues, so which fee the real server charges at the settlement
tick is unknown; the simulator charges the old one (`settle_due` runs before `venue_tick`) and rounds fees
half-to-even while the tape rounds up. The taker prices the higher fee from `effective_tick ≤ tick + 2` (B19).

### [2026-10-03] gotcha — under heavy load a full `pytest` run can die with a faulthandler dump
Twice on Sat morning (load from ~10 parallel review agents), `uv run pytest` ended with no summary and a
"Extension modules: psycopg_binary.pq, …" dump instead; the same commit passed on an immediate rerun (1127 and
1172 passed). Rerun before blaming the change; a crash that repeats on an idle machine is real.

### [2026-10-03] finding — duel_policy v2 sends nothing for many ticks against a conceding rival; the smoke plays the duel out
Private sim, 16-tick sessions: v2 held while the sim's rival conceded every tick, then accepted at D − 3 and D − 2
(94 and 88, 0 rounds, 56–58 % of the pie). The old 3-tick smoke step saw no move, so `scripts/sim_smoke.py` now runs
`duel run --play` to the session's deadline (SIM_DUEL_TICKS 24) and needs every duel closed as a deal inside our
limit. Two-issue session, `duel_days_signed` false: v2 valued the rival's 74 at 10 days as 36 (cost 40), offered 42 at
0 days and made 2 of a 63 pie; the sim's scoring put the 74 offer at +72. After the rival took our 42, v2 sent the second
"last offer" and the sim refused it (`duel_closed`): no cost, but a step that has a " refused " marker fails the smoke.

### [2026-10-03] build-error — `duel run` crashed when the team client could not read /me (N16)
symptom: the duel CLI tests exited 1 with `AttributeError: 'DuelClient' object has no attribute 'me'` → root
cause: the new bluff book reads our team id once at start and only caught `BazaarError` → fix: `_our_team_id`
fails open on any error (the tactic lessons then bind no team); a duel loop never waits on it.

### [2026-10-03] finding — in the simulator the words never move a price; only the tactic choice changes (N16)
Sim run with tactics on (tick 0-17): Abuela got kindness only and dealt at 7 after 2 bids; El Chato moved one
per our step ("You moved 1, I move 1") whatever the bluff, and both sim rivals conceded 1 P per tick, so every
tactic scored "toward" (+1). The sim's dealers read words only for mood (kindness, rudeness, injection). Expect
the same from real dealers ("their prices come from their own rules"): lying should pay, if anywhere, against
LLM duel rivals; the no-gain rule switches a tactic off where it earns nothing.

### [2026-10-03] gotcha — every worktree's simulator smoke binds 127.0.0.1:8765
BAZAAR_SIM=local has a fixed address, so two workers running `scripts/sim_smoke.py` at once collide
("address already in use", the second sim exits 3). Wait until `lsof -iTCP:8765 -sTCP:LISTEN` is empty; never
kill another worktree's simulator.

### [2026-10-03] gotcha — the duel CLI test fakes never ran past the first tick's `?done=true` read
`DuelStore.read_finished` is True on a runner's first tick, so `duel run` calls `client.duels(done=True)`; the
`DuelClient` fake in tests/test_jev_journal.py takes no `done`, and the TypeError is swallowed by `run_per_tick`
("tick loop: tick N failed"), so code placed after it in `on_tick` never ran in those tests. A fake for
`duel run` needs `duels(self, done=False)` (tests/test_bluff_wiring.py does).

### [2026-10-03] gotcha — git rerere is on and its cache is shared by every worktree
`git merge origin/main` in a scratch worktree printed "Resolved '.ai/memory.md' using previous resolution": a
reviewer's earlier scratch merge had recorded it. Check the result (`git diff HEAD`) before trusting a rerere
resolution; `git rerere forget <path>` drops a bad one.

### [2026-10-03] gotcha — a PR stacked on a base that was rebased before it merged conflicts add/add everywhere
#131 was cut from #96's pre-rebase commits; `git merge origin/main` then hit 29 conflicts, mostly add/add in
`learn/*` (the same files from two histories). Fix: apply only the PR's own commits onto main, `git diff --binary
<old base head> <PR head> | git apply -3` in a scratch worktree of main, resolve the few real conflicts there, and
use that tree for the merge commit (`git merge --no-commit origin/main`, then `git read-tree --reset -u <tree>`).
Under `duel_policy = v2` the duel words stay main's plain templates, so N16 tactics are off for duels there.

### [2026-10-03] build-error — an adopted orphan thread waited 2 more ticks instead of walking (B17 on #72)
symptom: `test_a_bid_in_between_resets_the_quiet_count` failed after B17 was squashed onto #72's round-3 head: thread
40 was read, never closed → root cause: #72's `patient()` waits up to `MAX_WAITS` ticks for her answer to a bid that
is not answered yet, and the adopted `Negotiation` started with `waits = 0` → fix: `_adopt` starts it with
`waits = MAX_WAITS` (her answer already had `orphan_after_ticks` ≥ `MAX_WAITS` ticks to come in).

### [2026-10-03] gotcha — after a restart, only the old taker's own threads may be touched (B17 review)
A quiet thread is not an orphan: a laptop `bazaar dealer buy` paused by its own `.local/PAUSE` stops bidding,
and the Railway taker cannot see that pause. The taker now owns a thread only when its decisions log names it
(`dealer_opened` rows carry the thread id); it adopts those on sight, because a fresh bid's "Deal!" can land
a tick after the new process starts. A `process_started` row marks the first process that writes
`dealer_closed`: earlier threads are never booked again (their process booked them silently).

### [2026-10-03] gotcha — decision inputs are scrubbed: a host name is stored as `[redacted]`
`DecisionLog` writes `inputs` through `telemetry.scrub`, which redacts anything that looks like an internal host
name (`Omars-MacBook-Pro.local` → `[redacted]`). An identity meant to be compared later must be a token the
scrubber keeps: `decisions.writer()` stores a short hash (`w` + 10 hex) of `RAILWAY_SERVICE_ID` or the host name.

### [2026-10-03] gotcha — the vendored SDK re-sends a 429 (GET and POST) and only a 4xx "costs nothing"
`bazaar_sdk._Http` re-sends a `rate_limited` call up to `retries` times, writes included, and waits 15 s per
attempt: on one key shared by every process that fills the 5 req/s bucket further. `TeamBazaar` (B18) never
re-sends a refusal or a write. RULES.md's "a refused request costs nothing" is about a `4xx`: a 5xx (or an edge
502/504) may come after the game applied it, so it keeps the team's accept slot and books the spend (#141 review).

### [2026-10-03] gotcha — a lapse looks exactly like someone else's cancel; the feed tells them apart
A bid gone from `/api/me/offers` at or after its `expires_tick` may have lapsed or been cancelled by `bazaar
flatten` / the desk, which already booked its refund. The live feed emits `offer.cancelled {offer, venue}` for
a cancel and nothing for an expiry (Friday: 644 offers past expiry, 136 cancelled, ≥ 460 silent); the simulator
emits one with `reason: "expired"`. The maker's lapse refund (B14) checks it, and skips under the kill switch.

### [2026-10-03] finding — the feed alone places 287 assets; LAT-10 is the scarcest rare (2 copies, tick 159)
`uv run bazaar supply` (N14b) at Friday's close, before any card scan: 287 assets placed from settlements and
listings, 42 packs opened. Complete pages that can exist now (fewest copies of a page card): LAT 2 (LAT-10),
MAL 3 (MAL-09/MAL-10), LAV 4 (LAV-09), SAL 4 (SAL-09/SAL-10). A starting asset never traded keeps the block
of its id: team k was dealt ids 15k−14…15k, so a scan names who holds an unmoved rare even though
`/api/cards/{id}` says only "a team".

### [2026-10-03] finding — a card scan places every scarce rare: 538 assets, no refusal at 2 req/s (05:42)
`uv run bazaar supply scan --rate 2` read ids 1–538 (doors closed, tick 159), then 5 unknown ids. With the
scan, the holders of every rare with at most 5 copies are placed (unplaced 0–1): LAT-10 t03, t15 · MAL-09
t11, t12 · MAL-10 t08, t09, t12 · LAV-09 t05, t07, t10, t14 · SAL-09 t13, t16, t17, t18 · SAL-10 t02, t13,
t17, t18 · LAV-10 t04, t05, t07, t10, t14. Rescan with `--from-id 539` for new pulls (incremental).

### [2026-10-03] finding — the flag rule fired 0 times on Friday's dealers; Jev says flags stay off until L4 shows
`uv run bazaar flags precision --feed-dir <capture>`: 1,027 dealer offers (Abuela 805, Chato 217 with a known topic),
0 would-flag, 5 with an empty topic `{}` (thread 44). Jev `enable_bad_faith_flags` (questions/flags.json) on that
state: no (0.06, margin 0.88). A hypothetical L4 state (4 would-flags on an untrusted dealer's 40 offers, 0 on the
trusted ones): yes 0.83; the same with 1 would-flag on a trusted dealer: undecided 0.33. Re-run when L4 opens.

### [2026-10-03] gotcha — `injection_flags` missed zero-width splits, combining marks, fillers and homoglyphs
"Ign\u200bore all previous instructions", "ig\u034fnore …", Hangul fillers (U+3164, U+115F, U+FFA0), the braille
blank and Cyrillic/Lisu look-alikes matched no pattern (S1 hostile-text tests, #152 audits). Fullwidth digits were
already matched (Python's `\d` is Unicode). The patterns now read NFKD text without Cf/Mn/Me or those fillers;
`odd_unicode` names the hiding (emoji joiners, "nº", "µ" and "ʼ" excepted); 0 tags on 1,091 Friday dealer texts.

### [2026-10-03] finding — bad-faith flags: precision over recall, and only to dealers a human opted in
Three #152 reviews showed honest out-of-stock words read like a trick in every shape ("La Tabacalera? Ya no
tengo.", "Rare card? Not today.", "I wish I still had it"), and Jev says yes to flags on counts alone. Decision:
any denial word anywhere in a dealer's message means it claims nothing (the swap is still refused: block, never
flag), a flag goes only to a GUARDRAILS.md `flag_dealers` dealer a human opted in after reading its would-flag
words in `bazaar flags precision`, at most `max_flags_sent` ever per data dir, never twice. A missed flag loses a
bonus; a wrong one costs points.

### [2026-10-03] gotcha — the pitch kit mixed two red-team counts and four duel numbers
`docs/pitch/story.md`/`qa.md` say 129 red-team cases; the W5 report says 168 (no source has 129). The duel
"0.27" baselines differ: simulator v1 0.268/0.278 (modelled rivals) vs the real Friday evals mean 0.279 (estimate, practice).
`docs/pitch/claims.md` tags every claim REAL/SIMULATED/PENDING/UNVERIFIED; quote only from it.

### [2026-10-03] finding — dealers buying from us DO raise their bid; `bazaar dealer sell` sells duplicates
Friday feed, 104 sell threads (`{"sell": {"assets": [id]}}`): Abuela bids `give.cash` and moves up when the
team moves down (commons 5→6, uncommons 12→16, 20→23), then a `final`. The simulator modelled a buyer that
never moved; it now raises one prima per move of ours up to `buy_ceiling` (Abuela 0.65 of book). A sale is a
ladder deal: `dealer sell` never closes at her opening bid. Private sim: LAT-04 sold at 6 (her opening 5).
### [2026-10-03] finding — our model priced buys above the official value; every buy is now capped at /api/me/value
Day-2 hint 1: `GET /api/me/value?card=` = our value of ONE more copy (book × affinity × copy marginal), the value the
score counts trades at. Our model adds a page-bonus share and lands higher (MAL-06 official 27.5 vs ours 36, SAL-07
32.5 vs 50.4). `guardrails.check()` now refuses a card buy above it (`official_value_margin`, read last, once per card
per tick, a failed read refuses). First proof, the sim smoke: `dealer buy LAV-01` walked at "price 8 > official
value 7" (LAV affinity 0.7). Tests run the cap only when marked `official_values` (tests/conftest.py).

### [2026-10-03] gotcha — a lone surrogate in another team's text stops a loop that writes it as UTF-8
An emoji cut in half by a JS/TS string slice reaches us as a lone surrogate (`"\ud83d"` in JSON). `json.dumps(...,
ensure_ascii=False)` written to a UTF-8 file raises `UnicodeEncodeError`, and Postgres jsonb rejects it raw or escaped.
`duel run` logged the raw /api/duels response that way before planning, so one such rival message stopped every duel
move each tick (fixed in #173: ASCII-escaped JSONL, `db.jsonb_safe` for the duels table). Same pattern elsewhere (other
owners): `feed.py` capture, `monitor.py`, `llm/chooser.py`, `runtime/mcp_server.py`, `agents/status.py`.

### [2026-10-03] finding — Radio Rastro's `news.posted` is in the public feed; Pilar is kind "collector" and sells only gold packs
`/api/levels` (tick ~330): Radio Rastro active since game hour 3.675; `news.posted` events (payload id, source, headline,
body) are in `/api/feed` too, so the taker's sentinel reads them at no request cost and backfills `/api/news` +
`/api/schedule` once per 10 ticks. `/api/dealers`: Doña Pilar `kind: "collector"`, level 3, opens to all at game hour
5.508; she sells only `sobre_oro` (list 420, 1/team/hour) and buys uncommon/rare/epic (SAL, RET loved). Our taker never
buys her pack while `max_price_pack` = 20; selling to her needs `bazaar dealer sell --dealer pilar` (no runner sells
to dealers). Schedule: "Salamanca fever: Pilar pays 25 % over book for Salamanca" from game hour 9.15 to 11.15.
### [2026-10-03] gotcha — rich wraps a counterparty's long text to column 0, whatever you indent the first line with
`console.print(f"    {words}")` indents only the first line: the wrapped rest starts at column 0, and padding made
of "printable" blanks (U+2800 braille blank, U+3164/U+FFA0 Hangul fillers) can push a forged line there (#176 review).
Print untrusted text as `Padding(Text(words), (0, 0, 0, 4))` (literal, every wrapped line indented) after blanking
unprintable characters, those fillers, and the characters rich measures 0 wide but terminals draw 2 wide (skin-tone
modifiers U+1F3FB-1F3FF, regional indicators U+1F1E6-1F1FF: the terminal itself would wrap to column 0)
(`flags_cli.printable`).

### [2026-10-03] gotcha — one exception in a bazaar-sim tick stopped its clock for good while /api/health said ok
`app._clock_loop` had no try/except: a raising rival (or a failed world save) killed the background task, the world
froze at that tick and every health check still answered ok. #178 holds a raising rival for the tick and makes the loop
log a failed tick or save and go on (the tick counter moves first, so a failure never retries in a hot loop).

### [2026-10-03] finding — at 15 s ticks every agent finishes in under 4 s; the taker's pack gate asked Jev every tick
`scripts/tick_profile.py` on a scratch merge of the Sunday PRs (#89 #96 #112 #91 #105 #108 #111 #71 #72) against a
local `bazaar-sim` at 15 s ticks, 40 ticks of taker + maker + duels together (SP1): at 100 ms per request the taker
took p50 1.12 s / p95 1.43 s, the maker 0.65 / 1.07 s, the duels 0.60 / 0.69 s of a 12.6 s budget; with 250 ms per
request and Jev 1 s slower, 3.31 / 1.55 / 2.04 s p50. 0 ticks over budget, 0 decisions dropped, 0 × 429, busiest
second 8-12 keyed requests (bucket 20), mean 0.66-0.68 keyed req/s for all three (limit 5). The taker asked Jev
`spend_pack_slot_now` on every tick for an unchanged state (40 calls in 40 ticks): `jev_cache_ticks` cut it to 12-13,
and `parallel_reads` brought the taker to p50 0.24 s (100 ms) / 0.53 s (250 ms + slow Jev).

### [2026-10-03] gotcha — local simulators share ports across workers: use 8900+ and refuse a busy port
Another worker's e2e taker traded on our `bazaar-sim` at 127.0.0.1:8815 (ticks 14-18, as sim-team1): every run on that
sim was discarded and re-run. The SDK opens a new TLS connection for every request (~25-30 ms from Madrid to the game,
measured on the keyless clock), so the simulator's ~1 ms answers understate a tick: profile with `SP1_LATENCY_MS`.

### [2026-10-03] finding — with #151, bazaar-sim duels score like the real game and share the team's one accept per tick
Since #151 merged (Sat 3 Oct): a deal keeps `(1 − d) ** rounds` with `rounds` = the fewer priced messages of the two sides (verified on 26/26
practice payloads; it was our priced messages and `** (rounds − 1)`), so simulator duel points drop about 6 % (scripted
team, 96 duels: 41.30 → 38.82). A duel accept now uses the team's `accepts_per_team_per_tick` slot, like a market accept
(a second one in the tick is `wait_for_tick`). New knobs, unset = today: `SIM_DUEL_STYLES`, `SIM_DUEL_DECAY`, `SIM_DUEL_PAIRS`.

### [2026-10-03] gotcha — a fresh `run_per_tick` handles the CURRENT tick at once
`run_per_tick(..., max_ticks=1)` starts with no last tick, so its first `on_tick` runs in the tick we are already in:
a "retry on the next tick" built on it went out in the same tick as the 429 it answered (PR #72 round 5). To act
on the next tick, read the clock until `tick` is strictly later (bounded), as `negotiate.retry_close_next_tick` does.


### [2026-10-03] gotcha — with team threads on, a taker without a Jev key sends no swap at all
`team_swap_jev_gate = true` (N17-enable): every swap proposal and every accept of a team's offer needs Jev
`team_swap_worth_it` to say a decided yes at 0.75. `agent taker --no-jev`, a missing `TYPESAFE_API_KEY` on the
service (judge answers `undecided`), a Jev timeout or a tick with < `jev_min_budget_s` left all mean no swap
(fail closed, a `rejected` decision row with the verdict). The cash we add to swaps is booked as `team:<card>`
spend rows (`team_swap_max_cash_per_hour` sums them), still counted in `max_spend_per_game_hour`.
### [2026-10-03] gotcha — a test connection left idle in a transaction hangs the schema teardown forever
An integration test that failed before `conn.close()` left a psycopg session `idle in transaction` (its last select
holds a lock), and the `schema` fixture's `drop schema … cascade` waited on it with no timeout: pytest hung for
minutes. Use `conn.autocommit = True` and `try/finally: conn.close()` in such tests. Also: macOS has no `timeout`
command, so `timeout 60 uv run pytest …` fails with 127 and prints nothing; run it in the background instead.

### [2026-10-03] finding — the catalog shows a release before anyone trades it: CHA is `released: false` (Sat)
Keyless `GET /api/catalog`: LAV/MAL/LAT/SAL `+0h`, RET `sat+0h`, CHA `sun+0h` with `released: false`, 12 cards
each, none `hidden`, CHA minted 0. The taker's cards heartbeat (`cards_heartbeat.py`) diffs the catalog it already
reads each tick (no request): Sunday's flip reports 12 `set_released` events with the dealers that sell/buy each.

### [2026-10-03] gotcha — a test connection left idle in a transaction hangs the schema teardown forever
An integration test that failed before `conn.close()` left a psycopg session `idle in transaction` (its last select
holds a lock), and the `schema` fixture's `drop schema … cascade` waited on it with no timeout: pytest hung for
minutes. Use `conn.autocommit = True` and `try/finally: conn.close()` in such tests. Also: macOS has no `timeout`
command, so `timeout 60 uv run pytest …` fails with 127 and prints nothing; run it in the background instead.

### [2026-10-03] finding — the published traits predict Friday's dealer limits within 5 % (N19)
Limit ≈ list × (1 + 0.25 × (shrewdness − generosity)): Abuela uncommon 22 (fills p50 22.5), packs 23 (21-22), Chato
uncommon 30 (29-30), rare 89 (89.5-90.5). Opening ≈ list × (1.12 + 0.17 × shrewdness). The patience trait barely
moves the bids before a final (4-6 for both). Replayed on Friday's threads (tests/test_persona_replay.py), the trait
prior's ladder scores the same share as the learned one (Abuela uncommon 0.402 = 0.402, packs 0.471 vs 0.465, Chato
uncommon 0.467 = 0.467, rare 0.476 vs 0.467). Step 1 beat step 2 on Abuela (0.40 vs 0.33).
### [2026-10-03] gotcha — a read-only Postgres role still gets PUBLIC's grants, and default privileges re-grant secrets
`bazaar_team_ro` (#184): CONNECT to every database, TEMP and EXECUTE on `pg_advisory_lock` come from PUBLIC, so a
role-only revoke does nothing (the RO role could take our ledger's advisory lock and stall accepts; documented).
`alter default privileges ... grant select on tables` also covers a later secret table or a view over one: the
script creates `venue_broker_keys` first, then revokes it. `pg_stats` hides columns the role cannot read.
### [2026-10-03] finding — whether a duel accept uses `accepts_per_team_per_tick` was never observed
Up to tick 548 (Sat, Duels I): 17 duel accepts on 17 ticks and 11 taker accepts on other ticks (`decisions` and
`ledger`), so no tick ever held both, and no 429 in the bazaar-duels logs. Our shared ledger always gives the
slot to one process, so passive data can never answer this; only a live probe (a duel accept, then a trade accept
in the same tick: `wait_for_tick` = shared) can. The simulator's shared slot is our assumption. We keep counting duel
accepts (GUARDRAILS.md `max_accepts_per_tick`). The same day, none of the taker's 528 rejections was a lost slot:
all were `cash_floor`, `max_spend_per_game_hour` or `max_price_*` (PR #201).

### [2026-10-03] finding — duels leave short merge windows; the watchdog replay found no trips on real rows
`bazaar deploy-guard` at tick 556 (session live): DO NOT MERGE, duel 2481 one tick from its deadline, safe only
ticks 558–560 before duel 2496 enters its 4-tick guard. Merge through `scripts/merge_safe.sh <pr>`. A read-only
replay of the watchdog rules on the shared DB (windows ending ticks 300/400/480/555) tripped nothing; its storms were
real (SAL-07 refused 86×, SAL-08 53×). Breakers fail OPEN with one read per tick and a 15 s backoff after a failure.
### [2026-10-03] finding — our maker's asks lapse unsold: 20-tick life, top-of-market price, never repriced (tick 466)
From the shared `feed_events` (ticks 0–466) and `executions`: the maker listed 74 asks on rastro and 1 was cancelled;
our only venue sales (LAT-08 25, SAL-10 76, LAT-09 68) were uncommons and rares. Every one of our listings comes back
with `expires_tick` = `created_tick` + 20 (all 74 `list_offer` rows in `executions` since Friday), although `maker.py:103` sends
`expires_in_ticks` = 40, the SDK default; other teams' rastro listings live 15–60 (t05 40, t04/t18 60), so the server is
not capping rastro at 20 (why ours halves is UNVERIFIED: one hand post with another value would tell). That is 10 min at
Saturday's 30 s ticks, 5 min on Sunday. Our ask sits at the top of what clears on venues: commons 10–12 vs a median of 9
(p80 10, 44 fills), uncommons 26 vs 22.5 (p80 26). And a relist keeps the same price: SAL-01 was posted 15 times at 10,
MAL-02/SAL-03/LAV-04 8–9 times at 10, MAL-08 8 times at 26. So a common or uncommon ask waits 20 ticks for a buyer at the
top of the range, lapses silently (the live feed has no event for an expiry), and comes back unchanged, spending one of
the 12 listings per tick each time. Fix candidates: step a relisted ask down toward the venue median (never below our
value + `sell_min_surplus`), sell the commons to a dealer instead (#183), and post with a longer `expires_in_ticks`.

### [2026-10-03] finding — dealer threads come close and end at her price or not at all: the deals give the ladder ~0 (tick 491)
Asked as "we seem near an agreement but don't reach it". From `feed_events` + `decisions` (ticks 0–491). Duels are NOT
it: Duels I (session 2) closed 7/7 as deals, and all 19 practice no-deals were one-sided (we were offline, or the rival
never spoke). Team threads: none ever opened with us. It is the dealer threads (12 opened, 7 deals, 5 no deal):
- Each side re-posts every tick, so the 2-tick (Fri) / 4-tick (Sat) `expires_tick` of a thread offer is not the cause.
- The dealers mirror our step ("I match what you move", "You moved two, I moved nothing"): with `step` 1 the gap closes
  ~2 P a tick, so an opening 8–15 P above our first bid needs 4–7 ticks to meet in the middle.
- We do not wait for that: three deals were `dealer_accept` with reason "jev: accept (inside limit)" after only 2–3 of
  our bids, at her current ask (bids 17, 18 → paid 25; 10, 11 → 25; 82, 83, 84 → 95). `dealer.decide` accepts only when
  her ask meets our next bid or is final, so it is the Jev step that turns an open counter into taking her price. A deal
  at her ask captures almost none of her range, which is why our ladder share is 0.009 with 7 deals.
- Thread 187 (Chato, Fri): we stepped 17→24 and he 33→31; our `max_price` sat below his floor, so we walked 7 apart.
- Thread 324 (Abuela, SAL-07): we stopped at 20 vs her 25 at tick 166 because the taker found the same card on rastro and
  spent the tick's one accept on board asks; the thread was never closed and idled out at tick 207, holding one of the six
  conversation slots for 40 ticks.
Fix candidates: let Jev accept only what `decide` accepts (or a final) inside a thread; keep stepping until she stops
moving; close a thread the moment its item is bought elsewhere.
### [2026-10-03] gotcha — a redeployed `duel run` stepped back on its own offers and spoke twice in one tick
Every merge to main restarts `duel run` (~30 redeploys on Saturday morning). Against a rival that has not priced,
v2 waits `first_offer_wait` (max(`duel_open_wait_ticks`, `duel_stall_ticks`) = 3) ticks before its first offer, but
`payload_start` restarted the clock at that first offer: 3 ticks of concession lost, so a seller's ask went up and
a buyer's bid down (9 times in duel session 2, one per restart tick, from `duels.payload`). A restart inside a tick
the old process had already offered in also sent a second message, refused `wait_for_tick` (8 in the Railway logs).
Fix: `payload_start(..., wait)` backs our earliest message off by the wait, and `duel run` holds an offer when the
duel already shows one of ours this tick (`spoke_this_tick`). Duel sends are not in `executions`: read `duels`.

### [2026-10-03] gotcha — a log line that says " refused " fails the simulator smoke
`scripts/sim_smoke.py` fails a step on any ` refused ` in its output (CRASH_MARKERS), our own WARN lines included: the
human-approval board's first fail-closed note ("… is refused (fail closed)") failed `agent taker --live`. Word new
WARN lines without " refused " (HA1 says "no trade at or above … goes out").

### [2026-10-03] finding — Jev's guardrail review keeps every rule; the official value blocks every cheap dealer buy (SG1, tick 668)
`questions/guardrail_review.json` on the live state: cash_floor keep_50 0.86, max_price_uncommon keep_26 0.78,
dealer_final_lift keep_0 0.96; dealer_sell_enabled, jev_accept_min_share, human_approval_above and
team_swap_max_cash_per_hour undecided (kept). Why ladder points are ~0: Abuela's common fills in the last 500 feed
events are 10-12 while our official value of a missing RET common is 7 (LAT 5), so no honest buy reaches her
range; the SG1 ladder probe plans nothing until fills drop or a card's official value rises. Strategy gates on the
same state: ladder_probe undecided (0.32), dealer_sell undecided (0.60). Our own asks on v19 are impossible:
RULES.md "You cannot trade on your own venue with your team key" (`self_venue`).

### [2026-10-03] finding — with Omar's aggressive risk posture Jev still changes no guardrail (SG1 re-run, ~16:30)
Same questions plus `risk_posture: aggressive`: decided keep_50 (0.79), keep_0 lift (0.93) and keep_v19 (0.75,
v19 stays open for the benches). Undecided, so kept: dealer_sell_enabled (0.41), max_price_uncommon (0.51),
duplicates_reserve (list_duplicates 0.60, was 0.87 in a looser earlier ask), podium_venue_rule (avoid_unless_2x
0.74, one hundredth under the bar). Strategy gates: ladder_probe 0.36, dealer_sell 0.70 (leaning yes). A verdict is
asked once and applied as given; re-asking until it says yes would launder the bar.

### [2026-10-03] finding — Opus as the decider (BAZAAR_DECIDER=llm) answers in 6.2-9.1 s through the CLI (LD1)
Three live `judge()` calls on the laptop's subscription token (duels.json 2 questions, negotiation.json 3 questions):
7955, 6197 and 9067 ms, each a fresh Claude Code CLI process with structured output. Verdicts came back in Jev's shape
and cleared the bars (duel_move accept 0.78 vs 0.75; negotiation_move accept 0.75). An 8 s budget would drop about a
third of them: the default is 12 s, and the duel and maker gates ask only with timeout + 1 s of the tick left.

### [2026-10-03] gotcha — `test_duel_run_bluffs_in_the_text_only…` fails ~6% of runs on main too (secret bluff seed)
Each `TacticBook` in `duel run` draws `secrets.randbits(64)` as its tie-break seed, and 25 of 400 seeds give that test's
rival the `plain` arm, whose duel words carry no number, so `price in numbers_in(text)` fails (seeds 14 and 23 fail on
an untouched export of HEAD as well; the 400 picks hash the same with and without the #212 r2 fixes). Rerun it, or pin
`BAZAAR_BLUFF_SEED` in that test.

### [2026-10-03] finding — every service read at the tick boundary and the key answered 429 (Sat ticks 646–650)
Taker, maker, duels and mcp all woke at the boundary on our one key (5 req/s, bursts of 20): `tick 647 maker: read
refused rate_limited … nothing sent` (649 too), `tick 646: /api/duels refused rate_limited` (a lost duel tick scores 0).
Fix (TS1): each tick loop wakes `BAZAAR_TICK_OFFSET_S` after the tick (≤ 10 s, ≤ 40 % of the tick), set by hand per
service (duels 0, taker 2.5, maker 5, mcp 7.5; declared `preserve()` in `.railway/railway.py`); `duel run` re-reads a
429'd `/api/duels` once (`sdk.read_once_more_after_429`). A new service or tick loop on the key needs its own offset.

### [2026-10-03] gotcha — `tests/test_readonly_user.py`'s fixture schema has its own `cards` table
`db.init_schema` in that schema fails (`column "set_code" does not exist`): the fixture's `cards (id, name)` is
not schema.sql's. Drop it before applying the schema there (AF1's read-only test does).

### [2026-10-03] gotcha — a killed pytest leaves its docker Postgres session open, holding schema.sql's advisory lock
symptom: every Postgres test on the laptop (all worktrees) hung in `init_schema`, then failed with lock timeouts →
root cause: a pytest killed mid-test left a backend `idle in transaction` after schema.sql (docker's port proxy keeps
the dead client's TCP connection open), holding the schema's advisory xact lock → fix: find it (`select pid, state,
client_port from pg_stat_activity where application_name = 'bazaar-pytest'`), check no live process owns its client
port (`lsof -nP -iTCP:<port>`), then `select pg_terminate_backend(<pid>)` on the LOCAL docker DB only.

### [2026-10-03] finding — real Market Tests: 16 ticks, auto_baseline per session, our exact broker = the stall (BE1)
Sessions 1-3 (ticks 201-217, 441-457, 681-697): `bench.finished` comes on the TEAM stream only (not the public
feed) as `{venue, session, efficiency, auto_baseline, matches}`: 0.899/0.899 (v08, the stall), 0.967/0.967 and
0.769/0.769 (v19, our exact broker, 4 and 5 matches). `bench.started` (public) carries `{name, ticks: 16, venues,
session, start_tick}` and NO run id, so `BenchSessions` opens sessions from the book only. A match answers
`{"queued": true, "settles_at_tick": tick + 1}`. Matched ids: buyers b35-4..9, b52-0..5; sellers b35-13..17,
b52-13..19 (ten a side?). On `bazaar_sim.bench`, exact equals the stall on every book; #84's edge without a guard
realises less than the stall on 2-26 % of books (mean below it with 20 traders); `scripts/bench_edge_proof.py`.

### [2026-10-03] finding — bench edge: points favour less guard; no policy can beat the stall on every book (BE1)
`scripts/bench_edge_proof.py --seeds 2000` (14 regimes × 6 policies; points under 4 readings of the unpublished curve):
on #77's reading (field at the stall, 0.5 × eff/stall below) the unguarded edge earns the most points in every regime
(normal ×20 traders: 0.604 vs guard 10 0.544 vs exact 0.500), even when its mean efficiency is below the stall; only a
harsh reading (rivals +0.10, 0 points at −0.05) with wrong priors makes exact best. Worst regret: margin 5 0.045,
unguarded 0.072, margin 10 0.110, exact 0.252. Per book, any deviation from the stall can lose: a guard that deviates
only when the edge wins at the worst corner of every limit band still loses 0.4-8.7 % of books, because the trader the
edge pairs now is the one the stall would have matched to a better late arrival (path effect, not estimate error).

### [2026-10-03] build-error — the taker took a trickster's fake FINAL at its list price (Los Pícaros, tick 863)
symptom: LAV-10 bought from `picaros` at 63, its rare list price, after bids 54→55→56 (~0 on the ladder) → root cause:
`dealer.decide` takes any FINAL inside our max as the dealer's limit, and Los Pícaros (`/api/dealers`: kind `trickster`,
strictness 0.1) keep talking after theirs → fix: `agents/trickster.py` marks the plan `forgiving` (published kind
`trickster`): its FINAL is a plain ask, no ask at or above its list price is taken, only one
≤ lowest fill + `trickster_accept_fill_share` × fill range (none seen: we only bid), and our bids stay below its list
price and below any ask we may not take. Same plan in the taker (opens, restart adoption, Jev) and `dealer buy`.
The range is read from OTHER teams' fills of that rarity in that set only, and needs 3 of them (#228 security P2: one
fill of ours at 63 made 63 acceptable; pooled sets made every LAV ask below list acceptable): fewer, and we only bid.
Abuela publishes strictness 0.1 too (and chattiness 0.75), so a strictness bar would make her real final a fake one:
`trickster_max_strictness` ships at 0 and the published kind alone decides.

### [2026-10-03] gotcha — a laptop checkout that is not pulled runs the OLD guardrails for every hand command
The main checkout sat at 1e57564f while main already had #223 (every set protected): `bazaar sell list` from that
laptop read `protect_page_sets = RET,CHA`, so a hand sell of a LAT/LAV/SAL/MAL last copy passed the guard (the
seller's own free-copy check caught it, tick 1028). After every merge, `git pull --ff-only` the checkout that runs
live hand commands, then `uv run bazaar rules`; Railway services redeploy by themselves, laptops do not.

### [2026-10-03] gotcha — a hand sell and the team desk can commit both copies of a duplicate in one tick
Tick 1028: the desk put MAL-06 #468 into a swap counter to t05 seconds before a hand `sell list` posted MAL-06 #1020 →
t02 (cancelled next tick, no fill). `committed_context` subtracts the copies in our open offers as read by THAT
command, so two writers posting in the same instant can still race; re-read `/api/me/offers` right before a hand post
and keep one copy free per card. A shell check piped through `grep` returns grep's exit code, not the check's.

### [2026-10-03] finding — selling a team-bought copy costs its neg_points, even to a dealer (SAL-07, tick 947)
SAL-07 (asset 438) came from t02 at tick 320 for 23 and completed Salamanca (/me your_value 118.6). Sold to Pilar
for 29 (hand-run `dealer sell`, floor 20): /me `neg_points` 134.2 → 44.6 at tick 948 (−89.6 = 29 − 118.6), board
`negotiating` 20.75 → 16.48 at its next update (tick 950, updates every 10 ticks): 0.048 score per neg_point. Buying
it back from Abuela (21) restored the page, not the points. While we led in neg_points, gains moved the board ~0
(ticks 376–386): k is relative to the other teams, so losses and gains are measured apart. `max_score_loss_per_move`
(MI1) now refuses a sale estimated below −0.2 unless `bazaar approve <card> --sell --min <P>`; `bazaar impact`.

### [2026-10-03] gotcha — a duel ladder measured to the deadline tick never sends our floor
v2's free offers to a rival that never priced ran `our_target(elapsed / total)`, and the runner never sends on the
deadline tick, so the floor (progress 1.0) was never sent: in Duels I our last silent offer (D − 1) stayed ~9 % off
our limit. Compressing the curve to end earlier (#215 first cut) also lowered D − 3/D − 2, the ticks every Duels I
silent deal closed on (−0.49 duel points on replay). Fix: keep the curve, put only the last
`duel_silent_floor_lead` ticks we send at our floor. Test any "end earlier" change by diffing every earlier tick.

### [2026-10-03] gotcha — two "free spare" pickers tie on one copy: the Workshop must see the team desk's talks (#235 reviews)
Every copy of a card in /me carries the same `your_value`, so the team desk's `desk_copy` (cheapest, then lowest id)
and the Workshop's kept copy (most valued, then lowest id) are the same asset: a swap posted in the tick gives #1 while
the Workshop crafts #2 and #3, and the page ends on a promised copy. `_taller` now runs before the desk posts, treats
every card of a live desk talk, a sell thread's asset and a card accepted this or last tick as busy, and promises its
crafted copies in `run.offers`. `/api/taller` is not in docs/api/openapi.json: its shape is the level's `how` text.

### [2026-10-03] gotcha — an approval tool must never reach an agent: keep it out of `tools.TOOLS`
`tools.TOOLS` feeds the desk's in-process server, every subagent allow-list and the remote MCP server at once, so a
spec added there is callable by our own LLMs. The human tools (HA2) live in `runtime/human_tools.py` and only
`mcp_server.build_app(..., approver=...)` serves them, behind `X-Approver-Token`. Testing them over the TestClient: the
per-token tool-call bucket has a burst of 5 with a frozen clock, so advance the fake clock between calls.
`tests/test_railway_iac.py::test_the_show_holds_no_team_key_and_no_database` failed on main (BAZAAR_KEY,
GAME_VIEW_TOKEN, ELEVENLABS_VOICE_SELLER undeclared in its list): fixed with HA2.

### [2026-10-03] finding — the server refuses a too-early venue notice `wait`; our generic one spammed it after every restart (MM2)
`executions` (sdk_method `broker_announce`, ticks 439-1166): 26 accepted, 12 refused `wait`, each 2-8 ticks after an
accepted notice; accepted gaps went as low as 10 ticks (616 → 626), so the server's gap is about 10 ticks, not 20
(UNVERIFIED: its exact message). The keeper remembered its notice in memory only, so every maker redeploy announced
again. The 33 accepted notices on v19 were the same generic text naming no card; v19 had 0 organic trades. MM2: the
notice names the page cards the most other teams miss (team matrix), one every 24 ticks, the feed's newest
`venue.announcement` for our venue counting as the last one.

### [2026-10-03] finding — the ranking reserved a dealer ladder's TOP, so the best buy never opened (UB1, ticks 1095-1166)
`strategy.guarded` checked every dealer buy at `mv.limit` (the ladder's top): MAL-09 (top 67) read "cash 58 - 67 <
cash_floor 5" for 70 ticks while Los Pícaros asked 60-65 and a first bid of 50 was affordable; `_all_denied` then said
"none affordable". A ladder is now ranked at its first rung (caps still at its top); each rung is checked when sent,
and a rung refused only for cash/spend bids the most we may still commit. Second loop found in `decisions` (ticks
1205-1227): RET-09/RET-10 walked at 50 > official value 49 and reopened 48, 49 every three ticks against asks of 64-73:
every guardrail walk of a dealer thread now rests on the card for an hour (#248 review: a cash walk replayed too).
### [2026-10-03] finding — what scores (rules audit) and why breaking a complete page still cost points
Marius's rules audit (8dbf50b7, PR #222; `docs/briefing.md` "Scoring", `STRATEGY.md` "What scores") fitted the score on `/me`
snapshots. Holdings, the album and `collection_value` never score by themselves; a card scores only when it moves: a team
trade (price − our `your_value` → `neg_points`) or a dealer deal (ladder share of that dealer's own range, opening price 0,
its final the whole range, best 3 per level, restarted every round). Per round, market ≈ 22.5 × `bench_points` + 7.5 ×
organic, negotiating ≈ ladder 7.5 + duels 7.5 + team trades 15, each capped at the top-3 mean. Incident that this does NOT
excuse: selling SAL-07, the only copy on a complete Salamanca page (Sat 3 Oct ~18:28), took `neg_points` from 134.7 to 44.6
at tick 948 (coordinator's decode of `/me`; score 28.25 → 23.98, rank 5 → 12, per `protect_page_sets`), although holdings
"never score": the page cards we had bought from teams were revalued at the new `your_value`. Our reading (inferred, not in the audit): team-acquired cards are
marked at the current `your_value`, not frozen at the trade. Lesson: a rules-text inference that touches the album gets
checked against the live `/api/me` score before it is acted on. `protect_page_sets` lists every set (hard rule).


### [2026-10-03] build-error — a fail-closed guard that needs Postgres turned every PR's sim smoke red (#233)
symptom: on main, `scripts/sim_smoke.py` failed at `dealer buy LAT-01` with "no_buyback_ticks ... (our sales
unreadable)" → root cause: `no_buyback_ticks` refuses every card buy when the impact board cannot read our sales, and
the smoke runs with no Postgres by design → fix (#258): a simulator target (`guardrails.simulator_target`, read once
from `Settings.simulator`) skips the unread case; the real game still fails closed, now also on a tape more than 3
ticks behind. A new rule that reads Postgres must say what it does on the simulator, and run the smoke before merging.


### [2026-10-03] gotcha — the shared ledger table only takes kinds spend, accept and listing
`sql/schema.sql` has `check (kind in ('spend','accept','listing'))`; the JSONL ledger has no such check, so a new kind
passes every file-ledger test and fails live with `CheckViolation` (found by the #236 reviews). A Workshop craft is
booked as `spend` at price 0 with item `taller:<refs>` and counted by prefix (`count_since(kind, t_hours, prefix)`).

### [2026-10-04] finding — activity audit of Saturday (ticks 160-1445): what stopped the agents, and what 15 s ticks break
From `decisions`/`executions` (read-only). Taker rejections: `max_price_uncommon` 341 (204 ticks, asks 27-33 vs cap 26,
ticks 174-310), `cash_floor` 100 + `max_spend` 72 (all before the Sat 16:35 loosening: floor 100/50, hourly 150), `max_price_rare`
71 (ticks 684-724, asks 98-104 vs 95), jev undecided below 0.75 on team swaps 231 (ticks 576-1322, Jev 0.26-0.44). The taker's
256-tick gap (502 to 758) was cash stuck at 81 under floor 50. The maker's 59-tick LAT-10 sell 86 refusal (973-1277) is
`max_score_loss_per_move` asking for a human approval (a hard rule, kept). `dealer_sell` breaker held 948-1065 until a manual reset.
Two real bugs: (1) the taker runs BAZAAR_DECIDER=llm and `needed_budget_s` = 13 s, but a 15 s tick leaves ~10 s: every Jev-gated
move would read "no tick budget for jev" (20 team opens already did at 30 s ticks); `decider()` now answers Jev below
BAZAAR_DECIDER_MIN_TICK_S (30). (2) the team desk re-cancelled a lapsed swap offer every tick (`offer_not_open` 36 times on 9
offers, 241 ticks, thread never freed); it now frees the thread and keeps the spend booked until a thread read ends the offer.

### [2026-10-04] build-error — one-shot claims counted as opened venues (PR #263)
Claim-only storage made `opened_before()` true (regression: `2 failed, 22 deselected`) → it excluded `_claim`
but counted `_once:bench_match_probe` → exclude the literal `_once:` prefix from both venue count and load.
Keep real keyless venue markers; an in-memory SQL regression covers both states and target isolation.
Validation also hit local Postgres contention: the full suite stalled inside psycopg, then a retry failed
the `rival_board` lock-timing test; the parallel coverage run hit a schema lock timeout in approvals setup.
Both affected tests passed alone (`2 passed in 2.89s`); the final full gate without competing coverage passed:
`5331 passed, 1 skipped, 2 xfailed, 42 subtests passed in 100.01s (0:01:40)`.

### [2026-10-04] build-error: PR #263 merge verification separator
The ad hoc memory-preservation check expected an extra blank line and failed despite retaining both parents' entries.
The corrected check verifies the exact main prefix and PR-only entry, ignoring only separator newlines; both pass.

### [2026-10-04] finding — Sunday guardrails for 15 s ticks (Omar approved): caps 30/105, dealer_sell auto re-arm
`max_price_uncommon` 26 -> 30 and `max_price_rare` 95 -> 105 are only ceilings: `official_value_margin` and the server's
`/api/me/value` still refuse any buy above our value (test_raising_the_card_caps_never_lifts_the_official_value_cap). The
`dealer_sell` breaker tripped by the watchdog now lapses after `dealer_sell_breaker_reset_ticks` = 40 game ticks via its
`until_tick` in `guard_breakers` (shared, never wall clock); evidence older than the trip is spent, so only a NEW below-value
sale re-trips it. Existing sell guards remain binding. Two replay tests pin their historical cap to 26.
PR #265 is limited to these three guardrail changes; duel sending and request budgets match origin/main.

### [2026-10-04] build-error — PR #265 local test gate stalled in psycopg (SU1)
The first full gate stopped progressing after 1,838 passed tests and was interrupted after 153.45 s.
The interrupt trace ended in `psycopg_binary/_psycopg/waiting.pyx:236`; a local PostgreSQL diagnostic
showed no blocked sessions. Cause unconfirmed; rerun the isolated suite with a 60 s traceback diagnostic.

### [2026-10-03] finding — no team has tried prompt injection on us yet; "pretend" alone is a dealer habit (IJ1)
`bazaar injections --backfill` over the shared archive (23,548 feed events to tick 1171, 193 stored thread
messages, 68 duels): 50 tagged texts, 0 attempts. 42 are venue announcements (v05, v07, v04, v20, v21, v24, v02)
describing their JSON offer format or a priced match (`code_or_json`, `money_command`); 8 are dealer lines, 7 of
them Pilar or Chato saying "I never pretend otherwise", which `role_play` reads as a role cast. Severity now needs a
cast ("pretend to be", "act as", "you are now"), so those are weak. Team-thread words were never stored before IJ1
(the feed carries a team's text as null; ThreadStore keeps only our dealer threads): the taker records them from now.

### [2026-10-04] build-error — existing-index DDL blocks injection recorder startup and backfill (#234)
`CREATE INDEX IF NOT EXISTS` still takes a ShareLock, so startup can wait behind a writer and a backfill can
block live inserts until its transaction ends. Check `to_regclass` first, bound setup lock/statement waits
to 1.5 s, and commit schema setup before backfill reads; `store()` now does no DDL. Local Postgres regression
tests cover the held-write transaction, missing-index timeout and released setup locks.

### [2026-10-04] build-error — injection setup test shadows the imported conn fixture (#234)
Ruff F811 on a local connection named `conn` → the module imports that name as a fixture → renamed the local
connection to `fresh`; the fixture and its callers are unchanged.

### [2026-10-04] build-error — inline team messages were recorded as dealer proofs (IJ1, #234)
`Taker._keep()` sees every listed thread but labeled each `dealer_thread`; the later team-desk pass then
recorded the same message under `team_thread`. Derive the source from thread kind and test both passes
against one buffer. Keep extraction inside the recorder's never-raises guards; malformed metadata must not
cost a taker move or stop the duel runner's post-send processing.

### [2026-10-04] gotcha — duel exit status does not prove post-send completion (#234)
`run_per_tick` catches tick exceptions, so a sent move plus CLI exit 0 can hide a failed recorder. The wiring
regression now checks the final `evals.after_tick` call as well, including an injected extractor TypeError.
The new test also hit Ruff F811 on the imported `duel_cli` fixture parameter; mark that intentional fixture reuse.
