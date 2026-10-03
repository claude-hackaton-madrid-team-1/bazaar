# SU1 — Sunday guardrails and bounded duel retries

Source: Omar's Sunday approvals and PR #265 reviews of head 5a27420e. Local backlog.

## Acceptance

1. Uncommon/rare ceilings are 30/105; independent official-value enforcement remains in place.
2. Watchdog dealer-sell breakers expire after 40 game ticks; only fresh bad-sale evidence re-trips them.
3. Only duel messages retry once on a transport failure. The retry uses the original tick's guarded
   deadline, skips when fewer than 1.5 seconds remain, and bounds its SDK timeout to the remaining budget.
   A synchronous deadline timer bounds the whole retry, including hooks and network stages; when the
   timer cannot be safely installed, skip the retry. A retry answered `wait_for_tick` counts as landed; every other retry refusal preserves the original
   ambiguous error. HTTP 4xx/5xx never trigger retries.
4. Duel accepts never retry: the endpoint cannot condition acceptance on the inspected rival offer.
   A lost response keeps the original reservation, even if the rival changes price, days, or offer ID.
5. Request and burst budgets include all message retries. Tests count the real CLI loop's calls and
   check combined Sunday capacity, including the existing stagger's assumptions.
6. Merge origin/main, preserve both memory histories, regenerate README, run the full requested gate
   and private-port simulator smoke on the final commit, push only fix/sunday-guardrails, and attach
   per-criterion evidence to PR #265.

## Limits

Client deadlines are not server-side idempotency guarantees. No live fault injection is authorized.
Existing last-copy exceptions and approval settings are outside this patch; do not certify absolute
last-copy protection. The all-loop ceiling requires the existing stagger, excludes additional operator
traffic, and assumes separate team and broker buckets.
