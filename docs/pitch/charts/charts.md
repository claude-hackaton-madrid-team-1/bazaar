# Sunday pitch charts: data specs (Team 1, The Bazaar)

Read-only extraction, 3 Oct 2026 (night). Every file sits next to this one. Branch paths are `origin/<branch>:<path>` in
`claude-hackaton-madrid-team-1/bazaar`, read with `git show`. Nothing here came from the live game.

Naming used throughout: **v1** = PR #60's `duelist.duel_move` (counters every tick). **v2** = W2b's `duel_v2` (PR #86).
B11's tables call v2 **`today`**. **endgame accept** = the silent "accept the best offer at the end" baseline.

Privacy: no `your_limit`, `your_offer`, `rival_offer`, duel `result` or `price`, card values, set affinities or album gaps
appear in any file. Duel ids, roles and round counts are kept (ids are public in `duel.closed` events). Chart 2's replay is
given as policy totals only. Our own official score (chart 6) is used as allowed.

---

## 1. The duel decay rule: every round of talk costs a share of the pie

- **Claim:** each round of talk keeps only 0.94 / 0.92 / 0.90 of the pie, and our Friday deals spent 1–9 rounds (mean 6.0), so we kept 57–94 % of what we had already won.
- **Type:** line chart (3 decay curves), with the 8 Friday deals as dots on the 0.94 curve and 2 vertical reference ticks (v1 6.85 and v2 1.23 rounds/deal in the zoo).
- **x:** rounds of talk (count, 0–16). **y:** share of the pie kept = (1 − decay)^rounds (0–1, show as %).
- **Data:** `01_duel_decay.csv`. `series` = `curve` | `friday_deal` | `reference`. Curves are computed (0.94^r, 0.92^r, 0.90^r for r = 0..16).
- **Source:**
  - Rule: `night/w2a-duel-zoo:docs/night/w2a-duel-zoo.md`, "Harness verdict" table, row "Real scoring rule": `rounds = min(our priced msgs, rival's)` on 26/26 payloads; `result = surplus × 0.94^rounds` on 8/8 deals.
  - Friday rounds: `tests/fixtures/evals/duels_done.json` (`GET /api/duels?done=true`, practice session 1, team t01), the 8 rows with `status = deal`, field `rounds`, `decay_per_round` = 0.06. Rounds per deal: 85: 7, 86: 8, 96: 7, 202: 9, 267: 1, 268: 5, 273: 4, 274: 7.
  - Reference ticks: `night/w2a-duel-zoo:docs/night/w2a-duel-zoo-tables.md`, "Tournament on the zoo", column `rounds/deal` (v1 6.846, v2 1.226).
  - Session decays for annotation: `night/b6-saturday-playbook:docs/night/saturday-schedule.json`, `events[].params.decay`: Duels I 0.06, Duels II 0.08, Duels III and Final duels 0.10.
- **Caveats:** Friday's duels were practice (unscored). Only the 0.06 curve has real points; 0.08 and 0.10 are the Saturday/Sunday session decays from the schedule. A no-deal keeps 0, not shown here. Duel 267 closed on our first offer (1 round).

## 2. Duel policy v1 vs v2: talk once, keep more

Two panels.

### 2a. Lift by rival style and by decay
- **Claim:** v2 earns 1.42–1.55× v1's points per duel at every decay pair (one duel at a time), and still 1.33–1.45× when 6 duels share a deadline and the team gets one accept per tick.
- **Type:** grouped bar chart (v1 vs v2 mean P per duel by rival style), plus a small dot/bar panel of the lift v2/v1 by decay pair with a dashed line at the 1.40 gate bar.
- **x:** rival style (7) or decay pair. **y:** mean points (P) per duel; or lift (ratio, ×).
- **Data:** `02a_duel_v1_v2_lift.csv` (`group` selects the panel: `by_rival_style`, `all_styles`, `by_decay_12_ticks`, `by_decay_16_ticks`, `gate_lift_one_duel_at_a_time`, `batch6_lift_one_accept_per_tick`).
- **Source:** `night/w2a-duel-zoo:docs/night/w2a-duel-zoo-tables.md`: "Tournament on the zoo" (all styles), "By rival style", "By decay and duel length", "Go/no-go: v2 vs v1" (three decay pairs). Batch-of-6 lifts: `night/w2b-duel-v2:docs/night/w2b-duel-v2.md`, "Go/no-go" table, row "W2a's batch runner (6 duels per deadline, 1 accept/tick, plan_moves)".
- **Caveats:** the zoo is a model of rivals (7 styles fitted to the 26 practice duels). v2's knobs were tuned on both zoos the same night, so the lifts are in-sample (W2b's own caveat: 0.06/0.08 went 1.388 → 1.420 that way). `no_show` rivals give 0 to both. If we move before the rival within a tick, the 0.06/0.08 lift falls to 1.34. Per-style rows are pooled over decays 0.06/0.08/0.10 and 12/16-tick duels (n = 2,400 each).

