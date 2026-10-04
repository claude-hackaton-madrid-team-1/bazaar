# Other dealers (Chato, Pilar, Banco): why Team 1 only deals with Abuela and Los Pícaros (Sun 4 Oct)

Session `other-dealers`, ~10:50–11:15 local. Read-only: Postgres `SELECT`, keyless public GETs, the dashboard repo.

## TL;DR
- **Not a dashboard gap.** From 09:00 (tick 1445) to tick 1828 our `threads` table holds 44 dealer threads, all
  with `abuela` (26) or `picaros` (18); **0 with chato, pilar or banco**. The dashboard does not filter dealers
  (bazaar-live `db/game.sql` `show.game_threads`: `where t.ours`, no dealer filter).
- **The taker knows the levels are empty and says why, every probe round** (`decisions.kind = 'ladder_slot'`,
  64 rows, ticks 1735–1813): `L3 0/3 pilar` and `L5 0/3 banco`: "sells us no card we may buy: only a dealer sell
  fills this level"; `L2 0/3 chato`: "no probe inside our caps" (his asks sit above the official value / rarity
  caps).
- **Nothing can sell to them today.** `dealer_sell_enabled = false` (maker), and even the hand command
  `bazaar dealer sell` refuses the last copy of ANY page card (`dealer_sell.only_copy`, `cli.py:1057`,
  `dealer_sell_desk.py:160/419`), ignoring Sunday's `protect_complete_pages_only = true`. Our only duplicate is a
  common, and Pilar, Chato and Banco buy no common. So flipping `dealer_sell_enabled` alone fills nothing at L2/L3/L5.
- **Other teams use them**: since 09:00, 33 Chato / 32 Pilar / 11 Banco threads (feed `thread.opened`);
  11 cards sold to Pilar by 6 teams, 6 cards bought from Chato by 4 teams, 1 epic sold to Banco.
  Saturday's three Pilar sales at her final were our best move per settlement (+1.1 board each, docs/points-ledger.md §2).
- **Fix (this branch, `fix/other-dealers`)**: `only_copy` follows `protect_complete_pages_only` when the card's page is
  known incomplete; hand sells to Pilar of incomplete-page singles become possible locally, no deploy needed.
  Chato (L2) and Banco (L5) have no positive move today; skip them.

## Method and data windows
- Postgres (read-only): `threads` (opened_tick ≥ 1380), `decisions` (tick ≥ 1445: kinds `ladder_slot`,
  `ladder_probe`, `dealer_open`, `dealer_skip`, `strategy_gate`), `feed_events` (`settlement`, `thread.opened`,
  tick 1445–1828 = 09:00–~10:50 local; Saturday settlements for prices), `me_snapshots` (t01, tick 1828).
  Queries: `other-dealers/queries.sql`.
- Keyless GETs (1 each, ≥1.1 s apart, ~10:52 local): `/api/dealers`, `/api/levels`, `/api/schedule`. No keyed request.
- Not read: Railway logs (the decisions rows carried the reasons; time-boxed).

## Findings

### 1. Threads per dealer since 09:00 (ours)
| dealer | level | threads | deals | walked | refused final |
|---|---|---|---|---|---|
| abuela | L1 | 26 | 4 | 20 | 0 (2 open) |
| picaros | L4 | 18 | 2 | 13 | 3 |
| chato | L2 | 0 | 0 | | |
| pilar | L3 | 0 | 0 | | |
| banco | L5 | 0 | 0 | | |

`dealer_open` rejections (121) are almost all Pícaros RET ranked buys (refused by price/value), the hourly spend cap
(ticks 1525–1631, before the cap was removed), Abuela's persona quota and `block_buying_held_cards`. No `dealer_open`
row ever names chato, pilar or banco: the strategy ranks no buy there and the ladder probe plans none.

### 2. The ladder probe's own verdict (`decisions.kind = 'ladder_slot'`, one per level per probe round)
- `L2 0/3 this round: chato no probe inside our caps (no fill seen for the rarity, a top under her lowest fill or
  the official value, a share under ladder_probe_min_share, probed this game hour, or her thread is busy)`.
- `L3 0/3 this round: pilar sells us no card we may buy: only a dealer sell fills this level`.
- `L4 2/3 this round: picaros ...` (probes planned 4× at 27→49 vs her lowest fill 46–48; not yet filled).
- `L5 0/3 this round: banco sells us no card we may buy: only a dealer sell fills this level`.
- Gate: `strategy_gate` Jev `ladder_probe_worth_it` was undecided (0.74 < 0.75) at ticks 1739/1743, yes 0.76–0.77
  later. (card-hunt is removing that gate for its path.)

