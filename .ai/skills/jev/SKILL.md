---
name: jev
description: Use Jev for typed, masked decision support in repository operations; verdicts never authorize action.
---

# Jev

Read [the vendored SDK README](../../../vendor/jev-sdk/README.md) for installation and CLI flags. Jev is an input to human/agent decisions, never a substitute for repository policy, tests, review, or explicit authorization.

1. State the decision as one typed question over only the needed evidence. Use a consumer-owned pack under `questions/` (Bazaar ships `questions/negotiation.json`).
2. Keep the key in `TYPESAFE_API_KEY` via the consumer's environment or keychain wrapper. Never paste it into a state, pack, log, or source file.
3. In Bazaar run our Python port, `uv run python -m bazaar_agent.jev judge --state <path or -> --questions <path>` (`vendor/jev-sdk` is reference only: no bun or node at runtime); treat `undecided` as a stop or manual decision, never as yes.
4. In the reference SDK, a review finding uses `jev-finding` with committed code and an explicit base, option comparison uses `jev-score-options`, and capability selection uses `jev-capabilities` against the consumer's roster. Our port's CLI has only `judge` and `report`.
5. Inspect decisions with `uv run python -m bazaar_agent.jev report` (the reference SDK also has `jev-outcome` to record observed outcomes). Keep `.local/jev-decisions` untracked.

See [protocol](references/protocol.md), [question packs](references/question-packs.md), [anti-patterns](references/anti-patterns.md), and [worked example](references/worked-example.md).
