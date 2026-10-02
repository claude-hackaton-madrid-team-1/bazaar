# opencode-model-routing.sh — per-stage model routing for the OPENCODE runtime ONLY.
#
# Sourced by scripts/sync-ai-docs.sh. It stamps a `model:` key into the frontmatter of
# .opencode/commands/*.md and .opencode/agents/*.md — and NOWHERE else. The .ai/ sources
# stay vendor-neutral, and the single-vendor mirrors stay clean:
#     .claude/  → Anthropic only   (an OpenRouter id here is unusable)
#     .codex/   → OpenAI only
#     .gemini/  → Google only      (transform drops the key anyway)
# opencode is the only model-agnostic tool in this repo, so it is the only one routed.
#
# CONTRACT: if this file is absent, sync falls back to a plain copy (template stays neutral).
# To redistribute the template without your picks, just gitignore or delete this file.
#
# GROUNDING: model picks track the DeepSWE leaderboard (https://deepswe.datacurve.ai/),
# the only SWE benchmark Omar trusts (contamination-free, agentic). Snapshot 2026-06-01:
#   gpt-5.5 #1 70%  · opus-4.8 #2 58%  · gpt-5.4 #3 56%  · gpt-5.4-mini #8 24%
# Lifecycle stages are coding tasks, so DeepSWE applies directly here.
# Token economics: frontier only where subtle reasoning pays; mechanical stages on the
# cheap-but-agentic-capable mini (NOT the single-digit-DeepSWE bargain models, which are
# cheap AND flaky at tool use). Re-run `sh scripts/sync-ai-docs.sh` after editing.
#
# Effort note: opencode honors `model:` per-command; recommended reasoning effort is in the
# comments below. Set global default + provider/auth in opencode.json (uses OPENROUTER_API_KEY).

OPENCODE_FRONTIER="openrouter/openai/gpt-5.5"        # #1 · 70% — subtle reasoning, suggest effort=high/xhigh
OPENCODE_CAPABLE="openrouter/openai/gpt-5.4"         # #3 · 56% — value champ, ties Opus 4.8 at ~1/3 cost
OPENCODE_CHEAP="openrouter/openai/gpt-5.4-mini"      # #8 · 24% — cheap ($2/task) but agentic-reliable

# opencode_model_for NAME  →  echoes the OpenRouter model id (empty = plain copy, no routing)
opencode_model_for() {
	case "$1" in
		# --- commands (lifecycle stages) ---
		spec)              echo "$OPENCODE_FRONTIER" ;;  # contract/behavior design
		review)            echo "$OPENCODE_FRONTIER" ;;  # subtle-bug catching
		consensus-review)  echo "$OPENCODE_FRONTIER" ;;  # 2-of-3 panel, highest-value review
		build)             echo "$OPENCODE_CAPABLE"  ;;  # implementation correctness
		acceptance)        echo "$OPENCODE_CAPABLE"  ;;  # per-requirement traceability — needs care
		evolve)            echo "$OPENCODE_CAPABLE"  ;;  # code-vs-contract drift judgment
		bootstrap)         echo "$OPENCODE_CAPABLE"  ;;  # one-time interview/setup
		plan)              echo "$OPENCODE_CHEAP"    ;;  # mechanical decomposition
		test)              echo "$OPENCODE_CHEAP"    ;;  # red-green (bump to CAPABLE if TDD intent slips)
		code-simplify)     echo "$OPENCODE_CHEAP"    ;;  # mechanical edits
		ship)              echo "$OPENCODE_CHEAP"    ;;  # mechanical commits
		excalidraw-spec)   echo "$OPENCODE_CHEAP"    ;;  # diagram via MCP
		# --- agents (reviewer personas) ---
		security-auditor)     echo "$OPENCODE_FRONTIER" ;;  # security subtlety = frontier
		code-reviewer)        echo "$OPENCODE_CAPABLE"  ;;
		performance-reviewer) echo "$OPENCODE_CAPABLE"  ;;
		test-engineer)        echo "$OPENCODE_CAPABLE"  ;;
		# --- default: capable mid-tier (safe value baseline) ---
		*)                 echo "$OPENCODE_CAPABLE"  ;;
	esac
}
