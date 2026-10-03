> Sanitised copy of `_night/BITES.md` (night shift of Fri 2 → Sat 3 Oct 2026, file state at ~07:15 Madrid).
> `[private]` marks redacted private game values (cash, card values, affinities, our price caps and bid
> ladders, album state). Numbers inside test descriptions (`tests/bites/*`, chaos runs, proof tests) are
> fixture values, not our real ones. See [../SUMMARY.md](../SUMMARY.md) and [../INDEX.md](../INDEX.md).

# BITES — what could bite us Saturday/Sunday (r2-bite-hunter)

Owner: session night-r2-bite-hunter. Tests that prove a bite live on branch `night/r2-bite-hunter`
(`tests/bites/`). Status: untested → confirmed / disproved / partly. "Where" = which code it reproduces on
(main, or a day-PR tip: #60, #62, #72 = #61+#68+#72, ...). Real-server behaviour we cannot test is marked
"sim-verified, real server unverified".

## Top risks for Saturday (06:40) — ranked (r2; evidence below, tests on `night/r2-bite-hunter`, PR #107)

1. **09:00 opens on `main`.** Railway taker + maker + duels are live from the opening. On main: one tick can
   breach `cash_floor`; dealer thread bids filling together paid 165 P in a game hour (cap 150) in the sim; a
   dropped Postgres connection silently kills the duel player; the taker can close at a dealer's opening ask.
   **Mitigation/decision:** before 09:00 merge #62 then #72 (#61+#68+#72) and the takeover chain #140 → #141 → #142
   → #143 (B17 restart orphans, B18 429s, B14 expired bids, B16 unsettled accepts). Verified 06:05: on #143 my chaos
   sweep (10 seeds × steady / redeploy every 7 ticks) has **0 flags**: ledger = what was really paid, worst real hour
   ≤ 150, cash floor held. On main the same sweep flags 19 of 20 runs. Follow b5's merge order and fix-ups (#120).
2. **Merging = redeploying the live agents mid-play.** Every merge touching `src/**` etc. restarts duels/taker/maker
   (minutes without moves). Without #140, every redeploy run under-booked the ledger by 10–65 P and 6 of 10 really
   paid 152–160 P in a game hour (X3). **Mitigation:** merge everything BEFORE 09:00, else only between duel
   sessions (Duels I ≈ 96 min from h6.5, Duels II ≈ 96 min from h13); never during a duel wave.
3. **Duels on the default v1.** Two-issue offers outside our limit at Duels II unless #60 is in (under the adverse
   days sign; probe P7). v1 closes 2 of 6 shared-deadline duels inside our limit (one accept per tick), re-anchors
   after a redeploy, and loses an inside-limit deal when D-2/D-1 are skipped (v2 too). **Decision:** merge the B27
   duel stack (#60 → #86 v2 → …) and choose `duel_policy` = v2 for Duels I; run probe P6 at the first wave (if the
   server does not count duel accepts per tick, our 1/tick slot is self-imposed and costs deals).
