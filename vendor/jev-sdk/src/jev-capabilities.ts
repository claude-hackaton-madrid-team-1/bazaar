#!/usr/bin/env -S bun --no-env-file
// Which skills, MCP servers and CLIs a unit of work should load, in two calls.
//
//   jev-capabilities --unit <file or -> [--extras <file.json>] [--log]
//
// Pass one ranks the whole roster and asks whether the unit wants a capability at all. Pass two
// re-reads the top few with each one's own text and asks whether each actually fits. The answer may
// be nothing, which is what stops a brief carrying a skill that only sounded close.
import { join } from "node:path";
import {
  JEV_CAPABILITY_EXCERPT_CHARACTERS,
  JEV_CAPABILITY_FITS_THRESHOLD,
  JEV_CAPABILITY_GATE_THRESHOLD,
  JEV_CAPABILITY_SHORTLIST,
  type JevCapability,
  jevCapabilitySuggestion,
  jevExtraCapabilitiesFrom,
  jevGateFrom,
  jevRerankQuestions,
  jevRosterFrom,
  jevShortlistFrom,
  jevWideQuestions,
} from "./capabilities";
import {
  jevFractionFlag,
  jevJsonFileAt,
  jevNumberFlag,
  jevTextFileAt,
  parseJevFlags,
  runJevCli,
} from "./cli";
import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
  jevLogDate,
} from "./decision-log";
import { jevStateFrom } from "./jev-judge";
import { type JevJudgeRequest, jevDependenciesFrom, judge } from "./judge";
import { readAllText } from "./stdin";

/** Where this repository keeps its skills. */
export const JEV_SKILLS_DIRECTORY = ".agents/skills";

export const JEV_CAPABILITIES_USAGE = `Pick the capabilities one unit of work should load, in two calls.

  jev-capabilities --unit <file or -> [options]

  --unit <path>         the unit of work to select for; - reads stdin. JSON is sent as structure.
  --skills <path>       where the skills live (default ${JEV_SKILLS_DIRECTORY} under the cwd)
  --extras <path>       a JSON file holding { "capabilities": [{ name, description, detail? }] }
                        for the MCP servers, CLIs and databases that are not skills on disk
  --top <n>             how many candidates the second call re-reads (default ${JEV_CAPABILITY_SHORTLIST})
  --gate <0..1>         below this mean over the three gate questions nothing is suggested (default ${JEV_CAPABILITY_GATE_THRESHOLD})
  --fits <0..1>         a candidate under this is not suggested (default ${JEV_CAPABILITY_FITS_THRESHOLD})
  --excerpt <n>         characters of each candidate's own text the second call reads (default ${JEV_CAPABILITY_EXCERPT_CHARACTERS})
  --timeout-ms <n>      how long each of the two calls may take
  --json                print the answer as data instead of text
  --directory <path>    override the consumer-relative decision-log directory
  --log                 append one JSON line per call to ${JEV_DECISION_LOG_DIRECTORY}/<date>.jsonl
  --help                print this text

  It prints the list to load, which may be empty, and exits 0. Exit 2 means the command was wrong.
  A suggestion is an input to the brief, never permission to run what the capability describes.
`;

const FLAGS = {
  withValue: [
    "--unit",
    "--skills",
    "--extras",
    "--top",
    "--gate",
    "--fits",
    "--excerpt",
    "--timeout-ms",
    "--directory",
  ],
  switches: ["--json", "--log", "--help", "-h"],
  usage: JEV_CAPABILITIES_USAGE,
} as const;

type Answer = {
  readonly roster: number;
  readonly gate: number | undefined;
  readonly shortlist: readonly string[];
  readonly load: readonly string[];
  readonly winner: string | undefined;
  readonly fits: Readonly<Record<string, number>>;
  readonly calls: number;
  readonly latencyMs: number;
  readonly reason: "suggested" | "nothing_fits" | "gate_below_threshold" | "judge_unavailable";
};

function answerText(answer: Answer): string {
  const gate = answer.gate === undefined ? "none" : answer.gate.toFixed(2);
  const fits = Object.entries(answer.fits)
    .map(([name, probability]) => `    fits ${probability.toFixed(2)}  ${name}`)
    .join("\n");
  const load = answer.load.length === 0 ? "  load nothing" : `  load ${answer.load.join(", ")}`;
  return [
    `roster ${answer.roster}, gate ${gate}, ${answer.calls} calls, ${answer.latencyMs} ms`,
    answer.shortlist.length === 0
      ? "  shortlist none"
      : `  shortlist ${answer.shortlist.join(", ")}`,
    ...(fits.length === 0 ? [] : [fits]),
    load,
    `  ${answer.reason}`,
    "",
  ].join("\n");
}

