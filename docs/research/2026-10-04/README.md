# Saturday review (Sat 3 Oct): start here

*Historical record: written before Sunday's 09:00 open, as a pre-flight; the game closed Sun 4 Oct 15:00. The other
reports in this folder, written during Sunday, are listed under [Sunday reports](#sunday-reports).*

Entry point to five research reports on Team 1's Saturday. Written Sun 4 Oct ~01:00 Madrid, for decisions that must
be made before Sunday's 09:00 open (15 s ticks, last day). Every code or parameter reference below was re-read on
`origin/main` **03244c12**. Numbers come from the reports. Private values are not here (§7).

| Report | Final commit | Review |
|---|---|---|
| [dealing.md](dealing.md) | 522b8f63 + wording 5e09190d | `_sat-review/review-dealing.md`: ACCEPT |
| [quiet.md](quiet.md) | 0f5a52b0 | `review-quiet.md`: its re-check text ends in REVISE; quiet §8 "Re-check" handles each item; final ACCEPT |
| [mm-probe.md](mm-probe.md) | 795fd903 | `review-mm-probe.md`: its re-check text ends in REVISE; mm-probe §9 "Re-check round" handles each item; final ACCEPT |
| [collections.md](collections.md) | ce6d04a3 | `review-collections.md`: its re-check text ends in REVISE; the collections review table's "Re-check 6, N1–N3" rows handle each item; final ACCEPT |
| [logs-eggs.md](logs-eggs.md) | 164264ea | `review-logs-eggs.md`: ACCEPT |

## 1. TL;DR per report

**[Dealing](dealing.md).** 29 settlements; we finished #9 at 25.65 (t10 #1 at 37.58). The 13 board trades with
teams made our negotiating score (≈ +190 neg_points); the 16 dealer deals added nothing on the gain side, and the two
sold below value cost points (SAL-07 alone: board −4.33). The ladder was the other lever: 3 hand-run Pilar sells (L3)
gave +3.35. Nothing came from 127 offers addressed to us (never evaluated: the taker reads boards without a key, §4),
29 of 75 dealer threads that could never close (official-value cap), or 83 team threads (0 deals). The Duels II days
latch never armed: ≈ 98 P of result left, duel 5730 lost. Top recs: trade with teams both ways (+1.2 to +3), fill the
ladder on purpose (+1.8 to +3 in round 3), read addressed offers and sell into bids (+0.3 to +1).

**[Quiet](quiet.md).** 29 Saturday settlements, joint 13th of 17 (others' median 46.5); on Friday we were already last.
Cause 1, cash lock-up: v19's 270 P bond at 10:20 plus the 100/50 floor left < 60 P of room on 809 of 940 ticks, and
v19 brought 0 trades (bench 0.5, the same as the free stall). Cause 2, our own caps blocked dealer-ladder deals
(official-value cap on dealer bids, `max_price_uncommon` 26 against Chato, `max_price_epic` 0, no Banco path): 11 of
15 ladder slots, while 10 of the other 16 teams filled 12–13. Cause 3, a half-fixed team-desk cancel bug. Near miss:
all three services traded the **simulator** 13:27–15:17. Sunday blocker: at 15 s ticks the taker falls back to Jev,
which said yes to 0 of 214 questions.

**[MM-probe](mm-probe.md).** All 6 Saturday benches scored exactly the stall (bench_points 0.500; 25 pairs, all
queued). The non-crossing probe (`BAZAAR_BENCH_POLICY=probe`) is already set on bazaar-maker, has never run live, and
its 8-refusal stop can never fire (§4). v19 had 0 organic trades, and other venues lost their credit again. Expected
final points: Book A (bench probe) +0.9 to +2.2; Book B (organic v19) +0.05 to +0.25; Book C (our quotes) ≈ +0.2 per
+10 P of sale surplus (k_gain ≈ 0.033 board per neg_point, n = 1). No second venue. §6.1 is pitch text with a "do not
claim" list.

**[Collections](collections.md).** A completed page scores nothing by itself: the bonus sits in the `your_value` of
the last missing card, so it counts only when that card is **bought from a team** (field median +1.94 vs +0.46 for a
dealer-bought completer). Sunday: Chamberí with 9 dealer buys and a reserved common bought last from a team, ≈ 225 P
for ≈ +1.1–1.2 final; it needs change A (~250 lines, new code) and B-CHA (+2 P overpay). Buy no packs
(`max_packs_per_game_hour` 0). `cash_floor` is 5; the hourly spend cap 250 binds, and our standing MAL-11 bid counts
against it. Also sell LAT-09 at ≥ 90 to a team (≈ +1.1 final) and buy LAV-11 from a team (≈ +0.7).

**[Logs-eggs](logs-eggs.md).** Other teams found 27 eggs and 21 badges on Saturday; we found 0, though Pilar gave us
the first clue 5 times (our agents only send price templates). An egg fires when a dealer message contains a secret
phrase (organisers' public editor bundle). LAT-13, the hidden card, is gone (t02). `/api/schedule` now puts the Sunday
open at h16.65: if that holds, round 3 starts at 09:00 and Duels III at ~11:00, and the night plan is stale (confirm
at 08:55). 11 news items stored, none acted on. The pen test (47 keyless GETs) found no hidden endpoint. 14 taker
restarts landed inside Duels I.

## 2. Sunday pre-flight (ranked)

A Railway variable change redeploys **one** service; a merge touching `src/**`, GUARDRAILS.md or STRATEGY.md
redeploys **all three**. The reports disagree on Sunday's clock: **JUMP** (09:00 = h16.65; collections and logs-eggs
call it likely) or **RESUME** (h13.37 continues; dealing and mm-probe planned on it). Item 2 decides. Freeze windows
(no deploy, variable change or pause):

| | JUMP (09:00 = h16.65) | RESUME (09:00 = h13.37) |
|---|---|---|
| Round 3 + Chamberí release / grant 150 P | 09:00 / 09:03 | 12:17 / 12:20 |
| Benches (Market Tests) | overdue h14.65 (hard) + h15 may fire at 09:00 or be dropped; 09:21, 11:21, 13:21 | 10:17, 10:38, 12:37, 14:37 |
| Duels III | ~11:00 | ~14:17 |
| Stalls close + Grand Final / freeze | 14:00 / 15:00 | after the 15:00 close / 15:00 |
| **Freeze** (JUMP column derived here: mm-probe's 5-min-before / 2-min-after rule on logs-eggs' offsets) | **08:45–09:40**; 10:55 until Duels III ends (Saturday's Duels II took 1 h 36); 13:16–13:26; 13:55 to the close | **10:12–10:23, 10:33–10:44, 12:32–12:44, 14:11 to the close** |

**Deploys:** anything that redeploys lands, and is verified, **before 08:45**. This takes the strictest of the
reports' windows: quiet suggested "09:00–09:31", which falls inside logs-eggs' 08:45–09:40 freeze. The next gap
depends on the clock scenario.

| # | When | Where | Exact change or check | Expected effect | Risk | From |
|---|---|---|---|---|---|---|
| 1 | before 08:45 | Railway **bazaar-taker** variables (restarts the taker only) | `BAZAAR_DECIDER=llm` (already set), **`BAZAAR_DECIDER_MIN_TICK_S=15`**, **`BAZAAR_DECIDER_TIMEOUT_S=8`** (decider.py:23, :28; the default floor is 30, and the test `tick < floor` makes Jev decide at 15 s). Duels stay on `BAZAAR_DECIDER=jev` | The LLM judges swaps and ladder probes (Saturday: 220 of 260 yes) instead of Jev (0 of 214) | Answers took 6.2–9.1 s and an 8 s timeout needs ~9 s of the tick, so slower ones refuse (fail closed) | quiet §5 row 0 |
| 1a | 09:00 + 20 ticks (5 min) | taker log + read-only SQL | The WARN "shorter than BAZAAR_DECIDER_MIN_TICK_S" is **absent**. `SELECT kind, jev->>'verdict', jev->>'reason', count(*) FROM decisions WHERE agent='taker' AND kind IN ('team_open','strategy_gate') AND tick > <first Sunday tick> GROUP BY 1,2,3` shows some `yes`, with "no tick budget" + timeout under 30 % of rows. If the check fails, take (c): no swaps, no probes. `team_swap_jev_gate=false` (swaps with no judge) only on Marius's word | Confirms the decider in force | ⚠ **Never set `team_swap_jev_min_confidence` below 0.5.** The field is `ge=0.5` (guardrails.py:207), and an invalid GUARDRAILS.md stops every write on all three services (guardrails.py:483–497). Even 0.5 unlocks nothing: Jev's maximum on Saturday was 0.47 | quiet |
| 2 | 08:55 | keyless `GET /api/clock`, `GET /api/schedule`; `uv run bazaar clock` | `t_hours` and `/api/schedule` show which column of the freeze table applies, and whether h14.65/h15 are overdue | Every window below | none | logs-eggs, mm-probe, collections, dealing |
| 3 | gap after the first Sunday bench(es): RESUME 10:23–10:33; JUMP 09:40–10:55 | Railway **bazaar-maker** variables (maker redeploy, gap only) | Keep `BAZAAR_BENCH_POLICY=probe`. Read the maker log (`bench probe … QUEUED / REFUSED <code> / gone / dropped`) and `/me` bench_points. **≥ 4 `REFUSED` at the POST and 0 `gone` → set `BAZAAR_BENCH_POLICY=exact`.** 1–3 refusals → count them together with the next bench. `gone` with bench_points < 0.500 → `exact`. `gone` with > 0.500 → keep. Leave `BAZAAR_BENCH_MATCH_PROBE` unset: #263 (merged 00:41) is a second probe path, off by default, that no report evaluated | +0.9 to +2.2 EV. The switch-off stops 6 refused matches per session (optics, RULES.md:84) | False switch-off in the limit world: −0.1 to −0.35 EV. Two maker containers alive during a bench can lose the session | mm-probe §3.1 |
| 4 | 08:40 | `railway logs -s bazaar-{taker,maker,duels} --lines 30 \| grep target:`; `railway variables` (print names and these values only); `uv run bazaar rules` | `target: real game` on all three; **no `BAZAAR_SIM`** anywhere; `BAZAAR_LIVE=1` on taker and maker; `BAZAAR_BENCH_POLICY=probe` and **`BAZAAR_BENCH_MATCH_PROBE` unset** on bazaar-maker (`preserve()`d by railway.py, so a hand-set value survives applies). Rules: `cash_floor` 5, `max_spend_per_game_hour` 250, `human_approval_above` 250, `max_accepts_per_tick` 1, `team_swap_jev_min_confidence` 0.75 | Catches a repeat of the 13:27–15:17 simulator run | none (reads) | quiet §5 |
| 5 | before 08:45, **one** GUARDRAILS.md commit (all three redeploy); run `uv run bazaar rules check` before merging | GUARDRAILS.md | `max_packs_per_game_hour` 3 → **0** (:28). `activity_stall_seconds` 30 → **15** (:155). Optional: `deploy_guard_bench_ticks` 10 → 20 (:145). Only if Marius decides (§5): `team_threads_enabled` → false (:105) and `max_spend_per_game_hour` 250 → 500 (:19). `buyer_rank_enabled` stays false (:137; §3) | Packs never score. The watchdog fits 15 s ticks. 20 ticks = 5 min of deploy guard | One bad value stops every write: check before merging | collections §5, quiet §5, mm-probe §5, dealing rec 6 |
| 6 | 09:00, before any CHA dealer buy | Game write (Marius's approval): MCP `revoke` card=MAL-11 side=buy, or let it lapse at tick 1657 (≈ 09:53 under JUMP) | Removes the standing MAL-11 bid, which counts against the 250/h spend cap | Frees the cap for the CHA page and the ladder buys | MAL-11 has to be re-approved later (cap in the companion) | collections §4.3 |
| 7 | dry run 09:00–09:30; live from round 3 (RESUME: plus one Chato deal before 12:17) | Hand CLI, between benches and outside Duels III | `uv run bazaar dealer sell <ref> --min <≥ value> --start <ask> --dealer pilar`, first without `--live`. `check_floor` (dealer_sell.py:329) refuses a `--min` below `your_value`, and an only-copy guard is in place. Aim for 3 negotiated deals per level, L3 first. Keep `dealer_sell_enabled` false (:120) | Round 3 ladder +1.8 to +3; Chato slot +0.3 to +0.6 (RESUME only) | Dealer patience and quota (Pilar and Chato 6/h). 1 msg per tick of request budget | dealing rec 2, rec 8 |
| 8 | from the open | GUARDRAILS.md:48, no change | LAT-09 to a team at ≥ 90 is already ordered (`protect_page_exceptions` LAT-09:90). Check that the maker lists it | ≈ +1.1 final, and +90 P cash for the epics | low | collections row 2, dealing rec 1 |
| 9 | after the CHA dealer spend is booked | Hand approval (Marius) | `uv run bazaar approve LAV-11 --buy --max <cap from the companion> --ttl-ticks 480` | ≈ +0.7 final | Ties up to 240 P of the spend cap for the bid's whole life (480 ticks = 2 h at 15 s) | collections row 3 |
| 10 | all day | process | No second venue: keep v19 and leave `max_venues` 2 (:98) unused. Never pause the maker or touch the kill switch during a bench (broker.py reads them per match) | Protects 270 P and 0.5 bench points per session | – | quiet rec 2, mm-probe R5, R7 |
| 11 | first Sunday `/me` | read | Do `neg_points` and `duel_points` reset at round 3? | Sizes every round-3 estimate | none | collections, dealing |
| 12 | every 30 min | read-only DB, `/health` | `logs-eggs/eggs.sql` §1–4 (new eggs, Chamberí/andén replies, `hidden` cards in `cards`) and the news query. Quiet's checks: a desk `say` within 2 ticks of a concession; `dealer_walk` reasons containing "official value"; `/health` activity; `uv run bazaar breaker list` | A new egg, secret card or true news item reaches a human within minutes | none | logs-eggs rec 4, quiet §5 |

## 3. Conflicts between the reports

- **1 accept per tick** (`max_accepts_per_tick` 1, shared with duels, duels first). Board accepts (dealing rec 1/3,
  the reserved CHA common) and dealer finals (CHA, ladder) compete; Duels III takes it first. **Priority:** finish CHA
  dealer buys and hand ladder runs before Duels III (JUMP ~11:00, RESUME ~14:17). Saturday lost no deal to the queue.
- **6 conversations, one per dealer.** Taker dealer threads, the desk (`team_threads_max_open` 2, dealer reserve 3),
  hand ladder sells, ~13 Abuela threads for CHA (one at a time), mm-probe's R2 invite and the egg messages all compete;
  an egg or hand thread is refused while the taker holds that dealer. **Priority:** ladder and CHA, then R2, then eggs
  in a quiet tick; run `uv run bazaar threads` before any hand open.
- **Team desk: three directions.** Dealing rec 6 says off (83 threads, 0 deals; frees 2 slots and requests); quiet
  says keep it with the LLM decider and the §4 row 3 fix; mm-probe's v19 invite (#251) rides in desk proposals.
  Evidence: 13 swaps market-wide all Saturday, 0 ours; the 2-tick lag is still on main; Book B alone is +0.02 to
  +0.05. **Set the decider (item 1) either way**: ladder probes (`strategy_gate`) need it. **Marius's call:** desk on
  or off (lean off if the item 5 batch ships). With the desk off, the 20-tick check sees only `strategy_gate` rows:
  rely on the missing WARN and extend the check to ~40 ticks.
- **Official-value cap on dealer bids** (`official_value_margin` 0, GUARDRAILS.md:27). Dealing rec 4 keeps it and
  clamps ladder tops so dead threads never open; quiet rec 3 lifts it for round-3 ladder slots; collections B-CHA adds
  +2 P on CHA; dealing rec 7 (flips) also breaks `off_page_min_surplus`, Marius's hard rule. Whether an above-value
  dealer buy costs neg_points was never observed (the first B-CHA buy would tell). All options need code by 08:45.
  **Marius's call** (§5 decision 3).
- **Cash: `cash_floor` 5 and `max_spend_per_game_hour` 250.** CHA (≈ 225 P), LAV-11 (up to 240), the standing MAL-11
  bid, ladder buys, flips, quiet rec 3 and a second venue (270 P) all draw on the same 250/h. **Priority:** revoke or
  lapse MAL-11 → CHA dealer buys (they also count as L1/L4 ladder deals, serving dealing rec 2 and quiet rec 3) →
  LAT-09 sale (cash in) → LAV-11 → MAL-11 re-approved → flips last or never; no second venue (quiet and mm-probe
  agree). Raising the cap to 500 is Marius's call.
- **`buyer_rank_enabled`:** dealing rec 1(b) says true; mm-probe R4 says false (GUARDRAILS.md:137: Friday addressed
  asks filled 6 % vs 19 % public; Saturday's 41 addressed asks filled 0). **Recommend false** unless a GUARDRAILS batch
  ships anyway; Marius's call.
- **Dealer sells:** dealing offers `dealer_sell_enabled` = true as an option; logs-eggs rec 5 and quiet cause 7 call it
  the SAL-07 risk. **Recommend hand runs** (every scoring L3 deal on Saturday was by hand).
- **5 req/s key budget** (4.73 req/s at every loop's ceiling, conditional). Dealing rec 3a adds 1 GET/tick
  (≈ 0.07 req/s); eggs ~10 requests; R2 ~3; each hand thread 1/tick; desk off saves requests. Probe and notices use the
  broker key (separate bucket assumed, never tested). **Priority:** no extra `dealer buy` loops; hand actions in quiet
  ticks.
- **Redeploys.** Change A, dealing rec 3a/4/5, the desk fix and R3b are code: each merge redeploys all three. The
  `exact` switch (item 3) and the decider (item 1) are single-service variables. **Priority:** one batch before 08:45;
  after that only item 3 and the duel-days fix (§4 row 4), in gaps.
- **Clock assumptions.** Dealing's "09:00–12:17 still counts for round 2" (the Chato slot) and mm-probe's 10:23
  readout hold only under RESUME; under JUMP, round 3 and CHA start at 09:00. Item 2 settles it; prepare both.

## 4. Live code defects (open on `origin/main` 03244c12)

| # | Where | Effect | Proposed fix | Size | Checked on main |
|---|---|---|---|---|---|
| 1 | `agents/bench_probe.py:39–40` (`DEFAULT_GIVE_UP_AFTER` 8 > `DEFAULT_MAX_PER_RUN` 6), budget at :117, latch at :151–156 | The `quote_rule` stop can never fire. Under the quote rule the broker sends 6 refused non-crossing matches **every session, all day** | mm-probe R3b: count POST refusals (code ≠ `dropped`) on their own and latch at ≥ 4 with nothing queued (safer: spread over ≥ 2 ticks). Do not just set give-up ≤ 6, because drops would count. Until then, the item 3 manual rule | ~15 lines + 2 tests; maker redeploy | yes |
| 2 | `agents/taker.py:1017` `_board_of` → `self.public.board()`, the keyless `PublicBazaar` (`sdk.py:51–55`). `agents/market.py:175–183` `board_offers` would keep `to == us`, but those offers never arrive. `market.py:200–210` `our_open_offers` skips them | **127 offers addressed to us were never decided.** About 30 neg_points of above-value bids were missed. `--accept-bids` alone does not fix it: `accept_bids` is False (`taker.py:187`), the start command is `agent taker` (`.railway/railway.py:283`), and the bid path reads the same keyless board (`taker.py:739–742`) | (a) Feed the offers that `/api/me/offers` returns with `to == us` into the taker's candidates, or read boards with the team client. (b) Add `--accept-bids` to the start command. Test that `protect_page_sets` and the move-impact guard bind on the bid path | (a) small-to-medium new path + tests (estimate); (b) one line; all-service redeploy | yes |
| 3 | `agents/team_desk.py:1053` (`body.get("status") not in DEAD`) and `:1098–1102` (`_still_open` returns True when the offer is unseen). #262 fixed only the `offer_not_open` loop (:1042–1049) | A successful cancel returns `{"cancelled": id}`, which the desk reads as alive. Every concession costs a futile cancel and goes out **2 ticks late** | Treat `body.get("cancelled") == old` as dead (refund, clear, post in the same tick); treat an unseen offer as gone. Test: a fake cancel returning `{"cancelled": id}`, then a `say` in the same tick | ~5 lines + 1 test | yes |
| 4 | `agents/duel_days.py` `evidence()` (:112) uses one global sign; a `conflict` "stays for good, every session" (:23–24) and is persisted in `<data_dir>/duels/days_sign.json` (:329, :339). `BAZAAR_DATA_DIR=/app/.local` is a **volume** on bazaar-duels (`.railway/railway.py:23, 54, 251, 279`) | Duels II: 372 of 372 offers went out with `days = 0`, ≈ 98 P of result was left, and duel 5730 was lost. **New here:** dealing says a redeploy re-arms the latch, but the code keeps Saturday's `conflict` on the volume. So Duels III will likely run worst-case days again, even if rec 5's code ships | Dealing rec 5 (a role-aware sign per duel) **plus** resetting the latch file on the duels volume, or ignoring a conflict from an earlier session. Do not set `duel_days_signed` (:80) by hand: one global sign is wrong for buyers | Medium; duels redeploy before Duels III | Code yes; **the live file's content was not checked** |
| 5 | Taker ladder plan / `agents/dealer_plan.py`, against the `official_value_margin` check | Ladder tops are planned above the cap, so 23 Pícaros RET threads and 6 Abuela RET-02 threads were dead on arrival and spent dealer patience and quota | Dealing rec 4: top = min(top, official value); skip a card whose capped top is below the dealer's lowest fill | Small | yes: `dealer_plan.py` has no `official_value` reference |
| 6 | `cards_heartbeat.py:84` (`visible … and not c.get("hidden")`) | A newly found secret card raises no `new_card` alert | Use the `eggs.sql` §4 watch for now | 1 line | yes |
| 7 | `strategy.py:786–792` `pack_ev` values a pack at `keep_value` (private value), not at score | A Jev or LLM yes could buy packs that score 0. Saturday had 0 pack buys | `max_packs_per_game_hour` 0 (item 5) | config | yes |
| 8 | Minor | `venue_keeper.py:388` posts notices during benches (no bench hold; 3 on Saturday, harmless). Main has no `BAZAAR_BENCH_EDGE_CONFIRM`, so `edge` goes live on the next redeploy if set. `no_buyback_ticks` 480 counts ticks (2 h at 15 s, not 4). `ledger_pg.py:70` sets only a server-side `statement_timeout` | as each report says | small | yes |

Collections' "main lacks #255's review fixes, so the taker half of `buy_targets` is off" was **not** re-verified here.

## 5. Decisions only Marius can make

1. **Decider env on bazaar-taker** (item 1), and the fallback if the 20-tick check fails: (c), or
   `team_swap_jev_gate=false`. Decide **by 08:40**.
2. **Team desk** on or off; if on, merge the two-line fix (§4 row 3). Also `buyer_rank_enabled` and the spend cap at
   500. All of these go in the one GUARDRAILS/code batch, **by 08:30** (so it lands by 08:45).
3. **The official-value cap on dealer bids:** keep it and clamp (dealing rec 4), or lift it for ladder slots
   (quiet rec 3) and CHA (B-CHA +2). Also flips (dealing rec 7, against `off_page_min_surplus`). Any of these is code.
   Decide **by 08:30**, or leave them for later.
4. **Collections' change A** (~250 lines, a deploy-day risk). Without it, the taker buys all of Chamberí from dealers
   and the bonus is lost. JUMP: merge **by 08:45** or drop it (CHA releases at 09:00). RESUME: the 10:44–12:12 gap.
5. **Addressed offers** (§4 row 2): the code (a), `--accept-bids` (b), both or neither. Before 08:45, or in a later
   gap.
6. **Duel-days fix + latch reset** (§4 row 4). Deadline: JUMP 10:50 (the 09:40–10:55 gap); RESUME 14:05.
7. **Probe:** the manual rule only, or also the code fix (R3b) by 08:45. Ask the organisers in person at 09:00 whether
   a match inside both hidden limits is accepted, and whether a team may run two venues.
8. **Game writes that need your approval:** revoke MAL-11 (09:00), approve LAV-11 (after the CHA spend), the hand L3
   sells (round 3), and the R2 invite thread (text and target team; mm-probe suggests 09:05). Each before its time.
9. **Eggs:** yes or no; who sends; which dealers. Logs-eggs' order: Pícaros, then Abuela, then Chato only inside a
   priced move. Do it in any quiet minute, the earlier the better, because finds are capped.
10. **v19:** keep it (recommended). Close it only if the organisers confirm the free stall comes back.
11. **Who set `BAZAAR_SIM` at 13:27 on Saturday,** and was it on purpose? Answer **before the 08:40 check**.
12. **Pitch** (judges ≈ 40 %): which lead to use, and keep mm-probe §6.1's "do not claim" list. Decide before the
    presentation.

## 6. Easter eggs (logs-eggs §5)

An egg fires when a team's message to a dealer contains a secret phrase (accents and case ignored), once per team,
with a cap on total finds. Eggs score 0 (RULES.md:122); badges show on the public board and the big screen. Phrases,
inferred from the dealers' public replies: Abuela "la chulapa dorada" (badge Sharp ear), Madrid lore such as "sile,
nole, repe, me falta", the chotis on one tile or her saint's day (Castizo), "cocido con sus tres vuelcos" (one of her
duplicates); Los Pícaros "Lazarillo", "Rinconete", "el timo de la estampita" (Trickster tricked); Chato "Plaza Mayor,
con caña" (a barrio pack); Don Ernesto "el oro de Moscú" gave **LAT-13**, the one-copy hidden card, now t02's (spent).
Chamberí's lore (Andén 0, El Tren Fantasma) may hide a Sunday secret card, which appears in `/api/catalog` only after
its first find. Watch with `logs-eggs/eggs.sql` §1–4 through `q.py` (read-only). **Triggering an egg is a game write**
(a dealer thread and a message), so it needs Marius's approval (decision 9).

## 7. Private companions (outside the repo, never commit)

Exact per-deal neg_points, our limits and floors, `your_value`s, cash, holders, epic caps and MAL-11's approval figures
are in `/Users/mariusserban/orca/workspaces/bazaar/_sat-review/dealing-PRIVATE.md` and
`/Users/mariusserban/orca/workspaces/bazaar/_sat-review/private/` (`collections-private.md`). Never paste them into a
commit, PR, slide or public channel.

## 8. Not verified in this README

- The live content of the duels days-latch file (Railway is not linked in this worktree, and this README did not link
  it).
- Collections' #255 claim (§4).
- The reviews' final ACCEPT for quiet, mm-probe and collections. Their re-check text ends in REVISE, and the ACCEPT
  comes from the orchestrator's statement.
- Which clock scenario happens (item 2 decides).
- Every expected-points range, which comes from the reports and rests on their stated calibration (round weighting
  × 0.6 is inferred).

## Sunday reports

Written during Sunday (4 Oct) by separate sessions, each self-contained with its own sources:

| Report | Question |
|---|---|
| [bench-books.md](bench-books.md) | Why `bench_books` is empty in prod (recorder deployed after the last Market Test) |
| [bench-search.md](bench-search.md) | A lookahead broker policy that beats the free stall on the Market Test, in simulation |
| [bench-sim.md](bench-sim.md) | Calibrated simulation of the Market Test against the free stall |
| [card-hunt.md](card-hunt.md) | What moves our score on Sunday, and why dealer buys bring no negotiation points |
| [dealing-fixes.md](dealing-fixes.md) | Code fixes for three dealing defects found in the Saturday review |
| [duel-days-fix.md](duel-days-fix.md) | Duels days latch stuck at `conflict`: root cause, fix, reset |
| [egg-hunter.md](egg-hunter.md) | The Easter-egg hunter: how eggs fire and how the agent looks for them |
| [market-making-dossier.md](market-making-dossier.md) | Market making and the Market Test: everything the team measured, with sources |
| [retry-loop.md](retry-loop.md) | A guardrail refusal retried 99 times (picaros RET-09), and the fix |
