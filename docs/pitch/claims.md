# Claims ledger (P1): what we may say on stage, and with which label

Draft as of Sat 3 Oct 2026, about 06:00 Madrid; updated about 10:30 Madrid with the first Saturday ticks (159–163, captured
09:30 Madrid in `.local/evidence/20261003T073058Z-first-ticks/`, gitignored) and the PRs merged up to then. Every sentence in
`outline.md` and `script.md` carries a claim id (`[C7]`) from this file. If a claim is not here, it is not said. Re-check every row on
Sunday morning.

**Rules sync, Sat 3 Oct evening:** rows C1, C27, C35, C65, C69 and C70 were brought in line with `docs/briefing.md`,
`GUARDRAILS.md` and `RUNTIME.md` (our venue is open, team threads are on, the cash floor was lowered during Saturday,
the LLM-words switch is off). Every other row is still as of Saturday 10:30 and needs the Sunday re-check.

"Merged" below means merged on `main`; a merge redeploys the live services, but no row says "running live" unless live evidence is
named next to it.

## Tags

| Tag | Meaning | What we may say |
|---|---|---|
| **REAL** | Measured or observed in the real game (or on real recorded data) and reproducible from a named source | Say it plainly, with its source |
| **SIMULATED** | Unit test, fixture, simulator, modelled rivals, or an offline replay of a policy | Always say "in simulation" / "in a replay"; never as a tournament result |
| **PENDING** | Depends on a PR not merged yet, or on data that does not exist yet (Saturday/Sunday) | Say it only after the row is changed to REAL or SIMULATED |
| **UNVERIFIED** | Reported by someone, not reproduced by us | Do not say it, or say "reported by" with the source |

Hard rules for the speaker:
1. Jev's float is its stated confidence. It is **not** a hit rate. Never say "Jev is 96% accurate".
2. A simulator number is never a tournament number. The simulator's v1 baseline (0.27) and the real Friday duel mean (0.279) are
   different measurements that happen to look alike. Never say "the simulator reproduces Friday".
3. "Undecided" is never a "yes". When Jev did not clear its bar, we say it did not decide.
4. We have **not** been attacked for real. We do not say we stopped a real trickster unless a PENDING row below turns REAL.
5. No private value is shown or said: no affinities, card values, duel limits, price caps, cash floor, or the endgame details of
   the duel policy.

