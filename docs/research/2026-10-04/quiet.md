# Why Team 1 was quiet on Saturday (sat-quiet)

Session sat-quiet, Sun 4 Oct 2026 00:10–01:30 Madrid. Read-only: Postgres `SELECT`s, `railway logs` /
`deployment list` / `variables` (non-secret names only), keyless public data already in the DB, `gh pr view`. No
game request with our key was made (0 keyed GETs). Times are Madrid; ticks are the game's.
Cash is private: it appears only as "room above `cash_floor`" buckets.

## TL;DR

1. **"Quiet" measured.** We had 29 settlements on Saturday, joint 13th of 17 active teams (median 46.5). The gap is dealer
   deals: 16 against a median of ~29, and Abuela sales (1 against a median of 10). But **the number of trades does
   not score** (RULES.md:122). t13 had 88 deals and finished 10th, t18 had 40 and finished 2nd, we had 33 and finished 9th.
   The quiet cost points only where it blocked a deal worth surplus. Those blocks are listed below.
2. **Cause #1: the venue locked up our cash.** v19 opened at 10:20 (tick 262) for 270 P (bond 250 + fee 20), most of
   our cash at the time. It earned **0 trades all day**, and its bench score was the free stall's 0.5 in all 5 sessions. Teams that
   kept the free stall (t05, t07, t15, t18) also have market 7.5. From 10:20 to 20:15, cash sat less than 60 P above
   the floor on 809 of 940 ticks. The guards logged 263 board skips for `cash_floor` and 27 dealer passes because
   nothing was affordable. The one buy that measurably scored and was lost: MAL-10 from a team at 75
   (ticks 504–584, about +38 neg_points at our values). We later bought it from a dealer instead, which adds no
   neg_points.
3. **Cause #2 is structural: from ~17:30 nothing was worth its price to us.** All our missing cards were in our two
   lowest-affinity sets (LAT, RET) or were MAL-09. The board showed **0 buy candidates on 547 of 548 ticks** from 17:34 to 23:00,
   so the 400 P payday at 20:37 stayed unspent: cash was more than 60 P above the floor on all 244 ticks after 20:58. Not
   buying was correct by the scoring rule (a buy above `your_value` loses neg_points). The point is that the album
   plan had no targets left to buy.
4. **Cause #3 is a bug that is still on main: no team swap ever made a concession.** The server answers a cancel with
   `{"cancelled": id}`, with no `status` field. `team_desk.py` reads that answer as "not dead", so it posts no
   new offer, then cancels the same offer again every tick (`offer_not_open`, 36 times). Result: 84 swap ladders
   opened, **0 concessions posted, 0 swaps**. Before 16:55 the Jev gate also refused 203 swap openings (mean confidence
   0.35 against a bar of 0.75).
5. **Not causes:**
   - Redeploys: about 78 per service during play. Still, taker, maker and duels each logged all 1,287 game ticks.
   - The learner: its skips matched the price caps.
   - Kill switch: 0 holds.
   - 429s: 2 maker reads.
   - Frozen Postgres: 1 `QueryCanceled`, handled.
   - The organisers paused the clock for 3 h 16 min (09:00–09:29, 13:25–15:29, 20:15–20:58).
   - **Near miss:** all three services ran against the **simulator** from 13:27 to 15:17 (`BAZAAR_SIM`). The game was paused then, and they came back 12 min before it resumed.

## Method and data actually seen

| Source | What | Window seen |
|---|---|---|
| Postgres `feed_events` | clock, settlements, offers (public feed, monitor-written) | ticks 159–1445 (09:28–23:00); continuous, no tick gap |
| Postgres `tape` | 793 settlements (= `settlement` events) | all Saturday |
| Postgres `decisions` / `executions` | every taker/maker/duels/guard decision with guardrail text, Jev verdict, SDK result | ticks 159–1445 (3,752 rows in all) |
| Postgres `me_snapshots` (t01) | cash, album, score, venue per tick | 1,287 of 1,287 ticks |
| Railway logs | 88 taker, 92 maker and 88 duels deployments (08:30–00:30), each fetched by deployment id, `--lines 5000` (no file reached the limit: the largest has 1,241 lines) | complete per-tick coverage 159–1445 for all three services; 11 taker, 10 maker and 11 duels deployments have 0 lines (replaced within seconds, or failed) |
| Railway `deployment list` | 103 taker deployments on Saturday: 79 ran, 22 SKIPPED (docs only), 2 FAILED | 07:00Z–21:05Z |
| Railway `variables` (taker, maker, duels) | non-secret `BAZAAR_*` names and presence of credentials | **as of 00:30 Sunday**, not as they were during the day |

