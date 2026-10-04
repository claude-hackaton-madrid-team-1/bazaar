# PM1 — Sustained public supply on requested partner markets

Local task. Omar explicitly requested using Team15 and Team18 zero-fee markets for eligible inventory. Existing routing prefers El Rastro's historical activity when no crossing bid is visible, so manual partner listings revert to Rastro after expiry. MR1's demand-only relocation does not address that preference.

Add `preferred_sell_venue_owners` to the existing STRATEGY.md configuration, validated with the existing team-ID parser. Default `none` preserves prior callers; deployed setting is `t15,t18`. Public asks first follow eligible crossing net demand. Otherwise use an open, non-owned, zero-fee preferred venue, distributing new asks deterministically by asset ID across sorted venue IDs. Addressed offers keep their lowest-fee route and exclude the recipient's venue; bids retain existing routing.

Existing public maker-managed asks on nonpreferred venues may migrate to the preferred fallback at their unchanged price, through confirmed cancellation and fresh locked publication guards. Already-preferred asks stay put unless better crossing demand appears. No new requests, manual-offer cancellation, price changes or guardrail relaxation. No claims that placing an offer guarantees a trade or points.

## Honest implementation report

| Criterion | Status | Evidence |
|---|---|---|
| Validated optional config, requested runtime owners | Verified | `test_config_defaults_disabled_and_deployed_owner_ids_are_validated` |
| Crossing-first, zero-fee eligible fallback with stable distribution | Verified | Actor new listings on v15/v28; pure venue/filter/order tests |
| Existing asks migrate once, preferred asks remain sticky | Verified | Two-tick actor, partner appearance, stronger demand migration tests |
| Cancellation uncertainty, commitments, quotas, deadlines stay safe | Verified | Refused/unknown cancellation, fresh promise, no slot/deadline and prior routing regressions |

`177 passed in 1.79s`; Ruff `All checks passed!`; Black `4 files would be left unchanged`; Ruff format `4 files already formatted`; mypy `Success: no issues found in 3 source files`.

Metric: 4/4 local implementation criteria verified. Independent review/full integrated gate/CI/guarded rollout remain release gates.
Unverified: live fills, score increase, availability or demand at deployment time.
Could-not-do: no live trade or deployment performed by this worker.
