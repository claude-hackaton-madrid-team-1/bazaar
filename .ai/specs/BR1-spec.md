# BR1 — Buyer rank: who we sell each card to

Source: local backlog (`.ai/specs/02-plan.md`, row BR1). Brief from the coordinator, Sat 2026-10-03, tick 453.

## Goal
Sell our duplicates and pack pulls to the team that pays most and is not a podium rival.

## Acceptance criteria
1. A pure ranking (`buyers.rank_buyers`) per card and team: willingness to pay from the tape (set + rarity, then
   rarity, then market, then book), set interest (affinity map), whether the team misses the card (supply map);
   each row explains why.
2. Rival penalty: a top-5 team, or one up to 3 ranks above us, never ranks above an equal-priced buyer
   elsewhere, and the maker never addresses an ask to one.
3. A card that would complete a top-5 team's page is never sold to it below 1.5 × our value (row blocked).
4. `bazaar buyers [--card X] [--json]`, read-only against the game; `--save` stores `team_buyer_rank`.
5. The maker addresses asks it already decided to post behind `buyer_rank_enabled` (ships false, because
   addressing restricts who may buy; Friday: 6 % addressed vs 19 % public fill). Prices, caps and `check()` are
   unchanged.
6. An addressed ask unfilled after `buyer_rank_fallback_ticks` is reposted for anyone at the exact same price
   (no Jev reprice, no Jev hold); one copy never goes to one team twice at one price; the listing limit holds.
7. A failure in the ranking (hostile feed strings) keeps the asks public and never costs the maker's tick.

## Out of scope
The team desk (N17) does not use the ranking yet. The addressed-offer memory is per process: it is lost on a
restart (see `98-nice-to-haves.md` if it is picked up).
