# Market making with the probe: what Saturday measured, and Sunday's plan (sat-mm-probe)

Written Sun 4 Oct 2026, 00:15–00:45 Madrid, revised ~01:30 after review (`_sat-review/review-mm-probe.md`: 2 HIGH, 8 MED, 2 LOW, all addressed in place; §9 lists what changed and the one point partly argued), by the `sat-mm-probe` research session for Team 1 (t01). Read-only on the game.
The doors are closed (`/api/clock` at 00:21: tick 1445, t 13.367, paused, next opening 09:00).

## Three ambiguities, read these first

1. **"Market that" has two meanings, and this report covers both.** (a) How to *sell our venue* to other teams' agents, so that they trade on v19: wording, what their agents read, fees. See §3.2 and §6.2. (b) How to *present market making to the judges*: it is a scored criterion, and the pitch is ≈ 40 %. See §6.1.
2. **The brief's words (fills, spreads, inventory, cash at risk) describe classic two-sided market making, but the scored "market-making" criterion is something else.** It is the Market Test efficiency plus the value created between *other* teams on our venue (RULES.md:119). Neither part uses our cash or cards, and we cannot trade on v19 (RULES.md:76, `self_venue`). Our own quoting of cards (the maker's asks and bids) scores in the *negotiating* column, at our private values (RULES.md:118). So the Sunday design below has three books:
   - **A**: the bench (probe broker);
   - **B**: organic flow on v19;
   - **C**: our own quoting. C is kept short because it belongs to the other criterion.
