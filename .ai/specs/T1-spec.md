# T1 — Card value calculator + trade scanner with per-counterparty caps  (per-task spec)

- Task id: T1 (migrated from GitHub issue(s) #14)
- Priority: P1
- Status: 🔵 taken over in N17: Marius's #79 (per-counterparty cap, affinity map, swaps) and #98 (rival profiles, opportunity scanner) are being landed with fixes, then team-to-team swap threads.
- Backlog source: local (`.ai/specs`). GitHub issues are not used any more (migrated and closed 2026-10-03).
- Traces up to: [`01-spec.md`](./01-spec.md)  ·  Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Know what each card is worth to us and to each team, and trade where the private-value surplus is largest, within a per-counterparty cap.

## Acceptance criteria (each MUST be testable)
- [ ] 1. See N17 (.ai/specs/N17-spec.md, PR #123).

## Source (the original issue text, verbatim)

### #14 — [trading] Card value calculator + trade scanner with per-counterparty caps

Migrated from https://github.com/claude-hackaton-madrid-team-1/bazaar/issues/14 on 2026-10-03 (closed there).

## Context
- **Holding cards does not score by itself**: the album "worth" and the pack "luck" are only displayed, just like grants and gifts from the personas.
- What scores is the *"private-value surplus from team-to-team trades, capped per trade and per counterparty"*. Valuation is there to tell us what to sell and what to buy.

Value of a card [inferred]:
- book × set affinity (private) × marginal per copy: 1 for the first, 0.25 for the second, 0.1 from the third onwards;
- +25% for completing a page (the 10 C/U/R of the set);
- +10% for master (page + epic + legendary).

The server gives the official value at `GET /api/me/value?card=`.

## What to do
- Local calculator validated against `/api/me/value` and `delta(give, want)` to evaluate offers.
- **Open packs early:** first copies are worth more and cards generate trade surplus.
- **Trade scanner:** sell duplicates (worth 25% or 10% to us, 100% to whoever doesn't have it) and buy the ones that complete a page. Scan `/api/venues/{vid}/offers` and `/api/me/offers`.
- **Spread across many counterparties:** there is a per-counterparty cap, and the ring guard scores pairs where one side always takes the whole pie as a 50/50 split.

## Acceptance criteria
- [ ] The calculator matches `/api/me/value` (±1%) on 20 cards.
- [ ] Every posted offer has expected surplus > 0 at our valuation.
- [ ] No counterparty exceeds X% (configurable) of our trade surplus.

**Comment by serban-marius:**

**Confirmed (RULES.md):**
- `your_value` comes per card in `GET /api/me`. No calculator needed for what we hold, only for the ones we're missing (`/api/me/value?card=`).
- Everyone has the same 6 set multipliers, shuffled.
- To ask for any copy of a card: `"want": {"cards": ["LAV-03"]}`.
- A thread between teams ends with a deal or at 200 messages.
- *"feed another team on purpose … those deals count for nothing"*.

**Comment by serban-marius:**

**Value formula verified against the real API** (details in #23): `book × affinity × [1, 0.25, 0.1][copies]`. The `your_value` of a card in hand is the value of the last copy, which is what we lose by selling it. Scarcity of each card in circulation in #22: there are no epics or legendaries, and LAV-09, MAL-09 and MAL-07 have only 1 copy.

**Comment by serban-marius:**

**Audit 2026-10-03 (main @ 1a89f86 + shared DB + monitor captures)** — status: still open, valuation and the surplus gates work, but there is no validation against `/api/me/value` and no per-counterparty cap.

- ✅ Done:
  - Local calculator (`src/bazaar_agent/strategy.py:350`, `strategy.py:453`).
  - We only buy with surplus ≥ `min_buy_surplus` (`agents/taker.py:106`).
  - We only sell above `your_value` + `sell_min_surplus` (`strategy.py:587-588`), and the `sell_min_value_ratio` guardrail also applies.
  - Board scan (`agents/taker.py:347-356`) and the maker's `sell_to_need`.
- ❌ Missing:
  - Validation against `/api/me/value` on 20 cards: `src/` never calls it.
  - `delta(give, want)` for card-for-card offers.
  - The per-counterparty cap.
- Findings (fix PR planned, no branch yet):
  - An accept and a dealer bid in the same tick can overrun the cash floor and the hourly spend.
  - Open dealer bids are not counted in `spent_last_hour`.
  - Refunds for cancelled bids are booked at the time of the cancel instead of the hour of the spend.
- Related:
  - #59 (open) books the cancel refund in the right hour, but only in its runtime tools.
  - #57 (merged after this audit) added Jev-chosen list prices for the maker (`agents/maker_jev.py`). Re-check the sell path against 8bcf0fd.
