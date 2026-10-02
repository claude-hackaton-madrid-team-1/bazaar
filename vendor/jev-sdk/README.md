# Jev: reusable decision support for repository agents

This private Bun package extracts Jev's typed judgments, routing, scoring, decision logs, MCP server, and stop hook from one application checkout. Jev supplies evidence to an operator; a verdict never authorizes an edit, deployment, financial action, or skipped review.

## Install in a consumer repository

Authenticate GitHub access to this private repository first. Use SSH when your GitHub SSH key has access:

```sh
bun add git+ssh://git@github.com/ogarciarevett/jev-sdk.git
```

With a GitHub token available to Bun's GitHub dependency resolver, the shorthand is:

```sh
bun add github:ogarciarevett/jev-sdk
```

Do not put a token in `package.json`, a command argument, or a checked-in `.env` file. Set `TYPESAFE_API_KEY` in the consumer's process environment, or use a consumer-owned keychain wrapper that exports it for one invocation. The installed binaries run Bun with `--no-env-file`; Jev does not load `.env` or a keychain itself.

## Judge one decision

From the **consumer repository's working directory**:

```sh
printf '%s\n' '{"change":"rename a test helper","files":["test/helper.ts"]}' \
  | bunx --no-install jev-judge --state - --questions questions/agent-operations.json --log
```

The bundled generic packs are templates under `node_modules/@ogarciarevett/jev-sdk/questions/`. Copy and customize them into the consumer's own `questions/` directory. `jev-judge` defaults to `questions/agent-operations.json` under the current working directory; `--questions <path>` overrides it. `jev-finding` defaults to both `questions/agent-operations.json` and `questions/plan-decisions.json` under the current working directory; `--questions-directory <path>` overrides their directory. No question pack is read from this package implicitly.

With `--log`, Jev defaults to `<consumer cwd>/.local/jev-decisions`. `jev-judge`, `jev-score-options`, `jev-finding`, and `jev-capabilities` accept `--directory <path>` to override it; `jev-outcome` and `jev-report` read that same override. Keep `.local/` in the **consumer's** `.gitignore`. Logs hold masked content, but still belong to the consumer, never to this package.

Without a key or on a network/model failure, the judge returns `undecided`, not permission to proceed. A usage error exits nonzero. The live smoke command is optional and requires the environment key.

## Commands and library surface

| Binary | Role |
| --- | --- |
| `jev-judge` | Judge typed questions about state. |
| `jev-score-options` | Compare options across weighted dimensions. |
| `jev-finding` | Route a review finding against committed code and a base ref. |
| `jev-outcome`, `jev-report` | Record and summarize observed outcomes. |
| `jev-capabilities` | Select capabilities from the consumer's skills and extras. |
| `jev-mcp-server` | Serve the `jev_judge` MCP stdio tool. |
| `jev-stop-hook` | Optional Claude Code stop hook, fail-open on uncertainty. |
| `jev-smoke` | Optional live latency/availability probe. |

Library imports use package subpaths, for example `import { judge, jevDependenciesFrom } from "@ogarciarevett/jev-sdk/judge"` and `import { maskJevText } from "@ogarciarevett/jev-sdk/mask"`. `skills/jev/` contains a portable agent skill and its references; consumers install/copy it only if their agent runtime uses skills. The package does not register hooks or MCP tools automatically.

## Source map and extraction boundary

The generic source from `scripts/jev/` is in `src/`: judge, score-options, finding, outcome, report, capabilities, MCP server, stop hook, decision log, CLI parsing, stdin, and masking. The masking dependency formerly in `scripts/public-text-sanitizer.ts` is now local at `src/public-text-sanitizer.ts`, with repository-specific database names removed. The Jev-specific `scripts/test/pipeline-2/jev-*.test.ts` tests are ported under `test/`; the question-pack test is adapted to the generic packs.

The original `admin-walk.json` and `demo-e2e.json` are application-specific and are **not shipped**. The original `agent-operations.json` and `plan-decisions.json` included application services, failure layers, and model-routing policy; this package ships only their reusable questions. Source `tests/pipeline-2/jev-operations-boundary.test.ts` and the pipeline-3/e2e harness tests enforce the source repository's architecture rather than the package API, so they remain in the source checkout and are not ported.

## Follow-up for Edel and a second consumer (not performed)

1. Add `"@ogarciarevett/jev-sdk": "github:ogarciarevett/jev-sdk"` to each consumer's `package.json` dependencies (or install the equivalent SSH Git URL with `bun add git+ssh://git@github.com/ogarciarevett/jev-sdk.git`). Keep repository-specific packs in each consumer's `questions/` directory and `.local/` ignored.
2. Replace imports in `scripts/test/pipeline-2/jev-*.test.ts` from `../../jev/judge`, `../../jev/mask`, `../../jev/finding`, `../../jev/decision-log`, `../../jev/capabilities`, `../../jev/score-options`, and `../../jev/cli` with the corresponding exported `@ogarciarevett/jev-sdk/<module>` subpaths; CLI entrypoint imports use `@ogarciarevett/jev-sdk/jev-judge` etc. Replace direct source paths in CLI tests with resolved package binaries (or test the package here instead). Replace `scripts/jev/questions/*.json` references with consumer-owned `questions/*.json`.
3. Replace direct `bun scripts/jev/jev-judge.ts`, `jev-score-options.ts`, `jev-finding.ts`, `jev-outcome.ts`, `jev-report.ts`, `jev-capabilities.ts`, `jev-mcp-server.ts`, `jev-stop-hook.ts`, and `jev-smoke.ts` invocations in the Jev skill, its references, local MCP registration, and stop-hook configuration with the corresponding `jev-*` binaries. If package scripts are desired, map names such as `"jev:judge": "jev-judge"` and `"jev:report": "jev-report"`; the current Edel `package.json` has no Jev-specific script to replace. Move `scripts/jev/questions/` to consumer ownership, then remove `scripts/jev/` only after the consumer tests pass. For a second repo, repeat the same dependency, binary, pack, and log setup without copying source.

No changes to either consumer were made by this extraction.

## Verify this package

```sh
bun install
bun test
bunx tsc --noEmit
```
