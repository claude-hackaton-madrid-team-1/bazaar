# Saturday playbook: hour by hour (Sat 3 Oct 2026, doors 09:00–23:00, 30 s ticks)

Night item B6, draft PR, base `main`. Nothing here touched the live game. Machine-readable twin:
`docs/night/saturday-schedule.json`, the output of `bazaar timeline … --plays docs/night/saturday-plays.json --json`.
Regenerate it at 08:55 from the live API with `uv run bazaar timeline --from-api` (two keyless GETs).

**How to read it.** Every row has a **trigger**: a feed event such as `schedule.fired`, `duels.scheduled` or
`day.opened`, which the monitor alerts on. The wall times are only expectations. Game hours are written `h`.
Points are W5's score model (PR #78):
- 1 Saturday round point = **0.40 final points**; a Friday round point = 0.20.
- The ladder weight is fitted. The duel, trade, bench and venue weights are **assumed** (12.5 / 5 / 15 / 15).

## 1. The clock: two columns until 08:55

Friday's game started late, so the game-hour schedule and the Madrid clock no longer line up:
- Friday's first tick was at about 20:21 (60 s ticks, `t_hours` = tick / 60).
- The clock froze at **tick 159 = h 2.65** at 22:59:47 (`feed_events.received_at`).
- `t_hours` counts "game hours since the opening; the clock stops overnight" (openapi `Clock`).
- So unless the organisers jump the clock, **Saturday 09:00 is h 2.65, not h 4.0**, and every event lands
  1 h 21 min later than the published calendar suggests.

The tool reproduces Friday: anchored at the clock fixture (h 0.5167, 20:52), it puts the practice duels at
22:20:59; they fired at tick 120 = 22:20:48.

| | **resume** (the clock picks up at h 2.65) | **jump** (the organisers set h 4.0 at 09:00) |
|---|---|---|
| Evidence | what the server does by itself | the schedule's own `day_opens` row: h 4.0 = Sat 09:00 |
| Game hours turn at | hh:21 (quotas, `max_spend_per_game_hour`) | hh:00 |
| 09:00–10:21 | **still Friday's round** (weight 0.5); practice duels finish | Saturday's round from 09:00 |
| h 17 Market Test | slips to Sun 09:21 | Sat 22:00 |
| Sunday | ends at h 22.65: the h 23 finale and h 24 freeze never fire, so the organisers must step in at some point | as published |

**G0 (08:55, doors closed):** three keyless reads:
1. `uv run bazaar clock` → `t_hours`. ≈ 2.65 → resume; ≈ 4.0 → jump. Any other value: the tool re-anchors on it.
2. `uv run bazaar timeline --from-api --compare docs/night/saturday-schedule.json` lists every event the organisers
   added, removed or re-timed since Friday's fixture. They may **re-time the schedule instead of jumping the clock**.
   Then `t_hours` reads 2.65, but the published `at_hours` moved. If anything is listed, only
   `uv run bazaar timeline --from-api` counts and the columns in § 4 are stale.
3. `uv run bazaar timeline --from-api` prints the day.

**Confirm at 09:01–09:05 from the feed.** The clock can also be jumped at 09:00:00, after the 08:55 read.
- Jump: round 2's `schedule.fired` and the grant's `gift.given` (150 P) arrive by 09:03.
- Resume: neither has arrived by 09:05, and the h 3 bench fires at ~09:21.
- If the clock changes mid-day (`clock.changed`, an announcement), re-run the timeline.

## 2. Where Saturday's points are (W5 model; this ranks our attention)

