# Night notes on market making: redacted digest

This is a digest of the market-making and Market Test notes Team 1 kept **outside git** from Friday 2 to Sunday 4 Oct 2026:
the Saturday analysis files in `_night/`, the Sunday review files in `_sat-review/`, and the task prompts given to each
Sunday session. The originals stay where they are. This file keeps what matters for market making, the Market Test,
organic trading on our venue and the scoring caps.

**Redaction.** Private values (card affinities, limits, your_value, cash, holdings, keys, URLs with credentials) have
been removed or aggregated. Synthetic bench quotes, other teams' public offers and leaderboard figures, and public dealer
prices are kept. Paths to `.env` files, scratchpads and Railway project ids are left out.

**Reading guide.** Sections run in the order the notes were written. "(superseded: …)" marks a claim that later
findings overturned. The main Sunday facts behind those marks are:
- the server checks **posted quotes**, not hidden limits: the one-shot match probe at tick 1692 (session 7, run b120)
  came back `400 bad_match "price must sit between the ask 40 and the bid 39"`;
- so the `probe` policy is dead, and the Saturday-night h13 hand probe was never sent;
- `edge` never deviated from `exact` on a real book, and both real-book replays tie the stall;
- per the Sunday dossier ([`reports/market-making-dossier.md`](reports/market-making-dossier.md)), Sunday's round averages sessions 7–9,
  and session 9 (~12:37 local) was the last bench.

---

## MM_PLAN.md (Sat 13:15, tick ~610)

Source: `_night/MM_PLAN.md`. The first plan for how Team 1 starts scoring on market making. It was written before the
2 h clock pause and was corrected later (§0 note and an appended §6 at tick ~709).

- The first published reading of market scoring: 30 % of the score, made of Market Test efficiency plus value created
  between other teams on our venue. Each session counts our best open venue. "Matching as well as the free auto stall
  earns half the bench points; the full points go to the mean of the top three" (RULES.md:67-84).
- Inferred formula: `market = 30 × (½ × bench_points + ½ × mm_share)` per round, with rounds weighted Friday 0.5 and
  Saturday 1.0. Evidence: 10 of 18 teams sat at exactly 7.5 = 30 × ½ × 0.5. (Later refined to
  `22.5·b + 7.5·organic`; see SCORE_CAPS.)
