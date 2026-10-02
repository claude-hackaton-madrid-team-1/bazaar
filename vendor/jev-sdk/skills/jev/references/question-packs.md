# Question packs

The package ships reusable templates at `questions/agent-operations.json` and `questions/plan-decisions.json`; copy the relevant questions into your repository's own `questions/` directory. The consumer owns its vocabulary, paths, policies, and changes to the packs.

A pack is JSON with `{"questions":{"id":{"type":"noul","stakes":"passive","instructions":"..."}}}`. Valid types are `noul`, `choice`, and `score`; stakes are `passive`, `design`, and `critical`. Ask only questions needed for the current branch, keep criteria atomic, and provide named state fields that the instructions reference.

`jev-finding` needs `finding_is_real` in `agent-operations.json` and `finding_is_pre_existing` in `plan-decisions.json`. Pass `--questions-directory` if these files live elsewhere. `jev-judge` accepts any explicit `--questions <path>` and otherwise reads `questions/agent-operations.json` from the consumer cwd.
