#!/usr/bin/env -S bun --no-env-file
// Records what a decision turned out to be, on the same log the judge writes.
//
//   jev-outcome --digest <stateDigest> --outcome right|wrong|unknown --note "..."
//
// A verdict without an outcome leaves every bar a guess. The digest comes from the decision line the
// call already wrote, so the two halves join without the state ever being stored.
import { join } from "node:path";

import { jevTextFileAt, parseJevFlags, runJevCli } from "./cli";
import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  JEV_OUTCOMES,
  type JevDecisionEntry,
  type JevOutcome,
  jevLogDate,
  jevOutcomeLine,
  readJevLog,
} from "./decision-log";
import { JevUsageError } from "./judge";

export const JEV_OUTCOME_USAGE = `Record how one Jev decision turned out.

  jev-outcome --digest <stateDigest> --outcome <word> [options]

  --digest <sha256>     the stateDigest printed on the decision line this judges
  --outcome <word>      ${JEV_OUTCOMES.join(", ")}
  --question <id>       only this answer turned out that way, not the whole call
  --note <text>         one line of context; it is masked exactly like a state
  --directory <path>    where the log lives (default ${JEV_DECISION_LOG_DIRECTORY} under the cwd)
  --note-file <path>    read the note from a file instead of the command line
  --help                print this text

  The digest has to name a call the log already holds. One state judged by two calls has one
  stateDigest and two callDigests; pass the callDigest, or name a --question, so the outcome
  reaches the call that was right or wrong and not the other one.

  It prints the line it appended and exits 0. Exit 2 means the command was wrong.
`;

const FLAGS = {
  withValue: ["--digest", "--outcome", "--question", "--note", "--note-file", "--directory"],
  switches: ["--help", "-h"],
  usage: JEV_OUTCOME_USAGE,
} as const;

/** A decision line carries a sha256 of masked text, so anything else names no call. */
export function jevDigestFrom(raw: string | undefined): string {
  if (raw === undefined) throw new JevUsageError(`--digest is required\n\n${JEV_OUTCOME_USAGE}`);
  if (!/^[0-9a-f]{64}$/u.test(raw)) {
    throw new JevUsageError("--digest must be the 64 character stateDigest of a decision line");
  }
  return raw;
}

export function jevOutcomeFrom(raw: string | undefined): JevOutcome {
  if (raw === undefined) throw new JevUsageError(`--outcome is required\n\n${JEV_OUTCOME_USAGE}`);
  if (!JEV_OUTCOMES.includes(raw as JevOutcome)) {
    throw new JevUsageError(`--outcome must be one of ${JEV_OUTCOMES.join(", ")}`);
  }
  return raw as JevOutcome;
}

/** The calls in the log that this digest names, under either of the two digests a line carries. */
function callsNamedBy(directory: string, digest: string): readonly JevDecisionEntry[] {
  return readJevLog(directory).flatMap((entry) => {
    if (entry.kind !== "decision") return [];
    if (entry.callDigest !== digest && entry.stateDigest !== digest) return [];
    return [entry];
  });
}

function questionIdsOf(entry: JevDecisionEntry): readonly string[] {
  return Object.keys(entry.verdicts ?? {}).sort();
}

/**
 * An outcome that names no call is feedback nobody can read back, so it is refused at the keyboard
 * rather than written and silently ignored by the report.
 */
function validatedAgainstLog(
  directory: string,
  digest: string,
  question: string | undefined,
): void {
  const matched = callsNamedBy(directory, digest);
  if (matched.length === 0) {
    throw new JevUsageError(`no decision line in ${directory} carries the digest ${digest}`);
  }
  if (question !== undefined) {
    if (matched.some((entry) => questionIdsOf(entry).includes(question))) return;
    throw new JevUsageError(`the call ${digest} never asked ${question}`);
  }
  const sets = new Set(matched.map((entry) => questionIdsOf(entry).join(",")));
  if (sets.size === 1) return;
  const callDigests = [...new Set(matched.map((entry) => entry.callDigest ?? digest))];
  throw new JevUsageError(
    `${digest} is one state judged by two calls that asked different questions. Name the call with ` +
      `its callDigest (${callDigests.join(", ")}) or name a --question.`,
  );
}

async function main(argv: readonly string[]): Promise<number> {
  const flags = parseJevFlags(argv, FLAGS);
  if (flags.has("--help") || flags.has("-h")) {
    process.stdout.write(JEV_OUTCOME_USAGE);
    return 0;
  }

  const notePath = flags.value("--note-file");
  const note = notePath === undefined ? flags.value("--note") : jevTextFileAt(notePath);
  const digest = jevDigestFrom(flags.value("--digest"));
  const outcome = jevOutcomeFrom(flags.value("--outcome"));
  const question = flags.value("--question");
  const directory = flags.value("--directory") ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY);
  validatedAgainstLog(directory, digest, question);

  const at = new Date().toISOString();
  const line = jevOutcomeLine({ stateDigest: digest, outcome, question, note }, at);
  appendJevDecision(directory, jevLogDate(at), line);
  process.stdout.write(`${line}\n`);
  return 0;
}

if (import.meta.main) runJevCli(main);
