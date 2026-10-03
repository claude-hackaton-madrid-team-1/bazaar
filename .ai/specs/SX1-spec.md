# SX1 — One sell exception to the last-copy rule: LAT-10  (per-task spec)

- Task id: SX1 (coordinator brief, 2026-10-03 ~20:20)
- Status: 🔵 PR (feat/sell-exception-lat10)
- Backlog source: local (`.ai/specs`). Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Omar's hard rule (`protect_page_sets` = every set) never sells our last copy of a page card. Omar, Sat 3 Oct
~20:20 (translated from Spanish): "La Latina 10 [LAT-10]: if we can sell it for at least 200, and the agent comes
down from there; it would help our cash flow". Our LAT page is 4/10 (x0.5), our value of LAT-10 is 35, and it
completes no page. Lift the rule for that card only, with no new override path.

## Acceptance criteria
- [ ] 1. GUARDRAILS.md rule `protect_page_exceptions` = LAT-10 (comma list of card refs, `none` = no exception),
  dated, quoting Omar; model field default `none`; listed in `ENFORCED_BY`.
- [ ] 2. `Guardrails.protects()` returns False for a listed ref (case and spaces normalised), and for nothing else:
  every other card of LAT and of every other set stays protected.
- [ ] 3. A malformed entry (not `SET-NN`) fails the guardrails load (fail closed).
- [ ] 4. Every other sale rule still binds LAT-10: `sell_min_value_ratio`, `max_score_loss_per_move`,
  `human_approval_above` (tested with the committed rules).
- [ ] 5. The committed-file tests assert every set protected except that one card.
- [ ] 6. The PR body lists every sell path that can now offer LAT-10, and at what price.
- [ ] 7. (coordinator, after the sell-path audit) Entries are REF:MIN (`LAT-10:80`): every selling kind of an
  excepted card (maker ask, bid accept, dealer sell, swap = cash + card received, hand/MCP post) below MIN is refused;
  the maker's quick sale and relist floors never go below MIN. (Replaces round 1's "human approval at any price".)
- [ ] 8. (review of #240) The list takes ASCII entries only; the item must match exactly (any other spelling stays
  protected); an excepted sale whose asset is not a copy of that card in /me is refused.
- [ ] 9. (Omar, Sat 3 Oct ~22:20) LAT-09 added: `LAT-10:80,LAT-09:90`; LAT-09 allowed at 90 or more, refused at 89 on
  every selling kind; the other LAT last copies stay protected.
- Out of scope: the 200 opening (the coordinator's hand post, hands-off for the maker).
