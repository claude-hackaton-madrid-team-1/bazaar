# MM1 build evidence

Scope: autonomous profitable routing and explicit trade destinations in Bazaar Live. Spec: MM1-spec.md. Three parallel slices covered routing, UI/projection, and independent live/code audit.

| Criterion | Status | Evidence |
|---|---|---|
| Current markets and Team10 audited | Verified | Tick1774: v07 owned by t10, open,0fees, empty direct book. T10 Rastro offer22486 sells LAT04 at8. |
| Autonomous net-benefit routing | Verified in tests | `165 passed in2.12s`; new routing regressions cover T10 vs higher gross/lower net Rastro bid, recipient-owned venue exclusion, late price/recipient changes and no extra market reads. Independent review50passed. |
| Cash decision destination recorded | Verified in tests | `20 passed in0.34s`; real JSONL decision retains ref,side,price,venue,counterparty. Public status allowlists unchanged. |
| UI visibility and status distinction | In progress in bazaar-live | Separate Live branch codex/trade-visibility; private projection, exact settlement evidence, no invented buyer. |
| Integrated checks/reviews and deployment | Pending | Focused tests pass; final full gate, PR review, additive Live view migration and rollout remain. |

Unverified: final integration/deployment, future trades and profit. Routing hints do not prove an order will fill. Publishing on every market without demand is not the objective, and assets are never promised twice.

Could-not-do: no demonstrated profitable current order on T10's own empty venue. Capability is tested with controlled offers; no manual trade was placed for the audit.
