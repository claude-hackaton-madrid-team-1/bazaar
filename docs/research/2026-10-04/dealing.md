# Saturday review: dealing (Team 1, Sat 3 Oct 2026)

Every deal and near-deal Team 1 made on Saturday, what each one scored, and what to change on Sunday.
Session `sat-dealing`, written Sun 4 Oct 00:00–02:30 Madrid, revised after review (`_sat-review/review-dealing.md`,
14 items; how each was handled is listed at the end). Read-only: SELECT-only Postgres; 4 logged keyed GETs and 1 keyless
GET, all with the game closed; no game writes; no Railway access.

**Redaction.** No figure here lets a reader recover one of our limits, floors, `your_value`s, affinities or cash
balances.
- Prices the public feed shows are given as they are.
- Our `/me` neg_points appear only as rounded aggregates over several deals.
- The board-per-neg_point factor is given only as a range.
- The exact per-deal figures are in `_sat-review/dealing-PRIVATE.md`, outside git. No commit on this branch has ever
  held them (the history was rewritten before any push).

## TL;DR

1. **Trades with teams moved our negotiating score; trades with dealers only lost it.**
   - The 13 board trades with teams added **≈ +190 neg_points** (9 buys ≈ +45, 4 sells ≈ +145).
   - The 16 dealer deals added **0 on the gain side**. The 2 sold below our value (SAL-07 to Pilar, RET-06 to Chato) cost
     **≈ −90** together.
   - Board effect: LAT-10 sold to t12 on rastro (86, tick 1304) gave **+1.98** over the field median, and SAL-07 gave
     **−4.33**.
   - Saturday's board-per-neg_point factor ranged from 0 to ~0.17 across four windows, and 0.03–0.05 in the two clean ones.
     A Sunday deal is worth about 0.6× a Saturday one on the final board (round weighting, §5).
2. **The ladder was the other lever, and every dealer deal that scored there was run by hand.**
   - 3 Pilar sells (L3) gave **+3.35** over the field (≈ +1.1 each), and 3 Pícaros buys (L4) ≈ +0.55–0.70 each.
   - All of them were hand `bazaar dealer sell` runs or hand-approved buys. Round 3 (≈ 12:17 Sunday) resets the ladder,
     and nothing automatic will repeat them.
   - L5 (Banco): 0 deals. L2 (Chato): one buy at 2 % off the opening, plus one sale below value.
3. **A whole channel went unread: offers addressed to us.**
   - 8 teams sent us **127 addressed public offers** on Saturday, and **0 reached any decision of ours**. The reason is
     confirmed in code plus one live check:
     - The taker reads venue boards without a key, and a keyless board does not show addressed offers.
     - `our_open_offers` drops the addressed offers that `/api/me/offers` returns.
     - `--accept-bids` is off on the live taker (0 `accept_bid` decisions all day).
   - We hand-posted **41 addressed asks**; none filled. t02 answered 41 times with counter-bids 3 P under our asks.
4. **Structural waste, not bad haggling.**
   - Only **4 dealer threads** walked with the dealer inside our limit (2 by design).
   - **29 of our 75 dealer threads could never close.** The official-value cap sat below every price Pícaros quoted for
     RET-09/RET-10 (23 threads), and our top sat under Abuela's floor for RET-02 (6 threads).
   - **83 team threads → 0 deals.**
   - The approval gate held LAT-10 off the board for 304 ticks at the same 86 it later sold for.
   - Duels II: the days latch could never arm (replayed), so all 372 of our offers carried `days = 0`. As sellers we left
     **98 P of result** (13 %) that rivals had already offered, and lost duel 5730.
5. **Others won with Pícaros flips, spending Payday, and team trades on the board.**
   - 20 Pícaros → Pilar flips netted +497 P (cash). They score only through the first three ladder slots per level, and
     through team resales.
   - t10 finished #1 at 37.58. Its 11.93 lead over us splits into 5.00 market and 6.93 negotiating.
   - We spent 61 P of the 400 P Payday and finished #9 at 25.65.

## Method and data actually seen

| Source | Window seen | Used for |
|---|---|---|
| Postgres `feed_events` (public feed: every team's dealer threads with prices and texts, offers incl. `to`, settlements) | ticks 0–1445. Saturday = tick ≥ 160, plus tick 159 rows received on Sat 09:28 (every t01 row at tick 159 is; tick 159 also spans Fri 22:59) | threads, offers, settlements, timeline |
| `me_snapshots` (our `/me`, almost every tick) | ticks 159–1445, 1,287 rows | our score components per deal (aggregated here) |
| `leaderboard_snapshots` (all teams, ~every 10 ticks) | ticks 610–1430 only | board effect vs the field median |
| `decisions` / `executions` / `ledger` (our agents and hand CLI) | ticks 159–1445 | limits (not printed), walk reasons, team threads, restarts, hand posts |
| `duels` (our duels, three sessions, snapshot at tick 1431) | 1 = practice (Fri – Sat 09:45, unscored), 2 = Duels I, 3 = Duels II | duel tables, days |
| `outcomes` (our internal ladder-share estimate) | 75 dealer outcomes | cross-check only (see Could not verify) |
| Keyed GETs (`dealing/keyed_requests.log`): `/api/me/threads` 00:21:27; `/api/me/value?card=` RET-03, LAT-08, RET-08 at 00:52:09–12 | our last 50 threads (ticks 1289–1441); 3 card values (end of Saturday) | team-thread replies; valuing inbound asks |
| Keyless `GET /api/venues/rastro/offers` 00:51:34 | the open rastro board after close | are addressed offers on the public board? |
| `git log --first-parent origin/main`, GUARDRAILS.md, `.railway/railway.py`, `src/` | Sat 09:00–23:00 | deploys, rules, code paths |
| `_night/*.md` (MARKET_MOVES, DUELS_PERF, DUELS_II_MONITOR, DEALER_SELL_STRATEGY, KNOWLEDGE_SAT_EVENING, HANDOFF_MM_PROBE, MM_STRATEGY) | as cited | context, not re-derived |

- **Wall time:** each tick's wall time is its first feed event. The clock paused twice (tick 630: 13:25–15:29; tick 1201:
  20:15–20:57), so no linear formula holds.
- **Board effect:** the change in our public `negotiating` minus the field's median change over the same snapshot window
  (`ladder_windows.sql`).
  - The board is relative, and duel points land in the same windows during Duels II.
  - The `/me` deltas (`score_changes.sql`, `settlements_score.sql`: snapshot T against the last snapshot before T) are
    exact. Their conversion to board points is not.
