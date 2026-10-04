# TT1 implementation evidence

## Acceptance

| Criterion | Status | Evidence |
|---|---|---|
| Actual team inactivity, bounded messages | Verified in tests | Team slice: `81 passed`; `test_team_desk.py` covers fresh replies and message cap. |
| Guarded structured cash counters | Verified in tests | `test_taker_tick_can_counter_when_no_swap_exists`, `test_cash_counter_drops_tick_expiring_during_reservation`, `test_cash_refund_after_restart_is_once_only_and_not_swap_budget`; team slice `81 passed`, related suites `111 passed`. |
| Dealer reserve counted once | Verified in tests | `test_team_desk.py` covers occupied dealer slots; `Taker._team_view` includes current-tick openings. |
| Fresh official-value opening plans | Verified in tests | Gameplay slice `164 passed in 1.71s`; `test_official_value_agents.py` covers known unreachable fills, alternatives, unknown finals, new valuation, round isolation and deadline expiry. |
| State-aware strategy retry and announcement cadence | Verified in tests | Strategy/venue slice `108 passed in 0.93s`; cross-path integration `167 passed in 1.70s`. Includes real maker cash-grant reconsideration and six announcements over120 ticks without429. |
| Full gate, independent review, guarded deployment and live timing | Pending | Static integration below passed; full scratch gate, CI and rollout pending. |

Current verified acceptance: 5/6 = 83%. No live trade or score improvement is claimed.

Static integration output:

```text
469 files would be left unchanged.
All checks passed!
725 files already formatted
Success: no issues found in 212 source files
```

`uv run bazaar rules`, generated documentation checks and `git diff --check` exited0.

## Unverified

- Final integrated pytest/coverage, independent final-revision reviews and CI.
- Production rollout,15-second progress and future counterparty acceptance of a cash proposal.
- Score impact. Capabilities do not guarantee profitable counterparties or trades.

## Could-not-do

- The separately hosted Claude architecture artifact remains read-only to the available account. Repository architecture HTML was regenerated; owner republishing is still required.

## Review decisions

- Reused existing structured listings, guards, shared publication reservations, message claims and atomic refunds. No schema change or new dependency.
- Cash counters use their own `teamcash:` accounting; the swap-only40P budget does not become a new cash-trading cap.
- Retained120 ticks for ordinary stable strategy refresh, with a bounded4-tick retry on state change or uncertainty. No added state-read request.
- Global hourly spend cap and amount approval remain disabled by the earlier explicit user decision. Sole-copy, value, cash and server limits remain enforced.
- Work split in parallel across team execution, dealer planning and strategy/venue changes. Independent cross-reviews cover each author's changes.

## CI fixture correction

The initial unit run reported `1 failed, 5500 passed` in the pre-existing ladder-tolerance fixture. It set an infeasible value before opening, so the new planner correctly chose a different card. The fixture now lowers value after opening and still checks both allowed and denied mid-thread bids. Reproduced locally: `1 failed, 6 passed`; after correction with official-value regressions: `32 passed in 0.38s`. Production code is unchanged by this correction.
