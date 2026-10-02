---
name: evolve
description: "Scan the repo and evolve the .ai/ source-of-truth. Diffs code-reality against .ai/context.md + .ai/specs/ and writes proposed evolution patches to .ai/specs/97-evolution.md for a human to apply. Works in every CLI; uses a /graphify knowledge graph as a Claude-Code-only accelerant with a direct-scan fallback everywhere else, and fans the per-dimension checks across the tool's native sub-agents. Use when the docs may have drifted from the code, after a big feature lands, or on a cadence."
---

# /evolve — keep the contract in sync with the code

The `.ai/` docs are the source of truth, but code is what actually runs. Over time they
drift: a dependency changes, a command stops being used, a behavior ships unspecced.
`/evolve` closes that loop — it reads the **code as ground truth**, compares it to the
`.ai/` contract and specs, and proposes concrete patches. It **never edits `.ai/` sources
or commits** — a human reviews `.ai/specs/97-evolution.md` and applies what's right
(hard rule: durable contract changes are human-reviewed; the human pushes).

> **Runs in every CLI.** One part is a Claude-Code-only *accelerant*, not a requirement:
> the `/graphify` knowledge graph (step 1). When it's unavailable — other CLIs, or Claude
> Code without graphify installed — the skill falls back to a direct repo scan. The
> per-dimension fan-out (step 2) always uses the tool's native sub-agents. Same output,
> shallower-but-honest coverage.

## When to use it

- After a sizeable feature or refactor lands, before the next planning cycle.
- When `/acceptance` or `/consensus-review` keeps citing things the specs don't mention.
- On a cadence (e.g. weekly) to stop slow drift from accumulating.
- NOT for a single small change — that's what `/review` and the per-slice doc-sync at the
  end of `/build` already cover.

## Steps

### 1. Map reality — knowledge graph (Claude Code) or direct scan (any CLI)

A graph makes evolve cheap to repeat and good at finding *cross-file* drift (e.g. a config
key read in three places but documented in none). Check for one first — it's the expensive part:

```bash
ls graphify-out/graph.json 2>/dev/null
```

- **Graph exists** → reuse it; optionally refresh with `/graphify . --update`. `hasGraph = true`.
- **No graph, `/graphify` available (Claude Code)** → build one: `/graphify .`. `hasGraph = true`.
- **No graphify (other CLIs, or not installed)** → skip it; read the repo directly with
  Grep/Glob/Read. `hasGraph = false`. Note in the report that coverage was a direct scan.

### 2. Scan for drift across five dimensions

Compare ground truth to the matching `.ai/` source on each of: **contract** (context.md),
**stack** (deps / build-test-lint commands), **specs** (00/01/02 vs implementation),
**pipeline** (commands / skills / pipeline.md), **surface** (new interfaces, env vars,
security-relevant inputs/secret handling).

Fan out one analyst per dimension across the tool's native parallelism — an **Agent Team** in
**Claude Code**, the tool's **sub-agents** in **Codex / Gemini / opencode** (or run them
sequentially if the run is small), per the "Parallel work" section of `.ai/pipeline.md`. Brief
each with the dimension's `.ai/` source + the actual files; collect a ranked `drift` list and
touch no file during the scan.

Either way, propose patches **only to `.ai/` sources** — never to generated files.

### 3. Write `.ai/specs/97-evolution.md`

Materialize the drift list into the report below. One row per drift item.

```markdown
# Evolution proposals — <YYYY-MM-DD>

Source: `/evolve`. Reality map: <graphify-out/graph.json | direct scan>.
These are PROPOSALS. A human reviews and applies; then run `sh scripts/sync-ai-docs.sh`.

## Drift table
| # | Dimension | Where (ground truth)   | Observed (code)        | Documented (.ai/)   | Target | Confidence |
|---|-----------|------------------------|------------------------|---------------------|--------|------------|
| 1 | stack     | <manifest/lockfile>:12 | actual test runner cmd | names a different one | .ai/context.md | high |

## Proposed patches
### 1 — .ai/context.md · Stack (high)
- **Drift:** Definition of Done names one test runner; the repo's manifest/CI runs another.
- **Patch:** Replace the test-command line under "Definition of Done" with the real command.

## Out of scope / low confidence
- <items dropped or deferred, with one-line rationale>

## Verdict
<one line: how many high-confidence patches, and the single highest-leverage one to apply first>
```

### 4. Hand off

Stop after writing the report. Tell the human what's there and that applying any patch
means editing the `.ai/` source then running `sh scripts/sync-ai-docs.sh` (the pre-commit drift gate
will confirm the generated docs regenerated cleanly). Record any environment quirk you hit
in `.ai/memory.md`.

## Notes

- **Ground truth wins.** When code and `.ai/` disagree, the report describes reality and
  proposes bringing the docs to it — unless the doc encodes an intentional rule the code
  violates, in which case flag it as a *code* bug instead, not a doc patch.
- **Idempotent.** Re-running with no real drift yields an empty drift list and a "no
  drift" verdict — safe to schedule.
- The sub-agent fan-out path is documented in the "Parallel work" section of
  `.ai/pipeline.md`. For how spec edits feed back into the lifecycle, see
  `agent-skills:spec-driven-development`.
