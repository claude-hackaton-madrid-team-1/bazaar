> Sanitised copy of `_night/BRIEF.md` (night shift of Fri 2 → Sat 3 Oct 2026, file state at ~07:15 Madrid).
> `[private]` marks redacted private game values (cash, card values, affinities, our price caps and bid
> ladders, album state). Numbers inside test descriptions (`tests/bites/*`, chaos runs, proof tests) are
> fixture values, not our real ones. See [../SUMMARY.md](../SUMMARY.md) and [../INDEX.md](../INDEX.md).

# Night shift brief (Fri 3 → Sat 4 Oct 2026, ~02:30 → 08:30), shared by every night session

You are one of seven independent night sessions for Team 1 (t01) in The Bazaar (Causa Prima hackathon).
Marius reviews everything in the morning. Your deliverable is a DRAFT PR plus a one-page report.

## Read first
- /Users/mariusserban/orca/workspaces/bazaar/_night/PLAN.md — the overnight plan (workstreams W1–W6), with the
  facts it rests on and the orchestrator's corrections. Your workstream is named in your prompt.
- In your worktree: vendor/bazaar-kit/RULES.md (official rules, scoring, fair play, rate limits: the hard boundary),
  GUARDRAILS.md, RUNTIME.md, STRATEGY.md, AGENTS.md, README.md, docs/services.md, and the code in src/.
- Open PRs you may build on (unmerged on purpose, Marius merges them): #60 duels inside limit, #61 dealer ladder,
  #62 shared ledger, #68 kill switch hold + flatten, #69 /state allow-list, #71 venue + broker build-only,
  #72 cash/spend accounting, #55 bazaar-sim (ogarciarevett). `gh pr view N`, `gh pr diff N`.

## Hard rules
- NEVER touch the live game: no calls to bazaar.causaprima.ai with a key, no `--live`, no BAZAAR_LIVE=1, no
  `duel run --play`, no venue opening. Doors are closed anyway. The simulator (bazaar-sim, run LOCALLY in-process
  or on localhost) and offline data are your playground.
- Do not change any existing GUARDRAILS.md value (cash_floor, caps, allow_venue_open, allow_flags...). New
  parameters are fine if their default keeps today's behaviour.
- Data: prefer repo fixtures (tests/fixtures/evals/duels_done.json, dealer_curves.json, feed_ours.json,
  tests/fixtures/api/*) and the local monitor capture
  /Users/mariusserban/orca/workspaces/bazaar/lets-start-using-the-real-feed-we-should-have-a-monitor-ready-in-the-code/.local/stream.jsonl
  (3,517 events, ticks 0–149, public feed + our agent.me snapshots). If you need the shared Postgres, you may run
  READ-ONLY SELECT queries using DATABASE_URL from that same worktree's .env: never print it, never write, never run
  `bazaar db init/load`, never run the test suite against it. Never copy our private values (affinities, limits,
  your_value) into committed files beyond what the repo already holds.
- Never commit .env, secrets, or .ai/memory.md. Never `git stash` (shared stash stack). Never force-push anything
  but your own branch. Never merge. Never edit other people's branches or PRs.
- Everything written to GitHub, commits and code is in ENGLISH.
- Black is the formatter. Gates before every push: `uv run pytest`, `uv run ruff check src tests scripts`,
  `uv run black --check src tests scripts`, `uv run mypy src`. Run `/code-review` (or review your own diff
  critically) before opening the PR and fix what it finds.
- Set up once: `git config core.hooksPath .githooks && uv sync`. Don't commit the CLAUDE.md/GEMINI.md the hook
  generates.

## Deliverables (by 08:00)
1. Branch `night/<your-workstream>` pushed; a DRAFT PR (`gh pr create --draft`) whose base is the branch named in
   your prompt (stacked) and whose body has: goal, what changed, evidence (tables/numbers), the go/no-go verdict
   against the criteria in PLAN.md, risks, what Marius must decide. `Refs #N` for the issues it touches.
2. A report at docs/night/<your-workstream>.md in the PR (one page: numbers over adjectives).
3. Append one status line to /Users/mariusserban/orca/workspaces/bazaar/_night/STATUS.md when you start, at each
   milestone, and when done: `HH:MM <workstream> <state> <one line> <PR url if any>`. Append only (`>>`).
If you get blocked, write why in STATUS.md and in the PR body, and deliver what you have. Don't stop early: use the
whole night to improve the result (more scenarios, better models, tighter tests).