Code: `src/bazaar_agent/agents/ladder_probe.py` `sells_us_cards` / `unfillable` (PR #277). #277 is working as
designed: it fills only by BUYING, and only Abuela/Chato/Pícaros sell cards; Chato's prices are above our caps.

### 3. What each other dealer does today (`/api/dealers`, ~10:52) and what they paid (feed settlements)
| dealer | L | sells | buys | fills to dealer (public) |
|---|---|---|---|---|
| chato | 2 | silver pack 150, uncommon (list 26), rare (list 77) | uncommon, rare | uncommon 11–16 (one 26), rare 29–49 (Sat) |
| pilar | 3 | gold pack 420 | uncommon/rare/epic, favourite sets SAL+RET | RET unc 22–26, SAL unc 23–29, other unc 16–21; RET rare 67–78, SAL rare 65–87, other rare 50–56; SAL-11 epic 179–199 |
| banco | 5 | gold pack, legendary 585 | epic, legendary | epic 116–126 (LAV-11, SAL-11) |

All five stalls close at h18.367 (~14:00 local, `/api/schedule`).

### 4. Why each empty level stays empty
- **L3 Pilar**: only a sale fills it. Candidates = our single copies on INCOMPLETE pages whose value to us is below
  her fills (El Retiro uncommons; a La Latina rare). Blocked by `only_copy` (hand and maker) and
  `dealer_sell_enabled = false` (maker). This is the one level with clearly positive sales.
- **L2 Chato**: selling to him is below our value for every card we hold (his uncommon 11–16, rare 29–49: a sale
  below value costs neg_points, Sat RET-06 at 16 → −1.5). Buying from him needs a missing card whose official value
  reaches his asks (26+/77+); our missing LAT/RET cards are valued under that. No positive move.
- **L5 Banco**: buys epics/legendaries only; we hold none. An epic bought from Pícaros (list 162) to resell must
  satisfy `off_page_min_surplus` (buy ≤ value − 10) and the sale floor (sell ≥ value): impossible at Banco's 116–126.
  No move.

## Recommendations (ranked by expected points before ~14:00)
1. **Hand sells to Pilar, L3 (up to 3 deals ≈ +0.15 ladder ≈ +1.5 to +3 board, Saturday rate +1.1/deal)** — run from
   this branch locally (no deploy; the fix only touches the hand command's copy rule):
   ```
   git switch fix/other-dealers
   uv run bazaar impact sell <REF> <price> --to pilar          # must not be negative
   uv run bazaar dealer sell <REF> --dealer pilar --start <~1.5× her top fill> --min <your_value + 2> --step 2       # dry run
   uv run bazaar dealer sell <REF> --dealer pilar --start ... --min ... --step 2 --live
   ```
   Card rule: page not complete; not Chamberí (being completed); a duplicate before a single copy; your_value + 2
   below Pilar's lowest public fill for that set/rarity (table §3). One card per thread, one thread at a time
   (one conversation per dealer), stop at 3. Prefer a team buyer for a rare a team will pay far more for (a team
   sale also moves neg_points; collections.md lists the La Latina rare for that).
   Risk: an incomplete page loses a card (its your_value already prices that); all sell guards still apply
   (`max_score_loss_per_move`, `sell_min_value_ratio`, `guardrails.protects` with the complete-page list).
2. **L4 third slot (Pícaros)**: the probe is planned (27→49); let it run. A sale also fills it: Pícaros buys commons
   and uncommons, our duplicate common can go there by hand (`--dealer picaros`, floor ≥ your_value).
3. **`dealer_sell_enabled = true`**: only useful together with card-hunt's PR (it handles the only-copy rule on its
   hunt path and restricts sell threads to dealers with an empty level). Alone it sells nothing at L2/L3/L5 and
   reopens the Saturday risk (sell threads holding a dealer the taker wants; 4 reopens for one copy). Merging
   either PR redeploys 4 services incl. bazaar-duels: never during Duels III or the 12:37 Market Test.
4. Chato L2 and Banco L5: skip today (no positive move).

## Dashboard
Not filtering. `show.game_threads` returns every thread of ours (`where t.ours`), and the data has none with
chato/pilar/banco. Cosmetic only: `src/ui/speakers.ts` names abuela/chato/pilar and falls back to a guest name for
picaros/banco.

## Code change (`fix/other-dealers`)
- `agents/dealer_sell.py`: `only_copy(..., page_complete=None)`; `page_complete(me, ref, rules)` (None unless
  `protect_complete_pages_only` and the album lists the set: fail closed); `copy_to_sell(..., page_complete=None)`.
  Default behaviour unchanged for every other caller (card-hunt owns `dealer_sell_desk.py`).
- `cli.py` `dealer sell`: passes the page state, re-read from /me each tick (a page completed mid-thread keeps its copy).
- Tests: `tests/test_dealer_sell.py` (unit + CLI dry run).

## Not verified
- Ladder score per level weight (L3/L5 vs L1) is inferred from Saturday deltas, not published.
- Pilar's current limit for our exact cards: only public fills seen.
- Railway logs not read.
