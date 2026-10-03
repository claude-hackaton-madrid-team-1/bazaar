# B1: organic market and a win-rate bench policy

Night of 3–4 Oct 2026. Branch `night/b1-organic-winrate`, draft PR #94, base #71 (`feat/venue-broker-build-only`, head e82ba8d). Refs #12, #13, #17.

## 1. Organic market-making

**The formula.** RULES.md only says "value created between other teams on your venue". Issue #17 quotes the site's front-end: "√ of the value that other teams create on our venue, capped per pair" [quote]. It is unverified against any real score. The √, the per-pair cap and the normalisation to the top-three mean (W5's model) are all parameters in `src/bazaar_agent/organic.py`. The 30 market points are split between the bench and organic in a way nobody knows (W5 assumes 15 + 15).

**What the real feed shows** (shared DB `feed_events`, ticks 0–159, read-only; `venue_flows`):

| venue | owner | fee | offers posted | makers | trades between other teams | distinct pairs |
|---|---|---|---|---|---|---|
| rastro (house) | — | 5 % + 1 P/card (11.6 % of volume paid) | 739 | 15 | 46 (1,093 P) | 38 (13 teams) |
| v01 "Mercado Team 6" | t06 | 0 bps | 0 | 0 | 0 | 0 |
| v02 "El Duende · zero fee" | t12 | 0 bps | 0 | 0 | 0 | 0 |
| v03 "Mercado Trece" | t13 | 100 bps | 0 | 0 | 0 | 0 |
| v04 "El Rastro Express" (auto) | t02 | 0 bps | 0 | 0 | 0 | 0 |

- Every public offer went to El Rastro: 739 of 739, from opening (ticks 100–129) to tick 159. That held despite two 0 % venues and three announcements (t13 at ticks 130 and 150, t06 at 154, "0 % fee" and "Still paying El Rastro 5 % + 1 P?").
- The kit's starter agent hardcodes `venue="rastro"`, and the SDK's `list_offer` defaults to the house. Venue owners t06, t12 and t13 post on El Rastro too, because an owner cannot trade on its own venue (`self_venue`).
- **Fee is not a lever** (two venues already charge 0). Announcements have reached nobody so far. Only agents that read `venues()` or the feed and choose a venue themselves (likely LLM-driven ones) can be reached.
- The feed shows prices, not private values, so "value created" cannot be measured from it. Volume overstates it.

**What one pair would score** (`organic.scenario_table`, pairs worth 10 P each, cap 10 P, normalised to the top-three mean; every team has a raw, most of them 0):

| field | √ | linear |
|---|---|---|
| we have 1 pair, nobody else has any | 1.00 | 1.00 |
| we 1 pair, one rival venue with 10 | 0.72 | 0.27 |
| we 3 pairs, one rival venue with 10 | 1.00 | 0.69 |
| we 1 pair, three rival venues with 10 each | 0.32 | 0.10 |

With 15 points on organic, a single genuine pair between two other teams on our venue, while nobody else has any, would be worth the full 15 × the day's weight under every reading. That is the most uncertain reading, and it is still the reason the cheap levers are worth pulling. Cheap levers, in order:

1. An honest description and one announcement at opening, with numbers: "0 % fee, no per-card charge: El Rastro took 11.6 % of every trade's price on Friday. Our broker crosses overlapping offers every tick." Nothing injection-shaped (W6 tests exactly that from the other side).
2. The broker matching public offers every tick, which #71 already does.
3. Fee 0 as hygiene, not as a differentiator.
4. Watching it: `venue_flows` over the feed every hour (offers posted per venue, distinct pairs).

**Not recommended:** any arrangement with another team to post on each other's venues. That is "feeding another team on purpose" (RULES.md fair play), and the pair cap is there to catch it.

## 2. A Market Test policy that maximises P(beat the stall)

`src/bazaar_agent/agents/bench_winrate.py`, `WinRatePolicy(prior)`: a `policy(book) -> [(sell, buy, price)]` callable. It is not wired into the broker. Every time it reads the book it does the following:

- It replays the free stall in its head (the stall is deterministic and sees the same book).
- It samples 128 futures consistent with the quotes seen: hidden limits, firmness, relax rates and patience from W1a's model, with unseen traders drawn whole.
- It scores every matching of the pairs that cross at today's quotes (the stall's plan, other matchings, waiting), each continued stall-style to the end, as win 1 / tie 0.5 / loss.
- It deviates from the stall's plan only when the paired gain clears 1 standard error.

