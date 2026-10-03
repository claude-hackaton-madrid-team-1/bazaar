# SP1 — Speed: every agent finishes its tick inside Sunday's 15 s  (per-task spec)

- Task id: SP1 (local backlog; coordinator task `task_d75f80cadcbc`)
- Status: **in review** (PR #157) — measured Sat 05:20-06:20; rebased onto main with #105 #91 #108 #72 (step 6 done)
- Backlog source: local (`.ai/specs`). Traces up to: [`01-spec.md`](./01-spec.md) · Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Sunday runs 15 s ticks (Saturday 30 s). The taker, the maker and the duel player must each finish a tick with
margin before `next_tick_in`, and the three together must stay under the key's 5 req/s (RULES.md: 5 per
second per key, bursts of 20). Measure where a tick's time goes with the PRs that will be live on Sunday,
then cut the hot spots in our code without changing a single move.

## Scope / non-goals
- Measure on a scratch merge of origin/main + #89 #96 #112 #91 #105 #108 #111 #71 #72 (never pushed), against
  a local `bazaar-sim` at 15 s ticks, with `scripts/tick_profile.py` (per-tick wall time, keyed and public
  requests, Jev, recall, Postgres, the /me source, the decisions dropped as `expired`).
- Fix in our code on origin/main; nothing is cherry-picked from the open PRs. A fix that needs a PR's code
  (recall, the /me snapshot) is described here and lands after that PR merges.
- Not in scope: the LLM words (`llm_words = false`), the desk, the MCP server's own traffic (dry run).

## Acceptance criteria (each MUST be testable)
- [x] 1. A repeatable profiler: `scripts/tick_profile.py run|report` against a local simulator only (refuses
  any other target); one JSONL row per tick and per request.
- [x] 2. Measurements at 15 s ticks for taker + maker + duels together (local latency and with a realistic
  per-request latency injected), with wall p50/p95/max, ticks over budget, dropped decisions, 429s, and the
  key's busiest second — pasted in the PR.
- [x] 3. The taker reuses a Jev answer for an unchanged state (offer accept, pack slot), tick and game hour
  aside, for `jev_cache_ticks` ticks; a failed call is asked again next tick; 0 turns it off.
- [x] 4. Each tick's independent reads go out together (`parallel_reads`): the public ones (dealers, catalog,
  venues, feed, each venue's board) at once, the keyed ones (/me or its snapshot, our offers, open threads, dealer
  threads) one at a time beside them; the decision waits for all of them.
- [x] 5. Tests prove identical moves: the same ticks with the speed rules off and on send the same writes
  and record the same decisions.
- [x] 6. Both behaviours sit behind GUARDRAILS.md lines (`jev_cache_ticks`, `parallel_reads`): code built
  without the file behaves as before (model defaults off); the file turns them on.
- [x] 7. All three agents together stay under 5 req/s at 15 s ticks (evidence from the run).

## Measurements (Sat 2026-10-03, 05:20-05:50, scratch merge, local `bazaar-sim` at 15 s ticks)
taker + maker + duels together, real Jev, local Postgres. Wall time per tick, p50 / p95 / max in seconds; the budget is
`action_budget_s` ≈ 12.5-12.6 s. "Fixed" = the scratch merge + this task's commits.

| Run | per request | Jev | code | taker | maker | duels | dropped | 429 | key: busiest 1 s / mean |
|---|---|---|---|---|---|---|---|---|---|
| run2 (40 ticks) | +100 ms | real | base | 1.12 / 1.43 / 2.14 | 0.65 / 1.07 / 1.44 | 0.60 / 0.69 / 1.06 | 0 | 0 | 11 / 0.68 per s |
| run3 (40) | +100 ms | real | fixed¹ | 0.24 / 0.72 / 4.56² | 0.65 / 1.06 / 3.47 | 0.59 / 0.69 / 1.66 | 0 | 0 | 12 / 0.68 per s |
| run4 (40) | +250 ms | +1 s | base | 3.31 / 3.41 / 3.87 | 1.55 / 3.06 / 4.99 | 2.04 / 2.10 / 2.17 | 0 | 0 | 8 / 0.66 per s |
| run5 (40) | +250 ms | +1 s | fixed | 0.53 / 1.85 / 2.71 | 0.27 / 1.78 / 3.46 | 2.04 / 2.16 / 2.35 | 0 | 0 | 9 / 0.66 per s |
| run6 (30), busy rules³ | +250 ms | +1 s | base | 3.31 / 3.79 / 4.13 | 1.54 / 3.04 / 6.71 | 2.06 / 2.17 / 2.17 | 0 | 0 | 8 / 0.69 per s |
| run7 (30), busy rules³ | +250 ms | +1 s | fixed | 0.53 / 2.33 / 2.58 | 0.27 / 1.78 / 5.13 | 2.05 / 2.11 / 2.11 | 0 | 0 | 9 / 0.69 per s |
| run8 (30), busy rules³ | +250 ms | +1 s | fixed, keyed lane⁴ | 1.06 / 2.84 / 3.26 | 0.53 / 2.04 / 5.61 | 2.08 / 2.20 / 2.22 | 0 | 0 | 9 / 0.69 per s |