- **Not seen:** Railway logs. Railway is not linked in this worktree or in lets-start, and I did not link it.
- **Files:** queries and scripts are in `docs/research/2026-10-04/dealing/`. `q.py` is SELECT-only, in a READ ONLY
  transaction. The other-teams sub-report is `dealing/other_teams/REPORT.md` (19 queries).

## 1. Per counterparty

All Saturday. "Msgs" are priced messages, ours / theirs.
- **Below our value:** deals priced under our value of the card (from the sign of the `/me` neg_points change).
- **Off the opening:** the discount we got from the dealer's first ask (buys), or the premium over its first bid (sells).
- **Dropped column:** "price vs our limit" (review item 2). Our sell "limit" was the hand CLI's `--floor`, not our value:
  SAL-07 and RET-06 sold above that floor and still below our value. Our buy "limit" was a price cap. Single-deal rows
  would also expose it.

Sources: `dealer_threads.py`, `team_counterparties.sql`, `addressed_in.sql`, `addressed_out.sql`, `score_changes.sql`,
`duels_agg.sql`, `duels_counterparty.sql`, `duels_by_rival.sql`.

### Dealers (75 threads we opened, 16 deals)

| Dealer, side | Threads | Msgs | Deals | Deal rate | Off the opening | Our msgs per deal | Below our value | Cash (P) | Cards | Score produced |
|---|---|---|---|---|---|---|---|---|---|---|
| Abuela (L1), we buy | 13 | 43 / 44 | 3 | 23 % | 18 % | 3.7 | 0 | −71 | +3 | ladder +0.009, +0.010, +0.020 |
| Abuela, we sell | 12 | 49 / 56 | 1 | 8 % | 20 % | 4.0 | 0 | +6 | −1 | ladder +0.003 |
| Chato (L2), we buy | 2 | 11 / 11 | 1 | 50 % | 2 % | 3.0 | 0 | −95 | +1 | ladder +0.001 |
| Chato, we sell | 2 | 15 / 15 | 1 | 50 % | 23 % | 10.0 | **1** (RET-06) | +16 | −1 | ladder 0; a small neg_points loss |
| Pilar (L3), we sell | 9 | 56 / 56 | 5 | 56 % | 19 % | 6.0 | **1** (SAL-07, an only copy) | +157 | −5 | ladder +0.050, +0.045, +0.044 (**board +3.35**); SAL-07 **board −4.33** |
| Pícaros (L4), we buy | 33 | 64 / 63 | 4 | 12 % | 17 % | 3.2 | 0 | −241 | +4 | ladder +0.049, +0.046, +0.047 (board +0.55 to +0.70 each) |
| Pícaros, we sell | 4 | 21 / 20 | 1 | 25 % | 25 % | 4.0 | 0 | +5 | −1 | ladder +0.012 |
| Banco (L5) | 0 | – | 0 | – | – | – | – | 0 | 0 | – |
| **All dealers** | **75** | | **16** | **21 %** | 18 % (field ≈ 15 %) | | **2** | **−223** | **+8 / −8** | ladder 0 → 0.336; neg_points ≈ −90 (the 2 below-value sales) |

- **Deal rate:** the field converts 37 % of dealer threads (other_teams §2). 29 of our 75 could never close (§3.4); on the
  46 others our rate is 35 %.
- **Prices:** good once we deal. Our Pilar sells got +18.7 % over her opening, the best of any team with more than 4 Pilar
  deals. We step 1 P, the smallest step in the field.
- **Dealer deals never added neg_points:** 14 deals at or above our value moved it by 0, and the 2 below value subtracted.
  RULES.md:118 counts "the value you gained in trades with other teams". Our losses with dealers counted anyway.
- **Ladder increments by level:** L1 0.003–0.020; L2 0.001, then 0; L3 0.044–0.050; L4 0.012–0.049. The 4th and 5th deals
  at L3 and L4 added 0 ("best three per level").
- **MAL-09** (Pícaros, 61, thread 1823) was bought under a human approval: no taker limit exists for it.
- **Unlocks:**
  - L4 at tick 761 (16:34), earned by our 3 Pilar sells, 120 ticks before Pícaros opened to all (881).
  - L5 at 971 (18:20), 120 ticks before 1091.
  - L3 only when it opened to all (502): early L3 needed 3 negotiated Chato deals, and we had 1.

### Teams: threads, addressed offers, board trades

| Team | Team threads we opened | Swap offers in them | Addressed offers we sent them | Addressed offers they sent us | Board buys | Board sells | Cash before fees (P) | Fees we paid (P) |
|---|---|---|---|---|---|---|---|---|
| t02 | 5 | 5 | 14 | **41** (bids for MAL-04, MAL-06) | 3 (SAL-10 72, SAL-02 9, SAL-07 23) | 0 | −104 | 10 |
| t03 | 0 | 0 | 0 | 4 (3 bids, 1 ask) + 1 thread (silver pack 124) | 0 | 0 | 0 | 0 |
| t04 | 13 | 13 | 7 | 1 (RET-03 12) + 1 thread (RET-03 for our LAV-06) | 1 (MAL-06 20) | 0 | −20 | 2 |
| t05 | 5 | 5 | 1 | **41** (36 asks: MAL-07/06/08/02, LAV-04) + 1 thread (tip) | 0 | 0 | 0 | 0 |
| t06 | 10 | 11 | 1 | 0 | 1 (MAL-02 3) | 1 (SAL-10 76) | +73 | 2 |
| t07 | 10 | 10 | 6 | 0 | 1 (RET-04 6, v02) | 0 | −6 | 0 |
| t08 | 6 | 6 | 3 | 21 (8 bids, 1 ask, 12 swaps) | 0 | 0 | 0 | 0 |
| t09 | 6 | 6 | 3 | 0 | 0 | 0 | 0 | 0 |
| t10 | 12 | 12 | 0 | 8 (7 asks) + 1 thread (ad) | 1 (MAL-07 14, v10) | 0 | −14 | 0 |
| t12 | 0 | 0 | 0 | 7 (5 asks, 2 bids) | 0 | 1 (LAT-10 86) | +86 | 0 |
| t13 | 0 | 0 | 0 | 0 + 2 threads (venue ads) | 0 | 0 | 0 | 0 |
| t14 | 0 | 0 | 0 | 0 | 0 | 1 (LAT-08 25) | +25 | 0 |
| t15 | 0 | 0 | 4 | 0 | 1 (SAL-05 9) | 0 | −9 | 2 |
| t16 | 3 | 3 | 1 | 0 | 0 | 1 (LAT-09 68) | +68 | 0 |
| t17 | 7 | 7 | 0 | 4 (bids) | 0 | 0 | 0 | 0 |
| t18 | 6 | 6 | 1 | 0 | 1 (MAL-04 8) | 0 | −8 | 2 |
| **All** | **83** | **84** | **41** | **127** (+6 threads in ticks 1289–1441) | **9** | **4** | **+91** | **18** → **+73 after fees** |

