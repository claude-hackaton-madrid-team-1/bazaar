# Night review (r1): every open PR reviewed, fixes re-verified

Session `night-r1-reviewer`, 03:14 to about 07:30 on Saturday 3 October. Two things are outside the repo: the full findings and the running log. The full findings (file:line, failure scenario, fix, evidence) are in `_night/REVIEWS.md`, and the log is in `_night/STATUS.md`. Proof tests are in `docs/night/r1-proofs/` and `_night/r1_proof*.py`. This page is for Marius.

## How
- One reviewer subagent per PR, each in its own scratch worktree, at high effort.
- Gates and the PR's headline numbers were re-run each time, against the simulator and fixtures only. Nothing ran against the live game.
- Every review also checked:
  - guardrail and kill-switch bypasses;
  - defaults that change today's behaviour;
  - private values;
  - tick and hour units;
  - races between processes;
  - breaks between PRs.
- Each fix was re-checked on its new head. For the takeover PRs (opened by the coordinator after 03:40), the checks were:
  - their content diffed against the last head r1 had verified;
  - the r1 proofs re-run;
  - findings still open carried over.
- "Defaults unchanged" was checked with differential runs. For example:
  - 100k duels for v1;
  - 400 taker runs for the arbitrage PR;
  - 40k fee rows for B19;
  - 3,000 duels across missed ticks.

## What Marius must decide (open PRs, status at about 06:40)
| PR | Verdict | Why |
|---|---|---|
| #71 live venue (ogarciarevett) | **Do not merge as is** | Merging opens a venue at game hour 6.5 and moves the cash floor (370 before the venue opens, then 100) with no human step, against the 02:30 decision. The code findings are mostly fixed: the vault lockout, the starter-stall with #105, and the reopen on a failed mark. Two remain open: a failed broker-key save is never retried, and an open whose answer is lost leaves no broker. Merge only with an explicit go-live, or split the flags into a one-line PR. |
| #150 duel stack (takeover of #60/#86/#103/#113/#115/#130) | Mergeable; **one call** | The squashes match the verified heads. Commit 6b56719 changes default v1 two-issue play with Jev on (the offer jumps by \|w\| × days), and the proof was run without Jev. Gate it to v2 or split it out. Also missing: 2fe2a40 (an extra ledger read in v1) and the slot release on a refused accept. #159 (B27) duplicates it: close #159 and carry its settings card over. |
| #151 duel sim | Mergeable outside game hours | It redeploys the shared simulator, which changes scoring and makes a duel accept use the team's accept slot. |
| #144 (B19 fee) | GO | A pure fix. A fuzz over 40k rows never priced below main. |
| #137 → #138 → #123 trade desk / scanner / team threads | Mergeable in that order, **squash** | #137 fixes the earlier dealer-bids-during-a-ledger-outage high. #123 must not be switched on until `/state` stops showing card, ref and fee. |
| #140 / #141 (B17 / B18 takeovers) | **Hold** | #140 still books one deal twice when two takers overlap (36 instead of 18). #141 conflicts with main's `TrackedBazaar`. A port list from the original night branches is in REVIEWS.md, with a ready `sdk.py` resolution: `_night/r1_b18_on_72_sdk_resolution.diff`, 1,059 tests pass. |
| #142 / #143 (B14 / B16 takeovers) | Mergeable | B14 fixes phantom spend only for bids this process posted. Two makers refund one lapse twice. B16 isn't wired into `dealer buy` or the desk. |
| #146 → #152 inspector / flags | Mergeable; flags stay off | #152: a truncated `flags.jsonl` stops the taker from starting, and the flag cap is now a lifetime cap. |
| #157 faster ticks, #158 hard dealers | Fix first | #157 turns both new rules on in GUARDRAILS.md. #158 has a high: at lift 0 the taker opens threads by its cash room instead of its free slots (proof in `_night/`). |
| #89 / #96 / #112 learner stack, #131 bluff tactics | **Do not merge** | All of them ride `BAZAAR_LEARN`, which is on by default: learned ladders replace live bids, and lessons wrap every Jev move. #131 bluffs at dealers by default. |
| #145 / #155 new sets | After Omar decides 2 defaults | `protect_page_sets = RET,CHA` and `supply_scarcity` are both on. B26's fix for dealer-minted cards was dropped. |
| #139 lean tracing | Merge after Duels I | Card values can still be recovered from "for X … surplus Y" on every accept. |
| #154 / #160 docs | Docs only | #160's Q&A still overclaims, including "r1 re-ran every claim"; r1 re-ran each PR's headline numbers, not every claim. Our cash appears in a spoken answer. Keep the repo private until after the Final. |
| #162 shared ledger (takes over #62), #161 (#72 follow-up) | Review in progress at the time of writing | See REVIEWS.md. |

Already merged during the night: #72 (with #61's content), #105, #106, #108, #91. #105 strips `starter_broker_key` from the agents' `/me`. #71's latest head handles that.

## Proofs
`docs/night/r1-proofs/` holds the first batch. `_night/r1_proof*.py` holds the rest; the first line of each file names the PR and head it targets.
