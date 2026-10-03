---
name: bazaar-points
description: "Playbook for earning official score in the Bazaar: how each scoring leg works, what actually earned Team 1 points on Friday and Saturday (with the measured numbers), why our duel policy worked, and a ranked per-tick loop for the ticks where no duel is live (dealer ladders, team trades, duplicate sales, Workshop, market and Market Test), each tied to its CLI command and the guardrail that gates it. Load before any buy, sell, swap, listing, dealer thread or duel decision, and before planning a Sunday round."
---

# Bazaar points playbook

Evidence for every number: `docs/points-ledger.md` (read it for the tables). Rules: `vendor/bazaar-kit/RULES.md`.
Hard rules still apply first: read `uv run bazaar status` before acting, never sell or swap a page's last copy, never
trade below `your_value`, one team key and 5 req/s shared by all our processes, tick-driven only, no secrets in logs.

## 1. How the score works (RULES.md "Scoring", briefing, fitted)

`score = negotiating (30) + market (30) + judges (40, not in the API)`; each day is a round, a round counts by the share
of its day played, Friday counts half. Each component is `W x min(1, ours / mean of the top-3 teams)`: **above the top-3
mean a component adds nothing, below it every point counts**.

| Leg | Raw field in `/me` score | Fitted weight | What moves it |
|---|---|---|---|
| Duels | `duel_points` | 7.5 | sum of one pie share per deal: `surplus x (1 - decay) ** rounds`, no cash or card moves |
| Dealer ladder | `ladder_points` | 7.5 | per dealer and price class, the share of the dealer's range we captured; **best 3 deals per level, a missing one is 0, higher levels weigh more, restarts every round** |
| Team trades | `neg_points` | 15 | `price - your_value` on every settlement with another team (also a team-sourced copy sold to a dealer) |
| Market Test | `bench_points` | 22.5 of market | your best venue per session; the stall's level = 0.5, the mean of the top 3 = full |
| Organic | `mm_points` | 7.5 of market | value created between OTHER teams on our venue |

Measured: **k = 0.048 board points per neg_point lost (-89.6 gave -4.27), 0.035 per neg_point gained (+55.6 gave +1.97)**;
a ladder point was worth 24 board points at tick 720 and 13 at tick 1100 (the top-3 mean grows as others catch up).
Never counts: number of trades, fees, pack luck, gifts. Dealer deals never touch `neg_points`.

## 2. What earned points (Saturday, ranked; ledger sections 2 to 6)

1. **Market Test at the stall's level: +7.5 of our 25.65 (29 %)**, for free. t10 (1st) took 12.5: **+5.0 is the largest
   unclaimed item** and it needs a broker that beats the auto stall, not more trades.
2. **Duels: about 7.5, saturated.** Duels I (15.02 raw, 27 of 34 deals) moved the board +2.7; Duels II (+27.35 raw, 57 of
   68) moved it about +0.1. More duel points do not pay once we are at the top-3 mean.
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

## 4. WHEN NO DUEL IS LIVE: the per-tick loop (70 % of Saturday's ticks had no duel; we moved once per 36)

Every tick, in this order, stop at the first row that applies (one accept per tick, one message per thread, 6 threads,
12 listings). Read `uv run bazaar status` first. Where a row says "hand", it is a human or desk command; the taker and
maker already run rows 2 and 5 on their own.

| # | Action | Command / agent | Gate (GUARDRAILS.md) | Evidence |
|---|---|---|---|---|
| 1 | **Sell a spare card to Pilar, ask until she finalises** (uncommons 14 to 30, rares 50 to 87, epics 140 to 199; start high, step down 1 to 2 P per distinct bid, take her `final: true`) | `uv run bazaar dealer sell CARD --dealer pilar --live` (hand; `dealer_sell_enabled` is false for the maker) | `protect_page_sets` (never the last copy), `sell_min_value_ratio` 1.0, `max_score_loss_per_move` 0.001, `no_buyback_ticks` 480 | 3 finals = +3.3 board; SAL-07 at 29 = -4.27 |
| 2 | **Dealer ladder buys, step 1 from low**: open at the lowest fill seen, never at her opening ask, climb by distinct bids until her final | taker (`agent taker --live`) or `dealer buy CARD --start P --max P --dealer D` | `max_price_*`, `official_value_margin` 0, `max_spend_per_game_hour` 250, `trickster_*` | Abuela commons 6 to 9 = 60 %; Pícaros rare 55 to 58 = 60 % |
| 3 | **Complete a page by buying the rare or epic from a team or dealer below our value** | `bazaar strategy`, `bazaar opportunities`, `sell bid` | `block_buying_held_cards`, `off_page_min_surplus` 10, `max_price_epic` 240, `human_approval_above` 250 | buys below value +5 to +14 raw each; t10 epics +1.9 board |
| 4 | **Sell a duplicate to the team that needs it** (the page-completing buyer pays 2 to 3x our value) | `bazaar buyers`, `bazaar swaps`, `sell list CARD`, `sell swap` | `max_counterparty_share`, `team_swap_*`, `watchdog_max_swaps_per_team` 3, human approval for rares | +33 to +55 raw per rare; `buyer_rank_enabled` is false |
| 5 | **Ladder by level, three per round**: Abuela, Chato, Pilar, Pícaros, **Banco** (we never opened a Banco thread) | `dealer buy`, `agent taker` | level caps and quotas per dealer (`bazaar dealers`) | 16 of 74 threads settled; 3 deals per level = the component |
| 6 | **Workshop only for a missing rare** | `bazaar taller` | `taller_enabled`, `max_taller_per_game_hour` 2 | luck; never scores by itself |
| 7 | **Market**: keep the board venue's broker running; probe the edge only in a closed window | `agent maker`, `venue status`, `broker`, `BAZAAR_BENCH_POLICY` | `allow_venue_open`, `max_venues` 2, `deploy_guard_bench_ticks` 10 | stall = 0.5; t10 12.5 |

