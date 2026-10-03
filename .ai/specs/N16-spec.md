# N16 — Strategic bluffing: tactics in the words, learned per counterparty  (per-task spec)

- Task id: N16 (local; indexed in [`02-plan.md`](./02-plan.md), P1, next to N14)
- Status: **approved by the coordinator brief (2026-10-03 04:20, Omar: "our agent must be capable of lying
  if necessary to win the card and the points, ALWAYS")**; Jev `bluff_policy` = `learned_per_counterparty` (0.90)
- Backlog source: local (`.ai/specs`); the coordinator's brief is the source text
- Traces up to: [`01-spec.md`](./01-spec.md) §9 (untrusted text), [`N3-spec.md`](./N3-spec.md) (lessons store);
  stacked on PR #96 (`ogarciarevett/feat-learner-auto-evolve`)

## Goal
Our agents may lie IN WORDS whenever it can win the card and the points. A deterministic tactic bank writes
the text of each message (budget cap, outside option, low need, walk threat, fake demand, cost floor; kindness
for Abuela), a per-counterparty bandit picks the tactic with the best learned value, and every tactic's outcome
becomes a `tactic` lesson that can switch that tactic off for that counterparty for the rest of the day.

## The rules that bound it (vendor/bazaar-kit/RULES.md, re-read 2026-10-03)
- "Words persuade, structure binds. Your agent may say anything." → the lie lives ONLY in the text. The
  structured `price` / `days` / offer of every message stay exactly what `decide()`, `duel_move()`, Jev and
  `guardrails.check()` decide today. A tactic never changes, raises or invents a structured field, never skips
  a guardrail check and never touches an accept.
- "Dealers remember how they were treated; some stop dealing with you for a while if you try to trick them"
  (`closed_reason: cooloff` + `until_tick`, `persona.strike`) → a strong negative lesson for that dealer.
- "Abuela likes kindness" → Abuela only ever gets kindness tactics (true thanks, flattery, patience).
- "A correct flag scores for the flagger" → a `flag.raised` on one of our tactic messages is a strong negative
  lesson. We never lie in OUR flags (`allow_flags` stays false; this task sends no flag).
- Never impersonate the organisers, a dealer or another team; never claim a rule that does not exist. Prompt
  injection is out of scope (it never changes dealer prices).

## Scope
1. `agents/tactics.py` — the tactic bank (pure). Ids: `budget_cap`, `outside_option`, `low_need`,
   `walk_threat`, `fake_demand` (sell), `cost_floor` (sell), and `kind_gratitude`, `kind_flattery`,
   `kind_patience` (Abuela). Spanish and English templates (`WordsRequest.language`); templates hold NO digit:
   every number comes from the structured price (`{p}`) or a number invented from it (`{alt}`), never from
   our limit, `your_value` or a multiplier; an invented number that collides with a private number is moved.
   The counterparty's text is never echoed.
2. `agents/bluff.py` — the chooser and its learning (`TacticBook`): per counterparty (dealer id, duel rival
   alias, team), default ON, picks the eligible tactic with the best UCB1 score over its tactic lessons
   (deterministic: ties broken by a seeded hash of counterparty, conversation, step). Rewards per message:
   counterparty price moved toward us +1, held 0, moved away −0.5; deal +1 (+0.5 within 3 of our messages:
   rounds saved); counterparty walked −1; cooloff, strike or a flag on our message −10 and that tactic is off
   for that counterparty for the rest of the game day (`clock.round`); two such penalties turn every bluff off
   for that counterparty for the day; no gain (≥ 3 uses, mean ≤ 0) turns that tactic off for the day.
3. Kill switches: `BAZAAR_BLUFF=0` (env, declared `preserve()` on the services that write words) and
   `bluff_enabled` in GUARDRAILS.md (validated by `uv run bazaar rules`). Either off → every message uses
   today's template words.
4. Learning in the N3 store: each tactic outcome is a `Learning(kind="tactic", source="outcome", team=us)`
   written through `LearningStore.record()` after the tick's sends, and read back at start and every 5 ticks
   after the sends (another process's lessons count too). Each process's book has its OWN `LearningStore`
   (own memory and connection), so tactic rows never trim the feed reader's blockers out of the LiveLearner's
   memory. N3's recall defaults to lesson/behaviour/policy, so tactic rows never reach Jev or the words context.
5. Wiring: the taker's dealer bids, `bazaar dealer buy`, and `bazaar duel run --play` offers. Each sent message
   records the tactic id and counterparty in its decision row under private input keys (the public status view
   is an allow-list and does not list them). The accept logic is untouched: an accept that is already good is
   sent before (and instead of) any bluff.

## Scope addition (coordinator, 2026-10-03, Omar: a negotiation-psychology skill for the runtime agents)
Jev `psychology_where` = `words_in_n16` (0.88). Two MIT skills from `wondelai/skills`
(commit `c172996495bed0fcd26896a9416b2093fd7073f0`, files byte-identical) are vendored in
`.ai/skills/negotiation/` (Voss) and `.ai/skills/influence-psychology/` (Cialdini), each with its LICENSE
and a SOURCE.md. Their ideas join the tactic bank as `psychology` tactics the bandit learns like the bluffs:
`empathy_label` (labeling), `calibrated_question` ("How am I supposed to pay 31 for one card?"),
`accusation_audit` (first message only), `no_question` ("Would it be unreasonable to meet at 19?"),
`reciprocity` (thanks and a promised return visit), `mirror`; and two more bluffs: `scarcity`,
`social_proof`. Mirroring echoes ONE safe token: the counterparty's own structured price, never their words
(the renderer cannot see them). Abuela's allow-list: kindness, `empathy_label`, `calibrated_question`.
Ackerman's 65/85/95/100 schedule and precise non-round numbers change prices: proposed for N14 in
`98-nice-to-haves.md`, not built here.

## Non-goals
No LLM in the executor path (`llm_words` stays false; with it on, a chosen tactic's text wins over the LLM's).
No change to prices, days, accept rules, Jev questions or guardrail caps. No flags sent. No prompt injection.
No team-to-team thread wiring (we run none today; the bank supports `team` counterparties for later).

## Acceptance criteria
1. The structured fields (price, days, kind, offer id) of every move are identical with and without a tactic
   (property test over generated dealer and duel moves).
2. Abuela gets only her allow-list: kindness, labeling and calibrated questions (any state, any history).
3. A cooloff (thread `closed_reason: cooloff`, `persona.cooloff` or `persona.strike` for us) after a tactic
   disables that tactic for that dealer for the rest of the day; a flag on one of our tactic messages does the
   same for that counterparty.
4. The kill switches: `BAZAAR_BLUFF=0` or `bluff_enabled = false` → no tactic, today's words; `bazaar rules`
   validates the new line; `BAZAAR_BLUFF` is declared `preserve()` (IaC allow-list test).
5. Language consistency: every message is in exactly one language (the request's).
6. No secret, limit or private value in the text: our limit, max price, `your_value` and multipliers never
   appear (property test); the counterparty's text never appears.
7. An accept beats a bluff: a duel or dealer move that accepts is unchanged by the bluff layer and sends no text.
8. The chooser is deterministic for a given seed and history, and picks the best learned tactic.
9. Tactic id and reason stay private: never in `/state`, `/events`, `/health`.
10. Simulator run (`BAZAAR_SIM=local`) with tactics on: transcript lines pasted.
11. Gate green: black, ruff, mypy, pytest (≥ 80 % on the new modules).
12. Scope addition: both skills vendored verbatim with LICENSE + attribution; every psychology tactic exists in
    both languages; mirroring never echoes more than the safe token; the accusation audit opens only.

## Interfaces / data touched
- New: `src/bazaar_agent/agents/tactics.py`, `src/bazaar_agent/agents/bluff.py`, `tests/test_tactics.py`,
  `tests/test_bluff.py`.
- `learn/model.py`: `Kind` gains `"tactic"` (no schema change: `learnings.kind` is free text).
- `guardrails.py` + `GUARDRAILS.md`: `bluff_enabled` (enforced by `agents.bluff.enabled`).
- `agents/taker.py`, `agents/dealer.py`, `cli.py` (dealer buy, duel run, agent taker): pass a `TacticBook`.
- `.railway/railway.py`: `BAZAAR_BLUFF: preserve()` on bazaar-taker, bazaar-maker (shared `agent()` env)
  and bazaar-duels; `tests/test_railway_iac.py` allow-list.

## Risks & assumptions
- Real dealers' prices "come from their own rules": words may not move them at all; then every bluff earns ~0
  and the no-gain rule turns it off (that is the learner working, not a bug). Duel rivals are LLM agents and
  are the likelier place where lying pays.
- A trick-sensitive dealer (Chato?) may cool us off: the penalty is immediate and per dealer, and two penalties
  stop all bluffs to that dealer for the day. A cooloff costs ticks of that dealer: accepted risk (Omar's call).
- "Next counterparty move" is read at our next observation of the conversation (the next tick): a reply that
  lands later is scored as a hold.
- `flag.raised` payload shape is unverified (`message` id assumed, as the simulator emits); no flag ever seen
  against us yet.
