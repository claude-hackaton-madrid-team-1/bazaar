# W2a: the duel rival zoo and the replay harness

Night shift of 3–4 Oct 2026. Branch `night/w2a-duel-zoo`, draft PR #80, stacked on #55 (`ogarciarevett/feat-bazaar-sim`).
Refs #5, #7. Every number below is in [w2a-duel-zoo-tables.md](w2a-duel-zoo-tables.md), except where marked (n = 200; v1 = PR #60's `duel_move`,
v2 = W2b's `duel_v2` @ 1d7cc26: `plan_moves` where duels share accepts, `single_duel_move` elsewhere). Offline only: nothing touched the live game.

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
| Noise | sd of mean P/duel over 5 seeds: 0.07 (v1) to 0.14 (v2); sd of the v2/v1 lift 0.005 |
| Gates | 862 tests pass; ruff, black, mypy clean |

## What the zoo says

| Policy | P/duel, 16,800 price-only duels | deal rate | rounds/deal | practice-mix P/duel (bracket) | replay P (12 duels) |
|---|---|---|---|---|---|
| v1 (#60) | 14.35 | 0.829 | 6.85 | 13.6 – 14.5 | 121.7 |
| **v2 (W2b)** | **22.06** | 0.834 | 1.23 | **16.7 – 21.3** | **173.5** |
| endgame accept (silent) | 21.93 | 0.739 | 0 | 14.1 – 20.9 | 185.0 |
| accept first inside | 11.22 | 0.739 | 0 | 10.2 – 11.3 | 85.0 |

1. **Talking is what decays the result.** v1 averages 6.85 rounds per deal (0.94^6.85 keeps 65 %). The real practice shows the same: we scored 0 P on the 12 duels we never answered, and the oracle on them is 195 P.
2. **Pure silence is not enough.** Silent endgame-accept fails 3 of 5 checks. Against tit-for-tat its deal rate is 0.49 vs v1's 0.99, and against listening one-shots it is 0.76 vs v1's 0.87. v2 talks about once per deal and keeps both deal rates (0.999 and 0.895).
3. **Two-issue duels.** This branch's v1 (`days=5`) closes outside our limit in 70 of 2,800 two-issue duels: 23 signed, 47 worst case (not in the tables; `scripts/duel_zoo.py --policy v1` without `PYTHONPATH` runs this branch's agent). PR #60's v1 closes 0, and so does v2 in 14,400 duels. **#60 should land.**
4. **Independent gate of W2b's v2 @ 1d7cc26: GO at every decay pair.**
   - Lift 1.420 at 0.06/0.08, 1.550 at 0.08/0.10, 1.483 at 0.06/0.10. Seeds 1–5 at 0.06/0.08 read 1.427–1.439 (sd 0.005).
   - The other 4 checks pass in every pair. Silent endgame-accept fails 3 of 5.
   - Two earlier v2 commits sat on or under the bar at 0.06/0.08 (8116b2c: 1.388, seeds 1.394–1.408). The current head added free descending offers to quiet rivals and fixed the one-shot gap: 24.4 vs v1's 23.7 P/duel, where it was 21.9.
5. **Robust to the zoo's assumptions, less so to tick order.**
   - Pinning any one assumption gives a v2/v1 lift of 1.29–1.44 (9 rows in the tables), including conceders and holdouts that never accept (1.44).
   - If we move before the rival in a tick, v2 drops 12 % (20.89 → 18.46 P) vs v1's 6 %, so its lift at 0.06/0.08 falls to 1.34. v2's replay drops from 173.5 to 152.8 P; W2b acted on this finding, and it was at 140.1 before.

