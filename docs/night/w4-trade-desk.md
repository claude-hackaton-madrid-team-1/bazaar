# W4 · Trade desk (night of 3 Oct 2026)

Draft PR #79, stacked on #72 (`fix/cash-spend-accounting`). Nothing touched the live game. Inputs: the public feed from the shared DB (read-only, 3,733 events, ticks 0–159), our `agent.me` at tick 149 (checked against the DB snapshot at tick 159: the same assets and cash, no event of ours after tick 149), and the catalog and venues fixtures.

Our private numbers are not in this file: our cash, our card values, asset ids and the exact planned prices are in `_night/w4-private-numbers.md` (outside the repo), and the full plan is what `bazaar trade-plan` writes to `.local/night/` (git-ignored).

## 1. Rival affinity map (`affinity.py`, `bazaar affinity`)

Every team holds the same six multipliers, shuffled. So the model computes, for each team, a posterior over the 720 possible assignments:
- **Interest:** buys, bids, dealer topics, sells and listings. Reprices are counted once, and the evidence is damped (sign × log1p) so that a bot repeating one policy counts as one decision.
- **Prices:** a buy or bid above `book × a × 1.25` makes multiplier `a` unlikely. A 20 % noise floor keeps one overpaying bot from deciding the result.

Calibration is a time split (fit on ticks < 60/80/100, then predict the set each team pushed hardest afterwards). Reproduce it with `uv run python scripts/affinity_eval.py --events FEED.jsonl --me ME.json --catalog CATALOG.json`. β and damping were chosen on these same splits (in-sample), and the target measures persistence of revealed interest, not the private multipliers:

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
- **CLI and runtime:** `bazaar sell list/bid/swap` and the runtime's sell tools read our volume from the whole feed history (the shared DB first, as the maker and the taker do) when the cap is on. `--to tNN` addresses an offer to one team.
- **Guarded swaps:** `bazaar sell swap ASSET --for REF --to tNN [--give-cash N | --want-cash N]` posts a card-for-card offer addressed to one team. It is checked as a sale at what we receive (never below `your_value`) and as a bid for the cash we add (price cap, cash floor, spend cap); both count toward the team's share.
- **Hands off:** a live `bazaar sell ...` post is booked in the shared ledger as a `hands-off:<offer id>` listing. The maker, which owns our board offers and cancels any that are not its targets, never cancels or reprices those, and posts nothing for the copy or card they cover.
- Tests: 25 for the cap, swaps and hands-off; 15 for the affinity map; 46 for the trade desk.

## 3. The 09:00 dry-run plan (`trade_desk.py`, `bazaar trade-plan`)

How the plan is built:
- **Prices:** each trade is priced inside the expected pie (we take at most half of it), at the price with the best `our surplus × P(the counterparty's value clears it)`.
- **Bids:** a bid never beats a dealer's median fill or a guardrail cap.
- **Swaps:** a cash leg splits the pie.
- **Selection:** an exact branch and bound (it finished, so the result is proven best) picks the plan with the best expected surplus in which no counterparty passes 25 % of the planned volume.
- **Selection pool:** the search sees at most 120 candidates (4 per copy or wanted card). It runs in 0.3 s on Friday's data.
- **Posting, fair in the worst case:** a listing posted for anyone can be taken by any holder, so it counts against every team (as the guardrail counts it). A listing goes public only while no team could pass 25 % of the planned volume by taking every public listing on top of what is addressed to it; otherwise it is addressed. Every trade then passes `guardrails.check()` with GUARDRAILS.md as it is, on top of our open offers and this game hour's spend; a refused trade is replaced by the next best plan.
- **What-if:** while the cap is off, the plan also reports how it would post at cap bases 200 and 400, including listings that would be addressed instead of public.
- **Commands:** each planned trade comes with its guarded command (`sell bid ... --to`, `sell swap ...`), dry run until `--live`.

To regenerate the plan (reads only, writes `.local/night/trade-plan.{json,md}`):

```
uv run bazaar trade-plan --live
```

The plan (prices and asset ids are in `_night/w4-private-numbers.md`):

| # | kind | trade | counterparty (posted to) | P(fill) | volume |
|---|---|---|---|---:|---:|
| 1 | bid | P₁ for any LAV-08 | t03, which holds 2 (t03) | 1.00 | 25 |
| 2 | bid | P₂ for any SAL-08 | t04 (t04) | 0.54 | 25 |
| 3 | bid | P₃ for any MAL-02 | t06 (t06) | 1.00 | 10 |
| 4 | bid | P₄ for any LAT-04 | t06 (t06) | 1.00 | 10 |
| 5 | swap | a LAT common + cash for MAL-08 | t08 (t08) | 1.00 | 35 |
| 6 | swap | a SAL common + cash for MAL-07 | t17 (t17) | 1.00 | 35 |
| 7 | swap | a MAL common for SAL-05 + cash | t12 (t12) | 0.93 | 20 |

**Totals:**
- **Expected surplus for us:** +79.9 P if each trade fills whenever its counterparty values it (P(fill) above), on 160 P of volume.
- **At Friday's fill rates:** +4.8 P. All 7 trades are addressed, and on Friday 6 % of addressed copies sold (20 % of public ones). The model's P(fill) only asks whether the counterparty values the price, not whether its bot is there to take it.
- **Counterparty shares:** t08 22 %, t17 22 %, t03 16 %, t04 16 %, t06 12 %, t12 12 %. In the worst case (one team takes every public listing too): 22 %, because nothing is public.
- **Cash:** bids and cash legs use all but 1 P of the cash above `cash_floor`.
- **Checks:** every trade has surplus for both sides; 0 checks fail.

