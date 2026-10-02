#!/usr/bin/env -S bun --no-env-file
// The command line in front of `judge()`, for a human and for another agent.
//
//   jev-judge --state <file or -> --questions <file.json> [--threshold 0.8] [--log]
//
// It prints one JSON object and exits 0 for any verdict, `undecided` included. A non-zero exit means
// the command was wrong, never that the judge was unsure.
import { join } from "node:path";

import { type JevFlags, jevNumberFlag, jevTextFileAt, parseJevFlags, runJevCli } from "./cli";
import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
  jevLogDate,
} from "./decision-log";
import {
  JEV_API_KEY_VARIABLE,
  JEV_DEFAULT_THRESHOLD,
  JEV_STAKES,
  JEV_STAKES_THRESHOLDS,
  type JevJudgeRequest,
  type JevQuestions,
  type JevStakes,
  JevUsageError,
  jevDependenciesFrom,
  judge,
} from "./judge";
import type { JevJsonValue } from "./mask";
import { readAllText } from "./stdin";

// The log itself lives in `decision-log.ts`, which the outcome and report commands share. These
// re-exports keep the command line's own surface unchanged for anything that already imports it.
export {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
} from "./decision-log";

export const JEV_DEFAULT_QUESTIONS = "questions/agent-operations.json";

export const JEV_CLI_USAGE = `Ask Jev a set of typed questions about operations state.

  jev-judge --state <file or -> --questions <file.json> [options]

  --state <path>        the state to judge; - reads stdin. JSON is sent as structure, anything
                        else as text. Every value is masked before the request.
  --questions <path>    default questions/agent-operations.json in the consumer cwd; a JSON file holding { "questions": { <id>: { type, instructions, criteria } } }
  --threshold <0..1>    one bar for every question in the call, above everything below it
  --stakes <word>       one bar for every question in the call, by what a wrong answer costs:
                        ${JEV_STAKES.map((stakes) => `${stakes} ${JEV_STAKES_THRESHOLDS[stakes]}`).join(", ")}
                        Without either flag each question keeps the bar its own \`stakes\` names,
                        and a question that names none is judged at ${JEV_DEFAULT_THRESHOLD}.
  --timeout-ms <n>      how long one call may take
  --directory <path>    override the consumer-relative decision-log directory
  --log                 append one JSON line to ${JEV_DECISION_LOG_DIRECTORY}/<date>.jsonl
  --help                print this text

  The key is read from ${JEV_API_KEY_VARIABLE} in the environment only. Without it every verdict is
  undecided with the reason typesafe_api_key_missing. Exit 0 for any verdict, 2 for a usage error.
`;

export type JevCliOptions = {
  readonly statePath: string;
  readonly questionsPath: string;
  readonly threshold: number | undefined;
  readonly stakes: JevStakes | undefined;
  readonly timeoutMs: number | undefined;
  readonly log: boolean;
  readonly directory: string | undefined;
  readonly help: boolean;
};

const JEV_JUDGE_FLAGS = {
  withValue: ["--state", "--questions", "--threshold", "--stakes", "--timeout-ms", "--directory"],
  switches: ["--log", "--help", "-h"],
  usage: JEV_CLI_USAGE,
} as const;

/** The stakes word a caller may write, refused outright when it is not one of the three. */
export function jevStakesFrom(raw: string | undefined): JevStakes | undefined {
  if (raw === undefined) return undefined;
  if (!JEV_STAKES.includes(raw as JevStakes)) {
    throw new JevUsageError(`--stakes must be one of ${JEV_STAKES.join(", ")}`);
  }
  return raw as JevStakes;
}

function optionsFrom(flags: JevFlags, help: boolean): JevCliOptions {
  const statePath = flags.value("--state");
  const questionsPath = flags.value("--questions");
  if (help) {
    return {
      statePath: statePath ?? "-",
      questionsPath: questionsPath ?? JEV_DEFAULT_QUESTIONS,
      threshold: undefined,
      stakes: undefined,
      timeoutMs: undefined,
      log: flags.has("--log"),
      directory: flags.value("--directory"),
      help,
    };
  }
  if (statePath === undefined) throw new JevUsageError(`--state is required\n\n${JEV_CLI_USAGE}`);
  return {
    statePath,
    questionsPath: questionsPath ?? JEV_DEFAULT_QUESTIONS,
    threshold: jevNumberFlag(flags, "--threshold"),
    stakes: jevStakesFrom(flags.value("--stakes")),
    timeoutMs: jevNumberFlag(flags, "--timeout-ms"),
    log: flags.has("--log"),
    directory: flags.value("--directory"),
    help,
  };
}

export function parseJevArguments(argv: readonly string[]): JevCliOptions {
  const flags = parseJevFlags(argv, JEV_JUDGE_FLAGS);
  return optionsFrom(flags, flags.has("--help") || flags.has("-h"));
}

/**
 * One bar for every question, or nothing so that each question keeps the bar its own `stakes`
 * names. `--threshold` is the absolute override; `--stakes` is the same override written as a word.
 */
export function jevCallThresholds(
  questions: JevQuestions,
  options: Pick<JevCliOptions, "threshold" | "stakes">,
): Readonly<Record<string, number>> | undefined {
  const bar = options.threshold ?? (options.stakes && JEV_STAKES_THRESHOLDS[options.stakes]);
  if (bar === undefined) return undefined;
  return Object.fromEntries(Object.keys(questions).map((id) => [id, bar]));
}

/** JSON is judged as structure, anything else as text. */
export function jevStateFrom(text: string): JevJsonValue {
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "object" && parsed !== null) return parsed as JevJsonValue;
    return text;
  } catch {
    return text;
  }
}

/** A pack file holds `{ "questions": { ... } }`; a bare question map is accepted too. */
export function jevQuestionsFrom(text: string, path: string): JevQuestions {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new JevUsageError(`${path} is not JSON`);
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new JevUsageError(`${path} must hold a question map`);
  }
  const record = parsed as { questions?: unknown };
  const questions = record.questions ?? parsed;
  if (typeof questions !== "object" || questions === null) {
    throw new JevUsageError(`${path} must hold a question map`);
  }
  return questions as JevQuestions;
}

async function stateTextFrom(statePath: string): Promise<string> {
  if (statePath !== "-") return jevTextFileAt(statePath);
  return await readAllText(process.stdin);
}

async function main(argv: readonly string[]): Promise<number> {
  const options = parseJevArguments(argv);
  if (options.help) {
    process.stdout.write(JEV_CLI_USAGE);
    return 0;
  }

  const questions = jevQuestionsFrom(jevTextFileAt(options.questionsPath), options.questionsPath);
  const state = jevStateFrom(await stateTextFrom(options.statePath));
  const request: JevJudgeRequest = {
    state,
    questions,
    thresholds: jevCallThresholds(questions, options),
    timeoutMs: options.timeoutMs,
  };

  const result = await judge(request, jevDependenciesFrom(process.env));
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (!options.log) return 0;
  const at = new Date().toISOString();
  appendJevDecision(
    options.directory ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY),
    jevLogDate(at),
    jevDecisionLine(request, result, at),
  );
  return 0;
}

if (import.meta.main) runJevCli(main);
