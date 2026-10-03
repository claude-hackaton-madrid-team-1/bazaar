# B28 · Go-live counterfactual for the taker (night shift, 4 Oct)

`uv run bazaar taker-replay STREAM --me FILE [--cash N] [--start-of-day] [--duel-ticks …]` replays a captured day
tick by tick through the CURRENT taker, live against fake clients built from the feed: the guardrails with a
shared ledger, accept slots, duels' slots, and a wallet each accept settles into at the next tick. There is no
dealer desk and no Jev. Then it changes one thing at a time.

**Verdict: GO for a live taker on board asks, with `min_buy_surplus` raised to 4–6** (a STRATEGY.md change for
Marius; nothing was changed tonight). Cash above `cash_floor` is the binding constraint. At `min_buy_surplus` 2,
the taker spends its headroom on 1–4 P commons in the first ticks; when a rare worth ~+37 P appears at tick 46,
the floor blocks it.

## Friday (ticks 0–149), from the start-of-day album, 400 P (counts only; P figures in the PR body)

| run | buys | blocked by | lost the slot |
|---|---:|---|---:|
| current rules and settings | 8 | max_price_uncommon 30, cash_floor 10, max_price_rare 2 | 10 |
| no cash_floor | 10 | max_price_uncommon 31, max_spend_per_game_hour 11 | 12 |
| no hourly spend cap | 8 | (identical: the hourly cap never binds) | 10 |
| no price caps | 8 | cash_floor 29 (dearer uncommons, less surplus) | 9 |
| min_buy_surplus 4 / 6 | 6 / 6 | cash_floor 53 / 25 | 9 / 4 |

- Which caps cost the most: `cash_floor` (lifting it nearly triples the surplus). The price caps protect: lifting
  them lowers the surplus. The hourly cap never binds. `max_price_uncommon` applies to ask + fee: 30 uncommon asks
  priced 25–29 were refused.
- Duels: our only duel accept inside the capture (tick 148) did not collide with a buy. Duel slots that collided: 0.
- Contested asks (another team filled the same ask at the same settlement): 0.
- Saturday (today's album, Friday's market as a proxy): from 353 P (09:00), the current settings buy 4 cheap
  commons and little else; `min_buy_surplus` 6 buys 2 cards with ~40× the surplus. From 503 P (after the grant) every
  setting lands within ±10 % of each other. The surplus is bounded by cash (233 P after the grant), not by
  opportunities: ~6 buys per Friday-like 2.5 h. On a 14 h Saturday at 30 s ticks, expect the headroom to be spent by
  midday, unless the maker's sales add cash.
