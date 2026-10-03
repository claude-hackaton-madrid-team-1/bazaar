# B21 · Fastest path up the ladder levels (night shift, 3 Oct 2026)

Draft PR on `night/b21-levels`, stacked on B9 (`night/b9-packs-ev`). Nothing went live.
- **Inputs:** Friday's public feed (local capture merged with the shared DB, read-only, ticks 0–159), the API fixtures, the #55 simulator's `dealers.json`, and W3/B3/W5/W7's reports.
- **Recompute:** `uv run bazaar plan levels` (read-only).

## 1. Every level event on Friday
| tick | event | content |
|---|---|---|
| 71 | `level.announced` | El Chato: «Better packs, friendly prices. If I like you.» |
| 98 | `level.activated` | how: "Better packs and rare singles; he buys uncommon and rare cards." `opens_to_all_in_hours: 1.0` |
| 98 | `level.unlocked` × 12 | t01 "4 deals with abuela", t03 "5", t05 "6", t06 "3", t07 "5", t09 "4", t10 "8", t12 "4", t13 "6", t14 "3", t17 "7", t18 "3" |
| 121–132 | `level.unlocked` × 4 | t02, t04, t08, t16: each "**3** deals with abuela", the tick its third deal settled |
| 158 | `persona.open_to_all` | El Chato, 1.0 h (60 ticks) after activation |
| 158 | `level.unlocked` × 2 | t11 and t15: "open to everyone now" |

