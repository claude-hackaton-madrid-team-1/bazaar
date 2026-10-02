#!/usr/bin/env sh
# update-from-template.sh — pull GENERIC updates from cross-ai-template into a downstream repo.
#
# A repo created FROM cross-ai-template customizes essentially ONE source file —
# `.ai/context.md` (its project contract) — and leaves everything else generic. This script
# refreshes the generic half (the lifecycle, commands, agents, skills, the sync machinery and
# the dispatcher hooks) from the template, NEVER touching your project-specific files, then
# re-runs `sh scripts/sync-ai-docs.sh` so every tool's generated config matches again.
#
# It can't auto-merge `.ai/context.md` (that interleaves new template conventions with YOUR
# filled-in values), so it saves the template's version as `.ai/context.md.incoming` for you —
# or the `/update-from-template` agent command — to reconcile by hand.
#
# Pure POSIX shell + git — NO node/python/bun. (The `npm/` wrapper is optional sugar over this.)
#
# USAGE (from inside your downstream repo):
#   sh scripts/update-from-template.sh [options]
#
# OPTIONS:
#   --ref <branch|tag|sha>   template ref to pull            (default: main)
#   --template <git-url>     template repo URL               (default: canonical GitHub HTTPS)
#   --source <dir>           use an already-cloned template (skip clone; used by the npx wrapper)
#   --target <dir>           downstream repo to update       (default: current git repo root)
#   --with-tooling           ALSO refresh root tool configs (.mcp.json, opencode.json, .codex/,
#                            .agents/) — OFF by default, since a project usually customizes these
#   --dry-run                print what WOULD change; touch nothing
#   --force                  proceed even if the target working tree is dirty
#   -h, --help               show this help
#
# It NEVER pushes, commits, or touches a remote — you review the diff and commit locally.
set -eu

TEMPLATE_URL="https://github.com/ogarciarevett/cross-ai-template.git"

usage() {
	sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
}

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

