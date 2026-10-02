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
#   .ai/memory.md              LOCAL, gitignored per-dev log (referenced, never inlined)
#   .ai/commands/<name>.md     canonical command defs (frontmatter `description` + body prompt)
#   .ai/agents/<name>.md       canonical subagent defs (md + frontmatter)
#   .ai/skills/<name>/SKILL.md canonical skills (+ bundled files)
#   .ai/references/*.md        checklists referenced by path (NOT generated)
#
# GENERATED (committed, but marked linguist-generated in .gitattributes; never hand-edit — the
# pre-commit drift gate regenerates these and blocks the commit if a committed copy is stale):
#   AGENTS.md                           — INLINE full contract (canonical; plain-text readers)
#   CLAUDE.md, GEMINI.md                — thin stubs: one `@AGENTS.md` import (Claude/Gemini resolve it)
#
# GENERATED (local-only, gitignored mirrors/adapters; never hand-edit or commit):
#   .ai/generated/rules.mdc             — cursor-friendly INLINE contract twin
#   .cursor/rules/00-context.mdc        — symlink → .ai/generated/rules.mdc
#   .claude/commands/*.md, .opencode/commands/*.md (copy) + .gemini/commands/*.toml (transform)
#   .claude/agents/*.md, .opencode/agents/*.md, .gemini/agents/*.md (copy)
#   .claude/skills/<n>/ (Claude + opencode) + .gemini/skills/<n>/ (Gemini)
#   .agents/, .codex/agents/            — Codex may create these runtime adapters itself
#
# `--all-inline` forces the contract stubs (CLAUDE/GEMINI) to inline too. Output is
# deterministic (no timestamps) so the pre-commit `git diff` only fires on real changes.
set -eu

# Resolve repo root from this script's location, so it runs from any cwd.
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

AI=".ai"
EXTRACT="$SCRIPT_DIR/lib/extract.awk"
INJECT_MODEL="$SCRIPT_DIR/lib/inject-model.awk"
SQ="'"

# Optional per-stage model routing for the opencode runtime ONLY. Default = no-op, so the
# template stays vendor-neutral; scripts/opencode-model-routing.sh overrides this if present.
# It stamps `model:` into the .opencode/ copies only — the .claude/.codex/.gemini mirrors are
# vendor-anchored and must never receive a foreign provider id.
opencode_model_for() { echo ""; }
ROUTING="$SCRIPT_DIR/opencode-model-routing.sh"
[ -f "$ROUTING" ] && . "$ROUTING"

ALL_INLINE=0
for arg in "$@"; do
	[ "$arg" = "--all-inline" ] && ALL_INLINE=1
done

# ---------------------------------------------------------------- contract docs
# Seed the local, gitignored memory log from the committed template if absent.
[ -f "$AI/memory.md" ] || cp "$AI/memory.example.md" "$AI/memory.md"

# Command substitution strips trailing newlines — the analogue of JS .trimEnd()/.trim() here.
banner=$(cat "$AI/templates/banner.md")
cursor_header=$(cat "$AI/templates/cursor.header.mdc")
context=$(cat "$AI/context.md")
pipeline=$(cat "$AI/pipeline.md")

memory_section=$(cat <<'EOF'
## Memory

Shared working log: `.ai/memory.md` — LOCAL and gitignored (seed from
`.ai/memory.example.md`; `sh scripts/sync-ai-docs.sh` seeds it for you). It is not inlined here;
tools that resolve imports pull it in, and opencode reads it directly:

@.ai/memory.md
EOF
)

# emit_md MODE CURSOR OUTFILE
#   MODE   = inline | agentref   (agentref falls back to inline under --all-inline)
#   CURSOR = 1 to prepend the cursor .mdc header, else 0
# inline   → banner + the full contract (context + pipeline) + the memory section.
# agentref → banner + a single `@AGENTS.md` import. AGENTS.md is the committed canonical inline
#            and already ends with the `@.ai/memory.md` reference, so the whole contract + memory
#            ride along through that one import — no duplication in CLAUDE.md / GEMINI.md.
emit_md() {
	mode=$1
	cursor=$2
	out=$3
	mkdir -p "$(dirname "$out")"
	# Drop a pre-existing symlink (e.g. an old CLAUDE.md → AGENTS.md) so we don't write through it.
	[ -L "$out" ] && rm -f "$out"
	{
		[ "$cursor" = "1" ] && printf '%s\n\n' "$cursor_header"
		printf '%s\n\n' "$banner"
		if [ "$mode" = "agentref" ] && [ "$ALL_INLINE" != "1" ]; then
			printf '%s\n' "@AGENTS.md"
		else
			printf '%s\n\n%s\n\n' "$context" "$pipeline"
			printf '%s\n' "$memory_section"
		fi
	} >"$out"
}

emit_md inline   0 "$ROOT/AGENTS.md"
emit_md inline   1 "$AI/generated/rules.mdc"
emit_md agentref 0 "$ROOT/CLAUDE.md"
emit_md agentref 0 "$ROOT/GEMINI.md"

