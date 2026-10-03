# 02 — Master plan, part 2 of 2: phases, backlog and steps

- Status: **draft** — awaiting approval together with [`01-spec.md`](./01-spec.md)
- Backlog source: **GitHub issues** (`claude-hackaton-madrid-team-1/bazaar`, #1–#24; 18 open, #23 closed 2026-10-03). They are the
  source of truth; this file is the thin local mirror. Tasks marked `new` have no issue yet and
  need one filed before `/spec` starts on them.
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

### Phase 2 — Saturday (weight 1, 30 s ticks): run, measure, improve

- Buyer + seller agents live: team trades driven by competitor profiles (#13, #14); sell LAT and
  duplicates to the teams whose flow says they need them.
- Bench every ~2 h, the **h16** one is the hard one. Compare our efficiency against the stall
  after each session (#12).
- Duels II (price + days) at **h13** (#7). RET released at h4: re-run the collect plan.
- Flags only with words/structure mismatch + `message_is_bad_faith` ≥ 0.9 (#10).
- `bazaar jev report` + outcomes after every session. Tune thresholds and policy on evidence.
- Ladder: each new dealer level goes through the same curve learning before we commit our quota (#9).

### Phase 3 — Sunday (15 s ticks, ends h24): finish and present

- CHA released at h18. Duels III at h20; Abuela closes and the Grand Final at h23. Score freezes at h24.
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
| [#21](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/21) | Feed capture + dealer curves | 0 → 1 | 🔵 `bazaar monitor` (#32), real-time stream (#40), thread-fill fix (#58); open: Abuela `open`/`limit`/β estimate, ladder view (PR #43) |
| [#2](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/2) | Team key + API client + fixtures | 0 | ✅ key works; SDK bridge; API fixtures (#26) |
| [#3](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/3) | Tick loop, governor, scheduler, kill switch | 0 → 1 | 🔵 tick loop + budget + `.local/PAUSE` done; cancel-open-offers kill switch ⬜ |
| [#8](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/8) | Abuela negotiator (concession curve) | 0 | ✅ 4 negotiated deals (7/9/9/22) |
| [#9](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/9) | Ladder maximizer + reach L2 | 0 → 2 | 🔵 level 2 reached (El Chato unlocked); first Chato deal walked (he held 33 vs our max 24); best-3 ladder table is `bazaar evals report` (#58); `egg.found` alert and the L2 rule write-up ⬜ |
| [#4](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/4) | Duel logger (practice h2) | 0 | 🔵 duels logged and stored (#41, #58); open: committed C1–C6 answers, full-session fixtures in `tests/fixtures/duels/`, live deadline proof |
| N1 (new) | Memory schema + repository + Railway-ready DB | 1 | ✅ (#29, #32, #33) |
| N2 (new) | Intel: order book, tape, competitor profiles | 1 | ✅ (#29, #32) |
| N3 (new) | **P0 (Omar)** · Learner / auto-evolve with a hybrid RAG: lessons from every outcome, BM25 + pgvector + RRF + local cross-encoder `recall()`, learned ladder parameters inside GUARDRAILS | 1 | 🔵 PR A (stacked on #89): lessons + `trader_behaviors` + embeddings + hybrid `recall()` in the taker, `bazaar learnings --lessons/--query`; PR B ⬜: auto-evolved ladder (start/step/walk) per dealer × class, lessons into Jev + words, MCP read tool, replay + sim proof |
| [#1](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/1) | Decision model: decider + Jev packs + policy | 1 | 🔵 autonomous taker + maker (`bazaar agent`), every move in `decisions`; LIVE on Railway since Sat 01:45 Madrid (`BAZAAR_LIVE=1` by hand) |
| [#10](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/10) / [#24](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/24) | Executor firewall, offer inspector, flags | 1 → 2 | 🔵 guardrails + offer-term check done (#30, #31); `untrusted_text` wrapping (#59); open: executor, flags, hostile-text tests, public `/state` follow-up, duel limit (PR #60) |
| N4 (new) | `service.py` + CLI + bazaar skill + commands | 1 | 🔵 CLI + skill done; `service.py` seam ⬜ |
| N5 (new) | Jev port to Python (judge, mask, log, report, parity) | 0 → 1 | ✅ (#29, #31); recorded-fixture parity test ⬜ |
| N6 (new) | Voice interface: ElevenLabs agent + Python tool server | 4 | ⬜ later |
| N7 (new) | Observability: OTel traces → Phoenix, `bazaar thread(s)` | 1 | ✅ (#34, #35) |
| N8 (new) | Runtime LLM: Jev-chosen model, `--llm-runtime`, ask, words, steer | 1 | 🔵 worker |
| N9 (new) | Guardrails rule book (GUARDRAILS.md) | 1 | ✅ (#30) |
| N11 (new) | Evals: online outcomes in Postgres + Phoenix annotations (Jev's design, `questions/evals.json`) | 1 → 2 | 🔵 duels, dealer ladder, team trades scored (CLI `bazaar evals`); next: inside the agents, no Railway service; Market Test stub until we run a venue |
| N12 (new) | **P1** · AI live-feed reader: dealer blockers (cooloff, quota, locks) and organiser notices into the RAG (`learnings`, `traders_behaviors`, embeddings) for the live taker and maker | 1 | 🔵 PR 1: deterministic reader (`bazaar_agent.learn`), `learnings` columns + `recall()`, the taker skips dealers under a blocker, the taker archives the feed window, `bazaar learnings`; PR 2 ⬜: LLM pass over free text, embeddings, `trader_behaviors`, Jev/words context, maker fee notices, MCP tool |
| N13 (new) | **P0 · Real-time holdings + card catalog in Postgres**: per-tick `/api/me` snapshot (album, cards, duplicates, missing, cash) refreshed after every deal; agents and bazaar-mcp read the DB | 1 | 🔵 worker (before Sat 08:30) |
| N14 (new) | **P1 · RAG-driven strategies per mechanic** (on top of N3): hard dealers (learned concession curves, blockers, when to walk), packs (EV with supply + 3/hour), supply and scarcity (print runs, who holds what), custom markets (venue choice by fill odds and fees, our venue's fee, not feeding rivals' market-making), duels (rival profiles, delivery days), new pages and grants; each strategy reads lessons via the hybrid recall and writes its outcome back | 1 → 2 | ⬜ after N3 v1 (Sat 12:00) |
| N10 (new) | NICE TO HAVE · Bazaar Live: buyer + seller animated (Motion) and voiced (ElevenLabs / Gemini TTS, tagged), repo `bazaar-live` | 3 | ⬜ planned (98-nice-to-haves.md) |
| [#14](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14) / [#23](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/23) | Strategy engine (scarcity, valuation, buy/sell, 3-pack quota) | 1 | #23 closed (done in #37: `bazaar strategy`); #14 open: `/api/me/value` check on 20 cards, `delta(give, want)`, per-counterparty cap |
| [#11](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11) / [#12](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12) | Venue + limit-estimating broker | 1 → 2 | ⬜ not started (Market Test, Saturday) |
| [#13](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13) | Organic market making | 2 | 🔵 maker posts/reprices/cancels asks and bids on the best venue (LIVE since Sat 01:45 Madrid); our own venue ⬜ |
| [#5](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/5) / [#7](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/7) | Duel policy, days module | 1 → 2 | 🔵 safe player + days worst case (#31); calibration ⬜ |
| [#15](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/15) | Score simulator + dashboard | 2 (nice-to-have) | 🔵 outcome evals (#58) partly cover it; top-3 normalisation ⬜, dashboard in PR #43 |
| [#16](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/16) / [#17](https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/17) | Pitch + scoring tracker | 3 | ⬜ |

Status legend: ⬜ todo · 🔵 in progress · ✅ done (impl + passing test, evidence pasted) · 🚫 blocked.

---

## Per-task steps (Phase 0 in detail; later tasks get theirs at `/plan` time)

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

---

## Parallel-work notes

File-disjoint slices that teammates or sub-agents can build at the same time once 0.4 (scaffold)
lands: **intel/** (N2), **jev/** (N5), **executor.py** (#10/#24), **agents/broker.py** (#11/#12),
**agents/duelist.py** (#5/#7), **CLI + skill/commands** (N4). Too coupled to split: `models.py`,
`schema.sql` and `decide/policy.py`. One owner each, merged first. Phase 0 tasks are small and
time-critical: do them directly, no team fan-out.
