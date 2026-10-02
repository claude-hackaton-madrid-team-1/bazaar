#!/usr/bin/env -S bun --no-env-file
// What the decision log says about each question, so a bar is tuned on counted results.
//
//   jev-report [--directory <path>] [--json]
//
// One row per question id: how often it was asked, how often it decided, how often it was undecided,
// and how the calls it answered turned out. A question that is undecided half the time has the wrong
// bar or the wrong options; a question that decides and is wrong has the wrong criteria.
import { join } from "node:path";

import { parseJevFlags, runJevCli } from "./cli";
import {
  JEV_DECISION_LOG_DIRECTORY,
  type JevQuestionTally,
  jevLogTally,
  readJevLog,
} from "./decision-log";

export const JEV_REPORT_USAGE = `Count what the Jev decision log holds, per question id.

  jev-report [options]

  --directory <path>    where the log lives (default ${JEV_DECISION_LOG_DIRECTORY} under the cwd)
  --json                print the rows as JSON instead of a table
  --help                print this text

  Columns: calls, decided, undecided, right, wrong, unknown. The last three count the outcomes
  recorded with jev-outcome.ts; a call with no recorded outcome counts in none of them.
`;

const FLAGS = {
  withValue: ["--directory"],
  switches: ["--json", "--help", "-h"],
  usage: JEV_REPORT_USAGE,
} as const;

const COLUMNS = ["calls", "decided", "undecided", "right", "wrong", "unknown"] as const;

/** A fixed-width table, so two runs of the report line up and a diff reads. */
export function jevReportTable(rows: readonly JevQuestionTally[]): string {
  if (rows.length === 0) return "no decisions in the log yet\n";
  const width = Math.max(8, ...rows.map((row) => row.question.length));
  const header = ["question".padEnd(width), ...COLUMNS.map((name) => name.padStart(10))].join("");
  const body = rows.map((row) =>
    [row.question.padEnd(width), ...COLUMNS.map((name) => String(row[name]).padStart(10))].join(""),
  );
  return `${[header, ...body].join("\n")}\n`;
}

async function main(argv: readonly string[]): Promise<number> {
  const flags = parseJevFlags(argv, FLAGS);
  if (flags.has("--help") || flags.has("-h")) {
    process.stdout.write(JEV_REPORT_USAGE);
    return 0;
  }

  const directory = flags.value("--directory") ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY);
  const rows = jevLogTally(readJevLog(directory));
  if (!flags.has("--json")) {
    process.stdout.write(jevReportTable(rows));
    return 0;
  }
  process.stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
  return 0;
}

if (import.meta.main) runJevCli(main);
