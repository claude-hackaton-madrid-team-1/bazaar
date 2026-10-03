# K1 — How The Bazaar is scored + plan to win (reference)  (per-task spec)

- Task id: K1 (migrated from GitHub issue(s) #17)
- Priority: reference
- Status: reference: keep it current with what we learn (findings in .ai/memory.md).
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
One place that says how points are earned, so every task can say which points it moves.

## Acceptance criteria (each MUST be testable)
- [ ] 1. Each N-task in 02-plan.md names the score component it moves (negotiating, market, judges).

## Current scoring (rules audit, Sat 3 Oct; wins over the source text below where they differ)
Full text: `docs/briefing.md` ("Scoring", "Dealers and ladder", "Duels", "Our own market", "Windows this weekend") and
`STRATEGY.md` ("What scores"). The official rules win on any clash: `vendor/bazaar-kit/RULES.md`.
- Holding cards, the album and `collection_value` never score by themselves. A card scores only when it moves: a team
  trade (price minus our `your_value`, into `neg_points`) or a dealer deal (the share of that dealer's own range, buying
  or selling; the opening price scores 0, the dealer's final scores the whole range; best 3 deals per level; restarts
  every round).
- Market-making per round = 22.5 x Market Test bench points + 7.5 x organic (value other teams create on our venue). The
  free stall is bench 0.5. Negotiating per round is about ladder 7.5 + duels 7.5 + team trades 15 (an estimate). Each part
  is capped at the top-3 mean.
- A round starts on the organisers' `round` action in `/api/schedule`, not when the doors open and not each day. Round 2
  started at tick 160; round 3 starts at game hour 16.65 (about Sun 11:34). A new round's weight ramps from 0 to full over
  about 160 ticks. Friday counts half and had 0 market-making for everyone.
- A duel round is one priced message from each side: rounds = min(ours, the rival's), and our result = surplus x
  (1 - decay)^rounds. Duels II are about Sat 20:34 (16 ticks, decay 0.08, price + days); Duels III about Sun 13:34
  (12 ticks, decay 0.1); the Grand Final is at game hour 21.65, scheduled after the doors close.
- The plan checklist and the game hours in the source text below (Duels I h6.5, Duels II h13, Duels III h20, Final h23)
  are history: re-read `/api/schedule`.

## Source (the original issue text, verbatim)

### #17 — [tracker] How The Bazaar is scored + plan to win

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/17 on 2026-10-03 (closed there).

Brainstorming based on the reverse-engineering of https://bazaar.causaprima.ai: `openapi.json`, public endpoints, the front-end JS bundles (including the admin screens) and the kickoff transcript (PR #6).

Legend: **[quote]** = literal string from the UI/API; **[inferred]** = our own deduction, to be verified with a key. There are details where the analyses disagree.

## How to win

**Judging:** ~60% leaderboard + **40% presentation** (kickoff: *"that's the 40% for us"*) → #16.

**Leaderboard score** = Negotiating (0–30) + Market (0–30) [quote: `weights {negotiating:30, market:30}`].
- Each component is normalised to the top-3 mean and weighted per round: Friday 0.5, Saturday 1, Sunday 1 [quote]. Being top-3 in everything scores more than dominating a single thing [inferred].
- **Negotiating** = duels + persona ladder + team-to-team trades [quote]. The internal weights are in `game.yaml` and are not public.
  - **Duels:** share of the pie (0–1) per duel, summed [quote]. No deal means 0 for both. If one side goes below its limit, nobody scores.
  - **Ladder:** best share per persona level, weighted by level [quote]. Share is the portion of the persona's price range that we capture.
  - **Trades:** private-value surplus, capped per trade and per counterparty [quote]. A ring guard scores pairs where one side always takes the whole pie as a 50/50 split.
- **Market** = bench (Market Test) + organic market-making.
  - **Bench:** 0.5 if you match the free auto stall, 1.0 at the top-3 mean, and 0 for sessions with no open venue [quote].
  - **Organic:** √ of the value that **other** teams create on our venue, capped per pair [quote].

**Does not score:** pack luck, grants (the 150P on Saturday and Sunday), gifts from the personas, practice duels, number of trades or fees collected [quote and kickoff]. Holding cards is only useful as ammunition for trades.

## Traps
- Offering or accepting beyond your own limit in a duel → 0 for both.
- Persona strikes (`injection, abuse, spam, false_claim`) → cool-off. Prices are decided by code, so injection doesn't move the price.
- Wash trading on a venue → suspension and bond slash (50% by default, not refunded).
- L4 personas put a worse card in the **structured offer** than in the text. Flagging correctly adds points and flagging wrongly subtracts them.
- There are 6 SSE streams per team/IP **and browser tabs count**.

## Plan

**Friday: learn (weight 0.5, no duels that score)**
- [ ] Blocker: get the team key and the kit (Python SDK) → #2
- [ ] Loop + rate governor + kill switch → #3
- [ ] Practice duels (h2): log the real protocol → #4
- [ ] Negotiate with Abuela, probe her curve, buy packs and open them → #8, #10
- [ ] Find out how to reach L2 (required to open a venue) → #9

**Saturday: execute (weight 1)**
- [ ] Duel policy for Duels I (h6.5) → #5
- [ ] Venue + broker before each bench, which happens every ~2h → #11, #12
- [ ] Organic market-making → #13
- [ ] Trades with many counterparties → #14
- [ ] Duels II (h13): price + days → #7

**Sunday: speed run (15 s ticks)**
- [ ] Duels III (h20) and Final (h23) → #5, #7
- [ ] Scoring and dashboard → #15
- [ ] Presentation → #16

## Issues
| Area | Issue | Prio |
|---|---|---|
| core | #2 Key + API client/SDK + fixtures | P0 |
| core | #3 Loop, SSE, rate governor, kill switch | P0 |
| duels | #4 Logger and protocol in Practice | P0 |
| duels | #5 Policy: floor, anchor, acceptance rule | P0 |
| duels | #7 Days + calibration and profiles | P1 |
| personas | #8 Negotiator that probes the curve | P0 |
| personas | #9 Ladder per round + unlocks/eggs/L2 | P1 |
| safety | #10 Offer inspector + auto-flag | P0 |
| market | #11 Own venue + broker loop | P0 |
| market | #12 Matcher + bench simulator | P1 |
| market | #13 Organic market-making | P1 |
| trading | #14 Valuation + trade scanner | P1 |
| scoring | #15 Score simulator + dashboard | P2 |
| pitch | #16 Presentation + decision log | P0 |

## Open questions
- Internal weights in `game.yaml` (duels/ladder/trades and `bench_weight`).
- Exact rule for reaching L2.
- Response schemas, which are not in `openapi.json`.
- Whether a deal closed at a persona's opening offer gives any share.
- How much a correct flag adds and how much a wrong one subtracts.

**Comment by serban-marius:**

**Official rules found:** `https://bazaar.causaprima.ai/bazaar-kit.zip` → `RULES.md`. Corrections to the tracker:
- Judging = **Negotiating 30 + Market 30 + Judges 40** (*"Your ideas and your craft"*).
- Ladder = **the 3 best deals per level**, not the best one (#9).
- Eggs don't score.
- Closing a duel outside the limit **subtracts**, it doesn't just give zero (#5).
- Opening our own venue **replaces** the starter stall. Use `mechanism: board`, because in `auto` the broker doesn't act (#11).
- The broker's edge lies in estimating the traders' hidden limits (#12).
- Penalties are a % of the round's score. Each day is a round, and the round in progress counts according to the fraction of its day already played.
- `GET /api/me` gives our live score. The public leaderboard is a snapshot.

**Comment by serban-marius:**

**Actual status (tick 0, ~20:20):**
- The key works: we are **Team 1 (`t01`)**.
- Clock `paused: true` at tick 0 even though `doors: open`, so the game hasn't started yet.
- Even so, other teams already have 14 threads open with Abuela.

New issues with what we learned using the API:
- #21: the public feed exposes everyone's offers to the dealers, so Abuela's curve can be spied on.
- #22: map of the 270 starting cards. There are no epics or legendaries; LAV-09 and MAL-09 have a single copy.
- #23: our profile. Affinities LAV 1.6 / SAL 1.3 / MAL 1.1 / CHA 0.9 / RET 0.7 / LAT 0.5; sell LAT and buy LAV.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, this is a tracker, and every linked issue is still open. Each one now has its own audit comment.

- Score at tick 149 (`/me`): 8.34 and falling with no activity (9.24 → 8.73 → 8.34). Rank 15, level 2, 4 deals. The only nonzero component is `ladder_points` 0.058. Duels, market-making and bench are all at 0, and we have no venue.
- Biggest gaps by weight:
  - **Market (30 points) is at 0.** No venue and no broker (#11, #12, #13). Four rivals already run venues.
  - **Duels.** Practice was not scored. The policy still lacks the acceptance rule that accounts for decay, and the days module (#5, #7).
- Fix branches in progress:
  - PR #60 (#5, #7, #24)
  - `fix/shared-ledger-reconnect` (#3)
  - `fix/dealer-ladder-counter` (#8)
- PRs touching these issues:
  - #57 merged after this audit (Jev duel and maker moves).
  - Open: #43 (dashboard), #46 (tracing ADR), #55 (`bazaar-sim`), #58 (evals), #59 (agent runtime + MCP).
- Open questions resolved since the tracker was written:
  - L2 = "a few good deals" with Abuela. We unlocked at tick 98 with "4 deals with abuela".
  - Response schemas: see the comments on #2.
