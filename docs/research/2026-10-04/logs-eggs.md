# Friday and Saturday logs, news and easter eggs (sat-logs-eggs)

Sun 4 Oct 2026, 00:10–00:50 Madrid; revised 00:50–01:30 after review (`_sat-review/review-logs-eggs.md`). Read-only research for Team 1 (t01). No game writes, no Railway writes, no keyed
requests. Scripts and queries: `docs/research/2026-10-04/logs-eggs/`.

## TL;DR

1. **The game has easter eggs, and on Saturday we found none of them.** The public feed holds 27 `egg.found`, 21
   `badge.awarded` and 6 `egg.given` events, all by other teams. 11 of 18 teams wear at least one badge on the public
   leaderboard ("Sharp ear" 11, "Trickster tricked" 6, "Castizo" 4). t01 has 0. **Eggs score 0 by rule**
   (RULES.md:122), but the badges show next to each team's name on the leaderboard and the big screen.
2. **We were handed the first clue five times and never followed it.** Between 15:38 and 16:21, Doña Pilar told us
   "ask Carmen at El Rastro about the golden chulapa" in 5 messages. Our agents never send free text, so nothing
   reacted. The chain is: Pilar's hint → Abuela ("Sharp ear") → "ask Don Ernesto about el oro de Moscú" → the hidden
   card **LAT-13 La Chulapa Dorada** (legendary, print run 1). t02 got it at 18:45, so it is gone (catalog: minted 1/1).
   The organisers' own editor bundle (public JS, Friday build) gives the mechanism: **"An egg fires when the team's
   message contains one of its phrases (accents and case ignored)"**, once per team, with a cap on total finds.
3. **The Sunday schedule changed after the doors closed (inferred, strong).** `/api/schedule` at 00:19 maps Sunday as
   game hours 16.65–22.65 = 09:00–15:00. If that holds, then from 09:00: **round 3 + Chamberí**, grant at ~09:03,
   Market Tests at ~09:21 / ~11:21 / ~13:21, **Duels III at ~11:00**, **stalls close + Grand Final at ~14:00**, freeze
   at 15:00. 16.65 is exactly Friday's 2.65 h + Saturday's nominal 14.0 h, so the server places Sunday as if Saturday
   had run without its 3.27 h of pauses. The clock still reads h13.367, so either it jumps at the open or the schedule
   moves again. The night plan (round 3 at ~11:34, Duels III at ~13:34, "Sunday morning counts for Saturday") is
   **probably stale; confirm at 08:55**. Offsets from the Sunday open hold either way (§2.3).
4. **News: 11 items on Saturday, all stored by our sentinel, none acted on.** When we first saw each one cannot be
   recovered: the store records the airing tick, not the read tick, and #1–#3 aired before the sentinel shipped. True:
   Boletín 2/2, plus two Radio Rastro price items. Each was followed by a dealer `persona.updated` posted by `news`.
   On item #9, Abuela's average bid for uncommons went from 14.3 to 20.0. El Tablón: 0 of 2 checkable items came true
   (#11 can only be checked after the reprint night). The Payday announcement (20:37, +400 P, "Don Ernesto's
   vault and Los Pícaros' epics are within reach") brought us 1 Pícaros rare. We never opened a Don Ernesto thread
   all day; teams opened 33.
5. **The pen test found no hidden endpoint, but the public bundles document the eggs.** 47 keyless GETs at ≤1 req/s.
   The live OpenAPI equals our Friday capture (76 paths). `/api/news` and `/api/taller` work but are not in it.
   The Friday-built bundles hold the egg machinery:
   - the public catalogue shows a **"Secret cards found"** counter ("rumours only — nobody has found one yet");
   - the organiser persona editor defines eggs as secret phrases with rewards `gift_card | grant_pack | badge |
     reveal`, `once_per_team`, `max_total` (15 by default) and `probability`, and says hints are there to "point
     towards easter eggs".

   A hidden card shows in `/api/catalog` only once someone finds it, so a Sunday secret card would stay invisible
   until then. Every egg trigger is a dealer message (a write), so they are all proposals for Marius in §6.

## 1. Scope, interpretation and data windows

**"Iterations"** is undefined in the request. I covered every reading I could find:

| Reading | Count (Friday · Saturday) | Source |
|---|---|---|
| Game ticks | **Fri** ticks 0 → 159 (159 ticks × 60 s = 2.65 h; first tick ≈ 20:20, derived §2.2a) · **Sat** 159 → 1445 (1,286 ticks × 30 s = 10.72 h); `t_hours` 13.367 at the close = 2.65 + 10.72 | `/api/clock` 00:19; `feed_events` |
| Duel sessions | Fri: Practice duels (t120–192, not scored, 306 duels, 12 ticks, decay 0.06) · Sat: Duels I (t459–651, 306 duels, 16 ticks, decay 0.06, ran across the 2 h pause) and Duels II (t1239–1431, 612 duels, 2 rounds, decay 0.08) | `duels.scheduled` / `duels.finished` |
| Market Test sessions | Sat: 6 (start ticks 201, 441, 681, 921, 1161, 1401; 16 ticks each) | `bench.started` |
| Rounds | round 2 "Saturday · Gran Vía" started tick 160 (09:29); round 3 pending | `round.started` id 10940 |
| Clock pauses | Fri: the t94–96 maintenance restart · Sat: 4: late open (unpaused 09:28), 10:31–10:32 (maintenance), **13:24–15:29** (2 h 04 min), **20:15–20:57** (Payday talk); 3.27 h in all | `clock.changed` |
| Level unlocks | Fri: El Chato announced t71, activated t98, open to all t158 · Sat: announced 5 (Pilar, Radio, Workshop, Pícaros, Don Ernesto), activated 5, open to all 3; 72 `level.unlocked` by teams over both days | `feed_events` |
| Dealer prompt versions | 7 `persona.updated`: banco v2 (t254, admin), chato v2/v3 (t463/583, **news**), pilar v2/v3 (t939/1179, schedule = fever), abuela v2/v3 (t979/1219, **news**) | `feed_events` |
| Radio Rastro rounds | 11 news items (ids 1–11), one every ~45–90 min | `/api/news`, `news.posted` |
| Organiser notices | 15 `announcement`, 14 `schedule.fired` (Fri + Sat) | `feed_events` |
| Railway deploy iterations | taker: 51 process starts (ticks 277–1426); ~50 deployments by 13:40 (RAILWAY_ERRORS.md); 20 deployments listed 19:34–00:13 | `decisions.kind = process_started`; `railway deployment list` |
| Our PR iterations | 139 "Merge pull request" commits dated Saturday on `origin/main` | `git log --merges` |

**Data actually seen:**
- Postgres `feed_events` covers ticks 0–1445, ids 15–73292, received Fri 22:21 to Sat 23:43.
- **Friday (ticks 0–159): DB only.** The monitor started at 22:21:43 and backfilled ticks 0–120 with that one
  `received_at`; live capture runs from t121 (22:23:33) to t159. Friday wall times before t121 are derived from ticks
  (§2.2a), not read.
- **Both days:** the keyless `/api/feed` read at 00:24 ends at the same max id, 73292, so the DB is complete at the top. Ids are shared with non-public events: 45k ids are missing
  in 16k gaps, and the biggest gaps sit at duel-session starts. So capture loss cannot be fully ruled out. The badge
  count does cross-check exactly: the leaderboard shows 21 badges and the DB holds 21 `badge.awarded`.
- `messages` / `threads` (ours), `learnings`, `decisions`: Saturday.
- Railway logs (read only, `railway logs --json <deployment>`): taker, maker and duels, 19:52 Sat → 00:18 Sun
  (4,419 lines). The CLI lists only the last 20 deployments per service. For before 19:52 I rely on
  `_night/RAILWAY_ERRORS.md` (10:19–13:40).
- Live keyless reads: 47 GETs, 00:19–00:41 Sun, all logged in §7 (25 in the first pass, 22 JS chunks in the revision).
- I did not use `.local/stream.jsonl` (Friday). Its window is in the DB anyway.

## 2. News, notices and schedule entries (Part 1)

Times are Madrid (`received_at` of the feed event). **"Stored" below means a row exists in `learnings.kind = 'news'`;
it is not a first-seen time.** `learning_of` stores the item's own airing tick (`news.py:230`,
`tick=max(0, item.tick or tick)`), and a restart upserts the row again: 11 of the 13 rows carry `updated_at` 22:50, the
other two 17:27 and 19:58. The sentinel reads `/api/news` once every 10 ticks (`news.py:36`), though `news.posted` in
the feed window the taker already reads can arrive sooner. It first shipped with #182 (merged 11:54), so #1–#3 were
seen at the earliest after that deploy. The Railway logs (≥ 19:52) show the items only as re-said after restarts, and
#11 aired at 19:36, before the window. **First-seen ticks are not verifiable for any item.**

