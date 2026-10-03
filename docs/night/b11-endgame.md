# Night B11: endgame squeeze mitigations for duel policy v2

Draft PR stacked on #86 (`night/w2b-duel-v2`). Offline only. Every new knob defaults to today's behaviour.
Names: our `oracle_squeezer` is W2a's `oracle_squeezer`; our `curve_inferrer` inverts our concession curve, unlike W2a's
least-squares `squeezer`. The gate is scored on W2a's harness first (independent of the policy author) and on ours second.

Reproduce:
- our arena: `uv run python scripts/duel_tournament.py`, `uv run python scripts/duel_b11.py`
- W2a's exploiters: PR #97 (`bazaar_sim.duel_exploit`), with this branch's `duel_arena.single(V2Params(...))`

## The exploit (reproduced)
Marius's concern: "accept anything strictly inside our limit in the last ticks" lets a rival that knows or infers
our limit hold firm and then offer 1 P inside it. Measured at decays 0.08/0.10 (pie share = our surplus / pie × kept):

| rival | v1 (#60) | v2 today |
|---|---|---|
| `oracle_squeezer` (knows our limit), our arena | 0.023 | 0.028 |
| `curve_inferrer` (inverts our concession curve from our offers), our arena | 0.053 | 0.172 |
| W2a least-squares squeezer / oracle / mirror B (same limits) | 0.19 / 0.07 / 0.08 | 0.28 / 0.15 / 0.14 |

**Leak.** v1's offers give our limit within 2 % in 99 % of duels. v2 prices less often, but when it does, it is within
2 % in 88 % of them. W2a: v1 within 6 % by tick 3 in 96 % of duels, v2 in 0 %.

## What the knobs do (v2 only, `GUARDRAILS.md`)
- **`duel_endgame_min_share` (0 = today).** Our pie estimate is the rival's best offer so far, at least 0.4 × our limit.
  A rival offer that leaves us less than this share of it is a squeeze. We refuse it until the last `duel_endgame_ticks`
  and answer with **one** fair offer at D − 2, priced at that share. The rival can still take it at its last move.
- **`duel_endgame_ticks` = 1.** Only the true last tick is "anything > 0". This value already exists; v1 reads it too.
  Under `duel_policy` = v2 it only shapes v2.
  - On its own it changes nothing for v2. v2 has a second last-ticks accept path, `left ≤ duel_accept_margin_ticks + 1`,
    which takes anything inside the limit at D − 2 and D − 1.
  - The share threshold is applied before both paths: a squeeze stops being acceptable until `left ≤ duel_endgame_ticks`.
    So the mitigation is the pair `duel_endgame_min_share` + `duel_endgame_ticks`; W2a's `eg1` preset (ticks 1, share 0)
    measures the same as today.
- **`duel_jitter` (+ `duel_jitter_seed`), 0 = today.** Seeded per-duel noise on anchor, floor and share.

## Trade-off curve (decays 0.08/0.10, n = 200; honest results relative to v2 today)
| setting | W2a squeezer | W2a oracle | W2a oracle, never backs off | W2a mirror B | W2a honest P / deals | our exploiters | our honest P / deals (rival first / we first) |
|---|---|---|---|---|---|---|---|
| today | 0.276 | 0.150 | 0.150 | 0.142 | 1.000 / 1.000 | 0.100 | 1.000 / 1.000 · 1.000 / 1.000 |
| min share 0.2 | – | – | – | – | – | 0.184 | 0.997 / 0.989 · 0.991 / 0.991 |
| **min share 0.3** | **0.374** | **0.263** | 0.134 | **0.258** | 1.007 / 0.995 | **0.226** | 1.005 / 0.975 · 0.996 / 0.979 |
| min share 0.4 | 0.442 | 0.289 | 0.123 | 0.282 | 1.023 / 0.989 | 0.269 | 1.013 / 0.955 · 1.003 / 0.961 |
| min share 0.5 | 0.497 | 0.305 | 0.113 | 0.297 | 1.040 / 0.984 | 0.306 | 1.024 / 0.930 · 1.012 / 0.938 |
| min share 0.6 | 0.555 | 0.314 | 0.105 | 0.306 | 1.059 / 0.977 | 0.332 | 1.030 / 0.899 · 1.022 / 0.913 |

All rows use `duel_endgame_ticks` = 1. Outside-limit closes are 0 in every cell on both harnesses.

Other variants:
- Two fair offers instead of one cost a round each, e.g. min share 0.5: W2a squeezer 0.340 instead of 0.497.
- `duel_endgame_ticks` = 2 makes the share useless, because the squeeze lands inside the "anything > 0" window.
- 0 (no last-tick exception) loses deals (W2a squeezer deals 0.46, honest deals 0.969).
- Jitter 0.25 cuts the leak (limit within 2 %: 89 % → 29 % of duels) but does not raise our share against these
  exploiters (min share 0.4: W2a squeezer 0.442 → 0.425).

## Verdict and recommendation
- **Recommended: `duel_endgame_min_share` = 0.3 with `duel_endgame_ticks` = 1, jitter 0.**
- It passes the gate on both harnesses with margin:
  - Pie share vs exploiters: 0.100 → 0.226 on ours (3 seeds: 0.224–0.230), 0.193 → 0.298 on W2a's three exploiters
    (W2a's final tables, PR #97 @ a98640c, 3 seeds, sd ≤ 0.007).
  - Honest zoo: result ≥ 0.996× v2, deals ≥ 0.975× v2 (bars 0.97 / 0.95).
  - 0 outside-limit closes.
- Min share 0.4 gains more (W2a squeezer 0.442) and still passes, but our arena's honest deals fall to 0.955, at the bar.
  0.5 and above fail our deal-rate bar.

**W2a's final tables** (PR #97 @ a98640c, presets at 14786a7, 3 seeds, n = 200, decays 0.08/0.10). Exploiters = mean of
squeezer, oracle and mirror B with shared limits.

| preset | exploiters | stubborn oracle | honest P | honest deals |
|---|---|---|---|---|
| today | 0.193 | 0.154 | — | — |
| eg1_share02 | 0.260 | 0.148 | 0.996× | 0.999× |
| **eg1_share03** | **0.298** | 0.141 | 1.007× | 0.994× |
| eg1_share03_jitter025 | 0.297 | 0.142 | 1.006× | 0.994× |
| eg1_share05 | 0.365 | 0.122 | 1.037× | 0.982× |
| eg0_share03_jitter025 | 0.260 | 0.128 | 1.000× | 0.966× |

Break-even against stubborn exploiters (W2a): share 0.3 pays unless more than 89 % of exploiters never back off; share 0.5
pays unless more than 84 % do. Share 0.5 scores higher on W2a's exploiters, but on our arena its honest deal rate (0.930)
fails the 0.95 bar. So 0.3 is the conservative point; Marius can take 0.4–0.5 if he trusts W2a's honest zoo over ours.

## Risks
- **A bet that exploiters give in when refused.** Against an oracle that never backs off (1 P to the very end), every setting
  is worse than today: 0.150 → 0.134 at 0.3. We still close, at 1 P on the last tick, but after an extra round. Nothing at
  the last tick can beat a rival that knows our limit and never gives in; only not revealing the limit can.
- **The mirror threat is weaker than feared.** The practice pairs share the item, not the numbers. In duels 85 and 273 the
  rival paid 138 and 171, above the buyer limit we held in the paired duels (126, 148). Paired duels also run at the
  same time, so a "learn in A, squeeze in B" learner cannot be sequential.
- All exploiters are models (ours and W2a's), and the settings were chosen on them (in-sample).

## What Marius must decide
1. Set `duel_endgame_min_share` = 0.3 and `duel_endgame_ticks` = 1 with v2, or go to 0.4 for more protection at ~4 % fewer
   honest deals?
2. Leave jitter at 0 (no measured benefit here).