| # | Lever | Window | Saturday round pts | Final pts | Cash | Basis |
|---|---|---|---|---|---|---|
| 1 | **bazaar-duels up and inside the limit** for Duels I and II | 2 × up to 96 min | up to 12.5 (each duel ≈ 0.12 at the top-3 level) | up to 5.0 | 0 | weight assumed |
| 2 | **Market Test: something of ours scores each session** | 8 × 8 min | 7.5 at stall level (0.94 per session); a venue adds only ~+0.45 if the free stall already scores for us | 3.0 (+0.19, B2) | 270 P locked | weight assumed; G3 decides |
| 3 | **Ladder best three at Abuela** (~0.95 share) | any time. Under resume, 09:00–10:21 also counts for Friday's round | restart per round: **10.7** at Friday's top-3 mean (1.11), **7.9** at 1.5 (likelier, with Chato and maybe level 3 all day); carry-over: +2.44 (0.733 → 0.95). Under resume, also +2.44 Friday round points (+0.49 final) | 3.2–4.3 / 1.0 | ~54 P | fitted; G2 decides |
| 4 | `duel_policy = v2` (#86) | before Duels I | +3.6 if v1 sits at 0.70 of the top-3 (W2 lift 1.42×) | 1.4 | 0 | lift measured in sim, weight assumed |
| 5 | Three Chato uncommons at 0.5 share | after the cap decision (G5) | +2.82 (carry-over) | 1.1 | ~87 P | fitted level weight 0.5 |
| 6 | W4's 7 trades (+80 P of surplus) | after the grant | +1.3 to +4.0 | 0.5–1.6 | 82 P | weight assumed |
| 7 | Value created on our venue (organic) | – | ~0: Friday had 0 settlements on any team venue (B1) | ~0 | – | B1 |

**Reading it:**
- Rows 1–2 are uptime problems, not trading problems: a missed duel session or a dead broker costs more than
  any trade.
- Row 3 is the cheapest point on the board: about 54 P for 1.0–4.3 final points.
- Every number is a W5 model output; the top-3 means are Friday's and will move.
- Rows 5–6 need cash that only exists after the grant (§ 5).

## 3. Before the doors open (08:30–08:59)

| Step | Who | What | Check |
|---|---|---|---|
| 1 | Marius | **G1, what is live at 09:00.** bazaar-taker and bazaar-maker have `BAZAAR_LIVE=1` (since Sat 01:45). bazaar-duels runs `duel run --play` on main's v1. Whatever is on main at 09:00 trades on its own. | `railway variable list --service <s>` per service; `/health` mode |
| 2 | Marius | Merge decisions. Merge **#60 no later than before Duels II**: main's v1 closed outside the limit in 67/2800 two-issue zoo duels, #60 in 0/2800. #61 still has 2 open P1s. #62 refuses to start a live process without a shared `DATABASE_URL`, so check that variable on each live service first. A GUARDRAILS.md change reaches Railway only on a redeploy. | B5's merge order (`night/b5-rehearsal`) |
| 3 | Marius | **G6:** `duel_policy` v1 or v2 for Duels I (#86 needs #60 underneath). | `uv run bazaar rules` after the deploy |
| 4 | Marius | **G5:** Chato caps (`dealer_price_caps = chato:uncommon=31`, `ladder_level_deals = 3`, #81) | `uv run bazaar rules` |
| 5a | Marius | **PR #71's head changed (03:14–03:23, r1 alert).** It no longer only builds the venue: `allow_venue_open = true`, `cash_floor` 270 → 100 + `venue_bond_reserve` 270, and the **maker opens a board venue on its own** at the first tick with `t_hours ≥ 6.5` (`venue_open_after_game_hours`). The PR text says "~11:30", which holds only under jump. Under **resume**, h 6.5 is **12:51, the same tick Duels I starts**. Until the venue opens, every purchase keeps 370 P, and 353 < 370 freezes **all buys until the grant at 10:24** (W7). This contradicts the 02:30 "no venue tonight" decision until Marius re-decides. | `git diff origin/main origin/feat/venue-broker-build-only -- GUARDRAILS.md` |
| 5 | Marius | Venue: 09:00, after G3, or never (B2 #92: `cash_floor` 270 → 50 + `allow_venue_open = true`, proposed, not applied). Under **resume**, read § 5 first: a 09:00 venue and the ladder's best three do not both fit in 83 P. | `uv run bazaar rules` |
| 6 | anyone | One monitor, on one laptop: `uv run bazaar monitor --notify`. Never a second laptop running taker + maker (W5). | alerts arrive |
| 7 | anyone | **G0** (§ 1) at 08:55, then `uv run bazaar timeline --from-api` | the column for the day |
| 8 | Marius | **Merge window (r2 X16).** Every merge to main that touches `src/**`, `GUARDRAILS.md`, `STRATEGY.md`, `RUNTIME.md`, `uv.lock`, `pyproject.toml`, `vendor/bazaar-kit/**`, `questions/**` or `.railway/**` redeploys the live bazaar-duels, bazaar-taker and bazaar-maker (`.railway/railway.py` `watchPatterns`). A redeploy means no moves during the build, a reset of the duel loop's progress, and orphaned dealer threads. So merge **before 09:00**, or only **between duel sessions**, never inside a Duels I/II window or a Market Test with a venue open (§ 4 windows; this PR's own `src/**` change included). | Railway deploy log quiet before each session |
| 8b | Marius | **#72 before 09:00 (r2 X7, high on main).** The Railway taker on main can break `cash_floor` and the 150 P/h cap within one tick: 3 dealer-thread bids filled at one boundary = 240 P against 150. #72 fixes it (r2's tests XPASS on its tip). It sits on #61 → #68, so the whole chain goes before 09:00. | r2's `night/r2-bite-hunter` @ 60f939e |
| 8b2 | Marius | **Day PRs whose new behaviour is ON by default once merged (r1, REVIEWS.md):** #89: the live taker skips dealers under a learned blocker and writes the feed into `feed_events` each tick (`BAZAAR_LEARN=0` turns it off). #91: duels, taker and maker score their own decisions every 6 ticks, in one long Postgres transaction that can delay a ledger accept during a redeploy's schema ALTER (r1 medium). #71's head: the venue opens on its own at h 6.5 (step 5a). If any of them merges, set its switch on Railway first, or merge it outside the duel windows. | r1's findings per PR |
| 8c | anyone | **After any redeploy (r2 X3, B17 open):** the taker's open dealer threads are orphaned: no longer driven or closed, and a deal the dealer makes on them is never booked as spend. Check with `uv run bazaar threads --status open` and close the orphans. With #68: `uv run bazaar flatten --live --threads`, after PAUSE (it also cancels every open offer; a closed dealer thread counts as a walk). On main: the SDK's `close_thread(id)`. | no open thread older than the restart |
| 9 | anyone | **09:00 restart (r2 X4).** With the doors closed, every loop polls the clock every 300 s (`ticks.CLOSED_POLL_MAX_S`). So the live taker, maker and duels can start up to 5 min late. Restart the three services just after 09:00:00, or watch for their first tick line. | first `tick` line in each service log by 09:01 |
| 10 | Marius | Stagger, opt-in: `BAZAAR_TICK_OFFSET_S` (#78: duels 0, monitor/broker 0.5, dealer 1, taker 2, maker 4), or the broker's `--read-offset 0.5` (#92), never both | `uv run bazaar budget --stagger` |

## 4. Hour by hour

`t` = game hour. Durations: a Market Test lasts 16 ticks (8 min). A duel session needs `⌈duels ÷ concurrent⌉ × duel_ticks`
ticks at most: Duels I = ⌈34 ÷ 3⌉ × 16 = 192 ticks (96 min); Duels II = ⌈68 ÷ 6⌉ × 16 = 192 ticks. Practice was
306 duels for 18 teams = 34 per team per round, and our Friday waves closed at ticks 132, 144 and 156. Early deals
free a slot sooner.

| t | resume | jump | Trigger | Do (who) | Check | Points |
|---|---|---|---|---|---|---|
| 2.65 | **09:00** | – | `day.opened` | **Resume: Friday's round is still running.** Do the ladder's best three now (taker desk, or `uv run bazaar dealer buy <ref> --start <s> --max <m> --live`; W3/W7 picks SAL-02 C, SAL-07 U, MAL-06 U, ~54 P). Headroom is only 83 P until the grant. Practice duels resume (our 6 live, 8 not started; not scored) and take the duel loop's first claim on the accept slot until ~09:17 (33 ticks of 30 s). | `/me` `score.ladder_points` rises | Friday round +2.44 (+0.49 final), and also Saturday's if the ladder carries over |
| 3.0 | **09:21** | overdue | `schedule.fired` bench | **First Market Test ever** (Friday never reached h 3). The free stalls arrive at +3 h. Nothing to run unless a board venue is open. | **G3** at ~09:30: `score.bench_points`, `bench_venue`, `bench_efficiency` in `/me`. This PR's `uv run bazaar status` prints them; on main, read the monitor's `agent.me` snapshot (`score` dict) | 0.94 at stall level |
| 4.0 | **10:21** | **09:00** | `schedule.fired` round | **Round 2 (Saturday) starts.** El Retiro is released (RET in dealer menus, packs and boards; price RET cards from live `your_value`). | **G2** on the first board refresh (≤ 5 ticks): `uv run bazaar evals score-check` (#78), or `score.ladder_points` = 0 means the ladder restarts per round | see G2 |
| 4.05 | **10:24** | **09:03** | `gift.given` / grant | +150 P and a pack. Cash 353 → 503, headroom 83 → 233. Open the packs (luck never scores). Then `uv run bazaar plan pages` (#87) and `uv run bazaar trade-plan --live` (#79, read-only). **The live maker cancels any board offer it did not post, within one tick (r2 X19)**, so W4's bids posted by hand die. Either send only W4's thread swaps (thread offers are untouched), or switch the maker to dry run while hand offers stand: `railway variable delete BAZAAR_LIVE --service bazaar-maker`, then set it back with `printf 1 \| railway variable set BAZAAR_LIVE --stdin --service bazaar-maker`. On main, PAUSE does not stop the maker's cancels (#68 fixes that). **If #71's current head is merged, the maker also runs our venue's broker**: never put it in dry run around a Market Test, where a dead broker scores 0 for the session (B2); use thread swaps only. A Railway variable change also restarts the service. Venue now if G3 said so (§ 5). | `uv run bazaar status` cash | W4 +1.3 to +4.0 |
| 5.0 | **11:21** | **10:00** | bench | Market Test. Under jump this is **the first one: run G3 at ~10:10**. With a board venue: broker loop alive; B2's probe (`uv run bazaar broker probe --auto`, then `--live`, Marius's call). | `tail .local/agents/broker_sessions.jsonl` | 0.94 / session |
| 6.5 | **12:51–14:27** | **11:30–13:06** | `duels.scheduled` "Duels I" | **Duels I**: price only, decay 0.06, 34 duels, 3 at a time, 16 ticks each. bazaar-duels must stay up for the whole window: **no merge to main** inside it (each one redeploys the live services). #62 should be live before it: on main, a dropped Postgres connection silently stops the duel player (r2). **Accept race (r2 X17):** the taker reserves the team's one accept 2 s into the tick, but a Jev-slow duel tick books it at up to 3.5 s. A duel on its deadline tick can lose its accept and score 0. #86's v2 fixes it; v1 is still exposed (B15). | after the first wave (~16 ticks): `uv run bazaar duel done`; `uv run bazaar evals report` | up to 12.5 shared with Duels II |
| 7.0 | **13:21** | **12:00** | bench | Market Test, **during Duels I** (3 live duels = 6 calls a tick; steady total ≈ 26 calls a tick, 0.87 req/s, W5) | broker sessions log | 0.94 |
| 9.0 | **15:21** | **14:00** | bench | Market Test. Quiet afternoon: Chato and the ladder (if G5), trades, re-plan (`uv run bazaar ladder floors --source feed --since-tick <Saturday's first tick>`, #81) | – | 0.94 |
| 11.0 | **17:21** | **16:00** | bench | Market Test | – | 0.94 |
| 13.0 | **19:21–20:57** | **18:00–19:36** | `duels.scheduled` "Duels II" + bench | **Duels II**: price + delivery day, decay 0.08, 68 duels, 6 at a time. **#60 must be live.** **G4** on the first payload (`days_meaning`, `your_days_weight`). The Market Test starts the same tick: 6 live duels = 9 calls a tick, so stagger. | `uv run bazaar duel done` | Duels II share; 0.94 |
| 15.0 | **21:21** | **20:00** | bench | Market Test (inside Duels II under jump only if it ran long) | – | 0.94 |
| 16.0 | **22:21** | **21:00** | bench "hard" | **Hard Market Test**: 12 traders, firmer, more impatient. Same broker loop (`--bench-preset hard` is optional, priors only) | – | 0.94 |
| 17.0 | Sun 09:21 | **22:00** | bench | Market Test (Saturday's last, jump only) | – | 0.94 |
| 18.0 | **23:00** | **23:00** | `day_closes` | Doors close (calendar, both columns). Offers stay open and nothing settles overnight: anything still open settles at Sunday's first tick at Saturday's prices. Review before 22:55: `uv run bazaar sell offers` (cancel: `uv run bazaar sell cancel <id> --live`). | – | – |

**Hourly limits in force all day:** 1 accept, 12 listings and 1 message per conversation per tick; 6 conversations;
30 open offers. Abuela allows 8 deals per team per hour and 3 packs per hour. Our own `max_spend_per_game_hour` is 150
and `max_packs_per_game_hour` is 3. The game hours turn at **:21 under resume**. `GET /api/clock` → `limits` has the
numbers in force; the feed announces every change.

## 5. Cash by hour (`cash_floor` 270 unless Marius changes it)

| | resume | jump |
|---|---|---|
| 09:00 | 353 P, headroom **83** | 353 P, then 503 at 09:03 (headroom 233) |
| Ladder best three (~54 P) | 09:00–10:21: fits in 83, and counts for Friday's round | 09:03+ |
| Grant | 10:24: 503 − 54 = 449 | already in |
| Venue (270 P; B2's floor 50 needs cash ≥ 320) | after the grant, before 11:21 (h 5): 449 → 179 | 09:00 per B2, or after G3 at ~10:10 |
| W4 trades (82 P) | after the venue: 179 − 82 = 97 ≥ 50 | 09:03+ |
| Chato three uncommons (~87 P) | only with floor 50 and no venue, or on Sunday's grant | same |

**If #71's current head is merged** (floor 100 + reserve 270 until the maker opens the venue at h 6.5), purchases need cash ≥ 370 before the venue opens:
- **Under resume:** 353 P blocks every buy until the 10:24 grant, including the Friday-round ladder deals. After the grant, 503 − 370 = 133 P is spendable until 12:51.
- **Under jump:** 503 − 370 = 133 P from 09:03 until 11:30.
- **Either way:** W4's 82 P plus the ladder's 54 P (136 P) is 3 P over that budget.

**Order under resume (today's GUARDRAILS):** ladder → G3 → grant → venue → trades.

W7 (03:27) proposes the reverse for the 83 P: W4's trades first, the ladder after the grant. B6 keeps the ladder first:
- **Trades are a flow** (assumed: a trade scores in the round it settles in). A trade settled before 10:21 counts in Friday's round, which weighs half. The same trade
  after 10:21 counts in Saturday's round at full weight.
- **The best three are a level.** If the ladder carries over (G2), deals done before 10:21 count in Friday's round
  and in Saturday's. If it restarts, they still lift Friday's round by +2.44 round points, and Saturday's best three
  are redone after the grant.
- **The maker cancels hand-posted board bids** (r2 X19), so W4's morning board posts need the maker in dry run anyway.

A venue at 09:00 under resume (B2's floor 50) would leave only 33 P above the floor. The ladder's best three would
then wait until 10:24, the point where they stop counting for Friday's round.

## 6. Gates (each one is a question with a check)

| Gate | When | Check | Then |
|---|---|---|---|
| G0 clock | 08:55, confirmed 09:01–09:05 | `uv run bazaar clock` → `t_hours`; `bazaar timeline --from-api --compare docs/night/saturday-schedule.json`; the feed's round/grant events | the column (§ 1), or the tool's live output if the schedule moved |
| G1 live code | before 09:00 | Railway variables, `/health` | Marius merges and redeploys. After 09:00, merge only between duel sessions (§ 3 step 8) |
| G2 ladder restart | first refresh of round 2 | `uv run bazaar evals score-check` (#78), or `score.ladder_points` (this PR's `bazaar status`) | 0 → Saturday's best three are worth 10.7 round points: run them now. Otherwise Friday's deals stand. |
| G3 free stall | after the first Market Test | `score.bench_points` with no venue | > 0 → venue optional (+0.19 final, B2). null/0 → every session without a venue costs 0.94 round points: open one (B2 runbook) |
| G4 days sign | first Duels II payload | `days_meaning` | keep `duel_days_signed = false` unless the sign is stated (B8) |
| G5 Chato | any time | #81 switches | +2.82 round points for ~87 P. Three negotiated Chato deals are probably also the early unlock for level 3, by analogy with Abuela's `early_min_deals 3` |
| G6 duel policy | before Duels I | #86 on top of #60 | v2 (GO 5/5 in the W2a gate) or v1 |

## 7. Not on the schedule

- **Levels** (`level.announced` → `level.activated` → open to all). Friday's Chato:
  - announced at h 1.18, activated 27 min later, open to all 1.0 h after that;
  - we unlocked him at activation with 4 Abuela deals;
  - on `level.announced`, read `GET /api/levels` and B3's persona plan (#93).
- **Pace or limit changes** (`clock.changed`, announcements) move every session end: re-run `uv run bazaar timeline --from-api`.
- **Dealer cool-offs:** `closed_reason` `cooloff` with `until_tick`.
- **Kill switch:** `touch .local/PAUSE` in each place a live process runs. For Railway:
  `for s in bazaar-duels bazaar-taker bazaar-maker; do railway ssh --service "$s" -- touch /app/.local/PAUSE; done`.
  PAUSE leaves open offers up. On main it does not stop the maker's cancels (#68 fixes that).

## 8. Unknowns that move this plan

- **Weights.** Duel, trade, bench and venue weights are assumed (W5). Duels I vs II may weigh by session or by duel
  count (0.18 vs 0.09 per duel, or 0.12 for both).
- **Whether the ladder restarts per round** (G2), **whether the free stall scores for us** (G3), and the sign of days (G4).
- **Clock.** Whether `t_hours` advances with the wall clock at 30 s ticks: the API says it does, and the simulator does
  too (`t_seconds += tick_seconds`). The organisers can also change the pace (5–60 s) or fire a session by hand
  (`/api/admin/duels`, `/api/admin/bench`).
- **Duel session length** is an upper bound. The real end is the last `duel.closed` of the session.