### 2b. The 12 practice duels we never answered, replayed
- **Claim:** on the 12 duels we left unanswered on Friday (0 P), v2 would have made 178.4 P against v1's 121.7 P, out of 195 P possible.
- **Type:** horizontal bar chart, one bar per policy, with a reference line at the oracle 195 P and the actual 0 P.
- **x:** points (P), total over the 12 duels. **y:** policy.
- **Data:** `02b_duel_replay_12.csv`. Plot `replay_P_one_clock_one_accept_per_tick` (the realistic rule); `replay_P_one_clock_no_cap` shows why silent endgame-accept is not the answer (185 → 140 under the cap). `replay_P_per_duel_table_conservative` is W2a's per-duel table (v2 there = `single_duel_move`, 173.5): do not mix it with the one-clock columns.
- **Source:** `night/w2a-duel-zoo:docs/night/w2a-duel-zoo-tables.md`, "One accept per tick for the whole team", second table ("The 12 unanswered practice duels replayed on one clock"), and "Replay on the 12 unanswered practice duels" (conservative P). Same 178.4 vs 121.7 in `night/w2b-duel-v2:docs/night/w2b-duel-v2.md`, Go/no-go table, row "Replay".
- **Caveats:** a counterfactual replay with the rival's real messages, conservative reading. It assumes a silent rival's standing offer stays acceptable (unverified on the real API). The planner order was chosen partly on duels 5, 6 and 201 of this replay, so it is not fully out of sample. v2's 178.4 is `plan_moves` on one clock (same with or without the cap); 173.5 is `single_duel_move` in the per-duel table: different code paths, kept in separate columns.

## 3. Exploiter rivals: who gets squeezed

