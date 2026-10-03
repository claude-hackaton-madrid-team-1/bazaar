# The 90-second live demo, and its backup

Draft as of Sat 3 Oct, ~06:00 Madrid. Asset inventory from Marius's earlier pass: `demo-inventory.md` (moved from `demo.md`; its
Saturday 04:30 facts may be stale). Commands here were checked against the code (see `evidence.md`), not run live.

**Goal:** show, in 90 seconds, that the agents are real, traceable and bounded: **Bazaar Live** (what they do) → **one Phoenix replay**
(why, tick by tick) → **one `/state` view** (what the public sees).
**Rule:** one thread, one story. The Phoenix replay and `/state` show the **same deal** as slide 3.

## Before the room (Sunday 08:30 pre-flight, then again 30 minutes before)

| Check | How | If it fails |
|---|---|---|
| Services up | `curl -s https://bazaar-taker-production.up.railway.app/health` and the maker's; `curl -s https://bazaar-live-production.up.railway.app/health` | Go straight to the backup recording |
| Phoenix logged in, trace open | https://phoenix-production-6aa3.up.railway.app, project `bazaar`, login and password are in Railway only; never on a slide or in the repo. Keep two tabs open: the chosen trace, and the Spans list | Screenshot of the trace (taken Saturday) |
| Bazaar Live sound gate clicked once | Open the URL, click "Start the show with sound" (or "Watch muted"), then leave it | `?mock=1&speed=2` plays recorded fixtures; say it is a recording |
| `/state` shows only sent rows | Open `https://bazaar-taker-production.up.railway.app/state` and read it **before** the room | Do not show it; use the screenshot |
| Database answers | `uv run bazaar db tables` (never show the URL) | Skip any on-screen SQL; use screenshots |
| Laptop | Unmuted, notifications off, do-not-disturb on, display scaling checked, no `.env` open, terminal font large | — |

**Never put on screen:** `.env`, the team key, `DATABASE_URL`, the Phoenix password, a `decisions` row with limits, `GUARDRAILS.md` values,
affinities or card values. Raw `/state` and `/events` are allow-listed since #69/#121 (sent rows only, `jev` null), but read the payload
once before you show it.

## The 90 seconds

| Time | Screen | Say (short) | Source |
|---|---|---|---|
| 0:00–0:30 | **Bazaar Live**, `https://bazaar-live-production.up.railway.app` (live), buyer and seller at the stall, the deal from slide 3 on the cork board or in the speech | "These characters act out our agents' public moves, spoken. This is the {{CARD}} deal." | `docs/services.md` "Bazaar Live" |
| 0:30–1:00 | **Phoenix**, project `bazaar`: the `negotiation` root of thread {{T}} → the `tick N` children → one event each of `message`, `dealer_offer`, `jev_verdict`, `guardrail`, `our_move`. Then the `ladder_share` annotation on the root | "Same deal, tick by tick: her message, her offer, Jev's floats and whether it cleared its bar, the guardrail verdict, our move. And the score of this negotiation." | README "Observability"; `capture.md`-verified span names |
| 1:00–1:30 | **`/state`** of the taker (`.../state`), browser JSON viewer, or `curl -s .../state \| jq '.decisions[0:3]'` | "Anyone can read this. It says what we did. It never says why in numbers; our limits and Jev's floats are not published." | `docs/services.md` "Public by design" |

If Phoenix's session view is merged (#139, N18) use **Sessions → `dealer:{dealer}:thread:{id}`** instead of the `negotiation` root. If
not, use the Spans tab, because a `negotiation` root appears **only when the negotiation ends**. Choose a thread that has ended.

## The deceptive offer is not in the 90 seconds

Slide 4 uses a pre-recorded terminal: `uv run pytest tests/test_accept_gate.py tests/test_inspector.py -v` on the merged inspector, with
the green lines for the bait test (`test_the_explainer_trickster_names_a_legendary_and_binds_a_common`). Put a small label on the
recording: **SIMULATED: crafted offer, unit test**. Do not run it live.

## BACKUP: what to record, and when

Record on **Saturday afternoon, during a real deal**, so the backup is real and not a mock. Three assets, in this order of priority:

| # | Recording | When | How |
|---|---|---|---|
| R1 | **Bazaar Live + Phoenix + `/state` as one 90 s screen recording**, exactly the table above, on the thread of a deal that just settled | Saturday 15:00–17:00 Madrid, as soon as a clean dealer deal settles (the Chato ladder and Duels II are the other candidates). Finish it with a deal you can name | macOS `Cmd+Shift+5` → record selected portion, microphone off, 1080p, laptop on power. Save as `pitch-demo-R1-<date>-<hhmm>.mov` in `~/Desktop/pitch/` and a copy on Marius's laptop |
| R2 | **Slide 4's pytest run** (the bait refused) | Saturday, any time after #146 merges | Terminal at large font, record the full run, 20–30 s |
| R3 | **A Bazaar Live mock** fallback with sound | Any time | `?mock=1&speed=2`, 60 s; use if the show is silent because agents are idle |
| S | Stills: the thread transcript, the settlement row, the Phoenix tree, the `/state` JSON, the `bazaar evals report` table | At the same moments | `Cmd+Shift+4`; name them `S<n>-<what>.png`; **no secrets in frame** |

Rules for recordings:
1. Record **while the deal is fresh**, then immediately save the evidence set (`evidence.md` §3) for the same thread.
2. Label on screen or in the first frame: date, time, "REAL, recorded". If it is a mock: "RECORDING OF A SIMULATION".
3. Watch each recording once, start to end, before Sunday; check no key, URL with credentials, or limit appears.
4. If the live demo and the recording disagree, trust the recording and say it is a recording.

## Failure plan (decide before the room)

| Failure | Action |
|---|---|
| Wi-Fi down | Play R1 from the desktop. Phone hotspot is the second network; test it Sunday 08:30 |
| Phoenix slow or logged out | Show the screenshot `S-phoenix`; keep talking |
| Bazaar Live silent (no agent activity) | `?mock=1&speed=2` and say "recording" |
| `/state` empty after a redeploy (the 50-decision ring resets) | Show the screenshot; say the archive is Postgres |
| Anything fails twice | Marius takes the laptop; Omar continues the story without it |

## Open items

- Bazaar Live PRs #4 (RPG scene) and #5 (real transcripts) were open at Saturday 04:30; confirm what is deployed.
- #139 (session replay) is open; the Sessions view is UNVERIFIED on the live Phoenix.
- The explainer site (`game-explainer-site`, Marius) was unpushed with a stale status chapter. Push it, or skip it. Not in these 90 s.
