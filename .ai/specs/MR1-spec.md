# MR1 — Move standing asks toward crossing demand

User request: sell eligible inventory through profitable markets, including partner markets, instead of accumulating cards. Holding inventory does not score. No market is forced merely because its owner is a partner.

The maker already routes new asks from public feed bids. Its offer planner only revisited standing asks when price changed, leaving a copy committed on the old venue when another venue gained crossing demand. The taker correctly could not sell that committed copy again.

Reuse confirmed cancellation and guarded republication at the existing ask price. Move only public, unexpired, maker-managed asks when an eligible other venue has observed net bid demand that crosses the ask and strictly exceeds demand on the current venue. Keep exact price, pending-fee treatment, recipient/own-venue restrictions, fresh holdings and commitments, publication lock, quotas and deadline checks. Feed data selects the route; it is not a guaranteed fill. No new board polling or trade acceptance path.

## Acceptance and evidence

1. Verified: unchanged ask moves after confirmed cancellation, once only. New actor regression fails on main (`1 failed in 0.25s`) and passes with this change.
2. Verified: no demand, below-ask demand, equal demand, unknown expiry, addressed and manually managed offers do not trigger migration. `tests/test_market_routing.py`.
3. Verified: unknown/refused cancellation, still-committed or departed asset, exhausted listing/request budgets and elapsed deadline cannot duplicate or force publication. Same test module; existing maker floor and protection tests remain in the focused run.
4. Verified: source uses the existing cached feed, cancellation and locked publication paths. `Maker._with_relocations`, `_venue`, `_reprice`, `_post` in `src/bazaar_agent/agents/maker.py`.

Honest Implementation Metric: 4/4 local implementation criteria verified. Independent review, integrated full gate, CI and deployment are release gates managed by the coordinator, not claimed here.

Unverified: live fill, increased score, present executable demand at any named partner venue.
Could-not-do: no live trades or deployment performed in this slice.