6. **One accept per tick binds when duels share a deadline.** RULES.md allows one accept per tick per team, and our GUARDRAILS `max_accepts_per_tick` = 1 applies it to duels. In the practice, 6 of our duels ended at tick 132.
   - `play_batch` runs a team's duels in lockstep and refuses an accept past the budget.
   - Silent endgame-accept collapses: in batches of 6, 19.2 → 9.5 P/duel; on the replay of the 12 duels on one clock, 185 → 140 P.
   - v1 is unaffected: it accepts when its target is met, which spreads its accepts over the duel.
   - v2's planner (`plan_moves`) queues its accepts early, so the cap costs it nothing. But planning for a shared deadline costs v2 about 4 % against isolated duels: 19.92 → 19.15 P/duel in batches of 6 at decay 0.06.
   - Lift in batches of 6: 1.27 at 0.06, 1.39 at 0.08, 1.53 at 0.10, or 1.45 for the 0.08/0.10 pair.
   - Replay with the cap: v2 178.4 P, the same as W2b's own arena (176.4 on its earlier commit), vs v1 121.7.

## Caveats (stated, not fixed)

- **Ambiguous labels.**
  - A one-shot that posted once and waited (95, 119, 120) looks exactly like a tit-for-tat rival against silence.
  - A rival that conceded while we countered every tick looks like a time-based one.
  - The mix bracket covers both readings.
  - Duel 267 (accepted our first offer) is folded into tit-for-tat.
- **The practice mix is pessimistic in absolute terms.** 4 of its 6 `no_show` labels are live duels truncated at tick 159, from an unscored round. That weight lowers every policy's P/duel equally and leaves the lifts unchanged.
- **The zoo's deal rates are higher than the practice's.** The public feed (local capture, ticks 120–149) shows 65 deals out of 141 closed practice duels (46 %), 40 of the no-deals at the first deadline, vs 0.72 (practice mix) to 0.83 (flat grid) for v1 in the zoo. The practice count includes absent teams and our own 12 silent no-deals, and it did not score. Absolute P/duel is likely optimistic; lifts between policies are less sensitive to it (a deaf-rival row is in the sensitivity table).
- **Unverified on the real API:** the outside-limit penalty (the result is kept negative and flagged), and whether a standing offer stays acceptable after the rival goes silent. The replay's 185 P relies on the latter for 131/132/147/148.
- **Live sim change:** its `rounds` now follows the real rule, `min` with `decay^rounds` (it was our priced messages only, with `^(rounds−1)`). This changes #55's points. It is the `rounds()` helper and one exponent in `duels.py` (commit 63c83bb, together with the zoo hook), easy to revert on its own.

## What Marius must decide

1. **Whether v2 ships as the default.** It is GO on all five checks at every decay pair here, and it survives the shared accept budget (lift 1.45 at 0.08/0.10 with 6 duels per deadline). The open risk is the within-tick order (lift 1.34 if we move first), which the morning probe below settles. Any one-duel-at-a-time policy that waits for the endgame (like silent endgame-accept) must not ship: the accept cap halves it.
2. **Whether to keep the live-sim rounds/decay fix** in #55 (verified on all 26 payloads).
3. **A morning probe**, in the next real duel session: does a silent rival's standing offer stay acceptable, and is the within-tick order the sim's?

Follow-ups, not done tonight to avoid w1a's files:
- Move `SIM_DUEL_STYLES`/`SIM_DUEL_DECAY` into `SimConfig`, and store the drawn style on `Duel`. It is redrawn per tick from (seed, duel id, pool) today, so changing the pool mid-session switches rivals.
- Route the native sim bot through `_zoo_turn`; parity is test-guarded today.
- Play the two-issue grid once for both days truths.
- `duel_replay.FIXTURE` points from `src/` into `tests/fixtures/`.
- Add a README line for the new env vars.
- `/api/schedule` still advertises `duels.DECAY` (0.06) when `SIM_DUEL_DECAY` is set. The fix is one line in `views.py`, next to w1a's edit there.
- The live sim limits accepts per duel, not per team. The zoo's `play_batch` applies RULES.md's one accept per tick, and the go/no-go checks score each duel alone (see finding 6).
