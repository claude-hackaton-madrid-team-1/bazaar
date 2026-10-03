# Collections and packs: what filling album pages scores, and whether packs pay (Saturday 3 Oct review)

Session `sat-collections`, written Sun 4 Oct 00:10–00:45 Madrid and revised 01:00–01:40 after the review
(`_sat-review/review-collections.md`; how each item was handled is at the end).
- **Read-only:** no game writes, no keyed API calls, no Railway or GitHub writes.
- **Redacted:** cash, raw `neg_points`, our values and caps for cards we might still buy, the named holders of those
  cards, and the exact album gaps. They are in `_sat-review/private/collections-private.md`, outside the repo and uncommitted.
- **Already in the repo:** our set multipliers (`.ai/specs/K1-spec.md:135`: LAV 1.6 / SAL 1.3 / MAL 1.1 / CHA 0.9 /
  RET 0.7 / LAT 0.5). Book × multiplier for a missing page card is used openly.
- **Code references** are to `origin/main` 166f9ca5. The report was first based on fc9cd61e and the reviewer checked
  ceef0f74; the commits in between do not touch the buy path, and `cash_floor` is 5 throughout.

## TL;DR

1. **A page scores nothing by itself (re-verified; w7's claim holds).** RULES.md:118-122 has no album term.
   - **Ours:** our 4 page completions moved negotiating by the ladder step of the deal, within drift, and nothing more.
     MAL, completed at tick 1212 from Pícaros at 61 P, moved it **0.00**.
   - **Field (30 completions), median excess over the field:**
     - completer bought from a team: **+1.94** (n=11);
     - completer bought from a dealer: +0.46 (n=15, the size of a ladder step);
     - control, a team buy with no page rise: +0.015 (n=34, outside duel windows).
2. **The page bonus scores only one way: buy the last missing card from another team.**
   - That card's `your_value` carries the whole bonus: 0.25 × Σ book × multiplier over the page's 10 cards
     (`/api/catalog` `values.page_bonus` 0.25). The team trade credits value − price − fee to `neg_points`.
   - We bought all three of our completing cards from dealers, so none of the bonus scored.
   - MAL-09 alone, bought from a team at ~95, was ≈ +49 surplus after the fee ≈ **+1.0–1.2 final points**.
3. **Sunday: complete Chamberí (CHA, ×0.9) the right way.**
   - Buy 9 cards from dealers (they also count as ladder deals) and buy one reserved common from a team, last.
   - Cost ≈ 225 P; the completing trade is ≈ +58 surplus ≈ **+1.2 final**.
   - It needs **change A**: one card reserved per page and blocked on every route until the other 9 are held, a
     guardrail backstop, and a lifted price cap for that card (§5).
   - A small overpay tolerance (+2 P over our value on CHA dealer buys) lifts Abuela's fill rate from 65 % / 44 % to
     98 % / 91 % (commons / uncommons). Without it the page may stall at 7–8/10.
   - #245, which raised the dealer price cap for completing cards, is **closed unmerged**. Keep it that way.
4. **Packs: buy none.**
   - What a pack pulls never scores ("luck", RULES.md:122). `/api/catalog` publishes the odds: barrio has no rare slot;
     silver's last slot is 86 % rare, 12 % epic, 2 % legendary.
   - Resold to dealers, barrio is worth ≈ its price (21) and silver ≈ 143 vs its 150–188 price. The gold pack's list
     price (420) is above its expected book (410.5).
   - Keep opening free packs. Set `max_packs_per_game_hour` = 0.
5. **Cash and order of buys.**
   - `cash_floor` is **5 on main, not 100** (GUARDRAILS.md:18).
   - The binding limit at 09:00 is the hourly spend cap of 250. Our standing MAL-11 bid survives the close and counts
     against it.
   - Order: revoke that bid; CHA page and the LAT-09 sale; then the epics; El Retiro only if a redeploy happens anyway
     (§4.3).

## Method and data windows actually seen

| Source | Window | Used for |
|---|---|---|
| Postgres `me_snapshots` (t01) | ticks 159–1445, 1,287 rows, read 07:28Z–22:02Z Sat | album, `neg_points`, ladder, luck, our 5 pack opens |
| Postgres `feed_events` | ticks 0–1445, 28,281 events (Fri 20:21Z → Sat 21:43Z) | settlements, `pack.opened` (125), gifts, crafts, schedule |
| Postgres `tape` | 793 settlements | prices per card and source |
| Postgres `leaderboard_snapshots` | 78 refreshes, ticks 610–1430, 18 teams | field-wide page test and its control |
| Keyless GET `/api/schedule`, `/api/clock`, `/api/levels`, `/api/dealers`, `/api/catalog` | one call each, 22:19:26–22:19:31Z (≈1/s) | Sunday schedule, dealer menus, pack odds, value constants |
| Repo `origin/main` 166f9ca5 | — | GUARDRAILS.md, STRATEGY.md, `strategy.py`, `guardrails.py`, `taker.py`, `pack_gate.py` |

- No keyed requests. Railway logs were not used (not needed for this topic).
- Tick 1445 is Saturday's close (`day.closed`), so the last `/me` is the end-of-Saturday state.
- `card_values` and `supply_cards` are empty in the DB, so values come from `/me` snapshots.
- The Friday capture `.local/stream.jsonl` was not used.

Scripts (all read-only, under `docs/research/2026-10-04/collections/`):
- `q.py`: SELECT-only helper.
- `our_pack_pulls.py`.
- `field_pages_test.py`: the field test with its control groups.
- `neg_points_check.py`: values are printed only with `--private`.
- `holders.py`.

## 1. Album at the end of Saturday and the scoring formula

### 1.1 Album, tick 1445 (`me_snapshots`; public parts are on the leaderboard)

| Page | Multiplier | Held | Missing (by rarity) | Page bonus (0.25 × 265 × mult) |
|---|---|---|---|---|
| Lavapiés | 1.6 | 10/10 complete (tick 864) | — | 106.0 |
| Salamanca | 1.3 | 10/10 complete (tick 443; broken at 948, rebuilt at 958) | — | 86.1 |
| Malasaña | 1.1 | 10/10 complete (tick 1212) | — | 72.9 |
| La Latina | 0.5 | 4/10 | 2 commons, 3 uncommons, 1 rare | 33.1 |
| El Retiro | 0.7 | 3/10 | 3 commons, 2 uncommons, 2 rares | 46.4 |
| Chamberí | 0.9 | 0/10 (released Sunday, 0 minted) | 5 commons, 3 uncommons, 2 rares | 59.6 |

- Public: `album_filled` 37 of 50, `pages_complete` 3, rank 9, score 25.65 (negotiating 18.15, market 7.5).
- Private companion: the exact refs and the one duplicate we still hold.
- Masters: no epic or legendary held. `/api/catalog` lists `master_bonus` 0.1, which did not show in any `your_value`
  we read.

### 1.2 Formula

- **Board:** `Σ_r w_r·p_r·R_r / Σ_r w_r·p_r` (RULES.md:124-125).
  - Friday w = 0.5; Saturday and Sunday w = 1.
  - p_r is the share of that day already played.
  - R_r = negotiating_r (30) + market_r (30).
- **Negotiating** (fit, `_night/SCORE_CAPS.md` §3): `30 · Σ_active W_i · min(1, x_i / top3_i) / Σ_active W_i`.
  - W ≈ ladder 7.5 / team trades 10.5 / duels 12, each ±1.5.
  - A part is active when its top-3 mean is above 0.
  - 1 round point ≈ 0.40 final points.
  - Each part counts only up to the top-3 mean of the field (`min(1, …)`). Every "final points" figure below assumes
    our trades part on Sunday is still below that cap. Saturday's cap was ~175–210 raw. Whether `neg_points` resets for
    round 3 is unknown until the first Sunday `/me`. Ranking the buys by P of surplus is correct either way.
- **x_trades = `neg_points`**, reconciled trade by trade (`neg_points_check.py`, all 29 of our settlements):
  - **Team buy:** value of the copy received − price − fee. The buyer pays the fee: ceil(5 % + 1) on the rastro, 0 on
    0-fee venues. 9 buys, residual −2.5 to 0.
  - **Team sale:** price − value of the copy given. 4 sales, residuals 0.0, +1.2, −8.9, +4.6 (unexplained, up to ±9).
  - **Dealer sale below value:** counts the loss. 2 of 2 exact: RET-06 to Chato at 16 vs 17.5 → −1.5 (tick 894);
    SAL-07 to Pilar at 29 vs 118.6 → −89.6 (tick 948).
  - **Dealer sale above value:** 0 (6 of 6: ticks 718, 726, 737, 1081, 1084, 1099).
  - **Dealer buy below value:** 0 (8 of 8, including all three page completers).
  - **Dealer buy above value:** never observed. This decides El Retiro and the size of change B's risk (§4.3).
- **Page bonus:** it lives only in the `your_value` of the last missing card of a page.
  - Before that, a missing card is worth book × multiplier (rares: LAT 35, RET 49, CHA 63).
  - The completer then carries the whole bonus. Saturday's history, kept here as evidence because those pages are
    complete and the values are no longer a reservation price: LAV-10 218 = 112 + 106, MAL-09 149.9 = 77 + 72.9,
    SAL-07 118.6 = 32.5 + 86.1.
  - Copy marginals 1 / 0.25 / 0.1 (`/api/catalog`).
  - No album, page, luck or collection-value term scores.

### 1.3 Worked examples (real Saturday events)

**(a) Page completed through a dealer: 0.** MAL-09 from Pícaros at 61, tick 1212.
- `pages_complete` 2 → 3; `neg_points` Δ 0.
- `ladder_points` Δ 0: level 4's best three were already LAV-09 58, MAL-10 59 and LAV-10 63.
- Negotiating: 15.98 at tick 1210, 15.98 at tick 1220 (Δ 0.00). The +0.23 at tick 1230 is field drift.

**(b) Page completed through a dealer, with a ladder step.** LAV-10 from Pícaros at 63, tick 864.
- Negotiating +0.66 at tick 870.
- The ladder alone explains it: 0.254 → 0.301 is +0.047 lp × ~21 R per lp ≈ +1.0 R2 ≈ +0.66 board. Nothing is left
  for the page.
- SAL-07 rebought from Abuela at tick 958: +0.26 = ladder +0.020 × 19.5 × 0.667.
- Tick 443 (SAL completed with SAL-09 from Chato): +0.10 against a ~0.01 ladder step. This is within the ±0.3 field
  drift, so it is not a clean data point.

**(c) A team trade, the one channel through which value counts.** LAT-10 sold to t12 at 86, tick 1304.
- `neg_points` +55.6 (the seller pays no fee).
- `_night/SCORE_CAPS.md` measured +3.1 R2 ±0.3, net of drift and duel moves (Duels II was running). That is ≈ +2.1
  board on Saturday and ≈ +1.24 final.

**(d) Counterfactual: MAL-09 bought from a team.**
- At 61 P on the rastro (fee 5): +83.9 surplus. We were far below the trades cap then, so at the measured 0.051–0.060
  R2 per P it counts fully: +4.3–5.0 R2 ≈ **+1.7–2.0 final**.
- At a realistic team price of 95 (fee 6): +48.9 ≈ **+1.0–1.2 final**.
- No team listed MAL-09 on Saturday (`_night/CARD_SCOUT.md` §2), so this was missed supply as much as routing.

**(e) Field-wide check, with a control** (`field_pages_test.py`). Excess = Δnegotiating minus the median of all teams
in the same leaderboard refresh. Duel windows: Duels I ticks 459–651, Duels II 1239–1431.

| Group | n | median excess | p10 | p90 |
|---|---|---|---|---|
| Page rise, a card bought from a team in the interval | 11 | **+1.94** | +0.01 | +3.03 |
| … the same, outside duel windows | 7 | **+2.00** | | |
| Page rise, dealer buys only | 15 | +0.46 | −0.01 | +0.74 |
| **Control:** no page rise, a team buy and no team sale, outside duel windows | 34 | **+0.015** | −0.18 | +0.48 |
| Control: the same, inside duel windows | 13 | +0.13 | −0.04 | +0.81 |
| Control: no page rise, dealer buys only, outside duel windows | 47 | +0.50 | −0.12 | +0.99 |

- Examples of team-bought completions: t03 LAT-09 at 88 → +3.03 (tick 730); t06 SAL-09 at 68 → +3.12 (790);
  t12 RET-09 at 84 → +1.94 (900).
- t04 at 1260 and t12 at 1310 fall inside Duels II and are confounded with duel points. They are excluded from the
  "outside duel windows" row.
- **"Team-bought" means the team bought some card from a team in that interval**, not necessarily the card that
  completed the page. A team buy without a page rise moves ≈ 0, so the +1.94 is not ordinary team-buy noise.
- Size check: +1.9 to +3.1 display ≈ +2.9–4.7 R2 ≈ 50–90 P of surplus at the measured slope. For a rare bought at
  68–88 that is only possible if the buyer was credited the bonus (33–106 depending on its multiplier). Without the
  bonus the same trades would be worth −50 to +25 P.
- The `deals` column counts dealer deals too, so the split uses `tape`.
- With n = 11 (7 outside duels) against a control of 34, this supports the effect strongly but does not pin its size.

## 2. Missing pages: cards needed, holders, prices, cost vs points

Prices are Saturday's (`tape`, ticks ≥ 160). "Team" = team-to-team settlements. Holder counts come from a replay of
the public feed (settlements, gifts, Workshop crafts, pack `best`) and are **lower bounds**: commons and uncommons
pulled from packs are invisible until they trade. The per-card table (refs, holders, every print) is in the private
companion.

**Dealer menus** (`/api/dealers`, 22:19Z):
- **Abuela:** commons (list 10) and uncommons (list 25) of released sets; 8 deals per team per hour.
- **Chato:** uncommons (26) and rares (77); 6/h.
- **Pícaros:** rares (63) and epics (162); 6/h. Pícaros is a trickster: our `trickster_accept_fill_share` 0.33 makes
  the taker take their ask only at or below ≈ 54 on rares.
- **Banco:** legendaries (585). Banco buys epics and legendaries.

Dealers mint a new set at release: Abuela sold RET-06 at tick 166 and RET-07 at tick 182, 6 and 22 ticks after RET's
`set.released` (tick 160). So CHA singles should be at the stalls from the first tick of round 3.

Saturday prices, all sets:

| Source | Common | Uncommon | Rare |
|---|---|---|---|
| Abuela sells | 8–15, median 9 (n=46) | 20–29, median 23 (n=45) | — |
| Chato sells | — | 26–61, median 31 (n=13) | 75–96, median 87 (n=25) |
| Pícaros sells | — | 50 (n=1, off-menu) | 48–67, median 56 (n=52); 15 of 52 at ≤ 54 |
| Team-to-team, LAT/RET | 0–49, median 8 (n=32) | 10–30, median 20 (n=21) | 55–88, median 77 (n=10) |

**Can the CHA dealer buys clear our value cap?** `official_value_margin` = 0 means every buy must be at or below our
`your_value` (CHA: common 9, uncommon 22.5, rare 63).

| Rarity | Abuela fills at ≤ our value | … at ≤ value + 2 | Release-day analogue (RET, ticks 160–400), ≤ value + 2 |
|---|---|---|---|
| Common | 30 / 46 = 65 % | 45 / 46 = 98 % | 15 / 15 |
| Uncommon | 20 / 45 = 44 % | 41 / 45 = 91 % | 17 / 18 (median 24) |

- **Rares:** Pícaros fills ≤ 63 in 50 of 52, but the trickster rule takes their ask only at ≤ ~54 (15 of 52 fills).
  Saturday's PAGE_CEILING_FIX shows the taker walking at 60–63 finals.
- **CHA rares are the real feasibility risk.** Our 4 Pícaros rares on Saturday (58, 59, 63, 61): three closed before the
  trickster rule was deployed (`trickster_accept_fill_share`, commit 85341571, merged a0eb65ed at 19:19, ≈ tick 1090).
  The one after (MAL-09 at 61, tick 1212) closed only after the taker's own thread had walked under that rule; what
  closed it was not determined. Chato's rares (75–96) sit above CHA's 63. Recommendation, one line: for rares of a
  planned page, let the trickster share go to 1.0 (take a Pícaros ask or final up to our value 63). The buy is still
  at or below our value, so it costs 0 in score and only cash; the inspector's wrong-card check stays.
- **Time is not the binding constraint.** About 13 Abuela threads, one at a time, at ~1.5–3 min each with 15 s ticks,
  is ≲ 1 h. The binding constraints are price and Pícaros's rule.
- **If the page is stuck at 7–8/10 when the stalls close:** the bonus never scores, and the ~150–200 P spent bought
  only ladder deals. Those still count for round 3's best three per level, and cash left at the end scores nothing.
  So the downside is the lost chance to use that cash on the epics, not lost points.

**Cost to complete vs what can score:**

| Page | Cards to buy | Cheapest route, P | Value added (base + bonus) | Surplus on the team-bought last common | Final points (0.02 / P, below the cap) | Code needed | Verdict |
|---|---|---|---|---|---|---|---|
| **CHA ×0.9** | 5C 3U 2R | Abuela C ~9 ×4, U ~23 ×3, Pícaros R ~56 ×2, last C from a team ~10 → **≈ 225** | 238.5 + 59.6 | 9 + 59.6 − ~10 − fee 2 ≈ **+57** | **≈ +1.1–1.2** | **A**; **B-CHA** (+2 overpay) recommended | **Do it** |
| RET ×0.7 | 3C 2U 2R | Abuela C ~9.5, U ~22, Pícaros R ~57 → **≈ 187** | 154 + 46.4 | 7 + 46.4 − ~8 − 2 ≈ **+43**; ≈ **+13** if above-value dealer buys count as losses (−30) | +0.3 to +0.9 | A + **B** (every dealer price is above our value: C 7, U 17.5, R 49) | Only if a redeploy happens anyway (§4.3) and cash is left |
| LAT ×0.5 | 2C 3U 1R | C 4–9, U 14–22, Pícaros R 60 → **≈ 110–144** | 82.5 + 33.1 | ≤ +31 | ≤ +0.6 | A + B | **No.** GUARDRAILS.md:48 already orders LAT-09 sold at ≥ 90 to a team (value 35): **+55** by itself beats the whole LAT bonus, and the sale leaves the page needing 2 rares |
| LAV / SAL / MAL | complete | — | — | — | — | — | Masters below |

**Why the last card should be a common:**
- The bonus is the same whichever card is last.
- A common's surplus is the largest, because its base value is closest to its price: ≈ +57 for a CHA common against
  ≈ +46 for a rare at ~77.
- Commons have the deepest team supply (print run 300).

**Masters and off-page epics of our complete sets.** An epic's `your_value` = book 180 × multiplier. Our caps and the
named holders are in the private companion.

| Epic | Team holders (feed) | Saturday prints (public) | Our route | Surplus at the route's cap, after the rastro fee |
|---|---|---|---|---|
| **LAV-11** | 2 teams, each bought from Pícaros at 155 | Pícaros 147–155; Pilar paid 140; Banco paid 120 | team buy ≤ `max_price_epic` | **≈ +35** (more if it fills lower or on a 0-fee venue) |
| MAL-11 | 3 teams | one team paid another 195; Pícaros 128–150 | standing human approval | positive by construction (the approval cap sits below our value); figure in the companion |
| SAL-11 | 4 teams (9 of 9 minted: sold out) | one team paid another 207; Pilar 179–199 | team buy ≤ value − 10 | ≈ 0 to +15 |

A dealer epic buy scores 0, like any dealer buy. Only the team route counts, which is what `buy_targets` (#255, on
main) does on a human approval.

## 3. Packs: published odds, real prices, EV

### 3.1 Odds (`/api/catalog`, keyless, 22:19Z) and what was seen

| Pack | Slots (catalog) | Expected book (catalog) | Seller (menu) | Saturday prices paid | Opens seen (Fri+Sat) | Our opens |
|---|---|---|---|---|---|---|
| `sobre_barrio` | C, C, C 0.75 / U 0.25 | 33.8 | Abuela, list 26, opening 30, 3/team/h | 19–26, median 21 (n=11); Friday 17–30, median 22 (n=32) | 77, no rare (as the slots say) | C C U, C C C |
| `sobre_plata` | C, C, U, U, R 0.86 / E 0.12 / L 0.02 | 160.8 | Chato, list 150, opening 188, 2/team/h | 162 (n=1); Friday 181 (n=1). Only 2 of 31 opened silver packs were bought from Chato | 31: 28 rare + 3 epic best | C C U U R, twice |
| `sobre_bienvenida` | C, U, U 0.6 / R 0.4 | 78.0 | free welcome pack | — | 17: a rare in 6 | C U R |
| `sobre_oro` | U, U, R, R, E 0.85 / L 0.15 | 410.5 | Pilar 420 / Banco 420 (openings 504 / 546), 1/team/h | none sold | 0 | — |

- **The draw:** each slot picks a rarity by its odds, then a released card of that rarity uniformly, skipping
  printed-out cards (`strategy.pack_cards`, B9 #109).
- **The set mix agrees:** silver's "best" cards on Saturday were SAL 9, MAL 7, LAT 7, RET 5, LAV 3 (n=31). On Sunday,
  with 6 sets, a pull is CHA about one time in six, and which CHA card is random.
- **Consistency check:** `/me` `luck` moved by exactly Σ book of the pulls minus the catalog's expected book. Both
  barrio opens give 33.8; the silver opens give 160.7 and 160.8. So luck is book-based and never scored.

### 3.2 EV in three currencies

Resale is valued at Saturday's dealer buy medians:
- Abuela: commons 6, uncommons 19.
- Pilar: rares 75; epics 187 (140–199, n=4).
- Legendaries: ≈ 300, an assumption. Banco is the only buyer and no sale was seen; 300 is about 450 × Banco's epic
  ratio.

| Pack | Price | Book EV | Cash EV, resold to dealers | Score EV |
|---|---|---|---|---|
| barrio | ~21 (19–26) | 33.8 | 2 × 6 + 0.75 × 6 + 0.25 × 19 ≈ **21**, and it uses up Abuela's 8 deals per hour | 0 for the pulls. Only resale to teams above our value scores; duplicates are worth 0.25× or less to us (commons +1 to +4 each, if a team buys) |
| plata | 150–188 | 160.8 | 12 + 38 + 0.86 × 75 + 0.12 × 187 + 0.02 × 300 ≈ **143** | 0 for the pulls; a resold rare scores only if a team pays above our value |
| oro | 420 (opening 504–546) | 410.5 | 38 + 150 + 0.85 × 187 + 0.15 × 300 ≈ **392** | 0 for the pulls; list price is above even its book |

**Against singles**, for the cards we actually want on Sunday:
- **A specific CHA common:** Abuela ~9 P. A barrio pack holds that card with p ≈ 2.75 / 30 ≈ 0.09, so ≈ 230 P per
  specific card, or ≈ 46 P per random CHA common.
- **A specific CHA rare:** Pícaros ~56 P. Through silver, p ≈ 0.86 / 12 ≈ 0.07, so ≈ 2,100 P.
- **A pack pull that completes a page** is like a dealer buy: the bonus never scores. That is why change A treats a
  pulled reserved card as a reason to re-reserve.

**Against trading:** a swap that brings in the completing card for one of our duplicates is the cheapest route of all
(surplus = its value − our duplicate's value, no cash). We hold one duplicate at close, so this route is thin.

The current code values a pack at private value (`strategy.pack_ev` with `keep_value`: copy value plus page-bonus
share), not at score. A Jev "yes" on that EV could buy packs that score 0. Saturday shows **0 pack-buy decisions**
(only 4 `pack_open`), so this has not bitten yet. Our own contents are n = 5, but the conclusion rests on the published
odds, not on that sample.

## 4. Cash plan, `cash_floor` and the Sunday speed run

### 4.1 Guardrails on main (166f9ca5)

| Rule | Value | Note |
|---|---|---|
| `cash_floor` | **5** | Not 100: 100 was #71's value at 09:44, then 50 → 20 → 5 by 17:20 (GUARDRAILS.md:18). The v19 bond is posted, so `venue_bond_reserve` adds nothing |
| `max_spend_per_game_hour` | 250 | **Binding.** A standing bid's cash counts against it while it stands (BUY_TARGETS.md; a `decisions` row at tick 1430 shows a buy refused as "spend … > max_spend_per_game_hour 250"). Our standing MAL-11 `buy_targets` bid (re-posted by the maker until the approval lapses at tick 1657; its price is in the private companion) survives the close, so at 09:00 only part of the 250 is left for dealer buys |
| `max_price_common / uncommon / rare / epic` | 12 / 26 / 95 / 240 | `guardrails.check` (guardrails.py `check`, the `max_price_for` line) applies these to every buy. A team that sees CHA chased may ask 15–25 for a common (still +40–50 surplus). Only change A(iv) lets the completing card exceed 12 |
| `official_value_margin` | 0 | Blocks every RET/LAT dealer buy (dealer prices sit above book × 0.7 / 0.5). For CHA it cuts Abuela's fill rate to 65 % (commons) and 44 % (uncommons): change B-CHA |
| `no_buyback_ticks` | 480 | Not a page issue, but its check (`_buyback_violations`, guardrails.py:1157, called from `check`) is the model for change A(iii): one check that covers every buy path |
| `human_approval_above` | 250 | Buys ≤ 249 need no approval; `buy_targets` treats approvals as orders |
| `max_price_pack` / `max_packs_per_game_hour` | 20 / 3 | Recommend `max_packs_per_game_hour` = 0 (§5) |

- **Cash at the close:** redacted (companion). It is enough for the CHA page plus one epic.
- **Sunday grant:** 150 P at h16.7.
- **Cash itself never scores** ("Only deals score, never cash you hold", feed announcement at tick 1201). Unspent cash
  at 15:00 is worth 0; the only reason to keep a buffer is to keep buying until the stalls close.

### 4.2 Two clock scenarios (check `GET /api/clock` → `t_hours` at 08:55)

- `/api/schedule` (22:19Z) lists CHA's release, round 3, Saturday's close and Sunday's opening all at **h16.65**.
  The clock stopped at **h13.367**.
- Saturday opened with a jump: the round-2 grant fired at tick 165, 3 minutes after the doors.
- `t_hours` advances 1:1 with unpaused wall time: 1,286 ticks × 30 s = 10.72 h = 13.37 − 2.65.
- The `sat-logs-eggs` session reads the schedule the same way (round 3 at 09:00, Duels III ~11:00; `_sat-review/STATUS.md`
  00:25).

| Event | JUMP (09:00 = h16.65, likely) | RESUME (09:00 = h13.37) |
|---|---|---|
| Market Tests at h14.65 (hard) and h15.0 | past due: may fire at or right after 09:00 | ~10:17 and ~10:38 |
| Round 3 + CHA release | 09:00 | ~12:17 |
| Grant 150 P | ~09:03 | ~12:20 |
| Sunday-morning team trades count for Saturday's round? | **No**: round 2 ended at the Saturday close | Yes, until ~12:17 |
| Duels III (h18.65) | ~11:00 | ~14:17 |
| Stalls close (h21.65) | **~14:00**: every dealer buy before then | after the 15:00 close: dealers until 15:00 |
| Scores freeze (h22.65) | 15:00 | 15:00 (wall) |
| Time for the CHA page | 5 h of stalls | ~2.7 h |

- `KNOWLEDGE_SAT_EVENING.md`'s "round 3 ~11:34" came from the clock as it stood at 19:05 and is stale.
- **Deploy window for change A:** at 08:55 also read `/api/schedule` for past-due Market Tests. Under JUMP the two
  benches may fire right at 09:00. Never deploy within 10 ticks of a Market Test (2.5 min at 15 s ticks), so deploy
  before 08:50 or after the second bench ends.

### 4.3 Speed-run constraints that bind the plan

- **15 s ticks.** One accept per team per tick, shared with duels. 12 offers per tick, 30 open offers, 6 open
  threads, one conversation per dealer.
- **Request budget:** Sunday is conditional (MORNING.md: 4.73 req/s at the ceiling). Run the CHA plan inside the
  taker's existing dealer loop. No extra `dealer buy` CLI loops.
- **Dealer quotas:** CHA needs ~7 Abuela deals (one hour of her 8/h, shared with the taker's other Abuela buys) and
  2 Pícaros deals. Under JUMP, finish the dealer part by ~11:00, before Duels III takes accept slots.
- **Hourly spend cap 250, standing bids included.**
  - At 09:00, read `/api/me/offers`.
  - Revoke MAL-11 before the CHA dealer buys: MCP `revoke` card=MAL-11 side=buy, or let the approval lapse at tick
    1657 ≈ 09:53 under JUMP.
  - Approve the epics only once CHA's dealer spend is booked. An approved epic parks up to 240 P against the cap for
    the bid's whole TTL (480 ticks = 2 h at 15 s), not just the hour it is posted.
  - Alternative: raise `max_spend_per_game_hour` to ~500 for Sunday (one GUARDRAILS.md line, a redeploy). Risk: a
    buggy loop could spend twice as fast. Cash is the real limit anyway.
- **El Retiro needs a probe, and the probe needs a redeploy.**
  - The probe: buy one RET common from Abuela at 9 (value 7, 2 over), then read `/me` `neg_points` at the next tick
    that is a multiple of 10.
    - −2 → above-value dealer buys count as losses: RET nets ~+13, skip it.
    - 0 → they don't: RET nets ~+43.
  - `official_value_margin` 0 is enforced in `guardrails.check` on every buy path, the hand `dealer buy` CLI included,
    and an approval never loosens another cap. So the probe needs a GUARDRAILS.md edit, which means a redeploy.
  - Do it only together with change B-CHA, which lifts the margin anyway; the first CHA overpay is then the probe.
    Read it on a 10-tick score window in which no other settlement of ours lands (no team trade, no sale), or the
    −1/−2 signal is confounded.
  - Never use a raw SDK call outside the guardrails.
- **First `/me` on Sunday:** check whether `neg_points` and `duel_points` reset to 0 at round 3 (the ladder did at
  round 2).

## 5. Ranked Sunday buy list and pack policy (by expected final points)

| # | Action | Cash | Expected (below the trades cap) | Change needed (file · parameter) | Risk |
|---|---|---|---|---|---|
| 1 | **CHA page: 9 dealer cards, the reserved common from a team** | ≈ 225 | ≈ +57 surplus → **≈ +1.1–1.2 final**, plus up to 9 ladder-deal candidates for round 3 | **Change A** + **B-CHA** (below), deployed outside the bench windows (§4.2) | medium: deploy-day code (~250 lines with tests). CHA chasers at ×1.6 may lift prices. A team may not sell the reserved common in time: the bid stands, and buying it from a dealer would score 0 anyway |
| 2 | **Sell LAT-09** to a LAT chaser at ≥ 90 (already ordered: `protect_page_exceptions`) | +90 in | +55 → **≈ +1.1 final** | none | low |
| 3 | **LAV-11 from a team** | ≤ `max_price_epic` | ≈ +35 after the fee → **≈ +0.7 final** | none: `uv run bazaar approve LAV-11 --buy --max <cap> --ttl-ticks 480` (cap in the companion); after row 1's dealer spend | low. Main lacks #255's review fixes, so the taker half of `buy_targets` is effectively off (BUY_TARGETS.md); the maker's public bid still works |
| 4 | **MAL-11** (approval lapses at tick 1657 ≈ 09:53 under JUMP) | approval cap (companion) | positive surplus after the fee → **≈ +0.4–0.8 final** | revoke at 09:00 (spend cap), re-approve after row 3 if cash and cap allow | low |
| 5 | El Retiro page | ≈ 187 | +0.3 to +0.9 final, decided by the probe | Change A + **Change B** (RET) | medium-high: an override of a safety rule, plus cash |
| — | La Latina page | — | worse than row 2 | none | — |
| — | **Packs** | 0 | 0 score; barrio ≈ break-even, silver ≈ −7 to −45, gold ≈ −28 or worse in cash | **GUARDRAILS.md `max_packs_per_game_hour` = 0** (`guardrails.check` refuses a pack buy when `packs_last_hour >= max_packs_per_game_hour`, so 0 refuses all). Keep `open_sealed_packs` = true; change A handles a pulled reserved card | none |

Under JUMP, rows 1–4 come to ≈ 225 + up to 240 + the MAL-11 approval cap of buys, against Saturday's leftover cash + 150 + 90 (the LAT-09
sale). That fits in cash, but not in one game hour of spend cap:
- 09:00: revoke or let lapse the standing MAL-11 bid.
- Rows 1 and 2 first (09:00–~10:30).
- Then row 3, then row 4 if cash and cap allow. One epic bid at a time.
- Row 5 last.

### Change A: one reserved card per page, bought only from a team, only last

**What it guarantees.** For a page we plan to complete (CHA on Sunday), exactly one missing card, the reserved card,
is never acquired by any route we control until the other 9 page cards are held. Once they are, it is bought only from
a team. If the reserved card reaches us early by a route we don't control (pack pull, gift), another missing common is
reserved at once.

**(i) One source of truth: `strategy.reserved_completer(m, set_code)`** in `src/bazaar_agent/strategy.py`.
- Candidates: the missing commons of the page; if none is missing, every missing page card.
- Pick: the candidate with the most team supply signals (live team asks on the boards, plus feed holders as in
  `holders.py`), ties broken by ref. Commons first, because the surplus is largest and supply deepest.
- **Sticky:** the choice is stored once, in a shared-ledger row (`page_plan:<SET>` = ref) written by the taker. The
  maker, the guardrails and the CLI read the same row, so two processes can never reserve different cards. It is
  re-picked only when the reserved card is no longer missing.
- Fail closed: if the row can't be read, no buy of any missing page card of that set goes out that tick.
- Page plans are listed in STRATEGY.md, e.g. `page_plan_sets` = CHA.

**(ii) Every route that can bring the card in, and what change A does about it:**

| Route | Code path (origin/main) | Before 9/10 | At 9/10 |
|---|---|---|---|
| Dealer ladder / final / lift / probe | `strategy.buy_move` → `dealer_buy` (strategy.py:594-600), `ladder_alternates`, `taker._open_one`, `dealer_final_lift` | skip the reserved card | skip: never from a dealer |
| Public team bid | `buy_move` falls through to `team_buy` when no dealer quote exists (strategy.py:560-600); the maker posts it | **no bid** (today it would bid at the tape from the first tick) | bid up to official value − `completer_min_surplus` (iv) |
| Board ask accept | `taker.ask_candidates` (`src/bazaar_agent/agents/taker.py:214`) takes any team ask ≤ value − `min_buy_surplus` for any missing card | skip the reserved card | accept up to official value − `completer_min_surplus` |
| Team swap (desk or hand `bazaar swap`) | team desk / `trade_desk` swap planner; the card a swap brings in | refuse a swap that brings it in | allowed (a team trade: scores) |
| Hand CLI `dealer buy` / `sell bid` | `cli.py` | refused by (iii) | `dealer buy` refused; `sell bid` allowed |
| Free pack opened | `taker._open_pack` (`open_sealed_packs`) | not blocked: the pull is luck, and we hold 0 sealed packs at close (the Sunday grant is cash only). If it pulls the reserved card → re-reserve | same: re-reserve if a common is still missing |
| Workshop craft | taker crafts (GUARDRAILS.md:40, picks "the triple whose pull may fill a missing page slot") | cannot pull a common (three commons → one uncommon), so it cannot hit a reserved common. Change A only stops the craft planner from targeting the set's last missing slot when the reserved card is not a common | same |
| Gift / egg | `gift.given` (Abuela gifted commons on Friday) | not controllable → re-reserve | — |
| Duels | move no cards | — | — |

**(iii) Guardrail backstop in `guardrails.check`**, next to `_buyback_violations` (guardrails.py:1157), which already
covers every buy path (dealer bid or final, board accept, maker bid, the card a swap brings in). A new
`_completer_violations(action, ctx, rules)` refuses:
- any buy of a reserved ref while its page still misses more than 1 card;
- a dealer buy of a page's last missing card. `Action.counterparty` is None for every non-team action (packs, Workshop
  crafts, duels too), so the rule keys on `action.kind` in (`buy`, `accept_buy`, `bid`), a page-card rarity
  (common/uncommon/rare) and `counterparty is None`. It must never refuse a pack open or a craft;
- a dealer buy that would leave a planned page with no missing common while the reserved card is not held. This is
  the race guard if the sticky row lags.
- Inputs `Context` lacks today:
  - `pages: dict[set, (have, of)]` from `/api/me` `pages`, filled in `context_from`;
  - `reserved: dict[set, ref]` from the `page_plan` ledger row;
  - the page refs per set from the catalog: `SET-01..SET-10` are the page cards, `cards.page` in the DB.
  - With any of them unread, a send of a page-card buy in a planned set is refused and a dealer thread holds (fail
    closed), as `no_buyback_ticks` does.
- New GUARDRAILS.md rule: `completer_from_teams` = true.

**(iv) Price cap for the completing card, in `guardrails.check`, not in strategy.** `max_price_for(rarity)` is applied
to every buy, so a strategy-only lift would be refused.
- New rule `completer_min_surplus` = 30: a **team** buy (bid, accept, or the card a swap brings in) of a page's last
  missing card may exceed `max_price_<rarity>` up to official value − 30, fee included.
- Its official value already carries the bonus, so at least +30 surplus is guaranteed. A CHA common could go to ~38.
- Every other cap still binds: cash floor, hourly spend, counterparty share, `no_buyback_ticks`.

**(v) Tests, one per route** (`tests/test_page_plan.py`, plus additions to `test_guardrails.py` and `test_taker.py`):
1. `buy_move` returns no move for the reserved card before 9/10, neither dealer nor `team_buy`.
2. `ask_candidates` skips a team ask for the reserved card before 9/10, and takes it at 9/10 at a price above
   `max_price_common` and ≤ official − 30.
3. `guardrails.check` refuses a dealer buy of the reserved card (and of any last card); a board accept, a maker bid
   and a swap-in of the reserved card before 9/10; and allows a team accept at 9/10 above `max_price_common`.
4. A team-desk swap proposal that brings in the reserved card before 9/10 is not posted.
5. The hand `dealer buy` CLI is refused with the completer reason.
6. A pack pull or gift of the reserved card → `reserved_completer` picks another missing common; the dealer path
   still skips the new one.
7. All commons held early → the reserved card becomes the remaining missing card: no dealer buy, the team bid stands.
8. Two processes read the same sticky reservation (the ledger row), and an unreadable row refuses page-card buys in the
   planned set.
9. A Workshop craft of three commons is unaffected; the craft planner doesn't target the last slot when the reserved
   card is an uncommon or rare.

**What happens at 9/10 if the missing card is not a common with team supply** (all commons arrived early): the team
bid stands at up to official − 30. A dealer never sells it to us (iii). If no team sells by 15:00, the page stays at
9/10. That costs nothing extra: a dealer-bought completer would have scored 0 anyway.

**Size:** ~250 lines with tests, which makes it a risky deploy-day change.

**No clean manual fallback exists.** `protect_page_sets` (CHA is already in it) only stops sales. Nothing today stops
the taker from buying one particular card. A human would have to stop the taker's dealer buys altogether at 8/10 CHA,
which also stops every other buy. That is why change A is listed as needed, not optional.

### Change B-CHA: a small overpay tolerance (recommended with A)

STRATEGY.md `page_plan_sets` = CHA plus GUARDRAILS.md `page_plan_overpay` = 2.
- For non-reserved cards of a planned page, a dealer buy may exceed our `your_value` by up to 2 P (rares 0: Pícaros
  fills fit).
- It lifts Abuela's fill rate from 65 % to 98 % (commons) and from 44 % to 91 % (uncommons), Saturday fills (§2).
- Worst case, if above-value dealer buys count as losses: 7 cards × 2 = −14, against +57.
- The first such buy is also the probe that settles the dealer-buy question (§4.3).
- It lives in the same `guardrails.check` value-cap line (`official_value_margin`), keyed on the planned set.

**Change B (El Retiro):** the same rule with `page_plan_sets` = CHA,RET and a larger overpay. Dealer prices sit 2–8 P
above our RET values per card. Only if the probe reads 0.

**#245 is closed, unmerged (closed 22:34Z Saturday).** Keep it that way. It lifted a completing card's dealer ceiling
to min(value − 2, 95), which spends up to 95 at a dealer on a card that then scores 0 there.

## Open questions for Marius

1. Change A on deploy day (~250 lines with tests, §5): merge before 08:50, after the morning benches, or not at all?
   Without it, the CHA bonus is very likely forfeited (the taker buys all 10 cards from dealers), and there is no
   switch for a clean manual fallback.
2. B-CHA (`page_plan_overpay` = 2 on CHA): OK? It is the fill-rate fix, and its first buy settles the dealer-buy-loss
   question for free.
3. LAV-11 at the epic cap, after the CHA dealer buys: approve? It is the largest single surplus available outside the
   page.
4. `max_packs_per_game_hour` = 0: OK? It also stops a Jev "yes" on private-value pack EV.

## What I could NOT verify

- **Whether a dealer buy above our value counts as a loss in `neg_points`.** Never observed. It sizes the risk of B-CHA
  and decides El Retiro.
- **Whether a pack bought from a dealer counts as a ladder deal.** We bought none, and other teams' ladder is not
  public.
- **Whether `neg_points` and duels reset at round 3** (the ladder reset at round 2). Known at Sunday's first `/me`.
- **The two team-sale residuals** (−8.9 on SAL-10 at tick 376, +4.6 on LAT-10 at tick 1304) are unexplained; team
  buys reconcile within 2.5.
- **Holders are a lower bound.** Pack-pulled commons and uncommons stay invisible until they trade, and the replay
  assumes the monitor's feed has no gaps (not audited).
- **Sunday CHA dealer prices and the legendary resale price.** Saturday's prices are the proxy; ×1.6 CHA teams may
  lift CHA prices, and the legendary's ~300 is an assumption.
- **Which clock scenario happens** (§4.2): read `t_hours` and `/api/schedule` at 08:55.
- **The size of the team-vs-dealer effect** in §1.3(e). It rests on n = 11 (7 outside duels) against a control of 34,
  with drift removed by a median, not a model.
- **Whether change A's sticky-row design fits the shared ledger** as is. The row kind is proposed, not checked against
  `ledger_pg.py`.

## Review items and how they were handled

| # | Item | Done |
|---|---|---|
| 1 HIGH | Change A must block every route | Rewritten: one sticky reserved card, a route table, re-reservation, the 9/10 case, a `guardrails.check` backstop with its `Context` inputs named, 9 tests (§5) |
| 2 MED | Pack odds are published | §3 rests on `/api/catalog`; the CI and the inferred 0.19 are dropped; silver EV redone with E 0.12 / L 0.02; gold added |
| 3 MED | Value cap binds commons too; feasibility | Fill-rate table at value and value + 2, the release-day analogue, the Pícaros rule, the stuck-at-7/8 outcome; B-CHA added (§2, §5) |
| 4 MED | Cap lift must be in the guardrail | Change A(iv) `completer_min_surplus` in `guardrails.check`, with tests 2–3 |
| 5 MED | Fees | Recomputed: LAV-11 ≈ +35, MAL-11 (companion), MAL-09 counterfactual +83.9 / +48.9, CHA last common +57. LAT-09 moved to row 2 |
| 6 MED | Reservation prices | Epic values, caps and named holders moved to the companion. Argued, not moved: the three Saturday completer values (218 / 149.9 / 118.6). Those pages are complete, so the values are no longer a buy price, the numbers follow from repo multipliers, and they are the evidence for the bonus mechanics |
| 7 LOW | Field control | Added to `field_pages_test.py` and §1.3(e); duel-window cases marked |
| 8 LOW | `bonus_at_stake` | The sentence is gone; change A counts the missing page cards from `/me` `pages` |
| 9 LOW | Stale refs | `agents/taker.py:214`; #245 closed unmerged; base commits noted |
| 10 LOW | Minor numbers | Pilar epics 140–199, median 187; Pícaros' one uncommon sale added |
| 11 LOW | 0.02 / P is conditional | Stated once in §1.2 and in the §5 header |
| 12 LOW | Deploy window | §4.2: read `/api/schedule` at 08:55 for past-due benches; deploy before 08:50 or after them |
| Re-check 6 | MAL-11 cap still derivable | Removed the bid price, the cap and the surplus figure from §2, §4.1, §5 row 4 and the §5 footer; they are in the uncommitted companion `_sat-review/private/collections-private.md`. The branch history was rewritten into one commit (never pushed), so no earlier commit holds them |
| N1 | CHA rares feasibility | §2: 3 of 4 Saturday Pícaros rares predate the trickster rule; recommend share 1.0 for planned-page rares |
| N2 | Probe confounding | §4.3: read on a window with no other settlement of ours |
| N3 | `counterparty is None` | §5 (iii): key on buy kinds and page-card rarity, never refuse packs or crafts |