- Our first two sessions scored exactly 0.5 (efficiency 0.899 on the v08 stall, 0.933 on v19 at h5, run b35, 4 pairs).
  The exact broker (#71) breaks ties in the stall's book order, so on the quote rule it *is* the stall. W1b's simulator
  already showed "greedy and exact equal the stall in every cell". (superseded: "session 1 was Friday" is corrected in MM_DEEP §4:
  it was Saturday h3, round 2. The per-session value 15/7 and the timetable also changed after the clock pause.)
- Organic trades are worth a lot. A single trade between two other teams lifted a starter-stall owner's market by
  +1.40 to +2.22 (t17, t14, t09). Volume alone does not score: t05's v10 had 2 trades and stayed at 7.5. Best reading:
  value is measured at private values and floored at 0.
- v19 had 0 trades and 0 traders since it opened at tick 262. Only 15 asks were ever listed on it (13 from a t15 burst
  at ticks 373–381, 2 from t04). Nobody ever bid there.
- The market is thin. In one snapshot there were 71 public asks and 15 public bids across all venues, 0 crossing pairs
  between two other teams, and exactly 1 broker `match` in the whole feed. Team-venue trades happen when a buyer
  accepts a posted ask, not when a broker crosses two offers.
- Where other teams list: venue hoppers (t15 rotates asks through every venue, t04 spreads single asks), activity
  followers (t13, t14 and t06 list on v02/v07), reciprocal routing (t05 on t10's v07 and t10 on t05's v10, both
  mostly addressed offers), and buyers who scan every venue. Fee and description do not attract traders (all team
  venues at 0 bps; v03/v05/v06 had strong copy and 0 trades). What correlates with trades is asks being present when a buyer looks, plus targeted notices naming bids and offer ids
  (t10's v07).
- The v19 keeper re-announced 14 times in 270 ticks, against a design of once per game hour. Cause: an in-memory timer
  reset by every maker restart. Seven more notices were refused with `wait`.
- Ranked plan (main items): a bench freeze around every session; book-driven announcements naming live offers; a
  notice when a hopper lands on v19; reciprocal routing (fair-play risk, needed Marius); bench edge #84, judged high
  risk for its gain; a second auto venue as a hedge. Don'ts: lower the fee (already 0), change the mechanism (fixed at
  opening), seed v19 ourselves (`self_venue`), pay rebates or gifts.
- It already noted that `BAZAAR_BENCH_CROSS=limit` was dead, because openapi `BrokerMatch` says
  "ask ≤ price and price + fee ≤ bid" (`x-verified`). Sunday's tick-1692 probe proved this right.
- A team pause (`trading_enabled=false` or the PAUSE file) also stops bench matching, since `broker_context` passes the
  kill switch. Whether bench matches should be exempt was left as an open policy question.
- §6 (tick ~709): there is no public `value_created` field. For starter-stall owners, `market − 7.5` is a clean organic
  proxy. The organic fit is `mm_share = own value / mean(top-3 value)`, capped at 1: capped teams stayed flat while the
  others shrank, and t07's +1.85 moved nobody else. Value is not pair-capped, and a price-0 swap scored +1.83 while 2
  cash trades on v10 scored 0. What separates venues with trades is **resident sellers** or hosted addressed offers, not fee, mechanism, notice
  count or notice detail. Cold venues got trades only from t15's hop asks (3 sold out of about 139, about 2 %) and from
  addressed offers. Listings in the 10 ticks after a notice vs before showed no lift.

## MARKET_MOVES.md (Sat 16:00, ticks 675–720)

Source: `_night/MARKET_MOVES.md`. A whole-score movement analysis. Most of its ranked actions are dealer-ladder
negotiating moves with private floors; those are dropped here and only the market-making parts are kept.

- The clock paused at tick 630 for 2 h 04 min (13:24:57 → 15:29:12 Madrid), so every wall time in the brief was ~2 h
  stale. New anchor: tick 715 = 16:12:10, 30 s ticks, `t_hours = 3 + (tick − 201)/120`.
- The h7 bench (ticks 681–697) ran after #209's redeploy: 5 `broker_match` rows, all done; efficiency 0.878,
  bench_points 0.5, market 7.50.
- The note judged the dealer ladder a bigger lever than the market for Saturday. It also judged team-trade gains capped
  (a later neg_points step moved negotiating by +0.00). (SCORE_CAPS later refit this: the trades cap moves with the field.)
- **What moves market points is a value-creating settlement between two other teams, and nothing else.** t07 got +1.85
  from one 0 P card swap (t08↔t05, tick 669) on an auto venue charging 300 bps. Fee, mechanism and price did not matter.
- First trade on an empty venue, observed: t17 +1.40, t09 +1.48, t07 +1.85, t14 +2.22 (mean +1.74). Later trades are
  worth less: t10 got +0.55 for its 4th, t06 +0.00 for a 20 P uncommon. (superseded: review-mm-probe finding 2 shows
  credit can be withdrawn, e.g. t07 back to 7.50 by tick 1210 and t12 12.43 → 7.50 at tick 910. The surviving-credit
  mean is about +1.09 displayed.)
- Messaging channels mapped: team threads (private, 1 msg per tick, 6 open, **shared with dealer threads**), venue
  announcements (public; the refusal text says one per 20 ticks, but notices 10 ticks apart were accepted on 5 venues;
  the feed shows at most 240 chars), directed offers (`to`), venue name/description (fixed at opening), fee notices.
  Offers carry no free text.
- Generic notices produced 0 trades anywhere (v03: 38 sent). Only targeted ones lined up with trades: v07 naming bids
  with ids, and v02's tick-647 "RET-06 buyer at 26, seller at 30, meet at 28" (it traded at exactly 28, but on v07).
  (superseded in part: review-mm-probe finding 4 found v19 listings arriving 3–20 ticks after our notices; the missing
  piece was a standing bidder.)
- Live book at tick ~683: 89 public offers, all on rastro, v02 and v07; 0 crossing pairs. t15's rotation ended at tick
  460 and t04's tour at 619, so hoppers were not coming back to v19.
- Dealer prices on Saturday (public): dealers bought commons at 2/6/10 and sold at 8/9/10 (min/median/max). Of 63 team
  cash trades, 35 fell inside the dealer spread.
- Ways a trade can land on v19 without us as a party: our broker pairs a crossing bid and ask; another team accepts an
  offer posted there; or two teams settle an addressed offer or swap there. All need others to post on v19 first.
- Proposed: a hand-sent notice inviting swap hosts (t08, t05, t13, t12, t07↔t15) to settle on v19 at 0 fee, with a
  ~200-char draft. Fair play: invitations are fine; rebates, gifts or "I trade on yours if you trade on mine" are not
  (RULES.md:131-132). Prompt injection is allowed against dealers only, so never against other teams' agents.
- Do-not list: merge #84 edge (t06's +0.25 at h7 had no attributed trade, which is weak evidence that a board can beat
  the stall); touch the fee or mechanism; seed v19.

## MM_DEEP.md (Sat 16:00–18:15, ticks 686–950)

Source: `_night/MM_DEEP.md`. A deep bench analysis with an offline broker benchmark, live book captures of b52 (h7) and
b69 (h9), and an organic-flow and ops review.

