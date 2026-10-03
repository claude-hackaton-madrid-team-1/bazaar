# B20: the fastest safe path to an open venue

Night of 3–4 Oct 2026. Branch `night/b20-venue-path`, draft PR, base #71 at 1696789 (the venue keeper). It reconciles #71 with W1b's B2 runbook (#92, already rebased on e489449) and uses W1a/B1's bench numbers (#77, #94). **Proposal only: no GUARDRAILS.md value is changed.** The one new parameter defaults to today's behaviour.

> **As merged by #218 (BE1, Sat 3 Oct afternoon) — read this before the steps below.** Two switches are wired on
> `bazaar-maker`: `BAZAAR_BENCH_POLICY` (`exact` default | `edge`) and `BAZAAR_BENCH_GUARD_MARGIN` (estimated P,
> default 10; `none` = unguarded, #84 as it was). `BAZAAR_BENCH_CROSS` and `BAZAAR_BENCH_PRESET` are **not wired**: set,
> the maker logs `IGNORED`, and no limit probe is ever sent. With `edge` the maker's start line is
> `venue keeper: broker bench edge (guard margin 10 P)` (or `(unguarded, as #84)`), and the keeper's
> `broker on for vNN (LIVE), bench ...` line repeats it. Proof and points tables: `scripts/bench_edge_proof.py`, PR #218.
> **Since `fix/bench-edge-needs-confirm`:** `edge` also needs `BAZAAR_BENCH_EDGE_CONFIRM=yes`; otherwise the maker logs `bench policy edge IGNORED` and stays `exact`.

## The premise needs one correction

"No venue = 0" does not apply to us as written. From team venues' opening (+3 h), every team without a venue has a **free starter stall** (RULES.md "Your own market"). `/api/me` carries its broker key (`starter_broker_key`, the kit's `me()`), and the kit says the starter broker "earns what the free stall earns, half the bench points". So without opening anything we should already score the stall's level, 0.5, every session. That is 0.375 final points per Saturday session in W5's/B2's units (15 bench points × 0.40 ÷ 8 sessions × 0.5).

**This is unverified.** Friday's feed ends at tick 159, before the first Market Test (h3), and no `/api/me` of ours shows a stall yet. The 10:10 check below settles it. Until then the decision has to be robust to both cases: the stall counts (likely), or it does not.

## What each choice is worth on Saturday (bench only, final points)