- **Board fills: 13.**
  - 9 were taker accepts of public asks (8 before tick 330), on rastro, v10 and v02.
  - 4 were maker asks filled, all on rastro.
- **Fees:** our side paid the fee on all 7 rastro buys (18 P; v10 and v02 charged 0). We got the full price on our 4
  sells. On every buy we were also the accepting side, so buyer-pays and taker-pays cannot be told apart.
- **Score:** buys ≈ +45 neg_points, sells ≈ +145; LAT-10 board **+1.98**.
  - The morning trades predate the leaderboard capture.
  - Two morning sells while we led neg_points moved the board +0.67 and 0.00 (MARKET_MOVES §5).
  - Two morning buys moved the raw board +3.39 with no field baseline, and drift added +2.07 more with no deal by tick 370.
- **Team threads: 0 deals.**
  - 83 threads, 84 proposals. 63 ended "no deal 3 ticks after our last proposal" (62 closed, 1 close failed), and 36 cancels
    failed with `offer_not_open`.
  - In the 25 threads we can read (ticks 1289–1441), no team answered inside the thread.
  - In 56 of 77 proposals the desk's own note scored the split as more for the partner than for us.
- **Addressed offers we sent: 41, 0 filled.**
  - All 41 were hand `bazaar sell list --to` posts: 41 of 41 are booked `hands-off:<offer id>` with ledger source `sell`;
    the maker's 166 listings are all public.
  - They include the 18:56 approvals: LAV-04 → t07 and LAT-02 → t04, re-posted at higher prices from tick 1047 on.
  - **t02 did answer, on the board**: from tick 1027 it re-posted addressed bids every ~10 ticks until 1369. It bid 5 for
    MAL-04 against our ask of 8, and 11 for MAL-06 against our 14–20. No process of ours ever saw those bids (§3.3).
- **Addressed offers we received: 127, 0 evaluated** (§3.3).

### Duels

| Session | Duels | Msgs (ours / rival) | Deals | Deal rate | Result as % of our limit (deals) | Rounds per deal | Rounds paid | Result lost to decay | duel_points | Board |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 Practice (Fri – Sat 09:45, unscored) | 34 | 188 / 116 | 15 | 44 % | 26.7 % | 4.67 | 70 | 108.8 P | – | – |
| 2 Duels I (11:59–15:39, paused 13:25–15:29; decay 0.06) | 34 | 212 / 164 | 27 | 79 % | 20.5 % | 1.96 | 53 | 50.5 P | **+15.02** | +2.0 raw (no field baseline before tick 610) |
| 3 Duels II (21:16–22:52, decay 0.08, price + days) | 68 | 384 / 298 | 57 | 84 % | 27.8 % | 1.54 | 88 | 134.4 P | **+27.35** | **≈ +0.2 over the field** (−0.86 for ticks 1240–1300, +1.04 for 1310–1430) |

Duels II per rival (`duels_by_rival.sql`): where the rounds, and so the decay, were paid.

| Rival | Duels | Deals | Rounds per duel | Result lost to decay |
|---|---|---|---|---|
| Plata | 10 | 8 | 2.40 | 30.1 P |
| Sol | 5 | 3 | 2.40 | 8.2 P |
| Luna | 3 | 3 | 2.33 | 9.9 P |
| Oro | 11 | 8 | 1.73 | 23.0 P |
| Rojo | 7 | 6 | 1.43 | 6.6 P |
| Noche | 7 | 6 | 1.14 | 8.4 P |
| Azul | 19 | 17 | 1.11 | 36.6 P |
| Verde | 6 | 6 | 0.83 | 11.7 P |

- **Duels I:** detail in `_night/DUELS_PERF.md`, not re-derived. 7 no-deals, all with silent rivals; the endgame defence
  was worth ≈ +1.4.
- **Duels II by role:**
  - Sellers: 28 of 34 deals, 1.36 rounds, 38.6 % of the limit.
  - Buyers: 29 of 34 deals, 1.72 rounds, 17.3 %.
  - 16 deals closed with 0 rounds.
- **Duels II no-deals: 11.** Nine had silent rivals. In 5730 and 5731 the rival had priced (§3.5).
- **Accept slot:** 5 accepts queued behind another duel's accept that tick, and no deal was lost to it. 33 duel offers
  failed (`refused network`).

## 2. Timeline of the day against deal flow

