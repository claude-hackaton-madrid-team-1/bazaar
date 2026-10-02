#!/usr/bin/env sh
# install.sh — one-time, dependency-free setup for the cross-ai-template machinery.
# Replaces the old npm `prepare` script. Run once per clone:
#
#   sh scripts/install.sh
#
# It does three things, all idempotent:
#   1. Point git at the committed hooks (`core.hooksPath .githooks`).
#   2. Make the hooks and scripts executable.
#   3. Run the sync once so every tool's config matches `.ai/`.
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

# 1. Wire the hooks directory.
git config core.hooksPath .githooks
echo "✓ core.hooksPath → .githooks"

# 2. Ensure everything that must run is executable.
chmod +x scripts/sync-ai-docs.sh scripts/install.sh scripts/update-from-template.sh 2>/dev/null || true
[ -f .githooks/pre-commit ] && chmod +x .githooks/pre-commit
[ -f .githooks/post-commit ] && chmod +x .githooks/post-commit
echo "✓ hooks + scripts executable"

# 3. Generate the per-tool configs from .ai/.
sh scripts/sync-ai-docs.sh

echo "✓ install complete — next: run /bootstrap in your AI tool, or edit .ai/context.md by hand."