¹ run3's maker started before its parallel read was ported (a control). ² tick 0 (start-up: first Jev call 1.7 s, every
stage stalled ~1.4 s at once). ³ no venue bond reserve, spend and price caps raised. ⁴ the shipped version (PR #157
security review): keyed reads (/me, our offers, open threads, dealer threads) one at a time in their own lane beside
the parallel public reads, so a drained key budget never sees a synchronized burst of retries; with the bucket drained
on the simulator's real limiter a snapshot reads ok after 5-6 keyed attempts with and without `parallel_reads`.

The taker asked Jev `spend_pack_slot_now` on every tick for the same state: 40 calls in 40 ticks (base), 12-13 with the
cache. The taker in the simulator stays mostly idle (spend caps, few dealer cards), so the busy case was also measured
with the real `Taker` over fakes that sleep 250 ms per request and 1.3 s per Jev call (3 venues, 3 dealers, 4 board asks):
**6.74 s p50 per tick with the rules off, 2.86 s on** with the keyed lane (2.33 s with every read parallel; max 7.02 /
5.73 s, the tick a cached answer expires), the same 10 writes. At 100 ms / 0.3 s: 2.09 s → 0.97 s.

Not hot (measured): Postgres per tick 14-280 ms in total (p95 per statement 3-6 ms locally), recall 5-60 ms per call,
`/me` from the snapshot (#105) ~110-260 ms (a live read at the injected latency). The duel player is bounded by its own
parallel Jev calls (~1 Jev + 2-3 requests per tick) and is untouched here.

Request budget: all three agents together sent 0.66-0.69 keyed requests per second on average (limit 5) and at most 8-12
in any one second (bucket 20); 0 × 429 in every run. Parallel reads did not raise the busiest second (11 → 12 at 100 ms,
8 → 9 at 250 ms). Worst case by the per-tick limits (12 listings, 6 duel messages, 3 dealer threads, 1 accept, 3 clocks,
~10 reads) is ~36 keyed requests per tick: 2.4 per second at 15 s ticks, under 5; a burst above 20 in one second would get
a `rate_limited` that the SDK retries (0.25 s, 0.5 s).

## Interfaces / data touched
- `src/bazaar_agent/agents/jev_cache.py` (new): `state_key`, `state_tick`, `VerdictCache`.
- `src/bazaar_agent/agents/runtime.py`: `read_together(reads, parallel)`; `read_snapshot(..., parallel, extra)`;
  `Snapshot.extra`.
- `src/bazaar_agent/agents/taker.py`: snapshot + open threads in one batch; boards and dealer threads in one
  batch each; `_ask_jev` through the cache.
- `src/bazaar_agent/pack_gate.py`: `jev_pack_judge(..., cache_ticks)`; `src/bazaar_agent/cli.py` wires it.
- `src/bazaar_agent/guardrails.py` + `GUARDRAILS.md`: `jev_cache_ticks`, `parallel_reads`.
- `scripts/tick_profile.py` (new), `tests/test_speed.py` (new).

## Risks & assumptions
- A cached answer is served even when the tick has too little time left to ask Jev; without the cache that
  state would get `undecided` (no budget). That is the only behaviour change, and it uses an answer Jev gave
  for the same state at most `jev_cache_ticks` ticks earlier.
- Parallel reads raise the instantaneous burst (up to 8 requests at once per agent); the bucket is 20 and the
  measured busiest second stays well under it.
- The simulator answers in ~1 ms; the real game costs ~25-30 ms per request from Madrid and more from
  Railway's europe-west4 (a new TLS connection per SDK request), so runs inject 100 ms and 250 ms per request.