Per session: 0.375 at the stall's level, 0.75 at the top-three mean. The edge numbers are B2's (500 simulated Saturdays, W1a's bench, default quote-rule world); "down" = the maker not matching in 5 % of sessions.

| Plan | free stall counts (likely) | free stall does not count |
|---|---|---|
| A. No venue (`allow_venue_open = false`) | 3.00 | **0.00** |
| B. #71 as shipped: board, exact broker, 11:30 | 3.00 − 0.12…0.15 (down) | 2.62 (h5 lost) − down |
| C. Auto venue at 09:00 (`venue_mechanism = auto`, opens at h4.05) | 3.00 (no broker to fail; −20 P fee) | **3.00** |
| D. Board + edge broker (#84) at 09:00 | 3.19, or +0.04…0.05 net of down; +0.9…1.7 in thick-book worlds | 3.19 − down |
| E. B2's plan: board + edge at 11:30 | 3.17 (+0.04 net of down) | 2.79 (h5 lost) |

1. **Being open early is insurance, not a gain.** Opening at 09:00 instead of 11:30 only adds the h5 session (10:00). That session is worth 0.375 if the stall does not count, and ~0.02 (edge only) if it does. Cash makes it free: #71 already keeps `cash_floor + venue_bond_reserve` = 370 P untouchable until the venue opens, so spendable cash is 133 P either way.
2. **A board venue with the exact broker is dominated by an auto venue.** Both score the stall's level while the maker runs, and the board venue scores 0 while it does not. Board only pays with W1b's edge policy (`BAZAAR_BENCH_POLICY=edge`, #84), and more if the server honours hidden limits (B2's `BAZAAR_BENCH_CROSS=limit`, +0.4…1.2). B1's cautious policy is a smaller alternative (+0.03 final points per session).
3. **Auto also serves organic market-making.** An auto venue crosses its own best bid and ask every tick (RULES.md), so a rival pair posted on it settles with no broker running.

## The proposal (one rule, decided before 09:00, kept all day)

> **Open at 09:00. Board with the edge broker if #84 is merged and `BAZAAR_BENCH_POLICY=edge` is set on `bazaar-maker` before 09:00; otherwise auto.**

**The choice is firm for the day.** The mechanism is set only at opening (RULES.md and the SDK offer `set_fee`, nothing else). Undoing it means a close, a 10-tick cooldown before the bond comes back, the 20 P fee again, the 270 P reserve again (which the floor will likely refuse), and a hand-opened venue whose key the keeper's vault does not hold.

- **"Edge live" means** `bazaar-maker` already runs `BAZAAR_BENCH_POLICY=edge` and its startup log says `venue keeper: broker bench edge`. Anything less means auto.
- **Choosing auto knowingly gives up** the edge (+0.17 default world, up to +1.5) and the limit probe (+0.4 to +1.2 if limits are honoured) for all of Saturday.
- **Opening at 09:00 costs little if the stall counts:** with the exact broker it is one more session exposed to maker downtime (≈ −0.02 at 5 %), and with the edge it adds +0.02 to +0.34 (W1b).

### GUARDRAILS.md diff (for Marius to approve; not applied)

```diff
-- `venue_open_after_game_hours` = 6.5 — ...
+- `venue_open_after_game_hours` = 4.05 — the first tick after Saturday's 150 P grant (09:03 under the published calendar; ~10:24 if the clock resumes at h2.65, B6)
-- `venue_mechanism` = board — ...            (new line in this PR; default board = #71's behaviour)
+- `venue_mechanism` = auto — ONLY if the edge broker is not live by 09:00
-- `cash_floor` = 100 — ...
+- `cash_floor` = 50 — optional: spendable 133 → 183 P (B2), the venue still opens
```

**Cash** (Friday's 353 P + the 150 P grant = 503 P, B2/W7):
- At h4.05 the opening needs cash − 270 ≥ `cash_floor`: 503 − 270 = 233. It passes at either floor.
- If the keeper reads `/me` before the grant lands, the opening is refused by our own guardrail (nothing is sent) and retried 10 ticks (5 min) later, still before h5 at 10:00.
- **The minimum safe `cash_floor` once the bond is locked is functionally 0.** The bond comes back on close, the server refuses an unaffordable accept at no cost, and duels need no cash, so the floor is a budget, not a safety rail. **Recommend 50**: it fits W3 + W4's ~136 P plan (133 P at floor 100 is 3 P short) with a ~47 P cushion. Below 26 P it blocks a single uncommon buy; Sunday's 150 P grant refills it.

### Saturday, wall clock

| When | What | Check |
|---|---|---|
| before 09:00 | Marius: merge #71 (+ this PR for `venue_mechanism`; + #84/#92 if the edge broker is wanted); approve the diff; set or leave `BAZAAR_BENCH_POLICY=edge` on `bazaar-maker` and pick `venue_mechanism` by the rule above | `uv run bazaar rules` shows `venue_open_after_game_hours 4.05` and the mechanism |
| 09:00–09:05 | the keeper opens "Team 1 market", 0 bps; the key goes to Postgres | maker log `venue: OPENED vNN (auto\|board, 0 bps)` |
| 09:05 | (board only) the maker's log shows `broker on for vNN (LIVE)` | — |
| 10:00–10:08 | **h5**, the first Market Test with our venue | (board) `Market Test bNN over: pairs, quoted surplus` |
| ~10:10 | `/api/me` → `score.bench_efficiency`, `bench_venue` | our venue named = sessions count our venue; the efficiency is a real calibration point (W1a's table: stall p50 0.83 in the default cell, 0.59–0.75 with wider shades) |
| every session | the same read | a board venue below the stall twice in a row → check the maker, consider `venue_mechanism` auto next day |
| optional | `uv run bazaar broker watch --ours` on the machine that holds our broker key, then `bazaar broker calibrate` | logs `bench_offers` every tick (read-only); unbiased on a board venue |

**If Marius keeps no venue for the morning** (plan A until a decision), run `uv run bazaar broker watch` with the team key from 09:05. It reads the free stall's book through `starter_broker_key` and logs the h5 bench, so the first real data on arrivals, patience and relaxing arrives before anything is opened. That book is biased (the engine crosses first; see `bench_watch.calibrate`).

## What changed in this PR

- `venue_mechanism` guardrail, `board` by default. The keeper opens an `auto` venue when it says so, and then starts no broker (a broker cannot act on auto).
- `bazaar broker watch` (read-only): logs the Market Test's `bench_offers` once a tick from the free stall's book (key from `/api/me`, scrubbed from telemetry) or our own (`--ours`), keeping any unknown offer field such as an expiry. It never sends anything.
- `bazaar broker calibrate`: per run, it reports arrivals, stays, crossed vs left (on the stall a crossed pair vanishes one relax step apart), firm share, relax step and extra fields. These are exactly W1a's unverified model inputs.
- Tests: 6 new (keeper auto and listed mechanism, watch, calibration). Gates: 1,976 passed, ruff, black and mypy clean.

## Risks

- The free stall counting at 0.5 is read from the kit's text and is not yet observed. Plan C is the choice that does not depend on it.
- B2's edge numbers and B1's policy are in-sample by model (W1a's bench).
- An opening at 09:00 locks 270 P three hours earlier than #71 planned. Spendable cash is unchanged (133 P, or 183 P at floor 50), because #71's reserve already holds it.
- `broker watch --ours` needs our broker key on the watching machine. B2 avoids copying it, so it is optional; the stall watch needs only the team key.

## What Marius must decide

1. Approve `venue_open_after_game_hours` 6.5 → 4.05.
2. Pick the mechanism by the rule: edge broker live by 09:00 → board, otherwise auto.
3. Optionally `cash_floor` 100 → 50.
