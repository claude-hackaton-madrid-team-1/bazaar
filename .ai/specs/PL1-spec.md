# PL1: Replenish pack inventory for guarded team resale

Source: Omar's explicit Sunday correction: buy packs to open and sell their contents to teams; do not reject them solely because private holding EV is below purchase price. Local backlog. Base: main176b83c7.

## Acceptance

1. Explicit `pack_restock_enabled = true` authorizes deterministic inventory acquisition, up to the observed22P Abuela quote (thread2670), within existing shared3/hour and dealer quotas, cash, commitments and tick limits. No Jev holding-EV veto.
2. Rank restock packs by expected immediately sale-eligible pulls from current holdings and mintable released catalog cards. Holding EV/surplus remain diagnostic; no forecast of guaranteed score or realized resale profit. Ordinary card-buy valuation remains unchanged.
3. Open acquired packs for resale when `open_sealed_packs` permits. After fresh holdings, maker may list true duplicates above their server value; sole-copy/page, floor and commitment guards remain mandatory.
4. Preserve legacy pack planning when restocking is disabled; verify full buy→settle→open→fresh holdings→guarded listing path with fake-only tests and obtain independent review before rollout.

## Plan

Add one explicit policy flag using existing planner, pack-slot gate and taker calls. Reuse existing guardrails, pack opening, maker and quota ledger. No schema, service, model or network-read additions. Other agents independently review security and integration; TT2's separate taker edits are coordinated before final gate.

## Honest implementation report

- Verified: explicit acquisition without holding-EV/Jev veto; regression keeps EV13.3 below price22 as a negative holding diagnostic and still proposes a22P ladder.
- Verified: pack availability, shared/dealer quotas, cash floor, price ceiling, sole copy, sell floor and expired tick remain effective.
- Verified: fake server cycle negotiates below opening30, accepts22, settles next tick, opens the acquired pack, then maker refreshes holdings and posts its pulled duplicate above actual value4.
- Verified: legacy strategy/gate behavior; final focused suite evidence: `232 passed in 1.30s`. Static checks: `All checks passed!`, `734 files already formatted`, `475 files would be left unchanged`, `Success: no issues found in 5 source files`; generated docs and diff checks clean.
- Pending: current-main full gate and independent verdicts before rollout.

Honest metric: 3/4 acceptance criteria verified locally (75%); final integration/review outstanding. Unverified: live pack acquisition, resale conversion and score gain. Could-not-do: none; this worker intentionally performs no live trade, merge or deployment.
