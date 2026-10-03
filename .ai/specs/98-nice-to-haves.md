# 98 — Nice-to-haves & deferred backlog

Out-of-scope items and review findings we chose not to fix now. (A) applied, (B) deferred.

## A. Applied in the finish pass
- (none yet)

## B. Deferred (documented, not built)

### NICE TO HAVE · N10 · "Bazaar Live": the buyer and the seller, talking out loud

**What.** A real-time show of our two trading agents for the Sunday pitch: the BUYER (taker) and
the SELLER (maker) appear as two animated characters at a Rastro stall, and every move they make is
spoken as a short, funny line of dialogue. Repo: `claude-hackaton-madrid-team-1/bazaar-live` (React,
its own repo, so the Python-only rule of this repo is untouched).

**Data (read-only, already live).** It consumes only the public services documented in
`docs/services.md`:
- `wss://bazaar-taker-production.up.railway.app/events` and `wss://bazaar-maker-production.up.railway.app/events`
  (`agent.tick`, `agent.decision`, `agent.execution`; a late joiner gets the last 200 events);
- `GET /health` and `GET /state` on both services for the initial picture (mode dry/live, tick,
  open offers, dealer threads, last 50 decisions);
- optionally the organiser's keyless `GET /api/feed` and `/api/clock` for the market around them.
No key ever reaches the browser.

**Animations (Motion for React, `motion` package).** Each event type gets one readable animation:
a tick heartbeat; an offer card flying from the seller's stall to the board (`post_ask` / `post_bid`)
and its price tag morphing on reprice; the buyer reaching for a card (`accept_ask`), a handshake and
confetti on an execution; a red stop sign that shakes on a guardrail denial; a Jev thought bubble with
the probability bar of its verdict; a DRY RUN / LIVE badge; a dealer (Abuela, El Chato) popping in for
`dealer_*` moves.

**Voices (TTS with expressive tags).** Each decision becomes a one-line exchange between the two
characters, written from the event (templates first; the runtime LLM can rewrite them later) and
marked up with expressive tags to make it funny (e.g. `[laughs]`, `[sarcastic]`, `[whispers]`,
`[gasps]`). Providers behind one adapter, chosen per availability:
1. ElevenLabs (the user asked for "v4 flash": confirm the exact model id and its supported audio tags in
   ElevenLabs' docs when building);
2. Gemini TTS (the user named "Gemini 3.8 TTS": confirm the model id and how it takes style
   instructions in Google's docs);
3. the browser's Web Speech API as the free fallback (no key, tags stripped), so the show works even
   without API keys during the hackathon.
Provider keys stay server-side: a tiny proxy (or the static host's function) holds them; the page
only receives audio. A mute toggle and a line queue so voices never overlap.

**Why deferred.** It scores with the judges (40 %, the pitch), not on the leaderboard; it must not
take time from the trading agents before Saturday's sessions.

**Rough cost.** One worker, ~2–3 h: Vite + React + TypeScript + Motion, the two WebSocket clients
with replay and reconnect, the event → animation and event → dialogue mappers, the TTS adapter with
fallback, and a static deploy (Railway service `bazaar-live`, declared in `.railway/railway.py`).

**Acceptance.** Connected to both live services, it renders the replayed history and then live
events; every event type has its animation; every decision speaks one tagged line (Web Speech at
least); works while the agents are in dry run; no key in the bundle; a 60-second screen recording
for the pitch.

### NICE TO HAVE · First eval target (Omar, 2026-10-03)
The evals (#58) score every decision's online outcome. Picking ONE target to optimise first is not a
blocker: Jev leaned toward duels (0.64 / 0.72, under its 0.75 bar). Revisit after Saturday's first
sessions, with real outcomes in `bazaar evals report`.

### PROPOSAL for N14 · Ackerman offers and precise numbers (from N16, Omar 2026-10-03)
N16 brought the `negotiation` skill (Voss, vendored in `.ai/skills/negotiation/`) into the words only.
Two of its ideas change PRICES, so they are not in N16 and belong to N14's per-mechanic strategies:
- **Ackerman schedule.** Set a target first, open at 65 % of it, then move to 85 %, 95 % and 100 %:
  the steps shrink, which signals a limit close by. Today's dealer ladder moves in fixed steps
  (`BidPlan.step`), and the duel anchor concedes linearly (`duelist.our_target`). An Ackerman ladder
  would be a new `BidPlan` shape. Its target is our walk point (N3's learned ladder or `max_price`),
  never above a GUARDRAILS cap.
- **Precise, non-round numbers** on the last offers (for example 23, not 25; or 97, not 100). They
  sound calculated and final.
- **A non-monetary closer** on the final offer: a promised return visit, or a card swap later. The
  words can already say it (N16 tactic `reciprocity`); a real extra item would be a structure change.
How to judge it: replay Friday/Saturday dealer threads (N3 `learn/replay.py`) and the duel zoo with
fixed steps vs Ackerman. Adopt it only where the learned fill prices improve. Dealers move only when
we move, and small steps earn small steps (RULES.md), so check that 65 % openings do not make
dealers ignore our first bids.

### Throughput / scale
- (none yet)

### Correctness / durability
- Guardrail `steering.json` and per-duel state live per container: move them to Postgres (Railway services report).

### Security hardening
- (none yet)

- Local score simulator + observability dashboard (was GitHub #15, closed 2026-10-03 as not needed to win; see docs/issues-archive.md). The evals (#58, #91) and Phoenix cover the useful part.

## S1 follow-ups (security audit r4 on #152, 2026-10-03)
- **Per-message human confirmation of a flag** (P2): an opted-in dealer's future messages are flagged without a
  human reading them, and honest out-of-stock words outside the denial list can still grade `flag` (28 of 31 in the
  audit's new battery). Proposal: `bazaar flags send <message_id>` after reading `bazaar flags precision`, or a
  confirmed-ids list; optionally count a claim only when the sentence also hands the card over ("here is", "for you").
- **Team-wide flag cap** (P3): `max_flags_sent` holds per data dir; two live takers on our key could each send the
  same flag. Claim the flag in the shared Postgres ledger before the POST, as accepts do.
- Look-alike letters still untagged (nit): U+01C0, U+A7AE, U+0196, Runic, Old Italic. Tags change nothing we send.

- **AF1: a stated multiplier as a valuation hint** (P2): feed `team_affinity` 'said' rows (confidence ≥ 0.5, agreeing
  with the inferred top set) into the team desk's estimate of the counterparty's private value, capped and never
  above our own estimate. Left out of AF1: words are untrusted and the swap ladder already prices on the map.
- **AF1: persist "asked today" across restarts** (P3): a restart may ask a team a second time the same day.

- **MI1 follow-ups (PR #227 reviews).** (1) The dealer sell desk picks the first copy of a duplicate in /me order; when
  a team-bought and a pack copy share the value (SAL-02 asset 22 from t02 vs 1019), prefer the copy NOT bought from a
  team (`impact_board` origins): selling the team copy is the model's unverified case. The desk is off today
  (`dealer_sell_enabled` = false). (2) An approval covers every sale of the card at or above its min (any copy, any
  counterparty) until it lapses; bind an impact approval to the asset and the counterparty kind. (3) A team buy whose
  settlement the taker has not archived yet (1-2 ticks) reads as a pack copy while the tape is still current.
