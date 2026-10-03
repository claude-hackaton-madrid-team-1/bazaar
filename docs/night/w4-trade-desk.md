# W4 · Trade desk (night of 3 Oct 2026)

Draft PR #79, stacked on #72 (`fix/cash-spend-accounting`). Nothing touched the live game. Inputs: the public feed from the shared DB (read-only, 3,733 events, ticks 0–159), our `agent.me` at tick 149 (checked against the DB snapshot at tick 159: the same 20 assets, cash 353, no event of ours after tick 149), and the catalog and venues fixtures.

## 1. Rival affinity map (`affinity.py`, `bazaar affinity`)

Every team holds the same six multipliers, shuffled. So the model computes, for each team, a posterior over the 720 possible assignments:
- **Interest:** buys, bids, dealer topics, sells and listings. Reprices are counted once, and the evidence is damped (sign × log1p) so that a bot repeating one policy counts as one decision.
- **Prices:** a buy or bid above `book × a × 1.25` makes multiplier `a` unlikely. A 20 % noise floor keeps one overpaying bot from deciding the result.

Calibration is a time split (fit on ticks < 60/80/100, then predict the set each team pushed hardest afterwards):

| model | log loss | Brier | top set hit | event AUC |
|---|---:|---:|---:|---:|
| uniform | 1.386 | 0.188 | 25 % | 0.50 |
| **damped, β 0.5 (default)** | **1.140** | **0.162** | 17/41 (41 %) | 0.63 |
| undamped, β 0.5 | 1.285 | 0.171 | 21/41 | 0.63 |

- The undamped model hits the top set more often but is overconfident.
- Between ticks < 80 and all of Friday, 11 of 17 teams keep the same top set.
- On our own team the model gives LAV 0.55; LAV is our ×1.6 set (STRATEGY.md).

| P(top set) ≥ 0.5 | teams |
|---|---|
| LAV | t05 0.78 · t10 0.72 · t14 0.70 · t04 0.58 (also t07 0.43, t09 0.38) |
| MAL | t12 0.72 (t08 0.42, t17 0.46 split with SAL) |
| LAT | t15 0.52 · t18 0.51 |
| SAL | t02 0.56 · t06 0.52 (t13 0.45, t16 0.47, t17 0.46) |
| RET, CHA | nobody. They were not released Friday, so their columns are priors. t09 (0.35) and t11 (0.33, no signal at all) are the likeliest RET/CHA chasers |

Other teams read the board the same way: t05 and t18 sent addressed LAT offers to t15.

## 2. Per-counterparty cap (#14)

- **New guardrails** in GUARDRAILS.md: `max_counterparty_share` (default 1.0 = off, so behaviour today is unchanged) and `counterparty_cap_base` (200).
- **The rule:** `check()` refuses a team trade when the counterparty's volume would pass `share × max(our volume + price, base)`. That volume is what the team settled with us (public settlements, notional) plus every open offer of ours it could take.
- **Public offers:** an offer anyone may take counts against every team, so the check is a worst case.
- **Fails closed:** when our volume was not read, or when the maker is a board pseudonym the feed never resolved.
- **Maker:** if the public offer would break the cap, it addresses the offer (`to`) to the strategy's counterparty with the most room.
- **Taker:** names the board maker from `offer.listed`.
- **CLI and runtime:** `bazaar sell list/bid` and the runtime's sell tools read our volume when the cap is on. `sell list/bid --to tNN` addresses an offer to one team.
- 18 tests (plus 13 for the affinity map and 41 for the trade desk).

## 3. The 09:00 dry-run plan (`trade_desk.py`, `bazaar trade-plan`)

How the plan is built:
- **Prices:** each trade is priced inside the expected pie (we take at most half of it), at the price with the best `our surplus × P(the counterparty's value clears it)`.
- **Bids:** a bid never beats a dealer's median fill or a guardrail cap.
- **Swaps:** a cash leg splits the pie.
- **Selection:** an exact branch and bound (it finished, so the result is proven best) picks the plan with the best expected surplus in which no counterparty passes 25 % of the planned volume.
- **Selection pool:** the search sees at most 120 candidates (4 per copy or wanted card). It runs in 0.3 s on Friday's data.
- **Posting:** every trade is posted through `guardrails.check()` with GUARDRAILS.md as it is, on top of our open offers and this game hour's spend. A trade it refuses is replaced by the next best plan.
- **What-if:** while the cap is off, the plan also reports how it would post at cap bases 200 and 400. Today's 7 trades pass at both.

To regenerate the plan (reads only, writes `.local/night/trade-plan.{json,md}`):

```
uv run bazaar trade-plan
```

The plan:

| # | kind | trade | counterparty (posted to) | fee (theirs) | P(fill) | volume |
|---|---|---|---|---:|---:|---:|
| 1 | bid | 22 P for any LAV-08 | t03, which holds 2 (anyone) | 3 | 1.00 | 25 |
| 2 | bid | 24 P for any SAL-08 | t04 (anyone) | 3 | 0.54 | 25 |
| 3 | bid | 10 P for any MAL-02 | t06 (anyone) | 2 | 1.00 | 10 |
| 4 | bid | 5 P for any LAT-04 | t06 (anyone) | 2 | 1.00 | 10 |
| 5 | swap | LAT-03 #7 + 14 P for MAL-08 | t08 (thread) | 2 | 1.00 | 35 |
| 6 | swap | SAL-03 #1 + 7 P for MAL-07 | t17 (thread) | 2 | 1.00 | 35 |
| 7 | swap | MAL-01 #4 for SAL-05 + 1 P | t12 (thread) | 2 | 0.93 | 20 |

