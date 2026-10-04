# TT1: Improve trading throughput on 15-second ticks

Source: Omar's request to implement the five findings from the live guardrail audit and verify agents can use them. Backlog is local. Audit at tick1679: 491 P; global hourly/amount approval limits already disabled; no evidence that other price or swap budgets need removing.

## Acceptance

1. Incoming team conversations use actual counterparty inactivity, not an unconditional three-tick lifetime when no swap exists. Productive replies retain a bounded opportunity to negotiate.
2. Agents can produce guarded structured cash counteroffers when a swap is unavailable, using existing trade/value/fee/publication paths. Words never authorize a trade; sole-copy, cash, value and shared action limits remain enforced.
3. Existing dealer conversations count toward reserved dealer capacity. Three open dealers do not consume three additional reserved slots; total server and team-thread limits remain unchanged.
4. Dealer opening plans use fresh official value, preserve affordable unknown concessions, and avoid known unaffordable outcomes such as RET09/10 repeatedly walking at50 against49. Extra reads fit the existing deadline and shared request budget.
5. Strategy judgments refresh on meaningful state changes and retry transient failure promptly instead of waiting120ticks. Confidence thresholds remain unchanged. Venue announcements respect the observed20tick server cooldown.
6. Exercise actual agent paths with focused tests, full integrated gate and independent review. Ship through a guarded PR rollout and verify15second tick progress/configuration; distinguish deployed capabilities from unobserved future fills/score gains.

## Execution

Parallel owners: team conversation/cash/reservation logic; taker official-value planning; strategy cache/venue announcements. Coordinator owns docs, integration and rollout. No presentation work, new provider, blanket price-cap relaxation or removal of inventory/value/API protections. Existing max_spend_per_game_hour=0 and human_approval_above=0 are intentional.
