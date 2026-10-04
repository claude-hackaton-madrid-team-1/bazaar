# Market Test benchmarks: how to run each harness, and what each one said

Every harness Team 1 built to study the Market Test (the synthetic bench book every venue receives each session), in
the order they were built. Each entry says how to run it, what it answers, the headline numbers it produced and where
those numbers are written up. Numbers are copied from the reports, not re-run for this page.

Conventions:
- **E[points]** = mean `bench_points` per session under the points rule (0.5 = the free stall's level, 1.0 = the
  top-three mean). "Linear" scores a session below the stall at 0.5 × E/E_stall (Saturday's fit); "zero-below"
  scores it 0 (the pessimistic reading; also written E0). Neither is published (see [README](README.md#what-is-still-unknown)).
- **P(above) / P(below)** = share of sessions where the policy ends above / below the stall on the same book.
- Commands run from the repo root. `--replay` reads Postgres `bench_books` and needs `DATABASE_URL` (read-only use
  only; never print it).

## At a glance

| Harness | File | Answers | DB? | Status |
|---|---|---|---|---|
| W1a bench simulator | `src/bazaar_sim/bench.py`, `uv run bazaar-sim bench` | How much headroom over the stall exists in a modelled bench | no | superseded by the calibrated worlds |
| Edge proof | `scripts/bench_edge_proof.py` | Does `BAZAAR_BENCH_POLICY=edge` beat the stall on #77's bench | no | done; edge ≈ stall |
| Venue on a simulator | `scripts/sim_market_test.py` | Does the maker open a venue and broker a Market Test end to end | no (local sim) | done |
| Saturday eval (B2) | `evals/saturday.py` on branch `night/b2-venue-runbook` only | What a venue is worth over a simulated Saturday | no | branch only, superseded |
| Hand probe | `scripts/bench_probe_once.py` | Does the server match a non-crossing pair (sends ONE POST) | no | never sent |
| Live match probe | `BAZAAR_BENCH_MATCH_PROBE=once` (`agents/bench_match_probe.py`) | Same question, inside a live session | live | **fired s7: `400 bad_match`** |
| Bench-book recorder e2e | `docs/market-making/reports/bench-books/e2e_local_pg.py` | Does the recorder write and the dashboard read `bench_books` | local Postgres | done |
| **Calibrated tournament** | `scripts/bench_tournament.py` | Which policy beats the stall on books fitted to the real sessions | only for `--replay` | **the main harness** |
| **Posterior proof** | `scripts/bench_posterior_proof.py` | `lookahead_safe` / `lookahead_bold` vs #292's `lookahead` | only via the tournament's `--replay` | done |
| Lookahead v2 | `scripts/bench_tournament.py` on branch `feat/bench-lookahead-v2` | `slack`, a time budget, zero-below scoring | only for `--replay` | branch only, not deployed |
| Saturday stats | `docs/market-making/reports/mm-probe/{bench_stats,market_stats,spreads}.py` | Per-session bench table, market medians, spreads at the close | read-only | done |
| Maker bid-lapse probe | `docs/night/b14_maker_probe.py` | Maker bids lapsing unfilled (spend accounting) | no | not a Market Test harness |

## 1. W1a bench simulator (`bazaar_sim.bench`, PR #77)

```
uv run bazaar-sim bench --seeds 1000            # stall and oracle per preset (normal, hard) and match rule
```
```python
from bazaar_sim.bench import HARD, NORMAL, make_book, simulate, stall_policy
r = simulate(policy, NORMAL, seed=7, rule="quote")   # policy(book) -> [(sell, buy, price), ...]
r.efficiency, r.stall, r.oracle, r.points()
```

- Presets: normal 10 traders / 16 ticks, hard 12 / 16; firm 0.20 / 0.35, impatient 0.25 / 0.35; arrivals, relaxing
  and shades were **assumptions** (no real book existed yet). Rule `quote` (today's) or `limit` (counterfactual).
- Headline (1,000 books): stall p50 0.83 (normal) / 0.82 (hard); oracle p50 0.91; oracle − stall p50 **+0.027**
  (normal, quote), +0.042 (hard, quote), +0.046 / +0.062 under `limit`. "Stall + 0.15" was unreachable in the
  default cell.
- Report: [night/w1a-bench-sim.md](night/w1a-bench-sim.md). Pitch chart data built from it and B2:
  `docs/pitch/charts/05_market_test.csv` (simulated Saturday-night figures; superseded by Sunday's real data, see
  [sessions.csv](sessions.csv)).
- Superseded because: the real books (sessions 7 and 8) have two-bump sellers, bids down to 0.6 × value and steps
  twice #77's; #77's preset cannot draw an ask above 78 ([reports/bench-sim.md §1](reports/bench-sim.md)).

## 2. Edge proof (`scripts/bench_edge_proof.py`, PR #218)

```
uv run python scripts/bench_edge_proof.py --seeds 1000 [--json out.json]
```

- Policies: stall, exact (`matcher.plan_matches`), edge with guard margin 10 (default) / 20 / 5, and unguarded
  (`BAZAAR_BENCH_GUARD_MARGIN=none`). Variants `default`, `x2`, `tick0`, `shade2`, `firm`, `narrow`, `x2narrow`.
  Four readings of the points curve (field, rivals +0.03, steep, harsh).
- Headline (W1b on W1a's bench, quote rule): edge mean efficiency = stall (±0.001), wins 7–9 % of sessions, session
  points 0.527 (normal) / 0.543 (hard). Corners only: whole book at tick 0 + 2× shade + firm, +0.13 p50.
- Live: `edge` never overrode exact on a real book (all 6 h11 pairs carry the exact reason; in fact h11 ran `exact`,
  see the [decision log](README.md#decision-log)). Calibrated sim: `edge` 0.513, `edge_none` 0.570; both real
  replays 0.500.
- Reports: PR #218 body; W1b's `docs/night/w1b-broker-edge.md` on branch `night/w1b-broker-edge`.

## 3. Venue on a simulator (`scripts/sim_market_test.py`)

```
uv run python scripts/sim_market_test.py --sessions 2 [--open-after-hours H]
```

One process, real HTTP against a local `bazaar_sim`: the maker opens its venue, its broker plays the Market Test,
and each session prints our realised share next to `bazaar_sim.bench.run_stall` on the same book. Proves the wiring,
not an edge (exact = stall by construction). The B5 rehearsal ran 11 local Market Tests with 0 refused
(`_night` log, 06:16 Sat).

## 4. Saturday eval (B2, `evals/saturday.py`, branch `night/b2-venue-runbook` only)

```
git switch night/b2-venue-runbook && uv run python -m bazaar_agent.evals.saturday --days 500 [--clock resume]
```

Simulated Saturdays (500 per row) on W1a's bench: plans h4.05 / h6.5 / never, policies exact / edge / edge + limit
probe, rival fields stall / strong / top3, broker downtime. Headline: the exact keeper = the free stall (+0.00);
edge +0.17 final points in the default world; edge + limit probe +0.6 to +2.35 **only if the server checked hidden
limits** (it does not: §6). Report: [night/b2-venue-runbook.md](night/b2-venue-runbook.md), reconciled by
[night/b20-venue-path.md](night/b20-venue-path.md).

## 5. Hand probe (`scripts/bench_probe_once.py`)

```
uv run python scripts/bench_probe_once.py                 # dry: book + candidate pair, no POST
uv run python scripts/bench_probe_once.py --send --watch  # ONE POST, then re-read the book next tick
uv run python scripts/bench_probe_once.py --after         # /api/me bench_points + public board
```

Built for the Saturday h13 session; there is no evidence it was ever sent ([reports/mm-probe.md §1, §2.2](reports/mm-probe.md)).
The question it asked was settled on Sunday by §6.

## 6. Live match probe (`BAZAAR_BENCH_MATCH_PROBE=once`, PR #263)

One non-crossing POST ever, guarded by a durable Postgres claim. It fired in session 7:

```
tick 1692 broker: PROBE sell b120-12 (ask 40) x buy b120-2 (bid 39) at 39, quotes 1 short of crossing
… probe answer: turned down {"accepted": false, "status": 400, "code": "bad_match",
  "message": "price must sit between the ask 40 and the bid 39"}
```

**The server checks posted quotes, not hidden limits.** One tick later b120-12 stepped to 38 and `exact` matched the
same two traders. This killed `probe` (PR #257), the limit-rule rows of §1, §2 and §4, and mm-probe's Book A EV.
Source: [reports/bench-baseline.md Q5](reports/bench-baseline.md), `decisions` id 4264.

## 7. Bench-book recorder end to end (`bench-books/e2e_local_pg.py`)

```
docker run -d --rm --name bench-books-pg -e POSTGRES_PASSWORD=x -p 127.0.0.1:55439:5432 postgres:18
LOCAL_PG_URL=postgresql://postgres:x@127.0.0.1:55439/postgres \
  uv run python docs/market-making/reports/bench-books/e2e_local_pg.py <dir of book_*.json> <stats dir>
docker stop bench-books-pg
```

Refuses any host but localhost. Replays captured `GET /api/broker/book` snapshots through the real `BenchBooks`
writer, then reads them back through bazaar-live's `show.venue_books`. Result: 44 Saturday b52 snapshots → 66 rows,
9 traders, 0 failures. In prod the recorder first ran in session 7: b120 61 rows / 24 offers, b137 59 rows / 20
offers. Report: [reports/bench-books.md](reports/bench-books.md).

## 8. Calibrated tournament (`scripts/bench_tournament.py`, bench-sim, PR #292)

```
uv run python scripts/bench_tournament.py --seeds 300                       # all worlds, all policies
uv run python scripts/bench_tournament.py --seeds 300 --robust              # main world, each parameter ±50 %
uv run python scripts/bench_tournament.py --replay b120 b137 --draws 300    # real books (DATABASE_URL, read-only)
uv run python scripts/bench_tournament.py --worlds cal_normal20 --only lookahead,edge_none --json out.json
uv run python scripts/bench_tournament.py --plugin my_policies.py           # POLICIES = {"name": lambda traders: Policy()}
```

- **Worlds** (`WORLDS`): `cal_normal20` (session 9's world: 10 a side, fitted to b120 + b137), `cal_hard24`
  (12 a side, firmer, more impatient), `cal_normal20_uniform` (one-bump sellers), `old_normal20` (#77's preset,
  twice the traders). `--robust` adds 12 worlds: firm, impatient, relax, shade, cheap_share, lives, each ±50 %.
- **Policies** (`POLICIES`): `stall`, `exact`, `edge20` / `edge` / `edge5` / `edge0` / `edge_none`,
  `edgecal_none` / `edgecal5`, `maxcount`, `hold3` / `hold6`, `lookahead`; bounds `who_oracle` (knows limits, never
  holds) and `oracle` (knows everything). `stall` and `exact` always run.
- **Plugins**: a `.py` file with `POLICIES = {"name": lambda traders: Policy()}`, `Policy()(book) -> [(sell, buy,
  price), ...]`; `book` is the real `GET /api/broker/book` shape. `traders` is the hidden truth, for bounds only.
- **Replay**: rebuilds a recorded book from `bench_books` and rejection-samples each offer's hidden limit, life and
  relax rate so that every recorded quote path is reproduced. Once a policy deviates from what we did, the book it
  sees is simulated. **A replay result is a posterior mean, not a real-data result.**

Headline, E[points] (300 books a world; [reports/bench-sim.md §2–4](reports/bench-sim.md)):

| world | exact | edge | edge_none | maxcount | hold6 | **lookahead** | who_oracle | oracle |
|---|---|---|---|---|---|---|---|---|
| cal_normal20 | 0.500 | 0.513 | 0.570 | 0.544 | 0.538 | **0.642** | 0.675 | 0.932 |
| cal_hard24 | 0.500 | 0.519 | 0.583 | 0.557 | 0.538 | **0.639** | 0.695 | 0.955 |
| cal_normal20_uniform | 0.500 | 0.540 | 0.636 | 0.632 | 0.525 | **0.682** | 0.709 | 0.960 |
| old_normal20 | 0.500 | 0.543 | 0.598 | 0.579 | 0.575 | **0.660** | 0.635 | 0.970 |

- `lookahead` on cal_normal20: P(above) 0.293, P(below) 0.140, worst −0.224 efficiency, mean margin +2.0 % of the
  possible gains (oracle headroom +10 %). ±50 % worlds: 0.583–0.666.
- The headroom is **timing**: `oracle` beats the stall by +8 to +10 %, `who_oracle` by < 1 % (and below it on #77's
  world). Naive holding (`hold*`, 42 settings) loses on average.
- Compute: mean 3 ms, worst ~0.1 s a tick on a laptop (the review measured 0.4–0.6 s on a contrived worst tick).

## 9. Posterior proof (`scripts/bench_posterior_proof.py`, bench-search, merged in #292)

```
uv run python scripts/bench_posterior_proof.py --seeds 900 [--samples 96] [--robust] [--worlds cal_normal20]
uv run python scripts/bench_tournament.py --plugin scripts/bench_posterior_proof.py --replay b120 b137 --draws 300
```

Runs the tournament's worlds with the same books for `stall`, `exact`, #292's `lookahead`, `lookahead_safe` (a
session below the stall weighed 0 in the planner) and `lookahead_bold` (weighed 0.5 × the fitted rule); prints E and
E0 (zero-below). The policies live in `agents/bench_posterior.py` (96 posterior samples, one-step lookahead, deviates
from the stall's pairs only if it beats them by `min_edge` 0.01 points).

Head-to-head, 600 books a world, E linear / zero-below ([reports/bench-search.md](reports/bench-search.md)):

| world | exact | #292 `lookahead` | **`lookahead_safe`** | `lookahead_bold` |
|---|---|---|---|---|
| cal_normal20 | 0.500 / 0.500 | 0.628 / 0.556 | **0.668 / 0.601** | 0.713 / 0.568 |
| cal_hard24 | 0.500 / 0.500 | 0.641 / 0.528 | **0.672 / 0.571** | 0.717 / 0.542 |
| cal_normal20_uniform | 0.500 / 0.500 | 0.682 / 0.555 | **0.682 / 0.567** | 0.717 / 0.532 |
| old_normal20 | 0.500 / 0.500 | 0.669 / 0.520 | 0.656 / 0.517 | 0.670 / 0.460 |

- `lookahead_safe` on cal_normal20: P(above) 0.35, P(below) 0.15, worst −0.34. Ahead of #292 under zero-below in 10
  of 12 ±50 % worlds, under linear in 12 of 12. Paired sign test (zero-below, safe vs #292): cal_normal20 112 / 64
  books (p ≈ 0.0004), cal_hard24 124 / 90, uniform 117 / 106 (n.s.).
- Compute: mean 11 ms, max 0.33 s a tick (loaded laptop), far inside the ~13 s tick.

## 10. Lookahead v2 (branch `feat/bench-lookahead-v2`, `af794985`, NOT deployed)

```
git switch feat/bench-lookahead-v2
uv run python scripts/bench_tournament.py --seeds 300 --robust --replay b120 b137 --draws 300 \
  --only lookahead,lookahead_slack1,lookahead_slack2,lookahead_slack4,edge_none
```

Adds to #292's `lookahead` (not to `lookahead_safe`): `LookaheadConfig.budget_s = 1.5` (past it, decide on the
samples done, or send the exact plan below 16 samples; review MED 1), zero-below scoring in the harness (review MED 2),
and `slack` (leave the exact plan for the best other matching when it is at most `slack` expected P worse).
cal_normal20 E linear / zero-below: `lookahead` 0.642 / 0.577, `slack1` 0.656 / 0.587, `slack4` 0.691 / 0.577.
Full table: [reports/bench-sim.md §7](reports/bench-sim.md). The code is a `src/**` change (maker, taker and duels
redeploy on merge), so it stays on its branch; only its report section was brought here.

## 11. The real books replayed (b120 = session 7, b137 = session 8, session 9)

E[points] linear / zero-below, 300 posterior draws per book. **In-sample**: the priors were fitted on these same two
books. And the replay's stall level is off: 0.937 vs the server's 0.967 on b120, 0.777 vs 0.895 on b137, so only
the sign of an edge is informative, not its size ([reports/market-making-dossier.md §4.4](reports/market-making-dossier.md)).

| policy | b120 (s7, hard, 12 a side) | b137 (s8, normal, 10 a side) | source |
|---|---|---|---|
| exact = stall | 0.500 / 0.500 | 0.500 / 0.500 | bench-sim §4 |
| edge, edge_none, hold6 | 0.500 | 0.500 | bench-sim §4 |
| maxcount | 0.500 | 0.456 (below in 99 % of draws) | bench-sim §4 |
| #292 `lookahead` | 0.502 / 0.422 | **0.899 / 0.827** (P above 0.82) | bench-sim §4, §7 |
| `lookahead_slack1` (v2) | 0.502 / 0.422 | 0.909 / 0.847 | bench-sim §7 |
| `lookahead_slack4` (v2) | 0.762 / 0.557 | 0.913 / 0.853 | bench-sim §7 |
| **`lookahead_safe`** (deployed for s9) | **0.806 / 0.653** | 0.783 / 0.610 (P above 0.60) | bench-search H2H |
| `lookahead_bold` | 0.808 / 0.653 | 0.783 / 0.610 | bench-search H2H |
| who_oracle (bound) | 0.640 | 0.481 | bench-sim §4 |
| oracle (bound) | 0.920 | 0.990 | bench-sim §4 |

What the deviations are, on the real quotes (bench-sim §4):
- **b137 t1783**: exact (= stall) matched cheap seller 12 (ask 32) to firm buyer 5 (bid 46); buyer 2 arrived one tick
  later bidding 68 → 74 and left unmatched. Holding seller 12 one tick = +22 quoted surplus, if seller 12 would have
  stayed (unknowable: we matched it the tick it appeared). This one decision is #292's whole b137 edge.
- **b120 t1696 / t1701**: take a relaxing buyer about to leave before a fresh one; two pairs where the stall makes one
  (neutral in reality: 22 × 10 crossed the next tick anyway).
- Paired sign test on the replays (zero-below): b120 192 / 81 draws for safe, b137 1 / 71 for #292. The split comes
  from the objectives (#292 holds for surplus; safe plays P(above)), not from in-sample priors (identical priors).

**Session 9** (the real outcome of `lookahead_safe`): see the [README session table](README.md#sessions-1-to-9).
A replay of its book was not run for this page; the command is the same with its run id
(`--replay <run> --plugin scripts/bench_posterior_proof.py`).

## 12. Saturday statistics (mm-probe scripts, read-only)

```
(cd docs/market-making/reports/mm-probe && uv run python bench_stats.py)    # per-session table → evidence/bench_stats.out
uv run python docs/market-making/reports/mm-probe/market_stats.py            # Saturday market by rarity → evidence/market_stats.out
uv run python docs/market-making/reports/mm-probe/spreads.py                 # close-of-day spreads → evidence/spreads_close.out
uv run python docs/market-making/reports/mm-probe/q.py "SELECT ..."          # read-only query helper
```

`q.py` reads `DATABASE_URL` from the lets-start worktree's `.env` (a hard-coded local path; edit it on another
machine), never prints it and opens every session read-only. Headline: Saturday's 6 sessions, 25 pairs, 0 refused,
bench_points 0.5 each; v19 0 organic trades; team trades print at 64–87 % of the median ask. Report:
[reports/mm-probe.md](reports/mm-probe.md).

## 13. Maker bid-lapse probe (`docs/night/b14_maker_probe.py`, not a Market Test harness)

```
PYTHONPATH=. uv run python docs/night/b14_maker_probe.py <label>
```

Runs the real maker alone against the in-process simulator (no rivals) for 240 ticks and counts bids posted,
bid-ticks on the board, lapses refunded and committed cash. It measures our **own quoting** (the negotiating
column), not the scored market-making criterion: after the B14 fix, bid-ticks on the board 164 → 470 at 30 s ticks.
Kept in `docs/night/` next to its report, [`b14-expired-bids.md`](../night/b14-expired-bids.md).
