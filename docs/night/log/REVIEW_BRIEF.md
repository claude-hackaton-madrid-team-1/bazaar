> Sanitised copy of `_night/REVIEW_BRIEF.md` (night shift of Fri 2 → Sat 3 Oct 2026, file state at ~07:15 Madrid).
> `[private]` marks redacted private game values (cash, card values, affinities, our price caps and bid
> ladders, album state). Numbers inside test descriptions (`tests/bites/*`, chaos runs, proof tests) are
> fixture values, not our real ones. See [../SUMMARY.md](../SUMMARY.md) and [../INDEX.md](../INDEX.md).

# Review brief (night reviewers R1 and R2)

Same hard rules as BRIEF.md (nothing live, never merge, never push to other sessions' branches, English).
You do NOT fix code yourself unless told; you find problems and route them.

## R1 — per-PR reviewer (session night-r1-reviewer)
Loop all night: every ~20 min, list night and day PRs (`gh pr list --state open --json number,title,headRefName,
baseRefName,updatedAt`), and for every PR whose head changed since your last review (track in REVIEWS.md):
1. Review the diff (`gh pr diff N`, or check out the branch in a scratch worktree of your own) at high effort:
   correctness, guardrail/kill-switch bypasses, writes that skip `guardrails.check()`, live-mode leaks
   (anything that could write to the real game by default), secrets/private values in committed files or
   public endpoints, races across processes, tick/hour unit mistakes, defaults that change today's behaviour.
2. VERIFY THE CLAIMS: re-run the PR's key numbers/tests yourself (the gate numbers in its body and
   docs/night/*.md), and check the go/no-go verdict follows from the evidence. Flag overclaims.
3. Cross-PR interactions: does it conflict with or silently break another open PR (#60 #61 #62 #68 #69 #71 #72 and
   the night PRs)? Coordinate with session night-b5-rehearsal (integration branch).
4. Write findings to /Users/mariusserban/orca/workspaces/bazaar/_night/REVIEWS.md under `## PR #N @ <sha>`:
   severity (blocker/high/medium/low), file:line, failure scenario, suggested fix. Then SendMessage the owning
   session (night-* name from ListAgents; the PR's branch tells you which) with the blockers/highs to fix.
   If the owner has exited, note "owner gone" so the orchestrator reassigns.
5. Append a STATUS.md line per review: `HH:MM r1 reviewed #N: <B blockers, H highs> ...`.

## R2 — bite hunter (session night-r2-bite-hunter)
Think adversarially about the WHOLE system for Saturday/Sunday and find cases that could bite us, then prove or
disprove each one with a test or a sim run (local bazaar-sim / fixtures only). Seed list (extend it):
- the clock pausing mid-tick, doors closing at 23:00 with open offers, the first tick after 09:00 (stale state),
  organisers changing tick pace (5–60 s) or per-tick limits mid-game (`/api/clock.limits`), schedule changes;
- server restart (tick 94 precedent), 429/503 storms, partial failures between send and ledger write;
- 15 s Sunday ticks with every agent + broker + duels + monitor on one key (5 req/s), LLM/Jev latency;
- a new persona/level appearing, a new set (RET Sat, CHA Sun), a payload shape change, unknown enum values;
- two processes on different ledgers, a laptop CLI and Railway acting at once, a redeploy mid-duel;
- adversarial rivals: injection in every text field, offers that differ from their text, venue fee hikes announced
  then applied at settlement (pending_fee), wash/ring patterns that could get US penalised;
- money: cash floor vs bond vs grants, refunds, duplicate buys, packs; scoring assumptions we haven't verified.
For each: severity, likelihood, evidence (test/sim output), and the fix or the decision Marius needs.
Write to /Users/mariusserban/orca/workspaces/bazaar/_night/BITES.md; for confirmed bugs, SendMessage the owning
night session if one owns that code, else add a BACKLOG.md item (next free B-number) for the orchestrator.
STATUS.md line per finding batch. Keep going all night; when the seed list is exhausted, invent more.