| Time | Tick | Event | Deal flow |
|---|---|---|---|
| 09:28 | 159 | Doors, 30 s ticks | the taker accepted 6 public asks at ticks 160–174 (rastro); Abuela LAV-08 |
| 09:29–09:31 | 160–165 | Round 2 starts (tick 160, when the clock unpaused); grant 150 P + RET pack | |
| 09:45 | 192 | Practice duels finish | |
| 09:49 | 201 | Market Test 1 | |
| 10:00–10:48 | | Merges #123 (team threads), #169 (`cash_floor` 270), #170 (duel v2), #171 (venue asap), #174 (duel endgame) | 260 board accepts rejected 10:00–11:00 (the quiet topic) |
| 10:16 | 254 | Banco persona v2 (before Banco opened) | |
| 10:20 | 262 | Pilar activates (t13 only). Our venue v19 opens; from here `cash_floor` is cut step by step (GUARDRAILS:18) | |
| 10:31 | 284 | Server restart (announced) | |
| 10:45–11:22 | 311–386 | | MAL-07, SAL-07, MAL-06 board buys; SAL-10 sold to t06 (76), LAT-09 to t16 (68) |
| 11:12 | | #177 `official_value_margin` (buys capped at official value) | |
| 11:45–11:47 | | #183 maker dealer-sell, #186 `max_price_rare` 95 | 11:51 Chato SAL-09 buy at 95 (tick 443) |
| 11:50 | 441 | Market Test 2 | |
| 11:59 | 459 | **Duels I** (34 duels, decay 0.06) | 19 duel accepts |
| 12:01 | 463 | Chato persona v2 (v3 at 13:01, tick 583) | |
| 12:20 | 502 | Pilar open to all (we reach L3) | |
| 12:23 / 12:48 | 509–560 | #190 dealer-sell on, #200 off | maker: 7 Abuela SAL-01 sell threads (732–782), 0 deals (her final always just under our floor) |
| 13:24–15:29 | 630 | **Clock paused 2 h 04** | |
| 15:39 | 651 | Duels I finished | |
| 15:46 | | #209 human approval (≥ 60 P) | Pilar SAL-10: 4 walks (threads 880, 889, 899, 914; ticks 645–672) |
| 15:54 | 681 | Market Test 3 | |
| 16:13–16:22 | 718–737 | | **3 Pilar sells (L3): board +3.35** |
| 16:34 | 761 | **L4 Pícaros for us** (3 Pilar deals) | |
| 16:39–17:26 | 771–864 | #216 spend limits loosened, #219 `cash_floor` 5 (17:22) | **3 Pícaros buys (L4)**: LAV-09 58, MAL-10 59, LAV-10 63. MAL-09 at 63 walked at 781 (no approval yet) |
| 17:34 | 881 | Pícaros open to all | |
| 17:41 | 894 | | RET-06 to Chato at 16, below our value |
| 17:54 | 921 | Market Test 4 | |
| 18:03 | 939 | **Salamanca fever** (Pilar +25 % on SAL) until 20:04 | |
| 18:08 | 948 | | **SAL-07 (our only copy) to Pilar at 29: board −4.33, rank 5 → 12** (hand `dealer sell`) |
| 18:13 | 958 | | SAL-07 bought back from Abuela at 21 (page back, neg_points not) |
| 18:20 | 971 | **L5 Banco for us** (3 Pilar deals) | 0 Banco threads all day |
| 18:24 | 979 | Abuela persona v2 ("pays more for uncommons until teatime", news 18:06); v3 at 21:06 (tick 1219) | |
| 18:26–19:04 | | 6 merges during the "freeze" (KNOWLEDGE §1.7) | |
| 18:51–19:52 | 1024–1145 | | first hand-posted addressed asks (41 by tick 1311); t02 counter-bids from 1027 |
| 18:56 | 1044 | Approvals: LAV-04 → t07, LAT-02 → t04 | posted by hand as addressed asks (1047–1115); 0 filled |
| 19:15–19:30 | 1081–1099 | | LAV-05 → Abuela 6, MAL-06 → Pilar 19, MAL-02 → Pícaros 5 |
| 19:28–19:56 | | #228 rival blocklist, #227 move-impact guard, #232 `human_approval_above` 250 | |
| 19:55 | 1161 | Market Test 5 | |
| 20:15–20:57 | 1201 | **Pause; Payday +400 P to every team** (20:37) | |
| 21:03 | 1212 | | MAL-09 from Pícaros at 61 (MAL page complete) |
| 21:03–23:00 | 1205–1435 | | **23 Pícaros RET threads + 6 Abuela RET-02 threads, 0 deals** (§3.4) |
| 21:08–21:12 | | #215 duel silent floor, #240 LAT-10 sell exception | |
| 21:16 | 1239 | **Duels II** (68 duels, decay 0.08, price + days) | 28 duel accepts; days latch never armed |
| 21:49 | 1304 | | **LAT-10 to t12 at 86 on rastro: board +1.98** |
| 22:09 | | #250 trade with everyone | |
| 22:37 | 1401 | Market Test 6 | |
| 22:52 | 1431 | Duels II finished | |
| 22:59 | 1445 | Close: rank 9, 25.65 (negotiating 18.15, market 7.50) | |

Sources: `feed_events` (clock, round, level, schedule, persona, bench, duels and settlement events),
`git log --first-parent origin/main`, `human_approvals`, `decisions`, `ledger`.

### Flow per wall hour (`hourly_flow.sql`)

| Hour | Ticks | Dealer deals | Team deals | Dealer threads opened | Team threads opened | Duel accepts | Taker restarts |
|---|---|---|---|---|---|---|---|
| 09 | 160–221 | 1 | 6 | 1 | 0 | 4 (practice) | 0 |
| 10 | 222–340 | 0 | 3 | 2 | 0 | 0 | 5 |
| 11 | 341–460 | 2 | 2 | 2 | 0 | 0 | 9 |
| 12 | 461–580 | 0 | 0 | 7 | 0 | 16 | 7 |
| 13 | 581–630 | 0 | 0 | 0 | 0 | 3 | 5 |
| 15 | 631–691 | 0 | 0 | 6 | 0 | 0 | 1 |
| 16 | 692–811 | 5 | 0 | 11 | 0 | 0 | 4 |
| 17 | 812–930 | 2 | 1 | 4 | 23 | 0 | 3 |
| 18 | 931–1050 | 2 | 0 | 2 | 9 | 0 | 2 |
| 19 | 1051–1170 | 3 | 0 | 5 | 14 | 0 | 7 |
| 20 | 1171–1205 | 0 | 0 | 4 | 2 | 0 | 1 |
| 21 | 1206–1325 | 1 | 1 | 20 | 19 | 15 | 3 |
| 22 | 1326–1445 | 0 | 0 | 10 | 16 | 13 | 4 |

(Thread 316, opened at tick 159 = 09:28, is outside the 09 row.)

- **98 merges to main** landed between 09:00 and 23:00 (47 of them before 12:00), and the taker restarted **51 times**
  (`process_started`).
  - Each restart rebuilds in-process state: thread ownership ("busy: driven by another process", 3 skips), Pilar's
    floors, and the duel runner's days latch.
- **Deal flow follows unlocks and hand runs, not the agents' activity.**
  - 12 of the 16 dealer deals came in 16:00–19:30, after L3 opened and the hand sells started.
  - The busiest thread hours (21–22: 30 dealer and 35 team threads) produced 1 dealer deal and 1 team deal.

## 3. Where we left points on the table (ranked by measured or estimated cost)

### 3.1 Selling an only copy to a dealer below value: −4.33 board (measured)
- **The sale:** SAL-07 went to Pilar at 29 at tick 948 (thread 1362, hand `bazaar dealer sell`) and moved the board −4.33
  against the field. Rank went 5 → 12. RET-06 to Chato at 16 (thread 1272, tick 894) was a small loss of the same kind.
