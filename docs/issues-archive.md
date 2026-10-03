# Closed GitHub issues (archive)

The repo's backlog is `.ai/specs/02-plan.md` and the per-task specs in `.ai/specs/`. On 2026-10-03 the open
GitHub issues were migrated: the ones still needed became specs (S1, M1, D1, P1, K1, N14, T1); the ones below
were closed. Their text is kept here verbatim.

## #1 — closed (done)

Jev is ported to Python (N5) and decides in every agent (taker, maker, duels, desk); see 02-plan.md N5/N8.

### #1 — Add Decision model

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1 on 2026-10-03 (closed there).

https://github.com/ogarciarevett/jev-sdk

**Comment by serban-marius:**

Integrated in main.

- The jev-sdk decision model is ported to Python in `src/bazaar_agent/jev/log.py` (port of `decision-log.ts`, same JSONL format `jev-report` reads), alongside `jev/judge.py` and `jev/mask.py`.
- Our own `Decision` lives in `src/bazaar_agent/decisions.py:27-43`, written by `DecisionLog` (Postgres, JSONL fallback). Tables `decisions` and `executions` are in `src/bazaar_agent/sql/schema.sql:79-88`.
- Every taker and maker move is recorded through `Recorder.decide/send` (`src/bazaar_agent/agents/runtime.py:196-301`).
- Verified in the shared DB: the `decisions` table holds real rows with candidates, guardrail verdict, Jev verdict and reason.

Out of scope for this issue: the "outcome" half exists (`jev/log.py:76` `outcome_line`, `outcomes` table) but nothing writes it yet. If we want it, it deserves its own issue.

**Comment by serban-marius:**

Reopening: I closed this against its title only ("Add Decision model"), but the team backlog (architecture page) defines #1 as **Decision model: decider + Jev packs + policy**, and its last step is still open.

- ✅ Decision model and log: `decisions.py`, `jev/log.py`, every taker/maker move recorded (see the closing comment above).
- ✅ Autonomous taker + maker (`bazaar agent`) running in dry run on Railway.
- ⬜ **Live switch-on** (`BAZAAR_LIVE=1` on taker/maker). Blocked on safety fixes first: kill switch must hold instead of writing (PR 3, in progress), cash floor and hourly spend accounting (PR 4, planned), and the public `/state` leak of our limits (PR 5, pending decision).


## #3 — closed (done)

The tick loop (`run_per_tick`), the shared Postgres ledger (1 accept per tick across processes), the kill switch (GUARDRAILS + .local/PAUSE) and the rate limits are built and live; see README 'Ticks' and 'Guardrails'.

### #3 — [core] Tick loop: single SSE, rate governor, scheduler and kill switch

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/3 on 2026-10-03 (closed there).

## Context
The game runs in ticks: 60 s on Friday, 30 s on Saturday and 15 s on Sunday. Doors close from 23:00 to 09:00. The clock **pauses** (right now `paused: true`, tick 0), so never plan by adding wall-clock hours.

Limits (`/api/clock.limits`):
- per tick: 1 accept, 1 message per side, 12 offers;
- in total: 6 open threads, 30 open offers.

Personas also have hourly quotas (Abuela: 8 deals/h and 3 packs/h).

## What to do
- **A single** keyed SSE stream: `GET /api/events/stream` with `X-Team-Key`. There's a cap of 6 streams per team/IP **and browser tabs count**; it has already returned `too_many_streams` to us without a key. Reconnect with backoff (0.6 s → 10 s) and fall back to polling `/api/clock`.
- Rate-limit governor with a per-tick budget, refilled on each `tick` event, and a priority queue: duel > broker > persona > scan.
- Scheduler based on `/api/schedule` and the game's tick/`t_hours` (not wall-clock time). Reloads on `schedule.fired`.
- `PAUSED` (stops posting, keeps reading) and `DRY_RUN` (logs what it would do). The kill cancels all open offers (`DELETE /api/offers/{id}`).
- JSONL log of every decision with its inputs, no secrets.
- Run unattended: process with restart, sleeps on `doors: closed` and has a heartbeat. A human starts it.

## Acceptance criteria
- [ ] 30 min in `DRY_RUN` without a single 429.
- [ ] Survives a forced SSE disconnect.
- [ ] `PAUSED` cancels open offers in ≤1 tick.
- [ ] Never opens more than 1 stream.

**Comment by serban-marius:**

