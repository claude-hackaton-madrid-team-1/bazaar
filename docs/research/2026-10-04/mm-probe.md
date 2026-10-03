# Market making with the probe: what Saturday measured, and Sunday's plan (sat-mm-probe)

Written Sun 4 Oct 2026, 00:15–01:00 Madrid, by the `sat-mm-probe` research session for Team 1 (t01). Read-only on the game.
The doors are closed (`/api/clock` at 00:21: tick 1445, t 13.367, paused, next opening 09:00).

## Three ambiguities, read these first

1. **"Market that" has two meanings, and this report covers both.** (a) How to *sell our venue* to other teams' agents, so that they trade on v19: wording, what their agents read, fees. See §3.2 and §6.2. (b) How to *present market making to the judges*: it is a scored criterion, and the pitch is ≈ 40 %. See §6.1.
2. **The brief's words (fills, spreads, inventory, cash at risk) describe classic two-sided market making, but the scored "market-making" criterion is something else.** It is the Market Test efficiency plus the value created between *other* teams on our venue (RULES.md:119). Neither part uses our cash or cards, and we cannot trade on v19 (RULES.md:76, `self_venue`). Our own quoting of cards (the maker's asks and bids) scores in the *negotiating* column, at our private values (RULES.md:118). So the Sunday design below has three books:
   - **A**: the bench (probe broker);
   - **B**: organic flow on v19;
   - **C**: our own quoting. C is kept short because it belongs to the other criterion.
