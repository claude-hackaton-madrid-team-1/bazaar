# Pitch notes (Sunday presentation)

Short, evidence-backed paragraphs for the Sunday presentation (strategy, how we built it, key
learnings). One section per task; every number names its source and says whether it came from the
simulator or a live run.

## N15 — Jev picks the model for every role of the desk

Our agent does not hard-code which LLM thinks. Before each request to the desk (the orchestrator and
its four subagents: strategist, buyer, seller, duelist), one call to Jev asks one question per role:
which Claude model should run this role for this request? Jev sees only the shape of the request: its
length, the largest price in it, and any prompt-injection patterns. It answers with a probability per
model, and we follow it only when it clears a 0.75 confidence bar; otherwise each role falls back to a
safe default (Sonnet). On the simulator, for "buy LAV-09 under 90" Jev sent the strategist, buyer and
seller to Opus (0.99) and the duelist to Haiku (0.90): our duel policy sets the price, so the duelist
only routes. Jev was unsure about the orchestrator (0.73, under the bar), so it stayed on Sonnet. For a
60-primas request it moved the buyer and seller down to Sonnet (0.93 and 0.95). Choices are cached per
role for five ticks, so a repeated request costs no Jev call. We check that each choice took effect by
reading which model answered. In one live conversation the buyer ran on claude-opus-5-5, then on
claude-sonnet-5-5 for the next, cheaper request, and the conversation was not lost. A model never
authorizes a trade: code sets prices, and every write still passes the guardrails twice.

Key learnings:
- **One call per request:** five questions in a single Jev request answered within Jev's 3 s budget.
  The cache makes a repeated request free, even from a new process.
- **The SDK detail that made enforcement possible:** a model set on the call that starts a subagent
  overrides that subagent's definition. Our guardrail hook sets that model on every call, so the desk's
  own LLM cannot change it.
- **Fail safe:** without a Jev key, or when Jev is undecided or slow, the desk runs on each role's
  default and the request still completes.
- Sources: `.ai/memory.md` (2026-10-03 entries for N15) and PR #108 (Honest Implementation Report).
