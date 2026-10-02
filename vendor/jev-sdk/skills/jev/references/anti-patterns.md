# Anti-patterns

- Do not treat a confident verdict as permission to act. Authorization and verification stay outside Jev.
- Do not ask one compound question where dimensions can disagree. Use separate questions or `jev-score-options`.
- Do not send raw secrets, account identifiers, connection strings, or private logs. Masking is defense in depth, not a reason to over-share.
- Do not map `undecided` to yes or hide why it was undecided.
- Do not commit `.local/jev-decisions` or put API keys in a pack, command line, or `.env` file.
- Do not let a finding read uncommitted files; `jev-finding` deliberately uses Git revisions.
