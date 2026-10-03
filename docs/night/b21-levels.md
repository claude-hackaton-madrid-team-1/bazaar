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
- **Which deals count is unverified.** No simple rule reproduces the server's counts, and the feed contradicts both exclusions in places:
  - **Opening-price deals:** RULES.md says a deal at the dealer's opening price does not count. Our own count was 4, and it includes LAV-03, taken at Abuela's first ask of 7 (we bid 6 first, and she answered with her opening line at 7). t15 had 4 Abuela buys before activation (ticks 75–82), 3 of them at her opening price, and was not let in early (it got in at open-to-all, after a 5th buy at tick 126).
  - **Sales:** of the 9 teams that also sold to Abuela before unlocking, 5 have a count equal to their buys (t02, t04, t09, t12, t13). But t08's 3 are a pack at her unmoved opening of 17, a **sale** (20→23) and one negotiated buy. And t16 got in the tick its sale settled (tick 132), after 4 buys that had not let it in. **t08 and t16 suggest a sale may count.** This is not the correction of B3 an earlier version of this report claimed: whether selling to Chato opens L3 early stays unverified.
  - **Fits at fd37ff6** (`bazaar plan levels`, 16 teams unlocked by deals): buys 8/16, buys where the team bid 7/16, not at the opening price 6/16, every deal 4/16, buys not at the opening price 4/16. "Buys", "buys where the team bid" and "every deal" are contradicted by t15.
- **What the plan does:** it counts only negotiated buys, below the opening price (`COUNTED`, a lower bound: it gives us 3 where the server gave 4). It never relies on sales or on opening-price deals, and it aims for 4. In `COUNTED`, "the opening" is the dealer's first price in the thread. That is her opening ask when she speaks first, and also when we bid first and she answers with her opening line (our LAV-03).
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

**If #71 is merged, its effective floor is 370** (W7 §5): 353 < 370 blocks every buy until the grant. That is 3 minutes under the jump (09:03), and ~84 minutes under the resume (~10:24). After the grant, the headroom is 503 − 370 = **133 P**: the Abuela three (~54 P) and two Chato uncommons (~56 P) fit, and **the third Chato buy is refused**, so no early L3. #71 is unmerged ("do not merge as is").

| when (doors at 09:00; the grant lands at 09:03 if the clock jumps to h4, ~10:24 if it resumes) | what | spend | needs |
|---|---|---|---|
| 09:00–09:06 | **L1 best three:** Abuela commons and uncommons (W3 plans 8→11 and 21→25, W7's cards SAL-02, SAL-07, MAL-06) | ~54 P (62 reserved) | nothing new |
| after the grant, ~3 min each | **L2 best three, and the L3 early unlock:** 3 Chato uncommon **buys** at 27→31 | ~84 P (93 reserved) | **proposal** `dealer_price_caps = chato:uncommon=31` (W3 #81). The desk does it with `ladder_level_deals = 3`, or by hand with `dealer buy <ref> --start 27 --max 31 --dealer chato` |
| at `level.announced` (L3) | read the teaser and `GET /api/dealers/<id>` `unlock` | – | `bazaar plan levels` |
| at `level.activated` (L3) | with 3 Chato buys, L3 opens to us at once (Friday's pattern) | – | – |
| first hour of L3 | **L3 best three** (`ladder_floor_quantile` has no floor yet, so it falls back to the deepest-discount ladder). If the Collector buys, `dealer sell` (B3) earns ladder deals; whether a sale counts toward the L4 unlock is unverified, so do not rely on it | – | read "how" |
| then | 3 negotiated **buys** from L3, for the L4 early unlock | – | depends on its menu and our caps |

Spend in the first rolling hour is ~138 P of 150; cash after the grant stays at ~365 P, above the 270 floor. **The reservations add up to 62 + 93 = 155 P, above the 150 cap.** If the guardrail counts open reservations against the cap, start the Chato threads only after the Abuela three settle (at ~54 P actual).

| step | floor 270 (today) | floor 370 (#71 merged) |
|---|---|---|
| 09:00 Abuela three (~54 P) | at once (83 P headroom) | after the grant (09:03 jump, ~10:24 resume) |
| Chato uncommons, after the grant (~84 P) | all three (233 P headroom) | two of three (133 − 54 = 79 P) |
| L3 early unlock | if the 3 buys count | no (2 Chato buys) |

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
| L3 Collector | depends on its menu. If it buys, `dealer sell` (ladder deals; unlock credit unverified). If it sells rares or epics, our caps (80 for rares; epics have none) | probably. Propose a `dealer_price_caps` entry for its class once its menu and first fills are seen |
| L4 Tricksters | the offer inspector (blocks every swap); flags off | no |
| L5 Banker | unknown | – |

## Risks
- The counting rule is not reproduced (8 of 16 at best), and both exclusions are contradicted somewhere: our LAV-03 at her opening counted, and t08 and t16 suggest a sale may count. Only the threshold (3) is solid. Aim for 4 negotiated buys, not 3.
- The L3+ rules are by analogy. The real `unlock` block appears only at announce.
- The round reset is unverified until Saturday's first board.
- The Chato plan rests on 6 closed Chato uncommon threads.
- No schedule publishes L3–L5 times.
