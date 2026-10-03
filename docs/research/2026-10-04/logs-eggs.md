# Saturday logs, news and easter eggs (sat-logs-eggs)

Sun 4 Oct 2026, 00:10–00:50 Madrid. Read-only research for Team 1 (t01). No game writes, no Railway writes, no keyed
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
3. **The Sunday schedule changed after the doors closed (inferred, strong).** `/api/schedule` at 00:19 maps Sunday as
   game hours 16.65–22.65 = 09:00–15:00. If that holds, then from 09:00: **round 3 + Chamberí**, grant at ~09:03,
   Market Tests at ~09:21 / ~11:21 / ~13:21, **Duels III at ~11:00**, **stalls close + Grand Final at ~14:00**, freeze
   at 15:00. The clock still reads h13.367, so either it jumps at the open or the schedule moves again. The night plan
   (round 3 at ~11:34, Duels III at ~13:34, "Sunday morning counts for Saturday") is stale. Check at 08:55.
4. **News: 11 items on Saturday, all logged by our sentinel on the tick they aired, none acted on.** True:
   Boletín 2/2, plus two Radio Rastro price items. Each was followed by a dealer `persona.updated` posted by `news`.
   On item #9, Abuela's average bid for uncommons went from 14.3 to 20.0. El Tablón: 0 of 2 checkable items came true
   (#11 can only be checked after the reprint night). The Payday announcement (20:37, +400 P, "Don Ernesto's
   vault and Los Pícaros' epics are within reach") brought us 1 Pícaros rare. We never opened a Don Ernesto thread
   all day; teams opened 33.
5. **The pen test found no hidden endpoint.** I made 25 keyless GETs at ≤1 req/s. The live OpenAPI equals our Friday
   capture (76 paths). `/api/news` and `/api/taller` work but are not in it. The bundles were built Friday and already
   render `egg.found` and team badges. The two odd fields are `/api/health` → `pending_voices` (6) and
   `/api/catalog` → `hidden: true` on LAT-13. Every egg trigger is a dealer message (a write), so they are all
   proposals for Marius in §6.

## 1. Scope, interpretation and data windows

**"Iterations"** is undefined in the request. I covered every reading I could find:

| Reading | Saturday count | Source |
|---|---|---|
| Game ticks | ticks 159 → 1445 (1,286 ticks, 30 s); `t_hours` 13.367 at the close | `/api/clock` 00:19; `feed_events` |
| Rounds | round 2 "Saturday · Gran Vía" started tick 160 (09:29); round 3 pending | `round.started` id 10940 |
| Clock pauses | 3: late open (unpaused 09:28), **13:24–15:29** (2 h 04 min), **20:15–20:57** (Payday talk) | `clock.changed` |
| Level unlocks | announced 5 Sat (Pilar, Radio, Workshop, Pícaros, Don Ernesto), activated 5, open-to-all 3; 72 `level.unlocked` by teams | `feed_events` |
| Dealer prompt versions | 7 `persona.updated`: banco v2 (t254, admin), chato v2/v3 (t463/583, **news**), pilar v2/v3 (t939/1179, schedule = fever), abuela v2/v3 (t979/1219, **news**) | `feed_events` |
| Radio Rastro rounds | 11 news items (ids 1–11), one every ~45–90 min | `/api/news`, `news.posted` |
| Organiser notices | 15 `announcement`, 14 `schedule.fired` (Fri + Sat) | `feed_events` |
| Railway deploy iterations | taker: 51 process starts (ticks 277–1426); ~50 deployments by 13:40 (RAILWAY_ERRORS.md); 20 deployments listed 19:34–00:13 | `decisions.kind = process_started`; `railway deployment list` |
| Our PR iterations | 139 "Merge pull request" commits dated Saturday on `origin/main` | `git log --merges` |

**Data actually seen:**
- Postgres `feed_events` covers ticks 0–1445, ids 15–73292, received Fri 22:21 to Sat 23:43. Friday's events were
  backfilled at 22:21, so their `received_at` is not their event time. The keyless `/api/feed` read at 00:24 ends at
  the same max id, 73292, so the DB is complete at the top. Ids are shared with non-public events: 45k ids are missing
  in 16k gaps, and the biggest gaps sit at duel-session starts. So capture loss cannot be fully ruled out. The badge
  count does cross-check exactly: the leaderboard shows 21 badges and the DB holds 21 `badge.awarded`.
- `messages` / `threads` (ours), `learnings`, `decisions`: Saturday.
- Railway logs (read only, `railway logs --json <deployment>`): taker, maker and duels, 19:52 Sat → 00:18 Sun
  (4,419 lines). The CLI lists only the last 20 deployments per service. For before 19:52 I rely on
  `_night/RAILWAY_ERRORS.md` (10:19–13:40).
- Live keyless reads: 25 GETs, 00:19–00:26 Sun, all logged in §7.
- I did not use `.local/stream.jsonl` (Friday). Its window is in the DB anyway.

## 2. News, notices and schedule entries (Part 1)

Times are Madrid (`received_at` of the feed event). "Our log" means the taker's sentinel learning
(`learnings.kind = 'news'`, `created_tick`).

### 2.1 Radio Rastro (`/api/news` = `news.posted`)

| id | Tick · time | Source | Headline · body | Came true? (evidence) | Our reaction |
|---|---|---|---|---|---|
| 1 | 283 · 10:30 | Boletín | Radio Rastro is on the air | yes (the level) | logged t283 |
| 2 | 331 · 10:55 | Radio | Atleti win 2-1… car horns on Gran Vía | "just Madrid" | logged t331 |
| 3 | 403 · 11:31 | Radio | El Chato is looking for rare Malasaña cards · pays above usual, one hour | **probably**: `persona.updated chato` by actor `news` at t463 (v2) and t583 (v3, +120 ticks = 1 h). Price effect unmeasured: 0 MAL rares sold to Chato in 463–582 | logged t403; no action (no "N % over book" wording, `news.py:45`) |
| 4 | 499 · 12:19 | Tablón | El Chato gives a legendary to anyone who says hello! | **no** (no `gift.given` by Chato ever) | logged |
| 5 | 583 · 13:01 | Radio | Metro line 5 closed Ópera–Callao | just Madrid | logged |
| 6 | 643 · 15:35 | Boletín | **Abuela gives out packs for her saint's day** · "Happy saint's day, Carmen." | **yes**: `sobre_barrio` landed 1 h later (t763, RULES_AUDIT news #3). **The saint's day is also an egg clue** (§5, "Castizo") | logged; the pack was opened (`open_sealed_packs`) |
| 7 | 763 · 16:35 | Tablón | Abuela stops buying common cards | **no** (she bought commons 6× after, avg 5.8 P; RULES_AUDIT news #4) | logged |
| 8 | 835 · 17:11 | Radio | Half-hour queue at San Ginés churros | just Madrid | logged |
| 9 | 943 · 18:06 | Radio | **Abuela pays more for uncommon cards until teatime** | **yes**: `persona.updated abuela` by `news` at t979 (v2), back at t1219 (v3). Abuela's uncommon buys: avg **14.3 P** before (n 9) → **20.0 P** in t979–1218 (n 4) | logged; **0 uncommons sold to her in the window** (`dealer_sell_enabled` false) |
| 10 | 1027 · 18:48 | Radio | Sun and 24°; a storm after ten | just Madrid | logged |
| 11 | 1123 · 19:36 | Tablón | All of Lavapiés will be reprinted tonight · sell spare LAV now | unverified (overnight); Tablón is 0/2 so far | logged; no action (correct) |

Queries: `select … from feed_events where type in ('news.posted','persona.updated')`; uncommon buy prices: settlements
with `persona = 'abuela'` and `items[0].to = 'abuela'`, grouped by window (in §8).

**Pattern (inferred from 3 cases, small sample):** a true market item is followed within 36–60 ticks by a
`persona.updated` whose actor is `news`. Fever patches carry actor `schedule`, and Don Ernesto's v2 at t254 carries
`admin`. Rumours had no such event. RULES_AUDIT (news #17) already lists "react to `persona.updated`" as NOT
IMPLEMENTED. The bundle shows the news is a pre-written deck aired by hand (`/api/admin/news/{id}/air` in
`index-B_RfsMCE.js`), so expect more items on Sunday.

### 2.2 Organiser notices and schedule actions (Saturday)

| Tick · time | Event | Text / change | Our reaction |
|---|---|---|---|
| 159 · 09:00 | day.opened, announcement | Saturday until 23:00, 30 s ticks | – |
| 159 · 09:28 | clock.changed | **unpaused 09:28**: the open was 28 min late | the taker's schedule lead times re-read |
| 160 · 09:29 | set.released RET, round.ended 1, round.started 2 | "Saturday · Gran Vía", weight 1.0, reset false | – |
| 165 · 09:31 | schedule.fired grant_all | "El Retiro has arrived: a pack and the Saturday allowance (150 primas)" | – |
| 201 · 09:49 | bench 1 | Market Test (16 ticks, 18 venues) | stall-level 0.5 (MM_DEEP) |
| 252 / 262 · 10:15 / 10:20 | level pilar announced / activated | "I collect what others throw away" | taker playbook |
| 254 · 10:16 | **persona.updated banco v2 (admin)** | Don Ernesto edited ~8 h before he was even announced (18:00) | – |
| 272 / 282 · 10:25 / 10:30 | level radio announced / activated | news via `/api/news` | sentinel on since #182 (11:54) |
| 282 · 10:30 | announcement | maintenance restart in 1 min (back 10:32) | – |
| 441 · 11:50 | bench 2 | | |
| 459 · 11:59 | duels.scheduled Duels I | 306 duels, 16 ticks, decay 0.06 | duels runner |
| 502 · 12:20 | pilar open to all | | |
| 630 · 13:24 → 15:29 | clock paused **2 h 04 min** | the Workshop and Pícaros announced during the pause (14:11, 14:16) | – |
| 681 · 15:54 | bench 3 | | |
| 706 · 16:07 | level taller activated | `POST /api/taller` (not in the OpenAPI) | not used (RULES_AUDIT #10) |
| 761 · 16:35 | level picaros activated | "flag a trick (POST /api/flags)" | flags off |
| 850 · 17:19 | announcement | "Salamanca fever: Pilar pays 25 % over book for Salamanca starting in 45 min" | logged; the sell desk is off (RULES_AUDIT #12) |
| 881 · 17:34 | picaros open to all | | |
| 921 · 17:54 | bench 4 | | |
| 932 / 971 · 18:00 / 18:20 | level banco announced / activated | "Gold is not shown. It is negotiated." | **0 banco threads by us all day** |
| 939 · 18:03 | schedule.fired persona_patch | fever on. The note still says "until 17:30" (stale wall time; it ran 18:04–20:04) | SAL-07 incident at t947 (KNOWLEDGE_SAT_EVENING §1.1) |
| 1091 · 19:20 | banco open to all | | |
| 1161 · 19:55 | bench 5 | | |
| 1179 · 20:04 | schedule.fired | "The fever breaks" | logged at t1167 from the schedule |
| 1194 · 20:11 | announcement | "the game pauses in 2 minutes … come to the front" | – |
| 1201 · 20:16 | announcement | "⏸ … Announcement at the front: **Payday, tips, and a congratulation**" | **content of the talk unknown to me (open question 1)** |
| 1201 · 20:37 | announcement | **"Payday in Madrid: every team gets 400 primas, a second starting purse. Don Ernesto's vault and Los Pícaros' epics are within reach. Only deals score, never cash you hold."** | stored as a learning; after it, t01 settled 1 Pícaros rare and sold 1 rare to a team. market-wide, epic settlements with the Pícaros went 7 before → 11 after, and epic or legendary settlements with Don Ernesto 2 → 1 (direction not split) |
| 1201 · 20:57 | announcement, clock unpaused | "Duels II starts in about 20 minutes" | |
| 1239 · 21:16 | duels.scheduled Duels II | 612 duels, 16 ticks, decay 0.08, 2 rounds | |
| 1401 · 22:37 | bench 6 | | |
| 1431 · 22:52 | duels.finished Duels II | | |
| 1445 · 23:00 | day.closed | "Closed until Sunday 09:00" | |

The pause shifted every game-hour event. Before 20:15 the taker logged "Sunday opens at game hour 14.078"; after
20:57 it logged 13.368 (taker logs, `schedule:` lines, 17:52Z and 18:58Z). The logging part of our sentinel worked:
every news item has a learning on its airing tick (`learnings.kind = 'news'`: 13 rows, ticks 283–1167), and the
schedule lines were re-said after each taker restart in the log window.
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

  Why I think the first column is the plan (inferred, not confirmed): at 16:52 the same entries said `day_opens sun`
  14.083 and `day_closes sun` 20.083 (RULES_AUDIT news #4). Now they say 16.65 and 22.65. Both moved by the same
  +2.567 h, so the two wall-pinned entries moved together, and the close now lands exactly on the score freeze:
  16.65 → 22.65 is 6 h, which is 09:00 → 15:00. The clock, though, is paused at 13.367, so getting there needs a
  **+3.28 h** jump at 09:00 (or another re-plan). The server's own `at_hours` for `day_opens sun` was 14.078 at 19:52
  and 13.368 at 20:58 (taker `schedule:` lines), and 16.65 at 00:19. So the change happened between 20:58 and 00:19;
  the server restart at ~23:06 (`uptime_s`) is the likely moment. In the second column the Grand Final and the freeze
  never happen, which no organiser would plan. What is open is how the clock gets there and what happens to the two
  benches at 14.65 and 15.0.
  **Consequence:** KNOWLEDGE_SAT_EVENING §1.6 (round 3 ~11:34, Duels III ~13:34, the finale after the close) and
  RULES_AUDIT TL;DR 8 ("Sun 09:00–11:34 still counts for Saturday's round") are probably obsolete.
- **The server was restarted after the close:** `/api/health` → `uptime_s` 4408 at 00:20, so it started at about
  23:06 Sat. That is a post-close deploy; the schedule change may come from it.
- **News deck:** `/api/news` is still at 11 items. The admin bundle airs pre-written items, so expect more on Sunday.

### 2.4 What we reacted to late or never

| # | Signal | When | Reaction | Evidence |
|---|---|---|---|---|
| L1 | **Pilar: "ask Carmen at El Rastro about the golden chulapa"**, to us | 5× at 15:38, 16:12, 16:14, 16:14, 16:21 (ticks 649–734) | **never** (our 255 sent messages are price templates; 0 contain any lore word) | `messages` ⋈ `threads.ours` (§8 Q4) |
| L2 | Abuela gave us 3 gifts ("por ser amable") | t511, t954, t1201 | not a signal; they show kindness works with her (RULES.md:54) | `gift.given` |
| L3 | News #9 (Abuela +40 % on uncommons, true) | 18:06–~21:00 | 0 sales (desk off by decision after the 12:24 sell loop) | §2.1 |
| L4 | Payday: "Don Ernesto's vault … within reach" | 20:37 | **0 Don Ernesto threads all day** (other teams: 33 threads, 11 teams). 1 Pícaros rare bought after payday | `threads` (ours: abuela 25, picaros 37, pilar 9, chato 4, banco 0); `thread.opened` |
| L5 | The 20:16 talk "Payday, **tips**, and a congratulation" | 20:16–20:57 | unknown. The first "Trickster tricked" came at 21:10 (13 min after play resumed) and 6 teams had it by 22:05; "Castizo" and the gift eggs all came at 22:04–22:34 | `egg.found` ticks; open question 1 |
| L6 | The schedule moved by the 20:15 pause, and again after the close | 20:57; ~23:06 | the taker re-said the new hours (logs). Our human plans did not (KNOWLEDGE_SAT_EVENING §1.6 predates both) | taker `schedule:` lines; §2.3 |
| L7 | Don Ernesto `persona.updated` v2 at 10:16, ~8 h before his announcement | t254 | none needed (inferred: that edit is where the egg went in) | `feed_events` |

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
(limit 60/s), 25 requests in all, no `/api/admin/*` (no token, so no wrong-token count), no stream, no fuzzing.
I listed paths only from the OpenAPI, the kit, the docs and the bundles, and I triggered nothing.

## 4. Pen test: the surface (Part 2)

| Source | What it gave | Odd or new |
|---|---|---|
| `/openapi.json` (live, 49.6 KB, sha `90e4b40f48cf`) | 76 paths, **the same paths, methods and schemas** as our Friday capture `docs/api/openapi.server.json` (only formatting differs) | `/api/news` and `/api/taller` are live but **absent** from the OpenAPI. 41 `/api/admin/*` paths (organiser token): listed, **not touched** |
| Kit (`/bazaar-kit.zip`) | sha256 `e899ea974d21…` = `vendor/bazaar-kit/VENDORED.md`. **No new kit** | – |
| `/robots.txt`, `/sitemap.xml`, `/humans.txt`, `/.well-known/security.txt` | all return the SPA `index.html` (same sha `d3e0fb9b0dd3`) | nothing hidden there |
| `/` + response headers | `Server: uvicorn`, `Via: 1.1 Caddy`, CSP `frame-ancestors 'none'`, `X-Frame-Options: DENY`, index `Last-Modified: Fri, 02 Oct 16:29:47 GMT` | the frontend has not been rebuilt since Friday |
| `/assets/index-B_RfsMCE.js` (390 KB) | every public route matches the OpenAPI. Admin routes include `/api/admin/news` and **`/api/admin/news/{id}/air`** (a news deck aired by hand) | no egg strings: the eggs live in the dealer prompts on the server |
| `/assets/EventLine-Bsrbkjwf.js`, `BigScreen-CIAu_gkJ.js`, `Teams-TPp3tDwD.js` | they render `egg.found` as "<team> found an easter egg", `egg.given` as "…for finding an easter egg", and **team badges** (`e.badges`) next to each team | eggs were planned from day one (Friday build) |
| `/api/health` | `{"ok", "tick", "last_tick_age_s", "loop_age_s", "paused", "doors", "uptime_s", "pending_voices": 6}` | **`pending_voices`** is undocumented. Inferred: the queue of dealer replies still being written (the "voices"); 6 were pending at the close |
| `/api/catalog` | LAT has 13 cards: **LAT-13 "La Chulapa Dorada", legendary, `hidden: true`, print_run 1, minted 1**, flavour "Only one was ever printed. Don Ernesto knows where." The other sets have 12; CHA is unreleased (`sun+0h`) | the only `hidden: true` card. RULES.md:49: "The hidden card is prestige only: no dealer buys it" |
| `/api/leaderboard` | each team has `badges`, `luck`, `rarest`, `adjustments` (all `[]`) and `frozen` | badges are public |
| `/api/dealers` | 5 dealers, with traits, unlock rules and menus. Don Ernesto's title: "treasury desk at **Casa Prima** on Calle de Alcalá" | "Casa Prima" is the place Abuela's clue names |
| `/api/clock`, `/api/schedule`, `/api/levels`, `/api/news`, `/api/venues`, `/api/feed?limit=50` | §2.3; the feed's newest id 73292 equals the DB's | – |
| Dealer texts in the public feed (12,012 `thread.message`) | team texts are `null` and dealer replies are public, so **the replies reveal the eggs** (§5) | the main source |

## 5. The eggs found on Saturday (from the public feed; triggers inferred from the dealer's reply)

Team texts are `null` in the feed, so every trigger below is **inferred** from the dealer's reply on the egg's tick
(query `eggs.sql` §1). Several triggering team messages carry no structured offer: t09 at t554, t10 at t1039 and
t05 at t1046 show empty `give`/`want`. That suggests a text-only message is enough (inferred).

| # | Dealer | Reward | Teams (first tick · time) | Inferred trigger (the words the dealer echoes) | Where the clue was |
|---|---|---|---|---|---|
| E1 | Abuela | badge **Sharp ear** | 11: t04 (407 · 11:33), t02, t09, t10, t05, t16, t13, t18, t03, t08, t06 (1337 · 22:05) | ask her about **"la chulapa dorada"** (the golden chulapa). She answers "shh… only one was ever printed… ask Don Ernesto at Casa Prima about **the Moscow gold**" | Pilar told 15 teams, 66 times from t320 ("ask Carmen about the golden chulapa"); 4 teams got the badge before Pilar's hint reached them, so there is another route too |
| E2 | Don Ernesto | **LAT-13 La Chulapa Dorada** (hidden legendary, 1 copy) | **1: t02 (1021 · 18:45)** | say **"el oro de Moscú"**: "So you know the story — very few do. For that, the chulapa is yours" | Abuela's E1 reply. **Gone:** t10, t05, t13, t04 and t08 asked later and were told "the chulapa stays in the vault" (t1042–1176) |
| E3 | Los Pícaros | badge **Trickster tricked** | 6: t18 (1227 · 21:10), t05, t10, t08, t02, t06 (1336 · 22:05) | name the old con stories: **Lazarillo, Rinconete (y Cortadillo), "el timo de la estampita"**. Reply: "you know the old trick… so no tricks for you… today" | no clue in the feed; first seen 13 min after the 20:16 talk ("tips") |
| E4 | Abuela | badge **Castizo** | 4: t08 (1335 · 22:04), t02, t10, t05 (1369 · 22:22) | Madrid lore: **"sile, nole, repe, me falta"** (the old card-swap chant), **the chotis danced on one tile ("una baldosa")**, **her saint's day** (Virgen del Carmen, 16 July), the verbena de la Paloma | news #6 "Happy saint's day, Carmen" (15:35). t02 thanked her for the saint's day at t803 without a badge, so the saint's day alone is not enough |
| E5 | Abuela | one of her duplicate cards (MAL-06 ×2, LAV-08) | 3: t10 (1364 · 22:19), t05, t08 (1394 · 22:34) | **"cocido con sus tres vuelcos"** (her mother's cocido), **"rosquillas tontas y listas"**. Reply: "take this one, for remembering her" | – |
| E6 | El Chato | a `sobre_barrio` pack | 2: t10 (1363 · 22:19), t08 (1394 · 22:34) | **"Plaza Mayor, con caña"** (calamari sandwich and a beer) and **Cascorro**. Reply: "you know Madrid. Here, for your trouble" | – |

What else the data shows:
- **One per team per egg.** No team holds the same badge twice. t10 and t08 hold 5 eggs each, with E1, E3, E4, E5
  and E6 all different.
- **The eggs came in waves.** Six "Sharp ear" badges came within 37 ticks (1040–1077, 18:54–19:13), right after t02's
  LAT-13 egg showed on the big screen at 18:45 (inferred: copycats). Every E3–E6 egg came after the 20:16 talk.
- **The Pícaros "no tricks" offers were not bargains.** After E3, the Pícaros offered to buy commons at 4 P. Their
  usual bid is 4–5 P (median 5, n 15 Pícaros common buys), so the only effect is that their offers stop switching the
  card "today". Our taker walked from 4 provable Pícaros switches (CARD_SCOUT §5, RULES_AUDIT #9).
- **Points: 0.** RULES.md:122 lists easter eggs and gifts as never counting, and RULES.md:49 says the hidden card is
  "prestige only". The badge correlates with rank: 5 of the top 6 teams wear one (not t12). But the top teams are
  simply the most active, so this is not causal.
- **Doña Pilar has no egg found yet;** she is the clue carrier. Don Ernesto's only known egg (E2) is spent.
  Speculation, unverified: Chamberí's theme ("ghost stations and hidden gardens", "Andén 0") reads like a home for a
  Sunday hidden card.

## 6. Recommendations for Sunday (ranked by expected points)

| # | What | Concrete change | Expected points | Risk |
|---|---|---|---|---|
| 1 | **Re-plan Sunday on the current schedule:** round 3 at 09:00 (not 11:34), Duels III ~11:00, benches ~09:21 / 11:21 / 13:21, stalls close + Grand Final ~14:00, freeze 15:00 | No code: the playbook and schedule watch read `/api/schedule` live. Humans: at **08:55** run `uv run bazaar clock` and a keyless `GET /api/schedule`, then take the matching column of §2.3. **No deploy 08:45–09:40**: it covers the 09:21 bench and the two overdue benches (14.65 hard, 15.0) if they fire at the open | protects the whole Sunday timing. Round 3 is "Sunday · Chamberí", weight 1, so a Sunday session is worth ~7.5 × its bench points (KNOWLEDGE_SAT_EVENING §2.2) and a missed Duels III or Grand Final wave costs its whole duel share | none (reading only). If the clock resumes at 13.367 instead, Duels III moves to 14:17 and the Grand Final falls past the close |
| 2 | **Pitch (judges 40 %):** tell it honestly: "the dealers hid a story in their replies. Pilar gave our agent the clue 5 times; it only listened for prices." Then show either (a) the read-only `eggs.sql` decoding of all 6 eggs from public replies, or (b) a badge earned live (proposal 3) | `docs/research/2026-10-04/logs-eggs/eggs.sql` (read only) | judges' share only; unknown size | (a) none; (b) see 3 |
| 3 | **Proposal for Marius, not done:** earn the badges by hand, in a quiet minute (no bench or duel wave). Each egg is **one dealer message (a write)**. Ranked by risk: **E3 Pícaros** (traits: strictness 0.1, memory 0.3; side benefit: no card switching "today"), **E1 + E4 + E5 Abuela** (strictness 0.1, patience 0.85: lowest risk, up to 2 badges and a duplicate card), **E6 Chato** (strictness 0.85, memory 0.9: only inside a genuine priced move, never text alone, to avoid the spam/cooloff rule of RULES.md:50-52). **Skip E2** (LAT-13 is gone, and Don Ernesto's strictness is 1.0) | SDK: `b.open_thread("<dealer>", topic={...})`, then `b.say(thread_id, "<phrase>")` with **no `price`** (a text-only message binds nothing), then `b.close_thread(thread_id)`: ~3 POSTs per dealer, ≥1 tick apart, ~9–12 in all. First check `uv run bazaar threads`: one open thread per dealer, so do it when the taker holds none with that dealer, or the open is refused | **0 by rule** (RULES.md:122). Badges show on the public board and big screen. The Pícaros honesty may save one walked deal | (i) cooloff with Chato or Don Ernesto if the words read as spam; (ii) a thread slot taken from the taker for 2–3 ticks; (iii) ~10 requests at 15 s ticks, where the Sunday budget is tight (MORNING §1: 4.73 req/s at the ceiling), so do it inside a quiet tick; (iv) a gifted card or pack is "luck/gift" and must not be sold below value by the guard (`max_score_loss_per_move` still applies) |
| 4 | **Read-only egg and news watch every 30 min on Sunday** (a human or a monitor laptop) | Run `eggs.sql` §1 and §2 through `q.py`; for news, `select … from feed_events where type in ('news.posted','persona.updated') and tick > <last>` | 0 directly; turns a new Sunday egg or true news item into a human decision within minutes instead of never | none (DB SELECTs, zero game requests) |
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

25 game requests in all, the 00:26 re-read included. 0 keyed requests. 0 writes.
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
- Q9 restarts: `select agent, kind, count(*) from decisions where kind = 'process_started' group by 1,2` → taker 51.

## 9. Open questions for Marius

1. **What did the 20:16 talk ("Payday, tips, and a congratulation") say?** The Pícaros, Castizo and gift eggs all
   began after it. If the tips were there, someone in the room has them.
2. **Do you want the badges** (proposal 3)? If yes: who sends, when (a quiet minute, never during a bench or duel
   wave), and which dealers (my order: Pícaros, then Abuela, then Chato only inside a priced move).
3. **The Sunday clock:** do you agree to treat 09:00 as round 3 until 08:55 says otherwise? Should the team freeze
   deploys 08:45–09:40?
4. Pitch: lead with "our agent was told and did not listen", or with the live decoding (`eggs.sql`)?

## 10. What I could NOT verify

- **The triggering team texts.** The feed hides them, so every trigger in §5 is inferred from the dealer's echo.
  Whether a text-only message triggers is inferred from empty offers on 3 triggering messages.
- **How the clock reaches h16.65 on Sunday** (a jump or another re-plan), and whether the hard Market Test (14.65)
  and the h15 bench fire at the open or are dropped.
- Whether eggs can be earned again on Sunday (no team has a repeat), whether there are new Sunday eggs (CHA,
  Pilar), and whether a wrong lore line costs a cooloff.
- Whether news #3 (Chato and MAL rares) moved a price: there was no MAL rare sale to Chato inside the window.
- News #11 (Lavapiés reprint "tonight") can only be checked after the reprint night.
- What `pending_voices` measures (the name is the only evidence).
- Railway logs before 19:52 Saturday: the CLI lists only the last 20 deployments per service. The earlier window comes
  from `_night/RAILWAY_ERRORS.md`.
- Whether badges matter to the judges at all.