Scripts and queries (all read-only) are in `docs/research/2026-10-04/quiet/`. `q.py` is the read-only SQL runner. `timeline.sql`,
`refused.sql`, `missed_asks.sql` and `lost_by_ref.sql` back the tables. `fetch_logs.sh` fetches the logs and
`parse_logs.py` turns them into per-tick coverage. `windows.py` produces the per-window counts of log lines. Log lines are
deduplicated on (tick, text), so two containers alive during a handover are not counted twice.

The windows follow regimes, not equal buckets:

| Window | Madrid | Ticks | Regime |
|---|---|---|---|
| — | 09:00–09:29 | 159 (frozen) | the organisers had not started the clock: day.opened 09:00:00, clock unpaused 09:28:37 |
| A | 09:29–10:20 | 159–261 | opening; the hourly spend cap is the binding limit |
| B | 10:20–11:49 | 262–440 | v19 opened (−270 P); `cash_floor` 100, then 50 |
| C | 11:49–13:25 | 441–630 | Duels I; cash flat; the maker's dealer-sell loop |
| — | 13:25–15:29 | 630 (paused) | organisers' pause; **our services pointed at the simulator 13:27–15:17** |
| D | 15:29–17:34 | 631–897 | Pilar sales, Pícaros buys; human approval ≥ 60 |
| E | 17:34–20:15 | 898–1201 | `cash_floor` 5 with almost no cash; the SAL-07 incident; team desk live |
| — | 20:15–20:58 | 1201 (paused) | announcement; payday +400 P at 20:37 |
| F | 20:58–23:00 | 1202–1445 | Duels II; about 400 P of cash and nothing to buy |

## 1. Tick-window timeline

"Up" means the service wrote at least one log line for every game tick of the window. Settlements are counted per
side. "Median" is over the other teams that settled at least once in that window.

| | A 09:29–10:20 | B 10:20–11:49 | C 11:49–13:25 | D 15:29–17:34 | E 17:34–20:15 | F 20:58–23:00 |
|---|---|---|---|---|---|---|
| Our settlements / others' median | **7** / 5 | 6 / 7 | **1** / 5 | 7 / 10 | 6 / 9 | 2 / 3 |
| Taker / maker / duels up | all ticks | all ticks | all ticks | all ticks | all ticks | all ticks |
| Taker redeploys started in window | 6 | 15 | 17 | 10 | 12 | 8 |
| Cash room above floor: < 25 / 25–59 / ≥ 60 P (ticks) | 2 / 1 / 100 | 112 / 9 / 58 | 0 / 188 / 2 | 34 / 163 / 70 | 173 / 130 / 1 | 0 / 0 / 244 |
| Taker ticks with ≥ 1 board candidate | 103 of 103 | 113 of 179 | 31 of 190 | 77 of 267 | **1 of 304** | **0 of 244** |
| Board accepts sent | 5 | 3 | 0 | 1 | 0 | 0 |
| Board skips: spend cap / cash floor / price cap / 1-accept quota | 264 / 0 / 201 / 32 | 57 / 187 / 127 / 2 | 8 / 28 / 0 / 2 | 6 / 48 / 70 / 3 | 0 | 0 |
| Dealer: opens allowed / no dealer buy affordable / open needs approval | 2 / 0 / 0 | 4 / 0 / 0 | 0 / 1 / 0 | 8 / 10 / 21 | 2 / 12 / 0 | 32 / 4 / 0 |
| Ticks holding ≥ 1 dealer thread | 7 | 5 | 2 | 30 | 9 | 68 |
| Swaps: Jev not yes / proposals / cancel loop / walked with no deal | 0 | 0 | 59 / 0 / 0 / 0 | 158 / 31 / 13 / 15 | 8 / 61 / 24 / 17 | 9 / 37 / 0 / 31 |
| Maker asks posted / mean open offers | 9 / 1.6 | 53 / 6.1 | 61 / 6.8 | 15 / 2.7 | 12 / 2.9 | 10 / 2.0 |