**Correction (RULES.md):**
- The rate limit is **5 req/s per key, with bursts of 20**. If you request too early it returns `429` with `next_tick`: wait for that tick, don't retry.
- The 12 offers per tick also count cancelled ones.
- The organizers can change the limits during the game (e.g. a single conversation on Friday). Always read `clock().limits` and the feed.
- Stream: `GET /api/events/stream?scope=team`. If it gives `429`/`503`, poll `/api/feed`.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, the stream and the kill switch work, but there is no rate governor, scheduler or heartbeat.

- ✅ Done:
  - One stream per process. Reconnects back off from 0.6 s to 10 s, and a 429/503 drops to polling (`src/bazaar_agent/stream.py:37-38`).
  - Pause file and dry run (`guardrails.py:301`, `agents/runtime.py:30`). Decision log with secrets scrubbed.
  - The agent sleeps while doors are closed, Railway restarts it, and it has `/health`.
- ❌ Missing:
  - A rate governor with priority duel > broker > persona > scan.
  - A scheduler on `/api/schedule` that reloads on `schedule.fired`.
  - Cancelling open offers on pause (criterion 3).
  - A heartbeat for `bazaar-duels` and the monitor.
- Findings:
  - The shared ledger never reconnects: after one dropped Postgres connection, `bazaar-duels` silently stops playing (the error escapes the tick, the process never exits, so Railway never restarts it), and the taker/maker skip every tick.
  - Separately, the shared `ledger` table has **0 rows** despite 4 Abuela purchases and at least 4 duel accepts of ours: the processes that traded were not on the shared ledger, so the team-wide accept slot and hourly caps were not shared.
  - Both are addressed on branch `fix/shared-ledger-reconnect` (live writes will require the shared ledger).
  - While doors are closed, `ticks.py:17` sleeps up to 300 s and ignores `next_opens`, so we can start up to 5 min late. Fix after PR #43.
  - The ~30 s maintenance restart at tick 94 is the case the unattended restart has to cover.
- Related: #59 (open) reopens the ledger and enforces per-tick caps, but only in its runtime/MCP tools, not in the taker, maker or duel loops.


## #4 — closed (done)

