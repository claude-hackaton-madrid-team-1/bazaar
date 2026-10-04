# Card hunt: what moves our score on Sunday, and where the points still are (Sun 4 Oct)

This session checked Marius's observation: "buying from the dealers is bringing 0 ROI for the negotiation metric".
- **Data:** read-only Postgres (`me_snapshots`, `leaderboard_snapshots`, `tape`, `decisions`), keyless boards and
  `/api/schedule` (13 GETs at 1 req/s, 10:48–10:52), and the bazaar-taker log (`railway logs`, read-only).
- **Not used:** no keyed request, no game or Railway write.
- **Private values:** our card values, limits and cash are not in this file.

## 1. Answer

Marius is right about `neg_points`: dealer deals score only as ladder slots.

| Fact | Evidence |
|---|---|
| Round 3 reset our `neg_points` to 0, and it stayed 0 | `/me` score JSON: `neg_points` 100.2 → 0.0 at 09:20 (tick 1466) and 0.0 since |
| Our 5 Sunday deals were dealer buys (Abuela ×3, Pícaros ×2, all Chamberí) | `tape` since tick 1445 |
| A dealer buy never moves `neg_points` | Saturday 8/8, Sunday 5/5 |
| Pícaros (L4) buys: ladder +0.07 and +0.063 | Board +0.43 and +0.64 over the field median at the next refresh (ticks 1482, 1682) |
| Abuela (L1) buys: ladder +0.014 to +0.02 | Board about 0 over the median. L1 is now 3/3 (best three per level count) |
| 11 team trades market-wide since 09:00 | They moved their teams +0.9 to +2.2 board each (next three rows) |
| t18 sells SAL-11 for 238 | +2.2 |
| t12 sells MAL-06 for 17 | +1.45 |
| t12 buys LAV-07 for 40 | +1.9 |

The team-trade part (about 15 of the 30 negotiating points) restarted with a near-zero top-3 mean, and we hold 0 of it.

How the field's Sunday-round negotiating was backed out:
- **Method:** `n_now = (1.5·n_sat + p·N3) / (1.5 + p)`, with `p` fitted from t11's market-only line (≈ 0.5 at 10:43).
- **Teams with team trades:** t18 ≈ 27, t13 ≈ 23, t12 ≈ 23.
- **Us:** ≈ 8, from the ladder alone.

Board numbers are the current blended board. Sunday weighs more at the freeze (0.4 of the final score, about 1.6× now).

## 2. Where the points are before 15:00

1. **Team trades at our values.** Any positive surplus at our values scores here: a buy below value, or a sale above value net of the fee. The accepting side pays the fee: 5 % + 1 P/card on El Rastro.
   - **Boards at 10:52:** 66 open offers on El Rastro and 12 team boards.
   - **Positive surplus:** exactly one, a 0-fee team bid above our value for a single copy of a page far from complete. The taker refused it because it priced in a page-bonus share for a page that cannot complete today.
   - **Other team bids:** mostly Chamberí collectors bidding under our value, and t09's v21 lowballing our complete pages.
   - **Addressed offers:** 16 since #277, all correctly refused. 6 were bids below value. 10 were t02 asks at about our own bid, skipped because "our own bid is cheaper".
2. **Ladder slots.**
   - **Status:** L1 3/3, L4 2/3, L2/L3/L5 0/3.
   - **L3 Pilar and L5 Banco:** they sell us nothing we may buy, so only a sale fills them, and `dealer_sell_enabled` is false. Saturday's Pilar sales were our best moves, at +1.1 board each.
   - **L2 Chato:** asks above our caps.
3. **Swaps:** the desk is blocked by Jev.
   - The taker log reads `15 s ticks are shorter than BAZAAR_DECIDER_MIN_TICK_S=30: Jev decides instead of the LLM`.
   - Every Sunday `team_open` was refused as "jev undecided" (0.33–0.47 < 0.75), and most ladder probes too (0.61–0.74).

## 3. What was built (PR feat/card-hunt, `BAZAAR_CARD_HUNT`, default on)

The behaviour is described in the module docstring of `src/bazaar_agent/agents/card_hunt.py`:
- dealer buys only for empty ladder slots;
- the probe and the team desk on their deterministic gates;
- asks below value taken over our own lower bid;
- no page bonus at stake on a page that cannot complete (CHA kept);
- maker dealer sells, still behind `dealer_sell_enabled`, only for empty ladder levels.

The PR body has the deploy steps, risks, rollback and the 20-tick live checks. Two actions sit outside the PR, and both are Marius's call:
- `BAZAAR_DECIDER_MIN_TICK_S=15` and `BAZAAR_DECIDER_TIMEOUT_S=8` on bazaar-taker;
- `dealer_sell_enabled` for the L3 Pilar slots.
