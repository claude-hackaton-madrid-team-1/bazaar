# SALES2 — Convert market supply into targeted team opportunities

Local backlog; Omar's 2026-10-04 request: Sales must use known rival needs to make real offers in markets and bring buyers to existing supply. Holding inventory and sending messages alone do not score.

## Plan and acceptance

1. Prioritize the existing guarded structured sale path for uncommitted inventory. Route targeted offers to an open market neither participant owns, preferring configured alliance venues when their acceptance fee is no higher. Reuse Maker's venue selector and shared publication reservations; never duplicate a promised copy.
2. When no structured outreach is sent, match fresh scanner needs to public asks: other teams' quotes on our open market first, then our existing quotes on configured partner markets. Read the own market's current public book, not only a finite event replay; revalidate the exact partner quote before promotion.
3. Send at most one new text-only introduction per tick, with the exact offer, card, price and venue. Preserve total/team thread caps, dealer reserve, PAUSE and deadlines. Durable offer/recipient hourly cooldown and unresolved-open claims prevent restart spam. Persist only acknowledged message IDs; words neither reserve inventory nor accept a trade.
4. Prove structured-offer priority, real committed-context partner promotion, changed/expired/private quote refusal, remaining inventory/floor protection and failure/cooldown behavior with fake-only actor tests. Independent reviews and the coordinator's full gate precede rollout.

## Honest implementation report

- Verified structured-offer priority and eligible venue propagation: `tests/test_sales_promotion.py::test_sales_prioritizes_real_offer_over_public_introduction` and `tests/test_sales_outreach.py::test_structured_sale_uses_eligible_alliance_venue_and_records_actual_route`.
- Verified fresh quote selection, exact committed-context handling and text-only writes: `tests/test_sales_promotion.py`.
- Verified shared unknown-open suppression, cooldown, PAUSE, expiry and ACK persistence: promotion/outreach/ThreadStore regressions.
- Focused output: `163 passed in 4.04s` across promotion, Sales, outreach, TeamDesk, team cash and thread persistence. Static output: `Success: no issues found in 4 source files`, Ruff `All checks passed!`, Black `7 files would be left unchanged.`

Implementation metric: 4/4 criteria verified by source and focused tests (100%). Unverified: production conversion, score change, full integration gate and deployment; those belong to coordinator review/rollout. Could-not-do: no live trading or infrastructure changes were attempted. Text promotion is not a structured offer or a scored trade; only subsequent accepted structured transactions can create value.
