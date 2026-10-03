# B2: venue go-live runbook and Saturday's Market Tests simulated

Night backlog item B2, 4 Oct 2026, 03:10–. Branch `night/b2-venue-runbook`, draft PR #92, stacked on #84 (W1b), which is stacked on #71. Nothing here touched the live game. Every guardrail change below is a **proposal**: `GUARDRAILS.md` is unchanged.

## What a venue is worth on Saturday (500 simulated Saturdays per row)

Saturday has 8 Market Tests (schedule fixture): h5, h7, h9, h11, h13, h15, h17, plus the hard test at h16 (game hour = wall hour, h4 = 09:00).

Without a venue we keep the free stall: 0.5 of the bench points every session. W5's split gives 15 round points for the bench (of market-making's 30), and one Saturday round point is 0.40 final points, so the stall is worth **3.00 final points** all day.

Each cell is the change in final points against that, for our board venue with the W1b edge broker. Sessions are run on W1a's bench (#77) against two stall-level rivals, with the probe's statistics kept across the day. `evals/saturday.py`: `uv run python -m bazaar_agent.evals.saturday --days 500`.

| Bench world | 09:00, edge | 11:00, edge | 09:00, edge + limit probe | 09:00, edge, broker down 5 % of sessions |
|---|---|---|---|---|
| default, quote rule (best guess) | **+0.19** (24 % of days worse, p10 −0.06) | +0.17 | +0.19 | **+0.05** (39 % worse, p10 −0.38) |
| default, limit rule | +0.19 | +0.17 | **+0.67** (10 % worse) | +0.03 |
| 2× shade, limit rule | +0.19 | +0.17 | **+1.51** (0 % worse) | +0.04 |
| whole book at tick 0, quote | +1.17 | +1.03 | +1.17 | +0.98 |
| tick 0, 2× shade, firm, quote | +1.69 | +1.47 | +1.69 | +1.49 |
| tick 0, 2× shade, firm, limit | +1.69 | +1.47 | **+2.69** | +1.47 |

- **A strong rival field** (one rival at the clairvoyant oracle) takes 0.01–0.12 off each cell and changes no conclusion.
- **On our own bench model** the default cell is +0.17, against +0.19 on W1a's bench.
- **The broker must not stop.** A board venue whose broker is not matching realises 0 in that session. It scores 0 there instead of the stall's 0.5, a loss of 0.375 final points per session. The edge gains only about 0.024 per session in the default world, so the break-even downtime is about **6 % of sessions**. In the favourable worlds the venue pays even with the broker down 5 % of the time.
- **09:00 vs 11:00:** opening at 11:00 gives up the h5 session, which is worth 0.02 (default) to 0.34 (favourable). Its upside is two hours to watch the first real Market Test from the free stall (`/me` → `bench_efficiency`) before paying 270 P.
- **The organic half** of market-making (15 round points for value created between other teams on our venue) is B1's item and is not in these numbers. It needs the venue to exist, so it only adds to the case for opening.

## Recommendation

Open at **09:00 only if the broker can run unattended all day**: a restart loop on an always-on machine, watched (§ Runbook, step 6). Otherwise open at **11:00** after watching one session, or not at all.

Run the edge broker under the quote rule (`--bench-policy edge`, one read a tick). Probe the match rule once during the 10:00 session. If the server accepts the probe, switch to `--bench-cross limit --bench-reads 3`: that is worth +0.5 to +1.3 more on the day.

## Minimal guardrail change (PROPOSED, for Marius to approve; not applied)

Today's money: Friday ended with **353 P**. The grant adds 150 P at h4.05 (09:03), giving **503 P**.

Opening takes the bond and fee (250 + 20 = 270 P), and #71's rule keeps cash ≥ `cash_floor` after them. Under today's floor of 270 that needs 540 P, so the opening is refused at any time on Saturday morning (W7). Even if it went through, every later purchase would be refused, because cash would sit below the floor.

`cash_floor`'s only stated purpose is to reserve those 270 P ("venue bond 250 + 20 opening fee"). Once the venue is open that reserve has been spent, so the floor has done its job. Two values change, nothing else:

```diff
- - `cash_floor` = 270 — never let a purchase take cash below this (venue bond 250 + 20 opening fee for level 2).
+ - `cash_floor` = 50 — never let a purchase take cash below this (the venue's 270 P are spent at its opening; 50 P stays as a cushion).
- - `allow_venue_open` = false — build only: ...
+ - `allow_venue_open` = true — ...
```

Why 50:
- It must be ≤ 83 to open at 09:00, before the grant (353 − 270).
- After the grant it must be ≤ 233.
- It must be ≤ 97 to leave W3 + W4's planned ~136 P of ladder and trade buys (W7's deduplicated plan).
- At 50, 183 P stays spendable after the opening.

Bond and fees never score (RULES.md "What never counts"), so the 270 P only matter through the purchases they would have funded.

- **Not proposed:** any other cap. The broker's matches move no cash of ours.
- **Order matters:** open the venue **before** the taker, maker and ladder start spending, or they can take cash under 320 and the opening is refused. A refused opening costs nothing (RULES.md).

## Runbook (venue at 09:00; for 11:00, shift the opening steps by 2 h and probe in the 12:00 session; the bench times are fixed)

All commands run from a checkout with #71, #84 and #92 merged, or from this branch.

