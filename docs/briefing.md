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
- **Rounds:** each day is a round and rounds are averaged. Friday counts half. A round in progress weighs by the share of its day already played.
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
- Value verified against the API: `book × affinity × [1, 0.25, 0.1][copy]` (#23). The `your_value` of a card in hand is the value of the last copy.
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
- **Some dealers lie.** `POST /api/flags`: a correct flag adds points and a wrong one subtracts. In the audio: *"there might be even some occurrences where you can track bad behavior by the API. If you're correct, you can earn extra points."*

## Duels

One-on-one between teams, under aliases. Each pair plays twice, as seller and as buyer, on the same scenario.

- Each side only sees its own limit. **Closing outside the limit costs points.** No deal, zero.
- The pie shrinks with each round of talk.
- Later sessions negotiate price and days (0–10), with a private `your_days_weight`. A message with a price and no `days` gives `missing_days`.
- The first session is practice and does not score. Details in #4, #5 and #7.

## Our own market

- From level 2: `POST /api/venues` with a refundable bond of 250 P plus 20 P. Fees capped at 10 % and 5 P per card. Team venues operate from +3h.
- Opening our own venue **replaces** the free starter stall.
- Use `mechanism: "board"`: in `auto` the engine matches before the broker does.
- **Market Test:** every ~2h all venues receive the same synthetic book.
  - Matching the auto stall gives half the points; the top-3 average gives the maximum.
  - Key: *"Traders quote away from limits they keep hidden"*. The broker that estimates those limits wins. Details in #11, #12 and #13.
- You cannot trade on your own venue with the team key (`self_venue`).

## Clock and limits

| Day | Open (Madrid) | Tick |
|---|---|---|
| Friday | 19:00–23:00 | 60 s |
| Saturday | 09:00–23:00 | 30 s |
| Sunday | 09:00–15:00 | 15 s |

- **Per tick:** 1 accept per team, 1 message per conversation, 12 new listings (cancellations count too).
- **At once:** 6 open conversations, 30 open offers, 1 conversation per dealer.
- **Requests:** 5 req/s per key with bursts of 20; keyless reads, 60 req/s per IP. Up to 6 SSE streams per key.
- Asking too early gives `429` with `next_tick`: wait, don't retry.
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

## Windows this weekend (game hour since opening)

| Hour | What |
|---|---|
| h2 | Practice duels (don't score): learn the protocol |
| h3 | First Market Test, the only one on Friday. Team venues start operating |
| h4 / 09:00 Sat | Round 2, El Retiro is released. Grant of 1 pack + 150 P (doesn't score) |
| h5–h17 | Market Test every ~2h; the one at h16 is the hard one |
| h6.5 | Duels I (price only) |
| h13 | Duels II (price + days) |
| h18 / 09:00 Sun | Round 3, Chamberí is released. 150 P |
| h20 | Duels III |
| h23 | Abuela closes · Grand Final of duels |
| h24 | Score freezes |

The source is `GET /api/schedule`. Game hours are not clock hours, because the clock pauses.
