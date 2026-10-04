# Retry loop: picaros RET-09 refused ×99 (Sun 4 Oct)

Dashboard (bazaar-live, 11:03): "Guardrail: official value ceiling 49 cannot reach picaros RET-09 negotiated fills
from 59 … refused ×99, last now · no longer on sale". Evidence: Postgres `decisions` (SELECT only, ticks 1588-1888),
`origin/main` 3fd27881. Fix on `fix/retry-loop` (based on `origin/main`; see "PR #291" below).

## 1. What is retried, and does it reach the game

Only a local `dealer_open` evaluation. Every tick the taker's dealer desk (`Taker._open`) ranked two strategy buys
from Los Pícaros, RET-09 (score 14.2) and RET-10 (13.8), both rare, both at ladder 46→63, and `_official_opening`
refused each one **before any send**:

| card | rows `dealer_open` rejected | distinct ticks | ticks |
|---|---|---|---|
| RET-09 | 105 (incl. 4 ladder-probe plans 27→49) | 101 | 1733-1888 |
| RET-10 | 100 | 100 | 1733-1888 |

- No thread is opened and no message is sent, so no dealer patience, no thread slot and no POST.
- It still costs, every tick: one `GET /api/me/value` per card (the guardrail `check()` reads the official value,
  cached per tick; ~2 reads/tick on the shared key, ~205 in total), two decision rows, and two of the three open
  attempts (`for _ in range(max_dealer_threads)`, 3 on Railway).
