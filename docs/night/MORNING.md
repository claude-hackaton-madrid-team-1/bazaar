# MORNING — Sat 3 Oct, read by 08:30

Finished at 06:30 by the w5w6 session, updated 06:40 (repo copy; `_night/MORNING.md` also has our cash figures). It builds on w8's 05:55–06:06 draft (kept as `_night/MORNING.w8-0606.md`) and
r1's verdicts at 06:40 (`night/r1-reviewer:docs/night/r1-reviewer.md`); PR states checked live on GitHub at 06:28.
Times are Madrid time; GitHub shows UTC (Madrid = Z + 2 h).

**What changed since w8's 06:06 version:** **#89 (learning taker, ON by default) was merged at 06:26** and is live on
`main`; #72 was **merged at 06:15** (it carries #61's ladder fix and #68's kill-switch
hold); #61 is merged through it, #68 closed. #62 closed, taken over by **#162** (review in progress). #140/#141 went
to **HOLD** (r1, 06:19). #105 now ships `holdings_from_db = false` (r1, 06:06). **#91** (evals inside the tick loop)
and **#145** (new sets, two defaults ON) were merged without the default decisions r1 asked for. #71 has a new head
(stall finding fixed, process blocker remains). #150 is mergeable with one call to make.

## TL;DR
0. **Live on `main` now, merged overnight with defaults ON:** the learner stack **#89 + #96 + #112** (06:26–06:46: the
   taker learns dealer blockers and skips them, learned ladders and class skips, and recalled "lessons" added to
   Jev's state for the duel, maker and taker moves), #145 (`protect_page_sets = RET,CHA`, `supply_scarcity`), #91
   (evals in the tick loop). r1 asked for all of them to default off. **Decide before 09:00** (§3.5).
   `BAZAAR_LEARN=0` on `bazaar-taker` stops **only the taker's learning**; the lessons wrapping of Jev is
   unconditional on `main` (`cli.py` `_duel_jev`, `_maker_jev`, `agent_taker`: `with_lessons(...)`, no flag). Jev
   still picks only among the legal moves code builds, but the lessons can steer which one.