3. **"Our probe strategy" means the bench probe** (`BAZAAR_BENCH_POLICY=probe`, PR #257). That is the market-making one. The taker also has a `ladder_probe` (Jev-gated dealer price probes, 13 rows on Saturday), which is unrelated and not covered here.

## TL;DR

- **The probe has never run live, and it now runs by default.** No evidence was found that the h13 hand probe was sent: there is no local log, no transcript or shell-history call, and `bench_points` stayed exactly 0.500. PR #257 merged at 00:18, and bazaar-maker is already on `BAZAAR_BENCH_POLICY=probe`: its keeper line reads `broker bench probe (exact + non-crossing probes)`, and the maker is waiting for 09:00. Sunday's first bench is the hard test (12 traders, firmer, more impatient), and it becomes the probe's first live test. **Its "stop after 8 refusals" latch can never fire** (6 probes per session cap, and refusals count toward them), so under the quote rule it sends 6 refused matches in *every* session: use the manual rule in §3.1 (switch to `exact` before h15 if h14.65 shows refusals and nothing gone).
- **Saturday's bench result: 6 of 6 sessions at exactly the stall (bench_points 0.500).** Our broker made 25 pairs; the server returned no error code on any of the 25 (`executions`), and every response body inspected (4, ticks 1405–1412) read `{"queued": true, "settles_at_tick": T+1}`. The `edge` setting was live in the environment for h11, but it never overrode exact: all 6 h11 pairs carry the exact reason string. No team beat the stall all day: every team above 7.50 on the board has trades on its venue.
- **Organic flow on v19 was zero, and organic credit elsewhere was fragile.** v19 had 0 trades all day; its 15 outside listings (all asks, 6–10 tick expiry) came 3–20 ticks after our notices, but no bidder was ever there. On other venues credit was neither per-trade nor permanent: t12 (v02, 11 trades) fell 12.43 → 7.50 at tick 910, t07 (v11) went 7.50 → 9.35 → 7.50 by tick 1210, t05 (v10, 2 trades) never showed credit, and v13/v15 had hundreds of addressed listings and 0 trades. Fee is not a lever below 0 (v02 went to the 5 P/card maximum at tick 941 and traded no more). The new levers (MM2 "wanted cards" notice #238, venue invite in swap proposals #251) merged at 23:25 and have never run live.
- **Sunday expected value (final leaderboard points; ranges, not promises):**
  - Book A (probe): **+1.0 to +2.5 expected**, weighted over three server worlds: hidden limits checked (P 0.35–0.55: worth +2.8 to +4.5), quotes checked (P 0.35–0.55: 0), nothing checked (P ≈ 0.1: about −0.1 to −0.3, capped at one session by the §3.1 rule). The low end assumes per-session bench averaging, the high end the round-average reading fitted on Saturday. Cash at risk 0.
  - Book B (organic on v19): **+0.05 to +0.25** (was +0.6 to +1.3 before review): P(a first trade on v19 on Sunday) 0.05–0.35 × ≈ +0.65 final of credit that survives, measured on Saturday's low-volume venues including the two that lost it.
  - Book C (our quoting): about +0.2 final (+0.33 on the board) per 10 P of surplus on a sale. **New measurement:** one team sale (LAT-10, tick 1304) showed that gains do count, at k ≈ 0.033 board per neg_point, against 0.048 on the loss side.
- **Before anything else at 09:00:** read `/api/clock` and `/api/schedule`. The schedule pins "Sunday opens" and "Round 3 starts" at t 16.65, while the clock froze at 13.367. If round 3 starts at the 09:00 opening, the two remaining round-2 benches (h14.65, h15) change rounds or never run, and every round-2 number above moves. Then:
  - keep `probe`;
  - do **not** open a second (auto "hedge") venue: no team ever ran two at once, and our simulator refuses it with `venue_exists` (§4 R5);
  - make no deploys, variable changes or pauses inside the bench windows (§3.0).

---

## 1. Method and data windows actually seen

- **Postgres** (shared ledger, read-only session `default_transaction_read_only=on`, helper `mm-probe/q.py`, URL never printed):
  - `decisions` (broker_match, venue_announce, maker post_ask/bid), `executions` (match responses), `me_snapshots` (t01, ticks 159–1445), `leaderboard_snapshots` (last real snapshot at tick 1430), `feed_events` (whole table ticks 0–1445: 8,422 `offer.listed`, 793 settlements, 429 notices; Saturday ticks 160–1445: 7,670 / 601 / 426), `cards`.
  - Saturday window: ticks 160–1445, 09:00–23:00 Madrid, with three pauses (ticks 284, 630, 1201).
- **Railway**, read-only, run from an already-linked scratch directory: `railway variables --service bazaar-maker --kv` filtered to `BAZAAR_(BENCH|LIVE|SIM|VENUE|DECIDER)`, `railway deployment list`, and a 25 s `railway logs` tail. **Window seen:** only the current container's start-up (≈ 00:18 Sunday, 9 lines). Saturday's maker logs were **not** read, because the CLI returns the current deployment only.
- **Keyless game GETs, 22 in total** (zero keyed requests; listed in `mm-probe/evidence/keyless_requests.log`): `/api/venues`, `/api/clock`, `/api/schedule` at 00:21, then `/api/venues/{v}/offers` for 19 venues at 1 req/s, 00:21–00:22.
- **Prior work reused, not re-run:** `_night/MM_STRATEGY.md`, `MM_DEEP.md`, `MM_PLAN.md`, `HANDOFF_MM_PROBE.md`, `MARKET_TEST_H11_READY.md`, `KNOWLEDGE_SAT_EVENING.md`. The real-book replay and the sim round table are another session's output (bench-beat-stall scratchpad). They are copied into `mm-probe/evidence/` (`real_books_analysis.txt`, `replay_real_books.txt`, `round_sim.txt`) and contain only synthetic bench data.
- **Transcript search** for whether the h13 probe was sent: every `~/.claude/projects/*/*.jsonl` file was searched for Bash calls of `bench_probe_once.py`, and `~/.zsh_history` was searched too. The only hits are edits of the script and a `git push`. Nothing in this search would show a run made in a terminal that does not write zsh history, so absence of evidence is all it shows.
- **Scripts:** `mm-probe/q.py` (query helper), `mm-probe/bench_stats.py` (per-session table), `mm-probe/market_stats.py` (Saturday market by rarity), `mm-probe/spreads.py` (close-of-day per-card spreads). Their outputs are in `mm-probe/evidence/*.out`.

## 2. Findings

### 2.1 What the probe strategy is (code on main, `000cc588`)

- `BAZAAR_BENCH_POLICY=probe` (`src/bazaar_agent/agents/broker.py:73-74,113-123`). The maker reads it once at start-up. An invalid value is ignored and the broker stays `exact`.
- Each tick (`broker.py:373-399`, `agents/bench_probe.py:102-135`):
  1. The **exact plan goes out first, unchanged**: the maximum quoted surplus among crossing pairs, which is what the stall does.
  2. Then, among the bench traders the exact plan leaves out, the lowest ask is paired with the highest bid, the next with the next, and so on, while `ask + fee − bid ≤ 20 P`. Each such pair is priced at the midpoint.
- Hard-coded caps (`bench_probe.py:36-40`; no environment override, so changing them means code + redeploy):

  | Cap | Value |
  |---|---|
  | Probes per tick | 4 |
  | Tries per pair | 3, never twice at one price |
  | Probes per session | 6 |
  | Refusals before it stops | 8 with nothing gone (`give_up_after`), **unreachable**: see below |

- **What the probe tests:** whether `POST /api/broker/matches` checks the *quotes* (openapi: "ask ≤ price and price + fee ≤ bid") or the traders' *hidden limits*. The kit's own broker docstring points to limits ("a quote is not a limit … a broker that estimates those limits … beats the stall", `vendor/bazaar-kit/starter_broker.py:88-91`, quoted in MM_STRATEGY §2.0).
  - Under the quote rule every probe is refused and the score equals the exact broker's.
  - Under the limit rule a settled probe takes gains that the stall can never take.
- **The process-wide stop never fires (review finding, verified in code).** A probe refused at the POST counts in `run.sent` as well as `run.refused` (`bench_probe.py:151-153`, `broker.py:489-491`), a queued one counts in `sent` (`bench_probe.py:139`), and `plan()` caps each run at `max_per_run − sent` = 6 (`:117`). So `refused ≤ sent ≤ 6 < 8 = give_up_after` in every run, and the `quote_rule` latch (`:155-156`) is unreachable with the defaults; the tests only exercise it with `give_up_after` 1 or 3 (`tests/test_bench_probe.py:79,110,184`). The reviewer ran 4 all-refused runs: 6 refusals each, latch still off. **Under the quote rule the broker therefore sends 6 refused non-crossing matches in every session, all day.** Each costs nothing in score ("costs nothing and moves nothing", RULES.md:150); the cost is optics (RULES.md:84). BENCH_BEAT_STALL's "≤ 8 refused POSTs per process" carries the same error. The fix is in §3.1 (manual rule) and §5 (code).

### 2.2 What Saturday's bench actually measured

`mm-probe/evidence/bench_stats.out`: pairs from `decisions` broker_match; score from the first `me_snapshots` row after each session.

| Session | Ticks | Venue | Our pairs | Quote spread of matched pairs, median / sum (P) | /me eff after | bench_points | Session eff if /me is the round average (reading B) |
|---|---|---|---|---|---|---|---|
| h3 | 201–217 | v08 starter stall (auto) | 0 (the engine crossed) | – | 0.899 | 0.5 | 0.899 |
| h5 | 441–457 | v19 board | 4 | 44 / 161 | 0.933 | 0.5 | 0.967 |
| h7 | 681–697 | v19 | 5 | 15 / 85 | 0.878 | 0.5 | 0.768 |
| h9 | 921–937 | v19 | 6 | 23 / 119 | 0.891 | 0.5 | 0.930 |
| h11 | 1161–1177 | v19 (env `edge`) | 6 | 28 / 165 | 0.886 | 0.5 | 0.866 |
| h13 | 1401–1417 | v19 | 4 | **4 / 29** | 0.854 | 0.5 | 0.694 |

- **Fills.** 25 pairs; every `executions` response was `{"queued": true, "settles_at_tick": T+1}`. There were 0 error codes on `broker_match` (`select sdk_method, error_code, count(*) from executions …`). Prices ranged 30–98 P, and the median quote spread was 20 P.
- **Spread at h13.** h13 matched the thinnest pairs of the day (quote spreads 1, 1, 7 and 20 P), and its efficiency was the lowest of the six sessions. **Inferred, not measured:** the h13 book probably held near-crossing pairs that departed unmatched, which is exactly what a probe targets. Only our own pairs are stored for h11 and h13; the whole book was not logged until #261 (merged 00:17), so from Sunday the `bench_books` table will hold it.
- **Who responded.** Bench traders are synthetic, and the server queued 100 % of our crossing pairs. No team's board venue rose above the stall (§2.3, final board table). The bench numbers exist only in each team's `/me`.
- **h11 with `edge`.** All 6 h11 rows carry the reason `maximum-surplus matching (exact), midpoint price`. An edge-chosen pair would read `bench edge: maximum estimated true surplus …` (`broker.py:456-463`), so the guarded edge never overrode exact on a live book.
- **h13 hand probe.** There is no evidence it was sent (see §1). **So the probe has not yet produced a single live data point.**
- **Near-miss habitat on the books that were captured** (h7/b52 and h9/b69, read every 5–10 s by MM_DEEP; `evidence/real_books_analysis.txt`):
  - b52: 13 co-present pairs, 8 of which never crossed by quote. One pair was provably inside both limits: b52-2 bid 56 against b52-10 ask 57, a gap of 1 P. Both sides left unmatched.
  - b69: 35 co-present pairs, 20 never crossed. b69-8 bid 61 against b69-11 ask 63 (gap 2) was provable, and b69-8 against b69-10 (gap 5) had both sides leave unmatched.
  - Replayed under the limit rule with the probe's geometry (`evidence/replay_real_books.txt`, rows `prod G20N4K8`): b52 efficiency **+0.062 over the stall** (above it in 100 % of limit draws), b69 **+0.023** (56 %). Under the quote rule: +0.000 on both. The h11 book (b87) shows 0 near-misses, but only because just our matched traders were reconstructed.
- **Inventory risk: none on the bench.** Bench offers are synthetic, cash-only and card-less. The broker spends no cash and holds no card (`MARKET_TEST_H11_READY.md` §5).
- **Hygiene.** Three notices went out inside a bench: ticks 445 (h5), 923 (h9) and 1166 (h11) (`feed_events` venue.announcement, 39 v19 notices in ticks 332–1421). Harmless to the score, but the keeper has no bench hold (§5).

### 2.3 What Saturday measured for organic flow (v19)

- **v19:** 0 trades, 0 traders, 0 pairs (`/api/venues` 00:21). Only 15 listings by others ever: 13 from a t15 burst at ticks 373–381, and 2 from t04 for MAL-06 at 29 and 27 P at ticks 550 and 612. Nothing after 612 (`feed_events` offer.listed venue=v19).
- **Notices:** 39 v19 notices in the feed (ticks 332–1421; `decisions`: 37 done, 16 refused `wait`), the last 11 from tick 1110 with one generic text ("0 % fee … our broker pairs crossing bids and asks every tick, at the midpoint"). **Response by timing:** t15's 13 asks at 373–381 came 12–20 ticks after the notice at 361; t04's asks at 550 and 612 came 3 and 10 ticks after notices at 547 and 602. All 15 were single-card asks with 6–10 tick expiry, and no bidder was ever on v19. Honest reading: notices may draw sellers; the missing piece is a standing buyer. Nothing listed after tick 612 (the evening notices drew nothing).
- **Team-venue trades on Saturday:** 42 settlements in total. v02 11, v07 11, v21 6, v01 3, v11 3, v16 2, v10 2, v06 2, v14 1, v17 1. Most were commons at 4–10 P; two 0 P swaps on v11 counted too.
- **Organic credit is neither per-trade nor permanent** (`leaderboard_snapshots` market column, ticks 610–1430):

  | Owner (venue) | Trades | Market path | Note |
  |---|---|---|---|
  | t12 (v02) | 11 | 12.43 at 900 → **7.50 at 910**, 7.25 at close | fell right after v02 trades at 898 and 903; v02's busiest stretch was a t14 burst (five 9 P commons, 591–598), the shape RULES.md:132 describes; then fee 10 % + 5 P/card at 941, 0 % + 5 P/card at 1051, no trade after 903 |
  | t07 (v11) | 3 | 7.50 → **9.35 at 670** (0 P swap at 669) → 9.17 at 1150 → **7.50 at 1210** | fell right after a 0 P swap on v11 at 1202 |
  | t05 (v10) | 2 (ticks 311, 398) | 7.50 from 610 (first snapshot) to close | any credit before 610 unseen |
  | t14 (v14) | 1 | +1.80 kept | |
  | t17 (v17) | 1 | +1.14 kept | |
  | t08 (v06) | 2 | +0.95 kept | |
  | t16 (v16) | 2 | +2.66 kept | |
  | t06 / t09 / t10 | 3 / 6 / 11 | +4.37 / +3.39 / +5.00 (cap) | |
  | v15 / v13 (starter stalls) | 0 | – | 160 and 396 listings, mostly addressed, 0 trades |

  3 of the 10 venues that traded ended with no organic credit, and the two visible wipes each came within 10 ticks of a trade on that venue. **Inferred mechanism (not verified):** credit is the venue's value created at private values, floored at 0, so a later value-destroying trade (or an organiser review under RULES.md:132) can erase it.
- **Where the listings went** (non-Rastro, Saturday):

  | Venue | Listings | Addressed | Makers |
  |---|---|---|---|
  | v07 | 552 | 390 | 10 |
  | v02 | 420 | 91 | 9 |
  | v13 (t11's inactive starter stall) | 396 | 102 | 4 |
  | v21 | 236 | 187 | 9 |
  | v01 | 199 | 137 | 6 |
  | v15 | 160 | 160 | 4 |
  | v19 | 15 | 0 | 2 |

  Most of this flow is three teams' agents posting **addressed** offers on other teams' venues: t08 on v07, v21, v01, v02 and v11; t05 on v07 and v15; t13 on v13, v02, v11 and v07. Addressed listings correlate with trades on v07/v21/v01 but did **not** produce trades on v15 or v13, so they are not sufficient.
- **What their agents read (inferred).** These routers do not simply follow trades: t05 first posted on v07 at tick 260, before v07's first trade at 351, and t08 posted on v01 at 231, before its first trade at 286. They never picked v19, which opened at tick 262. Each picked venues that opened early (100–201) or a team's starter stall. Possible readings: ordering by venue id or age, or by name and description. None is verified. Every open team venue is at 0 bps and all but v02 at 0 P per card (v02: 5 P/card, the maximum, since tick 1051, after 10 % + 5 P at 941; no trade after 903). So **fee is not a lever below 0**: it cannot go negative, and rebates are "feeding" (RULES.md:131-132); a high fee may hurt.
- **Venue names and descriptions** are the parseable fields (`/api/venues`: `name` ≤ 40 chars, `description`, `fee_bps`, `mechanism`, `trades`, `traders`).
  - Ours: "Team 1 market", described as "Board venue, 0 % fee: crossing offers are matched every tick".
  - The fullest one: v07, "Team 10 · fair broker, 0 fee", described as "Zero fees. A broker matches every crossing pair card by card at the midpoint, any copy included. Built by Team 10 for everyone."
  - Only the fee can be changed after opening (openapi `PATCH /api/venues/{vid}` = "Venue Fee").
- **Value of a first trade, re-derived with the failures:** the earlier figure (+1.74 displayed ≈ +1.04 final per first trade, MM_PLAN §1) was measured only on venues that moved. Over Saturday's six low-volume venues (1–3 trades: v14 +1.80, v17 +1.14, v06 +0.95, v16 +2.66, v10 0, v11 0 at the close) the mean surviving credit is **≈ +1.09 displayed ≈ +0.65 final** (× 1.5 / 2.5), range 0 to +1.6 final. The cap is 7.5 round points per round (t10 sat there all evening).
- **Final Saturday board** (`leaderboard_snapshots` tick 1430), market column:

  | Team | Market | Venue |
  |---|---|---|
  | t10 | 12.50 | v07 |
  | t06 | 11.87 | v01 |
  | t09 | 10.89 | v21 |
  | t16 | 10.16 | v16, auto |
  | t14 | 9.30 | v25, auto |
  | t17 | 8.64 | v17, auto |
  | t08 | 8.45 | v06 |
  | 7 teams incl. **t01** | 7.50 | – |
  | t12 | 7.25 | v02 (11 trades, so bench below the stall) |
  | t13 | 6.77 | v24 |
  | t03 | 6.08 | v20 |

  Every score above 7.50 belongs to a venue with trades. Three of those venues are `auto`, where a bench gain is impossible (RULES.md:80). **Nobody beat the stall on the bench**, consistent with MM_STRATEGY §6.

### 2.4 Book C, our own quoting on Saturday (negotiating column)

Sources: `evidence/market_stats.out` and `feed_events` with actor t01. Listing prices are public on the feed.

- **Our quotes:** 218 asks (161 public and 41 addressed on El Rastro, 16 on other teams' venues), 4 public El Rastro bids at the close (an approved off-page order: it holds cash while it stands), and 1 bid on v02.
- **Team desk (threads and swaps):** 83 team threads opened on Saturday, **all on `venue: "rastro"`** (`executions` joined to `team_open`), 92 `team_offer` sends done, 37 failed, 3 rejected; #250 ("trade with every team") is on main. No settlement is attributable to a desk deal (settlement payloads carry no thread key; our 13 Saturday settlements all look like board trades), so this is inferred.
- **Our fills:** **4 team sales**, 255 P in total (fee 19), so ≈ 1.8 % of asks filled. We also made 9 team buys (144 P on El Rastro, plus one each on v02 and v10). The maker's 59 rejected asks were all one card held back by its own floor (`decisions` post_ask rejected).
- **The whole market, Saturday medians in P** (non-addressed listings, team trades with cash > 0):

  | Rarity | Public ask | Public bid | Bid/ask | Addressed ask | Team trade price | Trade/ask |
  |---|---|---|---|---|---|---|
  | common | 9 (n 2,929) | 3 (n 756) | 0.33 | 10 | 7 (n 57) | 0.78 |
  | uncommon | 28 (n 638) | 14 (n 345) | 0.50 | 26 | 20 (n 39) | 0.71 |
  | rare | 84 (n 93) | 42 (n 369) | 0.50 | 93 | 73 (n 20) | 0.87 |
  | epic | 315 (n 73) | 114 (n 35) | 0.36 | 117 | 201 (n 4) | 0.64 |

  Quoted spreads are wide: the median bid is 33–50 % of the median ask, and trades print at 64–87 % of the median ask.
- **The book at the close** (`evidence/spreads_close.out`): 40 open public offers on El Rastro and v21 only. Exactly one card (LAT-08) was quoted on both sides, with a 25 P gap; there were 0 crossing pairs and 0 gaps of 5 P or less. The live market is thin and one-sided.
- **Score effect, new measurement.** After the LAT-10 sale to t12 at 86 (tick 1304), board negotiating rose 15.43 → 17.40 at the next board refresh (tick 1310). Duel points drifted +0.98 over the same window; the neighbouring refreshes show duel drift moving the board by ≈ +0.15. That gives **k_gain ≈ 0.033 board per neg_point**, against the measured loss side k = 0.048 (SAL-07, KSE §1.1). So a team trade with surplus scores again, at about 70 % of the loss rate. This is one event, so the confidence is low.

### 2.5 Live state right now (verified 00:20–00:30)

- `origin/main` = `000cc588`: #257 (probe) merged 00:18:21, #261 (bench book capture) 00:17:30, #254 (`max_venues` 2, auto hedge venue) 22:47, #238 (MM2 wanted notice) and #251 (venue invite in swap words) 23:25.
- bazaar-maker variables: `BAZAAR_BENCH_POLICY=probe`, `BAZAAR_BENCH_GUARD_MARGIN=5` (a leftover; it only matters for `edge`), `BAZAAR_LIVE=1`, no `BAZAAR_SIM`.
- Maker deployments: 7 between 23:54 and 00:18, the last `b005a859` DEPLOYING at 00:18. Log of the current container: `venue keeper: broker bench probe (exact + non-crossing probes)` … `waiting, doors closed, paused; next opening 2026-10-04T09:00:00+02:00`.
- GUARDRAILS.md (`allow_venue_open` is at line 95):

  | Setting | Value | Line |
  |---|---|---|
  | `allow_venue_open` | true | 95 |
  | `max_venues` | 2 | 98 |
  | `team_words_venue_invite` | v19 | 117 |
  | `deploy_guard_bench_ticks` | 10 | 145 |
  | `max_score_loss_per_move` | 0.001 | 158 |
  | `official_value_margin` | 0 | 27 |
  | `protect_page_sets` | every set | 47 |

- v19: open, board, 0 bps, bond 250, opened tick 262. Our cash at the close covers a second bond.

### 2.6 RULES.md constraints on venues and market making (quoted)

- "From level 2 you may open a venue … It costs a refundable bond of 250 P plus 20 P. Fees are capped at 10 % and 5 P per card; fee changes take effect after a public notice." (RULES.md:69-71)
- "Your broker sees your venue's order book (makers shown as pseudonyms) and pairs crossing offers: `GET /api/broker/book`, `POST /api/broker/matches {"sell": ..., "buy": ..., "price": ...}`." (:74). The rules say "pairs **crossing** offers"; the probe tests whether that wording binds the server.
- "**You cannot trade on your own venue** with your team key. Your market earns when *other* teams trade well on it." (:76-77)
- "A broker can only act on a `board` venue: on an `auto` venue, the free stall included, the engine crosses every pair first." (:80)
- "Each session counts your best venue open during it (none open counts 0) and the round averages its sessions, so closing a venue after a good session keeps nothing." (:81). GUARDRAILS.md:98 (`max_venues` 2) reads this sentence as allowing a second, auto venue as a hedge; whether the server allows two venues at once is **not** shown (§4 R5).
- "Matching as well as the free auto stall earns half the bench points; the full points go to the mean of the top three." (:82)
- "Venues can be closed by their owner (the bond comes back after a cooldown) and suspended by the organisers for breaking the rules (the bond is cut)." (:84). This is the tail risk of a probe being *read* as rule-breaking.
- "What never counts: the number of trades, fees you earned … gifts …" (:122)
- "Do not … feed another team on purpose … when one team keeps handing another the whole value of their deals, those deals count for nothing" (:131-132). This rules out rebates, paid flow and seeding.
- "Rate limit: 5 requests per second per key (bursts of 20)" (:134). The broker key is assumed to have its own bucket; that is untested (MARKET_TEST_H11_READY blocker 3).
- "A refused request is a `4xx` … it costs nothing and moves nothing." (:150)

## 3. Sunday design

### 3.0 Clock: two scenarios, decided at 09:00 by `/api/clock` + `/api/schedule`

The schedule (`/api/schedule` at 00:21) lists the hard test at 14.65 and a test at 15.0, then at 16.65 "Round 3 starts" next to `day_opens sun` with wall time 09:00. It also has `day_closes sun` at 22.65 with wall time 15:00. The clock froze at t 13.367, so the two cannot both hold.

| Event | S1: t runs on from 13.367 (Saturday's precedent: no jump at the open) | S2: the calendar sets t = 16.65 at 09:00 (would fit "freeze 22.65 = 15:00") |
|---|---|---|
| h14.65 hard test | **10:17–10:21** (ticks ≈ 1753–1769) | fires at once at 09:00, or is skipped (unknown) |
| h15 test | **10:38–10:42** | same as above |
| Round 3, CHA, +150 P | 12:16 / 12:19 | 09:00 / ≈ 09:03 |
| h17 | 12:37 | ≈ 09:21 |
| Duels III | 14:16 | ≈ 11:00 |
| h19 | 14:37 | ≈ 11:21 |
| h21 | after the 15:00 close | ≈ 13:21 |
| Finale / scores freeze | after the close | ≈ 14:00 / 15:00 |

**Freeze windows under S1:** no deploy, variable change or pause from 5 min before each bench until 2 min after it. That means **10:12–10:23, 10:33–10:44, 12:32–12:44, and from 14:11 to the close** (Duels III, then h19). Recompute at 09:00 under S2.

### 3.1 Book A: the bench, run by the merged probe (no change needed)

- **Quote sizing:** keep the caps of §2.1 (4 per tick, 6 per session, gap ≤ 20 P, midpoint).
  - On the two captured real books, a gap cap of 10 and one of 20 replay identically (b52 +0.062, b69 +0.023). A cap of 40 is worse on b52 (9.7 % of draws below the stall).
  - Changing the caps needs code + a maker redeploy, so leave them.
- **Spread per pair:** the midpoint between the quotes at fee 0 (`probe_price`). Both hidden limits sit beyond their quotes, so the middle has the best chance of falling inside both.
- **Inventory limits:** none needed (no inventory). **Cash at risk:** 0.
- **What the hard test changes:** "firmer and more impatient traders" means quotes closer to the limits and earlier departures. The sim's `hard24` world gives P(round above the stall | limit) = 0.56, against 0.50 for `normal20` (`evidence/round_sim.txt`).
- **Kill conditions** (roll back to `exact` only inside a gap, never inside a window):
  1. `/api/me` `bench_points` < 0.500 after a session, meaning we went below the stall. Expected cost ≈ −0.03 final, so this is hygiene, not panic.
  2. Any organiser warning: a feed `announcement`, or a refusal text that names a rule rather than a price. Roll back at once and tell the organisers it was ≤ 6 bounded probes.
  3. **The h14.65 readout rule (replaces the old "it stops itself").** In the gap after h14.65 (S1: 10:23–10:33; under S2, after the first Sunday bench that runs), read the maker log (`railway logs --service bazaar-maker`, lines `bench probe … QUEUED / REFUSED <code> / gone / dropped`) and `me_snapshots.score->>'bench_points'` (read-only DB):
     - ≥ 1 `REFUSED <code>` with a quote- or price-shaped code and 0 `gone` → **set `BAZAAR_BENCH_POLICY=exact` on bazaar-maker before h15** (a Railway variable change = maker redeploy, inside the gap only). Quote rule shown; this stops 6 refused matches per session for the rest of the day.
     - ≥ 1 `gone` and `bench_points` > 0.500 → keep `probe` (limit rule shown).
     - ≥ 1 `gone` and `bench_points` < 0.500 → set `exact` (the "nothing checked" world, or probes outside a limit settled).
     - `gone` with `bench_points` exactly 0.500, or only `dropped` → ambiguous; keep `probe` (cost ≤ one session's small loss).
- **Precondition (not a kill condition):** the keeper logs `bench probe (exact + non-crossing probes)` within 100 s of any restart, and no two maker containers overlap near a bench. If either fails, change nothing more: an overlap inside a bench is the one way to lose a whole session.
- **Readout after each session:** the maker logs `bench probe … QUEUED`, `… REFUSED <code>`, then `gone` or `dropped` next tick. `/api/me` `bench_points` > 0.500 proves the limit rule. From Sunday, `bench_books` (#261) stores the whole book, so the session can be replayed afterwards.
- **P&L (final points).** Three server worlds: limits checked P 0.35–0.55, quotes checked P 0.35–0.55, nothing checked P ≈ 0.1 (the probe's own premise that openapi's "ask ≤ price and price + fee ≤ bid" may not be enforced allows this one too). P(sessions above the stall | limit) ≈ 0.5 (sim 0.50–0.58; real books 100 % / 56 % of simulated limit draws):

  | Reading of the bench score, limit world | S1: round 2 (h14.65 + h15) | S1: round 3 (h17 + h19) | S1 total | S2: round 3 only (h17, h19, h21) |
  |---|---|---|---|---|
  | B, round average (fitted ±0.004, BBS §1): any positive round margin lifts the round 0.5 → 1.0 = +4.5 final | +0.8 to +1.25 | +0.8 to +1.25 | **+1.6 to +2.5** | +0.8 to +1.25 |
  | A, per-session average: a session above the stall = +0.56 final in round 2 (8 sessions), +2.25 in round 3 (2 sessions) | +0.2 to +0.3 | +0.8 to +1.25 | **+1.0 to +1.55** | ≈ +0.8 to +1.25 (1.5 per session × 3) |
  | Quote world (P 0.35–0.55) | 0 (6 refused POSTs per session until the §3.1 rule switches to exact) | 0 | 0 | 0 |
  | Nothing-checked world (P ≈ 0.1) | up to 6 midpoint probes per session settle; those outside a hidden limit realise negative gains (mid-gain −10 to −2 P on the b52/b69 pairs with gaps 17–21) and pull the session below the stall: ≈ −0.1 (reading A) to −0.3 (reading B) per round, capped at one session by the §3.1 rule → EV ≈ −0.01 to −0.03 | | | |
  | Limit world, dropped probes | ≤ −0.03 per session (a probe queued then dropped may remove a near-miss pair) | | | |

  Total EV (S1): +1.0 to +2.5 after weighting by P(limit) and netting the third world (previously +1.1 to +2.7 with P(limit) 0.4–0.6 and two worlds). I lean on reading B (it fits t03/t13 at ±0.004). The pitch must not quote "+4.5" on its own.

### 3.2 Book B: organic flow on v19 (the "market it to agents" half)

What §2.3 supports (correlation, with counterexamples):
1. A counterparty on the same venue, best of all a **resident bidder**: v19's 15 asks came after notices and died with no buyer.
2. **Team threads and swaps between other teams hosted on v19.** RULES.md:60 and :64 say every team-to-team trade happens on a venue and threads are opened on one, so a thread deal between two other teams opened on v19 counts for our venue. Nothing we send asks for this yet.
3. A concrete, actionable line.

Addressed listings alone are not enough (v13, v15: hundreds, 0 trades), and credit that lands can be wiped (t12, t07). Fee is not a lever below 0 (a rebate is feeding; v02's 5 P/card maximum coincided with no further trades). Midpoint pairing is our one honest price argument: a crossing ask of 10 and bid of 14 settle at 12, so **each side gains 2 P compared with lifting the other's quote, and nobody pays El Rastro's 5 % + 1 P**.

**Message wording.** Each message must stay ≤ 240 chars, name only public facts and never instruct another team's agent (v24's "migrate open book — POST …" style invites flags):
- *Near-miss matchmaking* (hand, approved one by one; the best notice type, MM_STRATEGY §3.2):
  > `RET-03: bid 6 on v21 (#18518), ask 10 on El Rastro (#18425). Meet at 8 on v19: 0 fee, our broker pairs crossing quotes at the midpoint every tick. Taking it on El Rastro costs the taker 1.4 P more.`

  Send it only while a live near-miss with a gap ≤ 5 P exists. At the close there were 0 (`evidence/spreads_close.out`), so check the books first.
- *Echo a real listing*: **dropped as a hand action.** Saturday's v19 listings expired in 6–10 ticks, which is 1.5–2.5 min at 15 s ticks, too short for a hand-approved notice. Only worth it automated in the keeper (a code item, §5, low priority).
- *The MM2 automatic notice* (#238, live from 09:00, `agents/venue_notice.py:59-82`): "… Wanted now: A, B, C (teams miss them for a page). Post asks and bids here, public or addressed." It names cards we hold that other teams miss.
  - It reaches holders who want to sell, but buyers are not on v19, so on its own it is one-sided.
  - It goes out every 10 ticks (2.5 min at 15 s ticks), which is the server's measured cadence: 95 of 297 Saturday notice gaps were exactly 10 ticks, none below (`venue_keeper.py:64-67`). It is capped at 24 per game hour.
- *The resident bidder invite* (one thread, hand-sent, Sunday 09:05) to the most active public buyer (t06, t04, t16 per MM_STRATEGY §3.2). **Open the thread on `rastro`, not v19**: we cannot trade on our own venue (RULES.md:76), so a thread of ours on v19 cannot close a deal and the open may be refused; the desk opens all its threads on `rastro` for the same reason.
  > "mirror your standing bids on v19, and open your threads and swaps with other teams there: 0 % fee, our broker pairs crossing quotes at the midpoint every tick; on El Rastro the side that accepts pays 5 % + 1 P per card."

  It is an invitation only: no payment, rebate or reciprocity (RULES.md:131). It uses one of 6 conversations per team (RULES.md:109), shared with dealer threads and with the desk (`team_threads_max_open` 2, GUARDRAILS.md:106), so send it when the desk holds a free slot and close it after the message.
- *The swap-proposal invite* (#251, `team_words_venue_invite` = v19; text in `agents/team_desk.py:77-82`): one line in every desk proposal, in Spanish, inviting the team to post its offers on v19. **It does not ask them to host their team threads and swaps there.** Suggested added clause (code + taker redeploy, §5): "… y abre allí tus hilos y cambios con otros equipos: comisión 0 %." ("… and open your threads and swaps with other teams there: 0 % fee.")

Design rules (**hand guidance only**: the live keeper posts every 10 ticks, `ANNOUNCE_EVERY_TICKS` at `venue_keeper.py:66`, with no bench hold; the code change is in §5):
- **Target:** near-misses only.
- **Cadence:** hand notices only while a fresh near-miss exists, silence otherwise. On Saturday, notice count did not separate venues (MM_DEEP §6).
- **Windows:** right after the CHA release and the +150 P grant, when demand is fresh and every team has cash (S1: 12:16–13:30; S2: 09:00–10:00).

**P&L, re-derived after review.** EV = P(≥ 1 trade between other teams on v19 on Sunday) × the credit that survives. Surviving credit for a low-volume venue ≈ +0.65 final (range 0 to +1.6; §2.3, including v10 and v11 at 0). P(≥ 1 trade) is my estimate against v19's measured base rate of 0 trades in ~1,180 Saturday ticks (15 asks, no bid):

| Plan | P(≥ 1 trade) | Expected final points |
|---|---|---|
| Nothing new (MM2 notice + swap invite run by themselves) | 0.03–0.08 | ≈ +0.02 to +0.05 |
| Plus hand near-miss notices | 0.05–0.15 | ≈ +0.03 to +0.1 |
| Plus a resident bidder who mirrors bids, and the thread-hosting clause | 0.15–0.35 | ≈ +0.1 to +0.25 |

The previous table (+0.6 to +1.3) used MM_STRATEGY §3.4's probabilities and the survivor-only +1.04; both were too high.

Cash at risk: 0. Inventory: none. We are never a party.

### 3.3 Book C: our own quoting (negotiating column, kept brief)

- **Quote sizing:** one copy per ask. Duplicates only: `protect_page_sets` covers every set, with the two exceptions in GUARDRAILS.md:48.
- **Spread by counterpart need:**
  - Price to the trade print, not to the asking tape. Trades fill at 0.71–0.87 of the median ask, and only 1.8 % of our asks filled.
  - Charge above that only to a team for which the card completes a page. `team_matrix` `missing_for_page` already ranks those teams, and the maker has `buyer_rank_enabled` (off). Keep in mind that addressed asks filled 6 % against 19 % for public ones on Friday (GUARDRAILS.md:137).
- **Inventory limits:** none to set.
  - Buys are capped at the official value of one more copy (`official_value_margin` 0), and off-page cards strictly below it. Sales must not lose score (`max_score_loss_per_move` 0.001), and `no_buyback_ticks` is 480.
  - So every fill is ≥ 0 in neg_points **as estimated by `move_impact`** (the guard's estimate, which the SAL-07 incident showed can be wrong), and we never carry resale inventory. Adverse selection is bounded, not absent: a fill means the counterparty values the card more than our ask, which is an opportunity cost (we could have asked more), not a score loss. Spread capture in primas is not the game; capture of private-value differences is.
- **Channels and caps:** the maker's asks, the desk's threads and swaps, R2's invite and the taker all use the team key (5 req/s shared, `max_accepts_per_tick` 1 shared with duels); the probe and venue notices use the broker key, whose separate bucket is assumed, not tested (MARKET_TEST_H11_READY blocker 3).
- **Cash at risk:** the cash behind standing bids (the approved off-page order) plus the venue bond. Both are bounded by `max_spend_per_game_hour` 250 and `cash_floor` 5.
- **P&L:** at k_gain ≈ 0.033 board per neg_point, a sale with +10 P of surplus over our value ≈ +0.33 displayed ≈ **+0.2 final**. Whether round 3 resets `neg_points` is unknown (KSE §5).
- **Desk:** threads and swaps stay on `rastro` (never v19, `self_venue`). Hosting our own deals on another team's 0-fee venue saves the fee but hands that team organic credit (our v02 and v10 buys on Saturday did); on `rastro` nobody gets credit.
- **Kill conditions:** already in the guards (score-loss refusal, breakers).

## 4. Recommendations for Sunday, ranked by expected points

| # | Recommendation | Expected (final) | Concrete change | Risk |
|---|---|---|---|---|
| R1 | **Keep `BAZAAR_BENCH_POLICY=probe` on bazaar-maker**, and touch nothing in the §3.0 windows | +1.0 to +2.5 expected (+2.8 to +4.5 if limits rule, 0 if quotes rule, ≈ −0.1 to −0.3 if nothing is checked) | none now. At 09:00, run the §3.0 clock check first. **After h14.65, apply the §3.1 readout rule** (switch to `exact` before h15 on refusals with nothing gone, or on `gone` with bench_points < 0.5) | Under the quote rule the latch never fires, so without the manual rule it sends 6 refused matches per session all day: the RULES.md:84 optics risk (bond cut, venue scores 0 afterwards). The first live run is the hard test |
| R2 | **One resident-bidder invite (hand, 09:05)**, asking for mirrored bids *and* hosted threads/swaps on v19, plus near-miss notices only when a live gap ≤ 5 P exists | +0.1 to +0.25 (estimate, §3.2) | `POST /api/threads {"with": <buyer>, "venue": "rastro"}` + 1 message (§3.2 text), then close it; `uv run bazaar venue announce "<text>" --live` | One of 6 conversations, shared with dealer threads and the desk (max 2 open). Fair play: invitation only. **Hand notices from a laptop:** set `BAZAAR_VENUE=v19` and `BAZAAR_BROKER_KEY` in the environment, since `config.py:235-237` otherwise reads `.local/broker.env` |
| R3 | **Ask the organisers in person at 09:00** whether a bench match priced inside both hidden limits is accepted when the quotes do not cross | De-risks R1 (and settles R5) | none | Free. Do not wait for the answer |
| R3b | **Fix the probe's stop in code (optional, Marius's call)** | protects optics; 0 points | `bench_probe.py:142-156`: count POST refusals (code ≠ `dropped`) separately and latch `quote_rule` when a run has ≥ 4 of them and nothing queued, instead of `refused ≥ give_up_after` (8, unreachable). Do **not** just set `DEFAULT_GIVE_UP_AFTER` ≤ 6 at `:39`: drops count as refusals, so under the limit rule six drops in one run would wrongly stop probing for the day. Test in `tests/test_bench_probe.py` with the **default** `ProbeConfig`: 4 POST-refused probes in run b1 → `quote_rule` true and `plan()` empty for run b2; 6 `dropped` → latch stays off | Code + maker redeploy (only in a gap, before 10:12 or between benches); the latch lives in process memory, so a restart resets it. Not needed if the §3.1 manual rule is applied |
| R4 | **Price maker asks to the trade print, by need** | ≈ +0.2 per +10 P surplus sale | `buyer_rank_enabled` stays false unless Marius wants addressed asks (GUARDRAILS.md:137); the relist steps already move toward the median fill (GUARDRAILS.md:30) | Addressed asks fill less often |
| R5 | **Do not open a second (auto "hedge") venue** until the organisers confirm that a team may run two at once (add it to the R3 question) | 0 now; if allowed, it would floor the nothing-checked world (≈ −0.1 to −0.3) and the ≤ −0.03 drops, via "best venue open" (RULES.md:81) | none. `max_venues` 2 (GUARDRAILS.md:98) stays, unused | Evidence against: no owner ever had two venues open in `feed_events` (t13's v22→v23→v24 each opened on the tick the previous one closed; t02 reopened only after v04's refund; t14's v25 replaced its starter stall), and `bazaar_sim/broker.py:56` refuses a second opening with `venue_exists` ("you already run …"). If the server *replaces* instead, an opening would close v19 and the probe would run on nothing. `/me` `bench_venue` is the readout if it is ever tried: read it right after opening and close the second venue before the next bench unless it still says `v19`, because if `bench_venue` moved to an auto venue it would cap us at the stall and zero R1. Costs 20 P plus 250 P bond held against `cash_floor` 5, cash that otherwise funds negotiating buys. The CLI saves the new venue's broker key to `.local/broker.env` (`venue.py` `save_broker_key`), so later hand notices from that machine would go to the new venue unless `BAZAAR_VENUE`/`BAZAAR_BROKER_KEY` are set |
| R6 | **Leave `BAZAAR_BENCH_GUARD_MARGIN=5`** unless the policy variable is changed anyway (then delete both in the same redeploy) | 0 | Railway bazaar-maker | Every variable change redeploys the maker; never inside a window |
| R7 | **Never pause the maker or touch the kill switch during a bench** | protects 0.5 per session | process | `broker.py:248`: the broker reads the kill switch and pause file per match, so a pause during a bench stops matching and the board venue scores below the stall for that session |

## 5. Guardrail and flag changes, and their risk

| Flag / setting | Now | Change needed for this plan | Risk if changed |
|---|---|---|---|
| `allow_venue_open` (GUARDRAILS.md:95) | true | none; keep true | false stops every broker match and notice: the board venue then matches nothing (below the stall), with no bond reserve |
| `max_venues` (:98) | 2 | none; do not act on it (R5) | its premise (two venues at once) is unverified; opening a second venue may be refused or may replace v19 |
| `BAZAAR_BENCH_POLICY` (Railway, maker) | probe | `exact` after h14.65 **if** the §3.1 rule says so (gap only) | `exact` = Saturday's 0.5. `edge` never ran a live bench; its 0.000–0.003 is a simulator column with live-style expiries (MM_DEEP §2), and **main has no confirm gate for it** (#231's `BAZAAR_BENCH_EDGE_CONFIRM` is not on main): a typo-free `edge` goes live on the next redeploy |
| `BAZAAR_BENCH_GUARD_MARGIN` | 5 | optional delete, bundled with any other maker variable change | a redeploy |
| Probe caps (`bench_probe.py:36-40`) | gap 20 / per tick 4 / tries 3 / give-up 8 / per session 6 | none for the caps; the stop logic fix is R3b | code + redeploy; give-up 8 > per-session 6 makes the stop unreachable |
| Notice cadence (`venue_keeper.py:66-67`) | every 10 ticks, ≤ 24 per game hour, no bench hold | optional: skip `_maybe_announce` while the book holds `bench_offers` (`venue_keeper.py:389`) | code + maker redeploy; three Saturday notices fell inside benches, harmless to the score |
| Swap invite text (`team_desk.py:77-82`) | "post your offers on v19" | optional: add "and open your threads and swaps with other teams there" | code + taker redeploy; words only |
| `team_words_venue_invite` (:117) | v19 | none | `none` removes the only automatic invite to teams we already talk to |
| `deploy_guard_bench_ticks` (:145) | 10 | consider 20 for Sunday (10 ticks is only 2.5 min at 15 s; a maker handover overlap takes 58–100 s) | a GUARDRAILS.md commit redeploys everything; do it before 09:00 or not at all |
| `max_score_loss_per_move` (:158), `official_value_margin` (:27), `protect_page_sets` (:47) | as is | none (they make Book C safe) | — |

## 6. Pitch material

### 6.1 For the judges: one paragraph plus numbers

> **Market making as a bounded experiment.** Our broker ran five Market Tests on Saturday and tied the free stall exactly in all five: 25 pairs, every one queued, 0 refused, bench points 0.500. A simulation showed that even a broker that *knows* every hidden limit ties too, if it waits for quotes to cross. The gains it leaves are pairs whose quotes almost meet, gaps of 1–5 primas, whose traders walk away. So we built a bounded test of a rule we had never seen tested: does the server accept a match priced inside both traders' hidden limits when their quotes do not cross? The probe sends the stall's exact plan first and unchanged, then at most six near-miss pairs per session at the midpoint, with no cash at risk, and we read the result after the first session and switch it off if the server refuses. Replayed on two real books with simulated hidden limits, it beats the stall in expectation by 6.2 and 2.3 efficiency points (above it in 100 % and 56 % of draws); under the quote rule it is the stall, by construction. On the venue side we learned that fees don't win flow (almost every venue charges 0) and that venue credit can vanish as fast as it comes. From Sunday our venue's notices name the cards other teams miss, and every swap we propose invites the other team onto our market.

Numbers that hold up, each with its source in §2:

| Number | Source |
|---|---|
| 5 / 5 broker sessions at exactly the stall (plus h3 on the auto starter stall); 25 pairs, 0 refusals | `me_snapshots`, `executions` |
| Truth bound: 0.876 vs the stall's 0.878 | MM_DEEP §2 |
| Near-miss gaps of 1–2 P that were provably inside both limits | real books b52, b69 |
| Replay with simulated hidden limits (books rebuilt from 5–10 s reads): +0.062 (b52, 100 % of draws above) / +0.023 (b69, 56 %) mean efficiency | `evidence/replay_real_books.txt` |
| ≤ 6 probes per session, 0 cash at risk; switched off by hand after the first session if refused | `bench_probe.py:36-40`, §3.1 |
| 42 team-venue trades on Saturday across 10 venues; 3 of those 10 venues ended with no organic credit (t12's v02 lost 4.9 displayed at tick 910 despite 11 trades) | `feed_events`, `leaderboard_snapshots` |
| v07: 390 of 552 listings addressed | `feed_events` |

**Do not claim:**
- that we beat the stall (unproven until a probe settles);
- "+4.5" on its own (it is +1.0 to +2.5 expected: +2.8 to +4.5 if limits rule, 0 if quotes rule);
- that the wanted notice works (never run live);
- that venues fill "because of addressed deals" (v13 and v15 had hundreds and 0 trades);
- that the probe "stops itself" (§2.1).

If a probe settles on Sunday, the claim becomes "first team above the stall". Under reading B, while round 2 is the newest finished round, that shows on the public board as market 7.50 → ≈ 15: Friday had no Market Test (market 0 for every team, weight 0.5), so the display is (0.5 × 0 + 22.5) / 1.5. Once round 3 counts, the display mixes rounds and the jump is smaller unless round 3 is lifted too.

### 6.2 For other teams' agents: venue copy (what to put where)

- **v19 name and description:** fixed at opening ("Team 1 market"), cannot change; only the fee can (PATCH). A better-named second venue is not an option unless the organisers confirm two venues (R5).
- **Notices:** use the §3.2 templates. Each one should name a card, a side, a price, an offer id and our venue id, the fields an agent can act on. Never use imperative API instructions aimed at other agents.
- **Threads and swaps:** the one ask we have never made: "host your team threads and swaps with other teams on v19: 0 % fee, against El Rastro's 5 % + 1 P per card" (in R2's invite and, after a code change, in the desk's invite line).

## 7. Open questions for Marius

1. **At 09:00, S1 or S2** (§3.0)? Does round 3 start with the doors, and do h14.65/h15 still run in round 2? This decides where Book A's EV lands.
2. **Was the h13 hand probe sent?** If yes, what did the script print (REFUSED code, or QUEUED then gone/dropped)? A refusal code would settle the quote rule tonight and make R1 worth 0 (harmless to keep). A QUEUED result followed by 0.500 would be the ambiguous case.
3. **Second venue (R5):** do you want the organisers asked whether a team may run two venues at once? Until then, keep `max_venues` 2 unused.
4. **Resident-bidder invite (R2):** approve the thread text and the target team.
5. **Organisers (R3):** will you ask in person, and should we tell them up front that the probe is bounded to 6 per session and switched off by hand after one session if refused?
7. **Probe stop (R3b):** apply the manual §3.1 rule only, or also ship the code fix (maker redeploy in a gap)?
8. **Desk invite text:** add the "host your threads and swaps on v19" clause (taker redeploy)?
6. **`deploy_guard_bench_ticks` 10 → 20** for 15 s ticks: commit before 09:00, or leave it?

## 8. What I could NOT verify

- **Whether the h13 probe was sent.** There is only an absence of evidence: no local log (the bench-beat-stall worktree was removed), no transcript call, nothing in zsh history.
- **Which scoring reading holds** (per-session vs round-average bench points, and the above-stall branch "full at the top-3 mean", which no team has reached yet). The EVs show both readings.
- **Whether the server checks quotes or limits.** This is the whole point of the probe. The P(limit) of 0.4–0.6 is a prior, not a measurement.
- **The h11 and h13 bench books:** only our matched pairs are stored, so "h13 had near-misses" is inferred from thin matched spreads and low efficiency.
- **Saturday's maker logs:** `railway logs` returned only the current container's start-up. Probe or edge log lines for h11 and h13 were not read; the evidence is `decisions` and `executions`.
- **How other teams' routers choose a venue** (§2.3 is a reading of `feed_events`, not of their code).
- **Whether the server allows two venues per team** (R5): evidence leans no (no precedent; our simulator refuses), but the real server's answer was never observed.
- **The gain-side k** rests on one event (LAT-10, tick 1304) with duel drift netted out by eye from neighbouring refreshes.
- **Sunday wall times:** they assume 240 ticks per game hour at 15 s ticks and no organiser re-timing.
- **Why t12 and t07 lost their credit** (value-destroying trades, an organiser review, or something else): the timing is measured, the mechanism is inferred.
- **The probe's behaviour under refusals** was reproduced by the reviewer in a scratch run; I verified the code path by reading (`bench_probe.py:117,139,151-156`), not by running it.
- **P(nothing checked) ≈ 0.1** is a guess; it exists as a world because the probe's premise is that openapi's rule may not be enforced.

## 9. Revision after review (what changed)

| Finding | Status | Where |
|---|---|---|
| 1 HIGH: the 8-refusal stop can never fire | fixed: §2.1 explains it, kill condition 4 replaced by the h14.65 readout rule (§3.1), code fix as R3b with file, line, test and why `give_up_after` ≤ 6 alone is wrong, pitch corrected | TL;DR, §2.1, §3.1, §4, §5, §6.1 |
| 2 HIGH: organic credit not per-trade nor permanent | fixed: counterexample table (t12, t07, t05, v13, v15), first-trade value re-derived on all low-volume venues (+0.65 final, not +1.04), Book B EV cut to +0.05 to +0.25, "venues fill on addressed listings" qualified, v02 removed from the pitch's success numbers | TL;DR, §2.3, §3.2, §6.1 |
| 3 fee 0 everywhere | fixed (v02: 10 % + 5 P at 941, 0 % + 5 P at 1051, no trade after 903) | TL;DR, §2.3, §3.2 |
| 4 notices "no response", hygiene count | fixed: responses by timing (all asks, no bidder), 3 notices inside benches, echo template dropped as a hand action | §2.2, §2.3, §3.2 |
| 5 hedge venue | already reversed before this review (78696eac: "do not open"); added the `bench_venue` consequence, the post-open check, the CLI key trap and the cash cost | §4 R5 |
| 6 third server world | added (nothing checked, P ≈ 0.1) with its downside and the kill rule that caps it | TL;DR, §3.1 |
| 7 threads and swaps channel | added: desk numbers (83 threads, all on `rastro`), the thread-hosting ask, R2 opened on `rastro`, slot interplay | §2.4, §3.2, §3.3, §4, §6.2 |
| 8 pitch overclaims | fixed: 5 broker sessions, "a bounded test of a rule we had never seen tested", "in expectation with simulated limits" in b52-then-b69 order, "from Sunday" | §6.1 |
| 9 design rules not in code | fixed: cadence marked hand guidance, keeper code change named; kill condition 3 restated as a precondition | §3.1, §3.2, §5 |
| 10 data windows and wording | fixed: Saturday counts 7,670 / 601 / 426; edge row; `move_impact` estimate and adverse selection; key and caps line. **Partly argued:** the displayed "≈ 15" holds while round 2 is the newest finished round, because Friday's market is 0 for everyone in the denominator; qualified for once round 3 counts | §1, §3.3, §5, §6.1 |