main() {
	REF="main"
	SOURCE=""
	TARGET=""
	WITH_TOOLING=0
	DRY_RUN=0
	FORCE=0

	while [ $# -gt 0 ]; do
		case "$1" in
			--ref) REF="${2:?--ref needs a value}"; shift 2;;
			--template) TEMPLATE_URL="${2:?--template needs a value}"; shift 2;;
			--source) SOURCE="${2:?--source needs a value}"; shift 2;;
			--target) TARGET="${2:?--target needs a value}"; shift 2;;
			--with-tooling) WITH_TOOLING=1; shift;;
			--dry-run) DRY_RUN=1; shift;;
			--force) FORCE=1; shift;;
			-h|--help) usage; exit 0;;
			*) die "unknown option: $1 (try --help)";;
		esac
	done

	command -v git >/dev/null 2>&1 || die "git is required on PATH"

	# ---- resolve + guard the TARGET (the downstream repo) ----------------------
	if [ -z "$TARGET" ]; then
		TARGET=$(git rev-parse --show-toplevel 2>/dev/null) \
			|| die "not inside a git repo — run from your repo, or pass --target <dir>"
	fi
	[ -d "$TARGET" ] || die "target is not a directory: $TARGET"
	TARGET=$(CDPATH='' cd -- "$TARGET" && pwd)
	for need in .ai/context.md .ai/pipeline.md scripts/sync-ai-docs.sh; do
		[ -f "$TARGET/$need" ] \
			|| die "$TARGET is not a cross-ai-template descendant (missing $need). Adopt the template first."
	done

	# ---- refuse to clobber uncommitted work (so the diff stays reviewable) -----
	if [ "$DRY_RUN" -eq 0 ] && [ "$FORCE" -eq 0 ]; then
		[ -z "$(git -C "$TARGET" status --porcelain 2>/dev/null)" ] \
			|| die "target working tree is dirty — commit or stash first (or pass --force)."
	fi

	# ---- obtain the SOURCE (template) -----------------------------------------
	WORK=$(mktemp -d 2>/dev/null) || die "mktemp failed"
	CLONE=""
	trap 'rm -rf "$WORK" ${CLONE:+"$CLONE"}' EXIT INT TERM
	if [ -n "$SOURCE" ]; then
		SOURCE=$(CDPATH='' cd -- "$SOURCE" && pwd)
	else
		CLONE=$(mktemp -d 2>/dev/null) || die "mktemp failed"
		printf '→ cloning %s @ %s ...\n' "$TEMPLATE_URL" "$REF" >&2
		git clone --quiet --depth 1 --branch "$REF" "$TEMPLATE_URL" "$CLONE" \
			|| die "clone failed ($TEMPLATE_URL @ $REF)"
		SOURCE="$CLONE"
	fi
	[ -f "$SOURCE/.ai/context.md" ] || die "source is not cross-ai-template: $SOURCE"
	SRC_REF=$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "$REF")

	REPORT="$WORK/changed"; : >"$REPORT"
	EXTRAS="$WORK/extras"; : >"$EXTRAS"

	# ---- copy helpers (copy-if-different; record, never churn unchanged) -------
	# These run inside `find | while` subshells, so they communicate via files, not vars.
	copy_file() {  # copy_file <relpath>
		_rel=$1; _s="$SOURCE/$_rel"; _d="$TARGET/$_rel"
		[ -f "$_s" ] || return 0
		if [ -f "$_d" ] && cmp -s "$_s" "$_d"; then return 0; fi
		echo "$_rel" >>"$REPORT"
		[ "$DRY_RUN" -eq 1 ] && return 0
		mkdir -p "$(dirname "$_d")"
		cp -p "$_s" "$_d"
	}
	mirror_dir() {  # mirror_dir <reldir> — additive: copy new/changed files, never delete
		_rel=$1; _s="$SOURCE/$_rel"; _d="$TARGET/$_rel"
		[ -d "$_s" ] || return 0
		( cd "$_s" && find . -type f ) | while IFS= read -r _f; do
			copy_file "$_rel/${_f#./}"
		done
		# Report files the downstream has but the template no longer ships (kept, not deleted).
		if [ -d "$_d" ]; then
			( cd "$_d" && find . -type f ) | while IFS= read -r _f; do
				[ -f "$_s/${_f#./}" ] || echo "$_rel/${_f#./}" >>"$EXTRAS"
			done
		fi
	}

	# ---- GENERIC sources (always refreshed) -----------------------------------
	copy_file .ai/pipeline.md
	copy_file .ai/memory.example.md
	copy_file .ai/AGENT-SKILLS-LICENSE
	mirror_dir .ai/commands
	mirror_dir .ai/agents
	mirror_dir .ai/skills
	mirror_dir .ai/references
	mirror_dir .ai/templates          # banner.md / cursor.header.mdc — sync `cat`s these; required
	for _t in "$SOURCE"/.ai/specs/_*; do   # underscore-prefixed spec TEMPLATES, not numbered specs
		[ -f "$_t" ] && copy_file ".ai/specs/$(basename "$_t")"
	done
	copy_file scripts/sync-ai-docs.sh
	mirror_dir scripts/lib
	copy_file scripts/install.sh
	copy_file .githooks/pre-commit
	copy_file .githooks/post-commit

	# ---- root tool configs: opt-in, since projects customize them --------------
	if [ "$WITH_TOOLING" -eq 1 ]; then
		copy_file .mcp.json
		copy_file opencode.json
		copy_file .codex/config.toml
		mirror_dir .agents
	fi

	# ---- the project contract: surface, never auto-merge -----------------------
	if cmp -s "$SOURCE/.ai/context.md" "$TARGET/.ai/context.md"; then
		CTX=identical
	else
		CTX=diverged
		[ "$DRY_RUN" -eq 0 ] && cp -p "$SOURCE/.ai/context.md" "$TARGET/.ai/context.md.incoming"
	fi

	# ---- record provenance + regenerate every tool's config --------------------
	if [ "$DRY_RUN" -eq 0 ]; then
		printf 'template=%s\nref=%s\nsha=%s\n' "$TEMPLATE_URL" "$REF" "$SRC_REF" >"$TARGET/.ai/.template-ref"
		printf '→ regenerating tool configs (sync-ai-docs.sh) ...\n' >&2
		( cd "$TARGET" && sh scripts/sync-ai-docs.sh >/dev/null ) || die "sync failed after update"
	fi

	# ---- summary --------------------------------------------------------------
	_verb="changed"; [ "$DRY_RUN" -eq 1 ] && _verb="would change"
	printf '\ncross-ai-template update — template @ %s%s\n' "$SRC_REF" \
		"$( [ "$DRY_RUN" -eq 1 ] && echo '  (dry run)' )"
	printf '  generic files %s: %s\n' "$_verb" "$(wc -l <"$REPORT" | tr -d ' ')"
	[ -s "$REPORT" ] && sort -u "$REPORT" | sed 's/^/    • /'
	if [ -s "$EXTRAS" ]; then
		printf '  template no longer ships (kept — delete manually if intended):\n'
		sort -u "$EXTRAS" | sed 's/^/    • /'
	fi
	case "$CTX" in
		identical) printf '  .ai/context.md: already matches the template — no merge needed\n';;
		diverged)  printf '  .ai/context.md: template version saved to .ai/context.md.incoming — reconcile, then delete it\n';;
	esac
	printf '  preserved (yours): .ai/context.md, .ai/specs/[0-9]*, scripts/opencode-model-routing.sh, .githooks/*.d/, .gitignore, .gitattributes\n'
	if [ "$DRY_RUN" -eq 0 ]; then
		printf '\nNext: review with `git status` / `git diff`, reconcile .ai/context.md, then COMMIT LOCALLY.\n'
		printf '      This script NEVER pushes. Tip: run /update-from-template to let the agent merge .ai/context.md.\n'
	fi

	# ---- self-update LAST: overwrite the running file as the final action ------
	# main()'s body is fully parsed in memory, so replacing scripts/update-from-template.sh now
	# is safe; we exit immediately after.
	if [ "$DRY_RUN" -eq 0 ] && [ -f "$SOURCE/scripts/update-from-template.sh" ] \
		&& ! cmp -s "$SOURCE/scripts/update-from-template.sh" "$TARGET/scripts/update-from-template.sh"; then
		cp -p "$SOURCE/scripts/update-from-template.sh" "$TARGET/scripts/update-from-template.sh"
	fi
	exit 0
}

main "$@"
