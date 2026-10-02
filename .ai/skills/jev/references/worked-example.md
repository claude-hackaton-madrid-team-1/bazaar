# Worked example

An agent needs to decide whether a reported defect is introduced by its branch. It commits the candidate code, prepares consumer-owned finding questions, then runs:

```sh
jev-finding --file src/worker.ts --lines 40-52 \
  --finding 'The retry path can skip an acknowledged result.' \
  --base origin/main --questions-directory questions --json --log
```

The response contains a route and reasoning. If the result is `decide_by_hand`, the agent stops and asks a person; it does not infer that the finding is false. After the outcome is observed, it can use `jev-outcome --digest <callDigest> --outcome right` and `jev-report` against the same log directory. No example value is a credential or a real decision log.