## A. Story and architecture

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C1 | Prices, accepts, limits and quantities are computed by code inside `GUARDRAILS.md`; the LLM may write words only (`llm_words` is false in `RUNTIME.md`, so live dealer and duel messages are templates). Every write passes `guardrails.check()` | REAL | `src/bazaar_agent/guardrails.py`, `GUARDRAILS.md`; `.ai/context.md` Hard rules; merged runtime hook #59 |
| C2 | A PreToolUse hook denies a guardrail violation before any write, and fails closed if it cannot read live state. It guards the LLM desk's tool calls, not the deterministic taker/maker (those call `guardrails.check()` directly). Merged on main; not shown deployed | REAL (merged) · test is SIMULATED | `src/bazaar_agent/runtime/hooks.py`; `tests/test_runtime_hooks.py:51`, `:109`, `:184` |
| C3 | One accept per tick and the spend cap hold across processes because they share one Postgres ledger. Live, Saturday ticks 159–163: the shared ledger held exactly one accept per tick across three writers (taker, maker, duels), no tick with two accepts reserved, no duplicate write to a thread, no execution error code | REAL (live, ticks 159–163, #162 merged 04:57 UTC); the 20→11 `/me` reads figure is SIMULATED | `src/bazaar_agent/ledger_pg.py`; coordinator's tick check `verify-ticks.txt` in the first-ticks evidence folder; `.ai/memory.md` 2026-10-03 holdings entries (N13) and the #156/#162 entry |
| C4 | Every Jev call is logged, masked, with its float, the bar it had to clear, the margin and the model; below the bar the verdict is `undecided` and code keeps today's behaviour | REAL | `src/bazaar_agent/jev/log.py`, `questions/*.json`, `.local/jev-decisions/` |
| C5 | Jev picks the Claude model per desk role from the shape of the request (Opus 0.99 for "buy under 90"; Haiku 0.90 for the duelist) and a cache makes a repeat free | SIMULATED (local simulator) | `docs/pitch-notes.md` N15; PR #108 merged |
| C6 | The rules were reverse-engineered from our own data: duel result = `abs(price − limit) × 0.94^rounds`, exact on 8 of 8 practice deals; `rounds = min(our priced messages, theirs)` on 26 of 26 payloads. 0.94 is the practice session's decay (0.06 per round); Duels II uses 0.08 and Duels III 0.10 | REAL | `docs/pitch/story.md` Act 2 (W2a #80, closed; numbers in `docs/night/w2a-duel-zoo-tables.md`) |
| C7 | Level 2 opened after 3 negotiated buys with the previous dealer; sales and deals at the opening price do not count | REAL | `docs/pitch/story.md` Act 2 (B21 #119, from every `level.unlocked` event) |
| C8 | The public feed is an order book: dealer text, real team ids and fill prices; capped at 500 events with no cursor, so we capture it every tick | REAL | `.ai/memory.md` 2026-10-02 entries; `bazaar feed capture` |

## B. Proof 1: a real deal and its settlement

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C10 | Friday, tick 55: our agent bought LAV-03 from Abuela at 7 P in 3 ticks (thread 99): bid 6, her ask 7, accepted. Run by the deterministic `bazaar dealer buy` runner, **before** the LLM words layer existed: no model wrote those messages | REAL | `.ai/memory.md` 2026-10-02 "first ladder deal"; `bazaar thread 99`; `tape` row; `dealer_curves` |
| C11 | Friday ladder: LAV-03 7, LAV-04 9, LAV-05 9, LAV-06 22 (4 negotiated deals). Official board at tick 159: negotiating score 8.34 | REAL | `.ai/memory.md`; `docs/services.md` evals example; official `/api/me` |
| C12 | A dealer's "Deal!" on our bid settles at the tick boundary; our own accept settles at the next tick | REAL | `.ai/memory.md` 2026-10-03 "Deal!" entries (13 of 13 and 27 of 27 reported) |
| C13 | **Saturday's first dealer deal, thread 316:** LAV-08 (Teatro Valle-Inclán) from Abuela. Tick 159 our bid 17; tick 160 her opening ask 29, our 18; tick 161 she came down to 25 ("Mira, te la dejo en 25 primas") and our taker accepted; settled at the tick-162 boundary at 25 P, fee 0. 25 is below her opening ask, so it is a negotiated buy for the level rule (C7). Our words came from the deterministic template (`src/bazaar_agent/agents/dealer.py:30-31`), **no LLM** and no bluff (it predates #131). Not a cheap fill: on Friday t09 paid her 21 for the same card (tape, tick 117) | REAL (thread, tape and ledger) · the guardrail `decisions` row and the `ladder_share` score are **PENDING** (the 09:30 evals report scored Friday only; the settlement event id is not in the capture, read it from the feed before the slide) | `bazaar thread 316`; `bazaar tape` (tick 162 row); `verify-ticks.txt` (tick 161 accept is the taker's); all in the first-ticks evidence folder |
| C14 | Saturday's first board buys, all accepts of listed asks on the `rastro` venue (no negotiation): MAL-02 at 3 + 2 fee (from t06, settled tick 160), SAL-05 at 9 + 2 (t15, tick 161), SAL-10 at 72 + 5 (t02, tick 163; 77 paid). With LAV-08 that is four settlements in four ticks, one accept per tick | REAL | `bazaar tape`; `verify-ticks.txt` (settlements with t01: 4) |
| C15 | Official board snapshot at tick 160: Team 1 rank 15 of 18, score 8.39 (negotiating 8.39, market 0), 5 deals; the leader had 29.94. Friday counts half. **Do not lead with it; say it if asked**, and never as "we are winning" | REAL (one snapshot; refreshes every 10 ticks) | `leaderboard.json` and `status.txt` in the first-ticks evidence folder |

Slide 3 uses C13 (Saturday, real). C10 stays as the backup line ("Friday"). Neither deal had model-written words.

## C. Proof 2: a deceptive offer stopped by our controls

No real deceptive offer has been seen: Friday had 0 injection attempts in 3,436 public events, and Level 4 Tricksters had not opened.
The offer inspector (#146) merged at 07:08 UTC (09:08 Madrid), and with `inspect_accepts = true` it gates every accept (dealer, board,
duel) of the build that carries it. Whether our four accepts at ticks 159–162 already ran on that build depends on when the redeploy
finished, which this update did not check. The Saturday capture shows **no** refused deceptive offer, so the stop on the slide is still a
crafted test. We say that on the slide. Proof 2 turns REAL only if a captured `decisions` row shows the inspector refusing a real
counterparty's offer.

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C20 | Friday had 0 injection attempts in 3,436 public events | REAL | `.ai/specs/P1-spec.md` audit; `docs/decisions.md` on #154 |
| C21 | The offer inspector refuses a lesser card slipped under a good card's name ("La Dama de Serrano, the legendary. Only 120", binding a common) and does not spend the accept slot | SIMULATED (crafted bait in a test) · code merged (#146, 07:08 UTC); no real refusal captured | `tests/test_accept_gate.py:31`, `:205`; `tests/test_inspector.py:28`; run `uv run pytest tests/test_accept_gate.py tests/test_inspector.py -v` |
| C22 | The same inspector flags 0 of 1,022 honest Friday dealer offers (Abuela 805, Chato 217). Caveat from `docs/decisions.md:179`: precision is unproven (other honest phrasings may flag), so this is "no false flags on Friday's offers", not "no false positives" | REAL data replayed through code (test merged with #146) | `tests/test_inspector.py:111` on main; the `bazaar flags precision --json` command is only on #152 (OPEN) |
| C23 | Hostile text (zero-width characters, Cyrillic homoglyphs, fullwidth digits, fake JSON, fake system notices) never changes a binding field; the tests found real bypasses and we fixed them | SIMULATED · **PENDING #152 merge** | `tests/test_hostile_text.py` (18 + 6 cases) |
| C24 | Red team: 168 hostile cases through every path that reads counterparty text, 0 binding fields changed. Planting a bug fails 34 of 48 selected cases, so the tests bite | SIMULATED · #78 closed; the tests are on main | `docs/night/w5w6-score-redteam-morning.md` §2 on `night/w5w6-score-redteam-morning`; `tests/test_redteam_injection.py`. **Do not say 129**: no source has it (older `story.md`/`qa.md` say 129) |
| C25 | A desk that obeys the injection and asks for an out-of-policy write (bid 900, buy 900, list at 1, extra accept) is denied by the hook before any write | SIMULATED (fake backend, not a live LLM) | `tests/test_redteam_injection.py:381` (branch #78); hook itself is REAL on main (C2) |
| C26 | The real Claude Code CLI enforced our deny hook in a dry run: it blocked an over-cap buy and `sell_bid 500`; nothing was sent | REAL, but our own requests, not a counterparty | `.ai/memory.md` 2026-10-03 "the real Claude Code CLI enforces our PreToolUse deny" |
| C27 | Bad-faith flags exist but are **off** (`allow_flags` = false), so nothing is sent. If switched on: at most `max_flags_sent` ever per data dir, only to a dealer in `flag_dealers` (Los Pícaros, set Sat 3 Oct after Jev's rules audit), never to Abuela or Chato (`flag_trusted_dealers`) | REAL (config: `allow_flags`, `max_flags_sent`, `flag_dealers`, `flag_trusted_dealers`) · flag decision rows as proof are **PENDING #152** (OPEN) | `GUARDRAILS.md` |

Not honest to say: "we stopped a real trickster", "we flagged a team", "we scored from flags", "injection-proof" (a live LLM obeying
hostile text is untested), "Jev said yes 0.83 to flags" (that was a hypothetical Level 4 input).

## D. Proof 3: a measured improvement against a baseline

Four different duel numbers exist. They are not interchangeable. The slide shows the baseline and the improvement from **one** row and
names its kind.

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C30 | **Baseline.** Friday practice duels, scored by our own evals: mean 0.279 over 20 scored duels (7 good, 5 ok, 8 bad). v1 countered every tick: about 6 to 7 rounds per deal (6.0 mean of the played deals, 6.85 in story.md; recount before quoting) | REAL (practice, our estimate; the real API shows no pie, so evals bound it with the rival's best offer) | `bazaar evals report`; `docs/services.md` "Evals scorecard"; P1-spec audit; story Act 2 |
| C31 | In simulation, our real client over HTTP against modelled rivals (1,552 finished duels per the d1 doc; the run and duel counts differ between `d1-sim-proof.md` and PR #150's body, so recount before quoting a count): mean score per duel v1 0.268 → v2 0.364 at decay 0.08 (zoo) and 0.278 → 0.401 at 0.10; 0 closes outside our limit | SIMULATED · the player is merged (#150, 04:50 UTC) and `docs/night/d1-sim-proof.md` is on main; the rival zoo is **PENDING #151** (OPEN). Ratio v2/v1 is 1.36× (0.08) and 1.44× (0.10): say "about 1.4× in simulation", not 1.5× | `docs/night/d1-sim-proof.md`; `scripts/duel_sim_proof.py run` then `table` |
| C32 | Offline tournament: 16,800 simulated duels per policy; 22.06 vs 14.35 P per duel; rounds per deal 1.23 vs 6.85; deal rate the same (0.834 vs 0.829). The gain is fewer rounds, not more deals | SIMULATED · in-sample (knobs tuned on the same zoo) · **PENDING #151** | `docs/night/w2a-duel-zoo-tables.md`; `uv run python scripts/duel_zoo.py` |
| C33 | Replay on the 12 Friday duels we never answered, with the rivals' real recorded messages: v2 178.4 P vs v1 121.7 P (ceiling 195), n = 12, one clock, one accept per tick | SIMULATED policy on REAL inputs · assumes a silent rival's offer stays acceptable (unverified on the real API) | `w2a-duel-zoo-tables.md` "12 unanswered practice duels"; `docs/pitch/charts/02b_duel_replay_12.csv` |
| C34 | A trivial rule "silently accept the best offer at the end" scores 21.93 vs v2's 22.06 in the zoo. v2 is not clearly better than that baseline there; it wins in the replay with the accept cap | SIMULATED | `w2a-duel-zoo-tables.md`. **Say it if asked**; hiding it is the failure mode |
| C35 | **v2 is live since Saturday about 10:00 Madrid, by a human decision.** Omar set `duel_policy = v2` during the live session after the simulator proof (#170, merged 08:12 UTC); Jev had answered undecided on the flag flips (C51), so Jev did **not** make this call. In #170's private simulator run all four duels closed inside our limit (n = 4). Duels I has since been played: 27 deals, official `duel_points` 15.02, about 0.56 a deal (`docs/briefing.md` "Duels"). Whether v2 caused that is **PENDING**: no controlled comparison exists, and Friday's 0.279 is our own practice estimate, not the official score | REAL (state: `GUARDRAILS.md` `duel_policy`, PR #170; live per the coordinator: the duel loop reads the policy at start, and the merge redeployed it. Our duel accept at tick 163 predates #170, so it ran v1) · the 4-duel run is SIMULATED · v2 vs v1 real result **PENDING** · the Duels I total is REAL (official `/me`, per the audit) | `GUARDRAILS.md` line `duel_policy`; PR #170 body; `docs/briefing.md` "Duels"; `docs/night/saturday-playbook.md` (Duels I window) |
| C36 | Abuela ladder, real Friday: ladder Level 1 best-three share 0.733 (4 deals near her opening ask); Level 2 (Chato) 0; dealer mean 0.464 | REAL | `bazaar evals report`; `eval_ladder` view |
| C37 | Replaying 273 public Abuela/Chato threads, a ladder 21→25 instead of 17→26 raises the modelled share of her range from 0.84 to 0.945 (replay bracket 0.80→0.973 at the top, 0.854→0.888 at the bottom). "21→25" is the unshipped plan; today's ladder is 17→26. Quote the bracket | SIMULATED (replay of public threads; the plan is **not shipped**) | `docs/night/w3-ladder.md`; `charts/04_ladder.csv` |
| C38 | The learning loop is built end to end and reproduced offline on Friday's real data: 26 outcomes → 26 lessons → recall returns the right lesson first (rerank +6.41, BM25 rank 1) → bounded, logged ladder change | SIMULATED/offline on real data · code merged (#89, #96, #112, #158, 04:26–07:31 UTC) · its effect on live deals is **UNVERIFIED** (no capture shows a learned ladder step or a recalled lesson in a live thread; thread 316 opened at 17, today's ladder start) | `uv run bazaar learnings --lessons --save`; PR #96 and #112 bodies |
| C39 | On real Friday threads the learner **confirmed** Abuela's ladder (no change, 0.352 = 0.352 held out) and would have **skipped** a Chato class we could not afford (thread 187). That is "avoid a loss", not "earn more" | REAL data replay · code merged (#112) | `uv run bazaar learnings --policy` |
| C40 | In the simulator the learning team paid about 15% less per Abuela uncommon (25.0 → 21.25, n ≈ 5 buys); the baseline was the strategy default that falls into the first-bid trap | SIMULATED · weak (n ≈ 5, degenerate baseline) | PR #112 body |
| C41 | **Saturday/Sunday delta**: `dealer ladder_share` and duel mean on live days against Friday's 0.464 / 0.279 | **PENDING** (the 09:30 Madrid evals report still scored Friday only: dealer 0.464 over 6, duel 0.279 over 20) | `bazaar evals report --json` after Saturday; not a controlled comparison (different dealers, conditions; v1 Friday vs v2 Saturday) |
| C42 | A real-game before/after of the learner | **UNVERIFIED**: none exists. The code is merged (C38), but no live effect is shown. Do not claim "the agent learned and got better" unless C41 shows it and a live thread shows the learned step | — |

## E. Jev

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C50 | Live `duel_move` on duel 131, tick 141: accept 0.82 / counter 0.17 / hold 0.01, confidence 0.74 under the 0.75 bar → `undecided` → the player kept its move. jev-1.13.0, about 250–290 ms | REAL (one logged call) | `.ai/memory.md` 2026-10-03 "Jev on a real practice duel" |
| C51 | Jev answered undecided on the three duel-policy flag flips (0.72, 0.68, 0.57 vs bar 0.90), so nothing was flipped | REAL | PR #150 body; `.local/jev-decisions/` on that host |
| C52 | `model_for_move`: a buy with 75 P at risk and 38 s left → Opus 0.96; a sell with 9 s left and injection flags → Sonnet 0.95 | REAL (logged calls) | `.ai/memory.md` 2026-10-02 |
| C53 | Jev's calibration (right / wrong / unknown per question) is a SQL view over outcomes | REAL (mechanism) | `eval_jev_calibration`; `evals/trades.py:44-52` |
| C54 | Jev is accurate / Jev improved results | **UNVERIFIED**: sample sizes are tiny, no with/without-Jev A/B exists. A float is a stated confidence, not accuracy | — |
| C56 | Jev on the live agents up to tick 163 (calls / decided / undecided): `duel_move` 21 / 2 / 19, `list_price_choice` 3 / 3 / 0, `offer_is_worth_accepting` 4 / 0 / 4. No decided call has a graded outcome yet (right 0, wrong 0). Mostly undecided means the safe default ran almost every time | REAL (counts) · says nothing about accuracy (C54) | `bazaar evals report`, "Jev calibration" table, in the first-ticks evidence folder |
| C55 | The decisions table had `[redacted]` in place of floats (scrubber false positives). Show floats from the Jev JSONL log, not from `decisions.reason`, unless fixed | REAL (known defect) | `src/bazaar_agent/decisions.py:46`; P1-spec audit |

## F. How we built it

| ID | Claim | Tag | Evidence / source |
|---|---|---|---|
| C60 | Up to 20 Claude Code sessions in parallel overnight, each on its own branch and draft PR; an independent reviewer session re-ran claims; an adversarial "bite hunter" found 20+ failure modes with tests; nothing merged without the human | UNVERIFIED (inherited from the B29 report; recount from git/PR history before saying a number) | `docs/pitch/story.md` Act 3; `gh pr list --state all` |
| C61 | Every PR gets `/pr-review` by a fresh-context reviewer with P0–P3 findings; every task ends with an Honest Implementation Report (no ✅ without pasted evidence) | REAL | `.ai/agents/pr-reviewer.md`; `.ai/context.md` |
| C62 | The score model fitted only on public data reproduces our official 8.34 at tick 159: **in-sample** 8.26 (RMSE 0.34 over 38 snapshots, which are only 8 distinct board refreshes); **held-out** 7.87, 0.47 off. Quote the held-out figure first | REAL data, in-sample fit + holdout · **PENDING #78 merge** | `evals score-sim` (branches #78/#128); `docs/pitch/story.md` |
| C63 | A full simulator of the organisers' API (`bazaar-sim`) is the merge gate: `sim-smoke` CI runs our CLI against it | REAL | `scripts/sim_smoke.py`; `.ai/memory.md` |
| C66 | Cross-venue arbitrage had 0 profitable crossings net of fees across 636 Friday offers; no page could be finished under our price caps; the Saturday clock resumes at h2.65 (Friday started 80 min late) | REAL (analysis of Friday data, reported in night reports W8/W7/B6, not re-run) | `docs/pitch/story.md` Acts 2 and 4 |
| C64 | Strategic bluffing in the words (never in the structure), learned per counterparty against a plain-words control; Abuela gets kindness, labeling and calibrated questions only; no template holds a digit, so a bluff never states our limit | SIMULATED (tests) · merged and **on** (#131, 08:20 UTC, `bluff_enabled = true`) · live use **UNVERIFIED** (thread 316 predates it and used the plain template). Under `duel_policy = v2` duel words stay the plain templates, so no duel bluffs | N16 spec; PR #131; `GUARDRAILS.md` `bluff_enabled`; `.ai/memory.md` N16 merge entry |
| C65 | Team-to-team swap threads with other teams' agents, with a share cap so we never feed a team | SIMULATED (simulator rivals that swap) · merged (#123, 08:00 UTC) · **on since Sat 3 Oct** by team decision (`team_threads_enabled` = true in `GUARDRAILS.md`; every swap needs a Jev yes, spare copies only, a cash cap per hour) · no live swap is shown in this ledger: never say it traded live unless a thread and a settlement are captured | N17 spec; PR #123; `GUARDRAILS.md` |
| C67 | Agent-behaviour tracing in Phoenix: every span of a negotiation carries its session (`dealer:abuela:thread:316`, `duel:<id>`), each Jev call is an evaluator span (verdict, float, bar, model), each request sent is a tool span with no price; our limits are scrubbed out of spans. Outcomes are written back onto the traces (14 annotated, 12 with no trace to attach to, at 09:30 Madrid) | REAL (merged #139, 07:22 UTC; live per the coordinator, not re-checked here) | PR #139; `bazaar evals report` "Phoenix annotations" line in the first-ticks evidence folder |
| C68 | Hard dealers: a per-dealer plan from recall, and readiness for Levels 3 to 5; taking a dealer's final offer above our caps stays **off** (`dealer_final_lift` 0) | SIMULATED (sim proof) · merged (#158, 07:31 UTC) · no live Level 3+ dealer yet | PR #158; `GUARDRAILS.md` |
| C69 | A cash floor stops any purchase from draining our cash. Omar's rule held it high so we could open our own market (#169, 08:06 UTC); #171 reshaped it 13 minutes later into a lower floor plus a venue reserve held only until our venue was open, and the team lowered the floor several times on Saturday. Today the floor is `cash_floor` alone; the spend cap, the official-value cap and human approval above `human_approval_above` still bind every buy. **Never say the numbers** (hard rule 5) | REAL (rules merged) | `GUARDRAILS.md` `cash_floor`, `venue_bond_reserve`, `human_approval_above`; PRs #169, #171 |
| C70 | Our own board venue (market making): switched on by team decision (#171, 08:19 UTC, `allow_venue_open = true`); the maker opened v19 around game hour 3.6, replacing the free starter stall. So far it scores exactly the stall's 0.5 bench with the exact broker, and 0 organic trades (the value other teams create on it). The edge broker (BE1, #218) is merged behind `BAZAAR_BENCH_POLICY` (default exact). At the tick-160 snapshot our market score was 0 (Friday had no market making for any team) | REAL (switch merged; v19 open; bench 0.5 per the rules audit) · the edge broker's effect on the real Market Test is **UNVERIFIED** | PR #171; `GUARDRAILS.md` "Our venue"; `docs/briefing.md` "Our own market"; `leaderboard.json` |

## G. Merge dependencies (what the deck can show, by PR state at the time of writing)

PR states read with `gh pr view` on Sat 3 Oct, about 10:20–10:35 Madrid. Re-check on Sunday 08:30.

- **Merged (UTC):** #59 hook (Fri 23:10), #89 / #96 / #112 learner (04:26–04:45), #150 duel player (04:50), #162 shared ledger (04:57),
  #146 offer inspector (07:08), #139 Phoenix tracing (07:22), #158 hard dealers (07:31), #123 team swaps, off (08:00), #169 cash floor
  (08:06), #170 `duel_policy` v2 (08:12), #171 our venue on (08:19), #131 bluffing in the words, on (08:20).
- **Still OPEN:** #151 (duel zoo), #152 (flags + text hardening), #78 (red team + score model), #128, #102.

| Slide | Needs | State now | What to show |
|---|---|---|---|
| 3 (real deal) | a Saturday deal | C13 captured (thread 316) | C13; C10 as the backup line |
| 4 (deceptive offer) | #146, #152, #78 | #146 merged; #152 and #78 OPEN | The inspector refusing the crafted bait (C21, SIMULATED, code on main) and the hook (C2/C26). The 168-case red team (C24) and the text hardening (C23) stay labelled PENDING, on open PRs |
| 5 (measured improvement) | #150 + #151, optionally #112 + #158 | #150, #112, #158 merged; #151 OPEN | The replay and the d1 simulation as SIMULATED bars; "v2 live since 10:00; Duels I total is in, v2 vs v1 in real duels is still PENDING" (C35). The zoo tournament (C32) stays PENDING #151 |
| Demo, Phoenix replay by session | #139 | merged | Filter one session (`dealer:abuela:thread:316`) |
| Demo, Bazaar Live | bazaar-live PRs #4 and #5 (state unknown) | its `/health` answered ok at 09:31 Madrid, transcript 40 items | Use the deployed version, or `?mock=1` and say it is a recording |
| `evals score-sim`, `cockpit`, `timeline` | #78 / #128, #102 | OPEN | Not shown live; screenshots only |

## H. Known corrections to earlier drafts

- `docs/pitch/story.md` and `docs/pitch/qa.md` say the red team ran **129** cases; the report says **168**. Use 168 (C24).
- `story.md` Act 4 mixes simulated and replayed numbers in one table without tags; use this ledger's tags when quoting.
- `demo-inventory.md` (was `demo.md`) calls the explainer-site branch unpushed and its status chapter stale ("dry run"); both were true
  at 04:30 Saturday. Re-check before using it.
