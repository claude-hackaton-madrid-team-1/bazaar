# Did session 7 beat the Market Test baseline?

*Sun 4 Oct 2026, 10:25–10:45 Madrid. Read-only: DB SELECTs, keyless `/api/schedule`, `/api/clock`, `/api/venues` and
`/api/leaderboard`, and `railway logs` for `bazaar-maker`. No game writes and no Railway changes.*

## Answer

**No. Session 7 tied the baseline; it did not beat it.**

- The baseline is the free auto stall's efficiency on the same synthetic book. Matching as well as the stall is worth
  `bench_points` 0.5.
- Session 7 (run b120, "The hard Market Test", tick 1690) scored efficiency 0.967 and **0.5**.
- Every one of the 18 teams scored 0.5 in session 7, and nobody has beaten the stall in any of the 7 sessions.
- Our `exact` matcher pairs exactly what the stall pairs, so it can only ever tie it.
- The match probe showed that the game refuses any pair whose posted quotes do not cross. Any edge has to come from
  which crossing pairs we match, and when.

**Session 8 was not at 11:16.** `/api/schedule` put it at game hour 15.0, and it started at tick 1774 (10:37:24
local), as predicted. Session 7 was an extra, unscheduled test. Session 8 tied again (efficiency 0.895, 0.5; see the
last section). The next one is game hour 17.0, tick ~2254, **~12:37 local**. The dashboard's countdown is wrong (see
Q3).

## Q1. What "the baseline" is and how bench points are computed

