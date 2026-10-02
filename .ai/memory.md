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