The duel logger and protocol checks shipped (#41, #58); duels are stored in Postgres and evaluated.

### #4 — [duels] Duel logger and protocol verification in the Practice duels

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/4 on 2026-10-03 (closed there).

## Context
The **Practice duels** are at `t_hours=2.0` (~Friday 21:00 if the clock doesn't stop). They last 12 ticks with decay 0.06 and **don't score**. It's the only window to see the real protocol before the duels that count (Duels I at h6.5, Saturday).

## What to do
Client for `GET /api/duels`, `POST /api/duels/{did}/messages {text, price, days}` and `POST /api/duels/{did}/accept`. It should log every payload and every `duel.started` / `duel.message` / `duel.closed` event to JSONL.

## Questions to answer (and document in the repo)
- [ ] **C1** What does the payload include? Role, own limit, per-day weight, day range, rival's current offer, history, `deadline_tick`, rival id?
- [ ] **C2** Do decay `rounds` count ticks or full exchanges? Does silence stop the decay?
- [ ] **C3** Does `accept` (no body) accept exactly the rival's last offer? What happens if both accept in the same tick?
- [ ] **C4** Does `accepts_per_team_per_tick: 1` also apply to duels?
- [ ] **C5** On close (`done=true`), does it reveal `share`/`result`/`max_pie`? If so, we can deduce the pie and the rival's limit.
- [ ] **C6** Days: what range do they have? Can the weights be negative?

## Acceptance criteria
- [ ] The bot plays all practice duels without missing any deadline.
- [ ] Sample payloads and C1–C6 answers committed.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, logging works, but the C1–C6 answers are not committed and there is no proof we meet the practice deadlines.

- ✅ Done:
  - Every `/api/duels` response and every move go to `.local/duels/duels.jsonl` (`src/bazaar_agent/cli.py:469`, `cli.py:454`).
  - The live payload shape has been read (b0f630c).
- ❌ Missing:
  - The C1–C6 answers, committed. **C3** (does `accept` take a snapshot of the rival's offer?) matters most for accept safety.
  - Proof of meeting the deadlines. 12 practice duels ended `no_deal` with 0 rounds because we never offered. b0f630c fixes that, but it has not been re-proven live.
- Data:
  - 26 duels, all practice (session 1) and price-only. 8 deals, all inside our limit, average result 22.1.
  - The payloads include `days_meaning`, `limit_meaning` and `your_days_weight` (input for C1 and C6).
- Suggested rewording: ~~log every `duel.started` / `duel.message` / `duel.closed` event~~ → "log every `/api/duels` response and move per tick". The keyed stream carries only public events and our `agent.*` snapshots, so duels can only be observed by polling.
- Related PRs:
  - #57 (merged) moved these lines to `cli.py:507` and `cli.py:466` on 8bcf0fd.
  - #58 (open) stores duels in a new `duels` table via `?done=true`. It finds that `result` = surplus × 0.94^rounds and that the API has no share or pie, which answers part of C5.

**Comment by ogarciarevett:**

Status update: #58 (merged) stores every duel in a `duels` table (`bazaar duel done`) and answers part of C5: a finished duel's `result` is our surplus × (1 − decay_per_round) ** rounds, and the API reveals no share or pie (logged in `.ai/memory.md`, 2026-10-03). Still open: committing the C1–C6 answers (C3 first, for accept safety) and proving the practice deadlines are met live after the b0f630c fix.


## #15 — closed (not needed)

Not needed to win this weekend (Jev: close_not_needed, 0.89). Kept as a line in 98-nice-to-haves.md; the evals (#58, #91) and Phoenix cover the useful part.

### #15 — [scoring] Local score simulator + observability dashboard

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/15 on 2026-10-03 (closed there).

## Context
The public leaderboard only shows `score`, `negotiating` and `market`. The subcomponents (duels, ladder, trades, bench, organic) are admin-only, so we have to reconstruct them to know where the headroom is.

## What to do
- **Score simulator:** reconstruct duels, ladder, trades, bench and organic per round from our data. Apply the top-3 normalisation (`30 · Σ w_r · min(1, raw/mean_top3) / Σ w_r` [inferred]) and the round weights (0.5 / 1 / 1). Fit the unknown `game.yaml` weights using leaderboard snapshots.
- **Local dashboard:** remaining rate budgets, open threads/offers/duels, cash and holdings, score and rank, last 50 decisions, and `PAUSED`/`DRY_RUN` clearly visible.

## Acceptance criteria
- [ ] Our negotiating and market reconstructed within ±5% of the public values.
- [ ] Report on which component has the most headroom up to the top-3 mean.
- [ ] The dashboard refreshes every 10 s.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, the score simulator is missing and the dashboard is only partly there.

- ✅ / 🟡 Dashboard (partial):
  - Phoenix traces.
  - Each agent's `/state` shows its last 50 decisions and whether it runs dry or live.
  - `PAUSED` shows in `rules show` (`src/bazaar_agent/cli.py:554`).
  - `bazaar status` and the monitor's summary line.
  - Still missing: a single screen refreshing every 10 s. That depends on PR #43. The ADR is in PR #46.
- ❌ Score simulator:
  - No top-3 normalisation, no round weights (0.5 / 1 / 1) and no fit of the `game.yaml` weights.
  - No reconstruction of negotiating or market within ±5 %, and no headroom report.
- Data the simulator should explain: our private score fell from 9.24 to 8.73 to 8.34 with no activity of ours (normalisation against others). At tick 149 its parts were `duel_points` 0.0, `ladder_points` 0.058, `mm_points` 0.0, `bench` null, rank 15.
- Related: #58 (open) partly reconstructs the score with an outcome eval per target (duel, dealer, trade; the Market Test is a stub), a best-3 ladder report, and the official `/me` numbers shown next to ours. It does not normalise against the top 3.

**Comment by ogarciarevett:**

Status update: #58 (merged) covers part of this. `bazaar evals report` reconstructs duel, dealer and trade outcomes per target and shows the official `/me` numbers next to ours; the Market Test is a stub. Still open: the top-3 normalisation, the round weights and the fit of the `game.yaml` weights, the headroom report, and a single screen refreshing every 10 s (PR #43, open).


## #21 — closed (done)

Finding recorded in .ai/memory.md ('the feed is an order book') and used by the curves, the tape and the feed reader (#89).

### #21 — [intel] The public feed exposes every team's offers to dealers

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/21 on 2026-10-03 (closed there).

## Finding (real API, 2026-10-02 ~20:20, tick 0)
`GET /api/feed` is **public, needs no key**, and emits a `thread.message` event for every message a team sends to a dealer. It carries the team, the thread and the **full structured offer**:

```json
{"type":"thread.message","scope":"public","actor":"t16","payload":{"thread":8,"kind":"persona","team":"t16","with":"abuela",
 "offer":{"id":1,"maker":"t16","to":"abuela","give":{"cash":15,"assets":[],"types":[]},
          "want":{"cash":0,"assets":[],"types":["pack:sobre_barrio"]},"expires_tick":2,"created_tick":0,"final":false}}}
```

`thread.opened` is public too: `{"thread":1,"kind":"persona","team":"t16","with":"abuela","topic":{"buy":{"pack":"sobre_barrio"}}}`. At tick 0 there were already 14 threads with Abuela opened by other teams. t16 has 3; t04 offered 12 P and t16 offered 15 P for a `sobre_barrio`.

## Why it matters
- **We can see Abuela's concession curve with other teams' money.** If her counter-offers also show up in the feed (to be verified once the clock moves), we see what price she accepts from each team and when she gives the "final", without spending our 8 deals/h.
- It lets us watch what rivals do live: who negotiates, how they open and what they buy.
- Offers in dealer threads **expire after 2 ticks** (`expires_tick = created_tick + 2`): they have to be renewed.
- `text` comes out `null` in the feed. The words are not published, only the structure.

## What to do
- [ ] A feed collector (`/api/feed`, or the SSE with `scope=team`, which also carries the public events) that stores **everything** in JSONL starting now.
- [ ] Rebuild, per dealer thread, the sequence team offer → counter-offer → outcome (`deal`/`walked`, final price) and estimate Abuela's `open`, `limit` and β for #8.
- [ ] Verify once the clock moves: do the dealer's counter-offers, the `final`s and the settlements show up in the feed? Do the settlements reveal which team receives which asset? (That would de-anonymise the map in #22.)
- [ ] Dashboard: average price each team pays per pack, deal rate, and who is ahead on the ladder.

## Acceptance criteria
- [ ] Feed JSONL with no gaps (paginated by `id`) since tick 0.
- [ ] Report on Abuela's curve with ≥20 threads from other teams.

**Comment by serban-marius:**

**Confirmed with the game running** (tick 6, 135 events in the feed):
- **Dealer text is public too.** Example: `{"sender":"abuela","text":"Welcome, hijo! Look, Neighbourhood pack, 17 P. Made for beginners.","offer":{"give":{"types":["pack:sobre_barrio"]},"want":{"cash":17},"expires_tick":3,"final":false}}`.
- **Abuela's welcome price is 17 P** for a `sobre_barrio` with a list price of 26 (book EV 33.8). There are settlements closed at 17. We should save our first chat with her for this (#8).
- **Settlements reveal who receives what**: `{"type":"settlement","payload":{"kind":"trade","parties":["abuela","t07"],"items":[{"id":271,"kind":"pack","ref":"sobre_barrio","frm":"abuela","to":"t07"}],"price":17}}`. With the asset ids, **the map in #22 gets de-anonymised** as cards change hands.
- `offer.listed` shows each team's sales on El Rastro with the full asset. For example, t07 is selling LAT-03 #5 for 10 P, expiring in 10 ticks.
- `gift.given`: Abuela gives away cards (t06 received MAL-03). It doesn't score, but it does hand out cards.
- `pack.opened` publishes the team and the best card that came out (`best`).

Real examples of all of this in the OpenAPI spec (PR #26).

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, the collector and the thread rebuild exist, but the Abuela estimate and the report do not.

- ✅ Done:
  - `bazaar feed capture` (`src/bazaar_agent/feed.py`) flags a possible gap whenever a full window doesn't reach the last id it saw (`feed.py:85`).
  - `intel.dealer_threads` + `curve_summary` / `bazaar curves` rebuild each dealer thread. `team_flows` shows what each team does.
  - The shared DB already holds **3,725 feed events** and **271 `dealer_curves` rows**.
- ❌ Missing:
  - An `open` / `limit` / β estimate for Abuela (feeds #8).
  - A committed report on her curve over ≥20 threads from other teams.
  - The ladder view (in PR #43).
- Suggested rewording: ~~Feed JSONL with no gaps (paginated by `id`) since tick 0~~ → "Feed captured every tick since we started, with `gap_possible` flagged." `/api/feed` has no cursor, so pagination by id is impossible.
- Related: #58 (open) fixes `intel.dealer_threads` giving an abandoned thread a later thread's fill (our thread 85 got thread 99's LAV-03 fill). Its dealer eval reads the learned range per dealer and rarity from `dealer_curves`.

**Comment by ogarciarevett:**

Status update: #58 (merged) fixed `intel.dealer_threads` so an abandoned thread no longer inherits a later thread's fill. The Abuela fill numbers over 31 threads are in `.ai/memory.md` (2026-10-02, `uv run bazaar curves --dealer abuela --threads 20`). Still open: an `open` / `limit` / β estimate for Abuela and the ladder dashboard (PR #43, open). This one can close once #43 merges.
