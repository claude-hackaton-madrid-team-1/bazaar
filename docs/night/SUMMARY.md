# Night shift summary (Fri 2 → Sat 3 Oct 2026)

Times are Madrid (GitHub shows UTC, Madrid = UTC+2). Numbers come from the night's own files (sanitised copies in
[log/](log/): MORNING, BITES, REVIEWS, STATUS, PLAN, BACKLOG). Every workstream, PR and branch is listed in
[INDEX.md](INDEX.md). Most simulation results are in-sample (the models were fitted to Friday's data). The real test
is Saturday and Sunday.

## 1. The audit: weak points in our agents and the PRs that fixed them

Before the night shift, Marius audited `main`, the shared database and the monitor captures, then opened one fix per
weak point:

| Weak point | Fix | Now |
|---|---|---|
| Two-issue duel offers could close on or outside our limit (`days = 5` sent unvalued; Jev could counter on price alone) | #60 | closed, taken over in **#150** (merged 06:50) |
| The shared ledger had **0 rows**: a dropped Postgres connection was never reopened, and the duel player stopped moving silently | #62 | closed, taken over in **#162** (merged 06:57) |
| The kill switch made agents write: a pause closed dealer threads (walks) and the maker kept cancelling | #68 | closed; its content merged in **#72** (06:15) |
| Cash and spend accounting: one tick could breach `cash_floor` and the hourly spend cap | #72 | merged 06:15 (carries #61 and #68) |
| The public `/state` leaked our card values, price limits and affinity multiplier | #69 | merged 02:08 (follow-up #121) |
| The taker and `dealer buy` closed at a dealer's opening ask (no ladder credit) | #61 | merged via #72 |

Issue triage: audit comments on 19 issues (#2–#24). #2 was closed. #1 was closed, then reopened because only its
title was done. All issues except #156 were later archived and closed by ogarciarevett's coordinator (04:53).

## 2. What the real data showed

- **The ladder was our only scoring component** at Friday's close (tick 149: `ladder_points` 0.058; duels, market
  and bench all 0).
- **Duel decay is charged per exchange, not per tick.** `result = |price − limit| × 0.94^rounds`, with
  `rounds = min(our priced offers, their priced offers)`. This matches all 8 real practice deals (r1 re-derived it to
  ±0.05 rounding). Our v1 countered every tick: 6.0 rounds per deal on average (1–9), keeping 57–94 % of the surplus.
- **Rivals conceded into our zone when we stayed silent.** In the 12 practice duels we never answered, accepting
  their best offer would have made ~195 P (16.3 P/duel), against 176.9 P on the 14 we played (12.6 P/duel).

## 3. What the night shift produced

- **Duels.** Policy v2 anchors once, then stays silent while the rival concedes. It scores **1.50×** v1 end to end
  (15 s ticks, 36 duels, taker running alongside: 118.8 vs 79.1 sim points), with **0 closes outside the limit**. v1
  lost 11 accepts to the one-accept-per-tick cap; v2 lost none. On the zoo gate it scored 1.42× and 1.55×, and on the
  real replay 178.4 P vs 121.7 P. The endgame exploit study showed that **v1 leaks its limit (±6 %) by tick 3 in 96 %
  of duels**. The B11 settings (`duel_endgame_min_share 0.3`, `duel_endgame_ticks 1`) raise our share against
  exploiters from 0.10 to 0.23, and keep ≥ 0.996× against honest rivals. All of this is on `main` via #150, but the
  defaults are still v1 (switching is one GUARDRAILS.md commit).
- **Ladder.** W3's Abuela plan raises our share of her price range from **0.80 to 0.97** on a real replay (0.84 → 0.945
  in the model; r1: 0.97 is the top of the bracket). B21 found the unlock rule: a dealer unlocks after **3 deals with
  the dealer before it** (plan for 4 buys). r1 found it unverified whether sales and opening-price deals count.
- **Taker.** On Friday there were **156 listings below our value** (median life 10 ticks) while our taker bought
  nothing. Replaying Friday through the current taker gives +28 P at copy value, or **+54 P with `min_buy_surplus` 4–6**.
  `cash_floor` is the binding cap. (The coordinator kept today's value: Jev leaned to 4 at 0.60, under its 0.75 bar.)
- **Market.** Each Market Test scores our best open venue (0 without one). A stall-level venue earns about half the
  bench points, and the free starter stall may already earn that (unverified until the first bench). Our broker's
  edge over the stall is small: the oracle gains +0.03–0.06 p50, and the shipped exact matcher equals the stall. A
  09:00 venue with the edge broker adds about +0.19 final points in the default bench world. **Win rate over the stall
  matters more than margin.** Organic market making is no source today: 0 of Friday's 739 public offers went to a team
  venue.
- **The honest no-gos.** Arbitrage: 0 crossings survived fees on Friday. Packs: one earns 12–37× fewer ladder points
  per P than Abuela's best three. Completing pages: no page can be finished under our price caps (Chato fills rares
  at 82–93), and a complete page does not score by itself.
- **Safety.** The red team ran 168 hostile-text cases with **0 binding-field changes**. r2 tested 29 bite cases
  (X1–X29): 6 disproved, 3 analysed, 1 known, 19 confirmed. Fixes for X4, X7, X11 and X17 are on `main`. Fixes for X3,
  X8, X15, X18 and X20 are in open takeovers #140–#144. X21 (sealed packs) and X24 (an agent can fail every tick while
  `/health` says ok) are still open.
- **Ops.** The night produced an operator cockpit, a morning assumption checker (`bazaar verify`), a Saturday
  playbook with two clock columns (jump to h4, or resume at h2.65) and a fact-checked pitch kit. A full rehearsal
  integrated 12 PRs, needed 10 cross-PR fix-ups, and ran 580 live sim ticks with 0 crashes.

## 4. What we learned about the process

- **We need one merge authority.** Two orchestrators ran overnight. From 05:40, ogarciarevett's coordinator closed
  most night PRs and reopened them as takeovers (#137–#163). It merged #105 over r1's blocker, and merged the learner
  stack #89/#96/#112, #145 and #91 with defaults ON that r1 had asked to ship off. It also merged #150 with an
  unreviewed v1 change (6b56719). Some review fixes ended up only on closed branches (#114/#116 → #140/#141).
- **The usage limit hit at 04:35.** Most sessions, r1 included, stopped mid-task and resumed around 05:50. A plan
  needs checkpoints that survive a stop.
- **Independent reviewers paid off.** r1 re-ran each PR's numbers. It caught overclaims: B21's "sales don't count", the
  pitch kit's 7 high claims, and an in-sample score model presented as the headline. It also caught cross-PR bugs:
  #62 × #79 `hands_off_ids`, #105 × #71 starter stall, #162 vs #141/#137, and #158's slot count. b5's rehearsal and
  r2's bite tests found what single-PR review misses.

Corrections to the night files: see [INDEX.md § Corrections](INDEX.md#corrections).
