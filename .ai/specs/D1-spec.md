# D1 — Duels: policy engine + delivery-days module for Duels II/III + calibration  (per-task spec)

- Task id: D1 (migrated from GitHub issue(s) #5, #7)
- Priority: P0
- Status: 🔵 the duel player is live on Railway (`duel run --play`, Jev duel_move) since Friday. #150 (Sat 06:50) merged Marius's duel chain: two-issue offers strictly inside our limit (#60 + review fixes), duel policy v2, B11 squeeze mitigations and the days-sign latch, behind GUARDRAILS flags. Omar set `duel_policy` = v2 live with #170 (Sat ~10:00; Jev had stayed undecided at 0.76 on the 0.90 bar); B11 (`duel_endgame_min_share`) and `duel_days_auto` stay OFF. Simulator proof: `docs/night/d1-sim-proof.md` (2,904 duels, 0 outside our limit, v2 1.55x v1 in the Duels II shape). Open: criterion 3 (calibration, #91 merged), #151 / #165 for the 23:00 window, #173 (duel-log surrogate fix), a days flip only on real evidence.
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Win the largest share of every duel pie: never below our limit, accept good offers early (decay), and trade delivery days for price in Duels II/III and the Final.

## Acceptance criteria (each MUST be testable)
- [ ] 1. Duels II: every offer carries `days`; our utility uses `your_days_weight`; tests on the simulator with both roles.
- [ ] 2. An offer inside our limit with a good share is accepted, never left to time out (Friday's evals: 8 log-only duels had the rival inside our limit).
- [ ] 3. Post-duel calibration: each finished duel's share is scored (evals #91) and fed to the learner.

## Source (the original issue text, verbatim)

### #5 — [duels] Policy engine: limit floor, anchor, acceptance rule with decay

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/5 on 2026-10-03 (closed there).

## Context
From the Duels bundle:
- *"Each duel scores a side's share of the pie (0–1)"*
- *"No deal scores 0 for both"*
- *"a side went below its own limit: neither scores (no feeding a friend)"*
- *"about one in six has no zone of agreement"*

The per-round decay is `(1−d)^rounds`, with d = 0.06 / 0.08 / 0.10. Limits are secretly scaled and shifted in each duel, so numbers can't be pre-agreed with allies.

## What to do
Pure policy: `(duel state, session params) → post | accept | wait`.
1. **Hard floor:** never offer or accept beyond our limit.
2. **Anchor at tick 0:** an ambitious but credible first offer.
3. **Acceptance rule [inferred]:** accept if `x/y ≥ (1−d)/(1+d)`, where x is our surplus under their offer and y under our current offer. Threshold ≈ 0.89 / 0.85 / 0.82. In the last 1–2 ticks, accept any surplus > 0.
4. **Geometric concession** towards the midpoint of the two current offers. Closing fast and close to 50/50 is worth more than playing hardball.
5. **No zone of agreement:** if after ~4 rounds their best offer is still on the other side of our limit, stop conceding. No deal, and that's fine.
6. The decision is deterministic (<1 s). An LLM may only draft the `text`, with timeout and fallback.

## Acceptance criteria
- [ ] Tests against simulated rivals (hardliner, splitter, naive accepter, no zone).
- [ ] Never crosses the limit.
- [ ] Average share ≥0.45 against the splitter.
- [ ] Handles 6 concurrent duels with 15 s ticks.

**Comment by serban-marius:**

**Correction (RULES.md):** *"a deal outside it loses you points"*. Closing outside our limit **subtracts points**, it doesn't just give zero. The hard floor is even more critical.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, the floor and the anchor work, but there is no acceptance rule that accounts for decay and no tests against simulated rivals.

- ✅ Done:
  - Hard floor, anchor at 0.6, then a linear concession to our limit ±5 %, plus an endgame accept inside the limit (`src/bazaar_agent/agents/duelist.py:71-109`).
  - The LLM writes only the words, with a timeout and a template fallback.
- ❌ Missing:
  - The acceptance rule `x/y ≥ (1−d)/(1+d)` using `decay_per_round`.
  - Geometric concession (the current one is linear), and a stop when there is no zone of agreement.
  - Tests against the 4 simulated rivals.
  - 6 concurrent duels on 15 s ticks: this fails with `llm_words` on, because the words are generated one duel after another.
- Findings:
  - In two-issue duels our counter can land **outside our limit**: PR #60. A deal outside the limit subtracts points, so this goes first.
  - Our 8 practice deals all closed inside our limit.
- Related PRs:
  - #57 (merged): Jev picks among legal accept / counter / hold moves (`agents/duel_jev.py`). It checks an accept against our limit after the cost of days, but a counter only on price, so the out-of-limit counter is still possible on main.
  - #55 (open): `bazaar-sim` has duels, but only one rival style (it concedes from 0.9 to 0.35 of the pie).

### #7 — [duels] Days module (Duels II/III) + post-duel calibration and rival profiles

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/7 on 2026-10-03 (closed there).

## Context
In Duels II, III and the Final, both price **and delivery days** are negotiated: *"each side has a private primas-per-day weight: trading days for price grows the pie"*. In addition, each pair faces **the same scenario twice with roles swapped**. The bulk of the duel score comes on Sunday (Duels III and Final), with decay 0.1.

## What to do
- **Days module:** utility = price ± own_weight·days. Days should go to the end of the range that favours the side with more weight [inferred]. Estimate the rival's weight from how their offers move (price, days). If we pick the wrong end, **both sides' share shrinks**.
- **Post-duel calibration:** from closed duels (if C5 in #4 confirms the pie is revealed), deduce the pie and the rival's limit, adjust anchors and profile each rival's concession style for the mirror duel.

## Acceptance criteria
- [ ] In simulation with random weights, captured pie reaches ≥90% of `max_pie` at a fair price.
- [ ] Never picks the wrong end of the days range when the weights are known.
- [ ] Per-session report: deal rate, share per role and rounds per deal.

**Comment by serban-marius:**

**Confirmed (RULES.md):**
- Days range from **0 to 10**.
- Own weight comes in `your_days_weight`.
- You must send `{"text","price","days"}` (or all of it inside `"offer"`). A message with a price and no `days` is rejected with `missing_days`.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, none of the days module exists.

- ❌ Missing (all criteria):
  - An estimate of the rival's days weight from how their (price, days) offers move.
  - Post-duel calibration and per-rival profiles for the mirror duel.
  - A per-session report: deal rate, share per role, rounds per deal.
- Findings:
  - Days are hard-coded to 5 (`src/bazaar_agent/agents/duelist.py:100`, still the same on 8bcf0fd). This causes the bug where two-issue counters land outside our limit.
  - PR #60 uses the days with the lowest penalty as a stopgap. It is not the days module.
  - The live payload already exposes `days_meaning` and `your_days_weight`. All 26 duels on record are price-only practice, so we have no days data yet.
- Related PRs:
  - #57 (merged): the Jev question `rival_cares_about_days` (`agents/duel_jev.py`) gives the rival its own days when our price still holds after them, and otherwise days stay 5. It is a yes/no signal, not a weight estimate or calibration.
  - #55 (open, `bazaar-sim`): two-issue duels with a random `your_days_weight` and `rival_days_weight`, which we can use for the "random weights" simulation criterion.
