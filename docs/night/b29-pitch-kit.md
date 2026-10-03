# B29: pitch kit for Sunday (draft; review pending)

Draft PR stacked on `night/b22-cockpit` (#122). Session stopped at its usage limit, about 05:00.

## Done
- `docs/pitch/story.md`: the five acts, each number tied to its source.
- `docs/pitch/qa.md`: 14 likely judge questions with short answers.
- `docs/pitch/demo.md`: the demo asset inventory and a timed 5-minute order. Covers the explainer site, Bazaar Live, Phoenix, the cockpit and score-sim. Credentials are left out.

## Not done
- `docs/decisions.md`: 43 entries plus 13 open decisions, added as drafted. Not reviewed beyond a grep for private values. It describes duel v2's shape and the exploitable endgame: hold it back until after the Final if rivals can read this repo.
- Chart specs + data: now in `docs/pitch/charts/` (charts.md + 10 files), copied as extracted, NOT reviewed. To reconcile:
  - Red team: story.md and qa.md now say 168 cases (W5's current report), like the chart.
  - Market Tests under resume: both counts are right. There are 8 on Saturday's wall clock (the playbook's count), but the h3 session at 09:21 falls in Friday's round, so 7 count for Saturday's round (the chart's count). charts.md now says so, and the playbook's points table carries the note (#102).
- Re-check every Saturday number on Sunday (story.md, "Fill in on Sunday").

## Risks found while checking the demo
- The explainer site branch was never pushed and has no PR. Its status chapter still says "dry run".
- Never show raw `/state` on screen until #121 merges.

## Fact-check (independent read-only agent, ~06:30) and fixes
- **Private values:** `04_ladder.csv` and `charts.md` gave our bid ladders and price caps. Replaced with the
  labels "today" and "W3 plan".
- **Wrong numbers:**
  - Friday's real deals took 6.0 rounds on average (1–9). 6.85 is the zoo mean.
  - 10 cross-PR fixes, not 9.
  - About 15 builder sessions, not "up to 20".
  - The clock was 81 min late.
- **Overclaims softened:**
  - "Sales don't count" is now marked unverified.
  - The shared ledger is a design with known gaps: #62 is unmerged, and a redeploy can under-book it.
  - PAUSE doesn't stop the maker's cancels on main.
  - The v2 fix sits in #150, awaiting merge.
  - "Every path" is now "every deterministic path".
  - The score model's holdout (7.87, within 0.47) is the honest headline.
- **decisions.md:**
  - A new "After 05:00" section: #106 and #105 merged at 05:07 and 05:51, and the coordinator's triage maps each
    closed night PR to its takeover (#137, #138, #140–#144, #150, #151; reports in #154).
  - Stale open decisions fixed.
  - #71's HIGH list updated.
  - The B11 endgame settings are no longer spelled out.
- **What still holds:** every other number spot-checked against its source matched.