- h9 result: efficiency 0.891, bench_points 0.5, so 4 of 4 sessions sat exactly at the stall (0.899 / 0.933 / 0.878 /
  0.891). Nobody beat the stall at h7 or h9: the only movers were below-stall venues recovering (t03, t13) and organic
  trades. (superseded: the note's "a Sunday session = 7.5 × bench points" assumed only h17 and h19 counted for round
  3; see the reading guide.)
- "No estimator of hidden limits beats the stall": the cheating `truth` broker, which knows every limit but matches at
  once, ties it (0.876 vs 0.878). The headroom is **departures and timing**, not price. Knowing departures was worth
  +0.02–0.035 efficiency in sim.
- "The server refuses a match with ask > bid" was stated here without a live source (BBS flagged that). Confirmed on
  Sunday by the tick-1692 `400 bad_match`.
- The live b52 capture: traders arrive all session, quotes drift toward limits each tick (bid 42→56, ask 62→55), and
  traders leave on patience, not expiry. Every offer carries `expires_tick` = session end, so the book hides departure
  times. b52-2 left at 56 against an ask of 57, one tick before crossing.
- The h9 gate (b69, 132 captures at 5 s, pre-match books rebuilt per tick): `exact`, `bonus5` and `count` picked
  **identical pairs on every tick**, so the pair-bonus switch is a no-op. Unmatched lifetimes were [2, 3, 3, 4, 5, 5, 7,
  7] ticks with a minimum of 2, so the patience broker failed its gate.
- Benchmark (300 seeds per world): stall/exact 0.878 in the normal world. `bonus5` gave 0.553 points (5 % / 11 % of
  sessions below / above the stall), `count` 0.579 (26 % / 17 %), `delay1` 0.43 (88 % below), oracle 0.956. Any
  hold/delay policy put 70–93 % of sessions below the stall. Calibration caveat: the real stall scored 0.878–0.933 on
  thin books, while the sim's thin worlds scored 0.76–0.81.
- Where the stall loses (sim, per session): it leaves 2.72 traders unmatched who belong in the best pairing (only 0.35
  of them ever cross anyone) and matches 1.30 traders outside the best pairing.
- (superseded: "#84 edge scores 0.000–0.003 live" is a **simulator** column with expiry set to the session end. Edge
  never ran in a bench; see BENCH_BEAT_STALL §4.)
- Session 2 (h5) had **3 maker restarts inside the bench** (ticks 445/451/459) and no pairs after tick 448. About 50
  maker restarts on Saturday, none of them crashes. Two LIVE maker containers ran the same tick at handovers 437, 439,
  445, 451 and 701, so **a redeploy within 10 ticks of a bench is the main risk** to the 0.5 floor. Separately, at
  13:27 a redeploy ran a LIVE maker against the **simulator** for about 2 minutes (no damage found).
- All 25 v19 notices came from fresh processes (`announced_at = None` at start, set before the send, so a `wait` is
  never retried). Built locally: `feat/maker-news-announce` (0de6801b) seeds the timer from the feed, adds a 21-tick
  cooldown, and puts news-citing notices behind `venue_announce_news` (default off). Its merge status is not in these
  notes. News cannot move bench limits anyway: `BenchOffer` is cash-only and card-less, and the matcher keys every
  bench offer as `bench:<run>`.
- Built locally: `feat/bench-pair-bonus` (`bench_pair_bonus` guardrail, default 0, plus the first per-tick bench-book
  log and the `bench_policies.py` / `bench_lifetimes.py` scripts).
- Organic: v02 (8 trades), v07 (5), v01 (3) all had resident sellers or hosted addressed swaps; v03/v05/v06 had 22–40
  notices and 0 trades. One value-creating trade ≈ +1.3–2.0 market. Best move: a hand-sent swap-host invitation
  (EV +0.3–0.6).

## MARKET_TEST_H11_READY.md (Sat 19:30–19:50)

Source: `_night/MARKET_TEST_H11_READY.md`. A GO/NO-GO check for the h11 Market Test (tick 1161, 16 ticks).

- At 19:32 the verdict was NO-GO as configured: bazaar-maker had `BAZAAR_BENCH_POLICY=edge` with
  `BAZAAR_BENCH_GUARD_MARGIN=5` (below the code default of 10). It had been set by a variable patch at 18:37 with the
  #218 merge.
- 19:37: Marius approved `BAZAAR_BENCH_POLICY=exact`; the keeper logged `bench exact` at tick 1129. 19:45: a teammate
  set it back to `edge` (not any Claude session on this machine; Railway metadata does not show who). Marius decided
  to leave edge on, stop redeploying and talk to the team.
- Why edge was on: the #218 proof (2,000 sim books) scored bench *points*, not efficiency. Margin 5 had the smallest
  worst regret (0.045) across 14 regimes × 4 score readings: mean 0.543 vs 0.500, 11.7 % of books below the stall in the
  normal regime.
