# Strategy · Team 1 playbook

The runtime reads this file. `src/bazaar_agent/strategy.py` parses every
`` - `param` = value — why `` line (same format as `GUARDRAILS.md`) and ranks what to do next.
`uv run bazaar strategy` prints the ranked playbook with the exact command for each move.
Guardrails still apply to every move: strategy proposes, `GUARDRAILS.md` disposes.

## The economics (from `GET /api/catalog`, verified 2026-10-02)
- Value of a card to us is book × our set affinity × the copy marginal (1, 0.25, 0.1 for the 1st, 2nd and 3rd copy).
- A complete page (5 commons, 3 uncommons, 2 rares) adds a 25 % page bonus; the epic and legendary add 10 % more.
- Supply is finite: print runs are 300 / 90 / 30 / 9 / 3 per card (common → legendary), and a copy only exists once a pack or a dealer mints it.
- Rares are the bottleneck: 1–4 copies of each exist on Friday. Packs give the next rarity down when one runs out.
- Teams trade rares at 53–80 P (tape, Friday); our ×1.6 set (LAV) is chased by at least one other team.

## Strategies the runtime implements
- complete_pages: buy missing page cards of our highest-affinity sets first; each missing card also carries its share of the page bonus.
- scarcity_first: the fewer copies exist, the sooner we act and the higher we value it; a card with zero minted copies cannot be bought yet, only pulled or waited for.
- sell_to_need: sell duplicates and low-affinity cards to the teams that chase their set, priced at what the card is worth to them, never below our own value.
- dealer_floor: buy plentiful commons and uncommons from dealers at their learned fill price, not from teams.
- pack_value: buy a pack only when its expected value to us (given what we already hold) beats its learned price, a pack slot is left this game hour (`max_packs_per_game_hour` in GUARDRAILS.md and each dealer's `per_team_per_hour`), and Jev (`spend_pack_slot_now`, `questions/packs.json`) decides yes; `no` or `undecided` keeps the slot.
- level_unlock: keep negotiated deals flowing with the newest dealer to unlock the next level early.

## How the engine scores a move
- Value of a missing page card: book × affinity, plus its share (by book, among the page's missing cards) of the page bonus, times `page_bonus_weight`.
- Expected price: median tape price for the card, else for its rarity, else the dealer list price, else `rare_fallback_price` (rares) or book; dealer buys use that dealer's fills, rare bids use team-to-team prints.
- Urgency: the mean of scarcity (1 at or below `scarce_minted_max` copies, then falling) and demand (teams whose top set is the card's set).
- Score: surplus × (1 + `scarcity_weight` × urgency). Each side shows its best `max_moves`.
- Sell ask: the highest of what we lose × `sell_min_value_ratio` (GUARDRAILS.md), `sell_need_share` × book × 1.6 and the tape price. What we lose is our `your_value`, plus the page bonus when we sell our only copy of a page card (all of it on a complete page, else its weighted share). A copy without `your_value` is never offered.
- A buy whose guardrail price cap sits below the market price is not proposed ("cap below market"): that ladder cannot fill.
- Dealer ladder: open at the lowest fill that dealer gave for the rarity; for a dealer with no fills yet (a new level), open at the deepest discount off list any dealer has given. The step reaches the max within `dealer_max_ticks_per_thread`. With `ladder_floor_quantile` above 0, a card buy uses the floor table instead (`bazaar ladder floors`): floor − 2 → floor + 2, step 1, under the cap and our value.
- Pack EV: per slot, rarity odds × the mean value to us of one more copy of a released card of that rarity (copy marginals applied); a printed-out rarity gives the next one down.

## Parameters
- `page_bonus_weight` = 1.0 — how much of a missing card's share of the 25 % page bonus counts toward its value.
- `scarcity_weight` = 1.0 — how strongly scarcity raises a move's priority (0 = ignore supply).
- `scarce_minted_max` = 5 — a card with at most this many minted copies is treated as scarce.
- `min_buy_surplus` = 2 — only propose buys whose value to us beats the expected price by at least this many primas.
- `sell_need_share` = 0.6 — ask a buyer this share of what the card is worth to them (book × 1.6 for a team that chases the set).
- `sell_min_surplus` = 5 — only propose sells that beat our own value of the card by at least this many primas.
- `rare_fallback_price` = 70 — expected price of a rare when the tape has none for that card.
- `pack_price_estimate` = 17 — expected price of a `sobre_barrio` (Abuela's learned floor).
- `max_moves` = 12 — how many ranked moves to show per side.
- `ladder_floor_quantile` = 0 — 0 keeps the lowest-fill dealer ladder; above 0 a dealer card buy opens 2 under that quantile of the limits every team's conversations closed at and stops 2 over it (`bazaar ladder floors`; 0.5 is the W3 plan).
- `ladder_level_deals` = 0 — 0 buys each card from the cheapest dealer; above 0 the newest dealer gets card buys (when its ladder fits our caps and value) until we closed this many deals with it (counted over the whole feed held, not per day): the ladder counts each level's best three, and they unlock the next level early.
- `dealer_jitter_start_spread` = 0 — 0 opens every dealer thread at the plan's start; above 0 the first bid drops a random 0..this many primas under it (B12: our dealer bids are public, a fixed ladder is predictable).
- `dealer_jitter_jump_share` = 0 — below the plan's start, the chance that a raise is a jump (base + 1..`dealer_jitter_jump_max`) instead of the base step; a jump lands at most on the start.
- `dealer_jitter_band_jump_share` = 0 — the same chance from the plan's start up, where a jump past the dealer's secret limit gives back ladder share (`docs/night/b12-dealer-jitter.md`).
- `dealer_jitter_jump_max` = 3 — the largest raise a jump may take.
- `dealer_jitter_band_gap` = 0 — 0 lets a band jump happen anywhere; above 0 only while the dealer's standing ask (never below her secret limit) is at least this many primas above where the jump lands.
- `dealer_min_step_pct` = 0.02 — with any jitter on, no raise is smaller than this share of the plan's max (our stand-in for book): the dealer never moves faster than our last step, and a smaller step earns nothing.
- `dealer_jitter_seed` = 0 — 0 draws a fresh seed per process (a committed seed plus the public thread id would replay our bids); any other value fixes the draws, for tests and simulations.
