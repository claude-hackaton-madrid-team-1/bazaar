# N19 — Persona model: dealer traits and menus become negotiation params

- Task id: N19
- Status: approved by the coordinator brief (Sat 3 Oct, tick 455)
- Backlog source: local (.ai/specs)
- Traces up to: [`01-spec.md`](./01-spec.md) · Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Read each dealer's published persona (`GET /api/dealers`: traits, menu, `deals_per_team_per_hour`, unlock rules)
every tick the taker trades, and derive the negotiation parameters from it, so a dealer with no price history
(the L4/L5 dealers of Saturday and Sunday) is traded sensibly from its first message.

## Scope / non-goals
- In: `persona_model.py` (pure), the taker's use of it (`agents/persona_desk.py`, `agents/persona_book.py`), the
  sell desk's ranking, terse words, `bazaar dealer personas`, GUARDRAILS `persona_model_enabled`.
- Not: raising any price or cap. The prior only lowers a ladder; `guardrails.check` and the official-value cap
  gate every send as before. No new request: the taker already reads `/api/dealers` every tick.

## Acceptance criteria
1. A pure function maps persona (+ learned curve) to params: opening fraction, step, bids before final, reply
   wait, reopen rule, opening ask never acceptable, tone, cooloff risk, never repeat a price.
2. Learned curves win at 5+ informative fills; traits are the prior otherwise.
3. Replayed on Friday's real threads, the trait prior lands near the learned ladder for Abuela and Chato.
4. The sell side offers a dealer only what its menu buys; a collector's named sets and an official fever rank first.
5. The hourly deal budget skips a dealer; unlock dealers go first.
6. Persona snapshots are stored in Postgres (`traders`) when they change, off the tick.
7. `persona_model_enabled` (default true) turns it all off; caps unchanged.
8. Full gate green and the sim smoke passes on a private port.
