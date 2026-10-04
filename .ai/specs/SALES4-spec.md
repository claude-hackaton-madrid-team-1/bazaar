# SALES4 — negotiate across allied markets

User priority: Sales hunts buyers across allied markets, including newly joined v07, instead of repeatedly reopening the same proposal. Keep protected copies, value floors, shared reservations, deadlines and fee checks.

## Implementation
- Persist the existing 20-tick team rest window per buyer/card before ranking another lead. Price changes do not bypass it; unresolved openings retain their claim.
- Put the exact card, price and venue before optional model wording.
- Permit guarded cash counters on open configured allied markets; preserve the actual venue in acknowledged speech.
- Add verified owner t10 for v07 to preferred market owners. Retain t18 as an ally, but its v28 is currently closed and must not receive orders.

## Validation and limits
Final integrated focused run: `284 passed in 1.97s`. Ruff and Black pass; mypy reports no issues in three changed source files. Independent review and final CI are required before merge; production behavior is not yet verified.

Unverified: subsequent rival replies, completed trades, and score improvement. Could-not-do: guarantee buyers or nine settlements. Broad swap routing, cancelling a still-live own thread offer to counter, and reclaiming already-listed inventory are outside this slice. The public API reports v28 closed; v05, v07 and v15 are open with zero fees.
