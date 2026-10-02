# `.githooks/post-commit.d/` — project after-commit tasks (language-specific)

The agnostic `../post-commit` executes **every executable file in this directory** (sorted
by name) after a commit succeeds. This is where the project's language-specific after-commit
tasks live — typically running the test suite or refreshing coverage/docs.

The template ships this directory empty (only this README). **`/bootstrap` fills it** once
you pick a language, e.g.

```
10-test.sh    # go test ./… / npm test / pytest -q / cargo test …
```

Rules for scripts you add here:

- Make them executable (`chmod +x`) — non-executable files (like this README) are skipped.
- A post-commit hook runs **after** the commit is recorded, so a non-zero exit cannot abort
  it; use post-commit only for reporting/side tasks. Put anything that must **block** a commit
  in `../pre-commit.d/` instead.
- Keep these fast or make heavy ones opt-in — they run on every commit.
