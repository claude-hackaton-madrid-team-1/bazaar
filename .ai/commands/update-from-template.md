---
description: Pull generic cross-ai-template updates into this repo, judgment-merge .ai/context.md, re-sync, and summarize for review (never pushes)
---

Refresh this repo's GENERIC template machinery from cross-ai-template, then do the one thing the
script can't: reconcile `.ai/context.md` (new template conventions vs. this project's filled-in
values). Work strictly locally — this is a hard rule: **NEVER push, open a PR, create a remote
branch, or touch a remote.** Use plain shell + file edits only, so this degrades gracefully across
Claude Code, Codex, Gemini, and opencode (no tool-specific accelerants required).

Steps:

1. **Preview, then apply.** Run `sh scripts/update-from-template.sh --dry-run` and read the report
   so you know what will change. If the working tree is dirty, tell the user to commit/stash first
   (or re-run with `--force`) — don't silently clobber. Then run `sh scripts/update-from-template.sh`.
   - If the script isn't present yet (a repo that predates it), bootstrap it first with git, then
     run it: `git fetch https://github.com/ogarciarevett/cross-ai-template.git main &&
     git checkout FETCH_HEAD -- scripts/update-from-template.sh`.
   - Pass `--with-tooling` ONLY if the user wants root tool configs (`.mcp.json`, `opencode.json`,
     `.codex/`, `.agents/`) refreshed too — these are commonly project-customized, so default to off.

2. **Judgment-merge `.ai/context.md`.** If `.ai/context.md.incoming` exists, diff it against the
   current `.ai/context.md`
   (e.g. `git --no-pager diff --no-index .ai/context.md .ai/context.md.incoming || true`). Then edit
   `.ai/context.md`: port every NEW generic convention from the incoming version (e.g. an updated
   lifecycle reference, new Definition-of-Done gates, new source-of-truth rules) while **preserving
   every project-specific value verbatim** — the project name, the locked stack, the coverage
   threshold, the real typecheck/lint commands, and any project hard rules. Never overwrite a
   filled-in section with an `<!-- FILL -->` placeholder. When done, delete `.ai/context.md.incoming`.

3. **Re-sync.** Run `sh scripts/sync-ai-docs.sh` so every tool's generated config matches the
   refreshed sources (the script already runs it, but re-run after your context.md edits).

4. **Summarize for review.** Report concisely: which generic files changed, which project-specific
   files were preserved, any commands/skills the template no longer ships (kept, not deleted — flag
   them as manual-delete candidates), and the specific decisions you made while merging
   `.ai/context.md`.

5. **Hand off.** Remind the user to review `git status` / `git diff` and commit locally. Do not
   commit for them unless they ask. Never push.
