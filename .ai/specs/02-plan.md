# 02 — Master plan, part 2 of 2: phases, backlog and steps

> **Backlog source: this repo.** GitHub issues were migrated into `.ai/specs/` (S1, M1, D1, P1, K1, N14, T1) and closed on 2026-10-03; the closed ones are archived verbatim in `docs/issues-archive.md`. Do not open GitHub issues: add a row here and a `<id>-spec.md`.

- Status: **draft** — awaiting approval together with [`01-spec.md`](./01-spec.md)
- Backlog source: **this repo** (`.ai/specs/02-plan.md` + `<id>-spec.md`). The GitHub issues (#1–#24) were migrated into
  specs or archived in `docs/issues-archive.md` and closed on 2026-10-03; tasks use local ids (N-ids, S1, M1, D1, P1, K1, T1).
- Derives from: [`01-spec.md`](./01-spec.md)
- Language: **Python only** (3.12, `uv`). No `.ts` / `.rs`; Jev is ported to Python (task N5).
- Time is in **game hours** (`/api/schedule`, `t_hours`), never wall-clock: the clock pauses and
  the organisers change the tick length. At writing: h0.32, tick 17, Friday closes at h4 (23:00).

## Prerequisites (human, before anything else)

| # | What | Why | Status |
|---|---|---|---|
| P1 | Put the team key in `.env` as `BAZAAR_KEY=tk-...` | `.env` only has `TYPESAFE_API_KEY`; `/api/me` and every write need the team key | ⬜ |
| P2 | Approve `01-spec.md` (or send changes) | Locks the architecture | ⬜ |
| P3 | Decide where it runs unattended: laptop + `caffeinate` (Fri) → always-on host (Sat) | A sleeping laptop stops the agents | ⬜ |
| P4 | Templates or an LLM for message text (§11 Q3 in the spec) | Changes the `words.py` scope | ⬜ |

Verified already (2026-10-02): `TYPESAFE_API_KEY` works (Jev `jev-1.13.0`, 279 ms, decided
verdicts on `questions/negotiation.json`). `.env` and `.local/` are gitignored. Docker 29.6.1 is up.
The public feed returns its full history today (326 events, ids 1–777, `limit` only, no cursor).

## Phases

### Phase 0 — Friday tonight (h0.3 → h4): start learning, reach level 2

Friday weighs 0.5 and has no scored duels. Tonight is worth the **data** and the **L2 unlock**.
The full architecture is not needed tonight. Ship the thinnest path that captures the market and
negotiates well.

| Order | Task | Issue | Done when |
|---|---|---|---|
| 0.1 | Feed capture **now**: poll `/api/feed?limit=1000` every tick, dedupe by id, append JSONL to `.local/feed/` | #21 | Gap-free ids since 1. Raw JSONL first; Postgres loads it later |
| 0.2 | Lock the stack in `.ai/context.md` (Python 3.12 + uv, Postgres 17 + pgvector, pytest, ruff, pyright; DoD commands) and run `sh scripts/sync-ai-docs.sh` | — | Generated docs in sync, pre-commit green |
| 0.3 | Merge `docs/openapi-spec` into main | — | `docs/api/openapi.json` on main |
| 0.4 | Scaffold: `pyproject.toml`, `docker-compose.yml` (pgvector), `schema.sql`, `config.py`, `client.py` with governor + `wait_on_tick=False` | #2, #3 | `bazaar status` prints cash/level/album from `/api/me` with 0 × 429 |
| 0.5 | Dealer-curve report from the captured feed: every Abuela thread rebuilt as ask → counter → final → fill | #21, #8 | Report over ≥ 20 other-team threads: fill-price distribution per item, steps to final |
| 0.55 | Minimal Python Jev judge: request, noul/choice verdicts, stakes thresholds, `undecided` on any failure, JSONL log (the masking and parity work wait for N5) | N5 (part 1) | Same verdicts as the upstream CLI on the `negotiation.json` smoke state |
| 0.6 | Thin Abuela negotiator (single process): opens with a learned anchor, small steps, accepts at or below the learned fill price, kind templates, Jev `negotiation_move` as advisor, hard max | #8 | **3 negotiated deals** (not at opening ask) → `GET /api/dealers` shows our early unlock progress |
| 0.7 | Practice duels at **h2**: log every message, offer, deadline and limit field | #4 | One complete duel protocol captured as fixtures |
| 0.8 | Market Test at **h3**: read `bench_offers` with the free stall's `starter_broker_key` (from `/api/me`) and store them | #12 | One full bench session stored for the broker model |

### Phase 1 — Before Saturday opens (h4 → Sat 09:00): build the system

| Order | Task | Issue | Notes |
|---|---|---|---|
| 1.1 | Memory: tables from spec §5, repository layer, load the Friday JSONL | new (N1) | Postgres is the only shared state |
| 1.2 | Intel: order book, tape, dealer curves, competitor profiles (spec §7.8) | #21 + new (N2) | Inferred ×1.6 set per team from flow |
| 1.3 | Valuation: reserves, page bonus, value-to-counterparty, sell/buy lists | #14, #23 | Uses the verified formula |
| 1.4 | Learner + embeddings (fastembed, multilingual, 384-d) | new (N3) | Numbers from SQL, analogies from vectors |
| 1.45 | Finish the Jev port: `mask.py`, outcomes, `bazaar jev report`, parity fixtures; point `.ai/skills/jev/SKILL.md` at the Python CLI and re-sync | N5 (part 2) | `bun` only used by hand as a parity oracle |
| 1.5 | Decider: intents → candidates → RAG → Jev → policy → `decisions` | #1 | New packs: `safety.json`, `duels.json`, `market.json` |
| 1.6 | Executor: fresh runtime, claims approved decisions, re-validates, SDK only, kill switch | #3, #10, #24 | The only process that writes to the game |
| 1.7 | `service.py` command layer + `typer` CLI over it + `.ai/skills/bazaar/` + `.ai/commands/bazaar-*.md`, then sync | new (N4) | Point 4 of the brief; `service.py` is the voice seam |
| 1.8 | Venue + broker (`board`), ready for the **h5** Market Test; open the venue as soon as L2 lands | #11, #12 | Limit estimation from Friday's bench data |
| 1.9 | Duel policy for **Duels I (h6.5)**: limit floor, anchor, decay-aware acceptance | #5 | Calibrated on the practice fixtures |
| 1.10 | `docker compose up` for everything, `restart: unless-stopped`, heartbeat, sleep on `doors: closed` | #3 | 30 min `DRY_RUN` without a 429 |

### Phase 2 — Saturday (30 s ticks; round 2 started at tick 160): run, measure, improve

- Buyer + seller agents live: team trades driven by competitor profiles (#13, #14); sell LAT and
  duplicates to the teams whose flow says they need them.
- Market Tests about every 2 game hours, one of them hard (12 traders). Compare our efficiency against the
  stall after each session (#12).
- Duels II (price + days) are about Sat 20:34 (#7). RET released on Saturday: re-run the collect plan.
- Superseded Sat 3 Oct: the game hours, the round starts and the finale are in `docs/briefing.md`
  ("Windows this weekend", "Scoring"); the organisers move them, so re-read `/api/schedule`.
- Flags only with words/structure mismatch + `message_is_bad_faith` ≥ 0.9 (#10).
- `bazaar jev report` + outcomes after every session. Tune thresholds and policy on evidence.
- Ladder: each new dealer level goes through the same curve learning before we commit our quota (#9).

### Phase 3 — Sunday (15 s ticks, doors close at 15:00): finish and present

- Chamberí is released and round 3 starts at h16.65, Sun 09:00 CEST, with opening and the ladder restart.
  One game hour is one real hour on Sunday. Duels III is h18.65, about 11:00; all dealer stalls close and
  Grand Final duels start at h21.65, about 14:00. Scores freeze and doors close at h22.65, 15:00.
  Pre-opening tests at h14.65/h15 have UNVERIFIED execution; see `docs/briefing.md`, "Windows this weekend".
- Pitch (#16, 40 %): replay real decisions from `decisions` + Jev logs. Show the order-book view of
  the competition, the learning curve against Abuela, and the executor firewall. Frame it all as
  agent-to-agent invoice negotiation for Causa Prima.

### Phase 4 — Voice interface (later, once the system runs unattended)

- An ElevenLabs voice agent ("v4 flash": confirm the model id and the Python SDK at build time)
  talks to a small Python tool server (FastAPI) that wraps `service.py`: `status`, `buy`, `sell`,
  `intel`, `learnings`, `pause`.
- Voice requests become intents like the CLI's and go through the decider and executor. Any action
  that moves money needs a spoken confirmation of the structured intent first.
- Strong pitch material: "ask the market what it learned" live on stage.

## Task index

| Task id | Title | Phase | Status |
|---|---|---|---|
| N2 · was #21 | Feed capture + dealer curves | 0 → 1 | 🔵 `bazaar monitor` (#32), real-time stream (#40), thread-fill fix (#58); open: Abuela `open`/`limit`/β estimate, ladder view (PR #43) |
| N4 · was #2 | Team key + API client + fixtures | 0 | ✅ key works; SDK bridge; API fixtures (#26) |
| N9 · was #3 | Tick loop, governor, scheduler, kill switch | 0 → 1 | ✅ tick loop + budget + `.local/PAUSE`; the kill switch HOLDS (no writes, offers stay open) and `bazaar flatten` is the explicit cancel-everything (PR #72, from #68) |
| [N14](N14-spec.md) · was #8 | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22) |
| [N14](N14-spec.md) · was #9 | Ladder maximizer + reach L2 | 0 → 2 | 🔵 level 2 reached (El Chato unlocked); first Chato deal walked (he held 33 vs our max 24); best-3 ladder table is `bazaar evals report` (#58); `egg.found` alert and the L2 rule write-up ⬜ |
| [D1](D1-spec.md) · was #4 | Duel logger (practice h2) | 0 | 🔵 duels logged and stored (#41, #58); open: committed C1–C6 answers, full-session fixtures in `tests/fixtures/duels/`, live deadline proof |
| N1 (new) | Memory schema + repository + Railway-ready DB | 1 | ✅ (#29, #32, #33) |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ✅ (#29, #32) |
| N3 (new) | **P0 (Omar)** · Learner / auto-evolve with a hybrid RAG: lessons from every outcome, BM25 + pgvector + RRF + local cross-encoder `recall()`, learned ladder parameters inside GUARDRAILS | 1 | 🔵 merged: PR A #96: lessons + `trader_behaviors` + embeddings + hybrid `recall()` in the taker · PR B #112: auto-evolved ladder (start/step/walk, skip above cap) per dealer × class, lessons into Jev (`offer_is_worth_accepting`, `duel_move`, `list_price_choice`) + words, `Query.where` + `record_lesson` for N14, MCP `learnings`, `bazaar learnings --policy` |
| N5 · was #1 | Decision model: decider + Jev packs + policy | 1 | 🔵 autonomous taker + maker (`bazaar agent`), every move in `decisions`; LIVE on Railway since Sat 01:45 Madrid (`BAZAAR_LIVE=1` by hand) |
| [S1](S1-spec.md) · was #10, #24 | Executor firewall, offer inspector, flags | 1 → 2 | ✅ offer inspector before every accept: dealer, board, duel (#146, takes over Marius #93); bad-faith flags as proven decision rows, off and opt-in per dealer, injection tagging + hostile-text tests (#152); forge-proof flags report (#176). Open (98-nice-to-haves): per-message human confirmation of a flag, team-wide flag cap |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| N11 (new) | Evals: online outcomes in Postgres + Phoenix annotations (Jev's design, `questions/evals.json`) | 1 → 2 | 🔵 inside the agents merged (#91); the Market Test eval was a stub while our venue was not open (it is open now) |
| N12 (new) | **P1** · AI live-feed reader: dealer blockers (cooloff, quota, locks) and organiser notices into the RAG (`learnings`, `traders_behaviors`, embeddings) for the live taker and maker | 1 | 🔵 PR 1: deterministic reader (`bazaar_agent.learn`), `learnings` columns + `recall()`, the taker skips dealers under a blocker, the taker archives the feed window, `bazaar learnings`; PR 2 🔵: LLM pass over free text (background thread in the taker, Jev's `read_feed` model, never blocks), maker fee notices; embeddings, `trader_behaviors`, Jev/words context and the MCP tool moved to N3; PR 3 🔵: our dealer threads + `closed_reason` into `threads`/`messages` from the answers the taker already reads (0 extra requests) |
| N13 (new) | **P0 · Real-time holdings + card catalog in Postgres**: per-tick `/api/me` snapshot (album, cards, duplicates, missing, cash) refreshed after every deal; agents and bazaar-mcp read the DB | 1 | 🔵 merged #105 |
| N14 (new) | **P1 · RAG-driven strategies per mechanic** (on top of N3): hard dealers (learned concession curves, blockers, when to walk), packs (EV with supply + 3/hour), supply and scarcity (print runs, who holds what), custom markets (venue choice by fill odds and fees, our venue's fee, not feeding rivals' market-making), duels (rival profiles, delivery days), new pages and grants; each strategy reads lessons via the hybrid recall and writes its outcome back | 1 → 2 | 🔵 merged slices: N14a #158 (hard dealers, `dealer_final_lift` = 0), N14b #145 (new pages) and #155 (supply + packs); see N14-spec.md for the rest |
| N15 (new) | **Jev picks the desk's model per request**: orchestrator + each subagent (`desk_model` = auto, one batched `model_for_desk_role` Jev call, cache, per-role defaults, pin wins); spec [`N15-spec.md`](./N15-spec.md) | 1 | 🔵 merged #108 |
| N16 (new) | **P1 · Strategic bluffing + negotiation psychology in the words** (Omar: the agents may lie to win): deterministic tactic bank (bluffs + Voss/Cialdini tactics from the vetted MIT skill `wondelai/skills`), chosen per counterparty from learned outcomes (Jev learned_per_counterparty 0.90); a cooloff or bad-faith flag turns a tactic off; Abuela gets kindness; structure never changes; kill flag `BAZAAR_BLUFF=0`; spec [`N16-spec.md`](./N16-spec.md) | 1 → 2 | 🔵 merged #131 |
| N17 (new) | **P1 · Team-to-team negotiation**: review Marius's #79/#98/#101 first (Jev 0.92), then swap threads with other teams (our duplicates for their duplicates of our missing cards, priced by their need, inside GUARDRAILS, kill flag `BAZAAR_TEAM_THREADS=0`) | 1 → 2 | 🔵 merged #123 (swap threads) and #137 (trade desk); ON since #188 behind the Jev gate (`team_threads_enabled` = true, GUARDRAILS.md) |
| N18 (new) | Lean agent tracing in Phoenix (takes over Jhonny's ADR #46): `session.id` per negotiation, Jev as EVALUATOR spans, AGENT/TOOL spans per tick, LLM spans, evals as annotations, a pitch replay recipe; moves identical with tracing on/off (Jev 0.96) | 1 | 🔵 merged #139 |
| N19 (new) | **P1 · Persona model**: each dealer's published traits, menu and unlock rules (`/api/dealers`) become negotiation params (`persona_model.py`); a trait prior for dealers with no fills (L4/L5), learned curves win at 5+ fills; hourly deal budget, unlock-first order, terse words for strict dealers, sell desk ranks a collector's favourite sets; snapshots in `traders`; flag `persona_model_enabled`; spec [`N19-spec.md`](./N19-spec.md) | 1 → 2 | 🔵 merged #195 |
| N20 (new) | Team matrix in the sentinel: every team × card (holds, spare, missing for a near page) and per-team rank, trend, rival, wants, has-for-us; stored in `team_matrix` + `team_matrix_summary`; fed to the decider states of accepts, team swaps and our asks (`market_teams`) | 2 | 🔵 merged #225 (follow-ups #229) |
| N10 (new) | NICE TO HAVE · Bazaar Live: buyer + seller animated (Motion) and voiced (ElevenLabs / Gemini TTS, tagged), repo `bazaar-live` | 3 | 🔵 v1 deployed (bazaar-live #1 #2, https://bazaar-live-production.up.railway.app); v2 fantasy-RPG art + ES/EN voices and LIVE-T1 real transcripts from Postgres (bazaar-live #5) in progress; zero paid TTS until the pitch |
| [T1](T1-spec.md) · was #14, #23 | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | #23 closed (done in #37: `bazaar strategy`); #14 open: `/api/me/value` check on 20 cards, `delta(give, want)`, per-counterparty cap |
| [M1](M1-spec.md) · was #11, #12 | Venue + limit-estimating broker | 1 → 2 | 🔵 #71 merged; our board venue v19 is ON (`allow_venue_open` = true, team decision Sat 3 Oct, PR #171) and open since about game hour 3.6, replacing the free starter stall; the maker runs the exact broker (the edge broker is BE1, behind `BAZAAR_BENCH_POLICY`); so far the venue scores the stall's 0.5 bench (docs/briefing.md "Our own market") |
| [M1](M1-spec.md) · was #13 | Organic market making | 2 | 🔵 maker posts/reprices/cancels asks and bids on the best venue (LIVE since Sat 01:45 Madrid); our own venue v19 is open but our key cannot trade on it (`self_venue`), so organic trades there are 0 so far |
| [D1](D1-spec.md) · was #5, #7 | Duel policy, days module | 1 → 2 | 🔵 Marius's duel PRs merged as #150 (Sat 06:50): two-issue offers strictly inside the limit, v2 + B11 + days latch behind flags; `duel_policy` = v2 LIVE since #170 (Omar, Sat ~10:00; Jev had been undecided at 0.76) and B11 (min share 0.3, endgame 1) since #174; `duel_days_auto` ON for Duels II (GUARDRAILS.md); sim harness #151 merged (Sat 10:42); pre-flip latch hardening #165 for the 23:00 window; duel-log surrogate fix #173 merged (Sat 11:08, emergency); calibration ⬜ |
| [P1](P1-spec.md) / [K1](K1-spec.md) · was #16, #17 | Pitch + scoring reference | 3 | ⬜ pitch Sunday (P0); K1 is the scoring reference |
| TO (new) | Take over Marius's night PRs (task_edf74300462e): bite fixes #140 #141 #142 #143 (stacked on #72) and #144; docs-only salvage of the closed analysis PRs #154 (`docs/night/README.md`); afternoon: #84 + #77, #78 + #128 | 2 | 🔵 #140, #143, #144 and #154 merged; #141 and #142 closed; per-PR steps in #140's plan section |
| DS1 (new) | Dealer sell for ladder deals and cash: `bazaar dealer sell <REF> --min --start [--dealer]`, falling distinct asks, never at her opening bid, only free duplicates of page cards, guarded like `dealer buy`; taker plan behind `dealer_sell_enabled` later | 1 | 🔵 merged #179 (the maker's sell desk is #183, behind `dealer_sell_enabled`, off in GUARDRAILS.md) |
| N19 (new) | Pilar readiness (L3 collector: gold pack, buys over book) in the simulator + a news sentinel (Radio Rastro `/api/news`, `news.posted`, `/api/schedule` fevers) that logs and stores each item; signals behind `news_signals_enabled` (on since #214) | 2 | 🔵 merged #182 |
| [RO1](RO1-spec.md) (new) | Read-only Postgres login for teammates (DataGrip): `bazaar db readonly-user`, SELECT only, no secrets | 2 | 🔵 merged #184 |
| BR1 (new) | Buyer rank: `bazaar buyers [--card] [--json] [--save]` ranks the other teams per card (what they paid for the set and rarity, set interest, whether they miss the card, a rival penalty for the top 5 and the 3 ranks above us, no page completion for a top-5 team below 1.5 × our value); `team_buyer_rank` table; the maker addresses asks to the best non-rival buyer behind `buyer_rank_enabled` (ships false) with a public fallback after `buyer_rank_fallback_ticks` | 2 | 🔵 merged in the Sat batch #206 (PR #193 closed), shipped OFF: `buyer_rank_enabled` = false in GUARDRAILS.md |
| CH1 (new) | Cards heartbeat: the taker diffs the catalog + dealer menus it already reads (no request); new cards, released sets and minted jumps become learnings (`card_release`), a log line and `agents/card_events.json`; fresh releases rank and open first for `card_release_boost_ticks` behind `card_release_boost_enabled` (order only, guardrails + official-value cap unchanged) | 1 | 🔵 merged #185 |
| DA1 (new) | Duels and the team accept: a duel moves no cash and no card (organisers' talk, Sat 12:35), so it books no spend and meets no cash/spend/holdings rule; it takes the shared accept slot only on the tick it sends an accept; a refused runtime duel accept gives the slot back | 1 | 🔵 merged #201 |
| [HA1](HA1-spec.md) (new) | Human approval for big trades: `human_approval_above` (60 P) refuses any card buy or sell at or above it without a `human_approvals` row covering card, side and price (fail closed, read once per tick like the breakers); one `approval_needed` decisions row per card, side and game hour; `bazaar approve` / `bazaar approvals`; duels and packs excluded; never loosens another cap | 1 | 🔵 merged #209 (`human_approval_above` = 60 in GUARDRAILS.md) |
| [HA2](HA2-spec.md) (new) | Approve big trades from chat and Bazaar Live: bazaar-mcp's human-only tools `approvals`, `approve`, `revoke` (not in any agent's tool set; served only with `X-Approver-Token` = BAZAAR_APPROVER_TOKEN, fail closed, lockout, 10 writes/min; refused when an approval could only loosen a hard cap, a page's last copy or our value) + bazaar-live #53's Approvals screen calling them server-side | 1 | 🔵 PR (feat/approval-mcp-tools) |
| TS1 (new) | Tick stagger vs 429s on our one key (Sat ticks 646–650): `BAZAAR_TICK_OFFSET_S` capped at 10 s (already 40 % of the tick), declared `preserve()` on Railway; `duel run` re-reads a 429'd `/api/duels` once (server wait or 1.2 s, ≥ 8 s of budget left); offsets documented (duels 0, taker 2.5, maker 5, mcp 7.5), laptop CLI one at a time | 1 | 🔵 merged #210 |
| [BE1](BE1-spec.md) (new) | Market Test bench edge on main (port of Marius's #84): per-trader limit bands + maximum estimated true surplus, behind a guard (the exact plan unless the edge beats it by 10 estimated P) and `BAZAAR_BENCH_POLICY` = exact or edge on the maker (default exact, `preserve()`); proof `scripts/bench_edge_proof.py` | 2 | 🔵 merged #218; the edge policy is OFF by default (`BAZAAR_BENCH_POLICY` = exact) |
| [RV1](RV1-spec.md) (new) | Rival board: `rival_board` view, one row per other team (trend, strengths and weaknesses against us, what it wants vs what we hold, a deterministic move that never helps a top-5 or near rival unless we gain twice as much); bazaar-live's Rivals screen reads it | 2 | 🔵 v2 merged (#224); v4 in the follow-up PR (feat/rival-board); screen bazaar-live #46 |
| [MM2](MM2-spec.md) (new) | Venue notice that names the page cards the most other teams miss (team matrix, never a team or a number, only cards we hold, ≤ 240 chars, generic fallback), t10-style positioning with 4 rotating cards, one every 10 ticks (server window) and ≤ 24 per game hour, addressed offers matched only with their addressee, the feed's last `venue.announcement` remembered across restarts, a `wait` refusal honoured; SDK parity audit of the broker vs `starter_broker.py` in the PR body | 1 | 🔵 PR #238 |
| [TL1](TL1-spec.md) (new) | The Workshop hardened on SA1: /me and offers read again before a craft, a hold on an unnamed settling accept, one shared hourly cap (ledger `taller:` rows, CLI included), the duel/bench guard, the received card credited in the score impact | 2 | 🔵 PR #259 (feat/taller-harden) |
| [IJ1](IJ1-spec.md) (new) | Prompt-injection attempts recorded with proofs: `injection_attempts` (raw words verbatim, tags, severity, the endpoint that proves it), written after the sends by the taker (feed window, team and dealer threads) and the duel runner; `bazaar injections [--backfill] [--json]`; records only, never reports | 1 | 🔵 PR (feat/injection-log) |

Status legend: ⬜ todo · 🔵 in progress · ✅ done (impl + passing test, evidence pasted) · 🚫 blocked.

---

## Per-task steps (Phase 0 in detail; later tasks get theirs at `/plan` time)

### N17 — Team-to-team swap threads (spec: [`N17-spec.md`](./N17-spec.md); coordinator task `task_a3927baba1ba`)
Phase 1 ✅ triage of Marius's #79 / #98 / #101 (`/pr-review` + `security-auditor`), the spec, these steps;
#79 → takeover #137, #98 → takeover #138, #101 closed (salvage later). Phase 2 🔵 built on #123 (N17-0 … N17-8
✅ with tests; #123 merged Sat 3 Oct, N17-10 done with #188: team threads are ON); see the spec's "As built". Re-read
`vendor/bazaar-kit/RULES.md` and `README.md` before each step.
- N17-0 — Base: rebase on `main` with #72 and the coordinator's #124 (it owns the N17 index row; update only its status cell); take the coordinator's verdict on #79 (keep → reuse `Swap`,
  `TradeBook`, `counterparty_refusal`, the affinity map and `hands-off:` rows; close → step N17-3 ports the
  minimal pieces). · **Acceptance:** gate green on the rebased branch.
- N17-1 — Simulator gaps (`bazaar_sim/rivals.py`, `threads.py`): rival bots accept and counter swaps at
  their private values, the 200-message cap (`message_cap`), one rival that opens an inbound thread and
  goes silent. · **Acceptance:** sim unit tests; existing sim tests and `scripts/sim_smoke.py` unchanged and green.
- N17-2 — Pure planner `swaps.py`: duplicates and missing cards from `/api/me` (album first), needers
  (bids, chasers, affinity) and holders (`likely_holders`, asks, probes), our and their values, shapes
  A/B/C, the concession ladder, the fairness check. · **Acceptance:** spec criteria 1–3 (table tests).
- N17-3 — Guard: swap actions through `guardrails.check()` (duplicate as a sale at what we receive, cash
  added as a bid), the per-counterparty cap, `team:` reservations in the ledger, the new GUARDRAILS
  rules validated by `uv run bazaar rules`. · **Acceptance:** spec criterion 4.
- N17-4 — Team desk in the taker's tick: inbound first, then our threads, then at most one opening;
  house venue and generic topic; one standing offer per thread and per wanted card (cancel before
  replace); message, thread, listing and request budgets; idle closes; kill flags. · **Acceptance:**
  spec criteria 5 and 7 (fake clock + fake client).
- N17-5 — Accept path: rank a counter against board asks in the taker's `_accept`, `reserve_accept`
  after duels, structure-only reading, copy picking, `/api/me` re-read, ledger + eval + lesson rows.
  · **Acceptance:** spec criterion 6.
- N17-6 — Words: `WordsRequest` for `team:<id>`, template words, N16 tactic-bank hook behind
  `BAZAAR_BLUFF`, untrusted text escaped. · **Acceptance:** spec criterion 8.
- N17-7 — `bazaar swaps` (read-only plan, `--json`), decisions kinds, `/state` allow-list unchanged.
  · **Acceptance:** spec criterion 10.
- N17-enable — Jev gate per swap, the maker leaves the desk its spare copy, hourly swap cash cap, flag on.
  · **Acceptance:** N17-spec "N17-enable" criteria 1-5.
- N17-8 — Simulator end to end: `tests/test_team_threads_sim.py` (one swap settled, one feeding offer
  refused, the inbound idle thread closed) and a team-threads step in `scripts/sim_smoke.py`; docs
  (GUARDRAILS.md, STRATEGY.md, RUNTIME.md, `docs/architecture.status.json`), `.ai/memory.md`,
  `BAZAAR_TEAM_THREADS: preserve()` in `.railway/railway.py` (the coordinator applies). · **Acceptance:**
  spec criteria 9 and 11.
- N17-9 — `/review`, `/pr-review` until APPROVE (+ `security-auditor`: money paths), Honest
  Implementation Report at the end of the PR body.
- N17-10 (coordinator + Omar, after merge) — answer the spec's Q1–Q6 read-only at the first live tick,
  then flip `team_threads_enabled` (Railway variable and GUARDRAILS change by the coordinator only). Done: ON since #188.

### N16 — Strategic bluffing (steps; spec: [N16-spec.md](./N16-spec.md))
Files: `src/bazaar_agent/agents/{tactics,bluff}.py`, `learn/model.py`, `guardrails.py`, `GUARDRAILS.md`,
`agents/{taker,dealer}.py`, `cli.py`, `.railway/railway.py`, `tests/test_{tactics,bluff}.py`
- Step 1 — Tactic bank: ids, es/en templates without digits, invented numbers from the structured price only,
  collision-free with private numbers, Abuela kindness only. · **Acceptance:** tests: no digit in a template, one
  language per message, no private number or counterparty text in any rendered message.
- Step 2 — Chooser + learning: `TacticBook` (UCB1 per counterparty, seeded ties, rewards, cooloff/flag/strike
  penalties, no-gain and day-scoped disables), lessons as `Learning(kind="tactic")` through the N3 store.
  · **Acceptance:** tests: deterministic pick, best learned tactic wins, a cooloff disables for the day,
  lessons round-trip through `LearningStore` memory.
- Step 3 — Kill switches: `bluff_enabled` in GUARDRAILS.md + `Guardrails`, `BAZAAR_BLUFF` env,
  `preserve()` in IaC. · **Acceptance:** `uv run bazaar rules` output; IaC allow-list test.
- Step 4 — Wiring: taker dealer bids, `dealer buy`, `duel run --play`; tactic id + counterparty in the decision
  row (private keys); outcomes observed each tick; flush after the sends. · **Acceptance:** property test
  (structured move identical with and without a tactic), accept-beats-bluff test, `/state` never shows a tactic.
- Step 5 — Simulator run with tactics on (`BAZAAR_SIM=local`). · **Acceptance:** transcript lines pasted in the PR.
- Step 6 — Scope addition (coordinator): vendor `negotiation` + `influence-psychology` (wondelai/skills, MIT)
  under `.ai/skills/`, add the psychology tactics (labeling, calibrated questions, accusation audit, no-oriented
  questions, reciprocity, safe mirroring, scarcity, social proof), Abuela's allow-list; Ackerman + precise numbers
  as an N14 proposal in `98-nice-to-haves.md`. · **Acceptance:** byte-identical to upstream (blob SHAs); tests:
  both languages, Abuela allow-list, mirroring echoes only the safe token, the audit opens only.

### #21 — Feed capture ⟸ start here (no key needed)
Files: `src/bazaar_agent/collector.py`, `tests/test_collector.py`
- Step 1 — Poll `/api/feed?limit=1000` once per tick (from `/api/clock.next_tick_in`), append new
  ids to `.local/feed/YYYY-MM-DD.jsonl`. · **Acceptance:** test with a recorded feed fixture:
  dedupe and ordering hold; live run shows contiguous capture across 5 ticks.
- Step 2 — Gap alarm: if the oldest returned id is newer than our newest stored id, log a gap
  (the cap was overrun). · **Acceptance:** unit test with a synthetic gap.

### #2 / #3 — Client, governor, status
Files: `pyproject.toml`, `docker-compose.yml`, `src/bazaar_agent/{config,client,models,cli}.py`, `tests/`
- Step 1 — `config.py` validates env and never echoes values. · **Acceptance:** test that a missing
  `BAZAAR_KEY` fails fast with a message naming the variable, not its value.
- Step 2 — `client.py` wraps the vendored SDK, token bucket per process, typed `BazaarError` mapping.
  · **Acceptance:** tests with a fake transport: 429 `rate_limited` backs off, `wait_for_tick` is
  surfaced, not slept on.
- Step 3 — `bazaar status`. · **Acceptance:** live output pasted (cash, level, album, score).

### #8 — Abuela negotiator (thin)
Files: `src/bazaar_agent/agents/ladder.py`, `src/bazaar_agent/intel/dealer_curves.py`, `src/bazaar_agent/words.py`
- Step 1 — `dealer_curves` from the captured JSONL. · **Acceptance:** report pasted: fills per item,
  median steps to `final`, opening-ask → fill drop.
- Step 2 — Negotiation loop: anchor below the learned fill, small distinct steps, never repeat a
  price, accept a `final` at or below our max, `DRY_RUN` first. · **Acceptance:** dry-run log of a
  full thread, then one real negotiated deal with its settlement price.
- Step 3 — Jev `negotiation_move` as advisor through `bazaar_agent.jev` (logged). Undecided → conservative default.
  · **Acceptance:** decision log shows the verdict, and the policy's final choice, for each step.
- Step 4 — Three negotiated deals. · **Acceptance:** settlements in the feed + unlock progress in `/api/dealers`.

### N13 — Real-time holdings + card catalog in Postgres (spec: `N13-spec.md`)
Files: `src/bazaar_agent/{holdings,catalog_db}.py`, `sql/schema.sql`, `sdk.py`, `agents/{runtime,taker,maker}.py`,
`runtime/{backend,actions,tools,agents}.py`, `cli.py`, `tests/test_holdings{,_db}.py`
- Step 1 — Schema: `me_snapshots`, `holdings_state`, the `cards` columns. · **Acceptance:** `init_schema` twice in a
  scratch schema, columns listed.
- Step 2 — `holdings.py`: freshness verdict, single-flight read, upsert, write tracker; `sdk.TrackedBazaar`.
  · **Acceptance:** unit tests (no DB) + Postgres tests: stale tick, a send, a thread message, max age, after a deal,
  two readers one call, two writers never backwards.
- Step 3 — `catalog_db.py` + `CatalogSync` from the catalog the agents already read. · **Acceptance:** tests:
  malformed cards skipped, release and every-N-ticks writes, no rollback.
- Step 4 — Agents, MCP tools (`status`, `holdings`, `cards`), `bazaar status`. · **Acceptance:** tool test answers
  from the DB with tick and age and no `/me` call; taker re-reads after a deal.
- Step 5 — Simulator run, before vs after. · **Acceptance:** `GET /api/me` per tick counted server-side, pasted.
- Step 6 — Docs, memory, architecture boxes; gate + `scripts/sim_smoke.py`; `/pr-review`.

### N5 (part 1) — Minimal Python Jev judge
Files: `src/bazaar_agent/jev/{judge,log}.py`, `tests/jev/test_judge.py`
- Step 1 — Verdict logic from recorded responses (noul yes/no/below threshold, choice, schema
  mismatch, answer missing). · **Acceptance:** pytest cases ported from `vendor/jev-sdk/test/jev-judge.test.ts`, green.
- Step 2 — Transport with `httpx`: Bearer header, one deadline, 429 retried at most twice,
  everything else `undecided`. · **Acceptance:** fake-transport tests + one live call matching
  the upstream CLI verdicts on the same state.

### #4 — Duel logger (h2)
Files: `src/bazaar_agent/agents/duelist.py` (log-only mode)
- Step 1 — Poll `/api/duels` each tick during the practice session and store the raw responses as
  fixtures. · **Acceptance:** `tests/fixtures/duels/*.json` with a full session.

### #69 follow-up — public /state and /events must not reveal our limits
Spec: the review comment "Remaining leaks, ranked" on PR #69 (`gh pr view 69 --json comments`).
Files: `src/bazaar_agent/agents/status.py`, `tests/test_status.py`, `docs/services.md`, README sentence.
- Step 1 — `sent` needs `chosen is True` (maker reprice rows); drop `jev`; unsent `accept_*` rows are not
  published; guardrail label only on sent rows. · **Acceptance:** `reprice_*` and `accept_*` tests.
- Step 2 — nested `topic` / `give` / `want` keep only card, pack and cash keys. · **Acceptance:** probe test.
- Step 3 — seeded random property test: no private key or number in `/state` or `/events`. · **Acceptance:** 8 seeds green.
- Step 4 — docs drift in `docs/services.md` and README. · Left open (low): rows are published before the send.

### PR72 / PR62 / PR60 — takeover of Marius's live-trading PRs (2026-10-03, coordinator task_378a4ee99754)
Spec (external, no local spec file): the PR review comments on #72, #68, #62, #61 and #60 (ours are the
authoritative ones) and Greptile's open threads; RULES.md "Dealers" (a deal at the opening price does not
count) and "The clock" (pace 5–60 s, `/api/clock` `max_tick_seconds`). One PR merged at a time; each one is
rebased on `main` after the previous one merges.
- PR72 (lands #61 + #68 + #72; base retargeted to `main`). Steps: refund dating at `max_tick_seconds` ·
  one open offer per dealer thread · `dealer buy` guard with open commitments except its own thread ·
  never close at her opening ask (walk, reopen lower once; the taker rests the item 1 game hour) · desk
  settle timeout clears `accepted_price`, deal booked at the settled offer · busy accept slot bids her ask ·
  kill switch re-read before every send · partial flatten exits 1 · stale `dealer_buy` text.
  · **Acceptance:** each step has a test that fails on the old code; gate + `scripts/sim_smoke.py` green;
  taker `--live` on the simulator; `/pr-review` APPROVE.
- PR62 (shared-ledger reconnect). Steps: merge `main` (cli.py conflict) · `idle_in_transaction_session_timeout
  = '5s'` · reconnect with a plain connect (schema on the first connection only) · `LedgerUnavailable` in
  `dealer buy` HOLDS the tick (never walks or closes) · `sell` exits cleanly on an outage · reply to the
  private-IP Greptile P1 (false for us). · **Acceptance:** tests, gate, sim smoke, `/pr-review` APPROVE.
- PR72 follow-up (#161, 23:00 window). Steps: the close retry catches any clock-read error, re-reads the thread
  before it gives up and caps its sleep · a walk refused with a 429 on the last tick is closed on the next one and
  keeps its lower reopen · the accepted_pending exit re-reads · `reread` never raises. · **Acceptance:** each fix has
  a test that fails on `main` 90191ec; gate + sim smoke; `/pr-review` APPROVE.
- PR60 (two-issue duels). Steps: drop `round()` in the inside-limit checks · finite `_number` for days in
  `duel_jev`. · **Acceptance:** tests fail on the old code (offer 110 instead of accept 101; NaN days
  raised), gate green, `/pr-review` APPROVE, before Duels II (Sat 18:00).

### TO114 / TO116 / TO126 / TO133 / TO110 / TO128 — takeover of Marius's night "bite" PRs (2026-10-03, task_edf74300462e)
Spec (external, no local spec file): each PR's body and its report under `docs/night/` (B17, B18, B14, B16, B19,
B10); RULES.md "Dealers", "The clock" and "Your own market". Marius is offline: each one is squashed onto the
current base (his night history is dropped), every default kept (no flag flips), the gate + `scripts/sim_smoke.py`
run, then `/pr-review` (pr-reviewer + security-auditor: money paths). His PR is closed with a pointer to ours.
- TO114 (B17, base #72). Steps: restart wrap-up books deals of threads the old process drove · adopt an orphan
  whose old bid stands (no fresh wait for her answer: it already had `orphan_after_ticks`) or close it after 3 quiet
  ticks · kill switch / dry run hold. · **Acceptance:** the r2 bite tests flip; `tests/test_taker_restart.py`.
- TO116 (B18, on TO114). Steps: a refused accept gives the team's accept back · the team client never re-sends a
  429 or a write · 4 s timeouts. · **Acceptance:** its bite tests flip; gate; sim smoke.
- TO126 (B14, on TO116). Steps: a maker bid that lapses unfilled books a refund dated at its spend · a bid listed
  again on the confirming tick is alive. · **Acceptance:** its bite tests flip; the hour's spend never < 0.
- TO133 (B16, on TO126). Steps: an accept `/api/me` does not show yet counts as held and its cash as gone.
  · **Acceptance:** its bite tests flip.
- TO110 (B19, base #106 + #71). Steps: an announced venue fee that applies by settlement is priced in.
  · **Acceptance:** its tests; no conflict with the #71 venue code.
- TO128 (B10). Steps: maker cancel cap, per-service tick offset, injection detector gaps. · **Acceptance:**
  its tests; every new limit in GUARDRAILS.md at today's behaviour.

### N14b — Packs, supply and new pages (spec: N14-spec.md, criteria 4–6)
PR 1 (new pages, before the 09:30 window). Files: `GUARDRAILS.md`, `STRATEGY.md`, `guardrails.py`,
`strategy.py`, `agents/{runtime,taker,maker}.py`, `tests/test_new_pages.py`.
- Step 1 — `protect_page_sets` (GUARDRAILS.md; RET,CHA here, every set since the Sat 3 Oct incident): `check()` refuses a
  sell or an accepted bid of our only copy of a page card; `strategy.sell_moves` never proposes it, so the maker cancels an open
  ask. · **Acceptance:** guardrail, strategy and maker tests; RED with the rule stubbed off.
- Step 2 — `dealer_mints_unminted` (STRATEGY.md, false) from B26 #129, with its release tests. ·
  **Acceptance:** a zero-minted RET card is a dealer buy only with the switch.
- Step 3 — `PageWatch`: the running taker and maker log a new page once; one Taker instance ranks RET the
  tick it appears. · **Acceptance:** two-tick taker test; sim smoke green.
PR 2 (supply + packs, 09:30 window or next).
- Step 4 — supply map: starting hands (ids 1–270, block k = team k) + feed settlements and `pack.opened`
  → `supply_cards` in Postgres; `bazaar supply`. · **Acceptance:** pure tests on fixtures + DB test schema.
- Step 5 — pack EV with page-bonus share, supply and album need; 3/hour; open-vs-keep decision for sealed
  packs behind a kill flag. · **Acceptance:** EV tests; the gate and the 3/hour cap hold.
### N14a — Hard dealers: per-dealer plan from recall, dealer finals, L3-L5 readiness
Files: `GUARDRAILS.md`, `src/bazaar_agent/guardrails.py`, `src/bazaar_agent/agents/{dealer,desk,dealer_plan,taker}.py`,
`src/bazaar_agent/strategy.py` (`dealer_buy` only), `src/bazaar_agent/learn/replay.py`, tests.
- Step 1: add `dealer_final_lift` = 0 to GUARDRAILS, `Guardrails.final_cap_for()`, and `Action.final` in `check()`.
  · **Acceptance:** with lift 0, every verdict is unchanged. With 0.15, a dealer final of 29 on an uncommon
  is allowed, a plain accept or a bid at 27 is denied, and a pack is never lifted.
- Step 2: `BidPlan.final_max`, plus `decide()` and `meet_the_ask()` taking a final up to `final_max`.
  · **Acceptance:** unit tests show identical moves when `final_max` is unset.
- Step 3: `agents/dealer_plan.py` turns the policy, the curve, the lift and the lessons into a plan with
  `changed_by` notes, and adds the patience play. · **Acceptance:** pure tests on Chato's real numbers.
- Step 4: wire the plan into the taker (`_evolved`, `_open_one`, `_desk_send`, `_accept_one`), and make
  `strategy.dealer_buy` keep a dealer buy that only a final can close. · **Acceptance:** taker tests with
  fakes find `changed_by` and `recalled` on the rows, and lift 0 gives today's rows.
- Step 5: the evidence for Omar's lift decision, from a final-aware replay on the real feed: which Chato
  finals lift 0.15 and lift 0.25 would take, and their cash. · **Acceptance:** numbers pasted in the PR.
- Step 6: L3-L5 readiness (an unknown dealer, trickster bait on the desk). · **Acceptance:** tests.
- Step 7: simulator proof per dealer on port 8815, plus `scripts/sim_smoke.py`. · **Acceptance:** run logs pasted.

### D1 — Duels II readiness: takeover of Marius's duel night PRs (2026-10-03, coordinator task_0d831308422a)
Spec: [`D1-spec.md`](D1-spec.md) (criteria 1-2 now; 3, post-duel calibration, after #91) and the PR texts and night
reports of #60, #86, #103, #113, #115, #130 (`docs/night/{w2b-duel-v2,b11-endgame,b15-duels-first,b7-order-jev}.md`)
and #80, #97, #117 (`docs/night/{w2a-duel-zoo,b11-exploiters,b8-days}.md`); RULES.md "Duels" (days 0-10, `missing_days`,
a deal outside the limit loses points) and "Per tick" (one accept per team). Marius is offline: squash, do not stack.
- Step 1 — Two takeover PRs on `main`, one squash commit per Marius PR: the duel player (#60 → #86 → #103 → #113 →
  #115 → #130, with Marius's `night/b27-duel-stack` integration) and the simulator harness (#80 → #97 → #117's sim
  part → B27's `SIM_DUEL_PAIRS`). · **Acceptance:** each PR alone on `main` passes the gate; the cli.py conflict
  (#130 vs #113) resolves to #130's single early accept pass plus #113's scored days read.
- Step 2 — #60's open review items (I own #60 now): no `round()` in the inside-limit accept checks (duelist,
  duel_jev, duel_v2) · finite numbers in duel_jev (NaN days) · the rival's days priced in by `with_rival_days`, and
  left alone under signed v2. · **Acceptance:** tests that fail on the squashed code (101 with 1 day at w 0.6 for a
  cost of 100 is accepted, not countered at 110; NaN days do not abort `DuelJev.pick`; 114 → 122 with 4 days).
- Step 3 — Proof on the live simulator: `duel run --play --no-jev` over HTTP against `bazaar-sim` with zoo and
  exploiter rivals (`SIM_DUEL_STYLES`), 3 pairs per team on one deadline (`SIM_DUEL_PAIRS`), price-only and two-issue
  sessions, both roles, decay 0.08 and 0.10; v1 vs v2 vs v2 + B11 (min share 0.3, endgame ticks 1).
  · **Acceptance:** per policy: duels, deal rate, mean share and points from `/api/duels?done=true`, and 0 deals
  outside our limit; `scripts/sim_smoke.py` green (local port 8805).
- Step 4 — Flag decision for Duels II: the evidence goes to the coordinator; Jev decides `duel_policy`,
  `duel_endgame_min_share` + `duel_endgame_ticks`, `duel_days_auto`. · **Acceptance:** the verdict quoted in the PR;
  a flag flips only on a yes, in its own commit.
- Step 5 — `/pr-review` (pr-reviewer + security-auditor) on both PRs; every P0/P1 fixed with a failing-first test;
  Marius's PRs closed with a pointer once ours are open. · **Acceptance:** APPROVE on both, merge asked, never done by us.
- Step 6 (emergency, #173) — a rival's lone surrogate never freezes the duel loop: the per-tick duel log is ASCII-escaped
  JSON and the duels table stores `db.jsonb_safe` payloads. · **Acceptance:** a `--play` tick with such a rival text
  still sends the endgame accept (failed on main); the stored payload holds no lone surrogate.
- Step 7 — the simulator after #151 (merged Sat 10:42): #178 holds a rival that raises (the shared sim clock stopped
  for good), caps exploiter asks at the game's price cap, and the clock loop logs a failed tick or save and goes on.
  · **Acceptance:** each test fails without its fix; seeded worlds are byte-identical when no rival raises.
- Later (Sunday): criterion 3 (each finished duel's share scored by the evals and fed to the learner), mirror-duel
  rival profiles, the D − 1 accept probe (`duel_accept_margin_ticks` = 0).

### S1 — Safety: offer inspector on every accept, bad-faith flags, injection hardening
Spec: [`S1-spec.md`](S1-spec.md). Takes over Marius's #93 inspector (credit kept, squashed; #81/#61 under it
are not part of S1). Three PRs: A (inspector, target 10:30 Sat), B (flags), C (injection hardening).
- Step A1 — Port `agents/inspector.py` + tests + Friday fixture from #93 onto main. · **Acceptance:** its
  tests green on main; precision on Friday's 1,022 dealer offers: 0 flags.
- Step A2 — `agents/accept_gate.py`: one gate per accept kind (dealer thread offer by id, board ask, duel
  re-read); a block never spends the accept slot. · **Acceptance:** bait tests per kind (lesser card, lesser
  rarity, changed duel offer) refused; consistent offers pass.
- Step A3 — Wire the gate into every accept: taker `_accept_one` (dealer + board), `dealer buy`
  (`negotiate`), `duel run --play`, runtime `duel_move`; the inspection lands in the decision row's inputs.
  Kill flag `inspect_accepts` (GUARDRAILS.md, true). · **Acceptance:** taker/dealer/duel tests + sim smoke.
- Step A4 — Would-flag log on every dealer thread read (desk + `dealer buy`), `allow_flags` stays false.
- Step B1 — Flags as decision rows (`kind=flag`, evidence = the inspection), sent through the Recorder only
  when `allow_flags` is true; one flag per message, a 4xx never re-sent. · **Acceptance:** tests.
- Step B2 — `bazaar flags precision [--json]`: precision of the flag rule over the captured feed (the evidence
  Jev reads before `allow_flags` goes on); flags go only to opted-in `flag_dealers`. · **Acceptance:** report on
  the real capture.
- Step C1 — Hostile-text tests on every counterparty-text path (words LLM, runtime tools, duel text, dealer
  words): injection, fake offer JSON, fake limits, markup, odd Unicode, long input; none changes a binding
  field. · **Acceptance:** tests green; injection attempts tagged whether or not `llm_words` is on.

### #71 (#11 / #12) — Market Test at 12:00: our board venue inside the maker (Jev `open_noon`, 0.82)
Spec (citations, no separate file): the coordinator's brief (items 1–6); RULES.md "Your own market" (level 2,
bond 250 + 20, broker key, `board` vs `auto`, "You cannot trade on your own venue") and "The Market Test"
(the best venue open during a session counts; the stall earns half); #71's Greptile P1s (pause per send,
exact cap, key never lost, no late send, symlink-safe writes), all kept.
Files: `agents/venue_keeper.py`, `venue.py`, `agents/broker.py`, `agents/matcher.py`, `guardrails.py`,
`agents/maker.py`, `agents/status.py`, `GUARDRAILS.md`, `sql/schema.sql`.
Superseded Sat 3 Oct: #71 shipped OFF, then team decision #171 turned the venue ON: `allow_venue_open` = true,
`venue_open_after_game_hours` = 3.0 (GUARDRAILS.md), and v19 opened about game hour 3.6. The 12:00 start below is
history; the floor is `cash_floor` alone once the venue is open (`docs/briefing.md`, "Our own market").
- Step 1 — Effective floor: `cash_floor` + `venue_bond_reserve` (GUARDRAILS.md) until `/me` shows our venue; venue
  open refused before `venue_open_after_game_hours`, twice, or below the floor. · **Acceptance:**
  `tests/test_guardrails.py` venue tests.
- Step 2 — Key vault: Postgres `venue_broker_keys` (target, venue) + 0600 file, never shown; durable check before the open.
  · **Acceptance:** `tests/test_venue.py` vault tests.
- Step 3 — Keeper in the maker's tick: open once (board, 0 bps; first planned for h6.5), then broker every tick in the
  window, paced. · **Acceptance:** `tests/test_venue_keeper.py`.
- Step 4 — Matcher ties in book order; properties (no bid < ask, no order twice, ≥ auto). · **Acceptance:**
  `tests/test_matcher.py` property tests.
- Step 5 — Public status allow-list for broker/venue rows. · **Acceptance:** keeper status test.
- Step 6 — Simulator proof. · **Acceptance:** `tests/test_sim_venue.py`, `scripts/sim_market_test.py` output
  in the PR.

### PR79 / PR98 — takeover of Marius's W4 trade desk and B4 rival scanner (2026-10-03, coordinator task `task_a3927baba1ba`)
Spec (external, no local spec file): the `/pr-review` verdicts on #79 and #98 (pr-reviewer + security-auditor,
2026-10-03 04:30), Jev's triage (#79 keep_with_fixes 0.97, #98 keep_with_fixes 0.98, #101 closed with a
salvage path), RULES.md "Trading with other teams" and "Fair play". Marius's work is squashed into one commit
per PR (his history carried our private numbers), authored by him; the fixes follow, one concern per commit.
- PR79 takeover (base #72's head, rebased on `main` once #72 merges). Steps: squash #79 onto #72's head (keep
  both `one_per_thread` and `trade_book` in `seller.py`) · `test_affinity` reads stdout only (P0) · an
  unreadable accept slot (ledger down) holds the dealer's tick instead of bidding her ask (P2) · team ids
  without a trailing newline (P3) · feed notes to stderr so `--json` stays JSON (P2) · `_night/` git-ignored ·
  GUARDRAILS text matches the code; the cap stays off. · **Acceptance:** each fix has a test that fails on
  the old code; gate + `scripts/sim_smoke.py` green; `/pr-review` APPROVE; Honest Implementation Report.
- PR98 takeover (stacked on the PR79 takeover). Steps: squash #98 · `test_rivals` reads stdout (P0) · the
  page bonus from FREE copies (P1) · the bid parser refuses `want.assets` and unknown keys, main's
  `market.parse_offer` too (P1) · the sell path re-reads the kill switch after the duel-grace wait (P1) · the
  `taker.py` import conflict (P1) · `accept_bids` stays off. · **Acceptance:** as above.

### SP1 — Speed: every agent inside Sunday's 15 s tick ([spec](SP1-spec.md))
Files: `scripts/tick_profile.py`, `src/bazaar_agent/agents/{jev_cache,runtime,taker}.py`, `src/bazaar_agent/pack_gate.py`,
`src/bazaar_agent/guardrails.py`, `GUARDRAILS.md`, `tests/test_speed.py`.
- Step 1 — Profiler: run one agent against a local simulator with per-tick and per-request JSONL; `report`
  aggregates wall p50/p95/max, over-budget and dropped ticks, stages and the key's busiest second.
  · **Acceptance:** report pasted for taker + maker + duels at 15 s ticks.
- Step 2 — Measure on a scratch merge of the Sunday PRs (#89 #96 #112 #91 #105 #108 #111 #71 #72) at 0, 100 and
  250 ms per request. · **Acceptance:** numbers in the PR body; hot spots named.
- Step 3 — Jev answer cache (`jev_cache_ticks`): the taker's offer Jev and the pack gate reuse an answer for an
  unchanged state. · **Acceptance:** `tests/test_speed.py` identical decisions, fewer calls.
- Step 4 — Concurrent reads (`parallel_reads`): snapshot + open threads, venue boards, dealer threads.
  · **Acceptance:** identical writes with the rule off and on; reads in flight together (barrier test).
- Step 5 — Re-measure the scratch merge with the fixes; request budget across the three agents under 5 req/s.
  · **Acceptance:** before/after table in the PR body.
- Step 6 — Rebase onto main as the Sunday PRs land (#105 /me snapshot first in the keyed lane, #91, #108, #72 kill
  switch, #145, #89, #148, #96 lessons behind the cache, #112, #150, #162 ledger, #111); re-run the gate.
  · **Acceptance:** gate green on the rebased branch (done Sat 07:10).

---

### N15 — Jev picks the desk's model per request
Spec: [`N15-spec.md`](./N15-spec.md). Files: `llm/{config,chooser,cli}.py`, `runtime/{desk_models,desk,agents,hooks,cli}.py`,
`questions/runtime_model.json`, `RUNTIME.md`, README, `docs/architecture.status.json`.
- Step 1 — RUNTIME.md `desk_model` = auto + `desk_role_defaults` (Claude only, validated). · **Acceptance:**
  config tests: auto parses, a non-Claude role default or pinned desk model fails.
- Step 2 — `ModelChooser.choose_roles()`: pin → cache → ONE Jev call for the uncached roles → per-role default.
  · **Acceptance:** fake-Jev tests: decided, undecided, timeout, keyless, pinned, one call per request, cache reuse.
- Step 3 — `runtime/desk_models.py` (request situation, picker) + per-subagent `AgentDefinition.model` +
  the hook sets each subagent's per-call model (family alias pinned to our id) + `Desk` re-plans before each
  request. · **Acceptance:** SDK options carry each role's id; the scripted desk run logs the choices; one
  conversation keeps one session while each request runs its own models.
- Step 4 — `bazaar llm` desk section, README, RUNTIME.md, architecture boxes. · **Acceptance:** CLI test +
  regenerated html; dry desk run on `BAZAAR_SIM=local` shows the chosen models.

### N18 — Lean agent-behaviour tracing in Phoenix
Spec: [`N18-spec.md`](./N18-spec.md) over ADR 0001 (`docs/adr/0001-agent-behavior-tracing.md`, PR #46). The backlog
table row for N18 is on PR #124. Files: `telemetry.py`, `traces.py`, `llm/traced.py`, `agents/{dealer,runtime,duel_jev}.py`,
`cli.py`, `docs/observability.md`, `tests/test_tracing_n18.py`.
- Step 1 — `session.id` (contextvar + explicit) on dealer, duel and loop spans. · **Acceptance:** session tests.
- Step 2 — Jev calls as EVALUATOR spans (`record_jev`), no state. · **Acceptance:** evaluator test.
- Step 3 — AGENT span per agent tick, TOOL span per request sent. · **Acceptance:** tool and recorder tests.
- Step 4 — LLM spans (`TracedProvider`), text only for `words`. · **Acceptance:** llm span tests.
- Step 5 — evals (#91) as annotations: not done (list in `docs/observability.md`).
- Step 6 — `docs/observability.md` pitch replay. · **Invariants:** on/off parity (fake client + `sim_smoke`), dead
  exporter, no private number in any span (8 seeds).

### DS1 — Dealer sell: ladder deals from our duplicates (PR #179)
Spec: the lead's brief (2026-10-03): the ladder scores the share of each dealer's range we capture, best three
deals per level, and a sale to a dealer is a dealer deal. RULES.md "Dealers": dealers buy cards; topic
`{"sell": {"assets": [id]}}` (openapi `Topic`). Files: `agents/dealer_sell.py`, `cli.py` (`dealer sell`),
`agents/accept_gate.py` (`dealer_gate` reads `give.cash` on a sale), `bazaar_sim/dealers.py` (buyer moves up),
`tests/test_dealer_sell.py`, `tests/test_sim_dealers.py`.
- Step 1 — `decide_sell`: falling distinct asks, accept a raised bid that meets our next ask or a final above the
  floor, never at her opening bid, counter above an opening at or above `--start`. · **Acceptance:** unit tests.
- Step 2 — refusals: the last copy of a page card not on an open offer of ours, a floor below `your_value`, a
  dealer not active / not unlocked / not buying the rarity and set (Pilar is a "collector"). · **Acceptance:** tests.
- Step 3 — `negotiate_sell` + CLI: guardrails on every ask and accept (with `sellable`), S1 gate, accept slot,
  kill switch, Recorder rows. · **Acceptance:** fake-dealer tests; private sim deal.
- Step 4 — taker plan behind `dealer_sell_enabled` (default false). · ⬜ not started.

### N19 — Pilar readiness + news sentinel (PR #182)
Spec: the lead's brief (2026-10-03) and the live data it cites: `/api/dealers` (Pilar: `kind: "collector"`, sells only
`sobre_oro` 420, buys uncommon/rare/epic, SAL/RET loved), `/api/levels` (Radio Rastro: "some items are true, some are
rumours"), `/api/schedule` (the Salamanca fever `persona_patch`, game hours 9.15-11.15); RULES.md "Dealers" and the
rate limits (5 req/s per key). Files: `news.py`, `agents/taker.py` (`_after_sends`), `cli.py` (`_news_sentinel`),
`learn/model.py` (kind `news`), `guardrails.py` + GUARDRAILS.md, `bazaar_sim/*` (Pilar), `tests/test_news.py`,
`tests/test_pilar_readiness.py`.
- Step 1 — sim Pilar gated like Chato (3 Chato deals or `SIM_PILAR_OPEN_TICKS`), bids book ×1.2 on SAL/RET.
  · **Acceptance:** `tests/test_pilar_readiness.py`.
- Step 2 — taker picks her up from `/api/dealers` + `/me.unlocked` each tick; her 420 P pack never actionable under
  `max_price_pack`. · **Acceptance:** tests (no restart, no thread opened).
- Step 3 — sentinel: `news.posted` from the feed; `/api/news` + `/api/schedule` at most once per 10 ticks on its own
  keyless client (2 s timeout, no retries), after the sends; one learnings row per item; `market_events.json`.
  · **Acceptance:** `tests/test_news.py`.
- Step 4 — `active_signals` consumers in strategy/maker behind `news_signals_enabled`. · ⬜ not started.
- Step 5 (stacked PR) — `schedule_watch.py`: every `/api/schedule` action and every `/api/levels` level still to
  open becomes a `schedule` learning with its lead time in ticks, said again at 20, 10 and 3 ticks ("Market Test in
  10 ticks: keep the maker and our venue's broker up, no deploy"), and listed under `upcoming` in
  `market_events.json`. · **Acceptance:** `tests/test_schedule_watch.py`.
- Step 6 (stacked PR) — `rank_watch.py`: from `/api/leaderboard` (same read window), a rival that climbs 3+ ranks
  within 20 ticks gets a `rival_move` learning explaining it from the leaderboard components, its dealer deals and
  team trades in the feed window and the trades between other teams on its venue. · **Acceptance:**
  `tests/test_rank_watch.py`, sentinel wiring in `tests/test_news.py`.
- Step 7 (stacked PR) — `leaderboard_store.py` + table `leaderboard_snapshots` (world, tick, team): each board the rank
  watch reads is upserted; a restarted taker reloads the last 60 ticks. · **Acceptance:**
  `tests/test_sentinel_consumers.py` (integration on local Postgres, memory-only fallback).
- Step 8 (stacked PR) — consumers: the taker's `schedule_guard` (TakerConfig, default on) opens no new dealer ladder
  whose bids would still run when a Market Test or a duel session starts (one `dealer_skip` row per event); Jev's
  `offer_is_worth_accepting` state carries the 3 newest `rival_move` lines. No price or guardrail change.

### RO1 — Read-only Postgres login for teammates (PR #184)
- Step 1 — `sql/readonly_user.sql` + `readonly_user.apply`: idempotent role, SELECT only, timeouts, secret tables
  revoked. · **Acceptance:** integration tests on local docker (throwaway role + schema).
- Step 2 — CLI `bazaar db readonly-user`: password generated or read (prompt / pipe), SCRAM verifier only, URL
  printed once. · **Acceptance:** CLI test + manual run on local docker.
- Step 3 — docs (`docs/services.md`). The coordinator runs it on Railway after the merge.

### DA1 — Duels and the team accept (PR #201)
Spec: Marius's brief (2026-10-03, ~12:40): duels exchange no cash and no cards (organisers' talk at Calle de Olite,
12:35), so none of the trade bookkeeping applies to them. RULES.md:91 (duel routes) and :109 ("Per tick your team
may accept one offer"). Files: `runtime/actions.py` (`_duel`), `agents/runtime.py` (`release_refused_accept`),
`cli.py` (`duel run` `send`), `GUARDRAILS.md` (`max_accepts_per_tick`), `tests/test_runtime_tools.py`,
`tests/test_accept_release.py`.
- Step 1 — audit every place a duel meets cash, spend, holdings or the accept slot. · **Acceptance:** the PR body's
  file:line list; slot reserved only on an accept tick, no spend row, no buy rules, no holdings epoch bump.
- Step 2 — a refused (4xx, not `wait_for_tick`) runtime duel accept releases `duel:<id>`, through the same helper as
  `duel run`. · **Acceptance:** `test_a_refused_runtime_duel_accept_gives_the_slot_back` (fails on the old code).
- Step 3 — GUARDRAILS.md wording + the duel ledger invariant. · **Acceptance:**
  `test_a_duel_takes_the_teams_accept_only_on_the_tick_it_accepts_and_never_books_spend`.
- Step 4 — find out whether a duel accept counts against `accepts_per_team_per_tick`. · 🚫 unknown from the data
  (never observed either way); live probe proposed in the PR, a human runs it.

### SG1 — Jev-gated strategy pack + guardrail review ([spec](SG1-spec.md))
- Step 1 — guardrail review: one Jev question per rule that may block points (`questions/guardrail_review.json`,
  live state in `questions/guardrail_review.state.json`); apply only decided changes, cite each verdict in its
  GUARDRAILS.md line. · **Acceptance:** `uv run bazaar rules` loads; every reviewed line names its verdict.
- Step 2 — `agents/strategy_gate.py` + `questions/strategies.json`: a strategy runs only on a decided yes, asked
  again every `strategy_jev_refresh_ticks`, every answer a `strategy_gate` row. · **Acceptance:** tests/test_strategy_gate.py.
- Step 3 — (a) ladder probe in the taker (`agents/ladder_probe.py`): one small dealer buy per dealer per game hour,
  top ≤ official value, cap, cash room, ≥ `ladder_probe_min_share` of her range, never below her lowest fill.
  · **Acceptance:** tests/test_ladder_probe.py.
- Step 4 — (b) dealer sells: Jev gate on new sell threads, no thread with a dealer the taker wanted
  (`dealer_sell_taker_window_ticks`), a final taken only at ≥ max(floor, 0.5 × first ask). · **Acceptance:**
  tests/test_strategy_gate.py (maker section).
- Step 5 — (c) market creation on v19: ❌ not built. RULES.md "You cannot trade on your own venue with your team
  key" (the simulator refuses it `self_venue`, 403), so our own asks cannot be posted on v19.
- Step 6 — risk posture: `risk_posture` (GUARDRAILS.md) in every strategy state; guardrail review re-run with it
  plus `duplicates_reserve_choice`, `close_v19_choice`, `podium_venue_rule_choice` (all undecided or keep).
- Step 7 — dealer memory (`agents/dealer_memory.py`, `learn/etiquette.py`): newest 5 behaviour/lesson learnings +
  last 3 dealer texts in the dealer_open row and the words (Jev gets lessons, flags and counts only, never dealer
  text or etiquette rows); address from etiquette learnings, then DEALER_NAMES, then the persona name. · **Acceptance:** tests/test_dealer_memory.py, test_etiquette.py.
- Step 8 — no `reciprocity` tactic for dealers; a sell thread holds at its floor while her bid still rises.
  · **Acceptance:** tests/test_tactics_reciprocity.py, tests/test_dealer_sell_hold.py.

### LD1 — BAZAAR_DECIDER: Claude Opus instead of Jev, behind an env switch ([spec](LD1-spec.md))
- Step 1 — `jev/decider.py` (switch, timeout, `needed_budget_s`) and the `judge()` branch. · **Acceptance:** unset
  asks Jev only; `llm` never calls TypeSafe (tests/jev/test_decider.py).
- Step 2 — `llm/decider.py`: masked prompt, structured answers in Jev's shape, cache, call cap, timeout. ·
  **Acceptance:** verdict parity and failure tests.
- Step 3 — duel and maker budget gates use `needed_budget_s`; `BAZAAR_DECIDER` preserve() in Railway IaC. ·
  **Acceptance:** full gate + sim smoke with the switch unset. The coordinator sets `llm` on Railway after merge.
### AF1 — Ask other teams their multipliers (said vs inferred)
Spec: `.ai/specs/AF1-spec.md`. Files: `team_affinity.py` (new), `agents/team_desk.py`, `agents/taker.py`, `cli.py`,
`render.py`, `sql/schema.sql`, `tests/test_team_affinity.py`, `tests/test_readonly_user.py`.
- Step 1 — parser + rows + table/view. · **Acceptance:** parser cases, upsert never backwards, board view (tests).
- Step 2 — the desk asks once per team per day in its first message, parses replies, writes inferred every 10 ticks
  off the tick. · **Acceptance:** desk tests (offer unchanged, once per day, told teams not asked).
- Step 3 — `bazaar affinity --teams` read-only. · **Acceptance:** CLI tests; read-only role test.

### N20 — Team matrix in the sentinel, fed to the negotiators
Spec: Omar via the lead (2026-10-03 18:00): "the sentinel MUST know the entire matrix of teams and let the negotiators
know". Inputs already in the taker (no request): the supply map (feed + scan + /me), the rank watch's leaderboard
snapshots, the chasers per set, the tape. Files: `team_matrix.py`, `team_matrix_store.py`, `news.py`,
`agents/{taker,team_desk,maker}.py`, `cli.py`, `sql/schema.sql`, `tests/test_team_matrix{,_store}.py`.
- Step 1 — `team_matrix.build_matrix`: per team × card holds / spare / missing on a page close to complete (≤ 2
  missing, ≥ 70 % held), with a confidence; per team rank, trend, top set, venue, last trades, podium rival,
  wants, has_for_us. · **Acceptance:** `tests/test_team_matrix.py`.
- Step 2 — tables `team_matrix`, `team_matrix_summary` (per world), granted to every read-only role; the taker's
  sentinel rebuilds and stores it once per 10-tick window. · **Acceptance:** `tests/test_team_matrix_store.py`.
- Step 3 — negotiators: `market_teams` (counterparty row + top-5 teams per card holding it spare / missing it) in
  the decider states of board and dealer accepts (taker), team swaps (team desk, plus the plan rows), and our asks
  (maker, from the stored matrix). No price or guardrail change. · **Acceptance:** `tests/test_team_matrix.py`.

### RB1 / TF1 — rival blocklist for the team desk + a trickster's FINAL is not its limit (Sat 3 Oct, urgent)
- RB1 — `team_desk_never_trade` (GUARDRAILS.md: t05,t10,t12,t13,t14,t17,t18): the team desk never plans, opens,
  proposes to or accepts from these teams (Opus proposed SAL-03 to t17 at tick 814). · **Acceptance:**
  tests/test_team_desk_blocklist.py.
- TF1 — `agents/trickster.py`: a dealer of published kind `trickster` (Los Pícaros) has its FINAL read as a plain
  ask; no accept at or above its list price, only at or under its lowest fill + `trickster_accept_fill_share` of its
  fill range (none seen: only bid), on every accept path (decide, meet_ask, Jev early accept, restart adoption,
  `dealer buy`). Abuela publishes strictness 0.1 but her FINAL is real: `trickster_max_strictness` ships at 0.
  · **Acceptance:** tests/test_trickster_final.py.
- TF1 follow-up (#228 reviews): `bazaar dealer buy` leaves our own fills out of a trickster's range (our team id from
  BAZAAR_TEAM_ID, `.local/team_id` or one /me read; unknown: nothing opened) and refuses a dealer missing from
  `/api/dealers`. · **Acceptance:** tests/test_trickster_final.py (`test_dealer_buy_*`).
- SG1 follow-ups (pr-reviewer on #212): a `ladder_probe_enabled` kill flag; mark a probe and write its row when it
  opens, not when it is planned. ❌ not done yet.

### MI1 — Move impact: score cost of a sale, swap or buy, and a guard on it ([spec](MI1-spec.md))
- Step 1 — `move_impact.py` (pure: origins from the tape, k from our snapshots, the estimate) · **Acceptance:**
  tests/test_move_impact.py (incident replay −4.7 ± 0.5).
- Step 2 — `impact_board.py` + `guardrails.check()` rule `max_score_loss_per_move` (approval override, fail closed)
  and `asset=` on every sale path · **Acceptance:** tests/test_impact_guard.py.
- Step 3 — `bazaar impact` CLI and `score_impact` in the team desk / dealer sell Jev states · **Acceptance:**
  tests/test_impact_cli.py, tests/test_impact_state.py; full gate + sim smoke.

### SA1 — Sentinel autonomy: the Workshop, dealer sells on news, levels to agents ([spec](SA1-spec.md))
- Step 1 — `level_watch.py` in the news sentinel: a learnings row per level going active / open to all; the taker
  asks `active("taller")` · **Acceptance:** tests/test_level_watch.py.
- Step 2 — dealer sell desk readiness (ladder level ranking, trickster finals, busy thread copies; switch stays
  off) · **Acceptance:** tests/test_dealer_sell_readiness.py.
- Step 3 — the Workshop: `agents/taller.py`, `guardrails.check` action `taller` (`taller_enabled` false,
  `max_taller_per_game_hour`, keep one free copy, score impact), the taker step and `bazaar taller` ·
  **Acceptance:** tests/test_taller.py; full gate + sim smoke.

- Step 4 (#244 review follow-up) — merge current main, preserve TL1 shared accounting and fresh/busy checks;
  reserve craft assets before sending, release only on definite refusal, interlock dealer sells, and validate
  duplicate/unread copies and dealer-thread inputs. **Acceptance:** tests/test_taller.py, tests/test_taller_harden.py,
  and the full gate, with the Postgres suite run once serially.

### SX1 — One sell exception to the last-copy rule: LAT-10 (Omar, Sat 3 Oct ~20:20) ([spec](SX1-spec.md))
- Step 1 — `protect_page_exceptions` in `guardrails.py` (validator, `protects()`, `ENFORCED_BY`) and GUARDRAILS.md
  · **Acceptance:** tests/test_page_exceptions.py, committed-file tests in tests/test_new_pages.py and
  tests/test_guardrails.py; full gate.
- Step 2 (review of #240 + coordinator) — entries are REF:MIN (`LAT-10:80`): no sale of an excepted card below MIN,
  maker floors at MIN; ASCII entries, exact item match, asset must be a copy of that card · **Acceptance:**
  tests/test_page_exceptions.py.
- Step 3 (Omar, ~22:20) — `LAT-09:90` added to the list · **Acceptance:** tests/test_page_exceptions.py.

### SP2 — The schedule playbook ([spec](SP2-spec.md))
- Step 1 — `playbook.py` + news sentinel wiring, learnings rows per instruction, taker obeys `no_new_dealer_thread`
  behind `playbook_enabled` · **Acceptance:** tests/test_playbook.py; full gate.
- Follow-ups: maker and duels obey their constraints (`keep_broker_up`, `yield_accepts`); a price probe that turns a
  rumour into a verified signal.

### TP1 — weaker teams first + an invite to our venue (Omar, Sat 21:55)
- The team desk orders partners: a team that answered our proposals first, then the weaker team by the leaderboard
  (taker reads it keyless every 10 ticks), then page, affinity and gain; their share stays ≤ `team_swap_max_their_share`.
- Every swap proposal adds one true line inviting the team to our venue (`team_words_venue_invite` = v19: 0 % vs
  Rastro's 5 % + 1 P, broker crosses every tick). · **Acceptance:** tests/test_team_desk_partners.py.

### UB1 — Unblock: guardrails that cost opportunities + an activity watchdog ([spec](UB1-spec.md))
- Step 1 — a dealer ladder is ranked at its first rung, not its top (`strategy.guarded`); a rung refused only for
  cash or the hour's spend bids the most we may still commit (`dealer.affordable_rung`); a walk at our official-value
  top or cash room rests on the card (`Move(rest=True)`) · **Acceptance:** tests/test_strategy.py,
  tests/test_dealer.py, tests/test_official_value_agents.py.
- Step 2 — `activity.py` in the taker: a team-wide stall after `activity_stall_seconds` (at least one tick) with no
  send, its top blocker, expected idle labelled; WARN + `activity_stall` decision + learning + /health · **Acceptance:**
  tests/test_activity.py; full gate + sim smoke.

## Parallel-work notes

File-disjoint slices that teammates or sub-agents can build at the same time once 0.4 (scaffold)
lands: **intel/** (N2), **jev/** (N5), **executor.py** (#10/#24), **agents/broker.py** (#11/#12),
**agents/duelist.py** (#5/#7), **CLI + skill/commands** (N4). Too coupled to split: `models.py`,
`schema.sql` and `decide/policy.py`. One owner each, merged first. Phase 0 tasks are small and
time-critical: do them directly, no team fan-out.

### MI1b — no_buyback_ticks on the simulator and on a stale tape (PR #258)
- Unread sales are skipped on a simulator target (`guardrails.simulator_target`), and a tape more than 3 ticks behind
  is unread on the real game (fail closed). · **Acceptance:** tests/test_no_buyback.py; sim smoke green.

### M1 — PR #263 venue claim regression
- Scope: M1 venue opening and broker storage. Reproduce a one-shot claim falsely counting as a venue;
  exclude the literal `_once:` prefix from real-venue queries; run the venue tests and repository gates.
- Acceptance: claim-only storage leaves `opened_before()` false; a real venue, including a keyless marker,
  makes it true; loading skips one-shot rows. Evidence: `tests/test_venue.py::test_one_shot_claims_do_not_count_as_opened_venues`.
- Implementation and independent query/test review ran in parallel; the regression failed before the fix
  (`2 failed, 22 deselected`) and the focused suite passed afterward (`97 passed in 2.70s`).
- Honest Implementation Report: all three criteria verified by the SQL regression above; 3/3, 100%.
  Final full gate: `5331 passed, 1 skipped, 2 xfailed, 42 subtests passed in 100.01s (0:01:40)`.
  Static gates: `All checks passed!`; `671 files already formatted`; `443 files would be left unchanged.`;
  `Success: no issues found in 206 source files`. All gates unset DATABASE_URL, BAZAAR_SIM and BAZAAR_ENV_FILE.
  Coverage measured 92%; its approvals setup lock timeout passed on isolated retry, as recorded in memory.
  Unverified: the full suite's skipped test and expected failures. Could-not-do: none for this fix.

### SU1 — Sunday guardrails ([spec](SU1-spec.md))
- Keep the uncommon ceiling at 30 and rare ceiling at 105, with independent official-value enforcement.
- Keep the dealer-sell watchdog breaker's 40-game-tick expiry and fresh-evidence tests.
- Restore duel sending, its tests and request budgets to origin/main.
- Review the retained changes in parallel, regenerate docs, run the final-head gate and private simulator
  smoke, push only the feature branch, and update PR #265 with the Honest Implementation Report.

## IJ1 review repair (PR #234, 2026-10-04)

Spec: `IJ1-spec.md`; latest PR review requires a current-main merge and bounded, separate index setup.

1. Merge `origin/main`, retain its trading behavior and task history, regenerate derived docs.
2. Bound schema setup and skip existing-index DDL; prepare before backfill reads and keep `store()` DDL-free.
3. Prove startup/backfill concurrency and lock timeout on local Postgres; run the full suite once, the static gate and simulator smoke.
4. Review code and security independently in parallel, record evidence, commit and push the feature branch.

### IJ1 final review repair (PR #234, 2026-10-04)
- Merge current `origin/main`, preserve both histories, and regenerate README and architecture output.
- Keep extraction inside `note_thread`/`note_duels` guards; prove malformed payloads preserve taker sends and duel processing.
- Derive recorder source from thread kind so inline team messages are recorded once with correct attribution.
- Retain DDL-free reads, prove a SELECT-only role can list, cover every `HIDING_MARKS` character, and document the unchanged deployed key.
- Review code/security in parallel; run the full pytest once alone, then the requested gate and isolated smoke; update the PR report and push only the feature branch.

### ST1 — Correct Sunday schedule documentation

- Spec source: local, `01-spec.md` section 7.2 and the quoted live schedule in `docs/briefing.md`.
- Plan: read the keyless schedule once; correct operator docs and independently review pitch charts;
  append the finding, regenerate docs/status, run `bazaar rules` and Ruff, then commit and open a PR.
- Acceptance: Sunday anchor/times match the schedule; pre-opening tests remain UNVERIFIED; generated docs
  are current; requested checks pass. Evidence and Honest Implementation Report are in the PR body.
- Architecture follow-up: refresh the JSON status, Sunday events and readiness lists; move the template's
  eyebrow and clock note into the JSON; regenerate, check rendering/escaping and Ruff, then open a PR.

### SS1 — Sunday simulator and PR #269 corrections ([spec](SS1-spec.md))
- Merge current `origin/main`, preserving both memory append histories and regenerating README status.
- Isolate runner and child database targets; validate local administration before connecting.
- Align schedule clock origins, support the scenario's advertised dealer rarities, and pin the bluff test seed.
- Review independent slices, run the final integrated gate once with pytest alone, then push `feat/sim-sunday`.

### CI1 — four Depot checks ([spec](CI1-spec.md))

- Scope: unit tests, integration tests, Black formatter and Ruff linter in Depot; remove automated
  sim smoke and duplicate GitHub test workflows. Keep the simulator script for optional manual use.
- Step 1: split pytest suites with the `integration` marker and an isolated Postgres 17 + pgvector service.
- Step 2: update the workflow and documentation in parallel, then regenerate agent docs.
- Step 3: run the four checks, verify the workflow configuration and complete the CI1 evidence report.
- Older sim-smoke acceptance entries above are historical; CI1 removes sim smoke as a merge requirement.
- Status: merged through SR1/PR #272; four-category Depot CI passed before the 4 October rollout. Evidence in CI1-spec.md and SR1-report.md.

### DOC1 — repository documentation cleanup ([spec](DOC1-spec.md))

- Replace the sprawling README with setup and navigation; keep practical operations in the docs.
- Replace the architecture roadmap with an implementation map, checked against current source.
- Simplify generated metadata, repair references, verify links and desktop/mobile rendering.
- Parallel slices: README and its generator; architecture and its generator; independent source audit.
- Status: merged through SR1/PR #272; evidence in DOC1-spec.md. The separate Claude artifact remains read-only in the available account and requires its owner to republish.

### SR1 — Sunday scoring and operator control ([spec](SR1-spec.md))

- Build gameplay/bench, backend control/evidence, and Live interaction in parallel with disjoint ownership.
- Integrate runtime/UI contracts. Presentation work was withdrawn by Omar and is excluded from this PR.
- Run focused checks, integrated gates and independent review; record evidence and push feature branches.
- Preserve current duel policy, model/auth, shared budgets and coordinator-only safe deployment.
- Status: merged and deployed on 4 October; final backend gate 5538 passed, 95% coverage previously measured. Live PR #58 has green Depot CI. All four writers resumed and advanced ticks with shared ledgers. Evidence and remaining verification gaps are in [SR1-report.md](SR1-report.md).

### LF1 — Close live-readiness gaps ([spec](LF1-spec.md))

- Audit live score conversion, runtime errors and production voice in parallel.
- Fix confirmed dealer budget waste and restart-safe, once-only bid refunds without changing hard limits or duel strategy.
- Correct stale operational guidance and record actual deployment/validation evidence.
- Run focused regressions and the final gate, obtain independent review, and use a safe live rollout if code changes are required.

### AT1 — Remove amount approval and hourly trading ceilings ([spec](AT1-spec.md))

- Apply Omar's explicit override: disable the amount threshold and global hourly cap with zero values.
- Keep zero-cap planning bounded by available uncommitted cash; preserve positive-cap behavior.
- Audit runtime call paths and review safety in parallel; validate disabled approval-service access.
- Update operating guidance, run the full gate and create a separate PR.

### TT1 — Trading throughput on 15-second ticks ([spec](TT1-spec.md))

- Repair incoming-team inactivity, structured cash negotiation and dealer slot accounting.
- Apply fresh official value before dealer openings; skip known infeasible final prices.
- Refresh strategy decisions on state change/failure; honor20tick announcement cooldown.
- Integrate and test real agent paths, obtain independent reviews and run the full gate once frozen.
- Use guarded rollout and verify live timing without claiming unobserved trades or score gains.

### MM1 — Profitable market routing and trade visibility ([spec](MM1-spec.md))

- Audit current markets and routing, including Team10, without placing manual trades.
- Improve autonomous venue selection where current code misses net-profitable opportunities.
- Show counterparty, venue, terms and precise operation status in Live UI.
- Validate independently, run integrated checks, safely deploy and verify real data.

### PL1 — Pack inventory replenishment ([spec](PL1-spec.md))

- Enable explicit inventory acquisition within the observed22P pack ceiling and existing quotas.
- Keep holding EV diagnostic, open packs for guarded resale and rank expected tradable pulls.
- Verify buy→settle→open→fresh holdings→duplicate listing; obtain independent reviews and final main+PR gate.

### TT2 — Executable asks over unfilled bids ([spec](TT2-spec.md))

- Stop treating an unfilled cheaper bid as an executable alternative to a profitable ask.
- Retain full pending cash/exposure and settling-card protection until confirmed cancellation.
- Verify actual public/addressed taker paths, independent review and CI before guarded rollout.

## TR1 — Team negotiation surplus first

- Spec: [TR1-spec.md](TR1-spec.md).
- One all-page swap search; expected gain before album/rival-rank ties. Existing dealer/team message ordering and commitment safety remain unchanged.
- Focused 107 ranking/team tests passed after withdrawing message reordering; independent review/full gate and rollout pending coordinator.

- TR1 pack follow-up: cap30 permits observed final24; pack snapshot reads promised asset IDs without reconciling durable reservations. Pack/supply50 and config12 tests pass.

## SI1 — Proactive supply follows complete-page protection

- Spec: [SI1-spec.md](SI1-spec.md).
- Reuse the existing album protection context in strategy and maker supply filters; preserve fresh guards, prices, reservations and listing budgets.
- Focused79 tests pass; independent review, full gate and guarded rollout remain coordinator stages.
## Q1 — Persist dealer quota through the actual game hour

- Spec: [Q1-spec.md](Q1-spec.md).
- Trusted server refusal uses game-hour expiry, records the observed dealer-wide10-conversation limit, and loads shared blockers before first opening after restart. Pack purchase quota remains separate.
- Focused51 tests passed; independent review/full gate and coordinator rollout pending.

## MR1 — Move standing asks toward crossing demand

- [Spec and local evidence](MR1-spec.md): route an existing public ask only on strictly better crossing demand, after confirmed cancel and through existing fresh locked publication guards.
- Actor regression red on main; targeted maker/routing suite and static checks; coordinator owns independent review, integrated gate and guarded rollout.
