# Points ledger: what moved our official score (Fri 2 Oct to Sat 3 Oct close, tick 1445)

Question (Omar): which moves earned and lost us points, how much, and what do we do in the ticks where no duel is
live. Companion skill: `.ai/skills/bazaar-points/SKILL.md` (the playbook; this file is the evidence).

Read-only analysis of the shared Postgres through the read-only role: `me_snapshots.score` (our `/me` score JSON, one
row per change, ticks 159 to 1445), `leaderboard_snapshots` (all 18 teams, every board refresh from tick 610),
`tape` (every settlement), `duels`, `outcomes`, `learnings`, `feed_events`. No game write, no Railway change.

## 0. How to read the numbers (limits first)

- **Two currencies.** *Raw legs* are what `/me` reports: `duel_points`, `ladder_points`, `neg_points` (team-trade
  surplus), `bench_points`. *Board points* are the official score the leaderboard shows. A raw point is not a board
  point: every component is normalised against the top-3 mean of all teams and a round counts by the share of its day
  played (`docs/briefing.md`, `docs/night/w5w6-score-redteam-morning.md`). Raw legs are exact; board attribution below is
  the **board change at the next refresh (every 10 ticks) after the event**, so two events in one window share it.
- **Board history of OTHER teams starts at tick 610**, and the other teams' raw legs are not public. Their move mix
  comes from `tape` (exact); their points come from `negotiating` and `market` only.
- **Weights are fitted, not published**: negotiating 30 = ladder 7.5 + duels 7.5 + team trades 15, market 30 = bench 22.5
  + organic 7.5 (briefing, audit). Anything "headroom" below depends on that split and is marked ESTIMATE.
- Historical settlements below are evidence, not permission to repeat them. Never sell or swap a page's last copy;
  never sell below our server `your_value`, and human approval does NOT waive that floor. These rules override any
  configured last-copy exception. Keep `max_score_loss_per_move` at `0.001` and no buy-back.
  `human_approval_above` was `250` when this was written; Omar set it to `0` (off) on Sun 4 Oct (`GUARDRAILS.md`). `dealer_sell_enabled false` means hand commands only after `bazaar impact`.
  Dealer sales can lose `neg_points`, so they require the same prospective impact check as other sales.

