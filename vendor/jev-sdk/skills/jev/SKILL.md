---
name: jev
description: Use Jev for typed, masked decision support in repository operations; verdicts never authorize action.
---

# Jev

Read [the package README](../../README.md) for installation and CLI flags. Jev is an input to human/agent decisions, never a substitute for repository policy, tests, review, or explicit authorization.

1. State the decision as one typed question over only the needed evidence. Use a consumer-owned pack under `questions/`.
2. Keep the key in `TYPESAFE_API_KEY` via the consumer's environment or keychain wrapper. Never paste it into a state, pack, log, or source file.
3. Run `jev-judge --state <path or -> --questions <path>`; treat `undecided` as a stop or manual decision, never as yes.
4. For a review finding, use `jev-finding` with committed code and an explicit base. For option comparison use `jev-score-options`; for capability selection use `jev-capabilities` against the consumer's roster.
5. Record observed outcomes with `jev-outcome` and inspect `jev-report`. Keep `.local/jev-decisions` untracked.

See [protocol](references/protocol.md), [question packs](references/question-packs.md), [anti-patterns](references/anti-patterns.md), and [worked example](references/worked-example.md).