**The source.** `vendor/bazaar-kit/RULES.md` ("Your own market", the organisers' kit, the same on origin/main):

> **The Market Test**: every two hours every venue receives the same synthetic book of buyers and sellers. Your broker
> (or your auto mechanism) matches them; your score is the share of the possible gains you realise. A broker can only
> act on a `board` venue: on an `auto` venue, the free stall included, the engine crosses every pair first. Each
> session counts your best venue open during it (none open counts 0) and the round averages its sessions […]
> **Matching as well as the free auto stall earns half the bench points; the full points go to the mean of the top
> three.**

The Scoring section of the same file:

> | Market-making 30 | The Market Test efficiency · value created between other teams on your venue |

**What each term means:**

- **Efficiency** (`/me` → `score.bench_efficiency`) is the share of the possible gains between the traders' *hidden
  limits* that the venue realises. It is not measured against the quotes.
- **The baseline** is the free auto stall: an `auto` venue whose engine crosses every crossing pair the moment it
  crosses. Reaching its efficiency gives `bench_points` = 0.5.
- **Full points**, 1.0, go to the mean efficiency of the top three venues in the session.
- **Between those anchors** the rules give no formula. The dashboard's labels assume linear interpolation and
  `below_stall` < 0.5; that is our reading, not the organisers' text. No cap is stated beyond the full points.

**How the 30 market-making points split.** Fitted on `/me` and the leaderboard (this confirms `docs/briefing.md`'s
Saturday fit, and it still holds on Sunday):

- Per round: **market = 22.5 × `bench_points` + 7.5 × organic**, where organic is value created between other teams
  on our venue, capped at the top-3 mean.
- The round's `bench_points` is the mean over its sessions.
- Saturday: `/me` market was 7.5 = (Friday 0 × 0.5 + Saturday 11.25 × 1) / 1.5.
- Now (tick 1722): 8.33 = (0 + 11.25 + 0.429 × 11.25) / 1.929, where 0.429 is the Sunday phase from
  `/api/leaderboard`.

**What a session is worth.** Sunday's round has three sessions (7, 8, 9). Raising one of them from 0.5 to 1.0 adds
22.5 × 0.5 / 3 = 3.75 to Sunday's market round. Sunday is 40 % of the game score, so that is **~+1.5 final points per
session**. For comparison, #8 on the board is 1.2 points above us (25.78).

## Q2. Did session 7 beat it? Why 0.5 at 97 %?

**Our `/me` history.** `bench_points` has been exactly 0.5 after every session, whatever the efficiency:

| Session (tick) | Venue | `bench_efficiency` | `bench_points` |
|---|---|---|---|
| 1 (201) | v08 | 0.899 | 0.5 |
| 2 (441) | v19 | 0.933 | 0.5 |
| 3 (681) | v19 | 0.878 | 0.5 |
| 4 (921) | v19 | 0.891 | 0.5 |
| 5 (1161) | v19 | 0.886 | 0.5 |
| 6 (1401) | v19 | 0.854 | 0.5 |
| 7 (1690, "hard") | v19 | 0.967 | 0.5 |

**Why it is always 0.5.** The score is relative to the stall's efficiency on the *same* book. It is not relative to
100 % or to the quoted optimum. Our efficiency moves because the books differ; the stall's moves with it, and we land
on it every time.

That is by construction. In `src/bazaar_agent/agents/broker.py`, `bench_policy = "exact"` is the maximum
quoted-surplus matching of the pairs whose quotes cross, sent the tick they cross. On a single-unit book that is the
same set of pairs the stall's engine crosses.

**Every team scored 0.5 in session 7.** The `leaderboard_snapshots` market column was read at tick 1702 (before the
session) and at tick 1722 (after). The Sunday round's market was backed out for each team:
`Sun = ((1.5 + p) × overall − 1.5 × Sat) / p`, with p = 0.404 before and 0.429 after.

| Team | Sunday round before | Sunday round after | Δ |
|---|---|---|---|
| t01, t02, t04, t11, t15, t18 | 0.00 | 11.23 | +11.23 |
| t03, t08, t10, t13 | ~0 | 11.22–11.25 | +11.21 … +11.25 |
| t06, t09, t14, t16, t17 | ~−0.3 … −2.2 (artefact: the Saturday round value moved after Saturday's close, likely the organic fix applied retroactively; only Δ is robust) | 9.1–10.9 | +11.27 … +11.36 |
| t05 | 6.70 | 17.53 | +10.83 |
| t07 | 11.27 | 21.84 | +10.57 |
| t12 | 17.44 | 27.62 | +10.18 |

Δ ≈ 22.5 × 0.5 = 11.25 for everyone. The three smaller deltas (t05, t07, t12) belong to the only teams with nonzero
organic before the session, so their organic was being recomputed in the same window. That fits Team 12's bad-trade
fix, which touched organic, not bench. Every venue therefore scored the stall's level, and the top-3 mean equalled the
stall's level.

**The open venues in session 7** (keyless `/api/venues`; `bench.started` lists 18 venues):

| Type | Venues |
|---|---|
| Free stalls (`auto`, `starter`) | v10 t05, v11 t07, v13 t11 (300 bps), v15 t15, v16 t16, v17 t17 |
| Boards | v01 t06, v02 t12, v05 t04, v06 t08 (100 bps), v07 t10, v19 t01, v20 t03, v21 t09, v24 t13, v26 t02, v27 t14 (200 bps), v28 t18 |

No per-venue efficiency is public. Only our own `/me` has `bench_efficiency`.

**What follows, with a caveat.** Nobody has ever exceeded the stall, so the shape above 0.5 has never been observed.
The robust claim is this: an efficiency above the stall's is the only way to score more than 0.5, and the magnitude is
unknown.

If the scale is linear and the top-3 mean includes our own venue, the first team to beat the stall is itself in the top
three. The top-3 mean is then (ours + 2 × stall) / 3, and ours sits at 3× the stall-to-mean distance, so it scores the
cap, 1.0. A step function would fit the data equally well.

## Q3. The dashboard's "7 of 6 matched" and "178 of 174 P · 100 %"

**This is a dashboard bug.** It is not a probe being counted and not double counting.

**How the dashboard computes it** (bazaar-live origin/main):

- `db/venue.sql:108-117` (`show.venue_books`) collapses the 61 `bench_books` rows to 24 offers. It keeps each offer's
  **first** quote, `(array_agg(b.quote order by b.tick))[1]`. "12 buy · 12 sell" is correct.
- `src/game/views/venue.ts:49-62` `possibleOf` pairs the best bids with the best asks on those first quotes. That
  gives **6 pairs / 174 P**.
- `matched` counts decisions in status `done` (7). `captured` sums bid − ask at match time (178).
- `share = Math.min(1, captured / possible)` (`venue.ts:166`) clamps the real 102.3 % to "100 %".

**The synthetic traders step their quotes.** Buyers raise their bids and sellers lower their asks. For example,
b120-12 asked 41 → 38, b120-5 bid 63 → 83 and b120-22 asked 77 → 69. Many first-quote pairs also never coexisted on the
book.

The real ceiling is a maximum-weight matching over the pairs on the book at the same tick, at that tick's quotes. That
gives **7 pairs / 178 P, which is exactly what our broker matched.** Over first quotes only it is 174 / 6. Over each
offer's best quote, ignoring timing, it is 209 / 6, which no venue could reach.

**What we matched in session 7** (from decisions and the maker logs; all queued, settles_at t+1, 0 refused):

| Tick | Sell (ask) | Buy (bid) | Price | Quoted surplus |
|---|---|---|---|---|
| 1693 | b120-12 (38) | b120-2 (39) | 38 | 1 |
| 1694 | b120-16 (40) | b120-7 (82) | 61 | 42 |
| 1694 | b120-13 (45) | b120-8 (63) | 54 | 18 |
| 1696 | b120-18 (38) | b120-6 (74) | 56 | 36 |
| 1697 | b120-23 (44) | b120-5 (83) | 63 | 39 |
| 1701 | b120-15 (35) | b120-9 (76) | 55 | 41 |
| 1702 | b120-22 (69) | b120-10 (70) | 69 | 1 |

Unmatched traders:

- Buyers: b120-3 (42 → 64, seen 1701–1705), b120-4 (56), b120-0 (25 → 30), b120-1 (37), b120-11 (37).
- Sellers: b120-21 (→ 80), b120-19 (→ 80), b120-17 (→ 88), b120-20 (→ 98), b120-14 (134).

**The probe is hidden too.** Decision 4263, at tick 1692 and refused with `bad_match`, does not show. `venue_matches`
takes `run` from `candidates.item`, and probes have none, so the session shows "0 failed".

**The countdown is wrong.** The dashboard said "session 8 in 211 ticks · 11:16". `clockOf` (`venue.ts:176-185`)
extrapolates the next session from the common gap between starts (240 ticks after 1690). Session 7 was off cadence,
so the estimate is wrong. `/api/schedule` is keyless and has the real `at_hours` of every upcoming bench.

**The fix for bazaar-live** (described only, not edited):

1. `show.venue_books` emits each offer's quote path (tick, quote).
2. `possibleOf` becomes a max-weight matching over pairs present at the same tick, each worth the best bid − ask − fee
   across their shared ticks.
3. Drop the clamp, label the figure "of the quotes we saw", and keep the official efficiency as the real measure.
4. `venue_matches` derives `run` from the `b120-*` offer-id prefix when `item` is missing.
5. The countdown reads `/api/schedule` (`action == "bench"`, `at_hours` minus `/api/clock.t_hours`, at 240 ticks per
   game hour on Sunday's 15 s ticks) instead of extrapolating.

## Q4. Why market-making shows 0

The "market-making 0" on the dashboard is `/me` → `score.mm_points`, the **organic** half: value created between other
teams on our venue. It is 0 because nobody trades on v19. The bench half is fine (it is the 0.5).

- [`mm-probe.md`](mm-probe.md) (from branch `research/sat-review-crinoid`): all of Saturday, v19 had 0 trades,
  0 traders and 0 pairs.
  - 15 outside listings arrived: 13 asks from t15 at ticks 373–381 and 2 from t04. All were single-card asks expiring
    in 6–10 ticks, and no bidder ever came.
  - Other teams' routers post on venues that opened earlier (v07, v21, v01, v02).
  - `/api/venues` now still shows v19 with trades 0, traders 0 and pairs 0.
- Team 12's bug fix (`docs/transcripts/2026-10-04-invofox-3.md` [04:05–05:00]): "a bad trade destroys the value of the
  marketplace". A seller underpricing a card on someone's venue no longer counts against the venue.
  - It changed how organic is computed. It explains t05, t07 and t12's smaller deltas above.
  - It did not change the 22.5 / 7.5 split, and it does not affect us: we have no organic to lose.

## Q5. What `exact` and the match probe did in session 7, and what to run in session 9

**From the `bazaar-maker` logs (UTC).** Every start today logs:

```
venue: bench match probe ARMED (one request ever, durable claim)
venue: broker on for v19 (LIVE), bench exact
```

Session 7 ran on deployment d4b7a9dd (PR #281, up 08:11:11 → 08:24:32).

**`exact`** made the 7 pairs above, with 0 refused. The log's summary line:

```
08:20:29 Market Test b120 over (ticks 1690–1706): 7 pair(s), quoted surplus 178, 0 refused
```

**The match probe** (`BAZAAR_BENCH_MATCH_PROBE=once`, PR #263) fired once:

```
08:16:48 tick 1692 broker: PROBE sell b120-12 (ask 40) x buy b120-2 (bid 39) at 39, quotes 1 short of crossing
… probe answer: turned down {"accepted": false, "status": 400, "code": "bad_match",
  "message": "price must sit between the ask 40 and the bid 39"}
```

- **The game checks the posted quotes, not the hidden limits.** A non-crossing pair can never be matched.
- One tick later b120-12 stepped to 38 and `exact` matched the same two traders.
- The probe's claim is stored in Postgres, so it will not fire again. "ARMED" in later logs is harmless.

**What the three bench policies mean now:**

| Policy | What it does | Verdict |
|---|---|---|
| `probe` (`BAZAAR_BENCH_POLICY=probe`) | Sends up to 4 non-crossing pairs a tick after the exact plan | Every one of them will be refused. Do not use. |
| `edge` | Holds or re-pairs crossing pairs on estimated true surplus, behind `BAZAAR_BENCH_GUARD_MARGIN` (default 10) | The only mode that can differ from the stall, but it can also score below the stall (below 0.5) |
| `exact` | Equals the stall | 0.5 guaranteed while the maker is up |

**What could ever have scored 1.0.** Only a different choice among *crossing* pairs:

- Holding a buyer for a seller who arrives later, or
- Not spending a trader on a +1 pair (ticks 1693 and 1702) when a better partner may come.

Whether that beats the stall on true limits is what the parallel bench-sim session is measuring on b120. Nothing in
session 7's real data shows it would have.

## Q6. Recommendations, ranked by expected points

1. **Default for session 9 (~12:37, tick ~2254–2270): keep `exact` and keep the maker up.** That is 0.5 at no risk.
   - Session 8 ran on it with the maker up (restarted for #284 at 08:27:11Z, deployment 9ce746ec).
   - **Do not merge anything touching `src/**` from ~tick 2240 to ~2275.** A redeploy during the session leaves v19
     unmatched. t07 and t08 showed in session 8 that this scores ~0, which is ~−1.3 to −1.5 final points.
   - Run `scripts/merge_safe.sh <pr>`, which knows the window (it printed "safe until tick 1763" before session 8).
     Deploys are fine until ~tick 2240 (~12:33).
2. **Session 9 (~12:37, tick ~2254): switch to `edge` only if bench-sim shows it beats the stall on b120 and the
   Saturday books.**
   - Setting: `bazaar-maker` env `BAZAAR_BENCH_POLICY=edge`, keeping the default `BAZAAR_BENCH_GUARD_MARGIN` (10),
     which only deviates from exact when the estimated gain is ≥ 10 P.
   - Upside: 0.5 → up to 1.0, which is +1.5 final.
   - Risk: below the stall (< 0.5) if its limit estimates are wrong. The worst case, ~0, is now observed (t07 and t08
     in session 8).
   - Expected value: positive only if bench-sim's win rate over the stall is well above 50 %. Otherwise stay on
     `exact`.
   - Window: a Railway variable change redeploys the maker. Do it now (session 8 is over) and before ~tick 2240
     (~12:33). The organisers can fire an extra unscheduled test at any time (session 7 was one), so
     check `/api/schedule` and `/api/clock` right before.
3. **Do not set `BAZAAR_BENCH_POLICY=probe`**: it is refused 100 % of the time. Leave `BAZAAR_BENCH_MATCH_PROBE` as
   it is; its claim is spent.
4. **Organic (+7.5 × organic per round, currently 0):** see mm-probe.md. Expected only +0.05 to +0.25 final, below
   rank 2.
5. **The dashboard fix** (Q3) is worth 0 points. It is cosmetic, though it would stop the next "100 %" and the wrong
   countdown misleading us. It is for bazaar-live.

**No code change in this repo is clearly needed**, so this branch carries only this document.

## Session 8, read after it ended

Session 8 (run b137, `bench.started` at tick 1774, 08:37:24Z, exactly where `/api/schedule` put it) is the eighth tie.

**Ours.** The book had 20 offers (59 rows). `exact` made 4 matches (quoted surplus 99): ticks 1779, 1783 (×2)
and 1786. `/me` from tick 1790: `bench_efficiency` **0.895**, `bench_points` **0.5**.

**Everyone else**, from the leaderboard market at tick 1782 and then 1802:

- 16 teams moved by only +0.02 to +0.04, which is the Sunday phase drift. Their Sunday bench mean stayed at 0.5, so
  they all scored the stall level again.
- **t07 fell 10.71 → 9.37 and t08 fell 9.06 → 7.73.** Both drops fit a session-8 `bench_points` of about **0**:
  - The Sunday bench mean went from 0.5 to 0.25. That is −5.6 on the Sunday round and about −1.3 overall at phase
    0.43.
  - t07 had replaced its free stall (v11) with a new board venue, v29, at tick 1758, 16 ticks before the session. The
    board apparently had no working broker in time.
  - t08's board (v06, 100 bps) also realised nothing.
- t12 −0.22 is organic drift.

**This is the first observed below-stall score.** The scale is not floored at 0.5: a board venue that does not match
scores about 0. It confirms rec 1, and it is the risk side of rec 2. A maker that is down, or an `edge` plan that holds
pairs until they leave, costs up to the whole 0.5 (~−1.5 final points per session).
