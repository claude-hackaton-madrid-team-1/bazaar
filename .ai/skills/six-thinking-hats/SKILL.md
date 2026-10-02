---
name: six-thinking-hats
description: Structured parallel-thinking method (Edward de Bono's Six Hats) for high-stakes decisions, design choices, and reviews. Use to separate facts, upside, risks, alternatives, reaction, and process instead of mixing them. Powers the /consensus-review panel and any "should we do X" deliberation.
---

# Six Thinking Hats

## Overview

Parallel thinking: wear ONE mode at a time so critique doesn't drown invention and feelings
don't masquerade as facts. The whole value is **mode-separation** + a **parallel (not
adversarial)** framing — everyone, or a single agent across passes, looks in the same direction
at the same time, then switches together. The hat metaphor is just packaging; the keeper is the
discipline of not mixing modes.

## When to Use

- High-stakes or ambiguous **decisions** (architecture, tradeoffs, "should we adopt X").
- **Design choices** with more than one viable option.
- The **`/consensus-review`** panel — it structures the fan-out and the synthesis (see below).
- A **pre-mortem** on a risky change.

**Skip it** for trivial or mechanical work — single-line fixes, formatting, obvious edits. The
ceremony costs more than it returns there.

## The Six Hats

| Hat | Mode | Asks | Watch for |
|---|---|---|---|
| ⚪ **White** | Facts / data | What do we know? What's missing? What's assumed? | Don't smuggle opinion in as fact. |
| 🟢 **Green** | Alternatives / ideas | What other options exist? What haven't we considered? | Generate *before* judging. |
| 🟡 **Yellow** | Value / upside | Why does this work? What's the benefit? Best case? | Be concrete, not cheerleading. |
| ⚫ **Black** | Risk / caution | How does it fail? What breaks? Worst case? | Critical, not cynical — every point needs a reason. |
| 🔴 **Red** | Reaction / feeling | Gut reaction? How will people feel about it? | For an AI: anticipated **stakeholder reaction**, labeled as such — no fake intuition. |
| 🔵 **Blue** | Process / meta | Frame the question, run the sequence, summarize, decide next step. | The chair — every pass starts and ends here. |

## Running a Hat Pass

Default sequence (adapt to the problem):

1. 🔵 **Blue** — state the decision and the success criteria.
2. ⚪ **White** — facts, constraints, unknowns.
3. 🟢 **Green** — enumerate options; do not judge yet.
4. 🟡 **Yellow** — the upside of each option.
5. ⚫ **Black** — risks / failure modes of each option (each with a concrete mechanism).
6. 🔴 **Red** — anticipated reaction (user / maintainer / teammate).
7. 🔵 **Blue** — synthesize → decision + next step + "what would change my mind."

One hat at a time. Don't let Black interrupt Green — judging an idea before it's fully formed
kills it. 

**AI adaptations (read these — they're where it actually changes your output):**
- LLMs over-index on ⚪ White + ⚫ Black. Spend *real* effort on 🟡 Yellow and 🟢 Green.
- You converge on the first workable answer. 🟢 Green is the antidote — force **≥ 2 genuine
  alternatives** before deciding.
- You produce plausible-but-unjustified cautions. Require a **concrete reason or mechanism** for
  each ⚫ Black point — no vibes.
- 🔴 Red is the weakest fit for a model. Reframe it as *anticipated stakeholder reaction* and say
  so; don't pretend to intuition you don't have.

## Parallel Fan-out (and graceful degradation)

For high-stakes calls, run hats **concurrently** — one sub-agent per hat (or per hat-group) —
then a 🔵 Blue synthesis pass. Use each tool's **native** parallelism: an **Agent Team** in Claude
Code, **sub-agents** in Codex / Gemini / opencode. Brief each with fresh, verified ground truth —
the relevant `.ai/specs/` section and the actual files, never guesses.

**Degrade gracefully:** when parallelism isn't available, or the stakes are modest, run a single
agent through the sequence above — same hats, same output shape. No committed scripts; this rides
the template's existing fan-out model (see `.ai/pipeline.md` → "Parallel work").

## In `/consensus-review`

The 2-of-3 panel **is** a hat fan-out:

- ⚫⚪ **Black + White** → the three reviewers (`code-reviewer`, `security-auditor`,
  `performance-reviewer`) audit their axis against evidence.
- 🟡 **Yellow** → name what's *good and worth preserving* — the Chesterton's-Fence handoff to
  `/code-simplify`: do not strip load-bearing complexity.
- 🟢 **Green** → the alternatives / simplifications the panel surfaced.
- 🔵 **Blue** → the lead applies the **≥ 2-of-3 APPROVE** gate, records the verdict + hat summary
  in `.ai/specs/03-review.md`, and decides the next step (remediate / defer / pass).

## Output Shape

End every pass with a Blue verdict:

```
🔵 Decision:  <the question being decided>
⚪ Facts:     <what's known / assumed / missing>
🟢 Options:   A / B / C
🟡 Upside:    <per option>
⚫ Risks:     <per option — each with a reason>
🔴 Reaction:  <anticipated stakeholder reaction>
🔵 Verdict:   <choice> — next step: <action>; would change if: <trigger>
```

## Anti-patterns

- Wearing all six hats on a trivial edit (overhead > value).
- Black-hatting an idea before Green finishes (premature judgment).
- Skipping 🟡 Yellow / 🟢 Green — the common LLM failure mode.
- Faking 🔴 Red as real intuition instead of labeled anticipation.
- A pass that ends with no 🔵 Blue decision and next step.

## Verification

- [ ] The decision/question was stated up front (Blue).
- [ ] At least 2 genuine alternatives were considered (Green).
- [ ] Every Black risk cites a concrete reason or mechanism.
- [ ] Red is labeled as anticipated reaction, not asserted as fact.
- [ ] The pass ends with a verdict + a next step.