**Totals:**
- **Expected surplus for us:** +79.9 P (+88.8 P if everything fills), on 160 P of volume.
- **Counterparty shares:** t08 22 %, t17 22 %, t03 16 %, t04 16 %, t06 12 %, t12 12 %.
- **Cash:** bids and cash legs promise 82 of the 83 P above `cash_floor`.
- **Checks:** every trade has surplus for both sides; 0 checks fail.

**Robustness:** I re-scored the same 7 trades under other maps. Expected surplus stays between +73.4 and +81.2 P:
- β 0.25 or 1.0, or undamped evidence;
- a map fitted on ticks < 80 only;
- no information at all (the uniform prior).

Most trades clear for any multiplier (duplicates, swaps with a cash leg). So the map earns its keep in choosing counterparties and prices, not in the fill estimate.

**Why 4 listings + 3 proposals, not 12 + 3:**
- **Cash:** only 83 P sits above `cash_floor`.
- **Commons:** `sell_min_surplus` 5 with a fair split rules out every common, because the pie is under 10 P.
- **The 25 % rule:** it holds back LAT-09 (70 P on a 160 P plan). The best plan without it is +108.2 P, so the rule costs 28 P. The two biggest trades it holds back:
  - LAT-09 + 59 P → LAV-09 with t07, +52 P expected, 140 P notional;
  - LAT-08 + 8 P → LAV-08 with t03.
- **With no cash for bids** (`--cash-budget 0`), no plan meets the 25 % rule at all.

**LAV page list (7/10, missing 08, 09, 10):**
- **LAV-08:** worth 56 to us. Abuela sold it at 17–24 (4 fills), so that dealer is the route; t03 holds a duplicate.
- **LAV-09 / LAV-10:** worth 157 each to us (×1.6 + page bonus share).
  - Every known holder is a likely LAV chaser and loses 96–107 on a sale.
  - Chato sold them at 82–93 (6 fills), above `max_price_rare` 80. A Chato buy at about 82–90 would gain about 67–75.

## Evidence that shaped the plan

| | Friday |
|---|---|
| Public asks filled | 176/512 (34 %) |
| Addressed asks filled | 2/27 (7 %) |
| Team-to-team settlements | 46, all on El Rastro |
| Team-to-team threads | 0 |

So listings go public wherever the cap allows. Direct proposals use a team thread, a path nobody has used yet. The fallback is the same offer posted on the board with `--to`.

## Verdict (W4 has no numeric gate in PLAN.md)

| Item | Verdict |
|---|---|
| Affinity map | **GO** (beats uniform on every metric). Confidence is moderate (top P 0.35–0.78). |
| Cap | **GO** as code. It is off by default, so nothing changes until enabled. |
| Enabling the cap at base 200 | **NO-GO.** Today's plan posts, but no single trade above 50 P (any rare, for example our LAT-09 at 70) passes until our team-to-team volume tops 200 P. |
| 09:00 plan | **GO as a dry run.** Fair by construction and proven best. Expected surplus is a model value, not a fill rate. |

## Risks

- **Holdings are partial.** Known: 204 rival copies from listings and settlements. Starting hands and pack pulls are unseen. A public bid also reaches holders we don't know about.
- **RET opens Saturday.** RET/CHA chasers are invisible until then.
- **P(fill) assumes an active bot that values the card as modelled.** It does not model whether the rival's bot is online.
- **The swap path is untested live.**
- **Data age:** the plan is built on Friday's close.

## Decisions for Marius

1. Enable `max_counterparty_share` = 0.25 with `counterparty_cap_base` ≥ 400, or leave it off.
2. `max_price_rare` 80 against Chato's 82–93: LAV-09/10 are the page's real lever (about 70 P each). No guardrail value was changed tonight.
3. Swap cash legs route around the price caps. The caps apply to cash only, so "LAT-09 + 59 P for a rare" passes. Decide whether to cap swaps by value.
4. Split the 83 P above the floor between the trade desk (bids) and W3's ladder (`--cash-budget`).
5. At 09:00, run `uv run bazaar trade-plan --live`. It reads the feed history from the shared DB plus the live window, and our `/me`, open offers and this hour's spend; it sends nothing. Then post the plan (`sell bid`, `sell list --to`) or hand it to the maker.

Code review (`/code-review high`, 10 findings, all fixed in ec28ab1):
- the search crashed on pools of 1,100+ candidates (RecursionError);
- swaps counted 0 volume toward the cap;
- trades the guardrails refused stayed in the plan;
- the plan ignored our open offers and this hour's spend;
- the cap values were hard-coded;
- the taker counted the fee as volume;
- the CLI and runtime made repeated reads.

Not done:
- #14's `/api/me/value` validation, which needs live calls.
- Wiring the affinity map into `strategy.chasers`, which still uses `team_flows`.