Sources: settlements from `timeline.sql`; up and candidates from `parse_logs.py` (taker line
`tick N taker: N accept candidate(s), N taken, N dealer thread(s)`, `agents/taker.py:754`); skips and swap rows from
`windows.py`; cash room from `timeline.sql` (floor history taken from the guardrail texts in `decisions`); maker
counts from the `tick N maker: N action(s), N posted, N open offer(s)` lines.

### What each window decided, and why it did not act

**A, 09:29–10:20 (ticks 159–261). The opening spree, then the spend cap.**
- The taker had candidates on every tick. It took 5 board asks and 1 Abuela deal in ticks 159–174. Most of the spend
  was SAL-10 at 72 + fee (tick 163).
- At tick 164 the hourly spend cap (`max_spend_per_game_hour` 150) was reached: "spend 139 + 27 > 150". For the next
  hour every candidate was refused for the spend cap (264 lines), the uncommon price cap 26 (201), or the 1-accept
  quota (32).
- 32 refused offers (23 + 7 + 2 by class), but **every card refused in A was bought later at the same or a lower
  price**: SAL-07 at 21 against asks of 26–35, MAL-06 at 20 against 22–27, MAL-07 at 14 against 20–28, SAL-08 at 25
  against 27–31 (`lost_by_ref.sql`). The cap delayed buys. It did not lose them.
- Maker: 9 asks. Duels: the practice session ended at tick 192.

**B, 10:20–11:49 (262–440). The venue takes 270 P.**
- #169 (10:06) first held 270 P in reserve for a venue. Then #171 (10:19, `allow_venue_open = true`, `cash_floor` 100)
  opened v19 at tick 262 for 270 P (`venue.opened` bond 250; cash dropped by exactly 270 on that tick). The free
  starter stall v08 closed (`venue.closed v08 replaced`).
- For the next 57 ticks the room above the 100 floor was under 25 P: 187 board skips for `cash_floor`.
- #174 (10:48) lowered the floor to 50. The taker bought MAL-07, SAL-07 and MAL-06 at once (ticks 311–321) and was
  stuck again. It sold SAL-10 to t06 (76) and LAT-09 to t16 (68) at ticks 376–386, then put 95 of that into SAL-09
  from Chato at tick 443.

**C, 11:49–13:25 (441–630). Cash flat at the floor, maker churning.**
- Cash did not move for 190 ticks: room 25–59 P on 188 of them.
- MAL-10 sat on the board at 75 (fee included) from tick 504 to 584. It was refused 31 times for `cash_floor` / spend cap and is the only lost team buy with
  surplus.
- The maker posted 61 asks (6.8 open on average) and sold none in C.
- `dealer_sell_enabled` was on 12:23–12:49. In that time the maker opened 8 sell threads with Abuela for SAL-01 and
  walked each one on her final of 5–6, below our floor of 7. That held our only Abuela conversation (RAILWAY_ERRORS row
  C).
- 17 taker redeploys in 96 min.
- Duels I ran from tick 459 (27 deals, 7 no-deals). Restarts cost the duels sends: 10 refused `wait_for_tick` and the
  re-anchored clock (RAILWAY_ERRORS A/B).

**Pause, 13:25–15:29.** At 13:27 a redeploy brought up **taker, maker and duels with `target: SIMULATOR`**
(`BAZAAR_SIM`). They traded the simulator (ticks 4044–4705) until the 15:17 redeploy put them back on the real game,
12 min before play resumed. No real ticks were lost. KNOWLEDGE_SAT_EVENING §2.2 noted "a live maker ran against the
simulator for ~2 min". It was all three services, for 1 h 50 min.

**D, 15:29–17:34 (631–897). Some cash, but approval and price gates.**
- Pilar sales (ticks 718–737) raised cash; room reached ≥ 60 P on 70 ticks.
- The taker bought LAV-09 (771) and MAL-10 (790) from Pícaros. Pícaros swapped the card in 9 offers, which the
  inspector rightly ignored (`picaros offer ignored: the offer gives [card:LAV-08] instead of exactly [card:LAV-10]`).
