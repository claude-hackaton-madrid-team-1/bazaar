# Market making and the Market Test (Team 1): start here

Everything Team 1 learned and built about market making, 2–4 Oct 2026: how it is scored, every session's result,
every policy we tried, the harnesses, the decisions and who took them, and what we can say in the pitch. The reports
in [`reports/`](reports/) and [`night/`](night/) are the evidence; this page is the summary and the index.

## TL;DR

- **Market making is 30 of the 100 game points**, split (fitted on `/me`, not published) into the **Market Test**
  (22.5 × `bench_points` per round) and **organic** trades between other teams on our venue (7.5 × organic).
- **The Market Test**: every two game hours every venue gets the same synthetic book of buyers and sellers with
  hidden limits. Score = share of the possible gains realised. Matching the free auto stall = **0.5**; the mean of
  the top three = **1.0**; the shape between and below is not published.
- **Sessions 1–8: we tied the stall every time** (efficiency 0.854–0.967, bench_points 0.5). So did every team with a
  working matcher; nobody ever beat the stall. t07 and t08 scored ~0 in session 8 (board venues that matched nothing).
- **Why we tied**: our `exact` broker sends the same pairs the stall crosses (replayed on session 8's recorded book:
  same 4 pairs, ticks and prices). One deliberate probe (session 7, tick 1692) showed the server refuses any pair
  whose posted quotes do not cross (`400 bad_match`). So the only lever is **which** crossing traders to match and
  **when**.
- **What we built**: a recorder for the real bench books (sessions 7–8), a simulator fitted to them, and two
  look-ahead brokers that plan over sampled futures. Best in simulation: `lookahead_safe`, E[bench_points] 0.67 vs
  0.50 for `exact` (P above the stall 0.35, P below 0.15).
