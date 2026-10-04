---
name: bazaar-points
description: "Playbook for earning official score in the Bazaar: how each scoring leg works, what actually earned Team 1 points on Friday and Saturday (with the measured numbers), why our duel policy worked, and a ranked per-tick loop for the ticks where no duel is live (dealer ladders, team trades, duplicate sales, Workshop, market and Market Test), each tied to its CLI command and the guardrail that gates it. Load before any buy, sell, swap, listing, dealer thread or duel decision, and before planning a Sunday round."
---

# Bazaar points playbook

Evidence for every number: `docs/points-ledger.md` (read it for the tables). Rules: `vendor/bazaar-kit/RULES.md`.
Hard rules apply before every action; historical results below do not authorize a trade:

- Never sell or swap a page's last copy. Sell only true duplicates of page cards.
  `protect_page_exceptions` is `none`; every page set is protected.
- Never sell below our server `your_value`, and human approval does NOT waive that floor.
  `sell_min_value_ratio` = 1.0. Refuse a 30 P sale of a copy worth 35 P.
- `max_score_loss_per_move` = 0.001. Require nonnegative prospective `bazaar impact` for every sale or swap;
  do not use approval to bypass a negative estimate or disable this limit.
- No buy-back. `no_buyback_ticks` = 480 blocks buying any card sold or swapped away in the last 480 ticks.
- `human_approval_above` = 0 and `max_spend_per_game_hour` = 0, disabled by Omar on Sun 4 Oct.
  Eligible automated trades do not wait for amount approval or a rolling-hour purchase ceiling.
  Cash, pending commitments, value/impact and last-copy guards still apply.
- `dealer_sell_enabled` = false means hand commands only after `bazaar impact`.
  Run `uv run bazaar impact sell CARD P --to pilar` for the concrete proposed price before a hand sale to Pilar,
  substituting the actual counterparty for other sales. A dealer final still must pass every rule above.
- Read `uv run bazaar status` before acting and again after each deal. One team key and 5 req/s shared by all
  our processes, tick-driven only, no secrets in logs.

## 1. How the score works (RULES.md "Scoring", briefing, fitted)

`score = negotiating (30) + market (30) + judges (40, not in the API)`; each day is a round, a round counts by the share
of its day played, Friday counts half. Each component is `W x min(1, ours / mean of the top-3 teams)`: **above the top-3
mean a component adds nothing, below it every point counts**.

| Leg | Raw field in `/me` score | Fitted weight | What moves it |
|---|---|---|---|
| Duels | `duel_points` | 7.5 | sum of one pie share per deal: `surplus x (1 - decay) ** rounds`, no cash or card moves |
| Dealer ladder | `ladder_points` | 7.5 | per dealer and price class, the share of the dealer's range we captured; **best 3 deals per level, a missing one is 0, higher levels weigh more, restarts every round** |
| Team trades | `neg_points` | 15 | Buy: acquired `your_value` minus total purchase cost, including fees we pay. Sell: net sale proceeds minus the copy's `your_value` lost (also a team-sourced copy sold to a dealer). |
| Market Test | `bench_points` | 22.5 of market | your best venue per session; the stall's level = 0.5, the mean of the top 3 = full |
| Organic | `mm_points` | 7.5 of market | value created between OTHER teams on our venue |

Measured: **k = 0.048 board points per neg_point lost (-89.6 gave -4.27), 0.035 per neg_point gained (+55.6 gave +1.97)**;
a ladder point was worth 24 board points at tick 720 and 13 at tick 1100 (the top-3 mean grows as others catch up).
Never counts: number of trades, fees, pack luck, gifts. Dealer sales can reduce `neg_points`: the team-sourced
SAL-07 sale to Pilar changed 134.2 to 44.6 at tick 948. Check prospective impact for hand dealer sales too.

## 2. What earned points (Saturday, ranked; ledger sections 2 to 6)

1. **Market Test at the stall's level: +7.5 of our 25.65 (29 %)**, for free. t10 (1st) took 12.5: **+5.0 is the largest
   unclaimed item** and it needs a broker that beats the auto stall, not more trades.