- **Why the hand run allowed it:** the CLI's `--floor` sat above the price paid and below our value, so the sale cleared
  its own floor (review item 2).
- **The buy-back** from Abuela at 21 restored the page and the ladder (+0.020), but no neg_points: dealer gains never
  count.
- **Since on main:** `protect_page_sets` covers every set (GUARDRAILS:47), and #227 adds a move-impact guard. Whether the
  hand CLI path reads both is not re-tested here.

### 3.2 Cash that never became deals: the Payday grant (opportunity, not measured for us)
- **The gap:** we spent 61 P of the 400 P on buys. t10 spent 554 P, t04 551 P and t12 475 P (other_teams §4.3).
- **What kept it idle:**
  - The official-value cap (`official_value_margin`, GUARDRAILS:27) and §3.4.
  - No `max_price_epic` until ~tick 1370 (GUARDRAILS:164).
  - `off_page_min_surplus`, Marius's hard rule (GUARDRAILS:165).
  - No code path to Banco.
  - "Only deals score, never cash you hold" (Payday announcement, tick 1201).
- **What it bought elsewhere:**
  - Flips (Pícaros → Pilar +497 P over 20 trips) score only through ladder slots and team resales. A Pícaros rare buy
    averaged +0.44 board (n=33).
  - A rare sold to a team on rastro averaged +1.26 (n=6).

### 3.3 Offers addressed to us: 127 received, 0 evaluated (measured; small, but free to fix)
- **What arrived:** 8 teams sent us 127 addressed public offers (ticks 177–1415; `addressed_in.sql`): t02 41 bids,
  t05 41 (36 asks), t08 21, t10 8, t12 7, t03 4, t17 4, t04 1.
  - No decision row names any of them.
  - The taker's `accept_ask` decisions covered 90 offer ids on Saturday, all public.
  - The reviewer's text search for 8 inbound ids (11099, 12872, 15532, 15718, 16001, 16782, 18095, 18135) in `decisions`
    and `executions` found nothing.