It uses the quote rule only, so it never sends a refusable match, and it decides in under 10 ms per read (0.04–0.09 s per 16-tick book).

**Results** (W1a's bench, quote rule, 300 holdout books, seeds 1000–1299, `scripts/b1_bench_tournament.py`). "Cautious" = `loss_curve="zero"` (the default), "aggressive" = `"linear"`.

| cell (books drawn from) | mode | win / tie / loss vs stall | points, loss linear | points, loss = 0 |
|---|---|---|---|---|
| normal (prior right) | cautious | 15.0 / 78.3 / 6.7 % | 0.571 | **0.542** |
| normal (prior right) | aggressive | 33.7 / 21.0 / 45.3 % | 0.637 | 0.442 |
| hard (prior right) | cautious | 15.7 / 77.0 / 7.3 % | 0.575 | **0.542** |
| hard (prior right) | aggressive | 35.3 / 14.0 / 50.7 % | 0.643 | 0.423 |
| normal, shades 1.5× (prior wrong) | cautious | 12.0 / 83.7 / 4.3 % | 0.556 | 0.538 |
| normal, shades 2× (prior wrong) | cautious | 8.7 / 84.3 / 7.0 % | 0.535 | 0.508 |
| normal, shades 2× (prior wrong) | aggressive | 17.3 / 55.0 / 27.7 % | 0.558 | 0.448 |
| normal, relax 0.2–0.5 (prior wrong) | cautious | 13.7 / 79.0 / 7.3 % | 0.562 | 0.532 |
| normal, relax 0.2–0.5 (prior wrong) | aggressive | 31.0 / 24.3 / 44.7 % | 0.621 | 0.432 |
| normal, all at tick 0 (prior wrong) | cautious | 31.7 / 59.3 / 9.0 % | 0.657 | 0.613 |
| hard, shades 2× (prior wrong) | cautious | 8.7 / 85.3 / 6.0 % | 0.540 | 0.513 |
| hard, shades 2× (prior wrong) | aggressive | 20.7 / 46.3 / 33.0 % | 0.573 | 0.438 |

The stall scores 0.500 in every row. Points are against two stall-level rivals.

Reading it:
- **Cautious** gains +0.042 points per session (0.542 vs 0.500 with losses at 0) when its prior is right. Where the prior is wrong it gains +0.008 to +0.038, rising to +0.113 if every trader is in the book at tick 0. It loses to the stall on 4–9 % of sessions.
- **Aggressive** wins more often (17–40 %) but loses 26–51 % of sessions, so it scores below the stall (0.42–0.45) whenever a loss scores 0, even with the right prior. It only pays if a loss below the stall still scores linearly. That segment of the curve has the least support (RULES.md gives two anchors, both at or above the stall), so **aggressive is never recommended** while the curve is unknown.
- For comparison, on the same bench the oracle scores 0.79 (normal) and 0.84 (hard) points, the stall 0.50, and W1b's edge policy 0.53–0.54. Cautious captures 12–24 % of the oracle's headroom over the stall.
- This resolves the tension with W1a's proposed "never below the stall" gate. The objective is expected points. "Never below" is the right safety rule only if below-stall scores 0 or the field is strong, and the cautious mode is built for exactly that case.

## Go / no-go

- Organic: **no-go as a points source today** (0 of 739 offers went to any team venue). **Go** on the free levers above, and on `venue_flows` to watch them.
- Win-rate policy: **go as an option, cautious mode only**, behind its own default-off parameter if it is ever wired into #71's broker (now live at h6.5, ties the stall by design). The gain is small (+0.6 board points a day at 15 bench points), and it is in-sample by model (see risks).

## Risks

- The policy's prior is a copy of W1a's generator, the same model it is scored on. Where the model is wrong the edge shrinks or vanishes (rows "prior wrong"). The real Market Test may differ in ways no row covers.
- The organic formula, its weight and the normalisation are unverified readings of one quoted string.
- A refused match is noticed when either trader reappears, or when the broker calls `policy.refused()`.

## What Marius must decide

1. Wire the cautious policy as a third value of the broker's bench-policy switch (W1b's #84 adds `bench_policy`), default off, or leave the stall-tying matcher live.
2. Post the honest venue description and the one opening announcement when the venue opens at h6.5.