async function unitTextFrom(path: string): Promise<string> {
  if (path !== "-") return jevTextFileAt(path);
  return await readAllText(process.stdin);
}

function logged(request: JevJudgeRequest, result: Parameters<typeof jevDecisionLine>[1], directory: string): void {
  const at = new Date().toISOString();
  appendJevDecision(
    directory,
    jevLogDate(at),
    jevDecisionLine(request, result, at),
  );
}

async function main(argv: readonly string[]): Promise<number> {
  const flags = parseJevFlags(argv, FLAGS);
  if (flags.has("--help") || flags.has("-h")) {
    process.stdout.write(JEV_CAPABILITIES_USAGE);
    return 0;
  }

  const unitPath = flags.value("--unit");
  if (unitPath === undefined) {
    process.stderr.write(`--unit is required\n\n${JEV_CAPABILITIES_USAGE}`);
    return 2;
  }

  const excerpt = jevNumberFlag(flags, "--excerpt") ?? JEV_CAPABILITY_EXCERPT_CHARACTERS;
  const extrasPath = flags.value("--extras");
  const extras: readonly JevCapability[] =
    extrasPath === undefined ? [] : jevExtraCapabilitiesFrom(jevJsonFileAt(extrasPath));
  const roster = jevRosterFrom(
    flags.value("--skills") ?? join(process.cwd(), JEV_SKILLS_DIRECTORY),
    extras,
    excerpt,
  );
  const state = { unit: jevStateFrom(await unitTextFrom(unitPath)) };
  const timeoutMs = jevNumberFlag(flags, "--timeout-ms");
  const dependencies = jevDependenciesFrom(process.env);
  const shouldLog = flags.has("--log");

  const wide: JevJudgeRequest = { state, questions: jevWideQuestions(roster), timeoutMs };
  const ranked = await judge(wide, dependencies);
  if (shouldLog) logged(wide, ranked, flags.value("--directory") ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY));

  const gate = jevGateFrom(ranked.verdicts);
  const gateBar = jevFractionFlag(flags, "--gate") ?? JEV_CAPABILITY_GATE_THRESHOLD;
  const shortlist = jevShortlistFrom(
    ranked.verdicts.which,
    jevNumberFlag(flags, "--top") ?? JEV_CAPABILITY_SHORTLIST,
  );
  const stopped = stoppedAfterPassOne(gate, gateBar, shortlist);
  if (stopped !== undefined) {
    return printed(flags.has("--json"), {
      roster: roster.length,
      gate,
      shortlist,
      load: [],
      winner: undefined,
      fits: {},
      calls: 1,
      latencyMs: ranked.latencyMs,
      reason: stopped,
    });
  }

  const rerank: JevJudgeRequest = {
    state,
    questions: jevRerankQuestions(roster, shortlist, excerpt),
    timeoutMs,
  };
  const rechecked = await judge(rerank, dependencies);
  if (shouldLog) logged(rerank, rechecked, flags.value("--directory") ?? join(process.cwd(), JEV_DECISION_LOG_DIRECTORY));

  const suggestion = jevCapabilitySuggestion(
    shortlist,
    rechecked.verdicts,
    jevFractionFlag(flags, "--fits") ?? JEV_CAPABILITY_FITS_THRESHOLD,
  );
  return printed(flags.has("--json"), {
    roster: roster.length,
    gate,
    shortlist,
    load: suggestion.load,
    winner: suggestion.winner,
    fits: suggestion.fits,
    calls: 2,
    latencyMs: ranked.latencyMs + rechecked.latencyMs,
    reason: suggestion.reason,
  });
}

/** Why the second call is not worth making. `undefined` means it is. */
function stoppedAfterPassOne(
  gate: number | undefined,
  gateBar: number,
  shortlist: readonly string[],
): Answer["reason"] | undefined {
  if (gate === undefined) return "judge_unavailable";
  if (gate < gateBar) return "gate_below_threshold";
  if (shortlist.length === 0) return "judge_unavailable";
  return undefined;
}

function printed(asJson: boolean, answer: Answer): number {
  process.stdout.write(asJson ? `${JSON.stringify(answer, null, 2)}\n` : answerText(answer));
  return 0;
}

if (import.meta.main) runJevCli(main);
