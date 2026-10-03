#!/usr/bin/env sh
# sync-ai-docs.sh — regenerate every per-tool agent artifact from the hand-edited sources
# of truth under `.ai/`. You only ever edit `.ai/`; everything else is generated.
#
# Pure POSIX shell + awk/sed — NO language runtime (no node/bun/python). This is what makes
# the template drop-in for a project in ANY language: the machinery needs nothing but a shell.
#
# SOURCES (hand-edited, the only files committed to git):
#   .ai/context.md             project contract
#   .ai/pipeline.md            GENERIC agent-skills lifecycle
#   .ai/memory.md              shared, committed team log (referenced, never inlined)
#   .ai/commands/<name>.md     canonical command defs (frontmatter `description` + body prompt)
#   .ai/agents/<name>.md       canonical subagent defs (md + frontmatter)
#   .ai/skills/<name>/SKILL.md canonical skills (+ bundled files)
#   .ai/references/*.md        checklists referenced by path (NOT generated)
#
# GENERATED (committed, marked linguist-generated in .gitattributes; never hand-edit — the
# pre-commit drift gate regenerates these and blocks the commit if a committed copy is stale):
#   AGENTS.md                           — INLINE full contract (canonical; plain-text readers)
#   CLAUDE.md                           — thin stub: one `@AGENTS.md` import (Claude Code resolves it)
#
# GENERATED (local-only, gitignored mirrors; never hand-edit or commit):
#   .claude/commands/*.md, .claude/agents/*.md, .claude/skills/<n>/
#
# Output is deterministic (no timestamps) so the pre-commit `git diff` only fires on real changes.
set -eu

# Resolve repo root from this script's location, so it runs from any cwd.
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

AI=".ai"

# ---------------------------------------------------------------- contract docs
# Seed the shared memory log from the template if absent (it is committed once created).
[ -f "$AI/memory.md" ] || cp "$AI/memory.example.md" "$AI/memory.md"

# Command substitution strips trailing newlines — the analogue of JS .trimEnd()/.trim() here.
banner=$(cat "$AI/templates/banner.md")
context=$(cat "$AI/context.md")
pipeline=$(cat "$AI/pipeline.md")

memory_section=$(cat <<'EOF'
## Memory

Shared working log: `.ai/memory.md` — committed and shared by the whole team (seeded from
`.ai/memory.example.md` if missing). It is not inlined here; tools that resolve imports pull it
in. Never write a secret in it:

@.ai/memory.md
EOF
)

# emit_md MODE OUTFILE
#   MODE   = inline | agentref
# inline   → banner + the full contract (context + pipeline) + the memory section.
# agentref → banner + a single `@AGENTS.md` import. AGENTS.md is the committed canonical inline
#            and already ends with the `@.ai/memory.md` reference, so the whole contract + memory
#            ride along through that one import — no duplication in CLAUDE.md.
emit_md() {
	mode=$1
	out=$2
	mkdir -p "$(dirname "$out")"
	# Drop a pre-existing symlink (e.g. an old CLAUDE.md → AGENTS.md) so we don't write through it.
	[ -L "$out" ] && rm -f "$out"
	{
		printf '%s\n\n' "$banner"
		if [ "$mode" = "agentref" ]; then
			printf '%s\n' "@AGENTS.md"
		else
			printf '%s\n\n%s\n\n' "$context" "$pipeline"
			printf '%s\n' "$memory_section"
		fi
	} >"$out"
}

emit_md inline   "$ROOT/AGENTS.md"
emit_md agentref "$ROOT/CLAUDE.md"

# ---------------------------------------------------------------- Claude Code assets
reset_generated_dir() {
	rm -rf "$1"
	mkdir -p "$1"
}

# Start the local mirrors from a clean slate so deleting from .ai/ removes stale generated copies.
# .claude/settings.local.json and any other tool-owned file under .claude/ stay untouched.
reset_generated_dir "$ROOT/.claude/commands"
reset_generated_dir "$ROOT/.claude/agents"
reset_generated_dir "$ROOT/.claude/skills"

commands=0
for f in "$AI"/commands/*.md; do
	[ -e "$f" ] || continue
	name=$(basename "$f" .md)
	[ "$name" = "README" ] && continue
	cp "$f" "$ROOT/.claude/commands/$name.md"
	commands=$((commands + 1))
done

agents=0
for f in "$AI"/agents/*.md; do
	[ -e "$f" ] || continue
	name=$(basename "$f" .md)
	[ "$name" = "README" ] && continue
	cp "$f" "$ROOT/.claude/agents/$name.md"
	agents=$((agents + 1))
done

skills=0
for d in "$AI"/skills/*/; do
	[ -d "$d" ] || continue
	name=$(basename "$d")
	cp -R "${d%/}" "$ROOT/.claude/skills/$name"
	skills=$((skills + 1))
done

# ---------------------------------------------------------------- summary
echo "sync-ai-docs: regenerated (AGENTS.md inline; CLAUDE.md @import stub)"
echo "  docs    → AGENTS.md, CLAUDE.md"
echo "  claude  → $commands commands, $agents agents, $skills skills (.claude/)"