2. **Duels: about 7.5, Saturday saturation is an ESTIMATE.** Duels I (15.02 raw, 27 of 34 deals) moved the board +2.7;
   Duels II (+27.35 raw, 57 of 68) moved it about +0.1. More duel points do not pay once we are at the current round's
   top-3 mean. Sunday's saturation and whether duel points restart are UNVERIFIED; retain duels-first accepts
   unless current-round evidence establishes saturation.
3. **Pilar sales at her final: +1.1 board each (3 deals = +3.3).** Best single move per settlement.
4. **Pícaros buys at 55 to 63: +0.7 each**, 40 to 60 % of their range (one was a fake final).
5. **Team trades: +100.2 raw.** Selling a duplicate rare to a team that needs it at 2 to 3x our `your_value`
   (LAT-10 86, SAL-10 76, LAT-09 68) gave +33 to +55 raw each; buys below our value gave +5 to +14 each.
6. **Losses:** SAL-07 sold to Pilar at 29 against `your_value` 118.6 = **-89.6 raw, -4.27 board**; an idle stretch costs
   about -0.10 board per 10 ticks as the others move (-2.73 over ticks 652 to 1238).
7. **Zero value:** deals at the dealer's opening price, Abuela packs (replay share 0.06), Workshop crafts (luck), our own
   venue's organic flow (0 trades).

**The gap to the leaders is not volume.** t04, t13, t08, t07 settled 56 to 65 times and ranked 10th to 17th; t03 gained
+7.1 on 13 settlements. They sold uncommons and rares to Pilar, bought page rares and epics from Pícaros, and t10 owned the market leg.

## 3. Why duels worked (code: `duel_policy = v2`, `src/bazaar_agent/duel_arena.py`, GUARDRAILS.md "Duels")

Our deal rate was 79 to 84 % against the league's 74 to 78 %; the practice session on v1 closed 44 %. Parameters, in
order of what mattered (`docs/night/w2b-duel-v2.md`, `d1-sim-proof.md`):

- **Anchor far, once**: `duel_anchor` 0.6, first priced offer about 54 % beyond our limit; the deal closes in 1.5 to 2
  rounds, where the kept surplus is 20 to 35 P (4 rounds keep 8 to 10).
- **Hold while the rival concedes**: v2 sends at most `duel_max_own_offers` 3 priced messages once the rival has priced,
  because each answered pair costs one round of decay (0.06 in Duels I, 0.08 in II, 0.10 in III).
- **Never settle near the limit early**: `duel_floor_margin` 0.05, `duel_endgame_min_share` 0.3, counter once at D - 2.
- **Accept only strictly inside the limit**: `duel_inside_limit` true counts the worst-case cost of days; `duel_days_auto`
  turns on the signed days weight once two real signals agree; the floor goes out only on D - 1 (`duel_silent_floor_lead` 1).
- **Plan the team's one accept per tick across duels** (`plan_moves`, `max_accepts_per_tick` 1, duels first); the deal at
  deadline - 1 settles on the deadline.
- Do not redeploy during a duel: `uv run bazaar deploy-guard`, merge with `scripts/merge_safe.sh`.

## 4. WHEN NO DUEL IS LIVE: the per-tick loop (70 % of Saturday's ticks had no duel)

Every tick, in this order, stop at the first row that applies (one accept per tick, one message per thread, 6 threads,
12 listings). Read `uv run bazaar status` first. Where a row says "hand", it is a human or desk command; the taker and
maker already run rows 2 and 5 on their own.