- **Mechanism, confirmed in code plus one live check:**
  1. The taker scans each venue with the **keyless** client: `taker.py:1007` `board_offers(self.public.board(venue_id), …)`,
     where `self.public` is `PublicBazaar`, "without the X-Team-Key header" (`sdk.py:51`).
     - A keyless board does not show addressed offers. At 00:51 the keyless rastro board listed 34 open offers, 0
       addressed. The feed shows addressed offer 19999 (t10 → t08, expires 1446) still open on rastro, and it was absent
       (n=1, the game closed).
     - `market.board_offers` would accept `to == us`; it never receives them.
  2. `/api/me/offers` does return offers addressed to us. `market.our_open_offers` skips them on purpose ("unless another
     team addressed it to us"), and nothing else reads them.
  3. Selling into bids is off on the live taker: `accept_bids: bool = False` (`taker.py:176`). The Railway start command is
     `bazaar agent taker` without `--accept-bids` (`.railway/railway.py:283`), and Saturday has 0 `accept_bid`
     decisions. So no process of ours could take a team's bid, addressed or public.
- **Value lost, honestly small:**
  - **Bids:** the reviewer's check against our values at the time finds 4 (team, card) pairs above value after the fee:
    t02 MAL-04, t02 MAL-06, t08 SAL-01, t03 SAL-10. Together ≈ 30 neg_points, ≈ +0.5–1.5 board at Saturday's factor.
    Most of it is t03's SAL-10 bid (id 11099, tick 735), two ticks before we sold SAL-10 to Pilar for an L3 ladder slot,
    probably the better use.
  - **Asks:** most of the 36 t05 asks were for duplicates we already held, or for MAL-06/MAL-07 that we bought cheaper on
    the board (MAL-07 14 on v10, MAL-06 20). The 5 asks for page cards we held 0 copies of (RET-03 ×3 from t10/t04,
    LAT-08 from t12, RET-08 from t08) were **all priced above our value** of those cards (3 logged `GET /api/me/value`,
    end of Saturday). Accepting them would have cost neg_points.
- **Why it still matters for Sunday:** addressed offers work for others. 2,203 of 7,670 Saturday listings were addressed,
  and ~27 of 131 team settlements match one (reviewer's heuristic join). Teams that want our cards bid to us directly
  (t02 did so 41 times). We had no reader for them.

### 3.4 Threads that could never close: 29 of 75 dealer threads (measured)
- **Pícaros RET-09/RET-10, 23 threads** (1811, 1822, 1824, 1830, 1834, 1839, 1843, 1936, 1945, 1948, 1952, 1959, 1961,
  1965, 1975, 1982, 1986, 2185, 2191, 2198, 2203, 2206, 2209; ticks 1205–1435):
  - The taker planned a ladder top above Pícaros' fills. The `official_value_margin` guard then denied every bid above our
    official value, which sat below every price Pícaros quoted us for those cards.
  - So each thread walked after 1–3 bids ("guardrail: denied: price … > official value …", 17 walks).
  - The cap binds only at send time: "Our model still ranks; this only caps" (GUARDRAILS:27). The learner even recalled
    "no deal although fills reach [our top]".
  - Cost: 3 `persona_quota` refusals (ticks 1234–1362), "picaros persona quota with us until T1401", and 41 of our bids
    against 42 Pícaros prices, all patience spent for nothing.
- **Abuela RET-02, 6 threads** (1933, 1950, 2056, 2091, 2186, 2199; ticks 1281–1426): our top sat under her floor every
  time ("no higher bid left inside our limit"). The repeats cost her patience ("Dealers remember how they were treated",
  RULES.md).

### 3.5 Duels II days: ≈ 98 P of result left, one deal lost (measured result, estimated points)
- **The rule** is stated in both payload texts:
  - Seller: "each delivery day adds this much cash to your side".
  - Buyer: "each delivery day costs you this much cash".
  - `your_days_weight` is positive for both roles. Duel 5632's result matches (price − our limit + weight × days) ×
    0.92^rounds to 0.1 P, with the rival's 5 days counted for us.
- **Why we never used it:** `duel_days_auto` = true (GUARDRAILS:81), but the latch never armed. `days_latch_replay.py`
  runs the code's own functions on all 68 real payloads.
  - Text evidence: 34 seller "unknown", 34 buyer "cost". Scored evidence: 2 seller "signed", 2 buyer "cost".
  - Feeding `DaysSwitch.observe` the payloads in the order they appeared: the first buyer duel (5619, tick 1239) set
    "cost", which forces `duel_days_signed` off (`effective_rules`). The first seller deal scored with days (5744) turned
    it into "conflict", which is permanent.
  - Cause: one global sign assumed for a role-dependent rule.
  - Result: worst case (every day costs) in every duel; 372 of 372 offers sent `days = 0`.
- **Cost:**
  - In the 28 seller deals, rival buyers had already offered days worth **98.0 P** of result after decay (13 % of our
    seller result; `duels2_days.sql`). This assumes each rival would accept its own days at the final price.
  - At 0.020 duel points per P of result, that is ≈ +2.0 duel points (+7 %).
  - Duel **5730** (seller): the rival's last offer was above our limit once its 5 days count for us. Under worst case it
    read below, we held, and it closed as a no-deal.
  - Duel 5731 (buyer) was outside our limit once its days cost counts, so holding there was correct.
- **Board value:** relative and small. Duels II as a whole landed at par with the field.

### 3.6 The team desk: 83 threads, 0 deals (measured)
- **Activity:**
  - 84 proposals, each a card for a card plus a cash leg. In 56 of 77, the desk's note scored the split as more for the
    partner.
  - 63 ended by the 3-tick rule.
  - 231 more openings were refused by the Jev gate (quiet topic).
  - None of the 25 readable threads got a reply inside the thread.
- **Inbound proposals** (t03, t04) attach a structured offer: "accept it as is and it settles next tick".
- **Cost:** request budget, plus 2 of our 6 conversation slots (`team_threads_max_open`, GUARDRAILS:106).

### 3.7 Inside-limit walks and other near-deals (small, listed in full)
- **Dealer threads with the dealer inside our limit and no deal: 4** (`dealer_threads.py`, `inside_walk`).
  - 324: Abuela's ask reached our max with 0 surplus, then the thread idled out. We bought SAL-07 cheaper on the board at
    tick 320.
  - 1093: Pícaros' final 63 for MAL-09, walked because the human approval did not exist yet. We bought it at 61 in 1823,
    431 ticks later.
  - 1015 and 1168: the final equalled the dealer's opening bid. By design: an opening-price deal does not count
    (RULES.md:35).
- **Pilar SAL-10, 4 walks** (threads 880, 889, 899, 914; ticks 645–672): her finals were 71, 69, 65 and 68. The approval
  was lowered twice and the sale closed at 70 (thread 1019, tick 737). The walks cost ~90 ticks and her patience
  ("tercera vez que me llama 'amigo'", MARKET_MOVES TL;DR 5).
- **Approval gate:** LAT-10 was listed at 86 from tick 973. `human_approval_above` (60 then) refused it 59 times until the
  approval (~tick 1279), and it sold at 86 at tick 1304.
- **Maker sells to Abuela** (threads 732–782, ticks 509–560): her final sat just under our floor each time. The cost was
  one L1 ladder slot, and the threads blocked the taker's Abuela slot (GUARDRAILS:120).
- **Board counter-bids from t02** (41, §1 Teams): 3 P under our addressed asks; never read (§3.3).

## 4. What other teams did that worked, and what each kind of deal scored

The full sub-report is `dealing/other_teams/REPORT.md`. The main points, with ours beside them:

| Kind of deal | Field: mean board excess (n) | Ours (window) | Lesson |
|---|---|---|---|
| Rare bought from a team on rastro | **+1.64** (6) | morning buys predate the capture | buy from teams below our value |
| Rare sold to a team on rastro | **+1.26** (6) | **LAT-10 +1.98** | sell to teams above our value |
| Uncommon, team board trade | +0.28 to +0.31 (26) | – | |
| Rare bought from Pícaros | **+0.44** (33) | +0.55 to +0.70 (L4 ladder, 3 deals) | ladder, and resale to teams |
| Pilar sell, uncommon / rare | +0.23 (37) / +0.27 (16) | **≈ +1.1 each** for our first 3 (L3 ladder) | the first 3 deals per level carry the ladder |
| Abuela common sell | 0.00 (23) | – | worthless |
| Epic sold to Banco | **−2.60** (3) | – | below value: a loss |
| Sale below value to a dealer | – | **SAL-07 −4.33** | never |

- **Addressed offers:**
  - 2,203 of 7,670 Saturday listings were addressed, and ~27 of 131 team settlements match an addressed offer between the
    same two teams at the same price (the reviewer's heuristic join; settlements carry no offer id).
  - t02, t05 and t08 used them to bid and ask to us directly. t05 also used team threads to broker other teams' offers
    ("post an open bid on v10 at ~23 and it crosses").
- **Flips:** buy a rare or epic from Pícaros (rares 48–67, epics 128–167) and resell to Pilar (rares 50–87, epics
  140–199) or to a team on rastro.
  - 20 Pícaros → Pilar trips netted +497 P, and 8 → rastro +277 P.
  - The cash does not score. A flip scores through the first three ladder slots per level, and through a resale to a team
    above the seller's value.
- **t10 (#1, 37.58):**
  - Its 11.93 lead over us is **5.00 market** (12.50 vs 7.50) **and 6.93 negotiating**.
  - Its +7.94 negotiating from tick 610 came with 6 flips, 11 Pilar sells, 10 Pícaros buys and post-Payday buys. It is not
    decomposed further (the board shows only the total).
- **t18 (#2) and t05 (#3)** built their scores before the leaderboard capture (tick 610). From 610 they gained only +2.01
  and +1.87, so they cannot be analysed here. t05 was our second-largest sender of addressed offers (41).
- **Salamanca fever** (ticks 939–1178): Pilar paid 78.2 on average for SAL rares (n=15), against 68.5 before.
- **Volume does not score:** t13 made 46 dealer deals and ended −1.71; t03 made 15 and gained +6.73.
- **Negotiation style:**
  - t10 opened Pilar at 1.5× her bid and came down 3 P per tick (61 → 77).
  - t12 anchored Pícaros at 55 % of the ask, paused 23 ticks, and got a final of 48.
  - Chato moves in proportion ("You moved four, I moved two"). Our 1-P steps get good prices but slow closes.

## 5. Sunday recommendations, ranked by expected points

**Calibration.**
- **Factor:** Saturday's board-per-neg_point factor ranged from 0 to ~0.17 across four windows, and 0.03–0.05 in the two
  clean ones (one gain, one loss). The first 3 deals at L3 gave about +1.1 board each.
- **Round weighting** (RULES.md:124–125, inferred, not measured): rounds are averaged and Friday counts half.
  - Saturday's end-of-day board weights round 2 by 1/1.5. The final board weights round 2 and round 3 by 1/2.5 each.
  - So a deal measured on Saturday's board is worth **≈ 0.6×** that on the final board. The column below applies it.
- **Resets:** each round resets `neg_points`, `duel_points` and `ladder_points` (seen at tick 160: 0.058 → 0).
- **Round 3 timing:** it starts at h16.65 per `/api/schedule`, **≈ 12:17** by the latest night estimate
  (HANDOFF_MM_PROBE:6, MM_STRATEGY:80; ~43 min later than the older docs). Until then, Sunday play still counts for round 2.
  Verify with `/api/clock` and `/api/schedule` at 09:00.
- **Request budget:** at 15 s ticks Sunday's budget is conditional: 4.73 req/s at every loop's ceiling, with no MCP/desk
  loops and no extra `dealer buy` (MORNING §1 and §3 item 7). Hand runs belong between Market Tests (≈ 10:17, 10:38, 12:38, 14:38)
  and outside Duels III (≈ 14:17).

| # | Change | Concrete change (file / parameter) | Expected final-board points | Risk, incl. requests |
|---|---|---|---|---|
| 1 | **Trade card value with teams on the board, both ways**: sell spares and cards worth little to us at the tape price, buy below our value | (a) Maker: list every spare and every `protect_page_exceptions` card on rastro at the tape price, never to a dealer below value (`STRATEGY.md` `sell_min_surplus`, GUARDRAILS:48). (b) **`buyer_rank_enabled` = true** (GUARDRAILS:137): the maker addresses each ask to the best buyer and re-posts it for anyone after `buyer_rank_fallback_ticks` 6. Hand approvals with a buyer keep using `bazaar sell list --to` (41 posts Saturday). (c) Keep `human_approval_above` 250 (GUARDRAILS:147) | **+1.2 to +3** in round 3 (2–4 rare-size trades; Saturday +1.26 to +1.98 each, × 0.6) | Gains may cap near the field's top neg_points (Saturday morning: +0.67, then 0.00, while we led): read `/me` and the board after each trade. Addressed asks went 41 for 0 on Saturday; the fallback keeps them public. Feeding: no side payments (RULES.md:131). Requests: within the maker's 12 listings/tick, no extra reads |
| 2 | **Fill the dealer ladder deliberately.** Before ≈ 12:17 (round 2): one negotiated Chato deal (L2 holds a 2 %-off buy and a below-value sale) and better-share L1 deals. From ≈ 12:17: 3 negotiated deals per level, L3 first | Hand checklist with `bazaar dealer sell` (it ran every scoring L3 deal Saturday), or `dealer_sell_enabled` = true (GUARDRAILS:120) with the floor at value + `dealer_sell_min_surplus`, **never a hand `--floor` below value**. Taker ladder probes: `ladder_probe_min_share` (GUARDRAILS:134) | **+1.8 to +3** in round 3 (Saturday L3 +3.35, L4 ≈ +1.8 for 3, × 0.6); **+0.3 to +0.6** for the Chato slot before ≈ 12:17 | Dealer patience and quota (Pilar and Chato 6/h); slot clash with the taker (GUARDRAILS:126–129). **L5 only by selling an epic worth less to us than Banco's bid** (`GET /api/me/value`), else skip: t16 −5.56, t06 −2.40. Requests: a hand thread is 1 msg/tick; run it between benches and outside Duels III |
| 3 | **Read offers addressed to us, and let the taker sell into bids** | (a) Taker: add the offers `/api/me/offers` returns with `to == us` to its board candidates (`agents/taker.py` `_board_offers`; `market.our_open_offers` already sees and skips them), or read boards with the team client. (b) `--accept-bids` on bazaar-taker (`.railway/railway.py:283` start command; `TakerConfig.accept_bids`): sells into a bid only when it beats our value by `sell_min_surplus` and every sell guard holds (`protect_page_sets`, move impact) | **+0.3 to +1** (Saturday's 4 above-value bid pairs ≈ 30 neg_points, × 0.03–0.05, × 0.6); more if teams address more to us on Sunday | New code (a) and a start-command change (b): merge outside benches and duel sessions, and test that `protect_page_sets` and #227 bind on the bid path. Requests: (a) is one `GET /api/me/offers` per tick unless the snapshot already holds it |
| 4 | **Clamp dealer ladder tops by the official-value cap before opening a thread**, and skip a card whose clamped top is under the dealer's lowest fill seen | Taker planning (`agents/taker.py` ladder plan, `agents/dealer_plan.py`): top = min(top, official value − `official_value_margin`); skip when top < the learner's lowest fill for that card or class | 0 directly; frees Pícaros' 6 deals/hour and patience (23 dead threads Saturday) | None. It **saves** requests (≈ 2 msgs per dead thread) |
| 5 | **Duels III: a role-aware days sign, per duel** | `agents/duel_days.py` `evidence()`: "adds … to your side" = gain for this duel, "costs you" = cost; decide per duel instead of a global latch. Seller: open with high days and accept the rival's days. Buyer: days at 0 unless the price compensates. Keep the worst case for unrecognised text | +1 to +2 duel points if Duels III has days (≈ +7 % on Duels II); ≈ +0.1 to +0.3 final board (relative, × 0.6) | A changed text falls back to the worst case. Merge before ≈ 14:17 and outside benches (a redeploy re-arms state). No extra requests |
| 6 | **Team desk off for Sunday; keep answering inbound offers** | `team_threads_enabled` = false (GUARDRAILS:105), or `team_threads_max_open` 0; inbound offers go through rec 3 | 0 directly; saves requests at 15 s ticks and returns 2 conversation slots to the ladder | We lose a rare inbound thread swap (t04's, Saturday) only if rec 3 is not in |
| 7 | **Decide on flips** (Pícaros → Pilar or rastro) | An exception to `official_value_margin` / `off_page_min_surplus` (Marius's hard rule) for buys with an observed exit ≥ price + margin | Unknown: about +25 P cash per flip. Score only through open L3/L4 slots and a team resale above our value | Breaks a hard rule. A dealer buy above our value may cost neg_points (not observed either way). Requests: each flip is two dealer threads (≈ 6–10 msgs): budget it like a hand `dealer buy`: MORNING §1 rates 3 extra `dealer buy` loops a NO-GO at 15 s ticks |
| 8 | **Never sell to a dealer below value**: verify that the hand `bazaar dealer sell` path reads `protect_page_sets` and #227, and refuses a `--floor` below value | A dry run by hand before 09:30 | Protects up to −4.33 per mistake on Saturday's board (measured) | None |

Not ranked here because other sessions own them: the 624 Saturday board-accept rejections and the Jev gate on team
swaps (quiet), venue and broker (mm-probe), album and packs (collections).

## Open questions for Marius
1. **Flips (rec 7):** may a buy go above our official value when a higher resale is already observed (Pilar's bids, a
   standing team bid)? It conflicts with `off_page_min_surplus`, your hard rule.
2. **Sunday 09:00 to ≈ 12:17:** spend it on round 2's open ladder slots (L2 Chato, better L1 deals) and team sales, before
   round 3 resets everything?
3. **L3 sells in round 3:** by hand again (Saturday's way), or `dealer_sell_enabled` on with floors ≥ value?
4. **Addressed offers (rec 3):** which do you want before Sunday's first bench: the taker reading `/api/me/offers` (code),
   `--accept-bids` (start command), both, or neither?
5. **Team desk:** off for Sunday (rec 6)?
6. **Duels III days fix (rec 5):** worth a merge before ≈ 14:17, given the freeze rules?

## What I could not verify
- **Railway logs:** not linked, and I did not link them.
- **Team-thread replies before tick 1289:** `/api/me/threads` returns only our last 50 threads, so the in-thread 0-reply
  finding covers 25 of 83. Board counter-bids (t02) are a reply of another kind (§1).
- **Addressed offers on the keyless board:** one live example (offer 19999), after the close. That the board hides them
  is consistent with 0 of 127 reaching a decision, but it is n=1 against the API.
- **The value of inbound bids** (≈ 30 neg_points) is the reviewer's computation against `snapshots.assets` at the time; I
  did not recompute it. The 5 inbound asks were checked against end-of-Saturday values, not the values at the time.
- **Who posted the 41 addressed asks:** "hand `bazaar sell list --to`" is proven by the ledger rows (`source` = `sell`,
  `hands-off:<offer id>`). Which person or session ran it is not recorded.
- **The neg cap:** whether gains saturate near the field's top neg_points. Two earlier windows and one clean gain only.
- **Whether a dealer buy above our value costs neg_points:** all 8 of our dealer buys were at or under it and moved 0.
- **The official ladder share formula:** our internal `outcomes.ladder_share` uses one price range for all sells to a
  dealer (Pilar 16 → 199 mixes uncommons and epics). It rates a 70 P rare sale at 30 % and a 19 P uncommon at 2 %, while
  the official `ladder_points` rose by about the same for both (+0.044, +0.050). Treat it as wrong for sells.
- **Round weighting (× 0.6):** inferred from RULES.md:124–125, not measured.
- **The board value of duel points:** relative to the field, measured only as windows.
- **Morning team trades** (ticks 160–386): `leaderboard_snapshots` starts at tick 610.
- **The 98 P days estimate** assumes each rival would accept its own proposed days at the final price.

## Review items and how they were handled (`_sat-review/review-dealing.md`)

| # | Item | Done |
|---|---|---|
| 1 | Addressed offers missing; "never posted" and "no team answered" false | Added to §1 (columns and t03/t13 rows), §3.3 (mechanism confirmed in code plus 1 live check; inbound asks valued), §4, rec 3, TL;DR 3; the two false claims corrected; the 57 non-maker listings explained (41 addressed hand posts + 19 public; 60 listings lack an `executions` row) |
| 2 | Sell "vs our limit" used the CLI floor | Column dropped; "below our value" count added; SAL-07 and RET-06 named; MAL-09 has no limit (noted) |
| 3 | Private values derivable | Per-deal neg_points removed; aggregates rounded; factor as a range; the floor sentence softened; exact figures in `_sat-review/dealing-PRIVATE.md` (outside git); branch history rewritten to one commit before any push |
| 4 | Round 3 ≈ 12:17 | Fixed throughout |
| 5 | Calibration: one window, no round weighting | 0–0.17 across 4 windows stated; × 0.6 applied to the expected column (inferred, flagged) |
| 6 | Rec 1(b) existed (`buyer_rank_enabled`) | Rec 1(b) rewritten |
| 7 | Request budget | Risk column covers requests on every rec; hand runs placed between benches |
| 8 | t10 over-attributed | Split 5.00 market / 6.93 negotiating; flips score only via ladder slots and team resales; t18/t05 not analysable (pre-610) |
| 9 | Team columns and fees | Addressed in/out, fees (18 P on 7 rastro buys: the reviewer's 21 on 8 counted one buy too many), after-fee cash +73, t03/t13 rows |
| 10 | `settlements_score.sql` window | Fixed to snapshot T against the last snapshot < T; it now reproduces `score_changes.sql`; marked private-output |
| 11 | Friday rows | 631 → 624 Saturday (617 from tick 160); tick-159 rows checked (all t01 ones are Sat 09:28); practice duels to 09:45 |
| 12 | Timeline gaps | Market Tests 2 and 3, Duels I end (15:39), Banco v2, round-2 start added |
| 13 | Ids, per-rival duels, walk count | Thread ids added; per-rival table added; 63 team walks (62 + 1 failed close) |
| 14 | Coverage | Covered by 1, 8 and 9 |

## Files (`docs/research/2026-10-04/dealing/`)

| File | What |
|---|---|
| `q.py` | read-only query helper (READ ONLY transaction, DATABASE_URL never printed) |
| `dealer_threads.py` | per-thread and per-dealer table (limits and surplus print only with `--with-limits`, for the private file) |
| `score_changes.sql`, `score_trajectory.sql`, `settlements_score.sql` | our `/me` components per tick and per settlement (**private output**: never commit it) |
| `ladder_windows.sql` | board change vs the field median for our key deals |
| `team_counterparties.sql`, `addressed_in.sql`, `addressed_out.sql` | per-team threads, addressed offers, board trades, cash, fees |
| `team_thread_replies.py`, `keyed_get.py`, `keyed_requests.log` | the logged keyed and keyless GETs and their analysis |
| `duels_agg.sql`, `duels_counterparty.sql`, `duels_by_rival.sql`, `duels2_days.sql`, `duel_nodeals.sql`, `days_latch_replay.py` | duels |
| `hourly_flow.sql` | flow per wall hour |
| `other_teams/` | part 4 sub-report and its 19 queries |
