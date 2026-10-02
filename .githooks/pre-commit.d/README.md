# `.githooks/pre-commit.d/` — project quality gates (language-specific)

The agnostic `../pre-commit` runs the sync drift gate, then executes **every executable
file in this directory** (sorted by name). This is where the project's language-specific
pre-commit checks live — lint, format, typecheck on staged files.

The template ships this directory empty (only this README). **`/bootstrap` fills it** once
you pick a language: it writes one or more numbered scripts here, e.g.

```
10-format.sh    # gofmt -l / prettier --check / black --check / cargo fmt --check …
20-lint.sh      # go vet / eslint / ruff / cargo clippy …
30-typecheck.sh # tsc --noEmit / mypy / …
```

Rules for scripts you add here:

- Make them executable (`chmod +x`) — non-executable files (like this README) are skipped.
- Exit non-zero to **block** the commit; the dispatcher stops on the first failure.
- Prefer checking **staged** files only (e.g. `git diff --cached --name-only --diff-filter=ACM`)
  so the hook stays fast.
- Keep each script focused on one concern; name them with a numeric prefix to order them.