| # | Action | Command / agent | Gate (GUARDRAILS.md) | Evidence |
|---|---|---|---|---|
| 1 | **Sell a spare card to Pilar, ask until she finalises** (uncommons 14 to 30, rares 50 to 87, epics 140 to 199; start high, step down 1 to 2 P per distinct bid, take her `final: true` only if the sale floor and nonnegative impact hold) | `uv run bazaar dealer sell CARD --dealer pilar --live` (hand only after `bazaar impact`; `dealer_sell_enabled` is false) | `protect_page_sets` (never the last copy), `sell_min_value_ratio` 1.0, `max_score_loss_per_move` 0.001, `no_buyback_ticks` 480 | 3 finals = +3.3 board; SAL-07 at 29 = -4.27 |
| 2 | **Dealer ladder buys, step 1 from low**: open at the lowest fill seen, never at her opening ask, climb by distinct bids until her final | taker (`agent taker --live`) or `dealer buy CARD --start P --max P --dealer D` | `max_price_*`, `official_value_margin` 0, `max_spend_per_game_hour` 0 (disabled), `trickster_*` | Abuela commons 6 to 9 = 60 %; Pícaros rare 55 to 58 = 60 % |
| 3 | **Buy a missing page card from a team or dealer below our value, including fees. Evaluate epics separately: they do not complete pages.** | `bazaar strategy`, `bazaar opportunities`, `sell bid` | `block_buying_held_cards`, `off_page_min_surplus` 10, `max_price_epic` 240, `human_approval_above` 0 (disabled) | buys below value +5 to +14 raw each; t10 epics +1.9 board |
| 4 | **Sell a duplicate to the team that needs it** (the page-completing buyer pays 2 to 3x our value) | `bazaar buyers`, `bazaar swaps`, `sell list CARD`, `sell swap` | `max_counterparty_share`, `team_swap_*`, `watchdog_max_swaps_per_team` 3, human approval for rares | +33 to +55 raw per rare; `buyer_rank_enabled` is false |
| 5 | **Ladder by level, three per round**: Abuela, Chato, Pilar, Pícaros; Banco feasibility is UNVERIFIED, see below | `dealer buy`, `agent taker` | level caps and quotas per dealer (`bazaar dealers`) | 16 of 74 threads settled; 3 deals per level = the component |
| 6 | **Workshop only for a missing rare** | `bazaar taller` | `taller_enabled`, `max_taller_per_game_hour` 2 | luck; never scores by itself |
| 7 | **Market**: keep the board venue's broker running; probe the edge only in a closed window | `agent maker`, `venue status`, `broker`, `BAZAAR_BENCH_POLICY` | `allow_venue_open`, `max_venues` 2, `deploy_guard_bench_ticks` 10 | stall = 0.5; t10 12.5 |

### Dealer notes (RAG `learnings` policy rows, replay shares, 0.95 confidence)

- **Abuela**: commons ladder 7 to 12 (share 0.43), uncommons 10 to 26 (0.24). Never a pack for points (0.06). Remembers
  kindness; a repeated price earns nothing. A deal at her opening price scores 0 and does not unlock the next level.
- **El Chato**: rares 75 to 95 (0.30), uncommons 26 to 61; finals after about 5 bids. Step 1 from low is the best ladder.
- **Doña Pilar** (L3, collector): buys uncommon, rare, epic; sells only gold packs. **Her finals are the best ladder deals
  we had.** Salamanca fever 939 to 1179 (+25 % over book): check `/api/schedule` before selling SAL. Before any hand sale, require
  `bazaar impact sell CARD P --to pilar` >= 0 and price >= the fresh server `your_value`; team-sourced copies can lose `neg_points`.
- **Los Pícaros** (L4, trickster): rares 48 to 67 (0.42), epics 128 to 167 (0.45). **A "final" at its list price is fake**
  (LAV-10 at 63 = 40 %); `agents/trickster.py` accepts only near the lowest fill. Do not flag unless `flag_dealers` allows.
- **Don Ernesto / Banco** (L5): observed epics at 116 to 120 in 3 fills. UNVERIFIED: the highest ladder weight and a
  legal resale to Pilar. Her observed 140 to 199 P bids do not prove an exit above our copy's `your_value`.
  This is a research lead, not an instruction to spend. Before a purchase could be recommended for resale, establish
  a concrete candidate sale price, fresh server value for the resulting copy, price >= that value and nonnegative
  prospective `bazaar impact` for the exit. If that cannot be established before buying, skip the purchase.
  Human approval alone does not establish feasibility; recheck value and impact before any eventual sale.
- **Ladder restart per round**: each round needs 3 new deals per level; a deal added after the best three only helps if its
  share is higher. Ladder deals beyond that earn nothing, so stop at three good ones and spend the slots elsewhere.
- **Slots**: 6 conversations, one per dealer. A thread that is not moving closes after `dealer_max_ticks_per_thread` 14.
  Close it the moment the item is bought elsewhere.

### Team trades and swaps (the largest headroom, ESTIMATE up to +10 board)

- Price by the counterparty's need, not ours: a duplicate is worth 0.25x to us, a page-completing card is worth a lot to them.
  Build the target list with `bazaar buyers` (willingness, interest, need) and `bazaar affinity`; weaker teams first
  (not a podium rival, `buyer_rank` excludes the top 5).
