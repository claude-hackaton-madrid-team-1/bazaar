---
name: project-bootstrap
description: Turns the blank cross-ai-template into a project-specific setup. Interviews the user for project, locked stack, Definition-of-Done commands, and hard rules, then fills .ai/context.md (and optionally seeds .ai/specs/00-requirements.md), wires the chosen language's pre-commit/post-commit hooks, and regenerates every tool's config with `sh scripts/sync-ai-docs.sh`. Use once, right after starting from the template, while .ai/context.md still has `<!-- FILL -->` placeholders, or when the user says "bootstrap", "set up this template", or "/bootstrap".
---

# Project Bootstrap

## Overview

The template ships with a generic contract: `.ai/context.md` is full of `<!-- FILL: … -->`
markers and `.ai/specs/*` are empty skeletons. This skill turns that blank scaffold into a
project-specific setup — the way a project scaffolder turns a prompt into a configured app —
by interviewing the user, writing the answers into the source of truth, wiring the chosen
language's commit hooks, and regenerating every tool's config from it.

The template machinery itself is **100% language-agnostic** — the sync generator is a
dependency-free POSIX shell script and the git hooks are empty dispatchers. The ONLY place a
language gets baked in is the `.githooks/{pre,post}-commit.d/` scripts this skill writes during
bootstrap. Keep it that way: never add a runtime dependency to the template's own machinery.

It is the *Define-phase entry point* for a brand-new repo. It composes two existing skills:
`interview-me` (how to extract intent one question at a time) and, optionally,
`spec-driven-development` (to take the requirements all the way into `01-spec.md`).

## When to Use

- Right after cloning / starting from the template, while `.ai/context.md` still has
  `<!-- FILL -->` markers.
- The user invokes `/bootstrap`, or says "set up this template", "configure this for my stack".

**When NOT to use:** `.ai/context.md` is already filled (no FILL markers) — this is a one-time
setup. If asked to re-run, confirm first; you'd be overwriting a real contract.

## Workflow (gated)

### 1. Check state
Read `.ai/context.md`. If it has no `<!-- FILL -->` markers it's already bootstrapped — STOP and
ask the user whether they really want to overwrite it before continuing.

### 2. Interview (one question at a time, best guess attached)
Apply `interview-me`: ask ONE question at a time, each with your best guess, until you can
predict the answers. Gather, in order:

1. **Project** — what it does, who uses it, and the single dominant correctness/quality
   constraint (e.g. "per-tenant isolation", "p99 < 100 ms", "exactly-once", "no data loss").
2. **Stack (LOCKED)** — runtime/language, web framework, validation lib, datastore + ORM,
   queue/cache, logger, test runner, linter/formatter — and explicit **NO**s (what's out of
   bounds). Infer sensible defaults from any files already in the repo and confirm them.
3. **Definition of Done** — the exact commands: test runner + coverage threshold, typecheck
   command, lint/format command, plus any project-specific gates.
4. **Hard rules** — project-specific additions to the always/never list (the generic ones —
   never push/deploy, never commit secrets — are already there).
5. **Hook language** — confirm the language whose format/lint/typecheck/test commands should
   run in the git hooks. Defaults to the Stack runtime/language; ask if the repo is polyglot
   (you can wire more than one — drop a script per concern into the `.d/` dirs).
6. **Requirements (optional)** — enough to seed `00-requirements.md`, or defer to `/spec`.

Don't invent stack choices — if you can't infer one with confidence, ask.