- From 15:46, human approval for trades ≥ 60 (#209) refused 21 dealer opens for LAV-10 (ticks 737–757) and MAL-09
  from Pícaros at 63 (tick 781). LAV-10 was approved and bought at 864. MAL-09 was not bought until tick 1212.
- MAL-10 on the board at 98–104 was refused 71 times by `max_price_rare` 95. That was correct: its official value was
  ~77 (#248's table).
- Swaps: 158 "jev not yes" lines in D, almost all before #213 (16:55) switched the taker's decider to the LLM. Before
  #213 the gate said yes to 0 of 214 swap questions. After it, it said yes to 220 of 260 (mean 0.81); the rest were 18 "no
  tick budget", 12 below the bar, 9 no and 1 timeout (`timeline.sql`, last query).

**E, 17:34–20:15 (898–1201). Nothing to buy and no money.**
- **One** board candidate in 304 ticks.
- The 12 dealer passes read "3 dealer buy(s) ranked, none affordable now: cash N − 67 < cash_floor 5" (cash redacted). That is
  the UB1 bug: affordability was checked at the top of the ladder, not its first rung. It kept MAL-09 closed from tick
  1095 to 1166 while Pícaros asked 60–65 (#248, fixed after the close).
- Every swap that reached its first concession died in the cancel loop (24 lines, §2.3).
- #228 (19:28) barred 9 teams from the desk until #250 (22:09).
- SAL-07 incident at tick 947. The `dealer_sell` breaker then stayed tripped until tick 1065.
- From 18:26 (#223), `protect_page_sets` covered every set, so the maker had only true duplicates to list (2.9 open offers on
  average).

**F, 20:58–23:00 (1202–1445). About 400 P and nothing to buy.**
- Payday (+400 P at 20:37, tick 1201). The taker bought MAL-09 from Pícaros at 61 (tick 1212). The maker sold LAT-10
  to t12 at 86 (tick 1304). Nothing else settled.
- Board candidates: 0 on all 244 ticks. The asks for our missing cards were LAT/RET commons at a median of 9 and
  RET-09/10 at 84 (`missed_asks.sql`), all above their value to us.
- 32 dealer opens:
  - 17 Pícaros threads for RET-09/10 walked at the official-value cap: "price 50 > official value 49".
  - 3 opens were refused by Pícaros's per-hour `persona_quota`.
- Swaps: 37 proposals, 31 walked with no deal.
- Duels II (21:16–22:53): 57 deals, 11 no-deals. 5 redeploys landed inside it (21:40, 22:10, 22:17, 22:47,
  22:49). 11 duel posts timed out
  (`refused network`).

## 2. Root causes, ranked by lost activity

"Lost activity" counts trades or threads that a gate or a bug stopped. "Score effect" is what the evidence supports.

| # | Cause | Lost activity (evidence) | Score effect | Status on main |
|---|---|---|---|---|
| 1 | **Venue bond: 270 P locked at 10:20.** v19 earned 0 trades all day and the same bench score as the free stall | cash room < 60 P on 809 of 940 ticks (10:20–20:15); 263 board skips for `cash_floor`; 27 dealer passes, none affordable; MAL-10 team buy missed (ticks 504–584) | MAL-10: about +38 neg_points at our values. Market gain from v19 over the stall: **0** (bench 0.5 in every session, 0 organic trades; t05/t07/t15/t18 on free stalls also have 7.5) | bond still posted (refundable 10 ticks after a close, as t13 did 3 times); `max_venues` = 2 allows a second hand-opened venue (another 270 P) |
| 2 | **No targets worth their price** (lowest-affinity sets + the official-value cap from #177, 11:12) | 0 board candidates on 547 of 548 ticks after 17:34; 17 Pícaros walks at the official value; about 400 P unspent for 2 h | none directly: by the rules, these buys would have lost neg_points. Indirect: no dealer-ladder deals to fill | by design; the round-3 plan must aim at deals that score (§4) |
| 3 | **Team desk: no concession ever posted** (cancel answer misread, then a re-cancel loop) | 84 ladders, 0 step-1 offers; 7 successful cancels never followed by a new offer; 36 refused re-cancels (`offer_not_open`) | 0 swaps. Upper bound small: only 13 swaps settled market-wide on Saturday | **still on main** (§3.1) |
| 4 | **Jev swap gate** (`team_swap_jev_gate`, bar 0.75) before #213 | 203 refused openings, ticks 576–805, mean confidence 0.35 | unknown, probably small (as #3) | after 16:55 the LLM decider said yes to 220 of 260; "no tick budget" 18 times at 30 s ticks |
| 5 | **Opening spend cap 150/h + price caps** | 32 refused offers in window A; 39 lost to the 1-accept quota | ~0: every refused card was bought later at a lower price | cap now 250 |
| 6 | **Human approval ≥ 60** (15:46–19:56) | 21 refused LAV-10 opens; MAL-09 at 63 refused (bought 4 h 20 min later at 61) | ~0 (delay only) | 250 now (#232) |
| 7 | **UB1: dealer affordability checked at the ladder top** | MAL-09 never opened in ticks 1095–1166 (12 + 4 passes) | ~0 (bought at 1212) | fixed in #248 (merged after the close; not seen live yet) |
| 8 | **Dealer sells: our floor of 7 above Abuela's finals of 5–6**, plus the 12:23–12:49 loop | 1 Abuela sale against a median of 10; 8 looped sell threads | small (level-1 ladder slots; we had 3 Abuela buys) | `dealer_sell_enabled = false` |
| 9 | Pícaros bait-and-switch | 27 ignored offers, threads walked | 0 (correct refusals) | — |

Checked and found **not** to be a cause:
- **Redeploys.** About 78 deployments per service ran during play (taker: 78 from 09:29 to 23:00). Taker, maker and duels each logged
  every one of the 1,287 game ticks: the old container keeps ticking until the new one is up. Their cost lands
  elsewhere: duels (10 `wait_for_tick` refusals and the restart re-anchor in Duels I; RAILWAY_ERRORS A/B), bench risk
  around Market Tests (MM_DEEP), and 25 v19 notices re-sent from fresh processes (MM_DEEP §7).
- **Learner** (`BAZAAR_LEARN` unset on the taker, so it was on all day). Its only skips were "chato card:uncommon" (55
  passes) and "chato card:rare" (19, while `max_price_rare` was 80). Both say "N of N conversations closed at or under
  the cap", so the price caps would have refused the same deals. The one learned block, "picaros persona quota until
  T1401", follows the server's own `persona_quota` refusals. Learned ladders only moved starting bids. No trade the
  guards would have allowed was blocked.
- **Kill switch / hold / flatten:** 0 "kill switch on" lines. The only breaker was `dealer_sell` (manual, tick 948 → reset 1065).
- **429s and rate budget:**
  - maker: 2 `read refused rate_limited`.
  - duels: 1 `/api/duels refused rate_limited`, 11 `refused network` timeouts in Duels II.
  - taker: 0.
- **#162 shared ledger / frozen Postgres:** 1 `holdings: snapshot not stored (QueryCanceled)` at 12:11 (handled). No
  tick lost, and the ledger Protocol methods (`release_accept`, `hands_off_ids`) exist on main (`ledger_pg.py:165`, `:212`).
- **#145 `protect_page_sets`:** RET,CHA until 18:26, then every set. It kept the maker's listing thin after that
  (2.0–2.9 open offers), and that was intended after SAL-07.
- **`cash_floor` 270 → 100 with the venue off (#71):** the floor was 270 only until 10:19. The floor that bound was
  100/50 *after* the bond had been paid (cause #1).
- **"Trade with everyone" (#250, 22:09):** in F, 8 threads walked because of the blocklist before it. After it, swaps
  still died at their first concession (cause #3).

## 3. Still broken on `main` (2bcc0949, 01:00)

1. **The team desk never posts a concession** (`src/bazaar_agent/agents/team_desk.py`):
   - `:1038–1048`: after a successful cancel, the answer is `{"cancelled": <id>}` (every cancel in `executions`, e.g.
     ticks 865, 1043). `body.get("status") not in DEAD` is then true for `None`, so it logs "offer N reads None: no new
     offer" and returns without clearing `talk.offer_id`.
   - `:1090–1094`: on the next tick `_still_open` returns True because the cancelled offer is no longer listed
     (`not seen`). It cancels again and gets `offer_not_open`, logs "cancel of offer N refused: no new offer this tick"
     and returns. This repeats until the thread walks.
   - Fix (not applied):
     - treat `body.get("cancelled") == old` as dead (refund, clear `offer_id`, go on to post);
     - treat the `offer_not_open` refusal as `_gone` (refund, clear, post);
     - in `_still_open`, read an unseen offer as gone, not open.
   - Test: a fake `cancel` that returns `{"cancelled": id}` must be followed by a `say` with the step-1 terms in the same tick.
2. **`no_buyback_ticks` = 480 counts ticks.** GUARDRAILS.md:161 means "4 game hours at 30 s ticks". At Sunday's 15 s
   it lasts 2 h. Low stakes. Say which is meant.
3. **Frozen-Postgres stall (r1, residual):** `PgLedger._run` sets a server-side `statement_timeout` but has no client
   deadline. A hung server behind the TCP proxy still blocks every writer's per-tick ping. Not seen on Saturday.
4. **Cash trap, not a bug:** `max_venues` = 2 (GUARDRAILS.md:98) allows a hand-opened `auto` venue for another 270 P. On
   Saturday's evidence the bench pays the same 0.5 to the free stall, a paid board venue, or both.
5. **Deploy churn:** every merge that touches `src/**`, GUARDRAILS.md or STRATEGY.md redeploys all three services (the
   Railway `watchPatterns`). There were 78 taker deploys during play. 5 landed inside Duels II.

## 4. Recommendations for Sunday, ranked by expected points

| # | Change | Where | Expected | Risk |
|---|---|---|---|---|
| 1 | **Don't lock cash Sunday morning:** no second venue (keep `max_venues` = 1, or just don't run `venue open`). Decide whether to close v19 to get the 250 bond back (10 ticks at 15 s = 2.5 min) | GUARDRAILS.md:98 `max_venues`; a hand `bazaar venue close` only if Marius chooses | keeps 250–270 P for the round-3 dealer ladder after ~11:34. Saturday's bench evidence says v19 adds 0 over a stall, **but** whether the starter stall comes back after a close is unverified: closing could cost the bench (0.94 per Saturday session, 3.75 per Sunday session). Default: keep v19, open no second venue | closing = possible loss of bench presence; ask the organisers first |
| 2 | **Fix the team-desk concession bug** (§3.1) before swaps run on Sunday | `team_desk.py:1038–1048`, `:1090–1094` | turns 0 concessions into real ladders. Upper bound small (13 swaps market-wide on Saturday), but each swap adds neg_points at our values | low: three branches, plus a test; merge only in a no-bench, no-duel window (one redeploy) |
| 3 | **Spend on deals that score in round 3:** the grant (150 at ~11:34) plus carried cash goes to dealer-ladder deals (3 per dealer level, sales included; RULES_AUDIT #4) and team trades bought below our value. Not to idle cash | strategy/ops; `ladder_probe_min_share`, `min_buy_surplus` 2 → 4 (B28) | ladder +1.7 to +3 (RULES_AUDIT estimate, unverified); idle cash scores 0 ("Only deals score") | buying above our value loses neg_points; the official-value cap still applies |
| 4 | **One merge batch, then freeze.** Every merge redeploys all three services. Merge only in 09:00–09:31 or after Duels III; never within 10 ticks of the hard Market Test (h14.65) or the Market Test (h15) | process | protects 3.75 per Sunday bench session plus Duels III clocks | none |
| 5 | **Decider at 15 s ticks:** if `jev: no tick budget` exceeds ~20 % of the taker's swap/probe verdicts in the first 20 ticks, set `BAZAAR_DECIDER=jev` on bazaar-taker. Swaps would then stop (Jev said yes to 0 of 214), so decide which you prefer | Railway bazaar-taker env | small either way | LLM latency p50 ~2 s against a 15 s tick |

## 5. Sunday pre-flight checklist (09:00 doors, 15 s ticks)

Each line names the Saturday cause it would have caught. Read-only commands. A Railway var change is Marius's step.

| When | Check | Pass | Would have caught |
|---|---|---|---|
| 08:40 | `railway logs -s bazaar-{taker,maker,duels} --lines 30 \| grep target:` | `target: real game https://bazaar.causaprima.ai` on all three | the 13:27–15:17 simulator run |
| 08:40 | `railway variables -s <svc> --json` piped to a filter that prints names only | taker/maker: `BAZAAR_LIVE=1`; **no `BAZAAR_SIM`** on any service; `DATABASE_URL` set on all three; duels `BAZAAR_DECIDER=jev`; taker `BAZAAR_DECIDER` chosen on purpose (now `llm`, OAuth token only, no `ANTHROPIC_API_KEY`) | simulator run; LLM `no tick budget` |
| 08:40 | duels log: `ledger: postgres ledger table … (shared…)` | not a JSONL fallback | #162 ledger not shared |
| 08:45 | `uv run bazaar rules` (or GUARDRAILS.md) | `cash_floor` 5, `max_spend_per_game_hour` 250, `human_approval_above` 250, `max_price_uncommon` 26 / `rare` 95, `max_accepts_per_tick` 1, `allow_venue_open` true, **`max_venues` 1 or no hand open**, `team_threads_enabled` true, `team_swap_jev_gate` true at 0.75, `team_desk_never_trade` none, `max_score_loss_per_move` 0.001, `no_buyback_ticks` 480 (= 2 h at 15 s), **`activity_stall_seconds` 15** (#248 says 15 for Sunday; it is 30) | the spend cap at open (A); approval at 60 (D); blocklist (E–F) |
| 08:50 | cash room: `/api/me` cash − `cash_floor` (one keyed GET, or the latest `me_snapshots` row) | ≥ 60 P, and a written plan for it (round-3 ladder) | cause #1 (cash lock) |
| 08:55 | `GET /api/clock` (keyless) | `paused` false at 09:00, `tick_seconds` 15 | the 09:00–09:29 wait |
| 09:00 + 5 ticks | one summary line per tick per service (taker `accept candidate(s)`, maker `action(s)`, duels `live duel(s)`) | a line on every tick, `LIVE`, `/me live` | down services |
| every 30 min | SQL on `decisions`: share of taker ticks with ≥ 1 board candidate; `dealer_skip` "none affordable" count; `team_offer` rows with `step 1`+ and a `say`; `executions` `error_code = 'offer_not_open'` | candidates > 0 somewhere; 0 `offer_not_open`; some step ≥ 1 swap offers | causes #2, #3, #7 |
| every 30 min | `/health` → `activity` (#248 watchdog) | `ok` or a labelled `idle` | the silent 1099–1201 stretch |
| every 30 min | `uv run bazaar breakers`; no PAUSE file; 0 `kill switch on` lines | nothing tripped by surprise | holds |
| each merge | `railway deployment list -s bazaar-taker --limit 5` | ≤ 1 deploy per batch, none within 10 ticks of h14.65 / h15 or during Duels III | churn (77 deploys) |

## 6. Open questions for Marius

1. Close v19 to free the 250 bond for round 3? Only if the organisers confirm that a team without a venue still gets
   a bench stall. Saturday shows the stall scores the same 0.5, but `venue.closed v08 replaced` suggests the stall does
   not come back by itself.
2. Who switched the three services to `BAZAAR_SIM` at 13:27, and was it on purpose (practice during the pause)? The
   check above catches it, but the step should be written down.
3. Taker decider on Sunday: the LLM said yes to 220 of 260 swap questions after 16:55. Every resulting swap still
   failed (bug #3), so that "yes" rate has never been tested against real fills. Keep `llm`, or go back to Jev?
4. `no_buyback_ticks`: 4 game hours, or 480 ticks?
5. Should the official-value cap stay a hard limit for round-3 dealer-ladder deals? A ladder deal scores by its share
   of the dealer's range, not by `your_value`. #248 notes Pícaros RET asks of 64–73 against an official value of 49.

## 7. What I could not verify

- Railway variable values **during** Saturday: I read them at 00:30 Sunday. A change during the day (for example
  `BAZAAR_SIM` 13:27–15:17) shows only through the logs' `target:` line.
- How the neg_points gain converts to board points. The only measured k (0.048) is from the loss side (#227). The
  MAL-10 figure (+38 neg_points) is at our private values, not official.
- Whether the starter stall returns after a venue close (question 1).
- The deployments with 0 log lines (11 taker, 10 maker, 11 duels): either they were replaced within seconds or their logs are gone. Coverage
  still has no tick gap, because the surrounding deployments logged those ticks.
- Swap counterfactuals: whether concessions would have closed swaps. The market-wide count (13 on Saturday) is the
  only bound.
- Board counterfactuals use the taker's own model value (`candidates->>'surplus'`). It includes a page-bonus share and
  is an estimate.
