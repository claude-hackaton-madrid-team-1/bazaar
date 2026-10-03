# Night shift index: every workstream, its PRs and where its work lives

PR states were checked on GitHub on 2026-10-03 at 07:50 Madrid (`gh pr list --state all`). Merge times are in Madrid
time (GitHub shows UTC; Madrid = UTC+2). "taken over by #N" means ogarciarevett's coordinator closed the night PR and
reopened its work as #N.

Where the reports live:
- **main**: `docs/night/` on `main` (arrived with #106 and #150).
- **#154**: the open docs PR that salvages the reports of closed night PRs. Its `docs/night/README.md` holds the
  findings and decisions table, so this index does not repeat them.
- **#78**: W5/W6 plus `docs/night/MORNING.md`. **#135**: the pitch kit (story, Q&A, demo, charts, `docs/decisions.md`).
  Read them there.
- Otherwise: the PR's branch, at `docs/night/<item>.md`.

## Workstreams (W) and the two reviewers

| ID | What | Verdict | PR(s) → state now | Branch | Report | Code |
|---|---|---|---|---|---|---|
| W1a | Realistic Market Test bench simulator | GO as a tool; stall + 0.15 unreachable (oracle +0.03–0.06 p50); live rule is `quote` | #77 ready for review (main merged into the branch 3 Oct; not on main) | `night/w1a-bench-sim` | #77 branch | #77 (open) |
| W1b | Bench broker edge for our venue | NO-GO for stall + 0.15; edge ≥ stall | #84 open (stacked on #71) | `night/w1b-broker-edge` | #84 branch | #84 (open); has `broker probe` without `--auto` |
| W2a | Duel rival zoo + replay harness on real payloads | GO (harness); independent v2 gate | #80 closed → taken over by #151 (open) | `night/w2a-duel-zoo` | #151 and branch | #151 (open) |
| W2b | Duel policy v2 ("silence is free") | GO: 1.42× / 1.55×, 0 outside | #86 closed → taken over by #150 (merged 06:50) | `night/w2b-duel-v2` | main | main (default still v1) |
| W3 | Ladder maximiser (floor table, bid plans) | Abuela GO (0.80 → 0.97); Chato + packs NO-GO under caps | #81 closed (no takeover) | `night/w3-ladder` | #154 | branch only (`bazaar ladder`, `dealer_price_caps`); used as the prior of #112 |
| W4 | Trade desk: rival affinity map, counterparty cap, 09:00 plan | map GO, cap GO off by default, plan marginal (+1.5 P at Friday fill rates) | #79 closed → taken over by #137 (open) | `night/w4-trade-desk` | #137 | #137 (open, squash) |
| W5+W6 | Score simulator, red team, request budget, MORNING | GO as tools; 7.87 out of sample vs 8.34; red team 168 cases, 0 binding changes | #78 open | `night/w5w6-score-redteam-morning` | #78 | #78 (open) |
| W7 | Page economics and the cash plan | no page finishable under our caps; buy no packs | #87 closed (no takeover) | `night/w7-page-economics` | #154 (copy predates the branch tip 77f0777) | branch only (`bazaar plan pages`) |
| W8 | Cross-venue arbitrage + guarded duplicate buys | switches OFF; NO-GO on Friday data (0 crossings net of fees); read-only scan GO | #101 closed (no takeover) | `night/w8-arbitrage` | branch only | branch only (`bazaar arb scan/study`) |
| r1 | Per-PR reviewer: re-ran claims, cross-PR bugs | reviews in [log/REVIEWS.md](log/REVIEWS.md) | no PR | `night/r1-reviewer` | branch (`docs/night/r1-reviewer.md`, `r1-proofs/`) | proofs only |
| r2 | Bite hunter: 29 cases X1–X29 | see [log/BITES.md](log/BITES.md) | #107 open (tests only) | `night/r2-bite-hunter` | #107 | #107 (`tests/bites/`) |

## Backlog items (B)

BACKLOG.md uses B20 and B21 twice. Both pairs are listed below.

| ID | What | Verdict | PR(s) → state now | Branch | Report | Code |
|---|---|---|---|---|---|---|
| B1 | Organic market + win-rate bench policy | organic NO-GO as a source (0/739 offers on team venues); cautious policy +0.04 pts/session, off | #94 closed | `night/b1-organic-winrate` | #154 | branch only (unwired) |
| B2 | Venue go-live runbook + Saturday bench sim | venue 09:00 ≈ +0.19 final pts (default world, upper bound); superseded by B20 | #92 closed (stacked on #84) | `night/b2-venue-runbook` | #154 | **branch only**: `broker probe --auto`, `evals/saturday.py`, `--read-offset` |
| B3 | L3–L5 personas, Trickster inspector, flag policy (off) | GO off: 0 flags on 1,022 honest offers | #93 open → superseded by #146 → #152 (open) | `night/b3-personas` | #93 branch | inspector in #146/#152; `dealer sell` + ladder levels branch only |
| B4 | Rival profiles + read-only opportunity scanner | scanner GO; profiles corrected after r1 | #98 closed → taken over by #138 (open) | `night/b4-rival-scanner` | #138 | #138 (open, squash) |
| B5 | Full dress rehearsal (12 PRs integrated) | 10 cross-PR fix-ups; 580 live sim ticks, 0 crashes | #120 closed (DO NOT MERGE) | `night/b5-rehearsal` | branch (`b5-rehearsal.md`, `-resolutions.md`) | branch only (`scripts/rehearsal`) |
| B6 | Saturday hour-by-hour playbook (jump / resume clock) | reference | #102 open | `night/b6-saturday-playbook` | #154, #102 | #102 (`bazaar timeline`) |
| B7 | Duel v2 within-tick order + Jev path | 55 % we move first; v2 + B11 lift 1.41 / 1.54 | #130 closed → taken over by #150 (merged) | `night/b7-order-jev` | main | main |
| B8 | Duels II days readiness (signed days, latch) | keep `duel_days_auto` OFF (7–11 % outside if the sign is wrong) | #117 closed → #151 (open); #113 closed → #150 (merged) | `night/b8-days`, `night/b8-days-wiring` | #151 | wiring on main; zoo scenarios in #151 |
| B9 | Packs as inventory vs ladder cash | NO: buy no packs | #109 closed | `night/b9-packs-ev` | #154 | branch only (`bazaar plan packs`) |
| B10 | Ops hardening: maker cancel cap, tick offset, injection detector | GO off; Sunday 4.73 → 3.40 req/s | #128 open (stacked on #78) | `night/b10-ops-hardening` | #128 | #128 (open) |
| B11 | Endgame exploitability (exploiters + mitigations) | `min_share 0.3` + `endgame_ticks 1`: 0.10 → 0.23 vs exploiters | #103 closed → #150 (merged); #97 closed → #151 (open) | `night/b11-endgame`, `night/b11-exploiters` | main (endgame), #151 (exploiters) | main + #151 |
| B12 | Unpredictable dealer bids (seeded jitter) | build GO, off by default | #100 closed (no takeover) | `night/b12-dealer-jitter` | branch only | branch only |
| B13 | Wake at the 09:00 opening (bite X4) | fixed | #106 merged 05:07 | `night/b13-wake-opening` | main | main |
| B14 | Lapsed maker bids book spend again (X15) | fixed for our own bids | #126 closed → taken over by #142 (open) | `night/b14-expired-bids` | #142 | #142 (open) |
| B15 | Duels first: the taker can steal a duel's accept (X17) | GO for v1 | #115 closed → taken over by #150 (merged; misses 2fe2a40) | `night/b15-duels-first` | main | main |
| B16 | Unsettled accepts count as held (X18) | fixed | #133 closed → taken over by #143 (open) | `night/b16-unsettled-accepts` | #143 | #143 (open) |
| B17 | Adopt/close dealer threads after a restart (X3) | r1 HOLD: double booking with two takers | #114 closed → taken over by #140 (open) | `night/b17-restart-orphans` | #140 | #140 (open); r1's port list fixes only on the night branch |
| B18 | Rate limits: refused accept frees the slot (X20, X6, X2) | r1 HOLD | #116 closed → taken over by #141 (open) | `night/b18-rate-limits` | #141 | #141 (open); r1 fixes only on the night branch |
| B19 | Price an announced venue fee (X8) | GO | #110 closed → taken over by #144 (open) | `night/b19-pending-fee` | #144 | #144 (open) |
| B20 (packs) | Open the packs we hold (X21) | never assigned | none | – | – | not built; #155 (open) adds an opener behind `open_sealed_packs` |
| B21 (fail loud) | `/health` fails after N failed ticks (X24) | never assigned | none | – | – | not built |
| B20 (venue) | Fastest safe path to an open venue | proposal; not adopted (venue stays at #71's h6.5) | #118 open (stacked on #71) | `night/b20-venue-path` | #154, #118 | #118 (open; `broker watch`) |
| B21 (levels) | Fastest path up the ladder levels | unlock after 3 deals with the previous dealer; whether sales count is unverified | #119 closed | `night/b21-levels` | #154 | branch only (`bazaar plan levels`) |
| B22 | 09:00 operator cockpit | GO, read-only | #122 closed; carried in #135 (open) | `night/b22-cockpit` | #135 and branch | `night/b22-cockpit` + #135 (`bazaar cockpit`) |
| B23 | Live opportunity alerts in the monitor | GO, off by default; Friday: 156 buy chances, 0 arb | #125 closed | `night/b23-opportunity-alerts` | branch only | branch only |
| B24 | Team-to-team thread negotiator | built; live NO-GO until a manual probe | #127 closed (N17 #123 covers team threads) | `night/b24-team-negotiator` | branch only | branch only |
| B25 | Morning assumption verifier | 112 assumptions, 18 automatic checks | #132 closed | `night/b25-verify` | #154 (copy predates review fix bbaa5bd) | **branch only**: `bazaar verify` |
| B26 | New sets + Sunday readiness | no crash on a new set; Sunday playbook | #129 closed → partly in #145 (merged, pre-review version) / #155 (open) | `night/b26-sunday` (final @ 95c9b47) | branch only (Sunday playbook not carried) | review fixes branch only |
| B27 | Duel stack consolidation + e2e + settings card | v2 1.50× v1 e2e, 0 outside; close #159 | #159 open (DO NOT MERGE), #163 open (into #150's merged branch) | `night/b27-duel-stack`, `night/b27-card` | #159, #163 | #163 (`scripts/duel_e2e.py`) |
| B28 | Taker go-live counterfactual | +28 P → +54 P with `min_buy_surplus` 4–6 (kept at today's value) | #134 closed | `night/b28-taker-counterfactual` | #154 | branch only (`bazaar taker-replay`) |
| B29 | Pitch kit for Sunday | fact-checked; 7 high overclaims fixed | #135 open | `night/b29-pitch-kit` | #135 (copy in #154) | docs only |

## The day PRs the audit opened

| PR | State now |
|---|---|
| #60 duel offers inside the limit | closed → taken over by #150 (merged 06:50) |
| #61 dealer opening ask | merged 06:15 via #72 |
| #62 shared ledger | closed → taken over by #162 (merged 06:57) |
| #68 kill switch holds | closed; content merged in #72 |
| #69 `/state` allow-list | merged 02:08 (follow-up #121 merged 04:30) |
| #71 venue + broker | open; r1: DO NOT MERGE AS IS (opens a venue at h6.5 and moves `cash_floor` with no human step) |
| #72 cash and spend accounting | merged 06:15 |

## Code that lives only on a branch

- `bazaar verify` (B25): `night/b25-verify` only (#132 closed).
- B2's `broker probe --auto`, `evals/saturday.py` and `--read-offset`: `night/b2-venue-runbook` only (#92 closed,
  stacked on #84). #84 itself has the manual `broker probe`, not `--auto`.
- B26's review fixes (a dealer-minted card is not scarce; the test no longer pins the live STRATEGY.md value):
  `night/b26-sunday` @ 95c9b47. #145 merged the pre-review version.
- r1's port lists for #140/#141 (once-only booking claim, GET re-send after 429): `night/b17-restart-orphans` and
  `night/b18-rate-limits`.
- 2fe2a40 (B15: v1 skips the v2 slot read): on `night/b15-duels-first`, not in #150.
- W3 ladder, W7 `plan pages`, W8 arbitrage, B1, B9, B12 jitter, B21 `plan levels`, B23 alerts, B24 negotiator,
  B28 `taker-replay`, B5 rehearsal harness: their branches only.

## Corrections

What the night files say that GitHub or a later review contradicts:

- MORNING.md says #162 is "review in progress". It was **merged at 06:57**, the same minute MORNING was finished.
- The task brief places B2's `probe --auto` and `evals/saturday.py` in #84. They are on `night/b2-venue-runbook` (#92).
- BRIEF.md dates the night "Fri 3 → Sat 4 Oct". The night was **Fri 2 → Sat 3 Oct 2026**.
- BACKLOG.md uses **B20 and B21 twice**. The pack opener and fail-loud items were never assigned. BACKLOG also leaves
  B28's PR blank: it is #134.
- PLAN.md says v1 used "7–8 rounds per deal". The fact-checked Friday figure is a **mean of 6.0 (1–9)**.
- B29's line in BACKLOG says "red team 129 cases". 129 was the 02:22 count; the final count is **168**.
- B21 says "sales don't count" toward an unlock. r1 showed t08's data suggests they may, and the owner withdrew the
  claim at 1317727.
- "Being open matters more than a cleverer matcher" (early pitch kit) was an overclaim per B2/#84. The free stall
  may already earn half the bench points, and a venue pays only if its broker beats the stall.
- MORNING recommends `min_buy_surplus` 2 → 4. The coordinator kept 2 (#154).
- Issue #1 was reopened by the audit, but is closed again now (archived by the coordinator at 04:53).
