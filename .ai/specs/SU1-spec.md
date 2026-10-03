# SU1 — Sunday guardrails

Source: Omar's Sunday approvals and the coordinator's final scope for PR #265. Local backlog.

## Acceptance

1. `max_price_uncommon` is 30; independent official-value enforcement remains binding, with its test.
2. `max_price_rare` is 105; independent official-value enforcement remains binding, with its test.
3. Watchdog dealer-sell breakers expire after 40 game ticks; only fresh bad-sale evidence re-trips them.
4. Duel sending, its tests and request budgets match origin/main exactly.
5. Include current origin/main, regenerate docs, pass the requested final-head gates and private-port
   simulator smoke, push only fix/sunday-guardrails, and document exactly the three retained changes
   with per-criterion evidence in PR #265.

## Limits

No live fault injection, game writes, Railway changes or merge to main. Existing last-copy exceptions
and approval settings are outside this patch; do not certify absolute last-copy protection.
