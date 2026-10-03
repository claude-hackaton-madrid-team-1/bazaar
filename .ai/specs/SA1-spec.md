# SA1 — Sentinel autonomy: the Workshop, dealer sells on news, levels to agents

**Source:** local backlog (`.ai/specs/02-plan.md`). Omar, Sat 3 Oct ~19:15 Madrid: "Keep in mind Don Ernesto, the
taller and more things that the sentinel MUST be taking the news alone and preparing alone the agents to do it
without me saying it." And: "Making more money between trades don't really give us more points. YOU MUST FOCUS ON
THE scoring." And: "not to sell the album, [sell] each repeated [card] on our main collection".

## Why
- RULES.md scoring: the dealer ladder scores the share of each dealer's range we capture, our best three deals per
  level, a missing one counts 0, higher levels weigh more; a dealer SELL is a ladder deal too. Cash scores nothing.
- `/api/levels` (keyless, 19:10): The Workshop (`taller`) active since game hour 7.2; Don Ernesto (`banco`, L5,
  kind banker: buys only epics and legendaries) active since 9.4, open to all at 10.4. Nothing in our agents acted on
  either: a human had to say it.

## What
1. **The Workshop.** `POST /api/taller {"assets": [a, b, c]}` (the level's own `how`; the route is not in
   `docs/api/openapi.json`): three spare copies of one rarity become one card of the next; the pull is luck, never
   scored. `agents/taller.py` ranks triples of FREE spares (held − copies in our open offers or a recent sell − one
   kept per card): a pull that may fill a missing page slot first, then the highest dealer level that buys the
   result, then the cheapest to give up; never a triple worth more to us than the result's book. `guardrails.check`
   action `taller`: `taller_enabled` (false), `max_taller_per_game_hour` (2, this process), one free copy of each
   card kept (any set), the kill switch, and `max_score_loss_per_move` for copies a team trade brought us. The taker
   crafts at most one triple per tick once the level watch shows the level active; `bazaar taller [a b c] [--live]`.
2. **Dealer sells on news.** `dealer_sell_enabled` stays false. The maker's sell desk is made safe to turn on:
   candidates ranked by ladder scoring (a level with < 3 scored deals today first, then the highest level), a
   trickster's FINAL read as an ordinary bid, thread offers counted as busy copies, a copy that stops being a spare
   walks, a kill switch raised mid-step holds. Each property has a test (`tests/test_dealer_sell_readiness.py`).
3. **Sentinel → agents.** `level_watch.LevelWatch` diffs the `/api/levels` answer the news sentinel already reads
   once per window (no new request) and writes one learnings row (kind `announcement`) per level going active or
   open to all, naming the agent behaviour it enables. The taker asks `news.levels.active("taller")` each tick.

## Acceptance
- A1: no craft while `taller_enabled` is false, the level is not active, or the kill switch is on; one craft per
  tick at most, at most `max_taller_per_game_hour`; never a card's last free copy (tests/test_taller.py).
- A2: the readiness properties of the sell desk hold with the committed GUARDRAILS.md (tests/test_dealer_sell_readiness.py).
- A3: a level going active / open to all writes one learnings row, once (tests/test_level_watch.py).
- Gate: full `uv run pytest` (DATABASE_URL unset), ruff, ruff format, black, mypy, `scripts/sim_smoke.py`.
