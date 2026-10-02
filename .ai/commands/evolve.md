---
description: "Scan the repo, diff code-reality vs the .ai/ contract+specs, and write proposed evolution patches to .ai/specs/97-evolution.md (proposes, never applies)"
---

Invoke the `evolve` skill.

`/evolve` keeps the `.ai/` source-of-truth in sync with the code. It works in **every CLI**
and it **proposes, never applies** — a human reviews the output and applies what's right.

1. **Map reality.**
   - *Claude Code (accelerated):* build/reuse a `/graphify` knowledge graph (`graphify-out/`).
   - *Any CLI (fallback):* read the repo directly with grep/glob — and if graphify isn't
     installed, this is the path on Claude Code too.
2. **Scan for drift** across five dimensions — contract, stack, specs, pipeline, surface —
   comparing ground truth to the matching `.ai/` source. Fan the five checks across the tool's
   native parallelism — an Agent Team in Claude Code, the tool's sub-agents in Codex / Gemini /
   opencode (or run them sequentially if the run is small), per the "Parallel work" section of
   `.ai/pipeline.md`. Brief each with the dimension's `.ai/` source + the actual files.
3. **Report:** write `.ai/specs/97-evolution.md` (drift table + concrete proposed patches to
   `.ai/` sources only, never generated files) with a one-line verdict.
4. **Hand off:** stop there. Applying a patch = edit the `.ai/` source, then `sh scripts/sync-ai-docs.sh`.

See the `evolve` skill for full detail and the report template.
