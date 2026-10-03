# W1b: a bench broker edge for the Market Test

Night shift 3–4 Oct 2026. Branch `night/w1b-broker-edge`, draft PR #84, **stacked on #71** (`feat/venue-broker-build-only`). Refs #12, #71, #77. Nothing live: `allow_venue_open` stays false, and every new switch defaults to today's behaviour.

## Since 03:40: rebased onto the new #71 (venue keeper)

#71 now opens our board venue from the maker at game hour 6.5 (`agents/venue_keeper.py`), with an exact broker whose ties follow the stall's book order. This PR was re-applied onto it as one commit (the old history is in `backup/w1b-old` locally).

- Every switch still defaults to today's behaviour.
- The keeper's broker takes the edge from the Railway environment: `BAZAAR_BENCH_POLICY=edge` (and optionally `BAZAAR_BENCH_PRESET`, `BAZAAR_BENCH_CROSS=limit`) on `bazaar-maker`. Unset means exact.
- Reads stay at one a tick inside the maker, so its tick is never blocked.
- With #71's tie-break, exact equals the stall on every modelled bench (its own memory note, and this PR's tournament). So the keeper's venue as configured earns the stall's bench points, no more. Setting `BAZAAR_BENCH_POLICY=edge` is the switch that gives the numbers below.

## What was built

