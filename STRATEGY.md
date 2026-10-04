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
- Separate models (details in `.ai/context.md`): duels score the pie share × (1 − decay)^rounds and move no cash or card, so `your_value`, the sell floor and the album never apply to a duel; the ladder scores the share of a dealer's range; team trades score price − `your_value`; market-making is 22.5 × bench + 7.5 × organic. Never mix their numbers or lessons.
- Top lever (a game founder, via Omar): trading with teams at private values, then dealer deals near their final, then duels and market. Completing a page by buying SAL-07 back raised collection value by about 88 and the score did not move.
- Holding cards, the album and `collection_value` never score in themselves, but that is no licence to break a page: selling the only copy of a page card on Sat 3 Oct (tick 948) dropped `neg_points` 134.7 → 44.6 (a sale at 29 of a copy with `your_value` 118.6: price − `your_value` = −89.6, `.ai/memory.md`). A card scores only when it moves:
  - sold to or bought from another team, at price minus our `your_value` (`neg_points`);
  - or as a dealer deal on the ladder: the share of that dealer's own range, buying or selling. A deal at the opening price scores 0 and a deal at its final scores the whole range. Best 3 per level, restarted every round.
- Market-making per round ≈ 22.5 × Market Test bench points + 7.5 × value other teams create on our venue. The free stall's level is 0.5 bench.
- Negotiating per round ≈ ladder 7.5 + duels 7.5 + team trades 15 (estimate). Each part is capped at the top-3 mean, so past the cap more of it adds nothing that round.
- Round 3, "Sunday · Chamberí", started at tick 1446. The pre-opening h16.65 anchor is obsolete. Keep cash for legal team trades with surplus and three negotiated deals per dealer level; packs do not score by themselves.
- The [live schedule](https://bazaar.causaprima.ai/api/schedule) at `now_hours=14.037` lists the next Market Tests at h14.65/h15/h17, Duels III at h15.367, dealer closure and final duels at h18.367, and scores freeze at h19.367. Duels III has price and days, 12-tick duels and decay 0.10. Doors close at the explicit wall time 15:00 CEST. Re-read clock/schedule and use the deploy guard before acting; see `docs/briefing.md` for evidence.
- Jev (questions/rules_audit.json):
  - Workshop (`workshop_build_noul`): no, duplicates are worth more as team trades than as Workshop inputs.
  - Overnight build order (`overnight_first_build_choice`): undecided, leaning toward the Market Test edge broker (0.58) over the round-3 ladder tracker (0.36). The edge broker has since merged (#218, behind `BAZAAR_BENCH_POLICY`), so the open build is the ladder tracker.

## Operating priority

Prioritise profitable team negotiations at private values. Buy below our marginal value, sell guardrail-eligible inventory above the server's `your_value`, and account for the accepting side's venue fees. Preserve sole copies on protected complete pages under current `GUARDRAILS.md` and obey approval, cash and tick limits. Next, negotiate dealer deals toward their final for the round's best three per level. Scheduled duels and Market Tests have their own policies and deadlines; the card playbook does not rank those mechanisms. Market-making rewards efficient matches and value other teams create on our venue.

The card playbook below is an inventory opportunity estimate, not the leaderboard objective. Its page-bonus share estimates buying value; it is never points for holding a card or completing a page. Scarcity is an urgency heuristic. A profitable settlement is the goal, and more trades, fees collected and pack luck earn no points.

## Card opportunities the runtime implements
- complete_pages: identify missing page cards with positive estimated purchase surplus; affinity and a page-bonus share estimate their private value. Completing the page itself earns no score.
- scarcity_first: the fewer copies exist, the sooner we act and the higher we value it; a card with zero minted copies cannot be bought yet, only pulled or waited for.
- sell_to_need: sell duplicates and low-affinity cards to the teams that chase their set, priced at what the card is worth to them, never below our own value.
- sell_spares: a spare copy (a duplicate, or a card of a set whose affinity to us is at most 1, i.e. no boost) nobody would pay `sell_min_surplus` over our value for at the buyer's need or the tape (or of a set nobody is seen chasing) is still offered to anyone, at our value + `sell_min_surplus` (`sell_spare_slots` of them at most), so the maker keeps listing what we can sell at a gain.
- new_pages: a page released mid-game (El Retiro Saturday, Chamberí Sunday) enters the ranking the first tick `/api/me` shows it: the playbook is rebuilt from `/api/me` and the catalog every tick, so no restart is needed. The current complete-page protection policy also applies to its proactive listings (`protect_page_sets` and `protect_complete_pages_only` in GUARDRAILS.md).
- dealer_floor: buy plentiful commons and uncommons from dealers at their learned fill price, not from teams.
- pack_restock: when explicitly enabled in GUARDRAILS, replenish available pack inventory within the cash floor and hourly quotas; open content for guarded team resale. Expected tradable pulls rank packs; holding EV is diagnostic, not a score prediction.
- pack_value (restocking disabled): buy a pack only when its expected value to us (given what we already hold) beats its learned price, a pack slot is left this game hour (`max_packs_per_game_hour` in GUARDRAILS.md and each dealer's `per_team_per_hour`), and Jev (`spend_pack_slot_now`, `questions/packs.json`) decides yes; `no` or `undecided` keeps the slot.
- level_unlock: keep negotiated deals flowing with the newest dealer to unlock the next level early.

## How the card playbook ranks a move
- Value of a missing page card: book × affinity, plus its share (by book, among the page's missing cards) of the page bonus, times `page_bonus_weight`.
- Expected price: median tape price for the card, else for its rarity, else the dealer list price, else `rare_fallback_price` (rares) or book; dealer buys use that dealer's fills, rare bids use team-to-team prints.
- Urgency: the mean of scarcity (1 at or below `scarce_minted_max` copies, then falling) and demand (teams whose top set is the card's set).
- Priority estimate, not leaderboard points: surplus × (1 + `scarcity_weight` × urgency). Each side shows its best `max_moves`.
- Sell ask: the highest of what we lose × `sell_min_value_ratio` (GUARDRAILS.md), `sell_need_share` × book × 1.6 and the tape price. What we lose is our `your_value`, plus the page bonus when we sell our only copy of a page card (all of it on a complete page, else its weighted share). That is today's code; on a complete page it counts the bonus twice (see "The economics"). Our only copy on a complete page remains protected; an incomplete page may supply a listing when `protect_complete_pages_only` is enabled. A copy without `your_value` is never offered.
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
- `preferred_sell_venue_owners` = t04,t10,t15,t18 — Omar, Sun 4 Oct: keep public sale inventory on Team4 (v05), Team10 (v07), Team15 (v15) and Team18 (v28) markets. When no eligible crossing bid provides a better route, distribute asks by copy ID across their open zero-fee venues, using current/pending fees. Concrete crossing demand takes priority; no eligible preferred venue falls back to activity. Never our own venue, never the addressee's venue; addressed offers retain their lowest-fee route. `none` disables the preference. Existing public asks on nonpreferred venues migrate through confirmed cancellation and fresh publication guards; an ask already on any preferred venue stays there unless better crossing demand appears. No extra market reads.
- `rare_fallback_price` = 70 — expected price of a rare when the tape has none for that card.
- `pack_price_estimate` = 22 — current planning estimate for `sobre_barrio` (Sunday Abuela quote, thread2670); negotiate from observed lower fills where available.
- `max_moves` = 12 — how many ranked moves to show per side.
- `dealer_mints_unminted` = false — true: a card with zero minted copies is still a dealer buy when a dealer sells its rarity for its released set (dealers mint: Friday, Chato sold LAV-09 serials 2–4 and Abuela LAV-04 serials 15–16 without buying them first). Matters the hour a set is released (RET Saturday, CHA Sunday), when every card of it has zero copies.
- `pack_ev_album` = false — diagnostic holding EV uses mean copy value per rarity; true adds missing page-bonus shares. When `pack_restock_enabled` is true, neither holding EV nor Jev's holding-value judgment vetoes acquisition. Rank packs by expected immediately sale-eligible pulls (duplicates or non-page cards), prioritize replenishment, and use existing cash and pack quotas. Opening is inventory preparation; only a later positive-surplus team trade can produce negotiation points.
- `supply_scarcity` = true — scarcity counts the copies other teams could sell us (minted, minus ours, minus those the supply map places with the teams that chase the set: `uv run bazaar supply`); false: every minted copy.
- `chaser_min_p` = 0 — who chases a set (sell_to_need buyers, the maker's `to` under the counterparty cap, buy urgency): 0 = the team-flow guess (`intel.TeamFlow.top_set`); above 0 = the teams whose top set it is with at least this probability in the rival affinity map (`bazaar affinity`; 0.5 is the night report's cut).