- The ladder probe (PR #277) planned RET-09 at picaros (27→49, "official value 49") 8 times (t1722-1878), each after
  a Jev `ladder_probe_worth_it` call, and each was refused the same way. It rests per dealer and game hour in
  memory (`_probed`), so the repeats come after each redeploy (8 deploy ids in the window), not every tick.

## 2. Why there was no memory, and did it starve other candidates

- The existing memories do not cover this refusal. `_learned_skips` (dealer, class) and `_skips` only dedupe the
  rows of `_evolved` / `_unblocked` / `_persona_shaped`; `cooling` and `reopen_at` are only set after a real thread
  walked (`_after_refused_walk`); the learner's blockers (#89/#96/#112) learn from game refusals (HTTP errors) and
  dealer behaviour, never from our own guardrail. A refusal in `_official_opening` just returned `False` ("a refused
  plan permits another bounded try"), so the same move came back next tick.
- Starvation: **partial, not observed as a lost deal**. The two refusals took 2 of the 3 attempts each tick; the
  third went to Abuela, and all 10 Abuela opens in t1733-1888 happened in ticks with RET refusals. Chato, Pilar and
  Banco had no candidate (other-dealers report: nothing inside our caps / they only buy), so nothing visible was
  pushed out. Unseen cost: a third picaros card could never be tried after the two RETs, and the candidates past
  the attempt limit are not logged, so they cannot be counted from the database.
- "No longer on sale" is the dashboard's board view (no team asks RET-09 on any board), not the dealer: Pícaros
  still mints it (15/30). For the taker it means: a card the strategy stops ranking with that dealer.

## 3. The underlying conflict: official value 49 against fills from 59

- RET-09 is an **album** buy (value 65.6 = 70×0.7 + bonus share 16.6, set chased by five teams), not a ladder buy;
  the ladder probe chose the same card for the last L4 slot (L4 picaros 2/3 this round).
- Our official `your_value` is 49 (margin 0 for rares). Pícaros' fills for RET-09 since the round started go from
  59; for a forgiving dealer we take an ask only at or under 55. `dealer_ladder_value_tolerance` (default 0) would
  have to be ≥ 10 to reach 59, and it applies only when the dealer's level has an empty slot.
- Points: a dealer buy never moves `neg_points` (card-hunt, 10:44); it scores only as a ladder slot (last L4 ≈ +0.06
  ladder ≈ +0.4-0.6 board). Paying ~10 over the official value for that is marginal, and the purchase itself loses
  10 P by the official measure. **Recommendation: leave the tolerance at 0**; fill L4's last slot by selling our
  duplicate common to Pícaros (other-dealers, 10:53), which the hand `bazaar dealer sell` can do today.

## 4. "Jev refused 8 swaps (74 % under 75 %)"

- `team_swap_jev_min_confidence` = 0.75 (GUARDRAILS.md:118, validator `ge=0.5`) is the swap gate, but in this window
  the team desk asked Jev once (t1764, 0.34). The 0.74 rows are **ladder-probe** gates: `strategy_gate` rejected
  `ladder_probe_worth_it` at 0.74 four times (t1735, 1739, 1743, 1835), plus 0.72 and 0.67 (t1658, 1670); its bar
  comes from the question's "design" stakes, not the swap parameter. The probe they gate is the RET-09 probe that is
  refused anyway, so lowering any bar would only buy more refused probes. No change recommended (never below 0.5).
- The dashboard labels these as swaps; worth relabelling to "ladder probes".

## PR #291 (feat/card-hunt) against this loop

- It does not fix it: `_ladder_only` keeps dealer buys where the dealer has an empty slot, and picaros L4 is 2/3, so
  RET-09/RET-10 pass and are still refused every tick.
- It drops the Jev gate on the ladder probe (no Jev call), and the probe keeps its hourly rest (`_probed`), so it does
  not make the probe per tick. Net: neutral without this fix; with it, the refused opens stop.
- Overlap: #291 edits `Taker._open` (the `_ladder_only` line after the sort) and `_with_probes`; this fix adds one
  line further down in `_open` and new methods after `_official_opening`. The commit cherry-picks onto
  `origin/feat/card-hunt` bb684318 without conflicts and `tests/test_official_value_agents.py`, `tests/test_card_hunt.py`,
  `tests/test_taker.py` pass there (94). The branch is based on `origin/main` because the card-hunt worktree is
  mid-merge with main; either merge order works.

## The fix (`src/bazaar_agent/agents/taker.py`)

- `Taker._refused_opens`: (dealer, item, ladder) → the refusal's inputs, guardrail text and tick, set in
  `_open_one` when an open is refused before any send (not on the kill switch, not when only a read failed: those
  are retried next tick as before).
- `_without_known_refusals` runs before `openings()`: a remembered move whose inputs are unchanged is dropped, so it
  costs no value read and no open attempt and the next-ranked candidate gets the attempt.
- Inputs (all read without a request): the move's ladder, value and guardrail verdict, this round's fills for that
  dealer and card (a new quote), a lower reopening, the latest `round.started`/`day.opened`, our holdings and cash,
  the guardrails and the strategy params. Any change re-evaluates once.
- Every `REFUSAL_RECHECK_TICKS` (20 ticks = 5 min) it is evaluated again, because an official value can drift
  without any of those inputs changing; the same refusal then writes no new row. Rows: once per change, not ×99.
- A remembered card the strategy no longer ranks with that dealer (no longer on sale, held, another plan) is
  forgotten; if it comes back it is judged afresh once.
- Memory only, per process: a redeploy re-evaluates once.
- Known limits (not changed): the holdings key includes cash and each asset's `your_value`, so every settlement
  re-evaluates the remembered refusals once (a value read and an attempt, no row); a learned `final_max` from
  `run.plans` is not in the inputs, the 20-tick re-check covers it; `activity_stall` names the taker's blocker from
  the newest refusal row, so with one row per change it may show an older row (cosmetic).

Tests (`tests/test_official_value_agents.py`): not retried and not logged again for 20 ticks, then re-read silently;
retried on a new fill; retried on a cap change (new text, one new row); the next-ranked candidate gets the only
attempt; a card no longer ranked is forgotten and judged afresh once. One existing test changed meaning: a new
official value is now seen at the re-check, not the next tick (`test_new_official_value_reconsiders_…`). Five of the
six new/changed tests fail on `origin/main`.

Effect live (from the window): ~2 value reads and 2 rows per tick less (≈ 205 reads and 205 rows over 155 ticks),
and a third dealer candidate can be tried every tick. No deal is expected from it today: the only refused cards are
the two RETs, and the other dealers have no candidate.
