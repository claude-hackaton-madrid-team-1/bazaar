# M1 — Market making: our venue + broker, matcher and bench, organic market  (per-task spec)

- Task id: M1 (migrated from GitHub issue(s) #11, #12, #13)
- Priority: P0
- Status: 🔵 #71 approved: the maker opens our 0 bps board venue at game hour 6.5 (~11:30) and brokers it; merges before 06:00. Matcher efficiency on the bench and organic pair breadth are open.
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Score the market half (30 points): a venue open in every Market Test session, a broker that matches at least as well as the free stall, and organic trades between other teams on our venue.

## Acceptance criteria (each MUST be testable)
- [ ] 1. Our venue is open in every Market Test session from Saturday 12:00 (venue check at 11:41).
- [ ] 2. Bench efficiency >= the free auto stall on the simulator's bench (#71: 0.892 vs 0.892); a max-weight matcher only if it beats it.
- [ ] 3. Organic: fee well below El Rastro, announced; trades between distinct team pairs counted per session; anti-wash guard.

## Source (the original issue text, verbatim)

### #11 — [market] Open our own venue + broker loop for the Market Test

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/11 on 2026-10-03 (closed there).

## Context
- The market is **half the score** (30 points).
- Bench: *"Matching as well as the free auto stall earns half; the teams above it share the rest, up to full at the mean of the top three."*
- *"Each session counts a team's best venue open in it, and 0 if none was; the round averages its sessions, so closing a venue never keeps its score."*
- Every team has a **free starter stall with auto-match**, which gives the 0.5 floor. Going beyond that requires our own venue with a broker.
- **Blocker:** *"teams open theirs from level 2"* (see #9).

Bench sessions (game hour):
- Friday: h3, the only one in R1;
- Saturday: h5, 7, 9, 11, 13, 15, h16 (the hard one: 12 traders, firmer and more impatient) and h17;
- Sunday: h19 and 21.

*"Only venues open when the session started are benched."*

## What to do
- As soon as we have L2: `POST /api/venues {name, fee_bps, fee_per_card, rules, description}` with low fees and empty `rules`, and store the broker key.
- Broker loop: `GET /api/broker/book` (with `X-Broker-Key`) **several times per tick**, matcher (#12) and `POST /api/broker/matches {sell, buy, price}`.
- Verify that the starter stall stays open alongside our own venue. Since the best venue counts, the starter would act as insurance.
- Never close the venue. Health check that alerts if it gets suspended (the default slash is 50% of the bond and is not refunded).

## Acceptance criteria
- [ ] Actual book schema documented (fixture).
- [ ] Venue open and broker live **before** every bench session once we have L2.
- [ ] Rejected matches logged with their reason; dry-run mode.
- [ ] Alert if the venue goes suspended/closed.

**Comment by serban-marius:**

**Important corrections (RULES.md):**
- Use `rules: {"mechanism": "board"}`. *"A broker can only act on a board venue: on an auto venue, the free stall included, the engine crosses every pair first."*
- **Opening our own venue replaces the starter stall immediately**, so it doesn't remain as insurance. It's only worth it if our broker matches or beats the auto stall.
- Bond of 250 P + 20 P. Fees capped at 10 % and 5 P per card.
- Team venues start operating at **+3h** (Friday ~22:00).
- The bench book shows up as `bench_offers` in the broker's book. The kit ships `starter_broker.py` as a starting point.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, nothing in `src/` opens a venue or runs a broker, although we have been L2 since tick 98, so the blocker is gone.

- ❌ Missing (all criteria):
  - `Broker` is imported from the SDK (`src/bazaar_agent/sdk.py:18`), but nothing uses it or `open_venue`.
  - Book schema: `tests/fixtures/api/get_api_broker_book.anon.json` is a 401 `bad_key` body, not a real book.
  - The broker loop, the log of rejected matches and a dry-run mode.
  - An alert when the venue is suspended or closed.
- Data:
  - At tick 149 the market side of our score is 0.0 (`mm_points` 0.0, `bench` null, `venue` null).
  - Four rival venues are already open: t06 "Mercado Team 6" (board, 50 bps), t12 "El Duende" (board, 0 bps), t13 "Mercado Trece" (board, 100 bps) and t02 "El Rastro Express" (auto-matching, 0 bps).
  - Two of them charge 0 bps, so lower fees than the house no longer set us apart. Matching quality (#12) has to.
- Related: #55 (open, `bazaar-sim`) simulates venues, the broker key, book / match / announce with `bench_offers`, and bench runs. We can test the broker loop against it while the game is closed.

### #12 — [market] Max-weight matcher + bench simulator + telemetry

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/12 on 2026-10-03 (closed there).

## Context
- Efficiency is realized surplus ÷ feasible surplus of the synthetic book. The price only splits the surplus, it doesn't change it [inferred], so what matters is **which pairs get crossed**.
- *"Share who leave after 1–2 ticks instead of 3–6: slow matching loses them."*
- *"Share whose quotes never relax: a broker waiting for them to cross waits in vain."* Share of firm traders: 0.2 normal, 0.35 in the hard one.

## What to do
- **Matcher:** bipartite graph of sells and buys of the same item with bid ≥ ask, weighted by (bid − ask) plus a bonus for the risk of them leaving (age or expiry). Solve max-weight matching (Hungarian, or sorted greedy if there is a single item). Price at the midpoint. **Never wait for the firm ones**; match right away unless both sides are patient and there is a clearly better candidate.
- **Bench simulator** with the presets normal (10 traders, 16 ticks, firm 0.2, impatient 0.25) and hard (12, 16, 0.35, 0.35), comparing against an auto-stall-style greedy baseline.
- **Telemetry:** read `bench.started` and `bench.finished` from the feed and the stream, and store our efficiency vs. the auto stall per session.

## Acceptance criteria
- [ ] Unit tests with hand-built books.
- [ ] Never worse than greedy over 1000 simulator seeds.
- [ ] <50 ms for 40 traders.
- [ ] Per-session table: our efficiency vs. the auto stall.

**Comment by serban-marius:**

**Official hint (kit README):** *"Traders quote away from limits they keep hidden, so a broker that estimates those limits does better."* The starter broker matches on quoted prices, just like the auto stall. The edge lies in **estimating the hidden limits** and matching pairs that don't yet cross at quoted prices but do at their limits. That has to be the core of the matcher.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, all four acceptance criteria are missing.

- ❌ Missing:
  - The matcher (max-weight, or estimating hidden limits as the kit README hints).
  - A bench simulator with the normal and hard presets, plus the greedy baseline.
  - Bench telemetry: nothing reads `bench.started` / `bench.finished`.
  - The table of our efficiency vs. the auto stall per session.
- Findings:
  - The vendored `vendor/bazaar-kit/starter_broker.py` is unused. Its docstring (line 6) says that crossing by quote earns only what the free stall earns: half the bench points.
  - Our market score is 0.0, and no venue of ours has been benched yet (#11).
- Related: #55 (open, `bazaar-sim`) has a bench of 10 traders with a hidden limit and a shaded quote each, and scores each venue's efficiency. It has no firm or impatient traders yet, so the hard preset (12 traders, 0.35 / 0.35) would still need to be added to it or built locally.

### #13 — [market] Organic market-making: low fees, pair breadth, anti-wash guard

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/13 on 2026-10-03 (closed there).

## Context
- Organic part of the market: *"√ of the pair-capped value created on the team's own venue between other teams"*.
- Only trades **between other teams** on our venue count, with a per-pair cap. **Breadth of distinct pairs** wins, not volume.
- The house (El Rastro) charges 500 bps + 1/card.

## What to do
- Fees well below the house (0–100 bps, no per-card fee) and announce them with `POST /api/broker/announce`.
- Have the broker match complementary offers from other teams on our venue.
- Note: fee changes take time to apply (*"will charge X from T{effective_tick}"*).
- Metrics: `trades_hosted`, distinct pairs and value created.
- **Anti-wash guard:** reject circular or repeated patterns between the same two teams. Suspension means a bond slash and 0 for the sessions missed; the admin's example is literally *"wash trading between Demo 2 and Demo 4"*.

## Acceptance criteria
- [ ] Configurable and announced fee.
- [ ] Dashboard with distinct pairs hosted.
- [ ] Tests for the anti-wash guard.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, nothing is built, and it depends on #11 (no venue yet).

- ❌ Missing:
  - Setting the fee and announcing it: the SDK has `Broker.announce` (`vendor/bazaar-kit/bazaar_sdk.py:334`), but nothing in `src/` uses it.
  - A metric for distinct pairs hosted.
  - The anti-wash guard and its tests.
- Data: competitors already charge 0 bps (t12 "El Duende" and t02 "El Rastro Express"), 50 bps (t06 "Mercado Team 6") and 100 bps (t13 "Mercado Trece"). A low fee no longer sets us apart, so the strategy should focus on the breadth of pairs and the quality of matching (#12) rather than the fee level.
- Related: #55 (open, `bazaar-sim`) simulates `broker/announce` and six synthetic rival teams that list and bid. We can use it to test the anti-wash guard offline.