- The guarded edge keeps exact unless the edge plan has at least as many pairs and beats exact by ≥ margin estimated P;
  any exception falls back to exact.
- Ops: 4–5 maker redeploys in the hour before the bench, merges landing with no review gate. A tick-rate error was
  caught (120 ticks per game hour, not 100).
- The broker needs no cash or cards: it pairs synthetic `bench_offers` from `GET /api/broker/book` and drops our own
  offers. Cash was not a constraint.
- Untested assumption: the broker's X-Broker-Key rate bucket is separate from the team's 5 req/s.
- (superseded: "#84 edge scored 0.000–0.003 live" is a sim figure. BENCH_BEAT_STALL §4 showed h11 actually ran
  `exact`, because a variable change and a #232 merge restarted the maker, one restart inside the bench.)
- A Jev fan-out call returned `undecided` (`typesafe_api_key_missing`), so all six sub-agents ran at "quick".

## BENCH_BEAT_STALL.md (Sat 20:11–21:20, ticks 1192–1208, game paused at 1201)

Source: `_night/BENCH_BEAT_STALL.md`. Saturday night's attempt to get above the stall. It built the `probe` policy on
local branch `feat/bench-beat-stall`, ran a 300-seed benchmark and replays, and analysed t10 and swaps.

- It framed one question: does `POST /api/broker/matches` accept a bench pair whose quotes do not cross when the price
  sits inside both hidden limits? All 21 matches sent so far had crossed by quote, so "0 refusals" proved nothing.
  (superseded: the tick-1692 probe on Sunday got `400 bad_match`; the server checks quotes.)
- It read the organisers' texts as pointing to limits: `starter_broker.py` ("a quote is not a limit") and the admin
  `Bench.js` ("a broker waiting for them to cross waits in vain"). (superseded: same probe. The texts describe the
  scoring metric, which counts gains between hidden limits, not what the API accepts.)
- Scoring fit (S1, on t03/t13 moves): bench_points come from the **round-average** efficiency E against the stall's
  round average Es. Below the stall, b = 0.5·E/Es (fits ±0.004). Above the stall it is unpublished; a lone team above by
  any margin would get 1.0, because the top-3 mean then includes it. A session 0.05 below costs b ≈ 0.0035.
- Per-session stall efficiency (reading B, from our round averages): h3 0.899, h5 0.967, h7 0.768, h9 0.930, h11 0.866.
  Nobody was above the stall in round 2; t08 ("matched on estimated limits") and t13 ("smart broker") advertised
  cleverness with no score to show.
- The real books: two pairs had provably crossing limits that never crossed by quote (b52-2 bid 56 × b52-10 ask 57;
  b69-8 bid 61 × b69-11 ask 63). b52 had 4 firm traders out of 8, b69 had 0 out of 10.
- **Shading regularity:** three of six relaxing buyers ended at exactly 4/3 of their opening bid, and three of five
  sellers at about 8/9 of their opening ask. That suggests buyers open near 0.75 × limit. Quotes move roughly linearly,
  1–6 P per tick, and short-lived traders take big steps.
- The `probe` policy: exact plan first, then up to 4 non-crossing pairs among leftover traders (gap ≤ 20 P) at the
  midpoint, at most 6 per session, plus a full bench-book log every tick. 16 tests. (superseded: dead under the quote
  rule. Its "≤ 8 refused POSTs per process" claim was also wrong; review-mm-probe finding 1 shows the give-up latch could
  never fire with the defaults.)
- Benchmark under the quote rule: probe = exact on all 65,100 sim sessions and on b52/b69/b87. Under the limit rule:
  P(round margin > 0) 0.50–0.85, expected round b 0.74–0.92; real books b52 +0.062, b69 +0.023 (56 % of draws).
  (superseded: limit rule disproved.)
- **Why edge "failed": it never ran in a bench.** Edge was live at ticks 1009–1128 and 1143–1156. h11 (1161–1177) ran
  `exact`: a variable change at 19:52 and a #232 merge restarted the maker inside the bench (tick 1166). The
  "0.000–0.003" is a simulator column where `hold_known` held every pair until the traders had left. The deployed
  `edge_plan` could not even hold, since it passes no `session_starts`. Three maker restarts landed in the 1151–1187
  guard window.
- Jev on the probe: `bench_policy_choice` undecided (leaning probe_g10, 0.40); `bench_probe_enabled` leaned **no**,
  P(yes) 0.38. Only firm on timing: merge overnight with the doors closed.
- **t10's 12.5 market is organic, not bench.** v07 has the same settings as v19 (board, 0 fee, quote-crossing broker).
  The difference: 538 public listings by 10 other teams, a resident anchor buyer (t06, in 7 of 11 trades), an early
  opening (tick 179, 4th team venue), and from tick ~575 **matchmaking notices** that name a live bid, the matching ask
  elsewhere and El Rastro's 5 % + 1 P fee saving. About 11 small trades took it to the apparent +5.0 cap.
- Swap and cycle scan of the whole game's feed: 7,650 listings, 472 card-for-card swaps, **0 complementary 2-way swaps
  open at the same time, 0 3-cycles**. 56 cash crosses (same card, ask ≤ bid, different teams), 40 on one venue (38 on
  El Rastro) and 16 across venues (about 1 per 70 ticks). The broker API pairs exactly two offers, so a 3-cycle cannot
  be expressed. The real limit on v19 is liquidity.

## SCORE_CAPS.md (Sat, tick ~1320–1330)

Source: `_night/SCORE_CAPS.md`. A fit of the hidden top-3 means (caps) for every scoring part. The file is marked
private; only the structure and public-board conversions are kept, and our raw parts are aggregated.

- **Market = 22.5·bench_points + 7.5·organic** per round, exact on 9 stall teams and on t10.
- Board conversion: `board = Σ w_r·p_r·R_r / Σ w_r·p_r`, Friday weight 0.5, other days 1. Friday is frozen with
  market R1 = 0 for everyone. So **R2 = 1.5 × displayed market** (and `1.5·display − 4.17` for negotiating). In the
  final, 1 round point = **0.40 final points**.
- The score recomputes only on ticks that are multiples of 10, using the raw parts as of that tick.
- The negotiating split in briefing.md (7.5 / 7.5 / 15) is wrong. Best fit: ladder ≈ 7.5, team trades ≈ 10.5, duels
  ≈ 12, with weights **renormalised over the parts whose top-3 mean is non-zero** (Friday's ladder weighed 12.5). Each
  part is linear up to its cap, and caps move with the field (they can fall).
- At tick ~1320 we were near the cap on ladder and duels, about half the cap on team trades, 0 % on organic, and at the
  stall on bench.
- Marginal values in final points: organic 0 → cap ≈ **+3.0 per round**; one session at the top-3 mean instead of the
  stall ≈ +0.56 (Saturday, 8 sessions) or +1.5–2.25 (Sunday); team trades to the cap ≈ +4.2 per round; Duels III ≈ +4.8.
- t10 led at 38.6 = negotiating 26.1 + market 12.5, i.e. stall bench (11.25 round) plus full organic (7.5 round).
  Our gap to t10 was 13.1 board points: 5.0 market, which is exactly the organic cap, plus 8.1 negotiating.
- Caveat: organic can be wiped; t12 lost all of it at tick 910 (possibly the fair-play rule, RULES.md:131-132).
- Unknown at the time: whether duels and neg_points reset in round 3 (the ladder did reset at tick 160).

## MM_STRATEGY.md (Sat 21:50–22:10, ticks ~1290–1330)

Source: `_night/MM_STRATEGY.md`. The plan from Saturday night to Sunday 15:00, building on BBS, MM_DEEP and
MARKET_MOVES.

- Strategy: settle the limit question with **one hand probe in the h13 Market Test** (ticks 1401–1417), then merge the
  default-off probe branch overnight and flip `BAZAAR_BENCH_POLICY=probe` on Sunday morning. Prior P(limit) = 0.6; EV
  +1.8 to +2.7 final over two rounds. (superseded: the h13 hand probe was never sent, the probe policy never fired in a
  bench, and the Sunday tick-1692 probe proved the quote rule.)
- No in-session readout exists: the broker book has `offers`, `bench_offers`, `recent` and no `settlements`, and
  `recent` stayed `[]` while five pairs matched. A queued match can only be read from the POST response and from
  `/api/me` after the session.
- Probe readout codes: 429 `wait_for_tick` is not a verdict; 422 is body shape (bench ids are strings like `"b93-4"`);
  a 400 naming the price or crossing is the quote verdict; `{"queued": true, "settles_at_tick": T+1}` means accepted.
- Sunday clock rule: ticks are 15 s, 240 per game hour; times ran ~43 min later than the earlier night docs.
  (superseded in detail: see the dossier for the actual Sunday session times.)
- Organic arithmetic: first trade on an empty venue ≈ +1.04 final; later trades decay; cap 7.5 round = +3.0 final per
  round. (superseded in size: review-mm-probe finding 2.)
- Tonight's public-book scan (tick ~1300): 114 open offers, v19 0; 0 crossing pairs; 8 near-misses with gap ≤ 5 P, all
  commons and split across venues (e.g. a RET-03 bid of 6 on v21 vs an ask of 10 on El Rastro). The feed's
  `offer.listed` carries the maker's team id, while venue books show pseudonyms.
- Competition in the matchmaking lane: v07 (t10, at the cap), v02 (t12), **v25 (t14, auto, opened tick 1251, "we post
  wanted bids and crossable pairs from every venue")**, and v24 (t13, an injection-style notice telling "portfolio
  agents" to migrate their book to v24). We were a late entrant with an empty book.
- Organic playbook (t10's, adapted): (1) a resident bidder first, via one thread inviting the most active public buyer
  (t06, t04 or t16) to mirror its standing bids on v19; (2) matchmaking notices naming one live near-miss with ids, the
  midpoint meet price and the fee saving, only while a fresh near-miss exists; (3) best window right after the round-3
  release and 150 P grant. EV +0.6 to +1.3 final. Never instruct other teams' agents (t13's v24 style invites flags and
  a suspension).
- LEAD 2 (tick 1326), every team off 7.50 explained. Positive Δ always came with ≥ 1 trade between other teams (t10
  +5.00, t06 +4.32, t09 +3.39, t16 +2.66, t14 +1.80, t17 +1.14, t08 +1.00), three of them on auto venues where a bench
  gain is impossible. Negative Δ belonged to board venues with 0 trades whose brokers lost sessions (t13 −0.85, t03
  −1.64). So **nobody was above the stall**. Δ grows concavely with trades.
- Don'ts: redeploy within 20 ticks of a bench; run edge or any hold policy; close v19 (a session with no venue open
  scores 0); build a new bench policy overnight; seed v19; print keys.
- Top risks named: settlement-time validation removing probed traders; a redeploy or container overlap inside a bench
  (the only way to lose a whole session); organisers reading non-crossing matches as rule-breaking; and imitation by
  t08/t13 raising the top-3 mean.

## HANDOFF_MM_PROBE.md (Sat 22:12, tick ~1350)

Source: `_night/HANDOFF_MM_PROBE.md`. A handoff from the strategy agent to the next agent, written when Marius
interrupted with a new request.

- State: clock running again, h13 Market Test at ticks 1401–1417 (≈ 22:38–22:46), Saturday close ≈ tick 1445. t01 rank
  16/18, market 7.50 (stall, 0 organic), v19 0 trades.
- Done: MM_STRATEGY written; a one-shot hand probe script `scripts/bench_probe_once.py` (untracked, lint clean, fake
  broker tests pass, live dry run read the book and sent nothing); the s7 near-miss scan.
- Marius said "go for it, get ready the code" for the h13 probe; the POST itself was his to run, with at most 2 verdict
  POSTs. (superseded: it was never sent; the dossier records h13 as plain exact, 4 pairs, efficiency 0.854.)
- The interrupted request was an unrelated buy order to be handled by the Railway agent, not market making; its
  details are private and dropped here.
- Open items as of the handoff: guide the h13 probe; one hand notice on v19 naming a live near-miss before 23:00 (if
  approved); overnight push and merge of `feat/bench-beat-stall` and `feat/maker-news-announce`; the Sunday probe flip;
  an organic window at 12:17–13:30.
- Hard rules carried: analysis and local code only, no game POSTs from the agent, no Railway writes, no commits, never
  bare `git stash`, never print keys.

## review-bench-books.md (Sun 07:27–07:35 UTC)

Source: `_sat-review/review-bench-books.md`. Review of `fix/bench-books-recorder`, which claimed `bench_books` was empty
only because the recorder shipped after Saturday's last Market Test. Verdict: **ACCEPT**.

- Root cause held: the writer (`39bb2932`, PR #261) reached main at 22:17 UTC Saturday, after the doors closed. The two
  maker deploys that ran Saturday's last bench did not contain it. The running maker on Sunday did.
- `bench_books` had 0 rows and `n_tup_ins = 0`: never an insert, not written-then-deleted. The prod path writes only when
  not in simulator mode, and `BAZAAR_BENCH_POLICY=exact` was live.
- The real payload: buyers carry `want.cash: 0`, so `side_and_quote`'s truthiness test is the right one. A fixture
  (`broker_book_bench_b52.json`) is byte-identical to 3 of 44 real captures.
- MED: the deployed wiring was untested. A mutation that stops the real maker writing to Postgres passed 594 tests. Fix
  suggested: a keeper test asserting `_bench_books("v19")` has a real connect and `world == "real"`.
- LOW, useful ops fact: **merges touching only `tests/**` or `docs/**` do not redeploy the maker** (Railway
  `watchPatterns`; PR #271 and #273 merges were `SKIPPED`).
- Sunday clock at 07:30 UTC: tick 1507, 15 s ticks, first bench at t 14.65 ≈ 10:16 local. `/me bench_venue` was null
  until the first session ran, as on Saturday.
- Afterwards the table filled: b120 (session 7) 61 rows / 24 offers and b137 (session 8) 59 rows / 20 offers, which fed
  the calibrated simulator (dossier §3).

## review-bench-lookahead.md (Sun 11:04)

Source: `_sat-review/review-bench-lookahead.md`. Adversarial review of `BAZAAR_BENCH_POLICY=lookahead` (`d4c5b328`,
PR #292). Verdict: **SHIP**.

- Live safety OK: lookahead only picks quote-crossing pairs (`feasible` on current quotes, same bench run, different
  makers), prices in `[ask, bid − fee]` so a `400 bad_match` cannot happen, never uses a trader twice in a tick, and
  falls back to the exact plan if `plan()` raises.
- Env rollback verified: main's `BENCH_POLICIES` (exact/edge/probe) ignores `lookahead` and stays exact, so the variable
  could be set with `--skip-deploys` before the merge.
- MED: no compute cap. Measured on a laptop: about 15 ms a tick on realistic books, 0.41 s worst case, 0.62 s
  adversarial. A slow tick does not fall back to exact; late sends just expire, and it delays our organic quotes.
- MED: the expected-points case rests on an unpublished below-stall rule. Under **zero below the stall**: cal_normal20
  0.577 (linear 0.642), cal_hard24 0.538. Real replays: **b120 ≈ 0.42** (P above 0.017, P below 0.173) and **b137 ≈
  0.83** (P above 0.823). The priors were fitted on b120 and b137, so both replays are in-sample.
- MED: the merge redeploys maker, taker and duels (`src/**`), so check that all three already run main HEAD first, or
  the merge also ships other PRs for the first time.
- LOW: no per-run match cap exists in code or rules; only the per-tick cap (15) applies. Two overlapping bench runs
  would reset the planner every tick (no evidence they ever overlap).
- Deploy checklist: merge after Duels III and before ~12:25, never 12:37–12:42; watch for `bench lookahead failed` and
  `expired` broker_match rows; rollback is `BAZAAR_BENCH_POLICY=exact` plus a maker restart.
- (Later, per the bench-deploy prompt, the shipped variant was `lookahead_safe`, from bench-search's posterior commits.)

## review-mm-probe.md (Sun)

Source: `_sat-review/review-mm-probe.md`. Review of [`reports/mm-probe.md`](reports/mm-probe.md) (then on branch
`research/sat-mm-probe`). First verdict REVISE; after the re-check, still REVISE on one MED.

- Reproduced: 25 `broker_match` rows on Saturday (ticks 442–1412), all done and all `queued`; efficiencies 0.899 /
  0.933 / 0.878 / 0.891 / 0.886 / 0.854, bench_points 0.5 throughout (h13 = 0.694 under reading B).
- Listings per venue over the game: v07 552 (390 addressed, 10 makers), v02 420, v13 396, v21 236, v01 199, v15 160
  (all addressed), **v19 15 (none after tick 612)**.
- HIGH 1: the probe's "stops after 8 refusals" latch could never fire, because refused ≤ sent ≤ 6 < 8. Under the quote
  rule it would have sent 6 refused matches every session. Fixed in the report with a separate POST-refusal counter
  latching at ≥ 4. (Moot after the tick-1692 verdict.)
- HIGH 2: **organic credit is neither per-trade nor permanent.** t05's v10 had 2 paid trades and stayed at 7.50; t07
  rose to 9.35 after a 0 P swap and was back at 7.50 by tick 1210; t12 (v02, 11 settlements) fell 12.43 → 7.50 at tick
  910, after a t14 burst that looks like the RULES.md:132 case. v15 (160 addressed listings) and v13 (396) had 0
  trades. Surviving-credit mean ≈ +1.09 displayed per first trade.
- MED: "fee 0 everywhere" was false. v02 went to 1000 bps + 5 P at tick 941, then 0 bps + 5 P/card at 1051, and had 0
  trades after tick 903.
- MED: notices may draw sellers. t15's 13 v19 listings came 12–20 ticks after our notice at 361; t04's came 3 and 10
  ticks after notices. All 15 were short-lived asks with no bidder present. Three v19 notices went out inside benches
  (ticks 445, 923, 1166). At 15 s ticks a 10-tick listing lives 2.5 min, too short for a hand-approved echo.
- MED: an auto "hedge" venue was reversed to "do not open". `/me` has a single `bench_venue`; if it moved to the hedge,
  it would cap us at the stall. It would also lock 250 P of bond, and a hand `venue announce` from the same machine
  would post on the hedge venue because the CLI saves the new broker key locally.
- MED: a third server world (checks neither quotes nor limits) was missing; there, midpoint probes outside a limit
  settle at a loss. Added at P ≈ 0.1.
- MED: the thread/swap channel was unaccounted for. RULES.md:60/64: every team-to-team trade happens on a venue, and
  threads open on a venue, so a deal between two *other* teams hosted on v19 counts for us. Our desk opened 83 threads
  on Saturday, **all on `rastro`**. The invite text asked teams to "post asks and bids" on v19 but never to **host their
  threads and swaps there**. Opening our own invite thread on v19 is barred (`self_venue`), so open it on rastro.
- LOW: notice cadence in code is `ANNOUNCE_EVERY_TICKS = 10`, `ANNOUNCE_MAX_PER_GAME_HOUR = 24`, with no hold during
  benches. The probe and notices use the broker key, while the desk uses the team key shared with taker and duels.
- Open after re-check (R1): the manual switch-off rule (≥ 1 refusal) was stricter than the code latch (≥ 4) and could
  forfeit round 3 in the limit world. (Moot after tick 1692.)

## Session prompts

Source: `_sat-review/prompts/`. What each Sunday session was asked to do, one line each.

- `bench-baseline.md`: find out whether session 7 (b120, tick 1690) beat the baseline, what "the baseline" is, why 97 %
  efficiency still scored 0.5, explain the dashboard's "7 of 6 matched / 178 of 174 P", why market-making was 0, and
  what to change before session 8.
- `bench-books.md`: find out why `bench_books` was empty in prod (writer, deployed commit, failures, reader side) and
  fix it on the agents' side with a real-shaped fixture, or write deploy steps if it was config only.
- `bench-deploy.md`: own the deploy of `lookahead_safe` (PR #292) for session 9: merge only via `merge_safe.sh` by tick
  ~2214, verify the maker log, roll back to exact if needed, never touch Railway 12:30–12:45, and record session 9's
  result.
- `bench-search.md`: search for a broker strategy that beats the stall (assignment on posterior limits, lookahead/DP,
  pairing, parameter search, kit quirks) with a floor guard, and ship the best behind a new `BAZAAR_BENCH_POLICY` value.
- `bench-sim.md`: calibrate the simulator to session 7's real book, run a ≥ 200-seed tournament of every policy plus
  new ones, replay b120, post an early best existing setting, and implement the best new policy on
  `feat/bench-beat-stall`.
- `mm-dossier.md`: write one dossier of everything known about market making and the Market Test, with a session-8
  (b137) deep-dive, what ships for session 9, and a no-overclaim pitch section; PR it, do not merge.
- `mm-probe.md`: cover "becoming a big market maker with our probe strategy and how to market it", in both senses
  (advertising v19 to other teams' agents, and presenting market making to the judges), with a Sunday design and
  guardrail changes.

## Cross-cutting ideas never built or tried

Collected from all the notes above. "Never built" means no code reached main per these notes; "never tried" means no
live use is recorded.

- **Matchmaking announcer** behind a flag (`venue_announce_matchmaking`): notices naming a live bid on v19 or a live
  near-miss across venues, with ids, the meet price and the fee saving. Proposed in MM_PLAN, MARKET_MOVES, MM_DEEP, BBS
  §9 and MM_STRATEGY; never built.
- **Hand-sent targeted notice on v19** (swap-host invitation or near-miss notice). Drafted twice; these notes hold no
  evidence it was ever sent.
- **Resident-bidder invitation**: one thread asking an active public buyer to mirror its standing bids on v19. Never
  tried.
- **"Host your threads and swaps on v19"** wording in the venue invite and team-desk messages. Never added.
- **Notice when a hopper lands on v19** (react to another team's listing on our venue the same tick). Never built.
- **Reciprocal routing** (list our spares on a partner's 0-fee venue and ask them to list on v19). Needed Marius's
  fair-play approval; never built.
- **Auto hedge venue** as a stall floor. Never opened; the Sunday review reversed it to "do not open".
- **Swap-pair support in the matcher.** Judged worth ~0 (0 complementary swaps all game); never built.
- **D max-weight on estimated limits, refusal-based limit learning, re-pricing ladders.** Listed as "not built" in BBS;
  dead under the quote rule anyway.
- **Patience broker** (`patience<P>`). Sim only; failed the h9 lifetime gate.
- **Compute deadline in the lookahead planner** (fall back to exact on overrun). Suggested in review; built as
  `budget_s` on branch `feat/bench-lookahead-v2` (bench-sim §7), not deployed.
- **Keeper wiring test for `_bench_books`** in non-simulator mode. Suggested in review.
- **Bench hold for venue notices** (no notices during a bench). Not in code.
- **Asking the organisers in person** whether a match inside the hidden limits is accepted. Recommended in
  MM_STRATEGY; no record it was asked (made moot by tick 1692).
- **Exempting bench matching from the team kill switch / PAUSE file.** Raised in MM_PLAN as a policy question; never
  decided.
- **Persisting the announcement timer** in Postgres (MM_PLAN) was superseded by `feat/maker-news-announce`'s
  feed-seeded timer; its merge status is not in these notes.
