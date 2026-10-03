# B26: new sets mid-game, and Sunday

Night of 3–4 Oct 2026. Branch `night/b26-sunday`, draft PR stacked on `night/b20-venue-path` (#118). Nothing here touched the live game. Sources: the schedule fixture (`tests/fixtures/api/get_api_schedule.anon.json`), the catalog fixture, B6's Saturday playbook (#102), W5's request budget (#78), W2b (#86), B11 (#103), B20 (#118).

## 1. New sets mid-game (RET Saturday, CHA Sunday)

**What a release changes.** The catalog already lists RET and CHA with all 12 cards each, marked `released: false` (RET's `release` is `sat+0h`), and `/api/me` carries an affinity for all six sets from tick 0. At release three things change:
- the set's page appears in `/me` `album.pages` (slots 40 → 50 → 60);
- the catalog's `released` flag flips;
- the minted counts start from 0.

Dealer menus that sell "released" sets start selling it. Packs most likely draw from it too: the simulator's packs draw only from released sets.

| Area | What happens at release | Risk | Action |
|---|---|---|---|
| Catalog reads | taker and maker re-read it every tick (`agents/runtime.py`); the runtime backend caches it 30 ticks for rarity only | none | — |
| "Released" | decided only by `/me` album pages (`strategy.py`, `album.py`); the catalog flag is never read | none; a page the catalog lags is skipped without a crash (tested) | — |
| Valuation | affinity per set from `/me`, defaults to 1.0 | none | — |
| **Dealer buys of the new set** | every new card has `minted = 0`, and `supply_of` calls that "pull or wait" | **missed buys** for the first hours of each new set, while dealers mint on sale (Friday: Chato sold LAV-09 serials 2–4, Abuela LAV-04 serials 15–16, without buying them first) | `dealer_mints_unminted` (new STRATEGY.md line, default false): set true before CHA, after seeing a dealer sell an RET card on Saturday (`uv run bazaar tape`) |
| Chaser map | `intel.team_flows` infers each team's ×1.6 set from its trades; a team chasing RET/CHA cannot show it before release | chasers misattributed until they trade the new set | re-run W4's map (#79) Saturday afternoon and Sunday late morning |
| Page, ladder and pack plans | W7 (#87), W3 (#81) and B9 were computed on Friday's 4 sets | stale; the pack EV per card drops as packs spread over more sets | re-run `bazaar plan pages` and the pack EV after each release |
| Monitor | alerts only on `schedule.fired` / `announcement` / `day.*` | a release without such an event goes unnoticed | the schedule lists `set_release` rows, which should fire `schedule.fired` |
| Parsers | card refs `[A-Z]{3}-[0-9]{2}` everywhere; rarity by number assumes 12 cards a set (`evals/dealers.py`) | none for RET/CHA (12 cards each) | — |
| Simulator (`bazaar_sim`) | never releases a set mid-game (catalog flags fixed; no `set_release` in its schedule) | B5's rehearsal cannot exercise a release | follow-up: a `SIM_RELEASE_AT` switch |

No agent crashes on a new set code, and unknown refs fail closed (the guardrails refuse a buy with no rarity cap). Tests in `tests/test_new_sets.py` cover the album, the strategy, a lagging catalog and the new switch.

## 2. Sunday playbook (15 s ticks, the round counts in full)

**The clock.** The published calendar puts Sunday at h18–h24 (09:00–15:00 if each day starts on its published hour). B6 found the clock may instead **resume** where it stopped (Friday froze at h2.65), which pushes every hour-based event later. Under resume, the h23 finale and the h24 freeze never fire unless the organisers step in. So **at 08:55 on Sunday, re-run B6's `uv run bazaar timeline --from-api`**, and trust the feed (`schedule.fired`, `day.opened`) over either column. The times below are the "jump" column (the published calendar).

| h | jump | Event (schedule) | Do | Check |
|---|---|---|---|---|
| 18.0 | 09:00 | round 3 "Sunday · Chamberí" (weight 1), **CHA released**, doors open, **15 s ticks** | restart taker, maker and duels just after 09:00 (closed doors poll every 300 s, r2 X4); confirm `tick_seconds` 15 | first `tick` line per service by 09:01 |
| 18.05 | 09:03 | grant: 150 P (no pack) | the CHA page appears in `/me` `album.pages` (see § 1); the ladder's best three for round 3 if the ladder restarts per round (B6's G2, decided on Saturday) | `uv run bazaar status` shows a CHA page |
| 19.0 | 10:00 | Market Test (16 ticks = **4 min**) | our venue (B20) or the free stall | `/me` `score.bench_efficiency` after 10:05 |
| 20.0 | 11:00 | **Duels III**: price + days, **decay 0.10**, **12-tick duels (3 min)**, 4 at a time, 2 rounds | bazaar-duels up for the whole window; no merge to main inside it | the duel log after the first wave (~3 min) |
| 21.0 | 12:00 | Market Test, **during Duels III** if it runs long | — | — |
| 22.8 | 13:48 | finale warning | stop discretionary trading; nothing new should open after this | — |
| 23.0 | 14:00 | **Final duels** (decay 0.10, 12 ticks, 4 at a time, 1 round); Abuela disabled ("stalls close") | duels only; the taker's dealer threads with Abuela end | — |
| 23.9 | 14:54 | freeze warning | — | — |
| 24.0 | 15:00 | scores freeze, the Bazaar closes | — | — |

**How long the duel windows are** (34 duels per team per round, as at practice; B6's method):
- Duels III: 2 rounds × 34 = 68 duels, ⌈68 / 4⌉ × 12 = 204 ticks ≈ **51 min** (11:00–11:51 at the latest). The h21 Market Test (12:00) falls just after it.
- Final: 34 duels, ⌈34 / 4⌉ × 12 = 108 ticks ≈ **27 min** (14:00–14:27), inside the freeze.

**Duel settings for decay 0.10** (all proposals from the duel PRs; decided Saturday evening, § 3):
- `duel_policy` = v2 (#86): 1.42× v1 in W2b's arena at 0.08/0.10, 1.55× on W2a's gate; 0 outside-limit closes.
- `duel_endgame_min_share` = 0.3 with `duel_endgame_ticks` = 1 (#103, B11): pie share against exploiters 0.19 → 0.30, honest results 1.007×, deal rate 0.994×.
- Days: `duel_days_signed` only if Saturday's first Duels II payload confirmed the sign (B6's P7, B8). Otherwise #60's worst case.
- 12-tick duels give half the room of 16-tick ones. Every priced counter costs 10 % of the pie, so v2's "silence is free" matters more on Sunday than on any other day.

**Request budget at 15 s** (W5's model, `uv run bazaar budget --ceiling --stagger --tick-seconds 15`):
- The steady load is 1.80 req/s. The loops at their ceiling make **4.73 req/s**, 5 % under the 5 req/s limit.
- **No** MCP, desk or `bazaar status` loops, and no extra `dealer buy` process during 15 s ticks. 0.5 req/s of operator tools gives 5.27 (NO-GO) and 3 extra `dealer buy` processes give 5.73.
- 4 live duels (Duels III) add about one call a tick to the duel loop (6 at 3 live, 9 at 6).
- **Turn the stagger on** (`BAZAAR_TICK_OFFSET_S`: duels 0, monitor/broker 0.5, dealer 1, taker 2, maker 4). With it, every modelled setup loses 0 calls at the ceiling.
- One laptop at most next to Railway (the monitor), never a second taker or maker.

**Tick-denominated parameters shrink in wall time at 15 s.**
- `steer_max_ttl_ticks` = 240 is 1 game hour on Sunday (4 h at Friday's 60 s).
- `dealer_max_ticks_per_thread` = 14 is 3.5 min.
- The keeper's `RETRY_TICKS` = 10 is 2.5 min.
- A Market Test is 4 min, so a broker that misses one minute misses a quarter of the session.

## 3. Decisions for Marius on Saturday evening (for Sunday)

Each decision has a trigger to read on Saturday, and none of them changes anything until Marius applies it.

| # | Decision | Default today | Proposal | Read on Saturday |
|---|---|---|---|---|
| 1 | `duel_policy` for Duels III and the Final | v1 | v2 (#86) | Duels I/II results: `uv run bazaar duel done`, `bazaar evals report` |
| 2 | `duel_endgame_min_share` / `duel_endgame_ticks` | 0 / 2 | 0.3 / 1 (#103), only with v2 | Duels II: did any rival squeeze at 1 P inside our limit? |
| 3 | `duel_days_signed` | false (worst case) | true only if the Duels II payload's `days_meaning` confirmed the sign (B8) | the first Duels II payload (B6's P7) |
| 4 | `dealer_mints_unminted` | false | true before CHA | a dealer sale of an RET card on Saturday with no prior copy (`uv run bazaar tape`) |
| 5 | `BAZAAR_TICK_OFFSET_S` (stagger) on every service | unset | on (duels 0, monitor/broker 0.5, dealer 1, taker 2, maker 4) | Saturday's refusals (`rate_limited`) in the service logs |
| 6 | Operator tools during 15 s ticks | allowed | none: no MCP, desk or `status` loops, no extra `dealer buy` | — |
| 7 | The venue on Sunday | as Saturday ended | keep it open, never close it: the bond coming back scores nothing, and a closed venue scores 0 in the h19/h21 sessions | `/me` `bench_efficiency` per session |
| 8 | `cash_floor` on Sunday | 100 (#71) or 50 (B20) | lower it to 0 after the Final starts, or as soon as nothing planned needs cash: cash never scores | Saturday's spend vs the plans |
| 9 | The ladder in round 3 | — | if the ladder restarts per round (B6's G2), the best three at Abuela from 09:03 with the 150 P grant; **Abuela is disabled at h23**, so finish dealer work before 14:00 | G2 on Saturday |
| 10 | Merges on Sunday | — | all before 09:00; none inside 10:00–10:04, 11:00–11:51, 12:00–12:04 or 14:00–14:27 (each merge redeploys the live services) | — |
| 11 | The clock | — | 08:55: B6's `bazaar timeline --from-api`; under resume, ask the organisers when the finale and the freeze will fire | Saturday's G0 answer |
| 12 | Sealed packs | none opened by code (r2 X21) | open them by hand (`open_pack`) after the 09:03 grant, unless an opener has merged (BACKLOG B20, packs) | — |

## What changed in this PR

- `dealer_mints_unminted` in STRATEGY.md (`false` = today) and `strategy.supply_of`.
- `tests/test_new_sets.py`: 4 tests (a release through album and strategy, the dealer switch, a lagging catalog, the strategy file's default).
- This report. Gates: 1,980 passed, ruff, black and mypy clean.

## Risks

- The Sunday times assume the published calendar ("jump"). Under "resume" (B6), everything moves later and the finale needs the organisers.
- Zero-minted dealer sales (serial 1) were not directly observed on Friday; only dealers minting beyond the existing copies was. Decision 4 waits for a Saturday observation.
- The Sunday request budget has 5 % headroom at the loops' ceiling (W5): any extra process can push the tick edge into `rate_limited`, and a lost accept wastes the team's slot.