3. **"Our probe strategy" means the bench probe** (`BAZAAR_BENCH_POLICY=probe`, PR #257). That is the market-making one. The taker also has a `ladder_probe` (Jev-gated dealer price probes, 13 rows on Saturday), which is unrelated and not covered here.

## TL;DR

- **The probe has never run live, and it now runs by default.** No evidence was found that the h13 hand probe was sent: there is no local log, no transcript or shell-history call, and `bench_points` stayed exactly 0.500. PR #257 merged at 00:18, and bazaar-maker is already on `BAZAAR_BENCH_POLICY=probe`: its keeper line reads `broker bench probe (exact + non-crossing probes)`, and the maker is waiting for 09:00. Sunday's first bench is the hard test (12 traders, firmer, more impatient), and it becomes the probe's first live test.
- **Saturday's bench result: 6 of 6 sessions at exactly the stall (bench_points 0.500).** Our broker made 25 pairs; the server answered every one `{"queued": true, "settles_at_tick": T+1}`, with 0 refusals (`executions`). The `edge` setting was live in the environment for h11, but it never overrode exact: all 6 h11 pairs carry the exact reason string. No team beat the stall all day: every team above 7.50 on the board has trades on its venue.
- **Organic flow on v19 was zero.** Since tick 612 no other team listed anything on v19, so it had 0 listings, 0 trades and 0 traders. 37 generic notices on Saturday drew no response. The venues that fill run on *addressed* listings and resident buyers (v07: 390 of its 552 listings were addressed), not on fees, because every venue already charges 0. Our venue's name cannot change (PATCH sets the fee only). The two new levers, the MM2 "wanted cards" notice (#238) and the venue invite inside swap proposals (#251), merged at 23:25 and have never run live.
- **Sunday expected value (final leaderboard points; ranges, not promises):**
  - Book A (probe): **+1.1 to +2.7 expected**. That figure is already weighted by P(limit) 0.4–0.6 and by P(sessions above the stall | limit) ≈ 0.5. If the server checks hidden limits, the probe is worth +2.8 to +4.5; if it checks quotes, 0. The low end assumes per-session bench averaging, the high end the round-average reading fitted on Saturday. Cash at risk 0; worst case about −0.03 per session.
  - Book B (organic on v19): +0.6 to +1.3 (estimate; v19's measured base rate is 0).
  - Book C (our quoting): about +0.2 final (+0.33 on the board) per 10 P of surplus on a sale. **New measurement:** one team sale (LAT-10, tick 1304) showed that gains do count, at k ≈ 0.033 board per neg_point, against 0.048 on the loss side.
- **Before anything else at 09:00:** read `/api/clock` and `/api/schedule`. The schedule pins "Sunday opens" and "Round 3 starts" at t 16.65, while the clock froze at 13.367. If round 3 starts at the 09:00 opening, the two remaining round-2 benches (h14.65, h15) change rounds or never run, and every round-2 number above moves. Then:
  - keep `probe`;
  - consider the auto hedge venue (≈ 20 P buys a stall-level floor under every probed session);
  - make no deploys, variable changes or pauses inside the bench windows (§3.0).

---

## 1. Method and data windows actually seen

- **Postgres** (shared ledger, read-only session `default_transaction_read_only=on`, helper `mm-probe/q.py`, URL never printed):
  - `decisions` (broker_match, venue_announce, maker post_ask/bid), `executions` (match responses), `me_snapshots` (t01, ticks 159–1445), `leaderboard_snapshots` (last real snapshot at tick 1430), `feed_events` (ticks 160–1445 = round 2 = Saturday; 8,422 `offer.listed`, 793 settlements, 429 notices), `cards`.
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
  | Refusals before it stops | 8 with nothing gone; after that, exact only for the life of the process |

- **What the probe tests:** whether `POST /api/broker/matches` checks the *quotes* (openapi: "ask ≤ price and price + fee ≤ bid") or the traders' *hidden limits*. The kit's own broker docstring points to limits ("a quote is not a limit … a broker that estimates those limits … beats the stall", `vendor/bazaar-kit/starter_broker.py:88-91`, quoted in MM_STRATEGY §2.0).
  - Under the quote rule every probe is refused and the score equals the exact broker's.
  - Under the limit rule a settled probe takes gains that the stall can never take.
- **Caveat:** the `quote_rule` latch lives in process memory, so a maker restart allows another 8 refusals. Each refusal is a 4xx that "costs nothing and moves nothing" (RULES.md:150).

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
- **Hygiene.** One generic notice went out at tick 1166, inside the h11 bench (`feed_events` venue.announcement). It was harmless, but the keeper does not hold notices during a bench.

### 2.3 What Saturday measured for organic flow (v19)

- **v19:** 0 trades, 0 traders, 0 pairs (`/api/venues` 00:21). Only 15 listings by others ever: 13 from a t15 burst at ticks 373–381, and 2 from t04 for MAL-06 at 29 and 27 P at ticks 550 and 612. Nothing after 612 (`feed_events` offer.listed venue=v19).
- **Notices:** 11 notices from tick 1110 to the close, all with the same generic text ("0 % fee … our broker pairs crossing bids and asks every tick, at the midpoint"), and 37 accepted plus 16 refused `wait` over the day. Response: 0 listings.
- **Team-venue trades on Saturday:** 42 settlements in total. v02 11, v07 11, v21 6, v01 3, v11 3, v16 2, v10 2, v06 2, v14 1, v17 1. Most were commons at 4–10 P; two 0 P swaps on v11 counted too.
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

  Most of this flow is three teams' agents posting **addressed** offers on other teams' venues: t08 on v07, v21, v01, v02 and v11; t05 on v07 and v15; t13 on v13, v02, v11 and v07.
- **What their agents read (inferred).** These routers do not simply follow trades: t05 first posted on v07 at tick 260, before v07's first trade at 351, and t08 posted on v01 at 231, before its first trade at 286. They never picked v19, which opened at tick 262. Each picked venues that opened early (100–201) or a team's starter stall. Possible readings: ordering by venue id or age, or by name and description. None is verified. All public venues are at 0 bps, so **fee is not a differentiator**: fees cannot go below 0, and rebates are "feeding" (RULES.md:131-132).
- **Venue names and descriptions** are the parseable fields (`/api/venues`: `name` ≤ 40 chars, `description`, `fee_bps`, `mechanism`, `trades`, `traders`).
  - Ours: "Team 1 market", described as "Board venue, 0 % fee: crossing offers are matched every tick".
  - The fullest one: v07, "Team 10 · fair broker, 0 fee", described as "Zero fees. A broker matches every crossing pair card by card at the midpoint, any copy included. Built by Team 10 for everyone."
  - Only the fee can be changed after opening (openapi `PATCH /api/venues/{vid}` = "Venue Fee").
- **Value of one trade:** the first trade on an empty venue moved four venues' market by +1.40 / +1.48 / +1.85 / +2.22 displayed (MM_PLAN §1, MARKET_MOVES §6), mean +1.74 displayed, which is ≈ +1.04 final (× 1.5 / 2.5). Later trades decay. The cap is 7.5 round points per round (t10 sat there all evening).
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
- "Each session counts your best venue open during it (none open counts 0) and the round averages its sessions, so closing a venue after a good session keeps nothing." (:81). This sentence is the basis for the auto hedge venue.
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
  3. No `bench probe` keeper line within 100 s of a restart, or two maker containers overlapping near a bench. In that case do nothing more, because an overlap inside a bench is the one way to lose a whole session.
  4. Do **not** roll back merely because probes were refused: under the quote rule the probe stops itself after 8 refusals and costs nothing.
- **Readout after each session:** the maker logs `bench probe … QUEUED`, `… REFUSED <code>`, then `gone` or `dropped` next tick. `/api/me` `bench_points` > 0.500 proves the limit rule. From Sunday, `bench_books` (#261) stores the whole book, so the session can be replayed afterwards.
- **P&L (final points)**, with P(limit) between 0.4 and 0.6 (the MM_STRATEGY prior; no new evidence moves it) and P(sessions above the stall | limit) ≈ 0.5 (sim 0.50–0.58; real books 100 % / 56 %):

  | Reading of the bench score | S1: round 2 (h14.65 + h15) | S1: round 3 (h17 + h19) | S1 total | S2: round 3 only (h17, h19, h21) |
  |---|---|---|---|---|
  | B, round average (fitted ±0.004, BBS §1): any positive round margin lifts the round 0.5 → 1.0 = +4.5 final | +0.9 to +1.35 | +0.9 to +1.35 | **+1.8 to +2.7** | +0.9 to +1.35 |
  | A, per-session average: a session above the stall = +0.56 final in round 2 (8 sessions), +2.25 in round 3 (2 sessions) | +0.23 to +0.34 | +0.9 to +1.35 | **+1.1 to +1.7** | ≈ +0.9 to +1.35 (1.5 per session × 3) |
  | Quote rule (P 0.4–0.6) | 0 | 0 | 0 | 0 |
  | Downside, either rule | ≤ −0.03 per session (dropped probes remove ≤ 6 near-miss traders) | | | 0 with the hedge venue (§3.4 R2) |

  I lean on reading B (it fits t03/t13 at ±0.004). The pitch must not quote "+4.5" on its own.

### 3.2 Book B: organic flow on v19 (the "market it to agents" half)

What attracts flow, from §2.3:
1. A counterparty on the same venue, best of all a **resident bidder**.
2. **Addressed** deals hosted on the venue.
3. A concrete, actionable line.

Fee is not a lever (all venues are at 0; a rebate is feeding). Midpoint pairing is our one honest price argument: a crossing ask of 10 and bid of 14 settle at 12, so **each side gains 2 P compared with lifting the other's quote, and nobody pays El Rastro's 5 % + 1 P**.

**Message wording.** Each message must stay ≤ 240 chars, name only public facts and never instruct another team's agent (v24's "migrate open book — POST …" style invites flags):
- *Near-miss matchmaking* (hand, approved one by one; the best notice type, MM_STRATEGY §3.2):
  > `RET-03: bid 6 on v21 (#18518), ask 10 on El Rastro (#18425). Meet at 8 on v19: 0 fee, our broker pairs crossing quotes at the midpoint every tick. Taking it on El Rastro costs the taker 1.4 P more.`

  Send it only while a live near-miss with a gap ≤ 5 P exists. At the close there were 0 (`evidence/spreads_close.out`), so check the books first.
- *Echo a real listing* (whenever anyone lists on v19):
  > `On v19 now: bid N for CARD (#id). Sellers: list CARD on v19 at N or less and our broker pairs you this tick, 0 fee, at the midpoint.`
- *The MM2 automatic notice* (#238, live from 09:00, `agents/venue_notice.py:59-82`): "… Wanted now: A, B, C (teams miss them for a page). Post asks and bids here, public or addressed." It names cards we hold that other teams miss.
  - It reaches holders who want to sell, but buyers are not on v19, so on its own it is one-sided.
  - Its rotation is every 10 ticks, which is 2.5 min at 15 s ticks. The server allows one notice per 20 ticks, so expect `wait` refusals. They are harmless, but they say the cadence should be the server's.
- *The resident bidder invite* (one private thread, hand-sent, Sunday 09:05): to the most active public buyer (t06, t04, t16 per MM_STRATEGY §3.2):
  > "mirror your standing bids on v19: same bid, 0 fee, our broker pairs at the midpoint every tick; sellers on El Rastro pay 5 % + 1 P to hit you, here 0."

  It is an invitation only: no payment, rebate or reciprocity (RULES.md:131). It uses one of 6 thread slots, which are shared with dealer threads.
- *The swap-proposal invite* (#251, `team_words_venue_invite` = v19): one true line in every team-desk proposal. It is already on.

Design rules:
- **Target:** near-misses only.
- **Cadence:** one notice per 20–40 ticks while a fresh near-miss exists, silence otherwise. On Saturday, notice count did not separate venues (MM_DEEP §6).
- **Windows:** right after the CHA release and the +150 P grant, when demand is fresh and every team has cash (S1: 12:16–13:30; S2: 09:00–10:00).

**P&L (estimate, MM_STRATEGY §3.4; v19's measured base rate over ~1,180 ticks: 0 trades):**

| Plan | Expected final points |
|---|---|
| Hand notices only | ≈ +0.6 |
| Plus a resident bidder | +1.0 to +1.3 |
| Nothing | ≈ +0.05 |

Cash at risk: 0. Inventory: none. We are never a party.

### 3.3 Book C: our own quoting (negotiating column, kept brief)

- **Quote sizing:** one copy per ask. Duplicates only: `protect_page_sets` covers every set, with the two exceptions in GUARDRAILS.md:48.
- **Spread by counterpart need:**
  - Price to the trade print, not to the asking tape. Trades fill at 0.71–0.87 of the median ask, and only 1.8 % of our asks filled.
  - Charge above that only to a team for which the card completes a page. `team_matrix` `missing_for_page` already ranks those teams, and the maker has `buyer_rank_enabled` (off). Keep in mind that addressed asks filled 6 % against 19 % for public ones on Friday (GUARDRAILS.md:137).
- **Inventory limits:** none to set.
  - Buys are capped at the official value of one more copy (`official_value_margin` 0), and off-page cards strictly below it. Sales must not lose score (`max_score_loss_per_move` 0.001), and `no_buyback_ticks` is 480.
  - So every fill is ≥ 0 in neg_points by construction, and we never carry resale inventory. Spread capture in primas is not the game; capture of private-value differences is.
- **Cash at risk:** the cash behind standing bids (the approved off-page order) plus the venue bond. Both are bounded by `max_spend_per_game_hour` 250 and `cash_floor` 5.
- **P&L:** at k_gain ≈ 0.033 board per neg_point, a sale with +10 P of surplus over our value ≈ +0.33 displayed ≈ **+0.2 final**. Whether round 3 resets `neg_points` is unknown (KSE §5).
- **Kill conditions:** already in the guards (score-loss refusal, breakers).

## 4. Recommendations for Sunday, ranked by expected points

| # | Recommendation | Expected (final) | Concrete change | Risk |
|---|---|---|---|---|
| R1 | **Keep `BAZAAR_BENCH_POLICY=probe` on bazaar-maker**, and touch nothing in the §3.0 windows | +1.1 to +2.7 expected (+2.8 to +4.5 if limits rule, 0 if quotes rule) | none (already live). Leave the probe caps alone: a cap change is code + redeploy. At 09:00, run the §3.0 clock check before anything else | Tail risk that organisers read non-crossing matches as rule-breaking (RULES.md:84, bond cut and the venue scores 0 afterwards). The first live run is the hard test |
| R2 | **Open the auto hedge venue before the first Sunday bench**, by hand, outside the windows | Removes the probe's ≤ −0.03 per session downside; may add organic if its name draws routers (unverified) | `uv run bazaar venue open --mechanism auto --live` (GUARDRAILS.md:98 already allows 2), with a parseable name ≤ 40 chars, e.g. "0 % fee · auto-cross every tick" | 20 P spent, 250 P bond locked until close + cooldown. Unverified whether a second venue's organic counts for us, or whether `bench_venue` switches. Opening it is a live write; no redeploy |
| R3 | **One resident-bidder invite (hand, 09:05)** and near-miss notices only when a live gap ≤ 5 P exists | +0.6 to +1.3 (estimate) | `POST /api/threads {"with": <buyer>, "venue": "v19"}` + 1 message; `uv run bazaar venue announce "<text>" --live` (§3.2 templates) | Uses a thread slot shared with dealer threads. Fair play: invitation only |
| R4 | **Ask the organisers in person at 09:00** whether a bench match priced inside both hidden limits is accepted when the quotes do not cross | De-risks R1 | none | Free. Do not wait for the answer |
| R5 | **Price maker asks to the trade print, by need** | ≈ +0.2 per +10 P surplus sale | `buyer_rank_enabled` stays false unless Marius wants addressed asks (GUARDRAILS.md:137); the relist steps already move toward the median fill (GUARDRAILS.md:30) | Addressed asks fill less often |
| R6 | **Leave `BAZAAR_BENCH_GUARD_MARGIN=5`** unless the policy variable is changed anyway (then delete both in the same redeploy) | 0 | Railway bazaar-maker | Every variable change redeploys the maker; never inside a window |
| R7 | **Never pause the maker or touch the kill switch during a bench** | protects 0.5 per session | process | `broker.py:248`: the broker reads the kill switch and pause file per match, so a pause during a bench stops matching and the board venue scores below the stall for that session |

## 5. Guardrail and flag changes, and their risk

| Flag / setting | Now | Change needed for this plan | Risk if changed |
|---|---|---|---|
| `allow_venue_open` (GUARDRAILS.md:95) | true | none; keep true | false stops every broker match and notice: the board venue then matches nothing (below the stall), with no bond reserve |
| `max_venues` (:98) | 2 | none (R2 is a hand open) | 1 forbids the hedge |
| `BAZAAR_BENCH_POLICY` (Railway, maker) | probe | none | `exact` = today's 0.5. `edge` scored 0.000–0.003 live in sim terms (MM_DEEP §2); and **main has no confirm gate for it** (#231's `BAZAAR_BENCH_EDGE_CONFIRM` is not on main): a typo-free `edge` goes live on the next redeploy |
| `BAZAAR_BENCH_GUARD_MARGIN` | 5 | optional delete, bundled with any other maker variable change | a redeploy |
| Probe caps (`bench_probe.py:36-40`) | 20 / 4 / 3 / 8 / 6 | none | code + redeploy |
| `team_words_venue_invite` (:117) | v19 | none | `none` removes the only automatic invite to teams we already talk to |
| `deploy_guard_bench_ticks` (:145) | 10 | consider 20 for Sunday (10 ticks is only 2.5 min at 15 s; a maker handover overlap takes 58–100 s) | a GUARDRAILS.md commit redeploys everything; do it before 09:00 or not at all |
| `max_score_loss_per_move` (:158), `official_value_margin` (:27), `protect_page_sets` (:47) | as is | none (they make Book C safe) | — |

## 6. Pitch material

### 6.1 For the judges: one paragraph plus numbers

> **Market making as an experiment with a kill switch.** Six Market Tests on Saturday told us our broker exactly tied the free stall: 25 pairs, every one queued, 0 refused, bench points 0.500 in all six. A simulation showed that even a broker that *knows* every hidden limit ties too, if it waits for quotes to cross. The gains it leaves are pairs whose quotes almost meet, gaps of 1–5 primas, whose traders walk away. We found the one rule nobody had tested: does the server accept a match priced inside both traders' hidden limits when their quotes do not cross? We shipped a probe that sends the stall's exact plan first and unchanged, then at most six near-miss pairs per session at the midpoint. It stops itself after eight refusals and puts no cash at risk. Replayed on two real books under the limit rule it beats the stall by 2.3 and 6.2 efficiency points; under the quote rule it is the stall, by construction. On the venue side we measured why venues fill: it is not fees, which are all zero, but addressed deals and resident buyers. So our venue's notices now name the cards other teams miss, and every swap we propose invites the other team onto our market.

Numbers that hold up, each with its source in §2:

| Number | Source |
|---|---|
| 6 / 6 sessions at exactly the stall; 25 pairs, 0 refusals | `me_snapshots`, `executions` |
| Truth bound: 0.876 vs the stall's 0.878 | MM_DEEP §2 |
| Near-miss gaps of 1–2 P that were provably inside both limits | real books b52, b69 |
| Replay under the limit rule: +0.062 / +0.023 efficiency | `evidence/replay_real_books.txt` |
| ≤ 6 probes per session, stops after 8 refusals, 0 cash at risk | `bench_probe.py:36-40` |
| 42 team-venue trades on Saturday across 10 venues, 22 of them on v07 and v02; fee 0 everywhere | `feed_events`, `/api/venues` |
| v07: 390 of 552 listings addressed | `feed_events` |

**Do not claim:**
- that we beat the stall (unproven until a probe settles);
- "+4.5" on its own (it is +1.1 to +2.7 expected: +2.8 to +4.5 if limits rule, 0 if quotes rule);
- that the wanted notice works (never run live).

If a probe settles on Sunday, the claim becomes "first team above the stall", which shows on the public board as market 7.50 → ≈ 15 under reading B.

### 6.2 For other teams' agents: venue copy (what to put where)

- **Hedge-venue name** (≤ 40 chars, R2): `0 % fee · auto-cross every tick`. Its description, true for an auto venue: `0 % fee, 0 P a card. The engine crosses the best bid and ask every tick. Post here; we never trade on it.`
- **v19 description:** fixed at opening, cannot change.
- **Notices:** use the §3.2 templates. Each one should name a card, a side, a price, an offer id and our venue id, the fields an agent can act on. Never use imperative API instructions aimed at other agents.

## 7. Open questions for Marius

1. **At 09:00, S1 or S2** (§3.0)? Does round 3 start with the doors, and do h14.65/h15 still run in round 2? This decides where Book A's EV lands.
2. **Was the h13 hand probe sent?** If yes, what did the script print (REFUSED code, or QUEUED then gone/dropped)? A refusal code would settle the quote rule tonight and make R1 worth 0 (harmless to keep). A QUEUED result followed by 0.500 would be the ambiguous case.
3. **Hedge venue (R2):** open it (20 P + 250 bond), and under which name?
4. **Resident-bidder invite (R3):** approve the thread text and the target team.
5. **Organisers (R4):** will you ask in person, and should we tell them up front that the probe is bounded to 6 per session?
6. **`deploy_guard_bench_ticks` 10 → 20** for 15 s ticks: commit before 09:00, or leave it?

## 8. What I could NOT verify

- **Whether the h13 probe was sent.** There is only an absence of evidence: no local log (the bench-beat-stall worktree was removed), no transcript call, nothing in zsh history.
- **Which scoring reading holds** (per-session vs round-average bench points, and the above-stall branch "full at the top-3 mean", which no team has reached yet). The EVs show both readings.
- **Whether the server checks quotes or limits.** This is the whole point of the probe. The P(limit) of 0.4–0.6 is a prior, not a measurement.
- **The h11 and h13 bench books:** only our matched pairs are stored, so "h13 had near-misses" is inferred from thin matched spreads and low efficiency.
- **Saturday's maker logs:** `railway logs` returned only the current container's start-up. Probe or edge log lines for h11 and h13 were not read; the evidence is `decisions` and `executions`.
- **How other teams' routers choose a venue** (§2.3 is a reading of `feed_events`, not of their code), and whether a second venue's organic counts for us.
- **The gain-side k** rests on one event (LAT-10, tick 1304) with duel drift netted out by eye from neighbouring refreshes.
- **Sunday wall times:** they assume 240 ticks per game hour at 15 s ticks and no organiser re-timing.
