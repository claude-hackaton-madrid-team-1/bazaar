# Protocol

A Jev call sends a masked state and a map of typed questions to TypeSafe's model. It returns a closed verdict per question id: yes/no, choice, score, or `undecided`. The caller validates the verdict against the question's stakes and retains authority for any action.

Use `jev-judge --state - --questions questions/agent-operations.json` for a single consumer-owned pack. For an existing code review comment, use `jev-finding --comment <id> --base <ref> --questions-directory questions`; it reads committed code, not untracked work. A real introduced finding is a fix candidate; a pre-existing one is a follow-up; uncertainty goes to a person.

With `--log`, Jev writes masked JSONL under the consumer's `.local/jev-decisions` by default. Override with `--directory` consistently for judge, outcome and report. A missing key, timeout, network error, or invalid model response produces `undecided`. A malformed caller request is a usage error instead. A verdict does not grant access to tools, credentials, edits, review bypass, deployment, or money movement.