### 2.1 Radio Rastro (`/api/news` = `news.posted`)

| id | Aired tick · time | Source | Headline · body | Came true? (evidence) | Our reaction (stored = a learning exists; first-seen unknown) |
|---|---|---|---|---|---|
| 1 | 283 · 10:30 | Boletín | Radio Rastro is on the air | yes (the level) | stored |
| 2 | 331 · 10:55 | Radio | Atleti win 2-1… car horns on Gran Vía | "just Madrid" | stored |
| 3 | 403 · 11:31 | Radio | El Chato is looking for rare Malasaña cards · pays above usual, one hour | **probably**: `persona.updated chato` by actor `news` at t463 (v2) and t583 (v3, +120 ticks = 1 h). Price effect unmeasured: 0 MAL rares sold to Chato in 463–582 | stored; no action (no "N % over book" wording, `news.py:45`) |
| 4 | 499 · 12:19 | Tablón | El Chato gives a legendary to anyone who says hello! | **no** (no `gift.given` by Chato ever) | stored |
| 5 | 583 · 13:01 | Radio | Metro line 5 closed Ópera–Callao | just Madrid | stored |
| 6 | 643 · 15:35 | Boletín | **Abuela gives out packs for her saint's day** · "Happy saint's day, Carmen." | **yes**: `sobre_barrio` landed 1 h later (t763, RULES_AUDIT news #3). **The saint's day is also an egg clue** (§5, "Castizo") | stored; the pack was opened (`open_sealed_packs`) |
| 7 | 763 · 16:35 | Tablón | Abuela stops buying common cards | **no** (she bought commons 6× after, avg 5.8 P; RULES_AUDIT news #4) | stored |
| 8 | 835 · 17:11 | Radio | Half-hour queue at San Ginés churros | just Madrid | stored |
| 9 | 943 · 18:06 | Radio | **Abuela pays more for uncommon cards until teatime** | **yes**: `persona.updated abuela` by `news` at t979 (v2), back at t1219 (v3). Abuela's uncommon buys: avg **14.3 P** before (n 9) → **20.0 P** in t979–1218 (n 4) | stored; **0 uncommons sold to her in the window** (`dealer_sell_enabled` false) |
| 10 | 1027 · 18:48 | Radio | Sun and 24°; a storm after ten | just Madrid | stored |
| 11 | 1123 · 19:36 | Tablón | All of Lavapiés will be reprinted tonight · sell spare LAV now | unverified (overnight); Tablón is 0/2 so far | stored; no action (correct) |

Queries: `select … from feed_events where type in ('news.posted','persona.updated')`; uncommon buy prices: settlements
with `persona = 'abuela'` and `items[0].to = 'abuela'`, grouped by window (in §8).

**Pattern (inferred from 3 cases, small sample):** a true market item is followed within 36–60 ticks by a
`persona.updated` whose actor is `news`. Fever patches carry actor `schedule`, and Don Ernesto's v2 at t254 carries
`admin`. Rumours had no such event. RULES_AUDIT (news #17) already lists "react to `persona.updated`" as NOT
IMPLEMENTED. The bundle shows the news is a pre-written deck aired by hand (`/api/admin/news/{id}/air` in
`index-B_RfsMCE.js`), so expect more items on Sunday.

### 2.2a Friday 2 Oct: organiser notices and schedule actions

Friday `received_at` is backfill (22:21:43 for ticks 0–120), so the times below are **derived from ticks**. The anchor
is the live capture: t121 starts ≈ 22:21:47 (first events captured 22:23:33, backfill cut at 22:21:43), and later
ticks follow every 60 s (t144 captured 22:44:46, t158 at 22:58:48, close at 23:00). Rule: tick T ≈ 22:21:47 +
(T − 121) min for T ≥ 96. Before t96, add the t94–96 maintenance restart ("about 30 seconds"), so the times are ±2 min.
On these numbers the first tick fell at ≈ 20:20, about 80 min after the nominal 19:00 open. That is derived;
there is no event marking a pre-t0 pause.

| Tick · time (derived) | Event | Text / change | Our reaction |
|---|---|---|---|
| 0 · ≈20:20 | day.opened fri, announcement, clock.changed (unpaused, 60 s) | "The Bazaar is open: Friday until 23:00, one tick every 60 s." | agents started on Friday |
| 3 · ≈20:23 | announcement + schedule.fired `welcome` | "Welcome to the Bazaar! Abuela Carmen is open. More stalls open during the evening." | – |
| 4–156 | gift.given ×21 (Abuela, to 16 teams) | "gift from Abuela Carmen" (she "likes kindness", RULES.md:54) | none to us on Friday (our first at t511 Sat) |
| 71 · ≈21:31 | **level.announced chato** | «Better packs, friendly prices. If I like you.» | – |
| 94 · ≈21:54 | announcement | "Short maintenance in 2 minutes: the server restarts for about 30 seconds. Your cards, cash and offers are kept. If your agent stops on an error, start it again." | – |
| 96 · ≈21:57 | clock.changed (unpaused, 60 s) | back after the restart | – |
| 98 · ≈21:59 | **level.activated chato** | "Better packs and rare singles; he buys uncommon and rare cards. Open a thread with him (with: chato)." `opens_to_all_in_hours` 1.0 | **we unlocked him on the activation tick** (`level.unlocked` t01, "4 deals with abuela"); 18 teams' unlocks fell on Friday |
| 120 · ≈22:21 | **duels.scheduled "Practice duels"** + schedule.fired | "Practice duels (not scored): learn the protocol"; 306 duels, 1 round, 12 ticks, decay 0.06 | duel runner played (RAILWAY_ERRORS row A: 8 restart steps-back in this session) |
| 144 · 22:45 (live) | announcement | "We close at 23:00. Offers stay open; the clock stops." | – |
| 158 · 22:58 (live) | **persona.open_to_all chato** (level 2) | | already unlocked |
| 159 · 23:00 | day.closed fri, announcement, clock paused | "Closed until Saturday 09:00. Offers stay open; the clock stops." (received only at the Sat 09:28 reopen) | – |

No news, egg or badge events on Friday: Radio Rastro and the eggs start Saturday. The Friday clock went
2.65 game hours in 159 ticks. On Saturday the organisers moved round 2, RET and the grant to the reopen (t160–165)
instead of jumping the clock to the planned h4.

### 2.2 Saturday 3 Oct: organiser notices and schedule actions

| Tick · time | Event | Text / change | Our reaction |
|---|---|---|---|
| 159 · 09:00 | day.opened, announcement | Saturday until 23:00, 30 s ticks | – |
| 159 · 09:28 | clock.changed | **unpaused 09:28**: the open was 28 min late | the taker's schedule lead times re-read |
| 160 · 09:29 | set.released RET, round.ended 1, round.started 2 | "Saturday · Gran Vía", weight 1.0, reset false | – |
| 165 · 09:31 | schedule.fired grant_all | "El Retiro has arrived: a pack and the Saturday allowance (150 primas)" | – |
| 192 · 09:45 | duels.finished "Practice duels" | the Friday practice session ended on Saturday's 34th tick | – |
| 201 · 09:49 | bench 1 | Market Test (16 ticks, 18 venues) | stall-level 0.5 (MM_DEEP) |
| 252 / 262 · 10:15 / 10:20 | level pilar announced / activated | "I collect what others throw away" | taker playbook |
| 254 · 10:16 | **persona.updated banco v2 (admin)** | Don Ernesto edited ~8 h before he was even announced (18:00) | – |
| 272 / 282 · 10:25 / 10:30 | level radio announced / activated | news via `/api/news` | sentinel on since #182 (11:54) |
| 282 · 10:30 | announcement | maintenance restart in 1 min (back 10:32) | – |
| 441 · 11:50 | bench 2 | | |
| 459 · 11:59 | duels.scheduled Duels I | 306 duels, 16 ticks, decay 0.06 | duels runner |
| 502 · 12:20 | pilar open to all | | |
| 630 · 13:24 → 15:29 | clock paused **2 h 04 min** | the Workshop and Pícaros announced during the pause (14:11, 14:16) | – |
| 651 · 15:39 | duels.finished "Duels I" | **Duels I ran across the 2 h pause** (scheduled t459, finished t651, 192 ticks for 16-tick duels, 3 at a time). A Sunday pause would hold Duels III open the same way | – |
| 681 · 15:54 | bench 3 | | |
| 706 · 16:07 | level taller activated | `POST /api/taller` (not in the OpenAPI) | not used (RULES_AUDIT #10) |
| 761 · 16:35 | level picaros activated | "flag a trick (POST /api/flags)" | flags off |
| 850 · 17:19 | announcement | "Salamanca fever: Pilar pays 25 % over book for Salamanca starting in 45 min" | stored (row tick 865, a schedule read); the sell desk is off (RULES_AUDIT #12) |
| 881 · 17:34 | picaros open to all | | |
| 921 · 17:54 | bench 4 | | |
| 932 / 971 · 18:00 / 18:20 | level banco announced / activated | "Gold is not shown. It is negotiated." | **0 banco threads by us all day** |
| 939 · 18:03 | schedule.fired persona_patch | fever on. The note still says "until 17:30" (stale wall time; it ran 18:04–20:04) | SAL-07 incident at t947 (KNOWLEDGE_SAT_EVENING §1.1) |
| 1091 · 19:20 | banco open to all | | |
| 1161 · 19:55 | bench 5 | | |
| 1179 · 20:04 | schedule.fired | "The fever breaks" | stored (row tick 1167, from the schedule before it fired) |
| 1194 · 20:11 | announcement | "the game pauses in 2 minutes … come to the front" | – |
| 1201 · 20:16 | announcement | "⏸ … Announcement at the front: **Payday, tips, and a congratulation**" | **content of the talk unknown to me (open question 1)** |
| 1201 · 20:37 | announcement | **"Payday in Madrid: every team gets 400 primas, a second starting purse. Don Ernesto's vault and Los Pícaros' epics are within reach. Only deals score, never cash you hold."** | stored as a learning; after it, t01 settled 1 Pícaros rare and sold 1 rare to a team. market-wide, epic settlements with the Pícaros went 7 before → 11 after, and epic or legendary settlements with Don Ernesto 2 → 1 (direction not split) |
| 1201 · 20:57 | announcement, clock unpaused | "Duels II starts in about 20 minutes" | |
| 1239 · 21:16 | duels.scheduled Duels II | 612 duels, 16 ticks, decay 0.08, 2 rounds | |
| 1401 · 22:37 | bench 6 | | |
| 1415 · 22:45 | announcement | "We close at 23:00. Offers stay open; the clock stops." | – |
| 1431 · 22:52 | duels.finished Duels II | | |
| 1445 · 23:00 | day.closed, clock.changed (paused, 23:00:29) | "Closed until Sunday 09:00" | |
| 1445 · **23:05** | **clock.changed (admin, paused)**, id 73277 | a second admin clock event 5 min after the close. The same pairs appear around restarts (08:32/08:33, 20:15 ×2, 20:57 ×2). It fits the post-close server restart (`/api/health` `uptime_s` puts the start at ~23:06) | – |

The pause shifted every game-hour event. Before 20:15 the taker logged "Sunday opens at game hour 14.078"; after
20:57 it logged 13.368 (taker logs, `schedule:` lines, 17:52Z and 18:58Z). Our sentinel stored every item (`learnings.kind = 'news'`: 13 rows with airing ticks 283–1167, 11 news and 2
schedule notes). When it first saw each one is not recorded (§2.1). The schedule lines were re-said after each
taker restart in the log window.
The level watch's learnings carry `created_tick` 1428, because a restart upserts the same row. That says nothing about
when it first saw a level.

### 2.3 Sunday hints (what the server says at 00:19–00:26 Sun)

- **The 6th barrio is Chamberí (CHA):** `/api/catalog` lists it with `released: false`, `release: "sun+0h"` and 12
  cards. Its theme is "Sunday expansion: ghost stations and hidden gardens"; CHA-11 is "Andén 0" and CHA-12 is
  "El Tren Fantasma". RET was `sat+0h` and came out at Saturday's first tick (t160).
- **No 6th dealer:** `/api/dealers` lists 5 and `/api/levels` 6, all active and open to all. There is no announced but
  inactive level.
- **The speed run:** Sunday runs at 15 s ticks (`/api/clock` → `days[2].tick_seconds = 15`). The kickoff said "on
  Sunday we are going for a speed run" (`docs/transcripts/2026-10-02-hackathon-kickoff.md` [11:02]).
- **Schedule (`/api/schedule`, unchanged between 00:19 and 00:26):**

| at_hours | Action | Note | If 09:00 = h16.65 (JUMP / re-planned) | If the clock resumes at h13.367 |
|---|---|---|---|---|
| 14.65 | bench (12 traders) | **The hard Market Test** | in the skipped window: fires at 09:00 or is dropped (unknown) | 10:17 |
| 15.0 | bench | Market Test | same as above | 10:38 |
| 16.65 | set_release CHA, round 3 "Sunday · Chamberí" (weight 1), day_opens sun (wall 09:00) | | **09:00** | 12:17 |
| 16.7 | grant_all 150 P | | 09:03 | 12:20 |
| 17.0 | bench | | 09:21 | 12:38 |
| 18.65 | duels | **Duels III**: 2 rounds, 12 ticks, decay 0.10, 4 concurrent, price + days | **11:00** | 14:17 |
| 19.0 | bench | | 11:21 | 14:38 |
| 21.0 | bench | | 13:21 | never (doors close h19.367) |
| 21.45 | announce | finale warning | 13:48 | never |
| 21.65 | 5 × persona enabled=false + duels "Final duels" (1 round, 12 ticks, decay 0.10) | **stalls close, the Grand Final on the big screen** | **14:00** | never |
| 22.55 / 22.65 | freeze warning / end_round + day_closes sun (wall 15:00) | **Scores freeze** | 14:54 / **15:00** | never |

  **Where 16.65 comes from (derived exactly):** `t_hours` is the sum of ticks × tick length. Friday 159 × 60 s =
  2.65 h, plus Saturday 1,286 × 30 s = 10.72 h, gives 13.37, the clock now. **2.65 + Saturday's nominal 14.0 h
  (09:00–23:00) = 16.65.** Saturday lost 3.27 h to pauses: the late open 28.6 min, 10:31–10:32 0.9 min, 13:24–15:29
  124.3 min, 20:15–20:57 42.4 min, 196 min in all. That is the 16.65 − 13.37 = 3.28 h gap. So the server now places
  Sunday as if Saturday had ticked without pauses.
  Two mechanisms fit:
  - a deliberate clock jump at the open;
  - the post-close deploy changed how the wall-pinned `day_*` entries get their `at_hours`.

  The history of those entries: 14.083 at 16:52 (RULES_AUDIT news #4), 14.078 at 19:52 and 13.368 at 20:58 (taker
  `schedule:` lines), then 16.65 at 00:19. So the change happened between 20:58 and 00:19, most likely at the ~23:06
  restart (`uptime_s`, and the 23:05 admin `clock.changed`). Under the 16.65 reading the close lands exactly on the
  score freeze (16.65 → 22.65 = 6 h = 09:00 → 15:00). In the second column the Grand Final and the freeze never happen,
  which no organiser would plan. Both columns stay possible until 08:55.

  **Plan by offsets from the Sunday open** (they hold under a jump or a recompute; at 15 s ticks game hours still run
  1:1 with wall time):

  | Offset from `day_opens sun` | Event | Wall time if the open is 09:00 |
  |---|---|---|
  | +0 | round 3 "Sunday · Chamberí" + CHA release | 09:00 |
  | +0.05 h | grant 150 P | 09:03 |
  | +0.35 h / +2.35 h / +4.35 h | Market Tests | 09:21 / 11:21 / 13:21 |
  | +2.0 h | Duels III | 11:00 |
  | +4.8 h | finale warning | 13:48 |
  | +5.0 h | stalls close + Grand Final | 14:00 |
  | +5.9 h / +6.0 h | freeze warning / scores freeze | 14:54 / 15:00 |
  | −2.0 h / −1.65 h (before the open) | the hard Market Test (14.65) and the 15.0 bench | fire at the open, or are dropped |

  The offsets break in one case only: events fire by `at_hours` while the clock resumes at 13.367 (column 2). That is
  what the 08:55 check (`uv run bazaar clock` → `t_hours`, plus a keyless `GET /api/schedule`) must rule out.
  **Consequence:** KNOWLEDGE_SAT_EVENING §1.6 (round 3 ~11:34, Duels III ~13:34, the finale after the close) and
  RULES_AUDIT TL;DR 8 ("Sun 09:00–11:34 still counts for Saturday's round") are probably stale; confirm at 08:55.
- **The server was restarted after the close:** `/api/health` → `uptime_s` 4408 at 00:20, so it started at about
  23:06 Sat. The 23:05 admin `clock.changed` (id 73277) points to the same moment. The schedule change most likely
  comes from it.
- **News deck:** `/api/news` is still at 11 items. The admin bundle airs pre-written items, so expect more on Sunday.
- **A Chamberí lead in the dealer replies (inferred):** at t1370 (22:22) the Pícaros answered t10: "¡**El tren de
  Chamberí**, dice! Nando, llora, que nos ha visto el alma. Y a nuestra madre recuerdos, que ella cortaba mejor la
  baraja." That is a recognised-lore reply in the style of E3, but no `egg.found` followed. CHA-12 is "El Tren
  Fantasma" and CHA-11 is "Andén 0" (Chamberí's real ghost station). At least one team is already probing Chamberí
  lore before the set is out. An egg tied to CHA may switch on only with the release. Other Chamberí or "andén" mentions
  (t894, t1101, t1253, t1255, t1303 to us) are the Pícaros' stock patter ("no hay otra, ni en Chamberí"), not a reply to
  lore.

### 2.4 What we reacted to late or never

| # | Signal | When | Reaction | Evidence |
|---|---|---|---|---|
| L0 | **The game announced eggs and a hidden card before Saturday opened.** RULES.md:49 ("The hidden card is prestige only: no dealer buys it") and RULES.md:122 ("… gifts, easter eggs, and organiser grants") have been in our repo since `07af557b` (Fri 2 Oct 20:13, the kit vendored). The public catalogue page has shown "Secret cards found: 0 — rumours only, nobody has found one yet" since the Friday build (`Catalogue-B7zHP7rb.js`; bundle `Last-Modified` Fri 18:29 Madrid) | Fri 18:29–20:13 | **never** followed up; no one listed "find the hidden card / eggs" as a task | git log; §4 |
| L1 | **Pilar: "ask Carmen at El Rastro about the golden chulapa"**, to us | 5× at 15:38, 16:12, 16:14, 16:14, 16:21 (ticks 649–734) | **never** (our 255 sent messages are price templates; 0 contain any lore word) | `messages` ⋈ `threads.ours` (§8 Q4) |
| L2 | Abuela gave us 3 gifts ("por ser amable") | t511, t954, t1201 | not a signal; they show kindness works with her (RULES.md:54) | `gift.given` |
| L3 | News #9 (Abuela +40 % on uncommons, true) | 18:06–~21:00 | 0 sales (desk off by decision after the 12:24 sell loop) | §2.1 |
| L4 | Payday: "Don Ernesto's vault … within reach" | 20:37 | **0 Don Ernesto threads all day**, though we unlocked him at his activation (t971, "3 deals with pilar"). Other teams: 33 threads, 11 teams. 1 Pícaros rare bought after payday | `threads` (ours: abuela 25, picaros 37, pilar 9, chato 4, banco 0); `thread.opened` |
| L5 | The 20:16 talk "Payday, **tips**, and a congratulation" | 20:16–20:57 | unknown. The first "Trickster tricked" came at 21:10 (13 min after play resumed) and 6 teams had it by 22:05; "Castizo" and the gift eggs all came at 22:04–22:34. **Two explanations fit:** (a) the talk gave tips; (b) the eggs were switched on server-side during the pause. The pause has paired admin `clock.changed` events at 20:15 (ids 57475, 57480) and 20:57 (57547, 57551), the pattern seen around restarts (08:32/08:33, 23:00/23:05). The editor bundle lets an egg be `enabled` per persona, and that goes "live on save". Counter-evidence for "always on": t13 said "cocido" to Abuela at t133 (Friday) and "Cascorro" to Chato at t869 with no egg | `egg.found` ticks; `clock.changed`; open question 1 |
| L6 | The schedule moved by the 20:15 pause, and again after the close | 20:57; ~23:06 | the taker re-said the new hours (logs). Our human plans did not (KNOWLEDGE_SAT_EVENING §1.6 predates both) | taker `schedule:` lines; §2.3 |
| L7 | Don Ernesto `persona.updated` v2 at 10:16, ~8 h before his announcement | t254 | none needed (inferred: that edit is where the egg went in) | `feed_events` |

### 2.5 Deploy iterations that hit a scoring window (Saturday)

Every merge to `main` redeploys every service, so taker `process_started` rows serve as a proxy for deploys. Windows
are the bench window (start − 10 ticks to start + 15) and the duel session (scheduled to finished):

| Window | Ticks | Taker starts inside | Ticks of the starts |
|---|---|---|---|
| Market Test 1 | 191–216 | 0 | – |
| Market Test 2 | 431–456 | **4** | 434, 439, 445, 451 |
| Duels I | 449–651 | **14** | 451, 459, 466, 473, 480, 506, 547, 560, 575, 591, 602, 615, 626, 630 |
| Market Test 3 / 4 | 671–696 / 911–936 | 0 / 0 | – |
| Market Test 5 | 1151–1176 | **2** | 1155, 1166 |
| Duels II | 1229–1431 | **6** | 1234, 1288, 1347, 1362, 1422, 1426 |
| Market Test 6 | 1391–1416 | 0 | – |

Query: `decisions where agent = 'taker' and kind = 'process_started'` joined to the windows; 51 starts in all, ticks
277–1426. The maker writes no `process_started` row, so its own restarts are inferred from the shared deploy. Sunday
has 4–6 Market Tests and 2 duel waves in 6 hours, at 15 s ticks. Each one is a window with no merges.

## 3. Fair play: the boundary for Part 2 (quoted from `vendor/bazaar-kit/RULES.md:128-150`)

> ## Fair play
>
> - One team, one key.
>   Do not share keys, run several teams, or feed another team on purpose.
>   It also scores nothing: when one team keeps handing another the whole value of their deals, those deals count for nothing until the organisers have looked.
> - Prompt injection against dealers is allowed and fun; it changes what they say, never their prices, and some of them will stop talking to you.
> - Rate limit: 5 requests per second per key (bursts of 20); reads without a key, 60 per second per address.
>
> ### Limits every request meets
>
> Agents from every team, and whoever else finds the address, talk to one server for a whole weekend.
> These keep it up:
>
> | What | Limit | Beyond it |
> |---|---|---|
> | Request body | 64 KB of strict JSON: finite numbers below 10^12, at most 8 levels deep (no `NaN`, no `Infinity`) | `413` · `400` |
> | Prices and cash | whole primas from 1 to 10,000,000; at most 50 items on one side of an offer | `400` |
> | Text | control, invisible and direction-changing characters are removed; a message keeps 1,200 characters, a venue name 40 | cleaned, not refused |
> | Thread topic between teams | a small object: at most 600 characters of JSON, 4 levels | `400` |
> | Wrong keys or tokens | 20 in a burst per address, then one every two seconds (a valid key is never slowed) | `429 too_many_failures` |
> | Live stream (`/api/events/stream`) | 6 open streams per team key (without a key: per address), 400 in all | `429` · `503`: poll `/api/feed` |
>
> A refused request is a `4xx` with `{"error": "<code>", "message": "<why>"}`; it costs nothing and moves nothing.

Also RULES.md:122: "What never counts: the number of trades, fees you earned, what you pulled from a pack (shown as
*luck*), gifts, **easter eggs**, and organiser grants."

How I stayed inside: GET only, no key header at all (so I never touched our 5 req/s budget), ≥ 1.1 s between requests
(limit 60/s), 47 requests in all (22 of them static JS chunks in the revision), no `/api/admin/*` (no token, so no wrong-token count), no stream, no fuzzing.
I listed paths only from the OpenAPI, the kit, the docs and the bundles, and I triggered nothing.

## 4. Pen test: the surface (Part 2)

| Source | What it gave | Odd or new |
|---|---|---|
| `/openapi.json` (live, 49.6 KB, sha `90e4b40f48cf`) | 76 paths, **the same paths, methods and schemas** as our Friday capture `docs/api/openapi.server.json` (only formatting differs) | `/api/news` and `/api/taller` are live but **absent** from the OpenAPI. 41 `/api/admin/*` paths (organiser token): listed, **not touched** |
| Kit (`/bazaar-kit.zip`) | sha256 `e899ea974d21…` = `vendor/bazaar-kit/VENDORED.md`. **No new kit** | – |
| `/robots.txt`, `/sitemap.xml`, `/humans.txt`, `/.well-known/security.txt` | all return the SPA `index.html` (same sha `d3e0fb9b0dd3`) | nothing hidden there |
| `/` + response headers | `Server: uvicorn`, `Via: 1.1 Caddy`, CSP `frame-ancestors 'none'`, `X-Frame-Options: DENY`, index `Last-Modified: Fri, 02 Oct 16:29:47 GMT` | the frontend has not been rebuilt since Friday |
| `/assets/index-B_RfsMCE.js` (390 KB) | every public route matches the OpenAPI. Admin routes include `/api/admin/news` and **`/api/admin/news/{id}/air`** (a news deck aired by hand) | no egg strings in the entry bundle itself; they are in the lazy chunks below |
| **`/assets/Catalogue-B7zHP7rb.js`** (the public catalogue page) | a KPI **"Secret cards found"**, `secrets = sets.flatMap(cards).filter(hidden).length`, hint **"rumours only — nobody has found one yet"** / "someone found a hidden one"; a card badge `secret` vs `shiny` vs `page card` | **an egg lead shown publicly since Friday.** Hidden cards are counted from `/api/catalog`, which listed none on Saturday (`.ai/memory.md:976`: "12 cards each, none `hidden`") and LAT-13 at 00:20. So **a hidden card appears in the catalog only after someone finds it**: a CHA or other secret card stays invisible until then |
| **`/assets/PersonaEditor-CY0YsfDU.js`** (the organiser persona editor; a public static file, its admin API not touched) | **the egg mechanism:** "An egg fires when the team's message contains one of its phrases (accents and case ignored). The persona reacts in the reply's spirit and the action runs once — the words never move anything else." Fields: `trigger {always, keywords, probability}`, `action.type ∈ none \| gift_card \| grant_pack \| badge \| reveal` ("gift_card … hidden cards only ever arrive this way"; "reveal: the reply is the secret"), `once_per_team` (default true), `max_total` (default 15, "found r/max"), `enabled`. Hints: "Plant rumours about other stalls, point towards easter eggs, announce what comes next" | the trigger is a **phrase inside the team's message**: it can ride in the text of an ordinary priced message. An egg can also fire by `probability` or `always`. Finds are capped per egg |
| `CardsAdmin-BXwbvoPk.js`, `PersonaList-D1blR-q3.js`, `Insights-CZNFWMow.js`, `LiveFeed-0LVmhUn1.js`, `Events-BtvMjJi8.js`, `Conversation-qXKH7jXc.js` | "hidden — only easter eggs and grants mint it"; per persona "N hints and N eggs", "eggs (found/total)"; Insights has an "Eggs" column per persona; the live feed prints "<team> found an easter egg at <persona>'s stall 🥚" | the organisers track finds per egg; we cannot read those numbers (admin API) |
| `ControlRoom-BPiPQhN5.js` | renders `t.llm.pending_voices` as "N voices pending · cap <max_calls>" | **`pending_voices` = dealer LLM replies queued** (confirmed by the bundle) |
| `Cromo-BD7ZopIA.js` | card art: `secret: e.hidden`; the word lists include `chulapa` | art only |
| `/assets/EventLine-Bsrbkjwf.js`, `BigScreen-CIAu_gkJ.js`, `Teams-TPp3tDwD.js` | they render `egg.found` as "<team> found an easter egg", `egg.given` as "…for finding an easter egg", and **team badges** (`e.badges`) next to each team | eggs were planned from day one (Friday build) |
| `/api/health` | `{"ok", "tick", "last_tick_age_s", "loop_age_s", "paused", "doors", "uptime_s", "pending_voices": 6}` | **`pending_voices`** is undocumented: the dealer LLM replies still queued (ControlRoom bundle); 6 at the close |
| `/api/catalog` | LAT has 13 cards: **LAT-13 "La Chulapa Dorada", legendary, `hidden: true`, print_run 1, minted 1**, flavour "Only one was ever printed. Don Ernesto knows where." The other sets have 12; CHA is unreleased (`sun+0h`) | the only hidden card **visible** now. Others may exist unfound (row above). RULES.md:49: "The hidden card is prestige only: no dealer buys it". Our `cards` table stores it (`hidden` true, upserted at t1436), but `cards_heartbeat.py:84` marks hidden cards not visible, so a newly found one is **not** reported as `new_card` |
| `/api/leaderboard` | each team has `badges`, `luck`, `rarest`, `adjustments` (all `[]`) and `frozen` | badges are public |
| `/api/dealers` | 5 dealers, with traits, unlock rules and menus. Don Ernesto's title: "treasury desk at **Casa Prima** on Calle de Alcalá" | "Casa Prima" is the place Abuela's clue names |
| `/api/clock`, `/api/schedule`, `/api/levels`, `/api/news`, `/api/venues`, `/api/feed?limit=50` | §2.3; the feed's newest id 73292 equals the DB's | – |
| `GET /api/dealers/{pid}` and an error body | not read by me. The reviewer read `/api/dealers/banco`, `/pilar` and `/nobody` (404 `{"error":"not_found","message":"no dealer 'nobody'; see /api/dealers"}`): `bio`, `unlock`, `menu`, nothing egg-like | from `review-logs-eggs.md` §4 |
| Dealer texts in the public feed (12,012 `thread.message`) | team texts are `null` and dealer replies are public, so **the replies reveal the eggs** (§5) | the main source |

## 5. The eggs found on Saturday (from the public feed; triggers inferred from the dealer's reply)

Team texts are `null` in the feed, so every trigger below is **inferred** from the dealer's reply on the egg's tick
(query `eggs.sql` §1). The mechanism itself is no longer inferred: the organiser editor bundle (§4) says an egg fires
when the team's message **contains one of its secret phrases, accents and case ignored**. It runs once per team by
default and up to `max_total` finds (default 15). Several triggering team messages carry no structured offer (t09 at
t554, t10 at t1039, t05 at t1046 show empty `give`/`want`), which fits "words only".

| # | Dealer | Reward | Teams (first tick · time) | Inferred trigger (the words the dealer echoes) | Where the clue was |
|---|---|---|---|---|---|
| E1 | Abuela | badge **Sharp ear** | 11: t04 (407 · 11:33), t02, t09, t10, t05, t16, t13, t18, t03, t08, t06 (1337 · 22:05) | ask her about **"la chulapa dorada"** (the golden chulapa). She answers "shh… only one was ever printed… ask Don Ernesto at Casa Prima about **the Moscow gold**" | Pilar told 15 teams, 66 times from t320 ("ask Carmen about the golden chulapa"); 4 teams got the badge before Pilar's hint reached them, so there is another route too |
| E2 | Don Ernesto | **LAT-13 La Chulapa Dorada** (hidden legendary, 1 copy) | **1: t02 (1021 · 18:45)** | say **"el oro de Moscú"**: "So you know the story — very few do. For that, the chulapa is yours" | Abuela's E1 reply. **Gone:** t10, t05, t13, t04 and t08 asked later and were told "the chulapa stays in the vault" (t1042–1176) |
| E3 | Los Pícaros | badge **Trickster tricked** | 6: t18 (1227 · 21:10), t05, t10, t08, t02, t06 (1336 · 22:05) | name the old con stories: **Lazarillo, Rinconete (y Cortadillo), "el timo de la estampita"**. Reply: "you know the old trick… so no tricks for you… today" | no clue in the feed; first seen 13 min after the 20:16 talk ("tips") |
| E4 | Abuela | badge **Castizo** | 4: t08 (1335 · 22:04), t02, t10, t05 (1369 · 22:22) | Madrid lore: **"sile, nole, repe, me falta"** (the old card-swap chant), **the chotis danced on one tile ("una baldosa")**, **her saint's day** (Virgen del Carmen, 16 July), the verbena de la Paloma | news #6 "Happy saint's day, Carmen" (15:35). t02 thanked her for the saint's day at t803 without a badge, so the saint's day alone is not enough |
| E5 | Abuela | one of her duplicate cards (MAL-06 ×2, LAV-08) | 3: t10 (1364 · 22:19), t05, t08 (1394 · 22:34) | **"cocido con sus tres vuelcos"** (her mother's cocido). "Rosquillas tontas y listas" alone gave no egg (t08 t1350, t10 t1362), nor did "cocido" on Friday (t13 t133). t06's "cocido con los tres vuelcos" at t1407 gave no gift either: possibly the `max_total` cap (3 finds) or other wording. Reply: "take this one, for remembering her" | – |
| E6 | El Chato | a `sobre_barrio` pack | 2: t10 (1363 · 22:19), t08 (1394 · 22:34) | **"Plaza Mayor, con caña"** (calamari sandwich and a beer). **Not** Cascorro alone (t13 t869, t02 t1360, t10 t1361: no egg), and not "Plaza Mayor" alone (t05 t1371: "Plaza Mayor, sí. Tourists pay double there", no egg). Reply: "you know Madrid. Here, for your trouble" | – |

What else the data shows:
- **One per team per egg.** No team holds the same badge twice (the editor default `once_per_team: true`). t10 and
  t08 hold 5 eggs each, with E1, E3, E4, E5 and E6 all different. **Finds are capped** (`max_total`, default 15): E1 is
  at 11. If its cap is 15, 4 finds are left; the real caps are not public.
- **The eggs came in waves.** Six "Sharp ear" badges came within 37 ticks (1040–1077, 18:54–19:13), right after t02's
  LAT-13 egg showed on the big screen at 18:45 (inferred: copycats). Every E3–E6 egg came after the 20:16 pause:
  either tips from the talk or a server-side switch-on during the pause (§2.4 L5).
- **The Pícaros "no tricks" offers were not bargains.** After E3, the Pícaros offered to buy commons at 4 P. Their
  usual bid is 4–5 P (median 5, n 15 Pícaros common buys), so the only effect is that their offers stop switching the
  card "today". Our taker walked from 4 provable Pícaros switches (CARD_SCOUT §5, RULES_AUDIT #9).
- **Points: 0.** RULES.md:122 lists easter eggs and gifts as never counting, and RULES.md:49 says the hidden card is
  "prestige only". The badge correlates with rank: 5 of the top 6 teams wear one (not t12). But the top teams are
  simply the most active, so this is not causal.
- **Doña Pilar has no egg found yet;** she is the clue carrier. Don Ernesto's only known egg (E2) is spent.
- **Sunday lead (inferred):** the Pícaros' "¡El tren de Chamberí, dice!" reply to t10 at t1370 (§2.3). Chamberí's
  theme ("ghost stations and hidden gardens", "Andén 0", "El Tren Fantasma") reads like a home for a secret card, and
  a secret card only shows in the catalog after its first find.

## 6. Recommendations for Sunday (ranked by expected points)

| # | What | Concrete change | Expected points | Risk |
|---|---|---|---|---|
| 1 | **Re-plan Sunday on the current schedule:** round 3 at 09:00 (not 11:34), Duels III ~11:00, benches ~09:21 / 11:21 / 13:21, stalls close + Grand Final ~14:00, freeze 15:00 | No code: the playbook and schedule watch read `/api/schedule` live. Humans: at **08:55** run `uv run bazaar clock` and a keyless `GET /api/schedule`, then plan by the **offsets from `day_opens sun`** in §2.3. **No deploy 08:45–09:40**: it covers the 09:21 bench and the two overdue benches (14.65 hard, 15.0) if they fire at the open | protects the whole Sunday timing. Round 3 is "Sunday · Chamberí", weight 1, so a Sunday session is worth ~7.5 × its bench points (KNOWLEDGE_SAT_EVENING §2.2) and a missed Duels III or Grand Final wave costs its whole duel share | none (reading only). If the clock resumes at 13.367 instead, Duels III moves to 14:17 and the Grand Final falls past the close |
| 2 | **Pitch (judges 40 %):** tell it honestly: "the dealers hid a story in their replies. Pilar gave our agent the clue 5 times; it only listened for prices." Then show either (a) the read-only `eggs.sql` decoding of all 6 eggs from public replies, or (b) a badge earned live (proposal 3) | `docs/research/2026-10-04/logs-eggs/eggs.sql` (read only) | judges' share only; unknown size | (a) none; (b) see 3 |
| 3 | **Proposal for Marius, not done:** earn the badges by hand, in a quiet minute (no bench or duel wave). Each egg is **one dealer message (a write)**. Ranked by risk: **E3 Pícaros** (traits: strictness 0.1, memory 0.3; side benefit: no card switching "today"), **E1 + E4 + E5 Abuela** (strictness 0.1, patience 0.85: lowest risk, up to 2 badges and a duplicate card), **E6 Chato** (strictness 0.85, memory 0.9: only inside a genuine priced move, never text alone, to avoid the spam/cooloff rule of RULES.md:50-52). **Skip E2** (LAT-13 is gone, and Don Ernesto's strictness is 1.0) | Cheapest: the editor bundle says the phrase only has to be **inside the team's message**, so it can ride in the text of a move the taker sends anyway. That costs 0 extra requests, but it is a code change, so a redeploy outside every §2.5 window. By hand instead: `b.open_thread("<dealer>", topic={...})`, then `b.say(thread_id, "<phrase>")` with **no `price`** (a text-only message binds nothing), then `b.close_thread(thread_id)`. That is ~3 POSTs per dealer, ≥1 tick apart, ~9–12 in all. First check `uv run bazaar threads`: one open thread per dealer, so do it when the taker holds none with that dealer, or the open is refused. Finds are capped (`max_total`), so earlier is better | **0 by rule** (RULES.md:122). Badges show on the public board and big screen. The Pícaros honesty may save one walked deal | (i) cooloff with Chato or Don Ernesto if the words read as spam; (ii) a thread slot taken from the taker for 2–3 ticks; (iii) ~10 requests at 15 s ticks, where the Sunday budget is tight (MORNING §1: 4.73 req/s at the ceiling), so do it inside a quiet tick; (iv) a gifted card or pack is "luck/gift" and must not be sold below value by the guard (`max_score_loss_per_move` still applies) |
| 4 | **Read-only egg and news watch every 30 min on Sunday** (a human or a monitor laptop) | Run `eggs.sql` §1–§4 through `q.py`: (1) every egg with the dealer reply; (2) clues dealers sent us; (3) **any `egg.found` with persona `picaros` or `pilar` after 09:00, and dealer replies matching `chamber\|andén\|fantasma`** (the §2.3 lead); (4) **the count of `hidden` cards in `cards`** (the monitor upserts the catalog; it equals the public "Secret cards found" KPI). For news: `select … from feed_events where type in ('news.posted','persona.updated') and tick > <last>`. Gap in our code: `cards_heartbeat.py:84` treats hidden cards as invisible, so a newly found secret card raises no `new_card` alert. Use query (4) until that changes | 0 directly; turns a new Sunday egg, secret card or true news item into a human decision within minutes instead of never | none (DB SELECTs, zero game requests) |
| 5 | **News signals: leave them as they are on Sunday** (logging on, no behaviour) | none. The only true market items were dealer bid changes, and acting needs the sell desk (`dealer_sell_enabled` false) | ~0 (the 4 P/uncommon uplift × a few duplicates) | turning the sell desk on is the SAL-07 risk |

## 7. Request log (every live request I made)

All were keyless GETs to `https://bazaar.causaprima.ai`, sent with `User-Agent: t01-readonly-probe` and ≥ 1.1 s apart.
The raw log is `docs/research/2026-10-04/logs-eggs/requests.log` (UTC). Bodies were kept outside git.

| UTC (Sun 4 Oct = Sat 3 Oct Z) | Method | Path | Status | Bytes |
|---|---|---|---|---|
| 22:19:52 | GET | /openapi.json | 200 | 49629 |
| 22:19:53 | GET | /api/health | 200 | 131 |
| 22:19:55 | GET | /api/clock | 200 | 910 |
| 22:19:56 | GET | /api/schedule | 200 | 2674 |
| 22:19:57 | GET | /api/levels | 200 | 2128 |
| 22:19:58 | GET | /api/news | 200 | 2108 |
| 22:19:59 | GET | /api/dealers | 200 | 4891 |
| 22:20:00 | GET | /api/catalog | 200 | 14440 |
| 22:20:02 | GET | /api/venues | 200 | 11087 |
| 22:20:03 | GET | /api/leaderboard | 200 | 17630 |
| 22:20:04 | GET | /robots.txt | 200 | 1079 (SPA) |
| 22:20:05 | GET | /sitemap.xml | 200 | 1079 (SPA) |
| 22:20:06 | GET | / | 200 | 1079 |
| 22:20:07 | GET | /bazaar-kit.zip | 200 | 17091 |
| 22:20:08 | GET | /.well-known/security.txt | 200 | 1079 (SPA) |
| 22:20:10 | GET | /humans.txt | 200 | 1079 (SPA) |
| 22:21:22 | GET | /assets/index-B_RfsMCE.js | 200 | 390197 |
| 22:21:23 | GET | /assets/useEvents-BpJ5PfZT.js | 200 | 15900 |
| 22:21:24 | GET | /favicon.svg | 200 | 549 |
| 22:21:45 | GET | /assets/BigScreen-CIAu_gkJ.js | 200 | 44898 |
| 22:21:46 | GET | /assets/EventLine-Bsrbkjwf.js | 200 | 14209 |
| 22:21:48 | GET | /assets/Teams-TPp3tDwD.js | 200 | 33030 |
| 22:24:03 | GET | /api/feed?limit=50 | 200 | 18190 |
| 22:26:00 | GET | /api/schedule | 200 | 2674 (unchanged) |
| 22:26:01 | GET | /api/clock | 200 | 910 |
| 22:40:42 | GET | /assets/Catalogue-B7zHP7rb.js | 200 | 19001 |
| 22:40:43 | GET | /assets/Cromo-BD7ZopIA.js | 200 | 90276 |
| 22:40:44 | GET | /assets/catalog-C4CNN-Mb.js | 200 | 885 |
| 22:40:45 | GET | /assets/LiveFeed-0LVmhUn1.js | 200 | 11415 |
| 22:40:47 | GET | /assets/Events-BtvMjJi8.js | 200 | 9663 |
| 22:40:48 | GET | /assets/Venues-By_ioigg.js | 200 | 12026 |
| 22:40:49 | GET | /assets/Duels--ctbgHOp.js | 200 | 23197 |
| 22:40:50 | GET | /assets/Threads-Bm_ipLV8.js | 200 | 10955 |
| 22:40:51 | GET | /assets/Conversation-qXKH7jXc.js | 200 | 8497 |
| 22:40:52 | GET | /assets/Insights-CZNFWMow.js | 200 | 33218 |
| 22:40:54 | GET | /assets/PersonaGallery-DvtlR2Yh.js | 200 | 16834 |
| 22:40:55 | GET | /assets/Bench-CQD-tuQh.js | 200 | 17118 |
| 22:40:56 | GET | /assets/Styleguide-0WmY8aCf.js | 200 | 24402 |
| 22:40:57 | GET | /assets/OfferView-C4dF4qTy.js | 200 | 3600 |
| 22:40:58 | GET | /assets/PackCard-uJX1JC15.js | 200 | 4272 |
| 22:40:59 | GET | /assets/names-CpRLV58L.js | 200 | 1012 |
| 22:41:01 | GET | /assets/KPI-BYUa0IMS.js | 200 | 1887 |
| 22:41:02 | GET | /assets/ControlRoom-BPiPQhN5.js | 200 | 43848 |
| 22:41:03 | GET | /assets/CardsAdmin-BXwbvoPk.js | 200 | 11805 |
| 22:41:04 | GET | /assets/PersonaEditor-CY0YsfDU.js | 200 | 90530 |
| 22:41:05 | GET | /assets/PersonaList-D1blR-q3.js | 200 | 14347 |
| 22:41:07 | GET | /assets/YamlEditor-BWHd2FPU.js | 200 | 771 |

**47 game requests in all**: 25 in the first pass (the 00:26 re-read included) and 22 static JS chunks in the
revision (00:40–00:41). 0 keyed requests. 0 writes. I fetched the organiser-page chunks (ControlRoom, CardsAdmin,
PersonaEditor, PersonaList, YamlEditor) as the static files any visitor's browser can download. **No admin API route
was called.**
Railway: `railway deployment list` and `railway logs --json <deployment>` (read only) for taker, maker and duels.

## 8. Queries

All ran through `q.py` (a READ ONLY transaction with a 60 s timeout; the DSN is loaded from the lets-start `.env` and
never printed).
- Q1 event types: `select type, count(*), min(tick), max(tick) from feed_events group by type`.
- Q2 organiser events: `select id, tick, type, actor, received_at, payload from feed_events where type in ('news.posted','announcement','schedule.fired','clock.changed','level.announced','level.activated','persona.updated','persona.open_to_all','day.opened','day.closed','round.started','round.ended','set.released','duels.scheduled','duels.finished','bench.started') order by id`.
- Q3 eggs: `eggs.sql` §1. Badge totals: `select payload->>'badge', count(*) … where type='badge.awarded' group by 1`.
- Q4 clues to us: `eggs.sql` §2 → 5 Pilar rows (t649, 717, 720, 721, 734). Our own texts: 255 messages, 0 matching the lore regex.
- Q5 the news #9 effect: settlements where `persona='abuela'`, `items[0].to='abuela'` and `rarity='uncommon'`, split at ticks 979 and 1219.
- Q6 after Payday: settlements where `parties ? 't01'` and `tick >= 900`, split at 1201; Pícaros and Don Ernesto epic or legendary settlements split at 1201.
- Q7 Don Ernesto threads: `threads where ours` grouped by counterpart; `feed_events` `thread.opened` where `with='banco'`, grouped by team.
- Q8 Pilar's clue against Sharp ear per team: a full join of the first Pilar "chulapa" message and the first badge, per team.
- Q9 restarts: `select agent, kind, count(*) from decisions where kind = 'process_started' group by 1,2` → taker 51; joined to the bench and duel windows in §2.5.
- Q10 Friday wall times: `select tick, min(received_at), max(received_at), count(*) from feed_events where tick between 95 and 159 group by tick` (backfill 22:21:43 up to t120; live from t121).
- Q11 Chamberí lore: `thread.message` from dealers where `text ~* '(andén|anden|fantasma|chamber|ghost)'` → 7 rows; only t1370 is a reply to lore.
- Q12 non-triggers for E5/E6: dealer replies matching `(cascorro|plaza mayor|cocido|vuelcos|rosquilla)` set against the `egg.found` ticks.
- Q13 hidden cards in our store: `select id, hidden, minted, updated_tick from cards where hidden` → LAT-13 only (t1436).

## 9. Open questions for Marius

1. **What did the 20:16 talk ("Payday, tips, and a congratulation") say?** The Pícaros, Castizo and gift eggs all
   began after it. Either the tips were given there (then someone in the room has them), or the eggs were switched on
   server-side during the pause. The paired admin `clock.changed` events at 20:15 and 20:57 fit the second reading.
2. **Do you want the badges** (proposal 3)? If yes: who sends, when (a quiet minute, never during a bench or duel
   wave), and which dealers (my order: Pícaros, then Abuela, then Chato only inside a priced move).
3. **The Sunday clock:** do you agree to treat 09:00 as round 3 until 08:55 says otherwise? Should the team freeze
   deploys 08:45–09:40?
4. Pitch: lead with "our agent was told and did not listen", or with the live decoding (`eggs.sql`)?

## 10. What I could NOT verify

- **The exact secret phrases.** The feed hides the team texts, so every phrase in §5 is inferred from the dealer's
  echo. The mechanism (phrase contained in the message, accents and case ignored) comes from the editor bundle, not
  from a test. Each egg's real `max_total`, `probability` and `enabled` state is admin-only.
- **When our sentinel first saw each news item.** The store keeps the airing tick and is re-upserted on restarts
  (§2.1), and the Railway logs start at 19:52.
- **Friday wall times before t121** are derived (±2 min), not captured.
- **How the clock reaches h16.65 on Sunday** (a jump or another re-plan), and whether the hard Market Test (14.65)
  and the h15 bench fire at the open or are dropped.
- Whether eggs can be earned again on Sunday (no team has a repeat), whether there are new Sunday eggs (CHA,
  Pilar), and whether a wrong lore line costs a cooloff.
- Whether news #3 (Chato and MAL rares) moved a price: there was no MAL rare sale to Chato inside the window.
- News #11 (Lavapiés reprint "tonight") can only be checked after the reprint night.
- How many secret cards exist but are still unfound: they are invisible in `/api/catalog` until found.
- **Chunks not read:** I read 26 of the 75 lazy chunks (plus the entry bundle). The 49 unread ones are the icon modules (arrow-*, chevron-*, award, bot, coins,
  eye*, flag, gift, handshake, landmark, lightbulb, lock*, octagon-alert, package, play, plus, radio, search,
  shield-*, snowflake, sun, trending-up, unlock, x) and the UI primitives (Button, ConfirmDialog, EmptyState,
  ErrorNote, IconBtn, PageHeader, Panel, PersonaAvatar, Pesetas, RarityBadge, LevelBadge, Slider, Sparkline, Tabs,
  Toggle, hooks, util, useFit, jsx-runtime). Their names suggest nothing egg-related, but I did not read them.
- Railway logs before 19:52 Saturday: the CLI lists only the last 20 deployments per service. The earlier window comes
  from `_night/RAILWAY_ERRORS.md`.
- Whether badges matter to the judges at all.

## 11. Review response (`_sat-review/review-logs-eggs.md`)

| # | Finding | Done |
|---|---|---|
| 1 HIGH | "logged on the tick they aired" is false | Removed from TL;DR 4, §2.1 and §2.2. The column is now "aired tick". §2.1 explains `news.py:230`, the 10-tick read and the 11:54 ship of #182, and states that first-seen is not verifiable for any item |
| 2 HIGH | Friday timeline missing | §2.2a added, with times derived from ticks (anchor t121 ≈ 22:21:47, ±2 min before t96). Saturday rows added for t192, t651 (Duels I across the pause), 22:45 and the 23:05 admin `clock.changed`. Friday data window stated in §1 |
| 3 MED | 16.65 not derived; "stale" too flat | §2.3: 2.65 + 14.0 = 16.65, Saturday's 3.27 h of pauses itemised, both mechanisms named, the plan given as offsets from `day_opens sun`; TL;DR 3 now says "probably stale; confirm at 08:55" |
| 4 MED | Pen-test depth; "Secret cards found" | 22 more chunks read (§7). §4 now has the Catalogue KPI and the PersonaEditor egg mechanism (phrases, rewards, `once_per_team`, `max_total`, `probability`); `pending_voices` is confirmed. "The only hidden card" is corrected to "the only visible one". Rec 4 adds the hidden-card count and the `cards_heartbeat.py:84` gap. The unread chunks are listed in §10 |
| 5 MED | Friday signals never followed | §2.4 L0 |
| 6 MED | The Chamberí lead | §2.3 and §5 (inferred); `eggs.sql` §3 and rec 4 |
| 7 LOW | A second explanation for E3–E6; E6 trigger | L5 and open question 1 give both readings. E6 narrowed to "Plaza Mayor, con caña" (Cascorro alone and "Plaza Mayor" alone: no). E5 non-triggers listed |
| 8 LOW | Restarts against scoring windows | §2.5 |
| 11 note | Friday and session rows in "iterations" | §1 table |

Nothing rejected.