### 3. Write `.ai/context.md`
Replace each `<!-- FILL -->` block with the gathered content. Keep it terse (it's inlined into
every tool's prompt). **Do not touch** the "Source-of-truth convention" or "Memory protocol"
sections — they're generic and must stay. Set the title `# Agent Operating Contract — <name>`.

### 4. Wire the commit hooks for the language
The git hooks ship as **empty, agnostic dispatchers**: `.githooks/pre-commit` runs the sync
drift gate then every executable in `.githooks/pre-commit.d/`; `.githooks/post-commit` runs
every executable in `.githooks/post-commit.d/`. **Do not edit those dispatcher files.** Instead
write small executable scripts into the `.d/` dirs using the project's Definition-of-Done
commands (see "Language hook recipes" below for defaults):

- `.githooks/pre-commit.d/10-format.sh` — formatter check on staged files (must block).
- `.githooks/pre-commit.d/20-lint.sh` — linter.
- `.githooks/pre-commit.d/30-typecheck.sh` — typecheck (skip for languages without one).
- `.githooks/post-commit.d/10-test.sh` — test suite (reporting only; cannot block).

Each script: start with `#!/usr/bin/env sh`, exit non-zero to fail (pre-commit only), and
`chmod +x` it (the dispatcher skips non-executable files). Prefer staged-file scoping for
pre-commit speed: `git diff --cached --name-only --diff-filter=ACM`. Then append the language's
ignore patterns to `.gitignore` (e.g. `node_modules/`, `target/`, `__pycache__/`, `vendor/`).

### 5. Seed requirements (optional)
If you gathered requirements, write them verbatim into `.ai/specs/00-requirements.md` (replace
the skeleton). Otherwise tell the user to run `/spec` next. For a full spec now, hand off to
`spec-driven-development`.

### 6. Regenerate & install hooks
Run `sh scripts/install.sh` — it wires `git config core.hooksPath .githooks`, marks the hooks
executable, and runs the sync. (If hooks are already installed, `sh scripts/sync-ai-docs.sh`
alone suffices.) This rewrites `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.ai/generated/rules.mdc`,
and every tool mirror from the new sources.

### 7. Confirm & hand off
Show what changed (the new contract, the wired hooks, the sync summary), remind the human to
commit (the agent never pushes), and point to the next step: `/spec → /plan → /build → /test → /review`.

## Language hook recipes

Sensible defaults per language — confirm against the project's actual tooling, and always
prefer the exact commands the user gave for the Definition of Done. `<staged>` stands for
`$(git diff --cached --name-only --diff-filter=ACM -- '<glob>')`.

| Language | pre-commit: format | pre-commit: lint | pre-commit: typecheck | post-commit: test |
|----------|--------------------|------------------|-----------------------|-------------------|
| TypeScript/JS | `prettier --check <staged>` | `eslint <staged>` | `tsc --noEmit` | `npm test` (or `bun/pnpm/yarn test`) |
| Python | `black --check <staged>` / `ruff format --check` | `ruff check <staged>` | `mypy .` | `pytest -q` |
| Go | `gofmt -l <staged>` (fail if non-empty) | `go vet ./...` | — (compiler) | `go test ./...` |
| Rust | `cargo fmt --check` | `cargo clippy -- -D warnings` | — (compiler) | `cargo test` |
| Java | `mvn spotless:check` / `gradle spotlessCheck` | — | — (compiler) | `mvn test` / `gradle test` |
| Kotlin | `ktlint <staged>` / `gradle ktlintCheck` | `gradle detekt` | — (compiler) | `gradle test` |
| C++ | `clang-format --dry-run --Werror <staged>` | `clang-tidy <staged>` | — (compiler) | `ctest` |
| PHP | `php-cs-fixer fix --dry-run` | `phpstan analyse` | `phpstan` | `phpunit` / `pest` |
| Swift | `swiftformat --lint <staged>` | `swiftlint` | — (compiler) | `swift test` |

Use the runtime the user actually has (e.g. `bun test` vs `npm test`). Skip a column when the
language has no separate tool for it (compiled languages typecheck during build/test). Example
`.githooks/pre-commit.d/20-lint.sh` for Go:

```sh
#!/usr/bin/env sh
set -eu
go vet ./...
```

## Rules
1. One-time, gated: never overwrite an already-filled `.ai/context.md` without explicit confirmation.
2. Never invent the stack — infer-and-confirm, or ask.
3. Never write secrets into `context.md` or `memory.md`.
4. Keep `context.md` terse — it is loaded into every agent's context on every session.
5. Wire the commit hooks via the `.githooks/{pre,post}-commit.d/` dirs ONLY — never hard-code a
   language into the dispatcher hooks or the sync script; the template machinery stays agnostic.
6. Always finish by running the sync (via `sh scripts/install.sh`), so the generated configs
   match the new contract and the hooks are wired.

## Output
A filled `.ai/context.md` (+ optionally `00-requirements.md`), language-specific commit hooks in
`.githooks/{pre,post}-commit.d/`, and regenerated per-tool configs, ready to commit. The repo now
follows the project's own contract — with enforced quality gates — in every supported AI tool.
