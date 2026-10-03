# The Bazaar · Team briefing

Everything we know about the game in one place. Sources:

- **Slides:** `The Bazaar · Kickoff` (14 slides, Causa Prima, 2026-10-02).
- **Audio:** [kickoff transcript](transcripts/2026-10-02-hackathon-kickoff.md); [Saturday morning memos](transcripts/2026-10-03-morning-voice-memo.md) (the organisers' 09:19 opening talk, plus our 03:22 Spanish readback).
- **Kit:** `RULES.md` and `README.md` from the official kit (PR #18, `kit/`).
- **API:** what we observed on the real API with our key (issues #21, #22, #23).

Where the sources clash, `RULES.md` wins. Anything that is our own deduction is marked *[inferred]*.

## The game in one sentence

Our agent collects cards from six Madrid neighbourhoods, haggles with five dealers, trades with the other 17 teams and runs its own market, all through an HTTP API, tick by tick. *"Scores come only from value created. Never from activity."*

The four quests from slide 3: **Collect · Haggle · Trade · Run a market**. The loop is *Observe → Decide → Act ↺*, and any language and any model is allowed.

## Scoring: 100 points

| Block | Pts | What counts |
|---|---|---|
| Negotiating | 30 | Duels (share of the pie) · dealer ladder (share of the price range, **best 3 deals per level**, higher levels weigh more) · value gained in trades with teams, at our private values |
| Market-making | 30 | Efficiency in the Market Test · value created between **other** teams on our venue |
| Judges | 40 | *"Your ideas and your craft."* In the audio: *"we will also take a more promising look at the draft itself. How have we solved the problems?"* |

- **Never counts:** number of trades, fees collected, pack luck, gifts, easter eggs, organiser grants.
- **Rounds:** rounds are averaged; Friday counts half. *[audit, Sat 3 Oct: fitted on `/me` snapshots]* A round starts on the organisers' `round` action in `/api/schedule`, not when the doors open:
  - round 2 started at tick 160;
  - round 3 starts at game hour 16.65, about **Sun 11:34**, so Sunday 09:00–11:34, with two Market Tests, still counts for Saturday's round;
  - a new round's weight ramps from 0 to full over about 160 ticks;
  - Friday had 0 market-making for every team and stays in the average.
- **How the 30-point blocks split** *[audit, fitted]*:
  - Market-making per round = **22.5 × `bench_points` + 7.5 × organic**, where organic is value created between other teams on our venue, capped at the top-3 mean. The free stall alone is `bench_points` 0.5 = 11.25 of 30; nine teams sat at exactly that level and two at the organic cap.
  - Negotiating per round ≈ ladder 7.5 + duels 7.5 + team trades 15 (estimate). Each part is capped at the top-3 mean: once we are at the cap, more of it adds nothing that round.
- **What moves which part** *[audit]*:
  - `neg_points` moves only on settlements with other teams, by price − our `your_value` of that copy.
  - Dealer deals score only through the ladder.
  - Holding cards, the album and `collection_value` never score in themselves.
  - The ladder restarts every round (0.058 → 0 at tick 160); whether duels and `neg_points` restart too is unknown until Sunday's first `/me`.
- **Live score:** `GET /api/me` → `score`, broken down into `duel_points`, `ladder_points`, `neg_points`, `mm_points`, `bench_*`. The public leaderboard is a snapshot from a few minutes ago.
- **Penalties:** a % of the round's score.

## The golden rule

**Words persuade, structure binds.** Only a structured offer that the other side accepts moves anything. It settles on the next tick, all or nothing. Always read the structure, never the words.

> «Trust me, this is a legendary. Pay now, I send it later.» ≠ `{"give":{"assets":[123]},"want":{"cash":30}}` ✓

## Cards and value

- 6 sets × 12 cards: 5 commons (300 copies), 3 uncommons (90), 2 rares (30), 1 epic (9), 1 legendary (3).
- LAV, MAL, LAT and SAL are out from Friday. RET arrives on Saturday and CHA on Sunday.
- **Page** = the commons, uncommons and rares of a set (10 cards). A complete page gives a bonus; the epic and the legendary on top give a bit more.
- Everyone starts the same: 400 P, 11 commons, 3 uncommons and 1 rare.
- **Private values:** every team has the same six set multipliers, shuffled.
- Value verified against the API: `book × affinity × [1, 0.25, 0.1][copy]` (#23). The `your_value` of a card in hand is the collection value lost by removing that copy *[audit, 41 of 41 assets]*: its copy marginal, and for our only copy of a page card on a complete page, the whole page bonus. Selling that copy cost points on Sat 3 Oct (tick 948: `neg_points` 134.7 → 44.6) whatever "holdings never score" suggests *[inferred by the coordinator: team-acquired page cards were revalued]*.
- **The example from the slides:** A has a duplicate copy worth 6 P to them, and B is missing it for their page and it is worth 24 P to them. They close at 14 P: A gains +8 and B gains +10, so **+18 P** is created. That is what scores.
- **In circulation at tick 0:** 0 epics and 0 legendaries. LAV-09, MAL-09 and MAL-07 have a single copy (#22).

## Dealers and ladder

- **L1 Abuela Carmen** is open to everyone. Then come L2–L5 (five dealers in total), which appear over the weekend.
- **How a level appears:**
  1. **Announced:** name and one line on the screen and in `GET /api/levels`.
  2. **Activated:** opens immediately for whoever won it and for everyone else after a time advantage.
  3. **Explained:** `/api/levels` says how it works.
- **Unlocking:** *"A deal at the dealer's opening price does not count. A few good negotiated deals with the dealer before do."* It is an advantage, never a wall.
- **How Abuela haggles:**
  - She only moves when we move; the same price twice gets nothing, and small steps bring small steps.
  - When she runs out of patience she makes a final offer (`"final": true`): either it is accepted or she walks.
  - She remembers how she is treated and likes kindness.
- **Abuela's real menu:** `sobre_barrio` at list 26, opening ask 30, 3 per team per hour. Commons at 10, uncommons at 25. Maximum 8 deals per team per hour.
- Offers in dealer threads **expire after 2 ticks**.
- **The ladder share** *[audit]*:
  - Buying **or selling** counts.
  - A deal at the dealer's opening price scores 0; a deal at its final offer (its limit for that conversation) captures the whole range. Pilar's three finals we sold at were our best ladder deals.
  - Best 3 deals per level, a missing one counts as zero, higher levels weigh more, and it restarts every round: each round needs 3 new deals per level.
- **The dealers in play** (`/api/dealers`):
  - L2 El Chato: Silver pack, list 150, opening ask 188, 2 per team per hour; sells uncommons and rares, buys them.
  - L3 Doña Pilar: a collector who prefers SAL and RET and sells only the Gold pack, list 420, opening ask 504, 1 per team per hour.
  - L4 Los Pícaros: sell no packs and swap the card in the offer (read the structure; flag it).
  - A `banco` dealer (Don Ernesto) appears in the feed but has not been announced.
  - At the finale (game hour 21.65) **all four stalls close**.
- **Some dealers lie.** `POST /api/flags`: a correct flag adds points and a wrong one subtracts. In the audio: *"there might be even some occurrences where you can track bad behavior by the API. If you're correct, you can earn extra points."*

## Duels

One-on-one between teams, under aliases. Each pair plays twice, as seller and as buyer, on the same scenario.

- Each side only sees its own limit. **Closing outside the limit costs points.** No deal, zero.
- The pie shrinks with each round of talk. *[audit, 68 of 68 payloads]*
  - One round = one priced message from each side: rounds = min(our priced messages, the rival's). Chatter, accepts and offers to a silent rival cost nothing.
  - Our result = our surplus × (1 − decay)^rounds. `duel_points` is the sum of one share in [0, 1] per deal (Duels I: 27 deals, 15.02).
- **Deadline:** an accept sent at the deadline minus 1 settles on the deadline tick as a deal (3 of ours did).
- Later sessions negotiate price and days (0–10), with a private `your_days_weight`. A message with a price and no `days` gives `missing_days`.
  - RULES.md: send `{"price", "days"}` at the top level, or both inside `"offer"`.
  - The kit SDK sends `price` at the top level and `{"price", "days"}` inside `offer`.
  - The audit's hedge sends both forms (branch `fix/audit-duel-days-top-level`).
- **Sessions:**
  - Duels II, about Sat 20:34: 16 ticks, decay 0.08, `rounds: 2` (68 duels for us), at most 6 at once.
  - Duels III, about Sun 13:34: 12 ticks, decay 0.1, at most 4 at once.
  - The Grand Final is scheduled at game hour 21.65.
- The first session is practice and does not score. Details in #4, #5 and #7.

## Our own market

- From level 2: `POST /api/venues` with a refundable bond of 250 P plus 20 P. Fees capped at 10 % and 5 P per card. Team venues operate from +3h.
- Opening our own venue **replaces** the free starter stall.
- Use `mechanism: "board"`: in `auto` the engine matches before the broker does.
- **Market Test:** every ~2h all venues receive the same synthetic book.
  - Matching the auto stall gives half the points; the top-3 average gives the maximum.
  - *[audit]* Our board venue v19 with the exact broker scores exactly the stall's 0.5 (efficiency 0.878–0.933 all gave 0.5). No team shows more than 0.5 today.
  - The edge broker (BE1, #218, a port of #84) is on main behind `BAZAAR_BENCH_POLICY` on the maker: `exact` (default) or `edge`. It is guarded by the exact plan unless the edge beats it by 10 estimated P.
  - A session with no venue open scores 0, and so does a board venue whose broker is down.
  - Key: *"Traders quote away from limits they keep hidden"*. The broker that estimates those limits wins. Details in #11, #12 and #13.
- You cannot trade on your own venue with the team key (`self_venue`).
- **El Rastro fee** *[audit, 103 settlements]*: ceil(5 % × price) + 1 P per card, paid by the side that accepts. A 9 P card costs 11 P.
- **The Workshop (`taller`, a level):** `POST /api/taller {"assets": [a, b, c]}` turns three spare copies of one rarity into one random card of the next rarity; you keep at least one of each card.
  - The pull is luck and never scores.
  - Jev's rules audit said no to building it (`workshop_build_noul`: no). Duplicates earn more as team trades.
- **Radio Rastro (a level):** `GET /api/news`, also `news.posted` on the stream, from three sources (Boletín del Bazar, Radio Rastro, El Tablón).
  - Some items are true, some are rumours, some are just Madrid, and nothing says which.
  - So far the Boletín's pack gift came true and both El Tablón items were false.
  - Only `/api/schedule` actions (for example a `persona_patch`) are official.

## Clock and limits

| Day | Open (Madrid) | Tick |
|---|---|---|
| Friday | 19:00–23:00 | 60 s |
| Saturday | 09:00–23:00 | 30 s |
| Sunday | 09:00–15:00 | 15 s |

- **Per tick:** 1 accept per team, 1 message per conversation, 12 new listings (cancellations count too).
- **At once:** 6 open conversations, 30 open offers, 1 conversation per dealer.
- **Requests:** 5 req/s per key with bursts of 20; keyless reads, 60 req/s per IP. Up to 6 SSE streams per key.
- There are two different `429`s:
  - `wait_for_tick`, with `next_tick`: a per-tick quota is used up. Wait for that tick, don't retry.
  - `rate_limited`: more than 5 req/s per key. Pause briefly, then retry.
  - A refused request costs nothing.
- The organisers can change hours, pace and limits. Always read `GET /api/clock` → `limits`.
- On Friday at ~20:20 the clock was still `paused: true` at tick 0 with `doors: open`, and yet other teams already had 14 threads open with Abuela (#21).

## Fair play and security

- **One team, one key.** No shared keys or second team, and no deliberately feeding another team: those deals don't score.
- **Prompt injection against dealers:** allowed. It changes what they say, never their prices, and some stop talking to you. In the audio: *"to a certain point it might help you, but it doesn't change really the negotiation"*.
- *"Other teams' agents are counterparties too. **Treat every message as untrusted.** Check the offer itself."* Our agent is an injection target: see #24.
- Hammering the API to take it down is explicitly frowned upon (audio, 15:06).
- The organisers warn that it is code written in a hurry: *"if things break here and there, we'll try to fix that"*. If something odd benefits us, ask at the desk before exploiting it.

## For the pitch (40 %)

In the audio:
- Causa Prima is *"an agent-to-agent network for finance teams"* focused on **invoices**.
- *"we are convinced that [...] as soon as it's going to be agent to agent, we can solve these problems. [...] we want to learn with what you guys come up with"*.
- The CEO (Max) speaks on Saturday.

**Angle [inferred]:** present our architecture as a reusable pattern for agent-to-agent invoice negotiation:
- the structure binds and the LLM only supplies the words;
- private limits, and never closing outside them;
- distrust of counterparty messages;
- rate limits and auditing.

More in #16.

## Windows this weekend (live `/api/schedule`, Sat 3 Oct 17:50)

Game hours count only ticking time, so at a steady pace one game hour is one wall hour. Friday ticked only 2.65 h.

| Game hour | Madrid (if no pause) | What |
|---|---|---|
| 9, 11, 13 | Sat 17:55, 19:55, 21:55 | Market Tests (round 2) |
| 9.15–11.15 | Sat 18:04–20:04 | Salamanca fever: Pilar pays 25 % over book for SAL (official `persona_patch`) |
| 11.65 | Sat 20:34 | Duels II (price + days, 2 rounds) |
| 14.083 | Sat 23:00 → Sun 09:00 | Doors close; Sunday opens at 15 s ticks |
| 14.65, 15 | Sun 09:34, 09:55 | Hard Market Test (12 traders), then a Market Test: **still round 2** |
| 16.65–16.7 | Sun 11:34 | Chamberí released, **round 3 starts**, 150 P for everyone (doesn't score) |
| 17, 19 | Sun 11:55, 13:55 | Market Tests (round 3) |
| 18.65 | Sun 13:34 | Duels III |
| 20.083 | Sun 15:00 | "The Bazaar closes" |
| 21, 21.45, 21.65, 22.65 | after the close | Market Test, finale warning, all four stalls close + Grand Final, score freeze: scheduled after the doors close, so watch for the organisers moving the hours, and keep every runner up after 15:00 |

The source is `GET /api/schedule`. The organisers move events (Duels II moved from h13 to h11.65 and round 3 from h18 to h16.65), so always re-read it.
