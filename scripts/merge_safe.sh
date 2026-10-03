#!/bin/sh
# Merge a PR to main only while the deploy guard says it is safe (a merge redeploys bazaar-duels on Railway).
# Usage: scripts/merge_safe.sh <pr-number>
set -u

pr="${1:-}"
case "$pr" in
  '' | *[!0-9]*)
    echo "usage: scripts/merge_safe.sh <pr-number> (digits only, got '${pr}')" >&2
    exit 2
    ;;
esac

status=0
uv run bazaar deploy-guard || status=$?
if [ "$status" -eq 0 ]; then
  exec gh pr merge "$pr" --merge
fi
echo "NOT merging PR #$pr: the deploy guard said no (exit $status). Wait for the next safe tick it printed and re-run." >&2
exit "$status"