### Dealer notes (RAG `learnings` policy rows, replay shares, 0.95 confidence)

- **Abuela**: commons ladder 7 to 12 (share 0.43), uncommons 10 to 26 (0.24). Never a pack for points (0.06). Remembers
  kindness; a repeated price earns nothing. A deal at her opening price scores 0 and does not unlock the next level.
- **El Chato**: rares 75 to 95 (0.30), uncommons 26 to 61; finals after about 5 bids. Step 1 from low is the best ladder.
- **Doña Pilar** (L3, collector): buys uncommon, rare, epic; sells only gold packs. **Her finals are the best ladder deals
  we had.** Salamanca fever 939 to 1179 (+25 % over book): check `/api/schedule` before selling SAL. Never sell a copy we
  bought from a team unless `bazaar impact sell CARD P` is >= 0 (it counts as neg_points).
- **Los Pícaros** (L4, trickster): rares 48 to 67 (0.42), epics 128 to 167 (0.45). **A "final" at its list price is fake**
  (LAV-10 at 63 = 40 %); `agents/trickster.py` accepts only near the lowest fill. Do not flag unless `flag_dealers` allows.
- **Don Ernesto / Banco** (L5): epics at 116 to 120 (3 fills), under every other source; hourly limits. UNVERIFIED: whether it
  carries the highest ladder weight and whether a bought epic re-sells to Pilar at 140+ with no score loss. **Probe with one
  small approved deal (`bazaar approve CARD --buy --max P`, `uv run bazaar impact`) before scaling.**
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

Only a copy we hold twice and that does not break a page: `protect_page_sets` covers every set; the only exceptions are
`protect_page_exceptions` (LAT-10, LAT-09 at a floor). A sale below `your_value` or with an estimated loss needs
`bazaar approve CARD --sell --min P --ttl-ticks 10` from a human. No buy-back of anything sold in 480 ticks.

### Market and Market Test

A session counts our best venue open during it (none open = 0); 16 ticks, same synthetic book for everyone. Our board venue
v19 with the exact broker equalled the stall (0.5). Beating it needs the edge broker (`BAZAAR_BENCH_POLICY=edge`, guarded)
or an `auto` venue beside it (`max_venues` 2, opened by hand). **Never merge or redeploy while a Market Test runs.**

## 5. Rules that cost us points when broken

- Selling a page's last copy (SAL-07: -4.27). Do not sell a card to a team or dealer when `bazaar impact` is negative.
- Opening-price deals and fake finals count as 0 ladder.
- 74 threads, 16 deals: do not open a thread you will not finish; the slot costs the next buy.
- Idling: nothing moved in 332 consecutive ticks (386 to 718). The stall alarm is `activity_stall_seconds` 30.
- A redeploy re-arms duel latches (`duel_days_auto`): freeze main while a session runs.

## 6. Sunday checklist (15 s ticks, Jev timeout 3 s, doors 09:00 to 15:00)

- [ ] 09:00 `uv run bazaar status`, `rules`, `deploy-guard`; `git pull --ff-only` on any laptop that runs hand commands.
- [ ] Ladder restarts at round 3: **plan 3 deals per level** (Abuela, Chato, Pilar, Pícaros, Banco) once it starts.
- [ ] **Hard Market Test about 09:34**: board venue broker up before 09:20, no deploys from 09:24. Edge policy only if it was proven.
- [ ] Before round 3 (about 11:34): spare duplicates sorted, Pilar sells queued, `bazaar buyers` fresh; Chamberí (CHA) released
  at the round start with +150 P: scan `cards_heartbeat` for the 12 new cards and buy page cards below `your_value`.
- [ ] Round 3 first 40 minutes (160 ticks) is the ramp: ladder deals count by the share of the day played; do the best three early.
- [ ] **Duels III about 13:34** (12 ticks, decay 0.10, at most 4 at once): freeze main from 13:15; `duel run --play`;
  duel points are saturated, so do not trade the ladder slot for them: keep dealer threads on non-accept ticks.
- [ ] Throughout: one move per tick that changes a raw leg (ledger section 6): a Pilar sale at her final, a Pícaros or Banco
  buy, a page-completing buy, a duplicate sold to the team that needs it.
- [ ] The briefing says the dealer stalls close at game hour 21.65 (the finale): read `/api/clock` and `/api/schedule` for the
  real time and finish dealer sales before it. Doors close 15:00: pause first, then `bazaar flatten --live --threads`.
- [ ] Append every error and finding to `.ai/memory.md`; end every task with the Honest Implementation Report.
