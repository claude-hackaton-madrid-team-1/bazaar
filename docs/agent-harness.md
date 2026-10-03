# Agent harness (Claude Code)

One operating contract, lifecycle, set of skills, personas and commands for Claude Code, from a
single source of truth. This is a Claude hackathon: Claude Code is the only supported tool.

You edit `.ai/`. A single generator (`scripts/sync-ai-docs.sh`, **dependency-free POSIX shell**,
no node/bun/python) materializes the Claude Code outputs. The pre-commit hook blocks a contract
edit that was not re-synced.

## How it works

```
.ai/                      ← the ONLY files you hand-edit (source of truth)
 ├─ context.md            project contract (stack, Definition of Done, hard rules)
 ├─ pipeline.md           the generic spec→plan→build→test→review lifecycle
 ├─ commands/*.md         slash-command definitions
 ├─ agents/*.md           sub-agent personas (code / security / performance / test / PR review)
 ├─ skills/*/SKILL.md     reusable workflow skills (TDD, code review, CI/CD, …)
 ├─ references/*.md       checklists the skills/personas cite
 ├─ specs/*.md            project specs (00–99) + per-task specs (A1, A2, … or external ids)
 ├─ templates/banner.md   banner stamped on the generated contract files
 └─ memory.md             shared, committed team log (seeded from memory.example.md)

      │   sh scripts/sync-ai-docs.sh   (POSIX shell, deterministic)
      ▼
AGENTS.md · CLAUDE.md          ← committed contract entry files (AGENTS.md inline, CLAUDE.md `@AGENTS.md` stub)
.claude/{commands,agents,skills}  ← local-only copies of the .ai/ sources: GITIGNORED, regenerated
```

**Never edit a generated file**: your edit is overwritten on the next sync. The `.claude/` mirrors
are gitignored, so the repo holds only the `.ai/` source: **run `sh scripts/install.sh` once after
cloning** or Claude Code sees zero skills, agents and commands. `AGENTS.md` and `CLAUDE.md` stay
committed (readable on GitHub, the contract loads on a fresh clone without a build step); the
pre-commit hook gates those. Change a convention in `.ai/`, run `sh scripts/sync-ai-docs.sh`, commit.

## Quick start (a fresh clone)

```sh
sh scripts/install.sh   # git config core.hooksPath .githooks + chmod + first sync
```

Then open the repo in Claude Code. Fill or change the contract by editing `.ai/context.md` (or run
`/bootstrap` on a blank template), then `sh scripts/sync-ai-docs.sh`.

## Specs

`.ai/specs/` holds project-level docs with the numeric scheme (`00-requirements.md` → `01-spec.md`
→ `02-plan.md` → `99-acceptance.md`); per-task specs use a letter+number id. Let the agent write
them: `/spec` fixes the task id and writes `.ai/specs/<id>-spec.md` (or cites the ticket), `/plan`
records the steps in `.ai/specs/02-plan.md`.

### Per-task spec ids (and when to skip them)

- **External backlog (Linear or GitHub Projects) → the tracker is the source of truth.** The task
  id is the external one (`ENG-421`, `#123`), the ticket body is the spec, and no local spec file
  is created.
- **No external backlog → the spec is saved in the repo** as `.ai/specs/<id>-spec.md` with a
  letter+number id (`A1-spec.md`, `B1-spec.md`, …). `02-plan.md` is the local backlog index.

`/spec` detects the source: GitHub Projects via `gh project list`; Linear via a configured Linear
MCP server / `LINEAR_API_KEY` / `eng-123` branch keys. Full rule:
[`.ai/context.md`](../.ai/context.md) → "Task identity & spec source".

### Honest Implementation Metric (anti-overclaim)

Every task closes with an **Honest Implementation Report** (emitted by `/build`, rolled up by
`/acceptance`). Each acceptance criterion gets a status (✅ verified · ⚠️ partial · 🔧 stubbed ·
❌ not done · 🚫 blocked) that is **✅ only if the proving command output is pasted inline**. The
metric is `verified ✅ ÷ total`, plus explicit *Unverified* and *Could-not-do* lists. Over-claiming
is a contract violation; under-claiming is fine. Full rule:
[`.ai/context.md`](../.ai/context.md) → "Honesty protocol".

## The lifecycle

Per task: `/spec → /plan → /build → /test → /review`. Once, at the end:
`/consensus-review → /code-simplify → /ship → /acceptance → /goal`. Optional, on a cadence:
`/evolve` re-syncs the `.ai/` contract to the code (`/graphify` accelerates it). Full definition:
[`.ai/pipeline.md`](../.ai/pipeline.md).

Parallel fan-out (the `/consensus-review` 2-of-3 panel, file-disjoint slices, `/evolve`'s
per-dimension scan) runs on Claude Code sub-agents or Agent Teams, with no committed scripts. See
"Parallel work" in [`.ai/pipeline.md`](../.ai/pipeline.md).

## Per-stage model routing (optional)

A command or persona may add a `model:` key to its frontmatter to pin the Claude model for that
stage (for example a cheaper model for mechanical stages). Keep the committed sources free of
hard-coded model ids unless a stage really needs one.

## Memory

`.ai/memory.md` is the shared, committed team log: terse `symptom → root cause → fix` entries.
Durable decisions also go in the commit message or `docs/adr/`. Never write secrets.

## Keeping it in sync

- `sh scripts/sync-ai-docs.sh`: regenerate `AGENTS.md`, `CLAUDE.md` and the `.claude/` mirrors.
- `sh scripts/sync-ai-docs.sh && git diff --exit-code -- AGENTS.md CLAUDE.md`: regenerate and fail
  if a committed file drifted (the pre-commit hook does this).
- `.githooks/pre-commit` runs the sync gate once `git config core.hooksPath .githooks` is set (done
  by `sh scripts/install.sh`), then every executable in `.githooks/pre-commit.d/`.
- `scripts/update-from-template.sh` and `/update-from-template` pull generic updates from the
  upstream cross-ai-template. The upstream template still ships other tools' files, so review the
  diff before committing.

## Commit hooks

`pre-commit` runs the sync drift gate, then every executable in `.githooks/pre-commit.d/`
(`10-readme-status`, `20-python-lint`); `post-commit` runs `.githooks/post-commit.d/`.