4. **#71 merged by habit.** Its head (1696789) makes the live maker open a 270 P venue at h6.5 on its own and moves
   `cash_floor` 270 → 100 (+270 reserve until then: every buy before the grant is blocked). r1: DO NOT MERGE as is.
   **Decision:** merge it only as a deliberate venue decision (with #118's path), never in the 09:00 batch.
   Checked 06:15 on #71 @ 9dea8bd: `agent maker` has `--venue` ON by default and Railway runs plain `agent maker`,
   so a merge arms the keeper; it opens only when the Postgres key vault answers (fail closed without it; Railway
   has it). In the sim from h6.4 with [private] P, every maker bid before h6.5 was refused ("cash 408 − 70 <
   cash_floor 100 + venue_bond_reserve 270"): the reserve idles the maker's buying side until the venue opens.
5. **Blind spots that cost quietly.** (a) An agent whose every tick raises keeps `/health` ok (X24): check
   `bazaar status` / decision rows every ~30 min (B21 open). (b) Nothing opens packs (X21): open Friday's welcome
   pack ([private] asset id) and the granted pack by hand; turn the taker's pack gate off until B20. (c) Keep one laptop
   monitor running (X23: after a redeploy without it, a bid limit moved by [private]). (d) Taker go-live: B28 says
   cash headroom is the binding constraint — raise `min_buy_surplus` to 4–6 or it spends the [private] headroom on 1–4 P commons
   in the first ticks. (e) Hand trades only via `bazaar sell … --live` (X19, #79 hands-off rows).

Rehearsal @ 347e317 (06:20): bite suite 43 passed, 31 XPASS, 0 failed — but it does not yet contain #106, #110/#144
or the takeover chain #140-#143, so X3, X4, X8, X15, X18 and X20 are still open there; the merge set for 09:00 must
add them (each verified on its own branch; X3/X15/X18/X20 together on #143 with 0 chaos flags).

Also checked tonight (06:00-06:20): SDK connections — one new TCP (TLS on the real server) connection per request,
16–20 per taker+maker tick in the sim; port exhaustion disproved (≈ 1–2 connections/s → tens of TIME_WAIT
sockets; the 22k TIME_WAIT on this Mac are the night's test runs); cost ≈ one extra TLS handshake per request
(~1–1.5 s per agent tick at 30 ms RTT; 10 % of a Sunday tick). Low; a keep-alive opener in `sdk.py` would remove it.

## Top decisions for Marius (r2, updated 03:49; PR #107, tests on `night/r2-bite-hunter` @ e80b859, `BITES_STRICT=1` to see flips)

1. **Merge #62 and #72 (= #61 + #68 + #72) BEFORE 09:00.** The Railway taker/maker/duels go live at 09:00 on `main`.
   On main: one tick can take cash below 270 and three dealer thread bids can fill at one boundary for [private] P against
   the 150 cap (X7); a dropped Postgres connection silently stops the duel player for good (X11); the taker can close
   at a dealer's opening ask (no ladder credit, #61). Evidence: X7 tests XPASS on the #72 tip.
2. **Merge #60 BEFORE Duels II (h13).** Main's v1 sends two-issue offers at days = 5 unvalued: 10 of 16 ticks
   outside our limit at |w| = 4 (X5-B1, blocker).
3. **Decide v2 (#86) for Duels I/II.** v1 (default) closes only 2 of 6 shared-deadline duels inside our limit
   (one accept per tick), re-anchors after a redeploy, and can lose its deadline accept to the taker (X5-B3/B6, X17).
   v2: 6/6, restart-safe, books the slot before Jev.
4. **Merge freeze (X16).** Every merge touching `src/**`, `GUARDRAILS.md`, `STRATEGY.md`, `RUNTIME.md`, `uv.lock`,
   `pyproject.toml`, `vendor/**`, `questions/**`, `.railway/**` redeploys duels+taker+maker: minutes with no moves,
   v1 duel progress reset, taker dealer threads orphaned and their deals never booked (X3). Merge before 09:00 or
   between duel sessions; after any redeploy close orphan threads (`bazaar threads --status open`).
5. **Expired maker bids eat the hourly cap (X15, high, main and #72).** One 65 P bid counts 130 P after 2 TTLs;
   in the sim run one expired 70 P bid blocked every buy for the rest of game hour 1 (95 P really paid of 150).
   Stopgap = `MakerConfig.offer_ttl_ticks` ≈ 1 game hour — a CODE change, so it needs a merge (→ X16 window).
   Proper fix: B14.
6. **Hand trades vs the live maker (X19).** The maker cancels every board offer it did not plan. Post only via
   `bazaar sell … --live` with #79's hands-off rows (dec8120); PAUSE does not stop cancels on main (only with #68).
7. **09:00 wake-up (X4).** Loops poll every 300 s while doors are closed: merge #106 (verified fix) before 09:00,
   or restart the services just AFTER 09:00:00.
8. **#71 is no longer build-only (head e489449, 03:43).** Its defaults: `allow_venue_open = true`,
   `venue_open_after_game_hours = 6.5`, `cash_floor` 270 → 100 with `venue_bond_reserve` 270 on top until the venue
   opens. Merging it = the LIVE maker spends 270 P on a venue at h6.5 on its own (the same tick Duels I starts), and
   buys keep cash ≥ 370 until then (blocks every buy before the grant). Contradicts the 02:30 decision (no venue, no
   floor change). Do not merge #71 by habit with the day PRs; if you want the venue, merge it on purpose.
9. **If probe P6 shows duel accepts do NOT count against `accepts_per_team_per_tick`**, our shared 1/tick ledger
   slot is self-imposed for duels: v1 accepts only at D-2/D-1, so a Duels I wave of 3 duels sharing a deadline closes
   at most 2 (X5-B3: 2 of 6 in the 6-duel test) — up to ~12 lost deals over Duels I's 12 waves. Then let duel accepts
   bypass the taker's slot (code + a merge window), or run v2 (6/6).
10. Open, unowned, worth a slot: B15 (taker vs duel accept, v1), B16 (unsettled accepts), B17 (adopt threads after a
   restart), B18 (429 accept burns the slot X20; SDK 429 re-sends; 15 s timeout), B19 (pending_fee).

## 09:00 read-only probes (each answers an "unverified" above; keyless unless noted)

| question | probe | decides |
|---|---|---|
| Does `t_hours` jump to 4.0 at the opening or resume at 2.65? | `GET /api/clock` at 09:00 (`t_hours`, `tick`) + `GET /api/schedule` | B6 playbook times (Duels I 11:30 vs 12:51) |
| What does a closed clock carry (`next_tick_in`, `next_opens`)? | `GET /api/clock` before 09:00 | the B13 fix |
| Do our thread bids appear in `/api/me/offers`? | `GET /api/me/offers` (key) with one dealer thread open | every cash-floor guard for thread bids (X7) |
| Is an accept settled when `/api/me` is read 0.3 s after the tick? | compare `/api/me` at T+1 with the accept of T | X18 likelihood |
| Which fee settles when a change is effective at T+1? | watch a `venue.fee_announced` + a settlement on that venue in `/api/feed` | X8 |
| Do duel accepts count against `accepts_per_team_per_tick`? | Duels I: two duel accepts in one tick (or the error code on the second) | X5-B3, X17 (self-imposed limit?) |
| Sign of `your_days_weight`; `issues` always present? | first Duels II payload `GET /api/duels` (key) | #60 worst case vs signed days (B8) |
| Does a redeploy overlap two instances? | Railway deploy log of the 09:0x restart | X16 duration |

## Index

| id | bite | sev | likelihood | status | where | route |
|---|---|---|---|---|---|---|
| X1 | Ledger across Fri→Sat: `t_hours`/tick jump or reset at `day_opens` breaks the hourly spend cap or the accept slot (unique (tick, slot)) | low | low | disproved (for now) | shared DB | none |
| X2 | One hung keyed GET blocks a loop 46.5 s (15 s timeout × 3 attempts) > a 15 s Sunday tick; sends stay safe (never late) | medium | low-medium | confirmed | main, #72 | BACKLOG B18 |
| X3 | Restart mid-thread: `Taker.convs` is in memory; open dealer threads are orphaned (not driven, bid still standing, a dealer-side deal never recorded as spend) | high | medium (every redeploy, X16) | confirmed | main, #72 | BACKLOG B17 |
| X4 | 09:00 oversleep: doors closed → 300 s poll; first Saturday ticks lost (grant at h4.05) | medium | certain (0–300 s, mean 150 s) | confirmed; FIXED in #106 (both X4 tests XPASS, verified 03:55; edges checked: past/late opening → 5 s poll, no timestamp → 300 s, bad timestamp caught) | main | B13 → #106 |
| X5 | Duels: (B1) v1 two-issue offers at days=5 unvalued → outside our limit; (B2) payload shapes: `issues` missing → accept 104 P @10 days worth 74 < 100, one malformed row kills the whole tick, null weight / float limit / missing deadline → hold = 0; (B3) one accept/tick: 6 shared-deadline duels → 2 deals in v1; (B4) skipped ticks skip the endgame (v1 and v2); (B6) restart re-anchors v1 | blocker (B1 on main), high (B2-crash, B3, B6), medium (B4) | B1 certain at Duels II without #60 | confirmed | main; B1 fixed by #60/#86; B3+B6 fixed by v2 (#86 @ 4417c54) | Marius: #60 before Duels II, v2 decision; w2b for B2/B4 |
| X6 | Request budget at 15 s: 70 of 75 keyed calls/tick at N=3 threads, K=3 duels, maker 45; SDK re-sends every 429 (GET and POST) ×3; #78's model misses SDK retries, per-proposal clock reads, bazaar-mcp | medium-high | medium (Sunday) | confirmed | main, #72, #78 | BACKLOG B18 (+B10) |
| X7 | Same-tick cash floor / hourly cap breaches (accept + bid in one tick; several dealers taking standing thread bids at one boundary); lost-reply deal booked at the wrong price | high on main | high | confirmed on main, FIXED by #72 (XPASS there) except lost reply without the settled offer in the thread view | main | Marius: merge #72 before 09:00 |
| X8 | Announced fee change ignored: `venues_from` drops `pending_fee`; an ask accepted the tick before the change can settle above `max_price_*` (sim charges the OLD fee; real order unverified) | medium | low-medium | confirmed (taker side); FIXED in #110 (night/b19-pending-fee @ 35a1955: its copy of my X8 tests passes; the sim-rounding xfail stays) | main, #72 | B19 → #110 |
| X9 | Offer differs from its text / from the board row (asset swapped for a lesser card) | low | low | disproved: board offers parse strictly (one card for cash, any `want` item or `types` → not an ask); dealer offers: the checked offer IS the accepted one (`newest_dealer_offer`), `offer_terms_problem` requires `give` == exactly [our item] and `want` cash only (a lesser pack/card, extra wants, asset ids without refs → ignored). Text is never read. L4 Tricksters: B3/#93 | main | none |
| X10 | Organisers change `limits` / tick pace mid-game | low-medium | low | analysed: limits are re-read every tick and min'd with GUARDRAILS (accepts 1→2 stays 1; threads 6→1 → no new thread, open ones kept); a renamed `limits` key silently falls back to the defaults (1/1/6/30/12). Pace 5 s: keyed budget 25/tick vs steady ~20 and ceiling 71 → 429s, which burn accepts (X20); `jev_min_budget_s` 4 > the 4.25 s budget minus reads → Jev effectively off; `dealer_max_ticks_per_thread` 14 = 70 s; a pace change mid-tick makes our deadline stale (the accept's `_fresh_tick` re-check covers accepts only) | main | none (B18 covers the 429 side) |
| X11 | Two processes on different ledgers (Postgres down on one → JSONL) double the accept slot and spend; a dropped PG connection silently stops the live duel player | high (duels) | low-medium | known, fixed by #62 (unmerged) | main | Marius: merge #62 before Duels I |
| X12 | New persona/level/set, unknown enum values, payload shape change through the strategy | low | low | disproved (fuzz): None fields, new rarity `mythic`, a new set, asset kind `badge`, feed rows with None tick/type/payload all survive; only string numbers (`book: "x"`) or a card without `id` crash `build_market` (strategy.py:180-187) — and a crash is silent (X24) | main | none |
| X13 | Doors close at 23:00 with open offers / standing bids: they fill at 09:00 against stale prices | low | low | analysed: asks stand ≤ 40 ticks into the morning at prices ≥ the Friday `your_value`; a page completed by the 09:00 grant pack (if opened, X21) raises values 25 % and on main an ask below its new floor stays until its target moves 5 % — #72 problem 5 re-checks floors every tick (fixed there). Bids filling at 09:00 are inside value. | main | #72 |
| X15 | Expired maker bids keep their spend; each repost books it again → phantom spend fills `max_spend_per_game_hour` (3×/h Sat, 6×/h Sun) | high | high (maker live from 09:00, any bid target) | confirmed | main, #72 tip | BACKLOG B14 |
| X16 | Every merge to main redeploys the LIVE duels/taker/maker on Railway mid-play: minutes without moves, duel progress (`first_seen`) and taker threads (`convs`) reset | high | high (morning merges planned during play) | confirmed by config; restart effects being tested (X3, B6) | .railway/railway.py | Marius: merge freeze windows |
| X17 | Taker steals the team's one accept from a slow duel tick (grace 2 s < duel loop's read + Jev 3 s) → a deadline-tick duel accept is refused, the duel scores 0 | high | medium (duel sessions × taker accept candidates × Jev latency) | confirmed (timing invariant); FIXED for v2 in #86 @ 4417c54 and for v1 on night/b15-duels-first @ 1915693 (forced endgame accept booked + sent before Jev; verified: books at ~0.6 s vs taker grace 2 s, 0.75 s at 5 s ticks). Non-forced v1 accepts still wait for Jev (a lost one costs a tick, not the duel) | main, #72 | B15 branch, #86 |
| X18 | An accept not yet settled is not "held": if `/api/me` at T+1 is read before settlement, the taker buys a second copy (duplicate at full price) and the cash floor sees pre-settle cash | medium | low-unknown (real settlement timing unverified) | confirmed (modelled lag) | main, #72 | BACKLOG B16 |
| X19 | The LIVE maker cancels every hand-posted board offer that is not its target (by design) → W4's 09:00 `sell list --to`/`sell bid`, W8 exits and manual listings are cancelled ~1 tick later and burn listing slots | high (ops) | high if anyone trades by hand while the maker is live | confirmed (behaviour test, passes) | main, W4 #79 | FIXED on #79 @ dec8120 (hands-off ledger rows for `bazaar sell … --live`); runbook in #102 |
| X20 | A 429 `rate_limited` accept keeps the reserved accept slot: the team's one accept of the tick is wasted and no other candidate is tried (duel loop same pattern) | high | medium (Sunday bursts) | confirmed | main, #72 | BACKLOG B18 |
| X21 | Nothing opens a pack we hold: no agent or CLI calls `open_pack` (no branch either). The taker buys packs on Jev's yes (≤ 3/h, ≤ 20 P) for an EV that exists only once opened; Saturday's granted `sobre_barrio` stays sealed | medium-high | high (pack gate live on Railway with TYPESAFE_API_KEY) | confirmed | all branches | BACKLOG B20 + w7 (B9) |
| X22 | The taker re-opens a dealer that is out of hourly quota every tick (`open_thread refused persona_quota` 92× in 240 ticks on main, 96× on #72); the maker lists an asset sitting in an accepted, unsettled offer (`asset_locked`) | low (wasted keyed requests, X6) | certain in the sim | confirmed (chaos) | main, #72 | #89 learns blockers (unmerged); else B18 |
| X23 | After a redeploy the taker/maker price from the shared `feed_events` (stale unless a laptop monitor runs all day: the Railway monitor was removed) + the last 500 events: the playbook shifts (a team bid limit and dealer limits move: [private]) | medium-low | high if no monitor runs Saturday | confirmed (Friday capture replay) | main | ops: run the monitor all day |
| X24 | Silent failure: an agent whose every tick raises keeps `/health` ok with a fresh `last_tick_at` (stamped before planning); Railway never restarts it, the dashboard stays green, only the log shows tracebacks | medium-high | low-medium per day (payload change, a bad merge) | confirmed | main, all | BACKLOG B21 + ops: watch decision rows |
| X25 | The strategy values a missing page card at book × aff + its pro-rata page-bonus share (`page_bonus_weight` = 1.0), but the server's values carry no bonus on incomplete pages (real `your_value` = book × aff × marginal on 19/19 held cards, Friday capture) and W7 finds no page completable under today's caps → buys between book × aff and the inflated value score a negative trade gain | medium-low | medium (low-affinity sets, where caps do not bind) | confirmed (real data + code) | main | Marius: `page_bonus_weight` 0 until a page is reachable (STRATEGY.md, a merge); w7 |
| X26 | Weekend-long feed growth slows every tick (taker + maker rebuild the market from ALL events each tick) | low | — | disproved: build_playbook 8 ms @ 3.4k events, 102 ms @ 52k, 170 ms @ 103k (linear; Sunday-scale ≈ 0.3 s per agent tick) | main | none |
| X27 | #105 (holdings in Postgres, day PR): a snapshot is reused inside the tick unless one of OUR `team_client` sends bumped the epoch; hand actions through the raw SDK or curl (W7's manual `open_pack`, a hand accept) do not bump it → up to 5 s / one tick of decisions on pre-action holdings | low | low | analysed (code) | #105 | note for #105 / runbook: hand actions via `bazaar` commands |
| X28 | Broker-key leak: Saturday's `/api/me` may carry `starter_broker_key`; where does it land? | low | — | disproved: the `snapshots` table stores cash/level/assets/album/score only; Friday's capture has no key; #105 strips key-shaped fields; `/state` is allow-listed (#69) | main | none |
| X29 | No HTTP keep-alive: the vendored SDK (`urllib.urlopen`) opens a new TCP/TLS connection per request (16–20 per taker+maker tick in the sim) | low | certain | confirmed (count); port exhaustion disproved; latency ≈ 1 TLS handshake per request | all | optional: keep-alive opener in sdk.py |
| X14 | Wash/ring patterns that could get US penalised (round trips, one-sided pairs); taker accepting our own maker's ask | low | low | disproved for self-trade (reasoning) | main | none |

## Integration check (b5 rehearsal @ de94f42 = main + #60 #62 #61 #68 #72@e92159a #71@0639bb3)
- Bite suite: 39 passed, 43 xfailed, 22 XPASS, 0 failed: X7 (same-tick cash/cap), X5-B1 (days), the PAUSE hold all
  fixed in combination; no cross-PR regression found. Lost-reply deal pricing (X7c) is fixed only in #72's newer
  head e4efc82 (told b5 to take it).
- Chaos (real taker + maker live vs the sim, 240 ticks): seed 7 → 16 buys, 265 P paid, ledger 265 (exact), worst
  real hour 150 P (= the cap, not above), min cash 735; seed 11 with a redeploy every 10 ticks → 10 buys, 153 P,
  ledger exact, worst hour 138. Main, same seed 7: 10 buys, 226 P paid but 296 booked (X15).

## Findings

(one section per bite, newest evidence first)

### X5 — duels (CONFIRMED; 67 tests in `tests/bites/test_b*.py` @ 7d88b0f, failures on main listed in `known_bites_main.txt`)
Versions: main 1112a65, #60 c6ccda4, #86 4417c54. Runs: main 35 F / 28 P; #60 17 F / 46 P; #86 19 F / 48 P.
- **B1 (BLOCKER on main, fixed by #60/#86):** `duelist.py:100` sends days = 5 unvalued; `duel_jev.py:183` makes a
  counter legal on price alone. "offers sent outside limit 50 after days: (126, 70, 5, 50.0) … (135, 54, 5, 34.0)":
  10 of 16 ticks unsafe at |w| = 4. Duels II (Sat h13) on main = deals outside our limit = lost points.
- **B2 payload shapes (all versions):** (a) `issues` missing while the rival's offer has days → we accept 104 P @ 10
  days worth 74 < limit 100, and #60's guardrail misses it (same `_two_issue` check); (b) ANY row that makes
  `duel_move` raise (e.g. `issues: 2`) kills the whole tick for every duel, endgame accepts included (`cli.py:550` has
  no per-duel try); (c) `your_days_weight` null in a two-issue duel: main sends unvalued days, #60/#86 hold forever
  (days = 0 would be safe under either sign); `deadline_tick` missing → endgame never comes; float/str limit or
  "Seller" → held as unreadable (scores 0). Likelihood low each; (b) is cheap to fix.
- **B3 one accept per tick (v1, all versions):** six duels sharing a deadline with rivals inside our limit but short
  of target → 2/6 deals (`accepts sent: [(134, 601), (135, 602)]`); the slot goes by API order, not value (4 P duel lost
  to the 1 P one); the duel that loses the slot says nothing that tick. v2 (#86): 6/6. UNVERIFIED whether the real
  server counts duel accepts against `accepts_per_team_per_tick` (the sim does not; our guardrail does — if the
  server does not, our limit is self-imposed and costs deals).
- **B4 skipped ticks (v1 AND v2):** `run_per_tick` plays only the tick it reads; missing D-2/D-1 (slow tick, Jev 3 s +
  a 10 s Postgres connect, a redeploy) loses an offer that was already inside the limit. Fix: accept earlier when
  the gap since the last handled tick > 1, or plan the accept at D-3 when an inside-limit offer stands.
- **B5 injection: DISPROVED** (structured price only; Jev state carries no words; sent price is ours; words guard
  rejects digits/number words/commitments).
- **B6 restart (v1, all versions; v2 fixed in 4417c54):** after a restart at 130 v1 offers 160, 151, 142, 132 instead of
  126, 122, 119, 115 (retreat to the anchor). The runtime's v1 `duel_move` tool (`runtime/actions.py:181`) always
  starts at `clock.tick` (always the anchor). Fix for v1: `payload_start` (earliest message tick, else deadline −
  duel length) and never offer worse than `your_offer.price`.
- Decisions for Marius: #60 must be in before Duels II (h13); turning on v2 (#86) fixes B3/B6/X17 for duels; B2(b)
  per-duel try/except and B4 → w2b.
- 03:48 update (verified by r2 on #86 @ f6f4435: 23 of my duel xfails XPASS): w2b fixed B2a (days valued whenever
  the weight is numeric or the rival's days ≠ 0, #60's guard included), B2b (each duel in its own try; my trigger
  `issues: 2` no longer raises, their own test covers the isolation), B2c (v2 plays 0 days without a weight; v1
  still holds), B4 for v2 only (wider accept margin after missed ticks). Still open: B4 for v1, float limit /
  missing deadline liveness (hold = 0, low likelihood).

### X25 — the page-bonus share inflates buy values (CONFIRMED on real data, medium-low)
- `strategy.buy_case`: value = book × affinity + share of the page bonus × `page_bonus_weight` (1.0, STRATEGY.md).
  Real Friday `/api/me` (stream capture, tick 149): every held card's `your_value` equals book × affinity ×
  copy-marginal exactly (19/19; no page complete): the server adds no bonus share to incomplete
  pages. The sim's `one_more_value` (its model of `/api/me/value`, used for trade gains) has no bonus term either.
- So a board accept or a maker bid priced between book × aff and book × aff + share scores a negative trade gain at
  our private values, unless that very trade completes the page. W7 (#87) finds no page completable under
  `max_price_rare` [private], so the share is never realised Saturday.
- Exposure: the price caps ([private]) sit at or below book × aff for most affinities, so high-affinity sets are
  capped anyway; the overpay window is the low-affinity sets (book × aff < cap), a few P per trade.
- Decision: set `page_bonus_weight` = 0 (or value with `GET /api/me/value?card=` before a board accept) until a page
  is within reach; it is a STRATEGY.md line → a merge (X16 window).

### X24 — an agent that fails every tick looks healthy (CONFIRMED, medium-high)
- `run_per_tick` reports an `on_tick` exception on stderr and goes on; the taker/maker catch only `BazaarError` and
  `LedgerUnavailable`. `StatusHub.health()` always says `"ok": true` and the taker stamps `last_tick_at` before
  planning. Evidence: `tests/bites/test_silent_failure.py` @ 944c42d: `build_playbook` raising KeyError on 10 ticks in a
  row → `/health` = `{'ok': True, ..., 'tick': 109, 'last_tick_at': fresh}`.
- Saturday: a payload change (new set, new rarity, a renamed field) or a bad merge stops all trading with every
  light green; nobody notices until the leaderboard does.
- Fix: count consecutive failed ticks in the loop; `/health` returns `ok: false` (HTTP 503) after N (e.g. 5) so
  Railway restarts it and the dashboard turns red; publish the last error code on `/state`. Ops until then: watch
  that `decisions` rows keep arriving (`bazaar status`).

### X23 — stale feed history after a redeploy (CONFIRMED by replay, medium-low)
- `MarketFeed` reads `feed_events` from Postgres when it answers (then never loads the JSONL) and merges the live
  window (500 events, the server cap). The Railway monitor was removed (railway.py: "the monitor runs in the CLI on
  a laptop"), so `feed_events` is fresh only while a laptop monitor runs. A long-running agent accumulates the
  window every tick (max 87 public events/tick on Friday, p90 35: the window covers a tick), but after a redeploy
  (X16) it rebuilds from Postgres + the last 500 events only.
- Replay on Friday's capture (tick 149 `/api/me`, fixture catalog/dealers): full history vs the last 500/200/100
  events → 11 of 15 playbook moves change: a team BID limit rises by [private] for the same card
  (overbid on missing tape), Abuela buy limits drop ([private]).
- Decision: keep one laptop monitor running all day Saturday/Sunday (B6 checklist), or put `bazaar monitor` back on
  Railway; after any redeploy check the monitor's `feed_events` lag (`bazaar status`).

### X22 — wasted requests on refusals the agents could predict (CONFIRMED in the chaos run, low)
- Chaos harness, 240 ticks, seed 7: `open_thread refused persona_quota (abuela has done all its deals with you this
  game hour)` 92× on main, 96× on the #72 tip: one keyed request per tick for the rest of the game hour. #89's feed
  reader learns `persona_quota`/`cooloff` blockers and the taker skips them, but #89 is unmerged (R1: merge-blocking
  issue). `list_offer refused asset_locked` 2×: `open_commitments` skips `accepted` offers, so the maker lists a card
  already sold this tick.
- Same run, for scale: on the #72 tip the agents bought 16 cards (266 P) vs 10 (226 P) on main with the same seed —
  main's phantom spend (X15) and its stricter blocking cost purchases; on #72 the LAT-10 bid was cancelled
  (refunded) by the reprice path instead of expiring, so X15 did not show in that seed (its unit test still fails
  on #72).

### X21 — packs are never opened (CONFIRMED, medium-high)
- `grep -rn open_pack src/bazaar_agent` is empty on main and on every origin branch (checked 03:5x). The SDK has
  `open_pack(asset_id)`. The taker's pack gate (`pack_gate.py`, Jev ON on Railway) buys packs judged on
  `pack_expected_value_to_us` — a value realised only by opening. The maker lists cards only (asks need
  `your_value`), so a sealed pack is not sold either. Saturday h4.05 `grant_all` adds a sealed `sobre_barrio`.
- Evidence: `tests/bites/test_sealed_packs.py` @ 25653fb: three live taker ticks with a sealed pack (fixture asset 6) → no
  `open_pack`.
- Cost: every pack bought = its price (≤ 20 P) for nothing until someone opens it by hand; the granted pack's
  cards (possible page cards for the ladder/trade plans) are invisible to the strategy.
- Already happening: W7 reports we hold Friday's sealed welcome pack ([private] asset id). W7's runbook step 1 (#87 @ da3498b)
  opens it and the granted pack by hand at ~09:05; `bazaar plan pages` warns while we hold a sealed pack.
- Fix: open every held pack at the start of a tick (one `POST /api/packs/{id}/open` each; it moves no cash, it is
  not an accept), or a one-line morning step: `python -c` / a `bazaar pack open` command. Decision for Marius:
  turn the pack gate off (no Jev pack judge) until packs are opened, or add the opener.

### X20 — a rate-limited accept burns the team's accept slot (CONFIRMED, high)
- `Recorder.send` returns None on ANY `BazaarError`; `Taker._accept_one` then returns True ("an accept that may
  have landed is never retried"): the ledger reservation stays and `used += 1`. RULES: a refused request "costs
  nothing and moves nothing", so a `rate_limited` accept never used the server's quota. Same pattern in the duel loop
  (`cli.py` reserves, `send` returns "failed").
- Evidence: `tests/bites/test_c1_request_budget.py::test_a_rate_limited_accept_does_not_burn_the_teams_accept_slot`
  @ 1dd1624: `accept attempts: [2] ledger accept items: ['LAV-08']`. Same on the #72 tip.
- Fix: on `rate_limited` / `offer_closed` / `insufficient_cash` release the reservation (delete the row) and stop
  accepting this tick; never release on `network` (may have landed) or `wait_for_tick` (counted).

### X6 — request budget and SDK retries at 15 s (CONFIRMED, medium-high)
- Measured taker tick with 3 threads: 12 keyed calls + loop clock (#78's ceiling 16: OK). Worst case per 15 s tick:
  taker 16 + maker 45 (30 cancels + 12 posts + 3 reads) + duels 6 + monitor 2 + broker 1 = **70 of 75**; one
  bazaar-mcp/CLI call, K = 6 duels (Duels II) or a lost-race loop pushes it over.
- The vendored SDK re-sends a 429-refused call up to 3× for GET AND POST (`bazaar_sdk.py:68-80`: the "never repeat
  a write" guard covers only network errors): `requests sent: Counter({'GET': 3, 'POST': 3})`. Bounded (no
  runaway). #78's sequential replay with the 0.25 s × attempt back-off (the right model): ceiling 64 calls → 71
  requests, 7 refused, 0 lost; 0 lost with the stagger; with 3 `dealer buy` children 110 requests, 2 lost. The taker also re-reads the clock once per proposal after
  a lost reservation race (3 keyed reads in one tick). #78's model omits SDK retries, these clock reads and
  bazaar-mcp.
- A final 429 on a taker READ aborts the whole tick (`taker.py:299-303`: "read refused ... nothing sent"),
  including every dealer move; repeated, dealers walk after `dealer_max_ticks_per_thread`.
- Fix: stagger loops (#78's `BAZAAR_TICK_OFFSET_S`), cap maker cancels/posts per tick (B10), no SDK retry on 429 for
  POST, stop the accept loop after one failed reservation.

### X2 — a hung read stalls a loop for 3 Sunday ticks (CONFIRMED, medium)
- `team_client` keeps the SDK's 15 s timeout and retries GET network errors twice: worst case 46.5 s per keyed
  GET, 31.5 s keyless (`test_one_hung_keyed_read_fits_inside_a_sunday_tick` @ 1dd1624). The loop's clock read uses the
  team client, so the loop stalls ~3 ticks. Sends stay safe: `test_a_hung_read_mid_tick_never_sends_late` passes
  (window check + `_fresh_tick`). A clean restart (connection refused) recovers ~8.5 s after the server is back
  (backoff 1-2-4-8-16 s).
- Fix: 3-4 s timeout and no network retry when `tick_seconds` ≤ 15.

### X8 — the announced fee change is ignored (CONFIRMED on the taker side, medium)
- `/api/venues` exposes `pending_fee` (`tests/fixtures/api/get_api_venues.anon.json:16`); `venues_from` drops it
  and nothing reads `venue.fee_announced`. Precedent: v03 announced 0 → 100 bps two ticks ahead (Friday).
- Evidence @ 1dd1624: `Venue.fee(10)` is today's 0 vs 6 from T+1; the taker would accept LAV-02 at 10 on a 0-fee venue
  whose capped fee (10 % + 5 P) applies at T+1: settles at 16 > `max_price_common` [private] IF the server charges the fee
  in force at settlement. The SIM charges the OLD fee (`settle_due` runs before `venue_tick`); the real order is
  unverified. Also: the sim rounds fees half-to-even (`round`), the tape and our agents ceil: sim backtests
  overstate surplus by ≤ 1 P.
- Fix (cheap either way): `Venue.fee` = max(current, pending) when `effective_tick ≤ tick + 1`.

### X3 + X7 end to end (chaos sweep, 10 seeds × steady/redeploy every 7 ticks, 240 ticks, start cash 900, @ e65d401)
- main: seed 1 really paid 165 P in one game hour (X7: a dealer bid passed on an empty ledger, then 140 P of maker
  bids the same tick, all filled); seed 2 with redeploys paid 206 P/h and the ledger under-booked 32 P (X3).
- b5 rehearsal f481e9e (all day PRs incl. #72 e4efc82): steady runs never exceed the cap; EVERY redeploy run
  under-books the ledger (−10 to −65 P) and 6 of 10 really pay 152-160 P in a game hour → X3 is the remaining
  money bite after #72 (B17 / #114 open). A float-boundary false positive in my harness was fixed (r1 low).

## r1 review of #107 (04:2x) — accepted corrections
- X15 chaos wording: say "the ledger overbooked 70 P" — the hour-1 denials in that seed would also have hit the cap
  without the phantom (unit test still supports HIGH).
- "#60 before Duels II" holds under the adverse days sign (worst case, abs weight); unverified sign (probe P7).
- X5-B4 for v2 is NOT shown fixed on f6f4435: my v2 skipped-tick tests still fail there.
- X6: use #78's 71 of 75 keyed requests everywhere. X20's duel-loop half is code reading, untested.
- Merge #107 before the fix PRs that carry copies of its tests (#106, #110, #114, B15) to avoid add/add conflicts.

### X3 — a restart orphans the taker's dealer threads (CONFIRMED, high; main and #72)
- `Taker.convs` is memory only (`taker.py:290` main / `:303` #72); our own open threads only count as busy
  (`:378` / `:425`); spend is booked only in `_finished` for threads in `convs` (`:455-459` / `:518-522`). Nothing
  adopts or closes threads on start (#72's `flatten` is manual).
- Evidence: `tests/bites/test_bite_a1_restart_orphans.py` @ 60f939e: after a restart, "abuela's deal at 18 on thread 5000
  was never booked: ledger spend 0"; "orphan thread 40 never read nor closed in 3 ticks". Same on #72.
- Saturday: any redeploy (X16) with up to 3 dealer threads open → each standing bid (≤ 80) can fill unbooked, the
  team's hourly cap undercounts, and the dealer stays blocked + one of 6 thread slots used until the dealer idles
  the thread out (40 ticks in the sim ≈ 20 min).
- Fix: on the first tick, adopt each open dealer thread of ours from its topic + our standing offer (conservative
  plan start = max = that bid) or close it; and book `deal` threads that have no spend row (#72's `settled_price`).

### X7 — same-tick cash floor and spend cap (CONFIRMED on main, FIXED by #72)
- `tests/bites/test_bite_a2_cash_floor.py` + `test_bite_a3_dealer_side_spend.py` @ 60f939e. On main: "cash 299 −
  committed 30 < cash_floor 270" (board accept + dealer bid in one tick); "if both dealers take our bids {40: 21,
  41: 20}, spend 41 > max_spend_per_game_hour 30"; three thread bids near the rare cap could all fill at one
  boundary: [private] vs the 150 cap. Lost `say` reply: the deal is booked at the previous step or not at all.
- On the #72 tip all of these XPASS except the lost-reply case when the thread view does not carry the settled
  offer (the real shape is unverified; the sim's does). Cross-tick thread bids are safe on both (they show in
  `/api/me/offers` — sim-verified, real response shape unverified: the recorded fixture is empty).
- Decision: #72 must be merged before 09:00; the main code the Railway taker runs now breaches the floor/cap.

### X19 — the live maker cancels hand-posted trades (CONFIRMED behaviour, high for ops)
- `plan_offers` cancels every plain board offer of ours that is not a maker target (documented in the maker's
  docstring: "stop the maker before trading by hand"). `tests/bites/test_maker_cancels_hand_trades.py` @ bb91cad: a
  `sell list 2 60 --to t12` ask and a `sell bid MAL-07 20` are both cancelled on the maker's first tick (main and
  #79's tip). The Railway maker is live from 09:00.
- Collides with: W4's 09:00 step 5 ("`trade-plan --live`, then post `sell bid` / `sell list --to`"), W8's guarded
  arbitrage exits, any listing Marius makes by hand. Each cancelled post still counts toward 12 listings/tick.
- Decision: either execute the 09:00 plans THROUGH the maker (targets), or send only thread offers (untouched), or
  switch bazaar-maker to dry run while hand trades stand (`railway variable delete BAZAAR_LIVE --service
  bazaar-maker`, restore after; PAUSE does NOT work on main, see caveat), or add a "hands-off" marker (e.g. offers with `to` set, or an id allow-list
  file) the maker never cancels. Direct thread proposals to teams are not touched (thread offers).
- 03:40: w4 fixed it on #79 @ dec8120: a live `bazaar sell list|bid|swap` books a `hands-off:<offer id>` listing row;
  the maker never cancels/reprices those ids (its test `test_the_maker_leaves_offers_posted_by_hand_alone`). Raw API
  offers are still cancelled (my test keeps documenting that). Runbook: post only via `bazaar sell … --live`.
- Caveat: on MAIN the PAUSE file stops the maker's posts but NOT its cancels (`_cancel` has no guardrail
  check): `test_on_main_the_pause_file_does_not_stop_the_makers_cancels`. #68 (inside #72) makes the switch hold
  (verified: that test fails on the #72 tip, as it should). So "PAUSE the maker" protects hand trades only once
  #72 is merged; before that, stop the service or remove BAZAAR_LIVE.

### X18 — an unsettled accept is not counted as held (CONFIRMED with a modelled lag, medium)
- `context_from` builds `held` and `cash` from `/api/me` only; the ledger's `accept` row for tick T (item = card
  ref) is not used. If the T+1 read lands before the server settled T's accept, the taker accepts another copy.
- Evidence: `tests/bites/test_taker_unsettled_duplicate.py` @ bf45705: `bought LAV-08 twice: [('accept', 2),
  ('accept', 3)]`; same on the #72 tip. Real-server settlement timing is unverified (Friday's capture has no
  settlement inside our `agent.me` window), so likelihood is unknown; the cost is one duplicate (0.25× value).
- Fix: treat `ledger.accept_items(tick-1)` and `(tick-2)` card refs as held, and their prices as committed
  cash, until `/api/me` shows them (cheap, local).

### X17 — the taker can take the accept a duel needs on its deadline tick (CONFIRMED by timing, high)
- `Taker._duel_grace` (`agents/taker.py`, same on #72 and #86): the taker waits `duel_grace_s` = 2 s
  (min with 15 % of the tick) into the tick, then reserves the team's only accept unless a `duel:` row is booked.
- The duel loop (`cli.py` duel `on_tick`, Jev ON by default on `bazaar-duels`) books its accept only after
  the 0.3 s settle + `GET /api/duels` + `DuelJev.pick`, which waits for every Jev question of the tick (each
  up to `jev_timeout_s` = 3 s, run in parallel). Worst case ≈ 3.5 s > 2 s.
- Evidence: `tests/bites/test_duel_accept_priority.py` @ 8dd89f7 (strict xfail): "taker takes the slot at
  2.00 s, the duel books it at 3.50 s" at 30 s and 15 s ticks. The taker's own reads usually take ~1–3 s, so it is
  a race, not a certainty; it needs a taker accept candidate (any dealer ask inside our ladder, a cheap board ask)
  on the same tick as a duel accept. On the deadline tick the duel's `reserve_accept` returns False → no deal → 0.
- Fix options: (a) the duel loop books the slot BEFORE asking Jev whenever any duel's default move is accept
  (release it if Jev says counter); (b) the taker skips accepts on ticks where a live duel is within
  `duel_endgame_ticks` of its deadline (it can read `/api/duels` or a ledger "intent" row); (c) grace ≥ 4 s
  while duels are live. (a) is the cleanest. Owner: duel loop → w2b (v2's accept planner), taker side → B15.
- 03:28 update: w2b fixed it for v2 in #86 @ 4417c54 (the planner's accept is booked BEFORE DuelJev.pick;
  `tests/test_jev_journal.py::test_under_v2_the_planners_accept_books_the_slot_before_jev_is_asked` passes, re-run by
  r2). v2 also recovers a duel's start from the payload's earliest message after a restart (X16 for duels).
  Still open with `duel_policy = v1` (the default) → B15. My invariant test stays xfail: it checks the v1/taker
  timing, not v2's ordering.

### X16 — merging to main redeploys the live agents in the middle of play (CONFIRMED by config, high)
- `.railway/railway.py`: `bazaar-duels` (`duel run --play`, always live), `bazaar-taker` and `bazaar-maker`
  (BAZAAR_LIVE=1 since 01:45) build from `main` with `watchPatterns` `src/**`, `GUARDRAILS.md`, `STRATEGY.md`,
  `RUNTIME.md`, `uv.lock`, ... Every PR Marius merges in the morning (#60–#72 and the night PRs all touch `src/**`)
  restarts all three. Each service has a volume, so the old deployment stops before the new one runs (no
  overlap; unverified on our project): roughly build + start of no moves per merge.
- What a restart loses (in memory only): the duel loop's `first_seen` (`cli.py` duel `on_tick`: progress restarts
  at 0 → the next offer jumps back to the anchor, being tested as B6 by the duels investigator); the taker's
  `convs` (open dealer threads orphaned, X3); the maker's Jev watch. `PAUSE` and the JSONL live on the volume
  and survive.
- Schedule: Duels I runs from h6.5 for about 12 waves × 16 ticks ≈ 96 min (34 duels, 3 at a time); Duels II
  from h13 for about 96 min (68 duels, 6 at a time). A merge in those windows costs moves, or a whole duel at its
  deadline.
- Decision for Marius: merge everything BEFORE 09:00, or only between duel sessions; never merge a docs-only
  change that touches a watched path during a duel wave. #62 first (X11).

### X11 — two ledgers / a dead PG connection (KNOWN, fixed by #62, high for duels)
- #62's own audit (body): on main a dropped Postgres connection is never reopened; in `duel run --play` the
  `LedgerUnavailable` escapes `on_tick` and the duel player **silently stops moving** for the rest of the
  process (Railway never restarts it). Taker/maker skip every tick. `open_ledger` falls back to JSONL once
  and never returns to Postgres.
- The real server enforces 1 accept/tick, so a split ledger costs a refused request, not a second accept. The
  real damage is per-process spend caps (taker 150 + CLI 150) and the dead duel player.
- Decision: merge #62 before 09:00 (it is a day PR, base main). Nothing new to test here.

### X15 — expired maker bids are counted again on every repost (CONFIRMED, high)
- Mechanism: `seller.post` / `Maker._post` book a bid's cash as `spend` when posted (`agents/maker.py:335-336` on main).
  Only `_cancel` books the refund. A bid that lapses at `expires_in_ticks` (`MakerConfig.offer_ttl_ticks = 40`) just
  disappears from `/api/me/offers`; `plan_offers` sees the target uncovered and reposts → a second spend row.
- Evidence: `tests/bites/test_maker_expired_bid_spend.py` @ b22b08a (strict xfail). Sunday 15 s ticks, one LAV-09
  target at 65: after 2 TTLs the hour shows **130 P spent with one 65 P bid open**; the 3rd repost is refused
  (`denied: spend 130 + 65 > max_spend_per_game_hour 150`); 2 of 5 reposts go out in 50 min. Same failure on the
  #72 tip (fix/cash-spend-accounting, which adds dated refunds for cancels but not for expiries).
- Saturday (30 s): 40 ticks = 20 min → each standing bid counts 3× per hour; Sunday 6×. With ≥ 2 bid targets
  (~50 P each) the cap is full within ~40 min and the TAKER's board accepts and dealer bids are refused too
  (the cap is team-wide, shared ledger). The maker and taker are live on Railway from 09:00 (docs/services.md).
- Integrated evidence (chaos harness, `tests/bites/chaos.py` @ 7d88b0f: real taker + maker LIVE over HTTP against
  the in-process sim, 30 s ticks, 240 ticks, seed 7, start cash 900): the ledger booked 296 P while the sim shows we
  really paid 226 P. The phantom 70 P is the maker's LAT-10 bid of tick 0 that expired unfilled. In game hour 1 we
  really paid 95 P, but the ledger said 165 → 326 buy attempts denied "spend > max_spend_per_game_hour" for the
  rest of the hour (55 P of real budget lost). Restarts every 10 ticks (23 restarts) changed nothing material in the
  sim (paid 225; cash floor and real hourly spend never breached: min cash 754, worst real hour 131 P).
- Fix options: (a) when a bid of ours leaves `/api/me/offers` without a fill, book its refund dated at its spend
  (`refund_row` from #72, using `created_tick`); needs the maker to remember its own bid ids; or (b) count bids as
  spend only at settlement and add open bids to `committed_context` (#72 already does this for thread bids:
  `thread_cash`); or (c) cheapest: `offer_ttl_ticks` ≥ 120 on Saturday / 240 on Sunday (one bid ≈ one hour), a
  param change, not a guardrail change. Decision for Marius: (c) as a stopgap at 09:00, (a)/(b) as the fix.

### X4 — the 09:00 opening is handled 0–300 s late (CONFIRMED, medium)
- `ticks.seconds_until_next_tick` returns `CLOSED_POLL_MAX_S = 300` while `doors != "open"`; `next_opens` is only
  logged (`agents/runtime.py:322`). Every loop (taker, maker, duels, monitor, capture, evals) uses `run_per_tick`.
- Evidence: `tests/bites/test_doors_open_wakeup.py` @ b22b08a: a clock 20 s before opening sleeps 300 s.
- Impact: 0–10 Saturday ticks (mean 5) lost at the opening; 0–20 on Sunday (15 s). Not money-losing, but the
  09:00 ladder (W3), the 09:00 trade plan (W4) and dealer allotments per hour start late.
- Fix: sleep `min(300, max(1, next_opens − now))` (or `next_tick_in` if the closed clock carries it).
  Ops workaround: redeploy/restart the Railway services at ~08:58 so the first poll lands after 09:00
  (a restart before 09:00 still polls at +300 s — restart AFTER 09:00:00, or apply the fix).

### X1 — ledger across the day boundary (DISPROVED for now, low)
- Shared Postgres (read-only): `ledger` table is EMPTY (no Friday spend/accepts), so nothing carries into
  Saturday's first hour. Friday's feed ends at tick 159, `t` 2.65; the schedule puts `day_opens` at h4.0, so
  either the server jumps `t_hours` to 4.0 or it continues from 2.65 (then every Saturday schedule event shifts
  by +1.35 h of wall time — check `/api/schedule` at 09:00; matters to B6's playbook, not to the guards).
- Ticks: the sim never resets ticks (`world.advance`); if the real server ever reset tick numbers, the unique
  `(tick, slot)` accept index would refuse accepts on reused ticks — only after rows exist for those ticks.
