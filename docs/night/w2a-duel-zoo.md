# W2a: the duel rival zoo and the replay harness

Night shift of 3–4 Oct 2026. Branch `night/w2a-duel-zoo`, draft PR #80, stacked on #55 (`ogarciarevett/feat-bazaar-sim`).
Refs #5, #7. Every number below comes from [w2a-duel-zoo-tables.md](w2a-duel-zoo-tables.md) (n = 200; v1 = PR #60's `duel_move`,
v2 = W2b's `single_duel_move` @ 8116b2c). Offline only: nothing touched the live game.

## What it is

```python
from bazaar_sim import duel_gate, duel_replay, duel_zoo
policy(duel: dict, tick: int, started_tick: int) -> move  # duelist.duel_move's signature; .kind/.price/.days
duel_zoo.run(policy, duel_zoo.scenarios(styles, n, roles, decays, duel_ticks, two_issues, days_truth))  # -> Records
duel_replay.replay_all(policy, counterfactual="conservative")  # the 12 practice duels we never answered
duel_gate.go_no_go(candidate, baseline, n=200, decays=(0.06, 0.08))  # the plan's 5 W2b checks, value vs threshold
```

- **7 rival styles** (`duel_zoo.py`): the plan's six plus `holdout`, seen in duels 273/274, which jumps close and then restates one price. Every style except the `sim` replica sees only its own limit, so no rival ever offers or accepts outside its limit (tested).
- **Scenarios**: role, decay 0.06/0.08/0.10, 12 or 16 ticks, price-only or two-issue with signed weights. Our days are valued `signed` or `worst`.
- **Payload**: the real `GET /api/duels` row's exact key set.
- **Live simulator**: `SIM_DUEL_STYLES=linear,holdout,...` and `SIM_DUEL_DECAY` give the running bazaar-sim zoo rivals. Unset, every duel faces today's bot. A bad value logs one warning and keeps the default rather than breaking the tick.
- **Report**: `scripts/duel_zoo.py` renders every table for any policy (`label=module:attr`).

## Harness verdict: GO, fit for W2b

| Check | Result |
|---|---|
| Real scoring rule | `rounds = min(our priced msgs, rival's)` on **26/26** payloads; `result = surplus × 0.94^rounds` on **8/8** deals (±0.05) |
| Sim parity | the `sim` style replays `duels.py`'s bot message for message; zoo rivals in the live sim = offline engine (holdout, tit-for-tat, one-shot) |
| Oracle cross-check | best rival offer accepted at once on the 12 unanswered duels = **195 P**, the plan's figure |
| Classifier on its own styles, silent | linear 200/200, convex 198/200, one_shot 200/200, no_show 200/200; holdout vs v1 147/200 |
| Realism (median, zoo vs real) | final gap toward us: linear 0.38 vs 0.39, one-shot 0.18 vs 0.11, tit-for-tat 0.25 vs 0.22, holdout 0.34 vs 0.36 (fraction of our limit) |
| Noise | sd of mean P/duel over 5 seeds: 0.07 (v1) to 0.15 (v2) |
| Gates | 851 tests pass; ruff, black, mypy clean |

## What the zoo says

| Policy | P/duel, 16,800 price-only duels | deal rate | rounds/deal | practice-mix P/duel (bracket) | replay P (12 duels) |
|---|---|---|---|---|---|
| v1 (#60) | 14.35 | 0.829 | 6.85 | 13.6 – 14.5 | 121.7 |
| **v2 (W2b)** | **21.56** | 0.834 | 1.16 | **16.2 – 20.5** | **173.5** |
| endgame accept (silent) | 21.93 | 0.739 | 0 | 14.1 – 20.9 | 185.0 |
| accept first inside | 11.22 | 0.739 | 0 | 10.2 – 11.3 | 85.0 |

1. **Talking is what decays the result.** v1 averages 6.85 rounds per deal (0.94^6.85 keeps 65 %). The real practice shows the same: we scored 0 P on the 12 duels we never answered, and the oracle on them is 195 P.
2. **Pure silence is not enough.** Silent endgame-accept fails 3 of 5 checks. Against tit-for-tat its deal rate is 0.49 vs v1's 0.99, and against listening one-shots it is 0.76 vs v1's 0.87. v2 talks about once per deal and keeps both deal rates (0.999 and 0.895).
3. **Two-issue duels.** This branch's v1 (`days=5`) closes outside our limit in 70 of 2,800 two-issue duels (23 signed, 47 worst case). PR #60's v1 closes 0, and so does v2 in 14,400 duels. **#60 should land.**
4. **Independent gate of W2b's v2: GO at decays 0.08/0.10 (lift 1.518) and 0.06/0.10 (1.449). NO-GO at 0.06/0.08 (1.388 vs 1.4).** The other 4 checks pass in every pair. W2b reproduces 1.388 / 1.399 / 1.403 over 3 seeds at 0.06/0.08.
5. **Robust to the zoo's assumptions.** Pinning any one assumption gives a v2/v1 lift of 1.27–1.43 (8 rows in the tables). Within-tick order also matters: if we move before the rival, v2 drops 13 % (20.42 → 17.75 P) vs v1's 6 %, and v2's replay drops from 173.5 to 152.8 P. W2b already acted on this finding; the replay was at 140.1 before.

## Caveats (stated, not fixed)

- **Ambiguous labels.**
  - A one-shot that posted once and waited (95, 119, 120) looks exactly like a tit-for-tat rival against silence.
  - A rival that conceded while we countered every tick looks like a time-based one.
  - The mix bracket covers both readings.
  - Duel 267 (accepted our first offer) is folded into tit-for-tat.
- **The practice mix is pessimistic in absolute terms.** 4 of its 6 `no_show` labels are live duels truncated at tick 159, from an unscored round. That weight lowers every policy's P/duel equally and leaves the lifts unchanged.
- **Unverified on the real API:** the outside-limit penalty (the result is kept negative and flagged), and whether a standing offer stays acceptable after the rival goes silent. The replay's 185 P relies on the latter for 131/132/147/148.
- **Live sim change:** its `rounds` now follows the real rule, `min` with `decay^rounds` (it was our priced messages only, with `^(rounds−1)`). This changes #55's points. It is the `rounds()` helper and one exponent in `duels.py` (commit 63c83bb, together with the zoo hook), easy to revert on its own.

## What Marius must decide

1. **Which decays the W2b gate uses.** Fact 1 of the plan puts the remaining scored sessions at 8 % (Duels II) and 10 % (Sunday). That makes v2 a GO (1.518). The flat 0.06/0.08 grid puts it 0.012 under the bar.
2. **Whether to keep the live-sim rounds/decay fix** in #55 (verified on all 26 payloads).
3. **A morning probe**, in the next real duel session: does a silent rival's standing offer stay acceptable, and is the within-tick order the sim's?

Follow-ups, not done tonight to avoid w1a's files:
- Move `SIM_DUEL_STYLES`/`SIM_DUEL_DECAY` into `SimConfig`, and store the drawn style on `Duel`. It is redrawn per tick from (seed, duel id, pool) today, so changing the pool mid-session switches rivals.
- Route the native sim bot through `_zoo_turn`; parity is test-guarded today.
- Play the two-issue grid once for both days truths.
- `duel_replay.FIXTURE` points from `src/` into `tests/fixtures/`.
- Add a README line for the new env vars.
