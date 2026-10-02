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

### Throughput / scale
- (none yet)

### Correctness / durability
- Guardrail `steering.json` and per-duel state live per container: move them to Postgres (Railway services report).

### Security hardening
- (none yet)