| Piece | What it does |
|---|---|
| `agents/bench_model.py` `TraderModel` | One model per bench trader id. The limit band comes from its quotes: cost ∈ [ask₀/1.30, ask₀/1.05] and never above an ask it has shown; value likewise from the bid. The leave hazard comes from #12's patience prior (normal: 0.125, 0.143, 0.25, 0.33, 0.5, 1 at ages 1–6). If the offer carries an explicit expiry, that tick minus one is used instead (the field's meaning is a guess). |
| `agents/bench_edge.py` `BenchEdge` | Takes the exact max-weight matching (#71's Hungarian) of the crossing pairs, weighted by **estimated true surplus**, and sends it at once (holding does not pay; see below). Optional `cross="limit"` probe: after the crossing pairs, it proposes non-crossing pairs at the price most likely to sit inside both limits. A refusal narrows the bands, by exact inclusion–exclusion. The probe gives up on evidence: no acceptance, at least 8 refusals, and P(all refused \| limits honoured) < 1 %. |
| `agents/broker.py`, `bazaar broker run` | New options: `--bench-policy exact\|edge` (default exact), `--bench-preset`, `--bench-cross quote\|limit` (default quote), `--bench-reads 1–3` (default 1). Extra reads are spaced over the tick window and never re-propose an offer already proposed that tick. The first read of each run logs the bench offer's keys to `broker_bench_shapes.jsonl`, so the first real Market Test shows its shape. |
| `evals/bench.py` | In-process tournament: stall, greedy (= `starter_broker.bench_plan`, checked against the kit), exact (#71), edge and edge_limit, plus three bounds: prescient (knows present limits and departures), oracle_quote and oracle_limit. Run with `uv run python -m bazaar_agent.evals.bench`. |
| `evals/bench_w1a.py` | The same policies on **W1a's bench** (`bazaar_sim.bench`, #77), with W1a's stall, oracle and session points. It needs bazaar_sim; tonight it ran on a local copy of W1a's four files. |

Gates: 1,278 passed and 35 skipped (the W1a adapter test skips until bazaar_sim is merged); ruff, black and mypy clean. Planning for 40 traders takes at most 1.5 ms by quote and 14 ms with the probe, retries included (#12's limit is 50 ms). `bazaar broker probe <sell> <buy> <price> [--live]` sends one match and prints the venue's verdict: the morning probe as a command, logged as a decision and gated like any match.

## Evidence: W1a's bench, 1,000 books per row, p50 efficiency

Points are against two stall-level rivals (W1a's reading of RULES.md): 0.5 at the stall, 1.0 above it, below the stall 0.5 × eff/stall. Won/lost is per session against the stall.

| preset | world | stall | **edge** | edge_limit | oracle (mean) | edge pts (won/lost) | edge_limit pts (won/lost) |
|---|---|---|---|---|---|---|---|
| normal | quote, default | 0.827 | 0.827 | 0.827 | 0.864 | 0.527 (6 %/5 %) | 0.527, 10 refused in total |
| hard | quote, default | 0.821 | 0.823 | 0.823 | 0.867 | 0.543 (9 %/6 %) | 0.543, 24 refused |
| normal | limit, default | 0.827 | 0.827 | 0.828 | 0.878 | 0.527 | 0.611 (24 %/14 %) |
| hard | limit, default | 0.821 | 0.823 | 0.827 | 0.882 | 0.543 | 0.646 (31 %/17 %) |
| normal | limit, 2× shade | 0.753 | 0.758 | 0.831 | 0.878 | 0.532 | 0.755 (53 %/16 %) |
| hard | limit, 2× shade | 0.717 | 0.721 | 0.821 | 0.882 | 0.551 | 0.819 (66 %/14 %) |
| normal | limit, 2× shade, all firm | 0.619 | 0.625 | 0.813 | 0.878 | 0.524 | 0.875 (76 %/9 %) |
| normal | quote, tick 0 | 0.991 | **1.000** | 1.000 | 0.999 | 0.690 (38 %/9 %) | same |
| hard | quote, tick 0 | 0.977 | **1.000** | 1.000 | 0.999 | 0.765 (53 %/10 %) | same |
| normal | quote, tick 0, 2× shade, firm | 0.728 | **0.862** | 0.862 | 0.856 | 0.781 (56 %/0 %) | same |
| hard | quote, tick 0, 2× shade, firm | 0.726 | **0.867** | 0.867 | 0.874 | 0.838 (68 %/0 %) | same |
| hard | limit, tick 0, 2× shade, firm | 0.726 | 0.867 | **0.976** | 1.000 | 0.838 | 0.966 (93 %/0 %) |

In every cell the edge's mean efficiency is ≥ the stall's, and its points are ≥ 0.52. The edge never sends a match the quote rule refuses: 0 refused in all quote cells. At most 15 requests per tick were used in the simulations (3 reads + 12 matches). The code allows up to 19 on the broker key (1 clock + 3 reads + 15 matches): 0.63 req/s on a 30 s tick.

## Evidence: own bench (#55 generator, relax 75 %), 1,000 books, p50

| preset | world | stall | edge | edge_limit | prescient | oracle_quote | oracle_limit |
|---|---|---|---|---|---|---|---|
| normal / hard | base (spread arrivals, quote) | 0.802 / 0.803 | 0.801 / 0.803 | same | 0.819 / 0.832 | 0.890 / 0.889 | 0.902 |
| normal / hard | offers carry `expires_tick` | 0.802 / 0.803 | **0.812 / 0.823** | same | 0.819 / 0.832 | | |
| normal / hard | stall crosses 1 pair/tick, tick 0 | 0.875 / 0.785 | **1.000 / 1.000** | same | | | |
| normal / hard | wide shade, limit rule | 0.732 / 0.716 | 0.733 / 0.718 | **0.804 / 0.803** | 0.827 / 0.840 | 0.814 / 0.803 | 0.902 |

Greedy (the starter broker) and exact (#71) equal the stall in every cell of both tables (identical p50 and mean, ±0.001), so they are left out. The full rows are in `--json`.

## What it means

1. **With the quote rule and staggered arrivals (the default, our best guess), no broker beats the stall by much.** The clairvoyant oracle gets +0.04–0.09. A broker that knew every present limit and departure gets +0.02–0.03. Ours gets +0.00. Under the quote rule, all a broker can choose is *which* crossing pairs to cross, and in thin books there is rarely a choice. Holding pairs for better crosses loses (hold threshold 0.13–0.34: −0.04 to −0.45). So does saving flexible traders (−0.02 to −0.04).
2. **The edge pays when the book is thick or the quotes are far from the limits.** With the whole book at tick 0 it wins 38–68 % of sessions and loses ≤ 10 %; with 2× shade and firm traders it is +0.13 p50 and wins 56–68 %. If the offers say when they leave, it is +0.010–0.020 (mean +0.012). It crosses one tick before the stated expiry on purpose: +0.014–0.022 if the field's meaning is confirmed and `expiry_margin` is set to 0.
3. **Under the limit rule, the probe is the big lever:** +0.08–0.25 p50 when the shades are wide, and points 0.61–0.97. If the server checks quotes, the probe costs 8–82 refused requests over 1,000 sessions, then switches itself off. Probing at once beats waiting until a trader is likely to leave: average points over the limit cells are 0.764 at once and 0.652–0.759 with a hazard threshold of 0.13–0.5.
4. **The stall's own rule matters as much as ours.** If the free stall crosses one pair per tick (RULES.md: "crosses its best bid and ask every tick"), then any broker, even greedy, is +0.12–0.19.

## Go / no-go against PLAN.md

- **p50 ≥ 0.85 and ≥ stall + 0.15 in both presets: NO-GO** in the default world, under both rules. The bar is met only in the corners: limit rule + tick 0 + wide/firm (0.98, +0.25); one-pair stall at tick 0 (+0.13/+0.22). Quote + tick 0 + wide/firm comes close (+0.13/+0.14, p50 ≥ 0.86).
- **0 infeasible matches: GO.** 0 refused by quote in every quote cell. The edge_limit refusals are probes by design.
- **Rate budget: GO.** ≤ 19 requests per tick on the broker key (W5 counts it as a separate bucket) and 1 on the team key.
- **W1a's points bar** (never below the stall on any book; mean points ≥ 0.70): the edge loses 1–10 % of sessions, so "never below" fails. Mean points ≥ 0.70 holds only in the tick-0 and limit-rule cells. The edge is never below 0.5 in mean.

**Recommendation:** merge as build-only (defaults unchanged). On the day a board venue opens, run `--bench-policy edge` with one read a tick: in every world modelled it is ≥ the stall in mean, and it never sends a match the quote rule refuses. Under the quote rule extra reads buy nothing, because the book changes within a tick only through our own matches. Use `--bench-reads 3` only together with `--bench-cross limit` (probe retries), and only if Marius accepts refused probe requests, a handful in total if the server checks quotes.

## Risks

- **The bench model.** No real Market Test has been observed: the feed capture and the shared DB have no `bench.*` event. Arrivals, relax, shades and patience are assumptions from #55/#12, and the edge's priors use the same numbers. If the shading is wider than the prior, selection degrades to stall level (`wide`: +0.00).
- **Losing sessions.** The edge loses 0–10 % of sessions to the stall, edge_limit up to 17 %. Under W1a's curve a loss scores 0.5 × eff/stall: a session 6 % short of the stall costs 0.03 against a tie. The real curve below the stall is unpublished.
- **Refused probes might count against a venue.** RULES.md says nothing about refused matches; suspension is "for breaking the rules". The SDK's `Broker.match` docstring reads "(ask <= price, price + fee <= bid)", which is evidence for the quote rule.
- **Probe statistics live in memory.** Every broker restart probes again, up to about 13 refused requests per restart on a quote-only server. A pair that would cross with the fee rounded down is never probed, so a server that floors the fee cannot fake an acceptance (r1 review).
- **After #68 merges**, `broker_context` should take the kill switch hold (`stops=kill_switch(rules)`) like every other agent.
- **Extra reads have not been tested against a real server** (fakes only). In W1a's bench the book does not change within a tick, so their value there is the probe's retries only.

## What Marius must decide

1. **The bar:** keep "stall + 0.15" (no-go) or adopt W1a's points bar.
2. **The morning probe:** one manual non-crossing match during a Market Test (`uv run bazaar broker probe b12-3 b12-4 <price between the quotes> --live`; a 400 means quotes), or let `--bench-cross limit` probe and give up by itself. Either way it needs an open board venue, which means `allow_venue_open` and 540 P with today's `cash_floor`. Neither changes tonight.
3. **Default after the first real Market Test:** read `broker_bench_shapes.jsonl` (an expiry field would turn on the hold, +0.01–0.02; check what the field means before setting `expiry_margin` to 0). Compare our `/me` `bench_efficiency` with W1a's calibration table, then choose `bench_policy`.
