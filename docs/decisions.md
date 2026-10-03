# Decision log: Team 1 (t01), The Bazaar

The decision log issue #16 asks for. Every strategy or architecture decision, in order, with its date, the decision,
why we made it, the evidence, the outcome and a link. Times are Madrid (Europe/Madrid, UTC+2). GitHub timestamps,
which are in UTC, have been converted. **As of Sat 3 Oct, ~06:30.**

**Status:** `decided` = merged, or stated by Marius or the team · `proposed` = a draft PR or report that is waiting
for Marius · `rejected` = considered and turned down · `killed` = dropped on purpose, with the reason.
**Decider:** Marius, Omar, Jev (our typed decision model, with its confidence), or a night session (an autonomous
Claude session working under the 02:30 rules).

Private values are left out on purpose: set affinities, card values, duel limits, per-rarity price caps and the
album cards we are missing. Cash totals, public scores and aggregates are kept.

---

## Friday 2 October: kickoff and the learning round (weight 0.5)

- **18:50 · Treat the pitch as 40 % of the score, and keep a decision log from day one** · `decided` · Marius
  Why: in the kickoff, "that's the 40 % for us". RULES.md then gave the split: Negotiating 30 + Market 30 + Judges 40 ("your ideas and your craft").
  Evidence: kickoff transcript (judging quote at [11:02], whisper), RULES.md in the kit. Outcome: #16 and the #17 tracker opened at 19:52; this file is the log.
  Link: #6, #16, #17.

- **19:58 · Build on the organisers' kit (Python SDK vendored byte for byte); Python-only stack** · `decided` · Marius
  Why: the kit ships the SDK, a starter agent, a starter broker and RULES.md, and the SDK already handles `wait_for_tick` / `rate_limited`. RULES.md overrides the transcript.
  Evidence: `diff -r` against the zip, SHA-256 in VENDORED.md. Link: #18 (merged 20:17), #2, #28 (stack locked).