- Spread counterparties: `max_counterparty_share` and `watchdog_max_swaps_per_team` 3 stop a feed-another-team pattern (RULES.md fair play).
- A swap must beat `team_swap_min_surplus` 3 after fees, give the other side at most half (`team_swap_max_their_share` 0.5),
  and pass Jev `team_swap_worth_it` at 0.75 (`team_swap_jev_gate`: without a Jev key no swap goes out).
- Team threads are negotiation with other LLM agents: `team_threads_enabled`, max 2 open, 12 messages, 3 idle ticks.
  Bluff in WORDS only (`bluff_enabled`), never in the structured offer. Read the structure of what you accept.
- The market is thin: 135 rastro and 53 team-venue trades in the whole weekend. Post addressed asks, do not wait for fills.

### Duplicate sales

Apply every hard rule above. For a page card, retain one uncommitted copy after the sale or swap; check open offers
as well as holdings. Refuse sales below the fresh server `your_value` even with human approval, and require a
nonnegative prospective impact at the proposed price. No page-card exception permits selling or swapping a last copy.

### Market and Market Test

A session counts our best venue open during it (none open = 0); 16 ticks, same synthetic book for everyone. Our board venue
v19 with the exact broker equalled the stall (0.5). Beating it needs the edge broker (`BAZAAR_BENCH_POLICY=edge`, guarded)
or an `auto` venue beside it (`max_venues` 2, opened by hand). **Never merge or redeploy while a Market Test runs.**

## 5. Rules that cost us points when broken

- Selling a page's last copy (SAL-07: -4.27). Do not sell a card to a team or dealer when `bazaar impact` is negative.
- Genuine opening-price deals score 0 ladder. A fake final does not necessarily score 0: LAV-10 at 63 added
  +0.047, with ladder 0.254 to 0.301 at ticks 863 to 864. The older memory entry's approximately-zero estimate
  was superseded by these snapshots; `final: true` alone still does not prove a good price.
- 74 threads, 16 deals: do not open a thread you will not finish; the slot costs the next buy.
- Ticks 386 to 718 were not 332 inactive ticks: Chato raised ladder 0.019 to 0.020 at tick 443, and Duels I added
  15.02 raw points. Use the ledger's separate idle-drift metric: 27 board-refresh windows with no event of ours
  between ticks 652 and 1238, totaling -2.73 board points. It is not a continuous inactivity interval.
  The operational stall alarm is `activity_stall_seconds` 30, not this score metric.
- A redeploy re-arms duel latches (`duel_days_auto`): freeze main while a session runs.

## 6. Sunday checklist, 15-second ticks

The pre-opening h16.65 anchor is obsolete. Round 3, "Sunday · Chamberí", started at tick 1446.
Use the current clock and schedule, with evidence in `docs/briefing.md`.

- [ ] Read `uv run bazaar status`, `rules` and `deploy-guard` before acting. Keep manual command checkouts current.
- [ ] Plan up to three legal ladder deals per level for the new round. Recheck remaining shared hourly spend
  and cash commitments before opening a dealer thread. An expensive opening ask can still concede to an
  affordable final; known negotiated fills above the remaining budget should not consume a thread.
- [ ] Keep the broker up for the next Market Tests. The read at `now_hours=14.037` schedules tests at h14.65,
  h15 and h17. Verify new books, requests, responses and actual settlements. Historical replay rows do not prove this.
- [ ] Preserve duels-first accepts for Duels III at h15.367: price + days, 12-tick duels, decay 0.10,
  two rounds and at most four concurrent. Current-round raw duel points were zero at tick 1540;
  Saturday's totals do not establish Sunday saturation. Do not merge during a protected event window.
- [ ] Re-read holdings after every deal. Only true duplicates may be sold or swapped, above the fresh sell floor.
  Amount-based human approval is disabled. Do not lower Jev's gate simply to increase activity.
- [ ] Finish eligible dealer work before the scheduled h18.367 stall closure and final duel wave.
  Scores freeze is currently h19.367; the schedule explicitly closes doors at 15:00 CEST.
  At closing, pause first, then use `bazaar flatten --live --threads` if needed.
- [ ] Append errors and findings to `.ai/memory.md`; report observed outcomes separately from estimates.