Sunday schedule correction, 4 Oct: the [live schedule](https://bazaar.causaprima.ai/api/schedule) says
"Round 3 starts" at h16.65, 09:00 CEST, coinciding with opening and Chamberí release. The ladder restarts then;
these Saturday results do not establish Sunday saturation. One game hour is one real hour on Sunday.
The 150 P grant is h16.7, about 09:03; Market Tests are h17/h19/h21, about 09:21/11:21/13:21; Duels III is
h18.65, about 11:00, with two issues, 12-tick duels and decay 0.10. Finale warning is h21.45, about 13:48;
all dealer stalls close and Grand Final duels start at h21.65, about 14:00. "Scores freeze" is h22.65, 15:00.
Whether the pre-opening h14.65 hard Market Test and h15 Market Test fire at opening or are skipped is
**UNVERIFIED**, as is their round attribution if fired. Full entries: `docs/briefing.md`, "Windows this weekend".

## 1. Where our 25.65 (rank 9) comes from, at tick 1445

`/me` score JSON (final row): `score 25.65 = negotiating 18.15 + market 7.5`; `duel_points 42.37`, `ladder_points 0.336`,
`neg_points 100.2`, `bench_points 0.5` (efficiency 0.854), `mm_points 0.0`, `luck -7.1` (never counts), 3 pages complete.

| Piece | Raw | Board share (ESTIMATE) | Evidence |
|---|---|---|---|
| Market Test, stall level | `bench_points` 0.5 | **7.5 of 25.65 (29 %)** | market leg 7.5 flat since tick 220; board jumped +4.89 at tick 220 when the first session booked 0.5 |
| Duels | 42.37 (Duels I 15.02 / 27 deals, Duels II 27.35 / 57 deals) | about 7.5 = at the cap | Duels II added +27.35 raw and about +0.1 net board (section 5) |
| Ladder | 0.336 | about 4.4 | board per ladder point fell 24 to 13 as other teams caught up (section 3) |
| Team trades | 100.2 | about 4.8 to 5.4 (k 0.048 / 0.035 per neg_point) | the loss at tick 948 and the gain at tick 1304 (section 4) |
| Sum | | 7.5 + 7.5 + 4.4 + 4.8 = 24.2 vs 25.65 observed | the 1.4 gap is round weighting and rounding |

**Measured k (board negotiating per neg_point): 0.048** on the loss (tick 948: neg_points 134.2 to 44.6 = -89.6, board
-4.27 at tick 950) and **0.035** on the regain (tick 1304: +55.6, board +1.97 at tick 1310). Guardrail fallback 0.053.
A gain made while we led in neg_points moved the board ~0 (ticks 376 and 386: +44.3 and +33.0, board +0.67 and 0.00).

## 2. Ranked move types (board points, Sat; ties broken by points per settlement)

| # | Move type | Raw | Board effect | Per move / per live tick | Verdict |
|---|---|---|---|---|---|
| 1 | Market Test at the stall's level (6 sessions x 16 ticks) | bench 0.5 | **+7.5** held (29 % of score) | 0 effort; v19 exact broker = free stall (sessions 1 to 3: efficiency equal to the auto baseline, BE1) | Floor, not a lead. t10 reached 12.5 (+5.0 over us) |
| 2 | Duels (Duels I + II, 2 x 193 live ticks) | 42.37 | about +7.5 total, but **Duels II about +0.1** | 84 deals; share 0.56 (I), 0.48 (II) | ESTIMATE: saturated in Saturday's round; Sunday UNVERIFIED |
| 3 | Dealer ladder, **Pilar sales at her final** (LAV-06 19, MAL-08 20, SAL-10 70, ticks 718/726/737) | +0.139 | **+3.34** (+1.21, +1.06, +1.07) | **+1.1 per deal**, 24 board per ladder point | Best move per settlement of the weekend |
| 4 | Dealer ladder, Pícaros buys (LAV-09 58, MAL-10 59, LAV-10 63) | +0.142 | **+2.05** (+0.73, +0.66, +0.66) | +0.7 per deal, 14 board per ladder point | Good, but LAV-10 was a fake final |
| 5 | Team trades, dup rare sold to a team (LAT-10 86, SAL-10 76, LAT-09 68) | +55.6 / +44.3 / +33.0 | +1.97 / +0.67 / 0.00 | best raw per move; board depends on the cap | Real, front-loaded; see section 4 |
| 6 | Team trades, buys below our value (MAL-07 14, SAL-07 23, MAL-06 20, SAL-10 72) | +13.2 / +6.5 / +5.5 / +11.5 | +3.55 over ticks 311 to 321 | +0.14 per neg_point early in the round | Cheap and repeatable |
| 7 | Other ladder deals (Abuela SAL-07 21, Chato SAL-09 95, Abuela 25 x2, sales at 5 and 6) | +0.055 | +0.26 and below | ~0 | Opening-price deals score 0 |
| 8 | Workshop crafts (4 by us) | 0 | 0 | luck never scores | Only for a missing rare |
| 9 | Organic market (our venue) | 0 | 0 | v19 had 0 organic trades | Nothing happened on our venue |
| 10 | **Sale of SAL-07 to Pilar at 29 (your_value 118.6)** | -89.6 | **-4.27** | the worst move of the weekend | Fixed: `max_score_loss_per_move`, `protect_page_sets` |

Other costs: **idle drift** (no event of ours in the window): the 27 refresh windows between ticks 652 and 1238 summed
**-2.73** (-0.10 per 10 ticks) because every other team kept improving the top-3 means.

## 3. The ladder, deal by deal (all our 16 Saturday dealer settlements, `tape` + `/me` raw legs)

| Tick | Dealer | Move | Price | ladder_points | Board at next refresh | Captured (`outcomes`) |
|---|---|---|---|---|---|---|
| 162 | abuela | buy LAV-08 | 25 | +0.009 | ramp | |
| 379 | abuela | buy SAL-08 | 25 | +0.010 | +0.67 (shared) | |
| 443 | chato | buy SAL-09 | 95 | +0.001 | 0 | opening-price type |
| **718** | **pilar** | **sell LAV-06** | 19 | **+0.050** | **+1.21** | final |
| **726** | **pilar** | **sell MAL-08** | 20 | **+0.045** | **+1.06** | final |
| **737** | **pilar** | **sell SAL-10** | 70 | **+0.044** | **+1.07** | final |
| 771 | picaros | buy LAV-09 | 58 | +0.049 | +0.73 | 60 % (range 73 to 48) |
| 790 | picaros | buy MAL-10 | 59 | +0.046 | +0.66 | 56 % |
| 864 | picaros | buy LAV-10 | 63 | +0.047 | +0.66 | 40 %, a fake final |
| 894 | chato | sell RET-06 (team-sourced copy) | 16 | 0 | np -1.5 | |
| 948 | pilar | sell SAL-07 | 29 | 0 | -4.27 (the np loss) | |
| 958 | abuela | buy SAL-07 (back) | 21 | +0.020 | +0.26 | 42 % |
| 1081 | abuela | sell LAV-05 | 6 | +0.003 | -0.02 | |
| 1084 | pilar | sell MAL-06 | 19 | 0 | 0 | |
| 1099 | picaros | sell MAL-02 | 5 | +0.012 | +0.16 | |
| 1212 | picaros | buy MAL-09 | 61 | 0 | 0 | 48 % |

- **Board per ladder point fell 24 (tick 720) to 14 (790 to 870) to 13 (960 to 1100)**: the top-3 mean of the ladder rose
  from about 0.31 to about 0.57 (7.5 / k). At 0.336 we hold about 59 % of the ladder component: **ESTIMATE +3 left**.
- **Best three per level count, a missing one is zero, the ladder restarts each round** (0.058 to 0.0 at tick 160). A
  fourth deal helps only if its share beats one of the three.
- The LAV-10 fake final still earned **+0.047**: snapshots show `ladder_points` **0.254 to 0.301 at ticks 863 to 864**.
  This observed delta supersedes the older approximately-zero estimate in `.ai/memory.md` and `GUARDRAILS.md`.
  A fake final does not prove either zero score or the dealer's true limit.
- **Dealer threads: 74 opened on Saturday, 16 settled (22 %).** The 72 `bad` dealer outcomes in `outcomes` (avg score
  0.017) are almost all "no deal ... captured nothing"; each held one of the six conversation slots.
- **We never opened a thread with Banco (level 5, Don Ernesto); its relative scoring weight is UNVERIFIED.** 33 threads were opened by
  11 other teams; 3 settled (LAV-11 120, SAL-11 116, SAL-11 120).

## 4. Team trades (neg_points): every move, raw +100.2

For a buy, surplus is the acquired server `your_value` minus total purchase cost, including fees we pay.
For a sell, surplus is net sale proceeds minus the server `your_value` lost with that copy. Net proceeds deduct
any fees we pay; with none, the sale formula is `price - your_value`. The buy formula has the opposite direction:
MAL-07 bought for 14 at tick 311 earned **+13.2**, as recorded below. A team-sourced copy sold to a dealer can also
change `neg_points`, as SAL-07 did at tick 948.

| Tick | Move | Raw neg_points | Board at next refresh |
|---|---|---|---|
| 160 | buy MAL-02 for 3 (rastro, t06) | +5.7 | ramp |
| 161 | buy SAL-05 for 9 | +1.6 | ramp |
| 163 | buy SAL-10 for 72 (t02) | +11.5 | +0.25 |
| 165 | buy MAL-04 for 8 | -0.3 | |
| 182 | sell LAT-08 for 25 to t14 | +13.7 | +0.95 |
| 311 | buy MAL-07 for 14 (t10 stall) | +13.2 | +3.55 over 311 to 321 |
| 320 / 321 | buy SAL-07 23, MAL-06 20 | +6.5 / +5.5 | |
| 376 | sell SAL-10 for 76 to t06 | +44.3 | +0.67 |
| 386 | sell LAT-09 for 68 to t16 | +33.0 | 0.00 |
| 894 / 898 | sell RET-06 to Chato 16 (a team-sourced copy sold to a dealer counts) / buy RET-04 for 6 | -1.5 / +1.0 | |
| 948 | **sell SAL-07 to Pilar for 29** (page card, your_value 118.6) | **-89.6** | **-4.27** |
| 1304 | sell LAT-10 for 86 to t12 (human approved, floor 80) | +55.6 | +1.97 |

Fifteen settlements in 1,285 ticks. The league's team market is thin: **135 rastro trades and 53 team-venue trades in the
whole weekend**, against 616 dealer settlements, and **no team-to-team thread is visible in the public feed** (all 1,750
`thread.opened` events are `kind: persona`, and no settled swap of ours appears in `tape`).
ESTIMATE: with k 0.035 to 0.048 and a 15-point weight, the top-3 mean of neg_points sits near 300 to 430 against our
100.2, so **team trades carry the most headroom (up to +10 board)**.

## 5. Duels: what they were worth

| Session | Live ticks | Our deals | League deals | Raw duel_points | Board in the duel-only windows |
|---|---|---|---|---|---|
| Practice (v1, unscored) | 120 to 192 | 15 / 34 (44 %) | 124 / 306 (41 %) | 0 | |
| Duels I (v2) | 459 to 651 | 27 / 34 (79 %) | 227 / 306 (74 %) | 15.02 | **+2.73 over 15 windows** |
| Duels II (v2, price + days) | 1239 to 1431 | 57 / 68 (84 %) | 475 / 612 (78 %) | 27.35 | **+0.08 over 14 windows** (+1.97 in the window that also held the LAT-10 sale) |

- Surplus kept by rounds (`duels`, deals): 0 rounds 24.7 to 35.5 P, 1 round 21.5 to 24.2, 2 rounds 20 to 25, 4 rounds 8 to 10
  (decay 0.06 in Duels I, 0.08 in Duels II): **the deals closed in 1.5 to 2 rounds are where the share was**.
- Our first priced offer sits **0.54 to 0.55 of our limit beyond it** (sellers +54 %, buyers -53 %, Duels I and II).
- ESTIMATE: duels were saturated for us in Saturday's round. +27 raw in Duels II moved the board ~0, consistent with
  a component at that round's top-3 mean. This does not establish saturation in Sunday's round. Whether duel points
  restart is UNVERIFIED (`docs/briefing.md`, "Scoring"). Before deprioritizing duels, require current-round evidence
  of saturation; otherwise preserve duels-first accepts and use remaining ticks for dealers.
- Duels ate 386 of 1,286 Saturday ticks (30 %); the other **900 (70 %) had no duel live**.

## 6. Idle time

Saturday ticks 160 to 1445 = 1,286. Duel windows 386. **900 ticks had no live duel**; this measures duel availability,
not inactivity. The former claim of 332 inactive ticks from 386 to 718 was false: ladder points rose **0.019 to 0.020
at tick 443**, and Duels I added **15.02 raw points during ticks 459 to 651**. No-duel ticks can still contain dealer,
team-trade or Market Test activity. A true inactivity interval would contain no change in any raw scoring leg;
no longest such interval is established here. Over the comparison window from tick 650 to 1230, which also contains
our settlements, **t01 lost 1.51 board points**; the top gainers:

| Team | negotiating 650 to 1230 | Settlements in 652 to 1238 | Move mix |
|---|---|---|---|
| t03 | **+7.10** | 13 | sells to Pilar 4, Picaros 3, rastro 5 |
| t06 | **+6.28** | 28 | Pilar 5, Picaros 7, rastro/venue 12 |
| t10 | **+5.46** | 23 | Pilar sells 8, Picaros buys 7, Abuela/Chato 6 |
| t15 | +4.48 | 30 | Abuela/Chato 15, Pilar 3, rastro sells 10 |
| t05 | +4.01 | 12 | Pilar 2, Picaros 4, rastro buys 5 |
| **t01** | **-1.51** | **14** | Pilar 5 (one a -4.27 mistake), Picaros 5, Abuela 3 |

Same volume, different result: t03 booked +7.1 on 13 settlements, we booked +2.8 before the SAL-07 loss on 14. The gain
came from Pilar sales (uncommons 14 to 30, rares 50 to 87, epics 140 to 199; ours 19 to 29 and one rare at 70), Pícaros
rares and epics (RET-09 59, RET-10 53, SAL-11 155) and a few rastro buys that completed pages (LAT-09 88, SAL-09 68).

## 7. What top-3 did that we did not (Saturday, tick 160 to 1445, `tape`)

| Team (final) | Settlements | Dealer buys / sells | Rastro b/s | Team venue | Threads opened | Market leg |
|---|---|---|---|---|---|---|
| t10 (1st, 37.58) | 42 | 16 / 16 | 4 / 4 | 2 | 175 | **12.5** (venue v07 since tick 179, "fair broker") |
| t18 (2nd) | 23 | 11 / 5 | 2 / 5 | 0 | 32 | 7.5 |
| t05 (3rd) | 29 | 12 / 9 | 3 / 0 | 5 | 49 | 7.5 |
| **t01 (9th, 25.65)** | **29** | 8 / 8 | 7 / 4 | 2 | **74** | 7.5 |

- **Volume is not the differentiator.** t04, t13, t08 and t07 settled 56 to 65 times and ranked 10th to 17th; t18 won
  2nd on 23. Quality per settlement and the market leg are.
- **t10's lead over us is +5.0 market and +6.9 negotiating.** The market leg is the cleaner lever: 6 sessions, same book
  for everyone, the full points go to the mean of the top three.
- **Epics and page rares from dealers**: t10 bought MAL-11 (195, t08), SAL-11 (155, Pícaros) at ticks 1264 to 1267 for +1.9
  board; Banco sold epics at 116 to 120, under every other source.

## 8. What the RAG says (809 `learnings`, 231 `outcomes`)

- `outcomes`: dealer 72 bad / 5 ok / 4 good (avg 0.017 / 0.446 / 0.700); duel 92 good / 36 ok / 8 bad (avg 0.875 / 0.1 / 0);
  trade 1 good / 5 ok / 7 bad; market_test 1 good (0.854).
- Policy lessons (confidence 0.95, `learn/curves`): Abuela commons ladder 7 to 12 step 1, replay share 0.434; uncommons 10 to
  26, 0.24; packs 17 to 20, 0.062 (a pack is not a ladder deal worth chasing); Chato rare 75 to 95, 0.30; **Pícaros rare 48
  to 67, 0.416; Pícaros epic 128 to 167, 0.449**; finals come after about 3 to 6 dealer bids.

## 9. Unverified and could-not-do

- The 7.5 / 7.5 / 15 and 22.5 / 7.5 splits are fitted; every "ESTIMATE" above inherits them.
- Other teams' raw legs and ladder shares are not public: no exact attribution of their points, only their move mix.
- Board windows can hold two events (tick 771 shared with a -0.23 drift; 311 to 321 grouped); the 10-tick refresh bounds
  precision to one window.
- Whether a Banco deal counts more on the ladder (higher level weight) and whether a bought epic can be re-sold to Pilar
  without a score loss are both **UNVERIFIED**. Historical prices of Banco 116 to 120 and Pilar bids 140 to 199 do not
  establish a legal exit or justify spending. Before any purchase recommendation based on resale, require a concrete
  candidate sale price at least equal to that copy's fresh server `your_value`, nonnegative prospective impact from
  `bazaar impact`, and compliance with every hard rule above. Missing evidence means no purchase recommendation;
  human approval alone cannot establish feasibility or waive the floor.
- The board before tick 610 (Duels I start, Market Test sessions 1 to 3) is only in our own `/me` score series.
- Phoenix traces were not queried (no key needed for this: the tables above hold the same events).

## Appendix: reproduce

`me_snapshots`: `select tick, score->>'neg_points', score->>'duel_points', score->>'ladder_points', score->>'bench_points',
score->>'score' from me_snapshots where team='t01' and score is not null order by tick;` then take the ticks where a raw leg
changes. Board: `select tick, negotiating, market, score from leaderboard_snapshots where team = ... order by tick`.
Moves: `select tick, venue, persona, buyer, seller, card_id, price from tape where buyer='t01' or seller='t01'`.
Duels: `select session, status, role, rounds, result from duels`. League duel counts:
`select payload->>'session', payload->>'status', count(*) from feed_events where type='duel.closed' group by 1, 2`.