# .cursor/rules/00-context.mdc → symlink to the generated cursor artifact.
mkdir -p "$ROOT/.cursor/rules"
rm -f "$ROOT/.cursor/rules/00-context.mdc"
ln -s "../../.ai/generated/rules.mdc" "$ROOT/.cursor/rules/00-context.mdc"

# ---------------------------------------------------------------- per-tool assets
# Backslash-escape then double-quote for a TOML basic string.
toml_basic() {
	printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}
# Backslash-escape then escape any embedded `"""` for a TOML multiline string.
toml_multiline_body() {
	printf '%s' "$1" | sed 's/\\/\\\\/g; s/"""/\\"\\"\\"/g'
}

reset_generated_dir() {
	rm -rf "$1"
	mkdir -p "$1"
}

# Start local mirrors from a clean slate so deleting from .ai/ removes stale generated copies.
# Keep tool-owned config/cache roots such as .claude/settings.local.json, .codex/config.toml,
# .codex/agents, and .agents untouched.
reset_generated_dir "$ROOT/.claude/commands"
reset_generated_dir "$ROOT/.claude/agents"
reset_generated_dir "$ROOT/.claude/skills"
reset_generated_dir "$ROOT/.gemini/commands"
reset_generated_dir "$ROOT/.gemini/agents"
reset_generated_dir "$ROOT/.gemini/skills"
reset_generated_dir "$ROOT/.opencode/commands"
reset_generated_dir "$ROOT/.opencode/agents"

# Commands → Claude/opencode (copy raw) + Gemini (md → toml).
commands=0
for f in "$AI"/commands/*.md; do
	[ -e "$f" ] || continue
	name=$(basename "$f" .md)
	[ "$name" = "README" ] && continue
	mkdir -p "$ROOT/.claude/commands" "$ROOT/.opencode/commands" "$ROOT/.gemini/commands"
	cp "$f" "$ROOT/.claude/commands/$name.md"
	# opencode copy: stamp the routed model into its frontmatter (plain copy if unrouted).
	ocm=$(opencode_model_for "$name")
	if [ -n "$ocm" ]; then
		awk -v MODEL="$ocm" -f "$INJECT_MODEL" "$f" >"$ROOT/.opencode/commands/$name.md"
	else
		cp "$f" "$ROOT/.opencode/commands/$name.md"
	fi
	desc=$(awk -v what=desc -v sq="$SQ" -f "$EXTRACT" "$f")
	[ -n "$desc" ] || desc="$name command"
	body=$(awk -v what=body -f "$EXTRACT" "$f")
	{
		printf 'description = %s\n' "$(toml_basic "$desc")"
		printf 'prompt = """\n'
		printf '%s\n' "$(toml_multiline_body "$body")"
		printf '"""\n'
	} >"$ROOT/.gemini/commands/$name.toml"
	commands=$((commands + 1))
done

# Agents → Claude/opencode/Gemini (copy; shared frontmatter, each tool ignores unknown keys).
agents=0
for f in "$AI"/agents/*.md; do
	[ -e "$f" ] || continue
	name=$(basename "$f" .md)
	[ "$name" = "README" ] && continue
	mkdir -p "$ROOT/.claude/agents" "$ROOT/.opencode/agents" "$ROOT/.gemini/agents"
	cp "$f" "$ROOT/.claude/agents/$name.md"
	cp "$f" "$ROOT/.gemini/agents/$name.md"
	# opencode copy: stamp the routed model into its frontmatter (plain copy if unrouted).
	ocm=$(opencode_model_for "$name")
	if [ -n "$ocm" ]; then
		awk -v MODEL="$ocm" -f "$INJECT_MODEL" "$f" >"$ROOT/.opencode/agents/$name.md"
	else
		cp "$f" "$ROOT/.opencode/agents/$name.md"
	fi
	agents=$((agents + 1))
done

# Skills → .claude/skills (Claude + opencode read it) + .gemini/skills (Gemini). Fresh copy.
skills=0
if [ -d "$AI/skills" ]; then
	for d in "$AI"/skills/*/; do
		[ -d "$d" ] || continue
		name=$(basename "$d")
		src="${d%/}"
		mkdir -p "$ROOT/.claude/skills" "$ROOT/.gemini/skills"
		rm -rf "$ROOT/.claude/skills/$name"
		cp -R "$src" "$ROOT/.claude/skills/$name"
		rm -rf "$ROOT/.gemini/skills/$name"
		cp -R "$src" "$ROOT/.gemini/skills/$name"
		skills=$((skills + 1))
	done
fi

# ---------------------------------------------------------------- summary
if [ "$ALL_INLINE" = "1" ]; then
	echo "sync-ai-docs: regenerated (contract forced --all-inline)"
else
	echo "sync-ai-docs: regenerated (AGENTS/cursor inline; CLAUDE/GEMINI @import stubs)"
fi
echo "  docs    → AGENTS.md, CLAUDE.md, GEMINI.md, .ai/generated/rules.mdc (+ .cursor symlink)"
echo "  commands→ $commands × {.claude, .opencode (md), .gemini (toml)}"
echo "  agents  → $agents × {.claude, .opencode, .gemini}"
echo "  skills  → $skills × {.claude/skills, .gemini/skills}"