| When | Command | Check |
|---|---|---|
| Before 09:00 | Merge the guardrail change above (a commit on `main`), then `uv run bazaar rules` | prints `cash_floor 50`, `allow_venue_open true` |
| 09:00 | `uv run bazaar status` | cash ≥ 320 (353 expected), level 2 |
| 09:00 | `uv run bazaar venue open --name "Team 1 market" --fee-bps 0` | dry run: the guardrail line says allowed |
| 09:01 | `uv run bazaar venue open --name "Team 1 market" --fee-bps 0 --live` | the write is sent; the key is saved to `.local/broker.env` (never printed) |
| 09:01 | `uv run bazaar venue status` | our venue listed, `board`, 0 bps |
| 09:02 | `uv run bazaar broker run --max-ticks 1 --bench-policy edge` | dry run, one tick, no error |
| 09:03 | in a dedicated always-on terminal: `rm -f .local/STOP_BROKER; while true; do uv run bazaar broker run --live --bench-policy edge --read-offset 0.2; [ -f .local/STOP_BROKER ] && break; sleep 2; done` | `broker: bench edge (normal, accepts by quote, 1 read(s)/tick)`, then one line per tick. To stop the loop: `touch .local/STOP_BROKER`, then Ctrl-C (Ctrl-C alone may just restart it) |
| 09:03+ | start the taker, maker and ladder as planned (W3/W4/W7) | they now spend above 50 P |
| 10:00 (h5 bench, first ticks) | `uv run bazaar broker probe --auto`, then the same with `--live` | prints the pair, the price, the chance P it is accepted if limits are checked, and the venue's answer. The pair avoids offers the running broker could take |
| 10:01 | **Accepted:** limits are checked. Stop the loop as above and restart it with `--bench-cross limit --bench-reads 3`. **Refused because an offer is gone or not open:** no evidence; probe again. **Refused on price:** probe again on a *different* pair, `--auto --avoid <sell>,<buy>` (add every earlier pair). Stop when the product of (1 − P) over the price refusals falls under 0.05: then the server checks quotes, and the loop stays as it is. Two refusals at P ≥ 0.8 already get there; at most 4 probes | each probe is logged as `broker_match` with `probe: true` |
| after each session | `tail .local/agents/broker_sessions.jsonl`, `/me` → `score.bench_efficiency` (the monitor's `agent.me` snapshot) | pairs > 0 every session |
| after 10:00 | `cat .local/agents/broker_bench_shapes.jsonl` | the real offer fields. An expiry field means the edge can hold pairs; set `expiry_margin` only after reading what it means |
| 21:00 (h16) | nothing to do: the hard test, same broker | `--bench-preset hard` changes priors only; not worth a restart |

- **Rate budget:** the broker uses its own key for reads and matches, plus one team-key call a tick. The loop already wakes 0.3 s after the tick, so `--read-offset 0.2` puts the first read at W5's broker slot (0.5 s, #78). Once #78 merges, use `BAZAAR_TICK_OFFSET_S` *or* `--read-offset` for the broker, never both.
- **Stopping:** `touch .local/PAUSE` stops every match at once, but a paused board venue scores 0 in a running session. Prefer fixing the broker and restarting the loop.
- **Closing:** `uv run bazaar venue close --live` is always allowed, and the bond comes back after the cooldown. A session counts only venues open during it. Whether the free stall comes back after a close is not stated.
- **Fees** stay at 0: fees never score, and B1 covers attracting other teams' trades.

## What changed in the code (PR #92)

- **`bazaar broker run --read-offset S`** (`BrokerConfig.read_offset_s`, default 0): waits S seconds after the tick before the first read, capped at 40 % of the tick. This is W5's stagger wired into the broker without touching `run_per_tick`, so it does not conflict with #78.
- **`bazaar broker probe --auto`:** picks the non-crossing bench pair and price most likely to be accepted if hidden limits are checked (`bench_edge.best_probe`). It skips offers the broker would cross by quote and any `--avoid` ids, prints the chance, then dry-runs or sends it like `broker probe SELL BUY PRICE`. Given together with SELL BUY PRICE, it is refused.
- **`evals/saturday.py`** + `tests/test_saturday_bench.py`. A test pins the sessions to the schedule fixture.
- Gates: 1,292 passed, 35 skipped; ruff, black and mypy clean.

## Risks

- **The probe is only as good as its answer codes.** A refusal because an offer is gone is not evidence, and the runbook says so. Pairs that would cross with the fee rounded down are never probed, so a lenient server cannot fake an acceptance. Probe statistics live in memory, so a restarted `--bench-cross limit` loop probes again.

- **Broker downtime is the dominant risk in the default world.** Above about 6 % of sessions down, the venue loses points.
- **Every bench number is a model.** Arrivals, shading, relax and the match rule are unobserved. The schedule and W5's weights are the only fitted inputs, and W5's 15/15 bench/venue split is itself assumed.
- **The opening order.** If other agents spend first, the opening is refused (free, but the day starts without the venue).
- **Unknown after a close:** whether the free stall comes back.

## What Marius must decide

1. Approve the two-value guardrail change (or another floor in the 0–83 range for 09:00, or ≤ 233 for after the grant).
2. 09:00, 11:00 or never, given who can keep the broker loop alive all day.
3. Whether the probe at 10:00 goes live.
