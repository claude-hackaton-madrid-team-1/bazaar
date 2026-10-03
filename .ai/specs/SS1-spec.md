# SS1 — Calibrated Sunday simulator

Source: PR #269 and its latest `pr-reviewer (codex gpt-6-astra)` review.

The opt-in Sunday scenario models the published schedule, dealer menus, grants, releases,
news, Workshop and calibrated rivals. The plain simulator retains its existing behavior.
The runner profiles our three agents against a private local simulator using a separate database.

Acceptance for the review corrections:
1. Merge `origin/main`; retain both memory histories and regenerate README status.
2. Before any connection, reject remote administration, libpq hostaddr/service overrides,
   real-game database names and inherited libpq overrides. Every child strips DATABASE_URL,
   uses an explicit sim database or dead address, and reads an empty environment file.
3. Schedule now_hours and at_hours use the same origin; countdowns match event ticks.
4. Scenario dealer operations support the advertised epic/legendary cards, while ordinary
   simulator behavior and menu/release validation remain intact.
5. The bluff wiring test is deterministic without changing production bluff selection.
6. The integrated gate passes on the final commit: pytest once alone, Ruff lint/format,
   Black, mypy, generated-doc sync, README check, rules and local smoke on a free port 8985–8989.
7. PR body includes per-criterion evidence, metric and explicit Unverified/Could-not-do lists.

No game calls, shared database connections, Railway changes, main merge, force push or secrets.
