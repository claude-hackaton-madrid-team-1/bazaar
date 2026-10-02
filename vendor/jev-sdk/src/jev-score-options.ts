#!/usr/bin/env -S bun --no-env-file
// Scores N options against M dimensions in one call, and prints the weighted table.
//
//   jev-score-options --plan <plan.json> [--json] [--min-margin 0.05] [--log]
//
// The plan file holds the shared `state`, the `options` and the `dimensions`, each dimension with
// its own question, its own ordered levels and its weight. The weights are combined here, in code,
// so the reason one option won is readable and adjustable.
import { join } from "node:path";

import { jevFractionFlag, jevJsonFileAt, jevNumberFlag, parseJevFlags, runJevCli } from "./cli";
import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
  jevLogDate,
} from "./decision-log";
import { type JevJudgeRequest, jevDependenciesFrom, judge } from "./judge";
import {
  JEV_SCORE_DEFAULT_MARGIN,
  jevScorePlanFrom,
  jevScoreQuestions,
  jevScoreTable,
  jevScoreTableText,
} from "./score-options";

export const JEV_SCORE_USAGE = `Score several options against several weighted dimensions, in one call.

  jev-score-options --plan <plan.json> [options]

  --plan <path>         a JSON file holding { state, options, dimensions }, where each dimension is
                        { question, criteria: [levels], weight?, stakes? }. Every level describes a
                        concrete situation; a one word level is refused.
  --min-margin <0..1>   how far ahead the leader must be to be a winner (default ${JEV_SCORE_DEFAULT_MARGIN})
  --timeout-ms <n>      how long the one call may take
  --json                print the table as data instead of text
  --directory <path>    override the consumer-relative decision-log directory
  --log                 append one JSON line to ${JEV_DECISION_LOG_DIRECTORY}/<date>.jsonl
  --help                print this text

  One request carries every dimension against every option. A winner is named only when every cell
  behind the leader cleared its own bar and the lead is at least the minimum margin; otherwise the
  leader is printed as a leaning and the choice stays with a person. Exit 0 either way.
`;

const FLAGS = {
  withValue: ["--plan", "--min-margin", "--timeout-ms", "--directory"],
  switches: ["--json", "--log", "--help", "-h"],
  usage: JEV_SCORE_USAGE,
} as const;

async function main(argv: readonly string[]): Promise<number> {
  const flags = parseJevFlags(argv, FLAGS);
  if (flags.has("--help") || flags.has("-h")) {
    process.stdout.write(JEV_SCORE_USAGE);
    return 0;
  }

  const planPath = flags.value("--plan");
  if (planPath === undefined) {
    process.stderr.write(`--plan is required\n\n${JEV_SCORE_USAGE}`);
    return 2;
  }

  const plan = jevScorePlanFrom(jevJsonFileAt(planPath));
  const request: JevJudgeRequest = {
    state: plan.state,
    questions: jevScoreQuestions(plan),
    timeoutMs: jevNumberFlag(flags, "--timeout-ms"),
  };
  const result = await judge(request, jevDependenciesFrom(process.env));
  const table = jevScoreTable(
    plan,
    result.verdicts,
    jevFractionFlag(flags, "--min-margin") ?? JEV_SCORE_DEFAULT_MARGIN,
  );

  const printed = flags.has("--json")
    ? `${JSON.stringify({ model: result.model, latencyMs: result.latencyMs, ...table }, null, 2)}\n`
    : `${jevScoreTableText(table)}\nmodel ${result.model || "none"}, ${result.latencyMs} ms\n`;
  process.stdout.write(printed);

  if (!flags.has("--log")) return 0;
  const at = new Date().toISOString();
  appendJevDecision(
    flags.value("--directory") ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY),
    jevLogDate(at),
    jevDecisionLine(request, result, at),
  );
  return 0;
}

if (import.meta.main) runJevCli(main);
