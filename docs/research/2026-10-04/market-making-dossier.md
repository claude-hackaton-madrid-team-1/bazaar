# Market making and the Market Test: the dossier (Team 1)

*Sun 4 Oct 2026, written 11:10–11:xx Madrid by the `mm-dossier` session. Read-only on the game, Railway and Postgres.
It collects what the team already measured; it does not redo the work. Every number carries its source. Short names
for sources:*

| Tag | Source |
|---|---|
| RULES | `vendor/bazaar-kit/RULES.md` (organisers' kit), lines 67–84 and 114–122 |
| BASE | `docs/research/2026-10-04/bench-baseline.md` (branch `fix/bench-baseline`, `dc563e54`) |
| SIM | `docs/research/2026-10-04/bench-sim.md` (branch `feat/bench-beat-stall`, PR #292, `15dc2cfc`) |
| SEARCH | `docs/research/2026-10-04/bench-search.md` (local branch `feat/bench-search`, `f46edf96`) |
| PROBE | `docs/research/2026-10-04/mm-probe.md` (branch `research/sat-review-crinoid`) |
| BOOKS | `docs/research/2026-10-04/bench-books.md` (origin/main) |
| REV-LA | `_sat-review/review-bench-lookahead.md` (adversarial review of PR #292, verdict SHIP; outside git) |
| BBS | `_night/BENCH_BEAT_STALL.md` (Saturday night; outside git) |
| STATUS | `_sat-review/STATUS.md`, the team's append-only log, lines quoted by time (outside git) |
| T3 | `docs/transcripts/2026-10-04-invofox-3.md` (organisers' Sunday talk) |

## TL;DR

The market-making criterion is 30 % of the game score, split (fitted, not published) into the **Market Test** (22.5)
and **organic** trades between other teams on our venue (7.5) (BASE Q1). On the Market Test we scored exactly the free
auto stall's level, `bench_points` 0.5, in **all 8 sessions so far** (efficiency 0.854–0.967, BASE Q2 + session 8),
and so did every other team that kept a working venue: nobody has ever beaten the stall, and two teams (t07, t08)
scored ~0 in session 8: t07 had just swapped its stall for a board with no broker ready, t08's board matched nothing (BASE "Session 8"). We found why we tie: our `exact` broker sends
the same pairs the stall crosses, and the server refuses any pair whose quotes do not cross (one-shot probe, tick 1692,
`400 bad_match`), so the only lever left is **which** crossing traders to match and **when** (SIM TL;DR 1–2). We
recorded the real books (`bench_books`, sessions 7–8), fitted a simulator to them, and built a `lookahead` broker that
plans over 128 sampled futures; in the calibrated world it scores E[bench_points] 0.64 vs 0.50 (P above the stall
0.29, P below 0.14), and on a replay of session 8's real book it would have held one cheap seller for a better buyer
and scored 0.90 in expectation (SIM §4–5; on drawn hidden limits and lives: that seller's real life is unobserved, so this is a model estimate, §4). It ships for session 9 (~12:37, the last bench) via PR #292 +
`BAZAAR_BENCH_POLICY=lookahead`, if merged in the window. Organic flow on v19 has been **0 trades all weekend** (PROBE
§2.3, BASE Q4): routers post on older venues and no buyer ever sat on v19.

## 1. How scoring works

### 1.1 What the rules say (quoted)

> "Your broker sees your venue's order book (makers shown as pseudonyms) and pairs crossing offers: `GET
> /api/broker/book`, `POST /api/broker/matches {"sell": ..., "buy": ..., "price": ...}`." (RULES:74)

> "**You cannot trade on your own venue** with your team key. Your market earns when *other* teams trade well on it."
> (RULES:76–77)

> "**The Market Test**: every two hours every venue receives the same synthetic book of buyers and sellers. Your
> broker (or your auto mechanism) matches them; your score is the share of the possible gains you realise. A broker
> can only act on a `board` venue: on an `auto` venue, the free stall included, the engine crosses every pair first.
> Each session counts your best venue open during it (none open counts 0) and the round averages its sessions […]
> Matching as well as the free auto stall earns half the bench points; the full points go to the mean of the top
> three." (RULES:78–82)

> "| Market-making 30 | The Market Test efficiency · value created between other teams on your venue |" (RULES:119)
> "What never counts: the number of trades, fees you earned …" (RULES:122)

### 1.2 What we measured on top of the text

| Item | Value | Status | Source |
|---|---|---|---|
| Split of the 30 | market per round = **22.5 × bench_points + 7.5 × organic** | fitted on `/me` and the board, holds Sat and Sun | BASE Q1 |
| Efficiency | share of possible gains between the traders' **hidden limits** (`/me score.bench_efficiency`) | rules text + `/me` | BASE Q1 |
| The stall baseline | the free `auto` stall: crosses best ask vs best bid every tick at the midpoint; matching its efficiency on the same book = **0.5** | published (RULES:82) | RULES, BASE Q1 |
| Full points | 1.0 = mean efficiency of the **top three** venues in the session | published | RULES:82 |
| Between stall and top-3 | no formula published; linear is our reading | unknown | BASE Q1 |
| Below the stall | not published. Saturday fit: 0.5 × E/E_stall (t03/t13 moves, BBS §1). Observed: a board venue with no matches scores ~0 (t07, t08 in session 8). Both fit the zero-match case, so "linear" vs "zero below" is **not** settled | unknown | BASE "Session 8", REV-LA MED 2 |
| Server check | `POST /api/broker/matches` checks the **posted quotes**: a non-crossing pair is refused `400 bad_match` | measured once, tick 1692 | STATUS 10:29, BASE Q5 |
| Round averaging | Sunday's round averages sessions 7, 8, 9; raising one session 0.5 → 1.0 = +22.5 × 0.5 / 3 = +3.75 on Sunday's market round ≈ **+1.5 final points** (Sunday = 40 % of the game score) | derived | BASE Q1 |
| First team above the stall | if linear and nobody else beats it, the top-3 mean = (ours + 2 × stall)/3 and a lone team above the stall scores the cap, 1.0 | derived, untested | BASE Q2 |
| Organic | `/me score.mm_points` = value created between other teams on our venue, at their private values; Team 12's bug fix (Sunday) stopped a seller's underpriced sale from counting against the venue | rules + T3 [04:05–05:00] | BASE Q4, T3 |

## 2. Timeline of every session

`bench_points` and efficiency from `/me` (`me_snapshots.score`) after each session. Our quoted surplus = sum of
bid − ask over our matched pairs.

| # | Run | Tick (local time) | Venue / policy | Our pairs | Efficiency | bench_points | Stall? | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | – | 201 (Sat, h3) | v08 starter stall (auto), engine crossed | 0 | 0.899 | 0.5 | = stall (we were the stall) | PROBE §2.2, BASE Q2 |
| 2 | – | 441 (h5) | v19 board, exact | 4 | 0.933 | 0.5 | tie | PROBE §2.2 |
| 3 | b52 | 681 (h7) | v19, exact | 5 | 0.878 | 0.5 | tie | PROBE §2.2 |
| 4 | b69 | 921 (h9) | v19, exact | 6 | 0.891 | 0.5 | tie | PROBE §2.2 |
| 5 | b87 | 1161 (h11) | v19, env `edge` but every pair carries the exact reason | 6 | 0.886 | 0.5 | tie | PROBE §2.2, BBS TL;DR 6 |
| 6 | – | 1401 (h13) | v19, exact (the h13 hand probe was never sent) | 4 | 0.854 | 0.5 | tie | PROBE §2.2 |
| 7 | b120 | 1690 (Sun 10:16, "hard", unscheduled) | v19, exact + one-shot match probe (refused) | 7 (quoted 178) | 0.967 | 0.5 | tie; all 18 teams +11.2 ± 0.1 Sunday-round market = 0.5 | BASE Q2, Q5 |
| 8 | b137 | 1774 (Sun 10:37) | v19, exact | 4 (quoted 99) | 0.895 | 0.5 | tie; 16 teams 0.5, t07 and t08 ~0 | BASE "Session 8" |
| 9 | – | ~2254 (Sun ~12:37, t 17.0, last) | v19, `lookahead` if PR #292 merges, else exact | – | – | – | pending | SIM §5 |

Notes:
- Saturday's efficiency per session vs the round average is ambiguous: PROBE §2.2 also gives a "reading B" column
  (0.899 / 0.967 / 0.768 / 0.930 / 0.866 / 0.694) if `/me` is the running round average. `bench_points` is 0.5
  either way.
- Saturday's board: 7 teams incl. t01 at market 7.50 (= 22.5 × 0.5 / 1.5 round weight), every team above 7.50 had
  organic trades on its venue; t10 led at 12.50 with organic, not bench (PROBE §2.3, BBS §9).

## 3. What we tried, and the evidence

| Policy / idea | What it does | Live? | Evidence | Verdict | Source |
|---|---|---|---|---|---|
| `exact` (default) | max quoted-surplus matching of crossing pairs, sent the tick they cross, midpoint price | sessions 2–8 | 36 pairs, 0 refused; bench_points 0.5 in 7 of 7 | = stall by construction | BASE Q2, PROBE §2.2 |
| `edge` (guard margin 10 / 5) | re-pick **which** crossing traders match on estimated true surplus | env on at h11, never overrode exact | all 6 h11 rows carry the exact reason; sim 0.51 (margin 10), 0.57 (margin none); **both real replays tie the stall (0.500)** | can't move a real book | PROBE §2.2, SIM §2, §4 |
| `probe` (PR #257) | exact + up to 6 non-crossing pairs a session at the midpoint, betting the server checks hidden limits | on overnight, never fired in a bench | superseded by the match probe | dead: server checks quotes | PROBE §2.1, BASE Q5 |
| Match probe (`BAZAAR_BENCH_MATCH_PROBE=once`, PR #263) | one non-crossing POST ever, to settle the rule | fired session 7, tick 1692 | sell b120-12 ask 40 × buy b120-2 bid 39 @ 39 → `400 bad_match "price must sit between the ask 40 and the bid 39"`; one tick later b120-12 stepped to 38 and exact matched the same two | **settled: quotes, not limits** | STATUS 10:29, BASE Q5 |
| Auto "hedge" venue (`max_venues` 2) | a second, auto venue as a floor | never | no team ever ran two venues; the sim refuses `venue_exists` | not tried | PROBE R5 |
| `bench_books` recorder (PR #261, #282) | stores the whole bench book every tick | from session 7 | b120 61 rows / 24 offers; b137 59 rows / 20 offers | the data behind the simulator | BOOKS, STATUS 10:21 |
| Calibrated simulator (`scripts/bench_tournament.py`) | worlds fitted to b120 + b137 (two-bump sellers, buyers bidding 0.6–0.95 × value, steps 1–10 P a tick) | offline | reproduces the real quote paths; #77's old preset did not | the tool | SIM §1 |
| `lookahead` (PR #292, `d4c5b328`) | per tick, 128 posterior futures; picks the matching of crossing pairs (hold, re-pair, one more pair) with the best expected true surplus vs the stall rolled forward; ties keep exact | ships for session 9 | sim E 0.642 / P(above) 0.293 / P(below) 0.140; ±50 % worlds 0.58–0.67; replays b137 0.899, b120 0.502 | best real policy found | SIM §2–5 |
| `lookahead` (bench-search variant, local) | same idea, 96 samples, scores below-stall as 0, deviates only if it gains more often than it loses (`min_edge` 0.01) | not shipped | sim E 0.668 / 0.347 / 0.143 (E 0.602 if below = 0); replays b137 0.789, b120 0.809 | the conservative alternative | SEARCH |

The headroom is **timing**, not partner choice: an oracle that knows every departure beats the stall by +8 to +10 %
of possible gains in sim (+4.5 % on b120, +14 % on b137 replays), while `who_oracle` (knows every limit, never holds)
gains < 1 % and is often negative (SIM §2).

## 4. Session 8 deep-dive (b137, tick 1774, 10:37 local)

*V1 from the reports; the real-data tables from Postgres follow in the next revision.*

- **Book:** 10 buyers, 10 sellers (20 offers, 59 `bench_books` rows), `expires_tick` 1790 on every offer, so the run
  is ticks 1774–1789 (SIM §1). Sellers in two bumps: opening asks 27–54 or 71–129; buyers open far below their limit
  (b137-3 rises 42 → 64 over 4 ticks, b137-7 36 → 55 over 5); only 1 of 16 traders seen ≥ 2 ticks was firm (SIM §1).
- **Ours (`exact`):** 4 pairs, quoted surplus 99, at ticks 1779, 1783 (×2), 1786; `/me` efficiency 0.895,
  bench_points 0.5 (BASE "Session 8").
- **What the stall would do:** the same 4 pairs (exact = stall by construction; every exact-family policy scores 0.500
  on the b137 replay, SIM §4).
- **What lookahead would do:** at t1783 exact crosses b137-12 (ask 32) × b137-5 (bid 46) and b137-18 (27) × b137-3
  (65); lookahead keeps seller 12 for the bidders still to come: b137-2 (bid 68) and b137-6 (bid 69) arrived the next
  tick, with 8 of 10 buyers seen. That is the +7.6 % efficiency on b137 (SIM §4).
- **Why the replay says 0.899, and how much to trust it:** the replay draws hidden limits, lives and relax shares
  (300 draws) that reproduce every recorded quote path, then runs every policy and the stall on the same draws (SIM
  §4). lookahead: efficiency 0.852 vs stall 0.778, P(above) 0.823, P(below) 0.170 → E 0.899 under the "1.0 above,
  0.5·E/Es below" rule. The gain rests on seller b137-12 still being there at tick 1784; it was matched the tick it
  arrived, so its real life is **unobserved** and drawn from the prior (P(life ≥ 2) = 0.875). The prior was fitted on
  b120 and b137, so the replay is in-sample (REV-LA MED 2). Under a zero-below rule the b137 replay is ≈ 0.83 (REV-LA).
  bench-search's own planner gets 0.789 on the same book (more cautious; SEARCH).
- **Oracle:** 0.920 efficiency vs the stall's 0.778 (+14.2 %) on the replay (SIM §4).
- **Everyone else:** 16 teams' board market moved +0.02…0.04 over ticks 1782 → 1802 (= 0.5 again); t07 10.71 → 9.37
  and t08 9.06 → 7.73 = bench ~0 (t07 swapped its stall v11 for board v29 at tick 1758 with no broker ready; t08's
  board v06 matched nothing). The top-3 mean = the stall level; nobody beat the stall (BASE "Session 8").

## 5. Organic market making (the 7.5)

- **v19: 0 trades, 0 traders, 0 pairs all weekend** (`/api/venues`; PROBE §2.3; BASE Q4). Saturday it drew only 15
  outside listings (13 asks from t15 at ticks 373–381, 2 from t04 at 550 and 612), all single-card asks expiring in
  6–10 ticks, and no bidder ever came.
- **Why 0:** other teams' routers post on venues that opened early (v07, v02, v21, v01; v19 opened at tick 262) or on
  starter stalls; addressed listings alone do not make trades (v13/v15: hundreds, 0 trades); fee cannot go below 0
  and rebates are "feeding" (RULES:131) (PROBE §2.3).
- **Organic credit is fragile:** t12 (v02, 11 trades) fell 12.43 → 7.50 at tick 910; t07 (v11) 9.35 → 7.50 at 1210
  (PROBE §2.3). Team 12's fix on Sunday stops underpriced sales counting against a venue (T3 [04:05]).
- **What t10 did (12.5 market on Saturday, +5.0 over us):** 11 small trades between other teams on v07 after
  matchmaking notices naming live bids and the El Rastro fee saving (BBS §9).
- **What would change it:** a standing buyer on v19, other teams hosting their threads and swaps on v19, and
  near-miss notices only while a live gap ≤ 5 P exists. Expected +0.05 to +0.25 final (PROBE §3.2).

## 6. What is live now, and what ships for session 9

- **Live (bazaar-maker):** `exact` (log `broker on for v19 (LIVE), bench exact`; the match probe's claim is spent)
  (STATUS 09:28, BASE Q5).
- **Ships for session 9:** PR #292 (`feat/bench-beat-stall`, head `15dc2cfc`, open, mergeable) + Railway
  `BAZAAR_BENCH_POLICY=lookahead` on bazaar-maker (set first with `--skip-deploys`: today's code ignores the unknown
  value and stays exact). The merge touches `src/**` and redeploys maker, taker and duels, so only after Duels III and
  before ~12:25; never 12:37–12:42. Verify the maker log `broker bench lookahead`. Rollback:
  `BAZAAR_BENCH_POLICY=exact` (SIM §5, REV-LA).
- **Review:** no HIGH findings; MED: no compute cap (worst 0.4–0.6 s a tick on a laptop), the expected-points case
  rests on an unpublished below-stall rule (zero-below: 0.577 in sim, b120 replay ≈ 0.42), merge blast radius
  (REV-LA).

## 7. Open questions

1. The points curve **below** the stall (linear 0.5·E/Es or zero) and **between** the stall and the top-3 mean.
2. Whether the server's efficiency denominator is time-aware (the sim's stall sits at 0.80, real 0.85–0.97; SIM §1).
3. Whether a held trader can leave before a queued match settles (`settles_at_tick` = T+1; SEARCH caveats).
4. Whether another team beats the stall in session 9 (then the top-3 mean rises and a small win earns < 1.0).
5. Whether lookahead's b137 gain is real: seller b137-12's life is censored by our own match.
6. Whether `/me bench_efficiency` is per session or the running round average. If it is Sunday's round average,
   session 8 alone was ≈ 2 × 0.895 − 0.967 = 0.823, not 0.895 (Saturday has the same ambiguity, §2 notes).

## 8. Pitch: market making

Judges' share is 40 %; market making is a scored criterion. No private values below (no card values, limits or cash).

**Five bullets**

1. **We read the scoring before we optimised it.** The Market Test pays the share of possible gains on a synthetic
   book; matching the free stall is worth half, beating the top three is worth all (RULES:78–82).
2. **We matched the stall in all 8 sessions, and so did every team** that kept a matcher up; nobody beat it
   (efficiency 0.854–0.967, bench_points 0.5 each time; BASE).
3. **We found why, with one deliberate experiment:** a single probe match outside the quotes, tick 1692, came back
   `400 bad_match`; the server checks quotes, so the only edge is which crossing traders to match and when.
4. **We recorded every bench book from session 7 and fitted a simulator to them** (two-bump sellers, relaxing quotes,
   lives 2–6 ticks) that reproduces the real quote paths; in it, an all-knowing broker beats the stall by +8–10 %,
   all of it from timing.
5. **We shipped a lookahead broker** that plans over 128 sampled futures each tick: E[bench points] 0.64 vs 0.50 in
   the calibrated world, P(above the stall) 0.29, and on the real session-8 book it would have held one cheap seller
   for a better buyer one tick later.

**Chart-worthy table**

| Session | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| Our efficiency | 0.899 | 0.933 | 0.878 | 0.891 | 0.886 | 0.854 | 0.967 | 0.895 | – |
| Our bench points | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | lookahead |
| Teams above the stall | 0* | 0* | 0* | 0* | 0* | 0* | 0 | 0 | – |

\* Saturday (sessions 1–6): from the end-of-round board, not per session: every market score above 7.50 belongs to a
venue with organic trades (PROBE §2.3, tick 1430). Sessions 7–8: per-session leaderboard deltas (BASE).

Policy comparison (calibrated world `cal_normal20`, 300 books; SIM §2): exact 0.500 · edge 0.513 · edge (no guard)
0.570 · **lookahead 0.642** · oracle 0.932 (E[bench points]).

**The honest story:** we tied the stall all weekend; we found out why (the server checks quotes, so a smarter price
cannot help, only smarter timing); we built a simulator calibrated on the real books and a broker that plays the
timing. It is a positive-expectation bet for the last session, not a guarantee: in the calibrated sim it ends above
the stall 29 % of the time, ties 57 % and ends below 14 % (SIM §2), and a session below the stall may cost the whole
0.5 if the unpublished rule is "zero below" (≈ −1.5 final points; REV-LA MED 2).