- **20:10 · Jev answers typed questions before the agent acts; a verdict never authorises a trade** · `decided` · Omar
  Why: we want questions with probability floats and fixed bars (0.75 design, 0.6 passive); `undecided` falls back to the deterministic move.
  Outcome: ported to Python (#29). Jev later chose Phoenix (0.90), the eval method (0.91), Postgres-only memory (1.0), the venue's `open_noon` (0.82) and closing #43 (1.0).
  Link: #19.

- **20:20 · Read the public feed as an order book and capture all of it** · `decided` · Marius
  Why: `GET /api/feed` needs no key and shows every team's structured offers to dealers, the dealers' text, and settlements with asset ids. The server keeps only the last 500 events, with no cursor.
  Evidence: at tick 0, other teams had already opened 14 Abuela threads; her welcome pack sold at 17 P against a list price of 26. Link: #21, #29, #32 (monitor, 21:38).

- **21:24 · Architecture: collector → intel → Postgres (+pgvector) memory → decider (Jev) → fresh executor; tick discipline is a hard rule** · `decided` · Marius
  Why: the clock pauses, and the doors close from 23:00 to 09:00, so every loop follows `/api/clock`. A late decision is dropped, never sent late.
  Link: #28 (spec, phased plan, team contract), #29.

- **21:29 · Deterministic code sets every binding field; an LLM only writes words** · `decided` · Marius
  Why: price, days, accept and give/want come from pure functions such as `dealer.decide()`. Counterparty text is data, never instructions. A duel closed outside the limit subtracts points.
  Evidence: offer terms are checked before every accept, and the clock is re-read before every write (#31). 0 injection attempts in 3,436 public Friday events.
  Link: #24 (design rules), #29, #31, #39, #57.

- **21:29 · GUARDRAILS.md: one human-edited rule book, parsed fail-fast, checked before every write; the kill switch is a PAUSE file** · `decided` · Marius
  Why: the limits sit in one reviewable file. An unknown rule id or a bad value makes the runtime refuse to trade.
  Evidence: the initial rules are cash floor 270 (it keeps the venue bond), 150 P per game hour, 1 accept per tick for the whole team, never buy a card we hold, never sell below `your_value`.
  Link: #30. The kill switch's policy is HOLD (no cancels, closes or walks): #68, open.

- **22:03 · Observability on a self-hosted Arize Phoenix; tracing is off by default and can never break a trade** · `decided` · Jev (0.90) / Omar
  Why: teammates on different laptops watch every negotiation live, and Phoenix's hosted cloud is gone.
  Evidence: a bounded span queue and a 3 s export timeout; a test shows that an observer that raises leaves the deal unchanged. Link: #34, #35. ADR 0001 (#46) is still `proposed`.

- **22:31 · Runtime LLM layer: Jev picks the model for each move (ask, words, steer); runs on the Claude subscription** · `decided` · Omar
  Why: "an LLM never sets a price and never trades", and every LLM failure falls back to the code path. Sat 00:16: Claude Agent SDK, no API key (#51).
  Sat 01:10: a desk orchestrator with 4 subagents, each with its own tool allow-list. A PreToolUse hook runs `guardrails.check()`, and bazaar-mcp serves the tools to teammates (#59).
  Link: #39, #51, #59.

- **22:40 · Fix the duel player to read the real payload** · `decided` · Marius
  Why: the player read `id`/`deadline` instead of `duel`/`deadline_tick`, so it skipped every practice duel.
  Evidence: 12 practice duels ended without a single offer from us. Link: #41 (b0f630c).

- **22:42 → Sat 03:07 · Always-on runtime on Railway as code; the monitor runs on a laptop; no service is ever declared OFF** · `decided` · Omar / team
  Why: IaC after Railpack found no start command (#42). Team decision at 23:00: the monitor runs in the CLI on a laptop (#45). A Railway apply then revived the OFF monitor from an old image, and it held one of the key's 6 stream slots (#73).
  Link: #42, #44, #45, #73.

- **23:45 · Unattended loops survive network errors (exponential backoff, one report per failed tick)** · `decided` · Marius
  Why: a DNS failure (a laptop changing networks) killed the monitor at Friday's close. Link: #47.

- **23:57 · Autonomous taker and maker, dry run by default, on a shared Postgres ledger: one accept per tick for the whole team, duels first** · `decided` · Omar
  Why: one key means one accept per tick across every process and machine. The taker waits a 2 s grace and steps back when a duel accept is booked.
  Link: #48.

## Saturday 3 October, 00:00–02:30: before the night shift

- **00:54 · Jev picks duel moves and maker prices only among the legal candidates the code builds** · `decided` · Omar
  Why: Jev chooses inside the guardrails and our own limit; `undecided` (below the bar, no key, timeout, no tick budget) keeps today's move. Link: #57.

- **01:14 · Audit of every issue against main, the shared DB and the monitor captures** · `decided` · Marius (#2 closed by Marius 01:33, #23 by Omar 01:34)
  Evidence: at tick 149 our score was 8.34 and falling with no activity of ours (9.24 → 8.73 → 8.34), rank 15. L2 came at tick 98 ("4 deals with abuela"). Only `ladder_points` was nonzero (0.058); market was 0 while 4 rival venues were open. The shared `ledger` table had 0 rows despite 4 dealer buys (47 P) and at least 4 duel accepts.
  Link: audit comments on #2–#24.

- **01:12–01:57 · Live fixes: duel offers strictly inside the limit (#60), never close at the dealer's opening ask (#61), a shared ledger that reconnects and is required for live writes (#62), kill switch HOLD (#68), cash and spend accounting (#72)** · `proposed` · Marius (a coordinator took them over while he was offline)
  Why: the ladder was our only scoring component and scores our share of the dealer's range, yet our 4 Abuela deals closed at or near her opening ask. Main's v1 two-issue counter can land outside our limit, and the ledger was never actually shared.
  Outcome: Jev's triage was "fix then merge #72, #62, #61; #60 through the gate; #68 closes into #72". #72 now bundles #61 + #68. r1: OK (#72 has one medium; #62 needs a patch if #79 also merges). All still open at 04:30.

- **01:29 → 03:07 · Evals are online outcomes in Postgres plus Phoenix annotations, run inside the agents rather than as a service** · `decided` (#58) / `proposed` (#91) · Jev (0.91 / 0.78) → Omar
  Evidence: Friday's practice duel mean 0.279 (20 scored), dealer mean 0.464, ladder L1 0.733 / L2 0. The `bazaar-evals` service was deleted (#73).
  Outcome: r1 says #91 must default to off in the live services. Link: #53, #58, #66, #91.

- **01:45 · Go live: `BAZAAR_LIVE=1` on the Railway taker and maker, trading from the 09:00 opening** · `decided` (set by hand; the sources do not say who decided)
  Evidence: both `/health` endpoints report `mode: live`; bazaar-duels runs `duel run --play` on main's v1. To stop: delete the variable, or PAUSE.
  Link: #70, #73.

- **02:08 · Public `/state` and `/events` publish an allow-listed view only (no values, limits or reasons)** · `decided` · Marius (#69) / Omar (#121, merged 04:30)
  Why: the services are public (CORS `*`, no auth), and decision rows exposed our card values, max prices, guardrail cash and reasons that spell out our multiplier. A rival could list at our max − 1.
  Link: #69, #121.

- **02:06 → 03:03 · A simulated Bazaar (bazaar-sim) to test every agent while the doors are closed; the sim smoke is the merge gate** · `decided` · Omar
  Why: the game is closed overnight. CI drives our real CLI and agents against a local sim, and a dead proxy blocks every other host. Link: #55, #75.

- **02:16 → 03:24 · Our own PR review gate replaces Greptile, and it enforces the pipeline artifacts (spec, plan, honest report)** · `decided` · Omar
  Why: Greptile hit its trial credit limit, and workers were skipping `.ai/pipeline.md`. Link: #76, #99.

- **02:34 → 02:54 · The repo stays Python-only and Claude-only; UIs live in their own repo; Bazaar Live is a separate show** · `decided` · Jev (1.0, PR triage) / Omar
  Why: the #43 dashboard brought a Next.js app, 9 open P1s and conflicts. The voiced show reads only the agents' public `/state`. Link: #43 (closed), #50, #83, #85, #104.

- **02:57 · Build freeze Sunday 06:00, deadline Sunday 14:00** · `decided` · Omar. Link: #82, #88.

- **03:02 · Learner / auto-evolve is P0; memory is Postgres only (Jev 1.0: no graph DB)** · `decided` (plan, #90, #95) / `proposed` (code: #89, #96, #111, #112) · Omar
  Outcome: r1 says the defaults must be off. `BAZAAR_LEARN` is on by default, so the live taker would start skipping dealers, and #96 adds about 350 MB of RAM to the live taker.

## Saturday 3 October, night shift (02:30–05:00, about 15 builder sessions plus a reviewer, a bite hunter and an orchestrator)

- **02:30 · Rules of the night** · `decided` · Marius
  Open PRs stay unmerged, and night work stacks on their branches. Each workstream runs in its own session and ends as a DRAFT PR plus a report. Nothing goes live. No venue opening and no `cash_floor` change tonight. (Held until ~05:00; see "After 05:00" for the first merges.) Duel policy v2 ships only behind a parameter whose default is today's policy.
  Scope: these rules bind the night sessions; day-side PRs (the coordinator's and Omar's) kept merging (#73, #75, #85, #88, #90, #95, #99, #104, #121). Link: `_night/PLAN.md`.

- **02:30 · Killed: dealer prompt injection, mirror-duel learning, eggs, fee tuning, polishing the LLM's words; flags stay off** · `killed` · Marius / orchestrator
  Why: words never move a dealer's price, duel aliases are unstable, eggs never score, two rival venues already charge 0 bps, and none of these scores.

- **02:30 · The duel decay fact behind the duel work** · `decided` (verified finding) · orchestrator
  `result = |price − limit| × 0.94^rounds`, where `rounds = min(our priced messages, the rival's priced messages)`. Every priced counter costs 6 % of the pie (8 % in Duels II, 10 % on Sunday).
  Evidence: exact on all 8 real practice deals. v1 counters every tick (7–8 rounds per deal, ~30 % of surplus lost). On the 12 unanswered duels, accepting the rival's best would have made ~195 P (16.3 per duel), against 176.9 P on the 14 we played (12.6 per duel).

- **02:20 → 03:08 · Duel policy v2, "silence is free": anchor once, hold while the rival concedes, at most 2 priced offers, decay-aware accept, always strictly inside the limit** · `proposed` · night w2b (w2a built the harness)
  Evidence: W2a gate 5/5. v2 makes 1.42× v1 at decay 0.06/0.08 and 1.55× at 0.08/0.10. One-clock replay of the real duels: 178.4 vs 121.7 P. With the shared accept slot (6 duels): 1.33×. 0 outside-limit closes on 14,400 × 6 arena duels, and 0 guardrail refusals on 108,702 fuzzed planner moves (r1).
  B7 (#130): on real payloads we price first in 55 % of shared ticks; under that mix v2 still makes 1.38–1.51×. Link: #86, #80, #130.

- **03:12 · Endgame exploitability: a rival that infers our limit can squeeze us in the last tick → B11 mitigations** · `decided` (the study, Marius) / `proposed` (the settings) · night w2a + w2b
  Evidence: by tick 3, v1 reveals its limit to within 6 % in 96 % of duels. With B11's endgame settings (in #150), an exploiter's pie share goes 0.19 → 0.30 (W2a, 3 seeds); against honest rivals the result stays ≥ 0.996× and the deal rate ≥ 0.975×, with 0 outside the limit. We do worse against an oracle that never backs off (0.150 → 0.134).
  Link: #97, #103.

- **02:30 → 04:17 · Two-issue days: keep #60's worst-case weight until a real Duels II payload confirms the sign; `duel_days_auto` stays OFF** · `proposed` · orchestrator (PLAN fact 3), night w2a (B8), r1
  Why: the text that gives the sign of days came from our own simulator. Real practice payloads have `days_meaning: null`.
  Evidence: with the right sign, v2 gains 3–10 % per two-issue duel. Flipped on without evidence, 7–11 % of duels close outside our limit. r1 found 3 HIGH issues in #113's latch; all were fixed and r1 closed the review at 04:30. Link: #60, #113, #117.

- **02:37 → 03:54 · Market Test: our broker's edge over the free stall is small, so being open matters more than the matching margin** · `proposed` · night w1a, w1b, b1, b2
  Evidence: in the bench, even the oracle beats the stall by only +0.03–0.06 (p50), so the plan's "stall + 0.15" bar is a no-go. #71's keeper as shipped (exact matching) scores the same as the stall; with the edge policy, +0.17. A venue at 09:00 vs stall only: +0.19 final points (break-even at ~6 % broker downtime).
  Organic market-making is a no-go as a points source: 0 of 739 public offers sat on team venues. Link: #77, #84, #94, #92.

- **03:14–03:23 · #71 rewritten from build-only to live: the maker opens a 0 bps board venue at game hour 6.5; `cash_floor` 270 → 100 plus a 270 P reserve** · `proposed` · Omar, Jev `open_noon` (0.82)
  Why: market is 30 of the 100 points, and ours is 0.
  Outcome: this contradicts the 02:30 decision. r1: DO NOT MERGE AS IS. The key-vault lockout HIGH was fixed at head 1696789 (04:35); still open: the process blocker, and two mediums (a failed mark can open a second venue; a failed key save is never retried). The original 03:46 HIGH (a starter stall in `/me` drops the floor to 100 and the reserve gets spent) is in REVIEWS.md. If the clock resumes, h6.5 = 12:51, the same tick Duels I starts. Link: #71.

- **04:07 · One venue proposal (B20): open at 09:03 (h4.05) as insurance; a board venue with the edge broker only if the maker logs it at 09:00, otherwise auto; `cash_floor` 50 optional** · `proposed` · night b20, endorsed by b2
  Why: the free starter stall should already earn 0.5 per session (unverified until `/me` after h5). Under floor 270, a venue needs 540 P; we hold 353 + a 150 P grant. Link: #118, #92.

- **03:32 · Saturday's clock: Friday froze at tick 159 = h2.65, so if the clock resumes, 09:00 is h2.65, not h4, and every event moves +1 h 21** · `proposed` (gate G0 at 08:55) · night b6
  Evidence: `feed_events` `received_at`, and the openapi Clock (`t_hours` = game hours since opening). Under resume, Duels I is at 12:51, cash at 09:00 is 353, and the headroom is 83 P until the grant. This conflicts with the roadmap's h4 = 09:00 (74eb164).
  Link: #102 (playbook, `bazaar timeline`), #122 (`bazaar cockpit`, a read-only operator screen).

- **02:23 → 04:14 · Ladder: bid from a floor table per dealer × rarity, with seeded jitter available; Chato and packs are a no-go under today's caps** · `proposed` · night w3, b12 (Marius asked for unpredictable bids), b21
  Evidence: Abuela's uncommon share is 0.945 (model) / 0.973 (real replay), against 0.84 / 0.80 today, with 0 repeated prices. The L2 unlock takes 3 negotiated buys from the previous dealer (opening-price deals don't count; whether sales count is unverified: r1 04:30, B25 04:33). Jitter keeps ≥ 0.95× of the share and cuts rivals' exact-hit rate 1.00 → 0.72 (off by default).
  Link: #81, #100, #119. Open: relaxing Chato's uncommon cap (`dealer_price_caps`).

- **02:13 → 03:53 · Trade desk: a per-counterparty cap (≤ 25 % of planned volume, "never feed another team") and a rival affinity map from the feed** · `proposed` · night w4, b4
  Evidence: the affinity map's log loss is 1.14, against 1.39 for a uniform guess. The 09:00 plan was revised down to +24.4 P (model) / +1.5 P at Friday's fill rates, so it is marginal. In Friday's replay, the 2 big snipes were taken by rivals within 3–5 ticks.
  Link: #79, #98, #127 (team-thread negotiator: no-go live until one manual probe).

- **02:20 · Page economics: completing pages does not score by itself; the cash plan** · `decided` (the scope, Marius) / `proposed` (the findings) · night w7, b9
  Evidence: under floor 270, a venue is refused at 09:03 (it needs 540 P). No page can be completed under today's rare cap. A pack earns 12–37× fewer round points per P than Abuela's best three, so the verdict is to buy no packs and open the free ones.
  Link: #87, #109.

- **02:30 + 03:35 · Cross-venue arbitrage and guarded duplicate buys behind switches that default to off** · `decided` (the scope, Marius) / `proposed` (verdict: keep both off) · night w8, b23
  Evidence: on Friday all 636 public offers were on El Rastro, with 0 crossings net of fees and 0 positive-surplus duplicate asks. A read-only scan is a GO. The alert replay found 156 buy opportunities (median life 10 ticks) and 0 arbitrage or duplicate ones.
  Link: #101, #125.

- **02:20 → 03:42 · Score model, red team and request budget** · `proposed` · night w5w6, ops
  Evidence: the model gives 8.26 in sample and 7.87 on holdout, against the official 8.34. Our 10.76 → 8.34 drop was other teams' Chato deals raising the top-3 mean (~2.2 points). 168 hostile-text cases changed 0 binding fields. Sunday's ceiling is 4.73 req/s against a limit of 5: a conditional GO with the opt-in stagger; a maker cancel cap brings it to 3.40.
  Link: #78, #128.

- **03:18 → 04:28 · Bite hunt → hardening fixes and a merge-freeze rule** · `proposed` · night r2 + fix sessions
  Evidence: X3 restart orphans (unbooked deals 26/17/1 → 0, #114), X4 09:00 wake up to 300 s late (→ 0.3 s, #106), X7 same-tick floor/cap breach (fixed by #72), X15 expired-bid phantom spend (#126), X17 taker takes a duel's deadline accept (#115), X20 a 429 keeps the accept slot (#116), X21 nothing opens packs.
  X16: every merge redeploys the live services, so merge only before 09:00 or between duel sessions. Link: #107, #106, #110, #114, #115, #116, #126.

- **03:46 · Keep `allow_flags` off; the trickster inspector stays ready** · `proposed` · night b3
  Evidence: 0 flags on Friday's 1,022 honest dealer offers, but r1 found honest phrasings that still get flagged, so precision is unproven. Link: #93.

- **04:16 · Dress rehearsal and merge order** · `proposed` · night b5, r1
  Evidence: all 12 PRs integrated at frozen heads, with 10 cross-PR fix-ups; 2,746 tests green. Merge #72 with a merge commit (it bundles #61 + #68). Squash-merge #79, #98 and #101, because earlier commits held private numbers.
  Link: #120 (DO NOT MERGE), `docs/night/r1-reviewer.md`.

- **04:28 · Sunday readiness: no agent crashes when a set is released mid-game; a guarded switch for dealer buys of a brand-new set** · `proposed` · night b26
  Evidence: the catalog already lists RET and CHA (`released: false`), and unknown refs fail closed. Dealers mint what they sell, so `dealer_mints_unminted` (default false) should go on before CHA. Link: #129.

---

## After 05:00: first merges and the PR triage

- **05:07 and 05:51 · #106 (wake at the announced opening, bite X4) and #105 (holdings and catalog in Postgres) merged** · `decided` · Omar
  Each merge redeployed the live taker, maker and duels (X16). #106 makes the 09:00 restart step unnecessary. #105 was merged with r1's default-on blocker still open in its last review (REVIEWS.md); check its default on the live services. Link: #106, #105.
- **05:40–05:46 · Coordinator triage: night PRs closed and replaced by takeover PRs** · `decided` · coordinator session
  The night drafts above link to their original PRs; where to find each one now:

  | Closed | Now in |
  |---|---|
  | #60, #86, #103, #113, #115, #117, #130 (duels) | #150 |
  | #80, #97 (duel zoo, exploiters) | #151 |
  | #79 (trade desk) | #137 |
  | #98 (rival scanner) | #138 |
  | #114, #116, #126, #133, #110 (bite fixes) | #140, #141, #142, #143, #144 |
  | #81, #87, #92, #94, #100, #109, #119, #122, #125, #127, #129 | closed; reports salvaged in #154 |
  | #101 (arbitrage) | closed 04:40, no takeover |

  Still open as they were: #61, #62, #68, #71, #72, #78, #102. Link: #150, #151, #154.

## Open decisions for Saturday morning

1. **08:55, G0 clock column.** Resume (09:00 = h2.65, Duels I 12:51) or jump (h4, Duels I 11:30). Check with `bazaar clock` and `bazaar timeline --from-api --compare`, then on the feed 09:01–09:05 (#102).
2. **Merges before 09:00** (each one redeploys taker, maker and duels):
   - #72 with a merge commit, then close #61 and #68 (fixes X7: a same-tick breach of the floor and the cap);
   - #62, plus r1's patch if #79 also lands;
   - #60 before Duels II at the latest, and before Duels I if v2 goes in.
3. **Duels I policy.** Either v1, plus #115 so its forced accepts go before Jev, or v2 (#86 → #103 → #130, B27 consolidation) with B11's endgame settings (#150, the takeover of #60/#86/#103/#113/#115/#130). `duel_days_auto` stays off until a real Duels II payload shows the sign.
4. **Venue.**
   - Options: #71 as is (r1: do not merge as is), B20's 09:03 opening (auto unless the edge broker is live), or no venue.
   - With it, `cash_floor` (270 / 100 / 50). Whatever the choice, one broker per venue: no laptop broker next to the Railway keeper.
5. **Under resume, the 83 P before the grant.** Ladder best three first (they count for Friday's round too) or the venue. Not both.
6. **G5, Chato.** Relax his uncommon cap and set `ladder_level_deals` (#81, #119): +2.82 round points, and probably an early L3.
7. **Omar's PRs whose new behaviour is on by default.** #89 and #96 (`BAZAAR_LEARN`), #91 (evals), and #111 (LLM feed reader): turn the default off before any merge (r1). #105 (holdings from the DB) was merged at 05:51 with that blocker still open in r1's last review: check its default on the live services.
8. **Hand trades against the live maker** (X19: it cancels any board offer it did not post). Send thread swaps only, or put the maker in dry run while hand offers stand, but never around a Market Test if the maker runs our broker.
9. **Packs.** Nothing opens them, so open them by hand (`open_pack`). B9 says buy none: turn off the taker's pack gate?
10. **Expired-bid phantom spend (X15).** Merge #126 in a window, or watch the taker's `max_spend_per_game_hour` refusals.
11. **Switches to decide on Saturday's data** (all default off): dealer jitter (#100), taker `--accept-bids` (#98), arbitrage and duplicate buys (#101, after the #125 alerts), the team-thread negotiator (#127, after one manual probe), the tick stagger (#78, #128). `allow_flags` stays off.
12. **09:00 read-only probes** (BITES P1–P8). Do duel accepts count against `accepts_per_team_per_tick`? Which fee settles when a change is effective at T+1? Is the free stall scoring 0.5 for us (`/me` after h5)?
13. **Saturday evening, for Sunday** (#129):
    - v2 + B11 for Duels III and the Final;
    - `dealer_mints_unminted` on before CHA;
    - the stagger on at 15 s ticks;
    - never close the venue;
    - `cash_floor` toward 0 late Sunday (cash never scores);
    - the ladder's best three before Abuela closes;
    - no merges inside Sunday's windows.

---

## Sources
Merged and open PRs #6–#130 and issues #2–#24 with their comments (`gh`); RULES.md (`vendor/bazaar-kit/RULES.md`);
`docs/transcripts/2026-10-02-hackathon-kickoff.md`; `.ai/memory.md` and `docs/architecture.status.json` on main;
night files `_night/PLAN.md`, `STATUS.md`, `REVIEWS.md`, `BITES.md` and `BACKLOG.md`; and the night reports on the
`night/*` branches (`docs/night/*.md`). Gaps: who authorised the 01:45 go-live is not recorded, and the exact time of
Marius's B11 and B12 requests differs between STATUS.md (03:12 / 03:15) and BACKLOG.md (03:25 / 03:40).