1. **One merge authority.** ogarciarevett's coordinator triaged the night: it closed most night PRs and opened takeovers
   (#137–#162), and merged some over r1's findings. Agree with Omar at 08:30 who merges today. Every merge to `main`
   redeploys the live agents, so **freeze at 08:50**.
2. **The planned order (#60, #62, #69, #61 → #68 → #72, #71) is obsolete:** #69 merged (02:08), #72 merged with #61
   and #68's content (06:15), #60 → #150, #62 → #162, #71 must not merge as is. The new batch is in §3.1.
3. **Clock column at 08:55:** `GET /api/clock` → `t_hours` ≈ 4.0 (jump) or ≈ 2.65 (resume; Friday froze at h2.65).
   If it resumes, every game-hour event slips 1 h 21 min, Duels I included.
4. **Don't merge:** #71 (opens a venue at h6.5 and drops `cash_floor` 270 → 100/370 with no human step), #131
   (bluffs at dealers by default; the rest of the learner stack is already merged), #157/#158 until fixed, #159 (duplicate of #150).
5. **Before Duels I:** #150 (duel stack v2) with its one call (commit 6b56719, §3.4) and the duel settings card. The
   numbers: v2 scores **1.50×** v1 at 15 s ticks in the end-to-end sim, 0 closes outside the limit, and v1 loses 11
   accepts to the 1/tick cap (v2 none).
6. **Cheapest lever:** `min_buy_surplus` 2 → 4 on the live taker (B28: Friday +28 → +54 P at copy value). The
   coordinator kept 2; your call.

## 1. Numbers that matter

| Topic | Number | Source |
|---|---|---|
| Score model | Out of sample **7.87** vs official 8.34 at tick 159 (fit on ticks < 140); in sample 8.26 (RMSE 0.34). Ladder = 12.5 × min(1, raw/top-3 mean). Others' Chato deals cost us ~2.2 points on Friday. 1 Saturday round point = 0.40 final points | W5 #78 |
| Ladder levers | First Chato deal at share 0.5: +0.94 round points; three: +2.82; a level-1 deal at share 1.0 replacing a 0.6: +1.50 | W5 #78 |
| Duel v2 | lift **1.42×** (decay 0.06/0.08), **1.55×** (0.08/0.10); replay 178 vs 122 P; e2e 15 s: 1.50×, pie share 0.40 vs 0.24, 0 outside the limit | W2b → #150, B27 #159 |
| B11 endgame | `duel_endgame_min_share 0.3` + `duel_endgame_ticks 1`: share vs exploiters 0.10 → 0.23, honest ≥ 0.996× | B11 → #150 |
| Days (Duels II) | keep `duel_days_auto` OFF until a real payload shows the sign: 7–11 % outside the limit when the sign is wrong | B8 → #150 |
| Ladder plan | Abuela uncommon plan 21→25: share 0.97 vs 0.80 (real replay), 0 repeats; Chato + packs NO-GO under the caps | W3 (salvaged in #154) |
| Broker vs stall | the broker as shipped (exact) = stall (+0.00); oracle over stall only +0.03…0.06 p50; win-rate over the stall matters more than margin | W1a/W1b/B2 |
| Taker counterfactual | Friday at current settings: **+28 P** at copy value; `min_buy_surplus` 4–6: **+54 P**. `cash_floor` is the binding cap | B28 (salvaged) |
| Pages / packs | no page finishable under `max_price_rare` 80 (Chato 82–93); buy no packs | W7, B9 (salvaged) |
| Red team (#24) | 168 cases, 0 binding-field changes; the desk's write tools are denied by the guard when it obeys hostile text | W6 #78 |
| Request budget | Saturday GO (2.37 req/s at every loop's ceiling). **Sunday conditional**: 4.73 req/s at the ceiling, 5.27 with 0.5 req/s of MCP/desk use, 5.73 with 3 extra `dealer buy` (NO-GO). Tick edge with the SDK's 429 re-sends: 0 calls lost at the ceiling, 1–2 lost when crowded; the opt-in stagger brings that to 0 | W6 #78, r2 X6 |

## 2. Night PRs, one line each (verdict · what you need to do)

Open PRs from the night sessions:

| PR | What | Verdict · key number | Needs from you |
|---|---|---|---|
| [#78](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/78) | Score sim, red team, request budget, this file | GO as tools · 7.87 out of sample vs 8.34; Sunday budget conditional | merge after the game-day batch (no live behaviour change: stagger off by default); merged with `main` at 06:32, clean |
| [#77](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/77) | Market Test bench sim | GO as a tool | nothing urgent (sim only) |
| [#84](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/84) | Broker edge (stacked on #71) | NO-GO for stall + 0.15; edge ≥ stall | wait for the venue decision |
| [#93](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/93) | L3–L5 personas, Trickster inspector (flags off) | GO off · 0 flags on 1,022 honest offers | superseded by #146/#152: close it |
| [#102](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/102) | Saturday hour-by-hour playbook | reference | read it; merging redeploys, so don't before Duels I |
| [#107](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/107) | r2 bite tests (tests only) | GO | merge with or before any fix PR |
| [#118](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/118) | B20 venue path (stacked on #71) | proposal, not adopted | venue decision (§3.3) |
| [#128](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/128) | B10 maker cancel cap + tick offset (stacked on #78; builds on #78's `BAZAAR_TICK_OFFSET_S`) | GO off · Sunday 4.73 → 3.40 req/s | after #78, rebased onto it (`ticks.py` now has #106's opening wake); decide whether the cap belongs in GUARDRAILS.md |
| [#135](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/135) | Pitch kit (story, Q&A, demo, charts) | fact-checked, 7 highs fixed | Sunday work; keep the repo private |
| [#159](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/159) | B27 duel stack integration + settings card | DO NOT MERGE (integration only) | close in favour of #150; carry its settings card |

Closed tonight by the coordinator and carried on in a takeover (review status from r1):

| Night PRs | Takeover | r1 verdict | Needs from you |
|---|---|---|---|
| #60 #86 #103 #113 #115 #130 (duel v1 fix, v2, B11, B8, B15, B7) | [#150](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/150) | mergeable, one call | gate 6b56719 to v2 or split it; cherry-pick 2fe2a40 |
| #80 #97 #117 (duel zoo, exploiters, days) | [#151](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/151) | mergeable outside game hours | merge after Duels I (redeploys the shared sim) |
| #114 #116 (B17 restart orphans, B18 rate limits) | [#140](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/140) → [#141](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/141) | **HOLD** | apply r1's port list (`_night/r1_b18_on_72_sdk_resolution.diff`, 1,059 tests) first |
| #126 #133 (B14 lapsed bids, B16 unsettled accepts) | [#142](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/142) → [#143](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/143) | mergeable (stacked on #141) | merge after #140/#141 |
| #110 (B19 pending fee) | [#144](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/144) | GO | merge in the batch |
| #79 #98 (W4 trade desk, B4 rivals) | [#137](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/137) → [#138](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/138) → #123 | mergeable, **squash** (private numbers in history) | after the batch; #137 needs the `hands_off_ids` patch; keep #123 off |
| #93 (inspector) | [#146](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/146) → [#152](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/152) | mergeable, flags stay off | after the batch |
| #62 (shared ledger) | [#162](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/162) | review in progress | check `DATABASE_URL` on every live Railway service before it merges |
| #129 (new sets) | #145 (merged) / #155 | merged with 2 defaults ON | decide `protect_page_sets = RET,CHA` and `supply_scarcity` (on now) |
| – (ogarciarevett's learner) | #89, #96, #112 (merged 06:26–06:46) / #131 (open) | live with learning on; #131 DO NOT MERGE | `BAZAAR_LEARN=0` on `bazaar-taker` (taker only); for the Jev lessons wrapping you need a revert or a gating patch |
| #81 #87 #92 #94 #100 #101 #109 #119 #120 #122 #125 #127 #132 #134 | reports in [#154](https://github.com/claude-hackaton-madrid-team-1/bazaar/pull/154) (docs) | – | read §1; #120's rehearsal branch `night/b5-rehearsal` has the 12-PR integration (0 crashes in 580 live sim ticks) |
| #106 (B13 wake at the opening) | – | merged 05:07 | nothing |

## 3. Decisions only you can make (recommendation in bold)

1. **The 08:35–08:50 batch:** **#144**, then **#140 → #141 → #142 → #143** once r1's port list is applied to #140/#141
   (the double booking with two overlapping takers and the `sdk.py` conflict; the resolution diff is ready). r2's chaos
   sweep: 0 flags of 20 on #143 vs 19 of 20 on main. If the port isn't in by 08:45, merge #144 only and run **one
   taker** (the double booking needs two). **#150 goes in this batch if you have decided 6b56719 (§3.4) by then**, with
   the duel settings edited into GUARDRAILS.md in the same PR (one redeploy, not two); otherwise it is a mid-play
   redeploy: right after a duel wave ends, never during one, and before Duels I. **#162** only once r1's review says GO and `DATABASE_URL` is set on every
   live service (the live writers refuse to start without a shared ledger).
2. **Don't merge #71** unless you make the venue go-live call on purpose; then split the flags into a one-line PR.
   Until h6.5 its 270 P reserve refuses every maker bid.
3. **Venue:** **no venue this morning** (our cash is short of the 540 P needed under floor 270 + reserve). See whether the free starter
   stall scores at the first bench (§4), then decide. **Keep `cash_floor` 270** until then.
4. **#150's call:** commit 6b56719 changes the *default* v1 two-issue play when Jev is on (the offer jumps by |w| × days;
   proved only without Jev). **Gate it to v2.** Duel settings: **`duel_policy = v2`, `duel_endgame_min_share 0.3`,
   `duel_endgame_ticks 1`, `duel_days_auto` OFF**. Merge #150 before Duels I, outside a duel wave.
5. **Merged with defaults ON overnight:** the learner stack #89/#96/#112, #145 (`protect_page_sets = RET,CHA`,
   `supply_scarcity`) and #91 (evals in the tick loop). **Decide on each before 09:00**; r1 recommended defaults off.
   **Set `BAZAAR_LEARN=0` on `bazaar-taker`** unless you want it learning in the first live hour. The Jev lessons
   wrapping (duels, maker, taker) has no switch: if you want it off before Duels I, it takes a revert of #96/#112 or a
   one-line gate on `BAZAAR_LEARN` in `_lessons()`; ask Omar which, since his coordinator merged it.
6. **`min_buy_surplus` 2 → 4** (STRATEGY.md): **yes** (B28: never worse in any replay).
7. **Sunday:** no MCP/desk loops and no extra `dealer buy` during 15 s ticks (budget). Turn on the stagger per Railway
   service (`BAZAAR_TICK_OFFSET_S`: duels 0, monitor 0.5, taker 2, maker 4) once #78 or #128 is merged.

## 4. Checklist 08:30–11:30 (Madrid · JUMP column: clock at h4 at 09:00 / RESUME column: h2.65)

| Time | Do |
|---|---|
| 08:30 | Read this. Agree the merge authority with Omar. `gh pr list`. Railway: taker/maker/duels up, `BAZAAR_LIVE` as intended, **`BAZAAR_LEARN=0` on `bazaar-taker`** (#89 is merged, on by default), `DATABASE_URL` set on every live service. |
| 08:35–08:50 | The batch (§3.1), the defaults (§3.5), `min_buy_surplus` (§3.6). **08:50 freeze.** |
| 08:45 | **Start `uv run bazaar monitor` on one laptop**: since #73 no Railway service runs it, and it is the only writer of `feed_events` and `/me` snapshots (score-check, cockpit, dealer curves and the evals read them; r2 X23). Then `uv run bazaar cockpit --watch` (branch `night/b22-cockpit`). |
| 08:50–08:55 | Deploy check after the batch: every live Railway service redeployed and healthy (`bazaar status`, decision rows, no crash loop), the env changes (§3.5–3.7) in place. |
| 08:55 | `GET /api/clock` → `t_hours`: ≈ 4.0 → JUMP, ≈ 2.65 → RESUME. |
| 09:00 | Doors. `bazaar-duels` log shows the shared ledger line, not the JSONL fallback. Watch 5 ticks. r2's read-only probes (BITES.md "09:00 read-only probes"). `uv run bazaar evals score-check` once (is Saturday's `ladder_points` back at 0? then the ladder restarts per round). |
| 09:10 | Open Friday's welcome pack by hand (nothing opens packs). `bazaar status` / decision rows every ~30 min (a loop failing every tick still shows `/health` ok). |
| 09:03 / 10:24 | h4.05: round 2, the RET set, the 150 P grant. |
| — / 09:21 | h3 bench (RESUME only). |
| 10:00 / 11:21 | h5 Market Test. |
| 10:10 / 11:31 | `/me` → `score`: does the free stall score at the bench (≈ 0.5)? Settles §3.3. |
| ~11:00 / 12:20 | Last safe moment for #150 + duel settings (a merge redeploys). |
| 11:30 / 12:51 | **h6.5 Duels I** (~96 min, 34 duels, 3 at a time). Probe P6: do duel accepts count against the 1/tick limit? |

## 5. Top risks
- **Accidental merges:** #71 (venue + floor), the learner stack (`BAZAAR_LEARN` on), #159.
- **Clock column:** a plan written for h4 at 09:00 is 1 h 21 min wrong if the clock resumes.
- **A merge during play redeploys:** without #140, each redeploy under-booked 10–65 P in r2's runs.
- **Duels on default v1:** two-issue offers outside the limit at Duels II and lost accepts at shared deadlines → #150.
- **No monitor running:** Railway has none since #73; without a laptop monitor the feed-based tools read Friday's data.
- **Two takers at once:** double-books dealer deals (#140 HOLD) and doubles the request rate (budget).
- **#123 on** leaks our duplicates and album gaps through `/state`; **#139** leaks card values in accept traces.
- **#160**'s Q&A overclaims and quotes our cash: keep the repo private until after the Final.

## 6. Left halfway
- #140/#141: r1's port list, not applied yet (hard-taker's fixes live on `night/b17-restart-orphans` / `night/b18-rate-limits`).
- #150 misses 2fe2a40 and the slot release on a refused accept.
- B27 e2e at 30 s was still running at 06:20 (#159).
- #145 lacks B26's fix for dealer-minted cards (a zero-copy dealer card must not count as scarce).
- `bazaar verify` (B25) lives only on `night/b25-verify`.

**Where things are:** `_night/STATUS.md` (log) · `_night/REVIEWS.md` (r1) · `_night/BITES.md` (r2, X1–X29) ·
`_night/BACKLOG.md` · `_night/FOR_OGARCIAREVETT.md` (relays for Omar's coordinator) · PR #154 `docs/night/` (salvaged
reports) · w8's earlier draft with more detail: `_night/MORNING.w8-0606.md`.

## r1 late additions (06:36)
- **#162 shared ledger (P0, takes over #62):** MERGEABLE with 2 conditions.
  - (1) Before the 09:00 deploy: with no client-side query deadline, a frozen Postgres blocks every live writer's per-tick ledger ping for more than 60 s. Fix: a bounded worker thread (as in holdings.py) plus tcp_user_timeout.
  - (2) r1's ledger Protocol patch (REVIEWS.md "## PR #62") is not carried. #141/#142 `release_accept` and #137 `hands_off_ids` use the removed `self._conn`, so a refused accept or the maker raises AttributeError after merge. Whoever lands second ports them.
- **#161 (#72 follow-up):** HOLD. HIGH: a dealer accept that /me already shows as paid no longer counts toward the hourly spend cap (proof: 21 + 12 sent under cap 30). One-line fix: drop the synthetic commit only for board accepts.
- Proofs: _night/r1_proof_pr161_desk_spend.py and _night/r1_probe162_freeze.py.
- **06:46 r1:** #96 and #112 are ALSO merged to main (04:43-04:45Z).
  - The live taker now replaces dealer ladders with learned ones and can skip whole price classes. That is default-on through BAZAAR_LEARN.
  - Lessons wrap Jev's binding moves in duels, maker and taker. The ~152 MB models warm in every agent process, and our past duel limits go to Jev.
  - BAZAAR_LEARN=0 on bazaar-taker turns off the taker's part. The duels and maker wrapping is not tied to it; verify that on main.