**Robustness:** I re-scored the same 7 trades under other maps. Expected surplus stays between +73.4 and +81.2 P:
- β 0.25 or 1.0, or undamped evidence;
- a map fitted on ticks < 80 only;
- no information at all (the uniform prior).

Most trades clear for any multiplier (duplicates, swaps with a cash leg). So the map earns its keep in choosing counterparties and prices, not in the fill estimate.

**Why 4 listings + 3 proposals, not 12 + 3:**
- **Cash:** the cash above `cash_floor` is small (shared with W3's ladder).
- **Commons:** `sell_min_surplus` 5 with a fair split rules out every common, because the pie is under 10 P.
- **The 25 % rule:** it holds back our one big sale, LAT-09 (70 P notional on a 160 P plan). The best plan without the rule is +108.2 P, so the rule costs 28 P of model surplus. The biggest trade it holds back is a LAT-09 + cash swap for LAV-09 with t07 (140 P notional).
- **With no cash for bids** (`--cash-budget 0`), no plan meets the 25 % rule at all.

**LAV page list (missing LAV-08, 09, 10):**
- **LAV-08:** Abuela sold it at 17–24 (4 fills), so that dealer is the cheap route; t03 holds a duplicate.
- **LAV-09 / LAV-10:** every known holder is a likely LAV chaser (P 0.43–0.78) and would lose ~96–107 on a sale. Chato sold them at 82–93 (6 fills), above `max_price_rare` 80. At Chato's price each would still be a large gain for us (exact value in `_night/`).

## Evidence that shaped the plan

| | Friday |
|---|---|
| Copies listed publicly that sold | 26/133 (20 %); per listing 27/511 (5 %) |
| Copies listed to one team that sold | 1/18 (6 %); per listing 1/25 (4 %) |
| Team-to-team settlements | 46, all on El Rastro |
| Team-to-team threads | 0 |

Public listings fill better, but they are only fair in the worst case when the plan has room for them. Direct proposals go out as addressed board offers (`sell swap`), the path t13 used for swaps on Friday; a team thread (`thread_proposal`) is the alternative, and nobody opened one on Friday.

## Verdict (W4 has no numeric gate in PLAN.md)

| Item | Verdict |
|---|---|
| Affinity map | **GO** (beats uniform on every metric). Confidence is moderate (top P 0.35–0.78). |
| Cap | **GO** as code. It is off by default, so nothing changes until enabled. |
| Enabling the cap at base 200 | **NO-GO.** No single trade above 50 P (any rare) passes until our team-to-team volume tops 200 P. Base 400 lets about 100 P per team through, roughly this whole plan, so for the morning it all but switches the 25 % rule off. |
| 09:00 plan | **GO as a dry run, small.** Fair in the worst case and proven best among its candidates, but all 7 trades are addressed: +79.9 P in the model, about +4.8 P at Friday's fill rates. The scanner (B4, #98), which takes offers already standing, is the bigger lever. |

## Risks

- **Holdings are partial.** Known: 204 rival copies from listings and settlements. Starting hands and pack pulls are unseen. A public bid also reaches holders we don't know about.
- **RET opens Saturday.** RET/CHA chasers are invisible until then.
- **P(fill) assumes an active bot that values the card as modelled.** It does not model whether the rival's bot is online.
- **The swap path is untested live** (`sell swap` is checked, dry-run tested and addressed like t13's Friday swaps).
- **Hand posts and the maker:** without the hands-off booking, the live maker cancels any plain board offer of ours that is not its target one tick later. Post hand trades only with `bazaar sell ... --live` (which books them), never with a raw API call.
- **Data age:** the plan is built on Friday's close.

## Decisions for Marius

1. The cap: off (today), or `max_counterparty_share` = 0.25. At base 200 no rare can trade until our volume passes 200 P; at base 400 the rule barely binds this morning. The plan itself is fair in the worst case either way.
2. `max_price_rare` 80 against Chato's 82–93: LAV-09/10 are the page's real lever. No guardrail value was changed tonight.
3. Swap cash legs route around the price caps (the caps apply to cash only; `sell swap` checks the cash leg as a bid, not the card). Decide whether to cap swaps by value.
4. Split the cash above the floor between the trade desk (bids) and W3's ladder (`--cash-budget`).
5. At 09:00, run `uv run bazaar trade-plan --live` (reads only), then post with the commands it prints, which are `bazaar sell bid|list|swap ... --live`. That path is guarded and booked hands-off, so the live maker leaves the posts alone. Or keep only the maker running and skip hand trades.
6. Set `chaser_min_p` = 0.5 in STRATEGY.md (default 0 = today) so the maker's buyers and its `to` candidates come from the map. On Friday's feed the map names MAL = t12 where team flows said t13/t15/t17, and LAT = t15/t18 where flows said t18 only.

Second review (r1, on c2d1c50), all fixed on this branch:
- public listings were scored for the intended counterparty only (now the worst case);
- this file held our private numbers (now in `_night/`);
- swaps had no guarded write path (`sell swap`);
- the CLI and runtime read our volume from the capture, not the DB;
- the what-if hid re-addressed listings;
- `Action` was built positionally, and posting exposure used the cash, not the notional;
- the eval script was not committed.

Code review (`/code-review high`, 10 findings, all fixed in ec28ab1):
- the search crashed on pools of 1,100+ candidates (RecursionError);
- swaps counted 0 volume toward the cap;
- trades the guardrails refused stayed in the plan;
- the plan ignored our open offers and this hour's spend;
- the cap values were hard-coded;
- the taker counted the fee as volume;
- the CLI and runtime made repeated reads.

Not done: #14's `/api/me/value` validation, which needs live calls.
