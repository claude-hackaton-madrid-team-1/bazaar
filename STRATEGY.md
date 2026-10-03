# Strategy · Team 1 playbook

The runtime reads this file. `src/bazaar_agent/strategy.py` parses every
`` - `param` = value — why `` line (same format as `GUARDRAILS.md`) and ranks what to do next.
`uv run bazaar strategy` prints the ranked playbook with the exact command for each move.
Guardrails still apply to every move: strategy proposes, `GUARDRAILS.md` disposes.

## The economics (from `GET /api/catalog`, verified 2026-10-02)
- Value of a card to us is book × our set affinity × the copy marginal (1, 0.25, 0.1 for the 1st, 2nd and 3rd copy).
- A complete page (5 commons, 3 uncommons, 2 rares) adds a 25 % page bonus; the epic and legendary add 10 % more.
- The server's `your_value` of a held card is the collection value lost by removing that copy (rules audit, Sat 3 Oct: 41 of 41 assets reproduce). On a complete page the `your_value` of our only copy of a page card already includes the whole page bonus; duplicates never carry it. Adding the bonus on top when selling counts it twice (fix on branch fix/audit-page-bonus-double-count).
- Supply is finite: print runs are 300 / 90 / 30 / 9 / 3 per card (common → legendary), and a copy only exists once a pack or a dealer mints it.
- Rares are the bottleneck: 1–4 copies of each exist on Friday. Packs give the next rarity down when one runs out.
- Teams trade rares at 53–80 P (tape, Friday); our ×1.6 set (LAV) is chased by at least one other team.

## What scores (rules audit, Sat 3 Oct; fitted on `/me` snapshots, private values left out)
- Holding cards, the album and `collection_value` never score in themselves. A card scores only when it moves:
  - sold to or bought from another team, at price minus our `your_value` (`neg_points`);
  - or as a dealer deal on the ladder: the share of that dealer's own range, buying or selling. A deal at the opening price scores 0 and a deal at its final scores the whole range. Best 3 per level, restarted every round.
- Market-making per round ≈ 22.5 × Market Test bench points + 7.5 × value other teams create on our venue. The free stall's level is 0.5 bench.
- Negotiating per round ≈ ladder 7.5 + duels 7.5 + team trades 15 (estimate). Each part is capped at the top-3 mean, so past the cap more of it adds nothing that round.
- Round 3 starts Sunday ~11:34 (game hour 16.65) and lasts ~3.4 h. Sunday's cash (with the 150 P grant) is best kept for team trades with surplus and three negotiated deals per dealer level, not for packs: pack luck never scores.
- Jev (questions/rules_audit.json):
  - Workshop (`workshop_build_noul`): no, duplicates are worth more as team trades than as Workshop inputs.
  - Overnight build order (`overnight_first_build_choice`): undecided, leaning toward the Market Test edge broker (0.58) over the round-3 ladder tracker (0.36).

## Strategies the runtime implements
- complete_pages: buy missing page cards of our highest-affinity sets first; each missing card also carries its share of the page bonus.
- scarcity_first: the fewer copies exist, the sooner we act and the higher we value it; a card with zero minted copies cannot be bought yet, only pulled or waited for.
- sell_to_need: sell duplicates and low-affinity cards to the teams that chase their set, priced at what the card is worth to them, never below our own value.
- sell_spares: a spare copy (a duplicate, or a card of a set whose affinity to us is at most 1, i.e. no boost) nobody would pay `sell_min_surplus` over our value for at the buyer's need or the tape (or of a set nobody is seen chasing) is still offered to anyone, at our value + `sell_min_surplus` (`sell_spare_slots` of them at most), so the maker keeps listing what we can sell at a gain.
- new_pages: a page released mid-game (El Retiro Saturday, Chamberí Sunday) enters the ranking the first tick `/api/me` shows it: the playbook is rebuilt from `/api/me` and the catalog every tick, so no restart is needed. Our only copy of each of its page cards is never sold (`protect_page_sets` in GUARDRAILS.md).
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
- Dealer ladder: open at the lowest fill that dealer gave for the rarity; for a dealer with no fills yet (a new level), open at the deepest discount off list any dealer has given. The step reaches the max within `dealer_max_ticks_per_thread`.
- Pack EV: per slot, rarity odds × the mean value to us of one more copy of a released card of that rarity (copy marginals applied); a printed-out rarity gives the next one down.
- Supply map (`uv run bazaar supply`): the 270 starting assets (ids 1–270, team k was dealt ids 15k−14…15k), the feed's settlements, listings and `pack.opened`, and the catalog's minted counts: who holds each card, how many copies another team could sell us, and how many complete pages of each set can exist (the fewest copies of any page card).

## Parameters
- `page_bonus_weight` = 1.0 — how much of a missing card's share of the 25 % page bonus counts toward its value.
- `scarcity_weight` = 1.0 — how strongly scarcity raises a move's priority (0 = ignore supply).
- `scarce_minted_max` = 5 — a card with at most this many minted copies is treated as scarce.
- `min_buy_surplus` = 2 — only propose buys whose value to us beats the expected price by at least this many primas.
- `sell_need_share` = 0.6 — ask a buyer this share of what the card is worth to them (book × 1.6 for a team that chases the set).
- `sell_min_surplus` = 5 — only propose sells that beat our own value of the card by at least this many primas (sells to a dealer use `dealer_sell_min_surplus`, GUARDRAILS.md).
- `sell_spare_slots` = 8 — sell_spares: besides sell_to_need, offer up to this many more spare copies (duplicates, or cards of sets with affinity at most 1) to anyone, at our value (page bonus included) + `sell_min_surplus`, when the buyer's need and the tape sit below that or nobody is seen chasing the set; never a protected card (`protect_page_sets`), never below our value. 0 = off (only sell_to_need). Sells still show at most `max_moves`.
- `rare_fallback_price` = 70 — expected price of a rare when the tape has none for that card.
- `pack_price_estimate` = 17 — expected price of a `sobre_barrio` (Abuela's learned floor).
- `max_moves` = 12 — how many ranked moves to show per side.
- `dealer_mints_unminted` = false — true: a card with zero minted copies is still a dealer buy when a dealer sells its rarity for its released set (dealers mint: Friday, Chato sold LAV-09 serials 2–4 and Abuela LAV-04 serials 15–16 without buying them first). Matters the hour a set is released (RET Saturday, CHA Sunday), when every card of it has zero copies.
- `pack_ev_album` = false — true: a pack buy is valued card by card (Marius's B9, #109): each card still mintable in a released set, at what the next copy is worth to us, plus its page-bonus share when our album lacks it (`/api/me`). False keeps Friday's EV (the mean copy value per rarity) for buys, because B9's verdict is to buy no Abuela packs on Saturday (her pack fills median 22 P > `max_price_pack` 20, and a pack thread takes the Abuela conversation the ladder needs). Opening a sealed pack always uses the card-by-card value.
- `supply_scarcity` = true — scarcity counts the copies other teams could sell us (minted, minus ours, minus those the supply map places with the teams that chase the set: `uv run bazaar supply`); false: every minted copy.
- `chaser_min_p` = 0 — who chases a set (sell_to_need buyers, the maker's `to` under the counterparty cap, buy urgency): 0 = the team-flow guess (`intel.TeamFlow.top_set`); above 0 = the teams whose top set it is with at least this probability in the rival affinity map (`bazaar affinity`; 0.5 is the night report's cut).