## 2. The trigger rules this implies
- **Threshold: 3 deals with the previous level's dealer.** At activation, every team with 3 or more was let in. After it, each team got in the tick its 3rd deal settled. This matches Abuela's `/api/dealers` block (`unlock.early_min_deals: 3`) and the simulator's El Chato (`early_deals_with: abuela, early_min_deals: 3`).
- **Deals before the announcement count.** Ours settled at ticks 54–69, and El Chato was announced at tick 71.
- **A deal at the dealer's opening price does not count** (RULES.md). t15 had 5 Abuela buys before activation, 3 of them at her opening price with no counter, and was never let in early.
- **Sales to the dealer do not count.** Of the 9 teams that also sold to Abuela before unlocking, 6 have a count equal to their buy settlements exactly (t02, t04, t08, t09, t12, t13). Counting their sales too matches **none** of the 9 (for example t09: 4 buys + 3 sales, server count 4). So **selling duplicates to Chato will not open L3 early** (this corrects B3's "unverified").
- **The server's exact count is not reproduced** by any simple rule: buys match 8 of 16 teams, buys where the team bid 7 of 16. The two facts above are what matter: buys only, and not at the opening price. Aim for 4 to be safe.
- **L3 and up, by analogy (unverified):** 3 negotiated purchases from the L2 dealer (El Chato), then the same for L4 with L3. At each `level.announced`, read `GET /api/dealers/<id>` → `unlock` (`early_deals_with`, `early_min_deals`, `early_min_level`, `open_to_all_at`).

## 3. Published open-to-all times
**None for L3–L5.** Every worktree's `/api/schedule` fixture is the same snapshot (h0.517). Its persona entries are only "Finale: stalls close" (Abuela disabled at h23, Sunday). Levels are activated by an admin (`POST /api/admin/levels/{lid}`), and Friday's head start was 1.0 h, given in `level.activated` itself.

**What an early unlock is worth:** **time, and nothing else.** Every dealer gives each team the same allotment per hour, so arriving early drains nothing. If L3 activates early in a day, its head start of about 1 h barely matters, because everyone reaches it within the round. It matters when a level activates late, for example within 1 h of the doors closing.

## 4. Does Saturday reset the round's best three?
**Not verifiable offline. Plan for yes.**
- **Why:** RULES.md says "Each day is a round, and rounds are averaged", and a new round counts by the share of its day played. So each round has its own components. W5 left the ladder's reset as unverified.
- **Check at about 09:05:** `uv run bazaar evals score-check` (W5, read-only). If `ladder_points` falls to ~0 when Saturday's round opens, the best three restart per round, and Saturday needs **3 new deals per level**. If they don't reset, Friday's Abuela best three (shares 0.73 mean) stay, and only better deals replace them.

## 5. Saturday plan: L2 best three and L3 ready before it is announced
Status at Friday's close: level 2 (Abuela and El Chato open to us), 0 deals with Chato.

**Cash:** 353 P at 09:00, plus 150 P at the grant; `cash_floor` 270 (W7). Under `max_spend_per_game_hour` 150 (a rolling hour), with one thread per dealer.

| when (doors at 09:00; the grant lands at 09:03 if the clock jumps to h4, ~10:24 if it resumes) | what | spend | needs |
|---|---|---|---|
| 09:00–09:06 | **L1 best three:** Abuela commons and uncommons (W3 plans 8→11 and 21→25, W7's cards SAL-02, SAL-07, MAL-06) | ~54 P (62 reserved) | nothing new |
| after the grant, ~3 min each | **L2 best three, and the L3 early unlock:** 3 Chato uncommon **buys** at 27→31 | ~84 P (93 reserved) | **proposal** `dealer_price_caps = chato:uncommon=31` (W3 #81). The desk does it with `ladder_level_deals = 3`, or by hand with `dealer buy <ref> --start 27 --max 31 --dealer chato` |
| at `level.announced` (L3) | read the teaser and `GET /api/dealers/<id>` `unlock` | – | `bazaar plan levels` |
| at `level.activated` (L3) | with 3 Chato buys, L3 opens to us at once (Friday's pattern) | – | – |
| first hour of L3 | **L3 best three** (`ladder_floor_quantile` has no floor yet, so it falls back to the deepest-discount ladder). If the Collector buys, `dealer sell` (B3) earns ladder deals but **not** the L4 unlock | – | read "how" |
| then | 3 negotiated **buys** from L3, for the L4 early unlock | – | depends on its menu and our caps |

Spend in the first rolling hour is ~138 P of 150; cash after the grant stays at ~365 P, above the 270 floor.

**Without the cap proposal:** L2's best three stay empty: 0 Chato buys can fill (his limits 28–32 vs our 26). We still get L3 at its open-to-all time, about 1 h after activation. The ladder weight and the head start are what we give up. W7/W5 put three Chato uncommons at +1.8 round points.

**Best-three targets per level and round:**

| level | dealer | target | plan | source |
|---|---|---|---|---|
| L1 | Abuela | 3 deals, share ≥ 0.94 | commons 8→11, uncommons 21→25 | W3 #81 |
| L2 | El Chato | 3 buys, share 0.70 at a cap of 31 (0.97 at 32 with a 23 start; thin data) | uncommons 27→31 | W3 #81 |
| L3 | Collector (name only) | 3 deals | buy or sell per its menu; floors only after 5 closed threads | B3 #93 |
| L4 | Tricksters | 3 deals, with the inspector on (`allow_flags` stays off until you decide) | exact card or pack topics only | B3 #93 |
| L5 | Banker | unknown | read "how" first | – |

## 6. What each level needs from us (B3) and the cap proposals (proposals only)
| level | needs | cap change? |
|---|---|---|
| L2 Chato | 3 uncommon buys | **yes:** `dealer_price_caps = chato:uncommon=31` (rares need `chato:rare=93`) |
| L3 Collector | depends on its menu. If it buys, `dealer sell` (ladder deals, no unlock credit). If it sells rares or epics, our caps (80 for rares; epics have none) | probably. Propose a `dealer_price_caps` entry for its class once its menu and first fills are seen |
| L4 Tricksters | the offer inspector (blocks every swap); flags off | no |
| L5 Banker | unknown | – |

## Risks
- The counting rule is not exactly reproduced (8 of 16 at best). The threshold and the two exclusions rest on 9 teams with sales and one team (t15), so aim for 4 deals, not 3.
- The L3+ rules are by analogy. The real `unlock` block appears only at announce.
- The round reset is unverified until Saturday's first board.
- The Chato plan rests on 6 closed Chato uncommon threads.
- No schedule publishes L3–L5 times.
