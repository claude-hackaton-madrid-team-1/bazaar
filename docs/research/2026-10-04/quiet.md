# Why Team 1 was quiet on Saturday (sat-quiet)

Session sat-quiet, Sun 4 Oct 2026 00:10–02:30 Madrid. Revised after review (`_sat-review/review-quiet.md`, 13 items;
§8 lists what changed). Read-only: Postgres `SELECT`s, `railway logs` / `deployment list` / `variables` (non-secret
names only), public feed data already in the DB, `gh pr view`. **0 requests to the game with our key.** Times are
Madrid; ticks are the game's. Cash is private, so it appears only as buckets of "room above `cash_floor`". Code is
read on `origin/main` **03244c12** (02:15; includes #262, #263, #264).

## TL;DR

1. **Sunday blocker (main as it is now):** at 15 s ticks the taker drops the LLM decider and uses Jev
   (#262, `BAZAAR_DECIDER_MIN_TICK_S` default 30). On Saturday, Jev said yes to **0 of 214** taker questions. So team
   swaps and ladder probes stay off all Sunday unless Marius sets §5 row 0 on Railway bazaar-taker before 09:00.
   **Do not lower `team_swap_jev_min_confidence` below 0.5:** the validator rejects it, and an invalid GUARDRAILS.md
   stops every write on all three services.
2. **How quiet, against what:**
   - 29 Saturday settlements: joint 13th of 17 (others' median 46.5), 4.8 % of the market's 603.
   - On Friday we were **last of 17** with 4, so being quiet started before Saturday's code, and per hour Saturday was
     busier for us (2.7/h against 1.5/h).
   - Window C (11:49–13:25) was quiet only for us: 1 of 83 settlements. F (20:58–23:00) was quiet for everyone
     (29/h market-wide, against 52–92/h before).
   - Saturday-only settlements against the final rank: t13 64 (10th), t18 23 (2nd), t03 23 (5th), us 29 (9th). The
     number of trades itself does not score (RULES.md:122). Dealer deals score only as **ladder slots** (best three per
     level, a missing one counts zero; RULES.md:118). We filled **11 of 15** slots. That ties us with t10 and t18
     (1st and 2nd), and with t03, t15 and t17, but 10 of the 16 other teams filled 12–13; only t09 has fewer. Missing: 1 Chato slot (level 2) and all 3 Banco slots (level 5); 14 of 17 teams have 0 at level 5.
3. **Cause #1 (largest measured score effect): cash lock-up.**
   - v19 opened at 10:20 for 270 P (bond 250 + 20). It earned 0 trades all day, and its bench score was 0.5 every
     session, the same as the free stall. Free-stall teams t05/t07/t15/t18 also have market 7.5, and t16/t17 more.
   - With the floor of 100/50 we then kept, cash sat less than 60 P above the floor on 809 of 940 ticks from 10:20 to
     20:15.
   - Measured loss: MAL-10 from a team, about +40 neg_points at our values. We bought it later from a dealer, which
     adds none.
4. **Cause #2: dealer-ladder deals blocked by our own rules, not by the scoring.**
   - After 17:34 nothing on the board was worth its price to us: 0 candidates on 547 of 548 ticks. That was correct,
     because team trades score at our values.
   - Dealer deals score on the ladder, not at `your_value`. There we blocked ourselves:
     - the official-value cap on dealer bids (our own extension of a team-trade hint) walked 17 Pícaros threads at
       "price 50 > official value 49";
     - `max_price_uncommon` 26 and the learner kept Chato (level 2) at 2 deals;
     - epics were unbuyable (`max_price_epic` 0 until ~tick 1370; no Banco path in `src/`).
   - Result: about 400 P of payday cash unspent from 20:58 to 23:00.
5. **Cause #3: a team-swap bug, half fixed on main.** The server answers a cancel with `{"cancelled": id}`.
   `team_desk.py` reads that as alive, so on Saturday no concession was ever posted: 84 swap ladders, 0 swaps, 36
   futile re-cancels. #262 broke the loop. Each concession still costs a futile cancel and goes out 2 ticks late (§3.1).
   Not causes: redeploys (all three services logged all 1,287 ticks), the learner beyond the caps, the kill switch,
   429s, Postgres. Organiser pauses: 3 h 16 min.
   **Near miss:** taker, maker and duels ran against the **simulator** from 13:27 to 15:17 (during the pause).

## Method and data actually seen

| Source | What | Window seen |
|---|---|---|
| Postgres `feed_events` | clock, settlements, offers (public feed, written by the monitor) | Saturday ticks 159–1445 (09:28–23:00), no tick gap; Friday ticks 0–159 |
| Postgres `tape` | 793 settlements: **190 Friday** (ticks 2–155) + **603 Saturday** (ticks ≥ 159; every Saturday number filters on this) | Fri + Sat |
| Postgres `decisions` / `executions` | taker/maker/duels/guard decisions with guardrail text, Jev verdict and SDK result | ticks 159–1445 (3,752 rows in the table, Friday included) |
| Postgres `me_snapshots` (t01) | cash, album, score and venue per tick | 1,287 of 1,287 Saturday ticks |
| Railway logs | 88 taker, 92 maker and 88 duels deployments (08:30–00:30), each fetched by deployment id with `--lines 5000` (no file hit the limit; the largest has 1,241 lines) | taker and duels: a line on all 1,287 ticks; maker: all but 647 and 649, both its logged `rate_limited` reads. 11 taker, 10 maker and 11 duels deployments have 0 lines (replaced within seconds, or failed) |
| Railway `deployment list` | 103 taker deployments on Saturday: 79 ran, 22 SKIPPED (docs only), 2 FAILED | 07:00Z–21:05Z |
| Railway `variables` | non-secret `BAZAAR_*` names and whether credentials are set | **as of 00:30 Sunday**, not as they were during the day |

Scripts and queries (all read-only) are in `docs/research/2026-10-04/quiet/`:
- `q.py`: the SQL runner (read-only session).
- `timeline.sql`: clock, settlements, market per window with Friday, cash room, refusals, swap steps, Jev verdicts,
  ladder slots, probe gate.
- `refused.sql`, `missed_asks.sql` (swap offers excluded since the revision), `lost_by_ref.sql`.
- `fetch_logs.sh`, `parse_logs.py` (per-tick coverage) and `windows.py` (per-window counts of log lines). Log lines
  are deduplicated on (tick, text), so two containers alive during a handover are not counted twice.

The windows follow regimes, not equal buckets:

| Window | Madrid | Ticks | Regime |
|---|---|---|---|
| — | 09:00–09:29 | 159 (frozen) | doors at 09:00:00, clock unpaused at 09:28:37 by the organisers |
| A | 09:29–10:20 | 159–261 | the opening; the hourly spend cap binds. Server maintenance pause 10:31:27–10:32:18 (tick 284) is in B: 2 × `http_502` on clock reads, recovered |
| B | 10:20–11:49 | 262–440 | v19 opened (−270 P); `cash_floor` 100, then 50 |
| C | 11:49–13:25 | 441–630 | Duels I; cash flat; the maker's dealer-sell loop |
| — | 13:25–15:29 | 630 (paused) | organisers' pause; **our three services pointed at the simulator 13:27–15:17** |
| D | 15:29–17:34 | 631–897 | Pilar sales, Pícaros buys; human approval ≥ 60 |
| E | 17:34–20:15 | 898–1201 | `cash_floor` 5 with almost no cash; the SAL-07 incident; team desk live |
| — | 20:15–20:58 | 1201 (paused) | announcement; payday +400 P at 20:37 |
| F | 20:58–23:00 | 1202–1445 | Duels II; about 400 P and no buy allowed |

## 1. Tick-window timeline

"Up" means the service wrote a line on every game tick of the window. Settlements are counted once per settlement.
"Median" is over the other teams that settled at least once in that window.

| | Fri (baseline) | A 09:29–10:20 | B 10:20–11:49 | C 11:49–13:25 | D 15:29–17:34 | E 17:34–20:15 | F 20:58–23:00 |
|---|---|---|---|---|---|---|---|
| Market settlements (per hour) | 190 (~74/h, 60 s ticks) | 78 (~92/h) | 83 (~56/h) | 83 (~52/h) | 156 (~75/h) | 144 (~54/h) | 59 (~29/h) |
| Ours (share of market) | 4 (2.1 %), last of 17 | **7 (9.0 %)** | 6 (7.2 %) | **1 (1.2 %)** | 7 (4.5 %) | 6 (4.2 %) | 2 (3.4 %) |
| Ours / others' median | — | 7 / 5 | 6 / 7 | **1 / 5** | 7 / 10 | 6 / 9 | 2 / 3 |
| Quiet for… | us (different code and limits) | nobody | slightly us | **us only** | slightly us | slightly us | **everyone** (Duels II) |
| Taker / maker / duels up | — | all ticks | all ticks | all ticks | all ticks | all ticks | all ticks |
| Taker redeploys started in window | — | 6 | 15 | 17 | 10 | 12 | 8 |
| Cash room above floor: < 25 / 25–59 / ≥ 60 P (ticks) | — | 0 / 0 / 103 | 112 / 9 / 58 | 0 / 188 / 2 | 34 / 163 / 70 | 173 / 130 / 1 | 0 / 0 / 244 |
| Taker ticks with ≥ 1 board candidate | — | 103 of 103 | 113 of 179 | 31 of 190 | 77 of 267 | **1 of 304** | **0 of 244** |
| Board accepts sent | — | 5 | 3 | 0 | 1 | 0 | 0 |
| Board skips: spend cap / cash floor / price cap / 1-accept quota | — | 264 / 0 / 201 / 32 | 57 / 187 / 127 / 2 | 8 / 28 / 0 / 2 | 6 / 48 / 70 / 3 | 0 | 0 |
| Dealer: opens allowed / no dealer buy affordable / open needs approval | — | 2 / 0 / 0 | 4 / 0 / 0 | 0 / 1 / 0 | 8 / 10 / 21 | 2 / 12 / 0 | 32 / 4 / 0 |
| Ladder-probe gate (strategy_gate) | — | — | — | — | Jev undecided ×3, LLM no ×2 | LLM no ×7 (0.12–0.25) | LLM yes ×7 |
| Ticks holding ≥ 1 dealer thread | — | 7 | 5 | 2 | 30 | 9 | 68 |
| Swaps: Jev not yes / proposals / cancel loop / walked with no deal | — | 0 | 0 | 59 / 0 / 0 / 0 | 158 / 31 / 13 / 15 | 8 / 61 / 24 / 17 | 9 / 37 / 0 / 31 |
| Maker asks posted / mean open offers | — | 9 / 1.6 | 53 / 6.1 | 61 / 6.8 | 15 / 2.7 | 12 / 2.9 | 10 / 2.0 |

Sources:
- Market, ours and medians: `timeline.sql`. Per hour = settlements / window length in minutes. Friday's
  `feed_events` were backfilled, so its rate uses 155 ticks × 60 s.
- Up and candidates: `parse_logs.py`, from the taker line
  `tick N taker: N accept candidate(s), N taken, N dealer thread(s)` (`agents/taker.py:754`).
- Skips and swap rows: `windows.py`.
- Cash room: `timeline.sql`, with the floor history from GUARDRAILS.md at each merge.
- Probe gate: `decisions` `kind='strategy_gate'`.
- Maker: the `tick N maker: N action(s), N posted, N open offer(s)` lines.

### What each window decided, and why it did not act

**A, 09:29–10:20 (ticks 159–261): the opening spree, then the spend cap.**
- The taker had candidates on every tick. In ticks 159–174 it took 5 board asks and 1 Abuela deal; most of the spend
  was SAL-10 at 72 + fee (tick 163).
- At tick 164 the hourly spend cap (`max_spend_per_game_hour` 150) was reached: "spend 139 + 27 > 150". For an hour,
  every candidate was refused by the spend cap (264 lines), the uncommon price cap 26 (201) or the 1-accept quota (32).
- The floor did not bind in A. It had been 270 all night ("venue bond 250 + 20"). #71 (merged 09:14, venue off) cut it
  to 100. #169 (10:06) set 270 again. #171 (10:19) set 100 and opened the venue. 0 taker `cash_floor` refusals in A.
- 32 offers were refused, but **every card refused in A was in our album later, mostly cheaper**:
  - on the board: SAL-07 at 21 (asks 26–35), MAL-06 at 20 (22–27), MAL-07 at 14 (20–28), SAL-08 at 25 (27–31);
  - MAL-08 (refused at 33) came from the packs we opened at ticks 285 and 312.
  - Sources: `lost_by_ref.sql`, `me_snapshots.cards`, `pack.opened`. The cap delayed buys; it did not lose them.

**B, 10:20–11:49 (262–440): the venue takes 270 P.**
- v19 opened at tick 262: `venue.opened` bond 250, and cash fell by exactly 270 on that tick. The free starter stall
  v08 closed (`venue.closed v08 replaced`).
- For the next 57 ticks the room above the 100 floor was under 25 P: 187 board skips for `cash_floor`.
- The lock had two halves: the 270 P bond, and the floor kept after paying it (100; 50 from 10:48; 20 from 16:39; 5
  from 17:22). With the floor at 5 from 10:20, the room after the bond would have been about 110 P, not 19.
- The taker bought MAL-07, SAL-07 and MAL-06 (ticks 311–321) and was stuck again. It sold SAL-10 to t06 (76) and LAT-09
  to t16 (68), then spent 95 on SAL-09 from Chato (tick 443).

**C, 11:49–13:25 (441–630): quiet only for us. 1 of 83 market settlements.**
- Cash did not move for 190 ticks: the room was 25–59 P on 188 of them.
- MAL-10 sat on the board at 75 (fee included) from tick 504 to 584. It was refused 31 times for `cash_floor` and the
  spend cap together. It is the only lost team buy with surplus.
- The maker posted 61 asks (6.8 open on average) and sold none.
- `dealer_sell_enabled` was on 12:23–12:49. The maker opened 8 sell threads with Abuela for SAL-01 and walked each one
  on her final of 5–6, below our floor of 7. Each held our only Abuela conversation (RAILWAY_ERRORS row C).
- 17 taker redeploys in 96 min. Duels I from tick 459: 27 deals, 7 no-deals; restarts cost duel sends
  (RAILWAY_ERRORS A/B).

**Pause, 13:25–15:29.**
- At 13:27 a redeploy brought up **taker, maker and duels with `target: SIMULATOR`** (`BAZAAR_SIM`). They traded the
  simulator (ticks 4044–4705) until the 15:17 redeploy, 12 min before play resumed.
- No real ticks were lost. KNOWLEDGE_SAT_EVENING §2.2 had "a live maker … ~2 min". It was all three services for
  1 h 50 min.

**D, 15:29–17:34 (631–897): some cash, but approval and price gates.**
- Pilar sales (ticks 718–737) raised cash: the room was ≥ 60 P on 70 ticks.
- The taker bought LAV-09 (tick 771) and MAL-10 (tick 790) from Pícaros. Pícaros swapped the card in 9 offers, and the
  inspector rightly ignored them.
- From 15:46, human approval for trades ≥ 60 (#209) refused:
  - 21 dealer opens for LAV-10 (ticks 737–757). It was approved and bought at tick 864.
  - MAL-09 from Pícaros at 63 (tick 781). It was bought at tick 1212.
- MAL-10 on the board at 98–104 was refused 71 times by `max_price_rare` 95. That was correct for a team buy: its
  official value was ~77.
- Swaps: 158 "jev not yes" lines, almost all before #213 (16:55) switched the taker to the LLM decider. Before #213,
  Jev said yes to 0 of the taker's 214 questions: on swap openings 193 undecided, 7 no and 4 "no tick budget"; plus
  8 accept_ask and 2 probes, all undecided.
  After it, the LLM said yes to 220 of 260 (mean 0.81). The rest: 18 "no tick budget", 12 below the bar, 9 no, 1
  timeout.
- Ladder probe: Jev undecided at 0.39–0.46 (ticks 767–805), then the LLM said no twice.

**E, 17:34–20:15 (898–1201): no cash, nothing worth buying on the board.**
- 1 board candidate in 304 ticks.
- 12 dealer passes read "3 dealer buy(s) ranked, none affordable now: cash N − 67 < cash_floor 5". That is UB1:
  affordability was checked at the top of the ladder, not its first rung. It kept MAL-09 closed in ticks 1095–1166
  while Pícaros asked 60–65 (fixed in #248 after the close).
- The ladder probe was refused 7 times (LLM no, 0.12–0.25) while cash was low.
- Every swap that reached its first concession died in the cancel loop (24 lines; §3.1).
- #228 (19:28) barred 9 teams from the desk until #250 (22:09).
- **The SAL-07 sale at tick 947 is the day's largest score event, and it was a trade, not quiet.** neg_points fell
  134.2 → 44.6 and rank went from 4 (tick 897) to 12 (tick 950). See KNOWLEDGE_SAT_EVENING §1.1. The `dealer_sell`
  breaker stayed tripped until tick 1065.
- From 18:26 (#223), `protect_page_sets` covered every set, so the maker had only true duplicates to list.

**F, 20:58–23:00 (1202–1445): about 400 P, and our rules allowed no buy. Quiet market-wide (29/h).**
- Payday: +400 P at 20:37. The taker bought MAL-09 from Pícaros at 61 (tick 1212). The maker sold LAT-10 to t12 at 86
  (tick 1304).
- **Board: correct to stay out.** 0 candidates on 244 ticks. Missing cards were offered as LAT/RET commons (median 9,
  minimum 7), LAT-06 at 20–21 and RET-09/10 at 84 (`missed_asks.sql`), all above what they are worth to us. A team buy
  above our value loses neg_points.
- **Dealers: blocked by our own rules.**
  - 17 Pícaros threads for RET-09/10 were planned on a "ladder 48→61". Each walked at "price 50 > official value 49":
    the official-value cap applies to dealer bids too. No such thread could ever close. 3 further opens hit Pícaros's
    per-hour `persona_quota`.
  - Epics: `max_price_epic` was 0 until ~tick 1370, and `src/` has no Banco (level 5) buy path. The organisers had just
    said "Don Ernesto's vault and Los Pícaros' epics are within reach". Market-wide there were only 3 level-5 deals,
    all teams selling an epic to Banco, and we held none.
  - The ladder probe said yes 7 times. Its plans were the same capped RET threads.
- Swaps: 37 proposals, 31 walked with no deal.
- Duels II (21:16–22:53): 57 deals, 11 no-deals. 5 redeploys landed inside it (21:40, 22:10, 22:17, 22:47, 22:49).
  11 duel posts timed out.

## 2. Root causes, ranked by expected score effect

Ranked by what each cause cost in score, not by how many moves it blocked (that is the third column).

| # | Cause | Score effect (evidence) | Lost activity | Status on main |
|---|---|---|---|---|
| 1 | **Cash lock-up: v19's 270 P bond (10:20) plus the 100/50 floor kept after it** | **About +40 neg_points at our values** (MAL-10 team buy, ticks 504–584; at the loss-side k 0.048 that would be ≈ +1.9 board, but k is unmeasured for gains). v19 brought **0** market over the free stall: bench 0.5 every session, 0 organic trades; free-stall teams have 7.5–10.16. | room < 60 P on 809 of 940 ticks; 263 board skips for `cash_floor` (distinct offers: 17 in B, 3 in C, 3 in D); 27 dealer passes "none affordable" | bond still posted (refunded 10 ticks after a close, as t13 did 3 times); `max_venues` = 2 allows a second, hand-opened venue (another 270 P) |
| 2 | **Dealer-ladder deals blocked by our own caps and gates** (official-value cap on dealer bids, `max_price_uncommon` 26 against Chato, `max_price_epic` 0, no Banco path, the probe gate) | **Unmeasured, possibly the largest.** Ladder slots 11 of 15: tied with t10/t18 (1st/2nd) and t03/t15/t17, below the 10 teams with 12–13; 1 empty L2 slot (Chato) and 3 empty L5 slots. Ladder = 12.5 × min(1, raw / top-3 mean): what an L2 deal is worth depends on the share it captures, which the DB does not give for other teams. I do not claim a RET buy at 50 would have beaten one of our three L4 deals. | 17 RET walks at 50 > 49; 2 Chato deals; 0 Banco; probe gate no ×9 (813–1201); about 400 P idle for 2 h | by design; §4 rec 3 |
| 3 | **Swaps: Jev gate (until 16:55) + the cancel bug (all day)** | 0 swaps. Upper bound small: 13 swaps settled market-wide on Saturday | 204 refused openings (mean 0.35 against 0.75); 84 ladders with 0 concessions; 36 futile re-cancels | the gate is back on Sunday (TL;DR 1); bug half fixed (§3.1) |
| 4 | **Opening spend cap 150/h + price caps** | ~0: every refused card was in our album later, mostly cheaper | 32 refused offers in A; 39 lost to the 1-accept quota | cap now 250 |
| 5 | **Human approval ≥ 60** (15:46–19:56) | ~0 (delay only) | 21 refused LAV-10 opens; MAL-09 at 63 refused (bought 4 h 20 min later at 61) | 250 now (#232) |
| 6 | **UB1: ladder top instead of first rung** | ~0 (MAL-09 bought at 1212) | MAL-09 not opened in ticks 1095–1166 | fixed in #248 (not seen live yet) |
| 7 | **Dealer sells: our floor of 7 above Abuela's finals of 5–6**, plus the 12:23–12:49 loop | small: level 1 already had 3 deals | 1 Abuela sale, against a median of 10 among the 11 teams that sold to her (6 sold none) | `dealer_sell_enabled = false` |
| 8 | Pícaros bait-and-switch | 0 (correct refusals) | 27 offers ignored | — |

Checked and found **not** to be a cause:
- **Redeploys.**
  - About 78 deployments per service during play. Taker and duels logged all 1,287 ticks; the maker missed 2 (its
    rate-limited reads). The old container keeps ticking until the new one is up.
  - Their cost lands elsewhere: duels (RAILWAY_ERRORS A/B), bench risk (MM_DEEP), and 25 re-sent v19 notices
    (MM_DEEP §7).
- **Learner** (`BAZAAR_LEARN` unset, so on all day).
  - Its skips: "chato card:uncommon", 55 passes, which rests on at most 4 of 44 conversations closing under the cap; and
    "chato card:rare", 19 passes while the rare cap was 80. In both cases the price cap would have refused the same
    deals.
  - It is one reason level 2 kept an empty slot, together with the cap (row 2).
  - The learned "picaros persona quota until T1401" follows the server's own refusals.
- **Kill switch / hold / flatten:** 0 "kill switch on" lines. The only breaker was `dealer_sell` (manual, tick 948 →
  1065).
- **429s:** maker 2 reads, duels 1, taker 0. Duels II also had 11 `refused network` timeouts.
- **#162 shared ledger / frozen Postgres:** 1 handled `QueryCanceled` at 12:11. The Protocol methods exist on main
  (`ledger_pg.py:165`, `:212`).
- **#145 `protect_page_sets`:** RET,CHA until 18:26, then every set. It thinned the maker's listing after that, as
  intended after SAL-07.
- **"Trade with everyone" (#250, 22:09):** 8 threads in F walked on the blocklist before it. After it, swaps still died
  at their first concession.

## 3. Still broken on `main` (03244c12)

1. **Team desk, partly fixed** (`src/bazaar_agent/agents/team_desk.py`):
   - #262 added `if self.rec.last_code == "offer_not_open": … talk.offer_id = None` (`:1042–1049`), which breaks the
     Saturday loop.
   - Still wrong:
     - `:1053`: after a **successful** cancel the answer is `{"cancelled": <id>}` (every cancel in `executions`).
       `body.get("status") not in DEAD` reads that as alive, so no new offer is posted that tick.
     - `:1098–1102`: `_still_open` treats an offer it cannot see as open, so the next tick cancels it again and gets
       `offer_not_open`.
   - Net effect: every concession costs one futile cancel request, goes out **2 ticks late** (30 s at 15 s ticks), and
     waits for its refund in `_check_later`.
   - Two fixes are left:
     - read `body.get("cancelled") == old` as dead (refund, clear, post in the same tick);
     - in `_still_open`, read an unseen offer as gone.
   - Test: a fake `cancel` returning `{"cancelled": id}` is followed in the same tick by a `say` with the step-1 terms.
2. **The decider at 15 s ticks** (#262, `jev/decider.py:28–67`): `BAZAAR_DECIDER=llm` turns into Jev whenever
   ticks are shorter than `BAZAAR_DECIDER_MIN_TICK_S` (default 30). Not a bug: the LLM answered in 6.2–9.1 s against a
   window of about tick − 4.5 s − 1 s. But it brings back Saturday's pre-16:55 regime (TL;DR 1, §5 row 0).
3. **`no_buyback_ticks` = 480 counts ticks.** GUARDRAILS.md:161 means "4 game hours at 30 s ticks"; at 15 s it lasts
   2 h.
4. **Frozen Postgres (r1, residual):** `PgLedger._run` has a server-side `statement_timeout` and no client deadline. Not
   seen on Saturday.
5. **Cash trap, not a bug:** `max_venues` = 2 (GUARDRAILS.md:98) allows another 270 P bond by hand.
6. **Deploy churn:** every merge touching `src/**`, GUARDRAILS.md or STRATEGY.md redeploys all three services: 78 taker
   deploys during play, 5 inside Duels II.

## 4. Recommendations for Sunday, ranked by expected points

| # | Change | Where | Expected | Risk |
|---|---|---|---|---|
| 1 | **Keep the LLM decider at 15 s ticks** (§5 row 0) | Railway bazaar-taker env only | Swaps and ladder probes only run with it. Probes feed the ladder (row 3); swaps are small (13 market-wide on Saturday) | answers of 6.2–9.1 s: slower ones time out and refuse (fail closed); the change restarts the taker |
| 2 | **No new bond.** Keep v19 and don't hand-open a second venue (`max_venues` 2 → 1, or a written rule). Close v19 only if the organisers confirm the stall comes back | GUARDRAILS.md:98; `bazaar venue close` is Marius's call | keeps 270 P for round 3. On Saturday's evidence a second venue adds 0 bench | closing v19 could lose bench presence (3.75 per Sunday session) |
| 3 | **Let dealer-ladder deals price on the dealer's range, not the official value**, for round-3 ladder slots we do not have yet: at most 3 per level, a hard P cap per deal, logged as ladder buys | `official_value_margin` path (GUARDRAILS.md:27) for dealer bids; needs code (a ladder-slot exception) and review | fills empty slots: round-3 ladder +1.7 to +3 (RULES_AUDIT estimate, unverified) | each buy above the official value spends cash that scores nothing else; dealer buys add no neg_points either way, so the only loss is the cash |
| 4 | **Fix the remaining team-desk branches** (§3.1) | `team_desk.py:1053`, `:1098–1102` | concessions on time; small upper bound | one redeploy, outside bench and duel windows |
| 5 | **One merge batch, then freeze:** 09:00–09:31, or after Duels III; never within 10 ticks of h14.65 / h15 | process | protects 3.75 per Sunday bench session | none |

## 5. Sunday pre-flight checklist (09:00 doors, 15 s ticks)

**Row 0, the #1 item: the taker's decider at 15 s ticks.** As main stands, the taker falls back to **Jev** at 15 s ticks,
and Jev said yes to 0 of 214 taker questions on Saturday (its highest swap confidence was 0.47). That means no swaps
and no ladder probes all day.

- **Do:** on Railway **bazaar-taker only**, **before 09:00** (the change restarts the taker):
  - `BAZAAR_DECIDER=llm` (already set);
  - **`BAZAAR_DECIDER_MIN_TICK_S=15`**;
  - **`BAZAAR_DECIDER_TIMEOUT_S=8`**.
- **Risk:**
  - LLM answers took 6.2–9.1 s on Saturday. An 8 s timeout needs about 9 s left in a 15 s tick, so slower answers time
    out and refuse (fail closed): fewer swaps and probes, never an unjudged one.
  - The LLM's yes (220 of 260 on Saturday) was never tested against real swap fills, because of the cancel bug.
- **Verify in the first 20 Sunday ticks:**
  - the WARN "shorter than BAZAAR_DECIDER_MIN_TICK_S" is **absent** from the taker log;
  - some `yes` verdicts appear in `team_open` / `strategy_gate`, with "no tick budget" or a timeout in under 30 % of them
    (SQL below).
  - If either check fails, there are no swaps and no ladder probes: fall back to (c) below. Do not lower the bar.
- **Alternatives:**
  - **(b) `team_swap_jev_gate = false`** (GUARDRAILS.md:113). This is the only Jev-side lever that changes anything.
    The rules alone decide swaps, **with no judge at all**: every swap that passes the surplus, share, cash and
    official-value rules is sent. Ladder probes stay off: their bar is `stakes: design` = 0.75 in
    `questions/strategies.json`, with no GUARDRAILS knob. Merging it redeploys all three services.
  - (c) Accept it: no swaps and no probes on Sunday.
- **⚠ Never set `team_swap_jev_min_confidence` below 0.5.**
  - The field is `ge=0.5` (`guardrails.py:207`). A lower value makes GUARDRAILS.md invalid, and an invalid file stops
    every write on taker, maker and duels (`guardrails.py:483–497`).
  - Even 0.5 unlocks nothing. The gate needs `verdict == "yes"` (`team_desk.py:837`), and every Saturday Jev swap value
    was below 0.5 (max 0.47).

Each row below names the Saturday cause it would have caught, or says it is a guard rather than a check.

| When | Check | Pass | Would have caught |
|---|---|---|---|
| before **any merge or hand action** (all day) | Gate, not a read: no bond, venue open or close, floor change, or `BAZAAR_SIM`/`BAZAAR_DECIDER*` change without a one-line expected gain and Marius's sign-off | the line exists | cause #1 (#171 was a deliberate mid-morning merge that no pre-open read could catch); the simulator run |
| 08:40 | `railway logs -s bazaar-{taker,maker,duels} --lines 30 \| grep target:` | `target: real game https://bazaar.causaprima.ai` on all three | the 13:27–15:17 simulator run |
| 08:40 | `railway variables -s <svc> --json`, filtered to print names and these values only | taker/maker `BAZAAR_LIVE=1`; **no `BAZAAR_SIM`** anywhere; `DATABASE_URL` set on all three; duels `BAZAAR_DECIDER=jev`; taker `BAZAAR_DECIDER*` as chosen in row 0 | simulator run; decider |
| 09:00 + 5 ticks | taker log after 09:00: `grep "shorter than BAZAAR_DECIDER_MIN_TICK_S"` | **absent** (row 0 applied); present = Jev decides, so no swaps or probes unless (b) | the decider choice actually in force (variables at 08:40 say nothing about the running process) |
| 09:00 + 20 ticks | `SELECT kind, jev->>'verdict', jev->>'reason', count(*) FROM decisions WHERE agent='taker' AND kind IN ('team_open','strategy_gate') AND tick > <first Sunday tick> GROUP BY 1,2,3` | some `yes`; reasons `no tick budget for jev` + `request_timeout` under 30 % of rows | the gate regime (cause #3) |
| 08:40 | duels log: `ledger: postgres ledger table … (shared…)` | not a JSONL fallback | ledger not shared |
| 08:45 | `uv run bazaar rules` | `cash_floor` 5, `max_spend_per_game_hour` 250, `human_approval_above` 250, `max_price_uncommon` 26 / `rare` 95 / `epic` 240, `max_accepts_per_tick` 1, `max_venues` (see rec 2), `team_swap_jev_gate` true (or false if row 0 (b) was chosen) and `team_swap_jev_min_confidence` **≥ 0.5** (0.75), `team_desk_never_trade` none, `max_score_loss_per_move` 0.001, `no_buyback_ticks` 480 (= 2 h at 15 s), **`activity_stall_seconds` 15** (#248 asks for 15 on Sunday; it is 30) | the spend cap at the open (A); approval at 60 (D); the blocklist (E–F) |
| 08:55 | `GET /api/clock` (keyless) | `paused` false at 09:00, `tick_seconds` 15 | the 09:00–09:29 wait (organisers; information only) |
| 09:00 + 5 ticks | one summary line per tick per service (taker `accept candidate(s)`, maker `action(s)`, duels `live duel(s)`) | a line every tick, `LIVE`, `/me live` | a down service |
| every 30 min | SQL: every `team_offer` decision at step ≥ 1 has a `say` execution within 2 ticks; `executions.error_code='offer_not_open'` ≤ 1 per concession | both hold | cause #3 (the Saturday loop, and the 2-tick lag left on main) |
| every 30 min | SQL: `dealer_walk` rows whose reason contains `official value`; dealer settlements per persona in the round | the walks are not repeating on one card (#248 rests them); slots growing at each level | cause #2 (detects it; does not prevent it) |
| every 30 min | `/health` → `activity` (#248 watchdog) | `ok` or a labelled `idle` | the silent stretch from tick 1099 to 1201 |
| every 30 min | `uv run bazaar breaker list`; no PAUSE file; 0 `kill switch on` lines | nothing tripped by surprise | holds |
| each merge | `railway deployment list -s bazaar-taker --limit 5` | ≤ 1 deploy per batch, none within 10 ticks of h14.65 / h15 or during Duels III | churn (78 deploys) |

## 6. Open questions for Marius

1. **Decider for Sunday:** apply §5 row 0 (LLM at 15 s ticks) before 09:00? If it fails the 20-tick check, take
   (b) `team_swap_jev_gate = false` (swaps with no judge) or (c) no swaps and no probes. Never a bar below 0.5.
2. Close v19 to free the 250 bond for round 3? Only if the organisers confirm a team without a venue still gets a
   bench stall (`venue.closed v08 replaced` suggests the stall does not come back by itself).
3. Who switched the three services to `BAZAAR_SIM` at 13:27, and was it on purpose?
4. Should dealer-ladder deals stay capped at the official value (§4 rec 3)? The cap came from a hint about the value
   the score counts **trades** at. Extending it to dealer bids was our choice.
5. `no_buyback_ticks`: 4 game hours, or 480 ticks?

## 7. What I could not verify

- Railway variable values **during** Saturday: I read them at 00:30 Sunday. The simulator window shows only through
  the logs' `target:` line.
- Ladder **shares**. `dealer_curves` has our threads, but the scorer's range per dealer and other teams' shares are
  not in the DB, so rec 3's value is RULES_AUDIT's estimate, not a measurement.
- How the neg_points gain converts to board points: k 0.048 was measured on the loss side only (#227). The MAL-10
  figure is at our private values.
- Whether the starter stall returns after a venue close.
- Whether swap concessions would have closed swaps: the only bound is 13 swaps market-wide.
- The tick boundaries of floor changes are deploy times (±2 ticks). The buckets do not move with them.
- Board counterfactuals use the taker's own model value (`candidates->>'surplus'`), which includes a page-bonus share.

## 8. Revision log (review of 2f0ea5cc)

| # | Review item | Done |
|---|---|---|
| 1 | HIGH: decider at 15 s ticks | re-read on main 03244c12; TL;DR 1, §3.2, §4 rec 1, §5 row 0 (exact settings and risk), Q1 |
| 2 | HIGH: dealer deals score on the ladder | TL;DR 2 and 4, cause #2 rewritten (board correct, dealer blocked by our caps), ladder slots per level against t10/t18, F narrative, rec 3 with risk |
| 3 | §3.1 stale | re-pinned; only the two remaining branches; checklist pass changed to "a `say` within 2 ticks, ≤ 1 `offer_not_open` per concession" |
| 4 | Friday baseline, market-wide slowdowns | Fri column, market per hour, our share; C quiet only for us, F for everyone |
| 5 | ranking against its criterion | now ranked by expected score effect, with lost activity in its own column |
| 6 | checklist rows | "before any merge or hand action" gate row; `bazaar breaker list`; decider rows; cause #2 row marked "detects, does not prevent" |
| 7 | `tape` 793 = Fri + Sat | method table fixed (190 + 603) |
| 8 | `missed_asks.sql` counted swaps at 0 | `want.types` filter added; F minima now commons 7, uncommons 20 |
| 9 | mixed counts in TL;DR | Saturday-only settlements for all teams |
| 10 | 270 floor attribution | **partly rejected:** #71 *was* merged (04ce5d6b, 09:14 Madrid; MORNING §2 predates it) and set the floor to 100 with the venue off; 270 was the night value and came back via #169 (10:06). Corrected the line, and the A cash buckets (floor 100 in A, not 270); the 10:31 maintenance pause added |
| 11 | Banco and epics | folded into cause #2 and F |
| 12 | SAL-07 pointer | added in E |
| 13 | affinity ordering | "our two lowest-affinity sets" removed |

### Re-check (0ef731d6)

| Item | Done |
|---|---|
| HIGH: option (b) 0.35 is invalid (`ge=0.5`) and would stop every write; 0.5 unlocks nothing | (b) is now `team_swap_jev_gate = false`, with its no-judge risk; a loud warning never to go below 0.5 (TL;DR 1, §5 row 0, the rules row, Q1); row 0 is the exact Railway setting, with its risk and the 20-tick check |
| LOW: t10/t18 framing | the slot count is now set against all 16 other teams (10 of them filled 12–13) |
| LOW: SQL check groups by verdict only | groups by `kind`, `verdict` and `reason`; the pass criterion names the reasons |