- **Claim:** against a rival that reads our limit, v1 keeps only 7 % of the pie and silent endgame-accept 2 %, while v2 keeps 16 %, and v2 leaks its limit half as often (0 % readable by tick 3 vs 96 % for v1).
- **Type:** grouped bar chart. Groups = rival (honest zoo, squeezer, oracle squeezer, oracle that never backs off); bars = policy (v1, v2, endgame accept; optionally the eg1_share05 mitigation in a muted colour).
- **x:** rival. **y:** score = share × kept (fraction of the pie after decay, 0 for no deal). Secondary (tooltip or a second small panel): `pie_share_in_deals` and `deal_rate`. Leak panel (small bar or table): `readable_by_tick_3/6/9` = share of honest-zoo duels where our first k ticks hold ≥ 2 priced offers to extrapolate our floor from (0–1); `median_floor_error_*` = |estimate − limit| / limit among those (an aggregate error, no limit value).
- **Data:** `03_exploiters.csv`, long format: filter `metric` (`share_x_kept`, `pie_share_in_deals`, `deal_rate`, `mean_P`, `readable_by_tick_k`, `median_floor_error_by_tick_k`).
- **Source:** `night/b11-exploiters:docs/night/b11-exploiters-tables.md`, Part 1 (decays 0.08/0.10), "Our share of the pie against each rival" (rows honest zoo, squeezer, oracle_squeezer, oracle_squeezer never backs off). Leak figure: same file, Part 1 "What our offers reveal about our limit". Narrative: `night/b11-exploiters:docs/night/b11-exploiters.md`, Findings 1–2 and 5–6.
- **Caveats:** the story is in the deal rate: v2 closes only 0.70 of duels against the squeezer (the squeezer's last probe falls outside our limit), which is why the score is share × kept. Silent endgame-accept scores 0.80 against the least-squares squeezer, because that squeezer extrapolates from our offers and silence gives it none: show it, but it is the oracle column that matters. The per-rival table is a single seed (n = 800 per cell); the 3-seed means are only in B11's summary table (v1 0.111, v2 0.193, endgame 0.285 over the exploiter mix). These rivals are models. eg1_share05 is W2b's mitigation (PR #103), not shipped; it does worse than v2 (0.125 vs 0.159) against an oracle that never backs off.

## 4. Ladder: Abuela uncommons, today's ladder vs the W3 plan

- **Claim:** starting Abuela uncommons at her median floor (21 → 25 instead of 17 → 26) lifts our share from 0.84 to 0.95 in the model and from 0.80 to 0.97 on real replays, at a 0.99 deal rate.
- **Type:** dumbbell (or paired bar) chart per class, today → W3, with a marker for what real teams got on Friday. Headline: the abuela uncommon row.
- **x:** share of the price range captured = (opening − price) / (opening − that conversation's limit), 0–1. **y:** dealer class (abuela uncommon, common, pack; optional chato rows).
- **Data:** `04_ladder.csv` (model share, deal rate, fill within 8 ticks, fill at 2 ticks per round, replay share at the top and bottom of each limit bracket, real teams' share).
- **Source:** `night/w3-ladder:docs/night/w3-ladder.md`, "Backtest: the real decide() (#61) against dealers fitted to these threads" table. Fitted on 273 public dealer threads (all teams, Friday ticks 0–159).
- **Caveats:** shares are scored against each thread's secret limit, bracketed by what the dealer countered and took (the organisers' "price range" is unknown). Chato rows rest on 6 and 9 closed threads and need cap changes (today's caps block them). Commons are already at the ceiling (0.98 → 0.975, no gain). The #55 simulator's Abuela waits 10 rounds before her final (Friday's real one waited 5), so the simulator shows no gain from starting high. Pack rows needed `max_price_pack` lifted above 20 (its value when this was written). On Sun 4 Oct it is 600, but `max_packs_per_game_hour` = 0 now blocks every pack purchase (`GUARDRAILS.md`).

## 5. Market Test: how much better than the free stall can a broker be?

- **Claim:** in the default bench world no broker beats the free stall by much (stall 0.83, edge 0.83, even the clairvoyant oracle 0.91 p50), but our edge broker reaches 0.86–1.00 when the book is thick or wide, and with the limit probe it is worth up to +2.35 final points on Saturday.
- **Type:** two panels. (a) Dot plot / grouped bars: efficiency p50 by bench world (rows) for stall, greedy, exact, edge, edge_limit, oracle. (b) Bar chart: Saturday final-point gain over the free stall by bench world for exact keeper, edge, edge + limit probe.
- **x (a):** bench world (preset × rule × arrivals/shade). **y (a):** efficiency = realised gain / possible gain at true limits (0–1). **x (b):** bench world. **y (b):** final game points vs the free stall's 3.00.
- **Data:** `05_market_test.csv`, long format: `metric` ∈ {`efficiency`, `session_points`, `final_points_vs_free_stall_3.00`}, `stat` ∈ {`p50`, `mean`}. Filter on both.
- **Source:**
  - Efficiency and session points: `night/w1b-broker-edge:docs/night/w1b-broker-edge.md` (branch only: #84 was closed, so this report is not on main), "Evidence: W1a's bench, 1,000 books per row, p50 efficiency" (12 worlds). Greedy and exact rows: same file, under "Evidence: own bench": "Greedy (the starter broker) and exact (#71) equal the stall in every cell of both tables (identical p50 and mean, ±0.001)". They are written as explicit rows equal to the stall.
  - Oracle p50 (default worlds only): `night/w1a-bench-sim:docs/night/w1a-bench-sim.md`, "Evidence" table (normal/hard × quote/limit: 0.91 / 0.93).
  - Saturday points: `night/b2-venue-runbook:docs/night/b2-venue-runbook.md`, "What the venue is worth on Saturday" (jump clock, 8 sessions, 500 simulated Saturdays, two stall-level rivals).
- **Caveats:** W1b's oracle column is a **mean**, the other policies are **p50**: plot the oracle p50 where it exists (4 default worlds) and label the rest as mean. No real Market Test had been observed; arrivals, shading, relax and the match rule (`quote` vs `limit`) are assumptions, and the headroom moves ~10× across them. The edge loses 5–10 % of sessions to the stall. Session points use W1a's reading of RULES.md (stall 0.5, any edge above a stall-level field 1.0). B2's figures also assume W5's 15/15 bench/venue split. The keeper as #71 ships it (exact) earns exactly the stall's points. *Superseded in part, Sat 3 Oct evening: the real game agrees. Our board venue v19 with the exact broker scored exactly the stall's 0.5 bench in every Market Test so far (efficiency 0.878 to 0.933 all gave 0.5), and no team shows more than 0.5 (`docs/briefing.md`, "Our own market"). Market making per round is about 22.5 x bench points + 7.5 x organic value.*

## 6. Score model: we can predict the official board

- **Claim:** a fitted model of the board formula tracks our official score from 10.76 to 8.34 over Friday's last 38 ticks with RMSE 0.34, and out of sample (fitted on ticks < 140) predicts the final 8.34 within 0.47; the whole drop is other teams' Chato deals.
- **Type:** step-line chart: official vs model (in-sample) vs model (holdout), optionally a flat dashed line "model without level 2" (10.5) to show what others' Chato deals cost us.
- **x:** game tick (122–159; Friday, 60 s ticks). **y:** our negotiating score (board points).
- **Data:** `06_score_model.csv` (per tick); `06_score_model_stats.json` (RMSE and max errors).
- **Source:** official series = `night/w5w6-score-redteam-morning:tests/fixtures/evals/friday_score.json`, key `ours` (our `/me` `score.negotiating`). Model series computed by running `night/w5w6-score-redteam-morning:src/bazaar_agent/evals/score_sim.py` (`ScoreModel()` defaults; `fit_level2_weight` on ticks < 140 for the holdout; `our_negotiating` per tick) on that fixture, from a temporary `git archive` copy of the branch (since deleted). Reported figures match `night/w5w6-score-redteam-morning:docs/night/w5w6-score-redteam-morning.md`, section 1, "official / model" table.
- **Stats:** in-sample RMSE 0.336 over 38 snapshots (max error 0.57, tick 135). Holdout (level-2 weight refitted on ticks < 140 → 0.58): RMSE 0.343 and max error 0.47 on ticks 140–159; 7.87 vs 8.34 at tick 159. In-sample at 159: 8.26 (−0.08).
- **Caveats:** the formula is fitted, not published (top-3 normalisation, ladder weight 12.5, level-2 weight 0.5). The board only refreshes every 5 ticks, so the model steps; the 38 snapshots are 8 distinct refreshes. The 38 points are not independent. A second check, the tick-30 public board (18 teams), has MAE 0.47 (not plotted here; in the report).

## 7. Red team: hostile words never move a binding field

- **Claim:** 168 prompt-injection cases across 13 code paths changed 0 binding fields: no price, accept, asset or limit moved.
- **Type:** horizontal bar chart of cases per path, with a single "0 binding changes" annotation (or a 0 column).
- **x:** test cases (count). **y:** code path.
- **Data:** `07_red_team.csv`.
- **Source:** `night/w5w6-score-redteam-morning:docs/night/w5w6-score-redteam-morning.md`, section 2 "Red team, prompt injection (#24)", the Path / Cases / Result table (`tests/test_redteam_injection.py`).
- **Caveats:** **the current count is 168, not 129.** 129 was the count at commits 22551c6–34f99b1, before the r1/r2 reviews added the desk tool-call cases, the digit-free payloads and the "settled / are yours" case (152 at a611bd7, 168 at 6ab5b1e, the branch head). Deterministic paths only: whether a live LLM obeys hostile text is not testable offline; what is covered is that the guard hook denies every harmful tool call it would make. One known gap: `sell_cancel` on any offer id passes the hook (moves no value). Planted bugs fail 34 of 48 selected cases (the tests bite).

## 8. The Saturday clock: superseded jump-or-resume forecast

- **Claim:** whether the frozen clock jumps to game hour 4 or resumes at 2.65 moves every Saturday event by 1 h 21 min and drops Saturday's Market Tests from 8 to 7.
- **Type:** dumbbell / two-lane timeline: one row per event, a dot at the jump wall time and one at the resume wall time (bars for the duel sessions' start–end). Colour by action (bench, duels, grant, round).
- **x:** wall-clock time (Sat 3 Oct 09:00 → Sun 4 Oct 15:00, CEST). **y:** event (ordered by game hour).
- **Data:** `08_saturday_clock.csv` preserves the original forecast, with later events labeled `[superseded forecast; see charts.md section 8]`. Its game hours and wall times are not the current schedule; the old `status` columns describe only that forecast.
- **Source:** `night/b6-saturday-playbook:docs/night/saturday-schedule.json`, `events[].slots.jump` / `.resume` (built from the organisers' `/api/schedule` and `/api/clock`; anchors: jump t = 4.0 at 09:00, resume t = 2.65 at 09:00).
- **Caveats:** historical Saturday planning evidence only, not observed event times or a Sunday timetable. The clock resumed at h2.65 and round 2 started at tick 160, but organisers moved events during Saturday. The venue keeper now opens at `venue_open_after_game_hours` in `GUARDRAILS.md` (3.0), and v19 opened around h3.6.
- **Sunday correction, 4 Oct:** the live [schedule](https://bazaar.causaprima.ai/api/schedule) anchors "Sunday opens" at h16.65, 09:00 CEST, with 15 s ticks. One game hour equals one real hour on Sunday. The following quoted entries supersede all Sunday predictions in the CSV:

| Game hour | Sunday CEST | Schedule entry |
|---|---|---|
| 14.65 | Before open; firing time UNVERIFIED | "The hard Market Test: firmer and more impatient traders" |
| 15 | Before open; firing time UNVERIFIED | "The Market Test: every venue gets the same synthetic book" |
| 16.65 | 09:00 | "Sunday opens"; "Chamberí released"; "Round 3 starts" (ladder restarts) |
| 16.7 | ~09:03 | "The Sunday allowance: 150 primas for everyone" |
| 17 | ~09:21 | "The Market Test: every venue gets the same synthetic book" |
| 18.65 | ~11:00 | "Duels III: two issues, shorter clock, harder decay" (12-tick duels, decay 0.10) |
| 19 | ~11:21 | "The Market Test: every venue gets the same synthetic book" |
| 21 | ~13:21 | "The Market Test: every venue gets the same synthetic book" |
| 21.45 | ~13:48 | "finale warning" |
| 21.65 | ~14:00 | "Finale: stalls close" (all five dealers); "The Grand Final: the last duel wave, on the big screen" |
| 22.55 | ~14:54 | "freeze warning" |
| 22.65 | 15:00 | "Scores freeze"; "The Bazaar closes" |

The h14.65 and h15 Market Tests precede the h16.65 opening. Whether they fire at the open or are skipped is **UNVERIFIED**. Sunday's 16-tick Market Tests last four minutes.

## 9. (Optional) Friday's public market: where trades actually settled

- **Claim:** on Friday every one of 184 public settlements went through a dealer (140) or the free public board El Rastro (44); the four team venues opened that day settled none.
- **Type:** stacked bar chart (or stacked area) of settlements per 10-tick bucket by venue type.
- **x:** game tick, 10-tick buckets (0–149, Friday, 60 s ticks). **y:** settlements (count).
- **Data:** `09_feed_settlements.csv` (columns abuela, chato, rastro, team venues; last row = totals 124 / 16 / 44 / 0).
- **Source:** the public feed capture `lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.local/stream.jsonl`, events with `scope = public` and `type = settlement`; category from `payload.persona` (abuela/chato, venue null) or `payload.venue` (`rastro` vs a team venue `vNN`). `agent.me` and other team-scoped rows ignored. Team venues opened at ticks 100 (v01, t06), 113 (v02, t12), 129 (v03, t13 and v04, t02).
- **Caveats:** the capture covers ticks 0–149 only (Friday ran to 159); the shared Postgres `feed_events` has the full day but could not be reached from this sandbox, so it was not used. Chato only opened at tick ~98 (level 2). The 0 team-venue settlements matches B1's finding ("0 settlements on any team venue", quoted in `saturday-schedule.json` → `points.venue_organic.friday`). No Market Test had run by tick 149.