- **Session 9 (12:37, the last bench) ran `lookahead_safe`** (PR #292, Marius's choice) and finished **slightly below
  the stall**: `/me bench_points` 0.500 → 0.472, efficiency 0.841, board market 9.00 → 8.75. Two moments explain it: a
  maker tick skipped on a `/me` timeout (2262) and one deliberate deviation that lost (2264). See [Session 9](#session-9).
- **Organic: 0 trades on v19 all weekend.** Other teams' routers posted on venues that opened earlier; nobody ever
  bid on v19.

## How the Market Test is scored

| Item | Value | Status | Source |
|---|---|---|---|
| Split of the 30 | per round: market = **22.5 × bench_points + 7.5 × organic** | fitted on `/me` and the board, Sat and Sun | [bench-baseline Q1](reports/bench-baseline.md) |
| Efficiency | share of possible gains between the traders' **hidden limits** (`/me score.bench_efficiency`) | rules + `/me` | RULES.md:78–82 |
| Stall baseline | the free `auto` stall crosses best ask vs best bid every tick at the midpoint; matching it = **0.5** | published | RULES.md:82 |
| Full points | 1.0 = mean efficiency of the top three venues in the session | published | RULES.md:82 |
| Between stall and top three | no formula; linear is our reading. Nobody above the stall ⇒ a lone team above it is its own top three ⇒ 1.0 | unknown | bench-baseline Q2 |
| Below the stall | Saturday fit 0.5 × E/E_stall (t03, t13); session 8: a board venue with no matches ≈ 0 (t07, t08). Both fit the zero-match case | **unknown** | dossier §1.2 |
| Server check | `POST /api/broker/matches` checks the **posted quotes**: a non-crossing pair is `400 bad_match` | measured once, t1692 | [benchmarks §6](benchmarks.md#6-live-match-probe-bazaar_bench_match_probeonce-pr-263) |
| Who counts | each session counts our **best venue open** during it; none open = 0; a broker acts only on a `board` venue | published | RULES.md:80–81 |
| Value of one session | Sunday's round averages sessions 7, 8, 9: one session 0.5 → 1.0 = +3.75 on Sunday's market round ≈ **+1.5 final points** | derived | bench-baseline Q1 |
| Organic | `/me score.mm_points` = value created between other teams on our venue at their private values; Team 12's fix (Sunday) stopped underpriced sales counting against the venue | rules + organisers' talk | dossier §5 |

## Sessions 1 to 9

Efficiency and bench_points from `/me` (`me_snapshots`) after each session. Data file: [sessions.csv](sessions.csv).

| # | Run | When (start tick) | Venue / policy | Our pairs (quoted surplus) | Efficiency | bench_points | vs stall | Others |
|---|---|---|---|---|---|---|---|---|
| 1 | – | Sat h3 (201) | v08 starter stall (auto): the engine crossed | 0 | 0.899 | 0.5 | tie (we were the stall) | nobody above |
| 2 | b35 | Sat h5 (441) | v19 board, `exact` | 4 (161) | 0.933 | 0.5 | tie | nobody above |
| 3 | b52 | Sat h7 (681) | v19, `exact` | 5 (85) | 0.878 | 0.5 | tie | nobody above |
| 4 | b69 | Sat h9 (921) | v19, `exact` | 6 (119) | 0.891 | 0.5 | tie | nobody above |
| 5 | b87 | Sat h11 (1161), 19:55 | v19, `exact` (`edge` was on 18:37–19:37 and 19:45–19:52; a 19:52 change set `exact`) | 6 (165) | 0.886 | 0.5 | tie | nobody above |
| 6 | b103 | Sat h13 (1401) | v19, `exact` (the hand probe was never sent) | 4 (29) | 0.854 | 0.5 | tie | nobody above |
| 7 | b120 | Sun 10:16 (1690), hard, unscheduled | v19, `exact` + one-shot match probe (refused) | 7 (178) | 0.967 | 0.5 | tie | all 18 teams 0.5 |
| 8 | b137 | Sun 10:37 (1774) | v19, `exact` | 4 (97) | 0.895 | 0.5 | tie | 16 teams 0.5; t07, t08 ≈ 0 |
| 9 | b155 | Sun 12:37 (2254), last | v19, **`lookahead_safe`** | 6 (101) | 0.841 | **0.472** | **below** | 13 teams 0.00 on the board; nobody visibly above |

Notes:
- Saturday's `/me` efficiency may be the running round average rather than the session's own (mm-probe gives a
  "reading B" column: 0.899 / 0.967 / 0.768 / 0.930 / 0.866 / 0.694). bench_points is 0.5 either way.
- Saturday "nobody above" is read from the end-of-round board: every market score above 7.50 belongs to a venue with
  organic trades ([mm-probe §2.3](reports/mm-probe.md)). Sessions 7–8 are per-session leaderboard deltas
  ([bench-baseline](reports/bench-baseline.md), [dossier §4.5](reports/market-making-dossier.md)).
- Session 8's quoted surplus is 97 (the per-tick log lines), not the 99 in bench-baseline.

### Session 9

Run b155, ticks 2254–2269, 19 traders seen (10 sellers, 9 buyers; buyer b155-2 never seen). Sources: `bench_books`,
`bench_evidence`, `decisions` ids 6453–6486, the maker log (`Market Test b155 over (ticks 2254–2270): 6 pair(s),
quoted surplus 101, 0 refused`), `me_snapshots`, `leaderboard_snapshots`; analysis in [bench-v3](reports/bench-v3.md).

| Tick | What happened | Effect |
|---|---|---|
| 2255 | `lookahead_safe` held S11 (43) × B4 (49) one tick ("score 0.552 against 0.500, 0 pairs") | neutral: the same pair matched at 2256 |
| 2256, 2257 | S11 × B4 at 45; S18 (47) × B0 (64) at 55 | = stall |
| **2262** | **maker tick skipped**: `read refused network (GET /api/me: The read operation timed out); nothing sent`. No book read, no `bench_books` rows | buyer b155-2 (the only trader never seen) was probably alive only this tick; the stall would have seen it |
| 2263 | S10 (29) × B9 (73), S14 (35) × B7 (59), S12 (39) × B8 (48) | = stall's set |
| **2264** | **deviation**: S15 (50) × B6 (50) instead of the stall's S13 (46) × B6, keeping relaxing S13 for rising buyers B5/B3 ("score 0.573 against 0.500") | lost: S13 left after 2266 at 41, never crossed |

- **Result**: `bench_points` 0.500 → **0.472**, `bench_efficiency` 0.895 → 0.841 (`me_snapshots` tick 2270). Board market
  9.00 → 8.75 (−0.25) between ticks 2262 and 2282 while 13 teams moved 0.00; t07 / t08 +0.58 / +0.68 (back to the
  stall after ≈ 0 in session 8); t05 / t12 small rises mixed with organic; t15, t04, t10 also fell.
- **Per session or round average?** −0.25 on the board fits `/me bench_points` being the Sunday round average (session 9
  alone ≈ 3 × 0.472 − 1.0 = 0.416); a per-session 0.472 would move it ≈ −0.07. bench-v3's replay cannot separate the two.
  Cost: ≈ −0.1 final points.
- **Attribution (bench-v3 replay, in-sample)**: our six pairs score 0.727 vs the stall's 0.830 (P(below) 0.93). Giving the
  stall's choice at both 2262 and 2264 closes 84 % of the gap; neither alone does. Only `lookahead_bold` would have
  beaten the stall on this book (P(above) 0.61).
- **Fixes, not shipped** (`src/**`, after the game): the maker should run the broker even when its `/me` read fails; and
  the planner's "keep a relaxing seller for rising buyers" bet needs a life prior that is not over-optimistic.

## What we tried

| Policy / idea | What it does | Live? | Evidence | Verdict |
|---|---|---|---|---|
| `exact` (default) | max quoted-surplus matching of crossing pairs, sent the tick they cross, at the midpoint | sessions 2–8 | 36 pairs, 0 refused; bench_points 0.5 in 7 of 7 | = stall by construction |
| `edge` (guard margin 10 / 5 / none) | re-picks **which** crossing traders match on estimated true surplus (#84 → #218) | on the maker Sat 18:37–19:37 and 19:45–19:52 (ticks 1009–1128, 1143–1156); h11 ran `exact`; never ran in a bench | sim 0.513 (margin 10), 0.570 (none); both real replays 0.500 | cannot move a real book |
| `probe` (#257) | exact + up to 6 non-crossing pairs a session, betting the server checks hidden limits | set on the maker Sun ~00:18, off again by ~00:56; never ran in a bench | its stop latch could never fire (mm-probe §2.1) | dead: the server checks quotes |
| Hand probe (`bench_probe_once.py`) | one non-crossing POST by hand | built for h13, never sent | no log, no transcript | superseded |
| Match probe (`BAZAAR_BENCH_MATCH_PROBE=once`, #263) | one non-crossing POST ever, durable claim | fired s7, t1692 | ask 40 × bid 39 @ 39 → `400 bad_match` | **settled: quotes, not limits** |
| Second "hedge" auto venue (`max_venues` 2) | an auto venue as a floor | never | no team ever ran two; our simulator refuses `venue_exists` | not tried |
| `bench_books` recorder (#261, #282) | stores the whole bench book every tick | from s7 | b120 61 rows / 24 offers; b137 59 rows / 20 offers | the data behind the simulator |
| Calibrated simulator (`bench_tournament.py`) | worlds fitted to b120 + b137 | offline | reproduces the real quote paths; #77's preset did not | the tool |
| `lookahead` (#292, `d4c5b328`) | 128 posterior futures a tick; picks the crossing matching with the best expected true surplus vs the stall rolled forward | merged in #292, not selected | sim 0.642 (P above 0.29, below 0.14); replays b137 0.899, b120 0.502 | strong on b137, flat on b120 |
| **`lookahead_safe`** (#292, `192f6ac0`) | 96 samples; scores a session below the stall as 0; leaves the stall's pairs only if that wins more often than it loses | **s9** | sim 0.668 (zero-below 0.601; P above 0.35, below 0.15); replays b120 0.806, b137 0.783 | deployed for the last bench |
| `lookahead_bold` (#292) | same, below-stall weighed at half the fitted rule | no | sim 0.71 linear but 0.46–0.57 zero-below | too much below-stall risk |
| Lookahead v2 (`feat/bench-lookahead-v2`, `af794985`) | #292's `lookahead` + time budget + `slack` | no | slack1 0.656 / 0.587; slack4 0.691 / 0.577 | documented alternative, not deployed |
| Organic: notices, "wanted cards" (#238), venue invite in swaps (#251) | draw other teams' trades to v19 | from Sun 09:00 | 0 trades on v19 all weekend | no effect measured |

The headroom is **timing**: in the calibrated sim an oracle that knows every departure beats the stall by +8 to +10 %
of possible gains, while one that knows every limit but never holds gains < 1 % ([bench-sim §2](reports/bench-sim.md)).
Harness commands and every number: [benchmarks.md](benchmarks.md).

## What won

Through session 8, nothing beat the stall: `exact` held the 0.5 floor in every session with the maker up, which is
what a working board venue earns (t07 and t08 show the cost of not matching: ≈ 0). The best policy we found offline
is `lookahead_safe` (sims) / #292's `lookahead` (the session-8 replay). Its one live session (9) finished slightly
below the stall, mostly from a skipped tick combined with one deviation ([Session 9](#session-9)). **Nothing we ran ever
beat the stall live.** After session 9, bench-v3 ranks `lookahead_bold` first under the linear reading (0.707) and
`lookahead_safe` first under zero-below (0.602); the maker was switched to `lookahead_bold` at 13:15 in case an
unscheduled test fires (none is on `/api/schedule`).

## Decision log

Times are Madrid local unless marked Z (UTC). "Who" is who decided or acted, from the logs; where the record does not
say, it says so.

| When | Decision | Who | Why / evidence | Source |
|---|---|---|---|---|
| Sat 3 Oct 02:08–04:16 (night) | Simulate the bench (W1a #77), build an edge broker (W1b #84), organic/win-rate study (B1 #94), venue runbook (B2 #92) and venue path (B20 #118). Verdict: "stall + 0.15" unreachable; `exact` = stall; organic no-go as a points source | night sessions, for Marius | oracle only +0.03–0.06 p50 over the stall in W1a's bench | [night/](night/), [notes-night.md](notes-night.md) |
| Sat 03:14–07:14Z | #71 (venue keeper, Omar): board venue at h6.5, exact broker. Merged 07:14Z with the venue switched off (team decision 06:08); v19 later opened at tick 262, board, 0 bps | Omar (#71), team | market is 30 points and ours was 0 | `docs/night/INDEX.md`, [mm-probe §2.5](reports/mm-probe.md) |
| Sat h3 (tick 201) | Session 1 runs on our free starter stall (v08) | – | no venue yet | [mm-probe §2.2](reports/mm-probe.md) |
| Sat 16:33Z | #218 (BE1) merges `edge` behind `BAZAAR_BENCH_POLICY` (default `exact`) and `BAZAAR_BENCH_GUARD_MARGIN` | team (port of Marius's #84) | ship the edge off by default | PR #218 |
| Sat 18:37 → 19:52 | `edge` (margin 5) went live with a variable patch at the #218 merge (18:37); **Marius approved `exact`** at 19:37 (tick 1125); a teammate set `edge` again at 19:45 (Railway does not show who) and Marius left it; a variable change at 19:52 set `exact`, and a #232 merge restarted the maker inside the h11 bench (tick 1166). h11 ran `exact` | Marius, a teammate | #218's proof scored edge 0.543 vs 0.500 points (margin 5 had the smallest worst regret); the risk was a redeploy inside a bench | `_night/MARKET_TEST_H11_READY.md`, `BENCH_BEAT_STALL.md` ([digest](notes-night.md)) |
| Sat night | Build a non-crossing `probe` to test whether the server checks hidden limits (the kit's own docstring hinted it might) | Marius / BENCH_BEAT_STALL session | the only idea left that could move a stall-equal book | `_night/BENCH_BEAT_STALL.md`, `MM_STRATEGY.md` ([digest](notes-night.md)) |
| Sun 00:17–00:41 (22:17–22:41Z Sat) | #261 (bench-book recorder) and #257 (`probe`, off by default) merge; bazaar-maker set to `probe`; #263 (one-shot match probe, ogarciarevett) merges | team | record the real books; test the rule | PRs #261, #257, #263; [bench-books](reports/bench-books.md) |
| Sun ~00:15–01:30 | mm-probe: keep `probe`, but its 8-refusal stop can never fire (6 a session cap) | sat-mm-probe session | code read + reviewer's scratch runs | [mm-probe §2.1](reports/mm-probe.md) |
| Sun ~00:42–00:56 | Maker back on `exact` (restarts after 22:42Z stop logging `bench probe`; 07:20Z: `bench exact` + `match probe ARMED`) | **not recorded** | – | [bench-books "Could not verify"](reports/bench-books.md), STATUS 09:28 |
| Sun 10:16, t1692 | The one-shot match probe fires in session 7: `400 bad_match`. **The server checks quotes.** `probe` is dead | automatic (#263) | ask 40 × bid 39 at 39 refused; one tick later exact matched the same two | [bench-baseline Q5](reports/bench-baseline.md) |
| Sun 10:46 | bench-baseline: keep `exact` for s9 unless the sim shows an edge; never `probe` | bench-baseline session | 8 ties; t07/t08 show below-stall ≈ 0 exists | [bench-baseline Q6](reports/bench-baseline.md) |
| Sun 10:55 | bench-sim recommends #292 `lookahead` (E 0.642, P above 0.29) | bench-sim session | calibrated worlds + replays | [bench-sim §5](reports/bench-sim.md) |
| Sun 11:34 | bench-search: `lookahead_safe` edges #292 in the sims under zero-below, loses the b137 replay; its own call: keep #292 | bench-search session | paired sign tests, replays split | [bench-search](reports/bench-search.md) |
| Sun ~11:34–11:40 | **Marius chose `lookahead_safe`**; `BAZAAR_BENCH_POLICY=lookahead_safe` set on bazaar-maker with `--skip-deploys`; #292 fast-forwarded to `192f6ac0` (lookahead + lookahead_safe/bold) | Marius | the record gives no stated reason; on the table: safe better in sims under zero-below (the main risk after t07/t08), b120 replay 0.806 vs 0.502, b137 0.783 vs 0.899 | `_sat-review/prompts/bench-deploy.md`, STATUS 11:40 |
| Sun 11:45 | #292 merged at tick 2045 via `scripts/merge_safe.sh` (after Duels III, before the ~12:25 deadline); maker log `bench lookahead_safe (posterior samples...)` verified 11:48; still on after the 11:51 and 11:54 redeploys | bench-deploy session (merge by serban-marius) | deploy guard; rollback = `BAZAAR_BENCH_POLICY=exact` | STATUS 11:45–12:04 |
| Sun 12:15 | v2 (`slack`, time budget) kept on its branch, not deployed | orchestrator | not reviewed for s9; `src/**` merge = 3 services redeploy | STATUS 11:19, 12:15 |
| Sun 12:37 | Session 9 runs `lookahead_safe`: 0.472, below the stall | – | tick 2262 skipped on a `/me` timeout; deviation at 2264 lost | [Session 9](#session-9) |
| Sun 12:50 | bench-v3: b155 replay, read downtime, three readings, every policy rerun (worktree on Sonnet 5.5, chosen by Jev 0.88) | mm-hub, Marius | learn from session 9 | [bench-v3](reports/bench-v3.md) |
| Sun 13:05 | Organic push: matchmaking notices on v19 every 20 ticks until 14:55 naming live near-misses between two other teams (first at tick 2399); one invitation thread to t03 (thread 3690, on El Rastro, closed after the message). Maker switch to `exact` declined | Marius | organic is the only market-making score left | STATUS 13:04–13:14 |
| Sun 13:15 | bazaar-maker `BAZAAR_BENCH_POLICY` → `lookahead_bold` (deploy 31bd6a51; log `bench lookahead_bold`) in case an unscheduled test fires | Marius | bench-v3: the only policy above the stall on b155 | STATUS 13:17 |

## What is still unknown

1. The points curve **below** the stall (0.5 × E/E_stall or 0) and **between** the stall and the top-three mean.
2. Whether the server's efficiency denominator is time-aware: the sim's stall sits at ~0.80 vs real 0.85–0.97, and
   the b137 replay's stall at 0.777 vs the server's 0.895. So replay **levels** are not trustworthy, only signs.
3. Whether a held trader can leave before a queued match settles (`settles_at_tick` = T + 1).
4. Whether `/me bench_efficiency` is per session or the round's running average (Saturday and Sunday both).
5. Whether lookahead's b137 gain was real: seller b137-12's life is censored by our own match.
6. Why `who_oracle` scores below the stall on the b137 replay (a harness check before trusting replay levels).
7. How other teams' routers choose a venue (why nobody ever posted a bid on v19).
8. Whether the server allows two venues per team (never tried; our simulator refuses).

## Pitch facts

No private values (card values, limits, cash) here or anywhere in this folder.

**Claims with evidence**

| Claim | Evidence |
|---|---|
| We read the scoring before optimising it: matching the free stall = half, top three = full | RULES.md:78–82; [bench-baseline Q1](reports/bench-baseline.md) |
| We scored exactly the stall's level in sessions 1–8 (session 1 on our free starter stall, 2–8 with our own broker: 36 pairs, 0 refused; efficiency 0.854–0.967), and nobody beat the stall. Our look-ahead broker's one live session (9) finished slightly below it; we can say why, tick by tick | `/me`; [sessions.csv](sessions.csv); leaderboard deltas (dossier §4.5) |
| We found out why with one deliberate experiment: a single non-crossing match, refused `400 bad_match`; the server checks quotes, so only timing and partner choice among crossing traders can help | t1692, `decisions` id 4264 |
| We recorded every bench book from session 7 and fitted a simulator that reproduces the real quote paths (two-bump sellers, relaxing quotes) | `bench_books`; [bench-sim §1](reports/bench-sim.md) |
| In that simulator an all-knowing broker beats the stall by +8–10 % of possible gains, almost all from **timing** | [bench-sim §2](reports/bench-sim.md) |
| We built posterior-sampling brokers and shipped one for the last session: E[bench points] 0.67 vs 0.50 in the calibrated world, P(above) 0.35, robust in 12 perturbed worlds (0.60–0.70) | [bench-search](reports/bench-search.md) |
| On session 8's real book, `exact` (= the stall) spent a cheap seller one tick before a buyer bidding 68–74 arrived and left unmatched: +22 P of quoted surplus available to a broker that waits | [dossier §4.3](reports/market-making-dossier.md) |
| A down or non-matching venue costs the whole half point (t07, t08 ≈ 0 in session 8); we kept v19 matching in every session and deployed only through a deploy guard outside bench windows | dossier §4.5; `scripts/merge_safe.sh` |

**Do not claim** (from [mm-probe §6.1](reports/mm-probe.md), updated with Sunday)

- That we beat the stall: we never did (sessions 1–8 tied, session 9 finished below).
- "First team above the stall": nobody visibly was.
- That the probe found an edge: it settled the rule the other way (the server checks quotes); `probe` never ran in a
  bench and its stop latch could never fire.
- "+4.5" or any limit-world EV from Saturday night: that world does not exist.
- "0.899 on the real session-8 book" as a real result: it is a mean over sampled hidden limits, and the replay's
  stall level is 0.12 below the server's.
- That the "wanted cards" notice or the venue invite brought trades: v19 had 0 organic trades.
- That venues fill "because of addressed deals" (v13 and v15 had hundreds and 0 trades).
- An organic credit as permanent: t12 and t07 lost theirs after later trades.

## Index

**This folder**

| File | What it is |
|---|---|
| [README.md](README.md) | This page: TL;DR, scoring, sessions, what we tried, decisions, unknowns, pitch facts, index |
| [benchmarks.md](benchmarks.md) | How to run every harness, with the headline numbers each produced and the real replays |
| [sessions.csv](sessions.csv) | Sessions 1–9, one row each (policy, pairs, quoted surplus, efficiency, bench_points), for charts |
| [notes-night.md](notes-night.md) | Redacted digest of the uncommitted Fri–Sun notes (`_night/*.md`, `_sat-review/review-*.md`, session prompts) |

**Reports** ([`reports/`](reports/))

| File | What it is |
|---|---|
| [market-making-dossier.md](reports/market-making-dossier.md) | The Sunday-morning dossier (PR #293): scoring, sessions 1–8, session 8 deep-dive, organic, pitch draft |
| [bench-baseline.md](reports/bench-baseline.md) | Did session 7 beat the baseline (no); the 22.5 / 7.5 fit; the dashboard bug; session 8 read after it ended |
| [bench-sim.md](reports/bench-sim.md) | Calibrated simulator, policy tournament, real-book replays, #292 `lookahead`; §7 the v2 addendum |
| [bench-v3.md](reports/bench-v3.md) | Session 9 into the harness: b155 replay, broker downtime, three points readings, what-we-did vs the stall, every policy rerun |
| [bench-search.md](reports/bench-search.md) | `lookahead_safe` / `lookahead_bold`, head-to-head with #292, paired sign tests |
| [bench-books.md](reports/bench-books.md) | Why `bench_books` was empty on Sunday morning (recorder shipped after Saturday's last bench) |
| [bench-books/e2e_local_pg.py](reports/bench-books/e2e_local_pg.py) | Replays captured broker books through the real recorder into a throwaway local Postgres |
| [mm-probe.md](reports/mm-probe.md) | Saturday's bench and organic evidence, Sunday's three-book plan (bench, organic, own quoting), pitch text and "do not claim" |
| [mm-probe/q.py](reports/mm-probe/q.py) | Read-only Postgres query helper (URL from a local `.env`, never printed) |
| [mm-probe/bench_stats.py](reports/mm-probe/bench_stats.py) | Per-session bench table from `decisions` and `me_snapshots` |
| [mm-probe/market_stats.py](reports/mm-probe/market_stats.py) | Saturday market medians by rarity (public, addressed, team trades) |
| [mm-probe/spreads.py](reports/mm-probe/spreads.py) | Close-of-day per-card spreads across venues |
| [mm-probe/evidence/bench_stats.out](reports/mm-probe/evidence/bench_stats.out) | Output: Saturday's 6 sessions (pairs, quote spreads, efficiency, bench_points) |
| [mm-probe/evidence/market_stats.out](reports/mm-probe/evidence/market_stats.out) | Output: market medians by rarity, team-to-team settlements by venue class |
| [mm-probe/evidence/spreads_close.out](reports/mm-probe/evidence/spreads_close.out) | Output: open offers at Saturday's close (1 two-sided card, 0 crossing) |
| [mm-probe/evidence/real_books_analysis.txt](reports/mm-probe/evidence/real_books_analysis.txt) | Near-miss pairs on the captured Saturday books b52 and b69 |
| [mm-probe/evidence/replay_real_books.txt](reports/mm-probe/evidence/replay_real_books.txt) | b52 / b69 replayed under the (later disproved) limit rule |
| [mm-probe/evidence/round_sim.txt](reports/mm-probe/evidence/round_sim.txt) | P(round above the stall) per sim world under the limit rule |
| [mm-probe/evidence/keyless_requests.log](reports/mm-probe/evidence/keyless_requests.log) | The 22 keyless GETs mm-probe made |

**Night shift, Fri 2 → Sat 3 Oct** ([`night/`](night/))

| File | What it is |
|---|---|
| [w1a-bench-sim.md](night/w1a-bench-sim.md) | W1a (#77): the first bench simulator, oracle headroom, the points-based go/no-go bar |
| [b1-organic-winrate.md](night/b1-organic-winrate.md) | B1 (#94): organic market as a points source (no-go) and a win-rate bench policy (cautious, default off) |
| [b2-venue-runbook.md](night/b2-venue-runbook.md) | B2 (#92): venue go-live runbook and simulated Saturdays (exact / edge / limit probe) |
| [b20-venue-path.md](night/b20-venue-path.md) | B20 (#118): open at 09:00 as insurance, board + edge or auto; not adopted |

**Elsewhere in the repo (linked, not moved)**

| Path | What it is |
|---|---|
| `src/bazaar_agent/agents/broker.py` | The broker: reads the venue book each tick, picks the bench policy from `BAZAAR_BENCH_POLICY`, falls back to exact on any error |
| `src/bazaar_agent/agents/matcher.py` | `exact`: max quoted-surplus matching of crossing pairs, midpoint price, fee-safe |
| `src/bazaar_agent/agents/bench_model.py` | Per-trader belief: hidden-limit bands from the quotes, departure risk (used by `edge`) |
| `src/bazaar_agent/agents/bench_edge.py` | `edge`: max estimated true surplus among crossing pairs, behind the guard margin |
| `src/bazaar_agent/agents/bench_probe.py` | `probe`: exact + a few non-crossing pairs (dead since t1692) |
| `src/bazaar_agent/agents/bench_match_probe.py` | The one-shot live match probe (`BAZAAR_BENCH_MATCH_PROBE=once`), spent in s7 |
| `src/bazaar_agent/agents/bench_lookahead.py` | #292's `lookahead`: 128 posterior rollouts, max expected true surplus |
| `src/bazaar_agent/agents/bench_posterior.py` | `lookahead_safe` / `lookahead_bold`: posterior sampling, one-step lookahead, plays P(above) |
| `src/bazaar_agent/agents/bench_capture.py` | The `bench_books` / `bench_evidence` recorder |
| `src/bazaar_sim/bench.py` | W1a's simulated Market Test (presets, stall, oracle) |
| `scripts/bench_tournament.py`, `scripts/bench_posterior_proof.py`, `scripts/bench_edge_proof.py`, `scripts/bench_probe_once.py`, `scripts/sim_market_test.py` | Harnesses: see [benchmarks.md](benchmarks.md) |
| `docs/night/b14_maker_probe.py` | Maker bid-lapse probe (own quoting, not the Market Test); report `docs/night/b14-expired-bids.md` |
| `docs/pitch/charts/05_market_test.csv` | Saturday-night **simulated** chart data (W1a/W1b/B2); for real data use [sessions.csv](sessions.csv) |
| `docs/research/2026-10-04/bench-sim.md`, `bench-search.md` | Redirect stubs (cited by code comments in `src/`) |

**Branches (on origin, not merged here)**

| Branch | What it holds |
|---|---|
| `feat/bench-lookahead-v2` (`af794985`) | Lookahead v2 code: `slack`, `budget_s`, zero-below scoring (report section brought in as bench-sim §7) |
| `fix/bench-baseline` | Origin of `reports/bench-baseline.md` (commits cherry-picked here) |
| `research/sat-review-crinoid` | Origin of `reports/mm-probe.md` and the Saturday review index (`docs/research/2026-10-04/README.md`) |
| `night/w1b-broker-edge` | W1b's report `docs/night/w1b-broker-edge.md` and #84's original edge |
| `night/b2-venue-runbook` | B2's `evals/saturday.py`, `broker probe --auto`, `--read-offset` |
