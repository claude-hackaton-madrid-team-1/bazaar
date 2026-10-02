// Two-pass capability selection, from `https://docs.typesafe.ai/cookbooks/skill_suggestion.md`.
//
// Pass one reads the whole roster cheaply: one `choice` over every capability with its one line
// description, plus three `noul`s asking whether the unit wants a capability at all. Pass two reads
// only the top few properly, with each one's full description and the opening of its own text, and
// one `noul` per candidate that may reject all of them. Two thresholds, no round trip wasted.
//
// The wide `choice` deliberately carries no no-match option. The three gate nouls and the per
// candidate fits nouls do that job here, and the cookbook measured the pair: wrong loads fell from
// 16.8 to 7.3 percent and needless loads from 9.8 to 4.0. A no-match option inside the ranking would
// take probability away from the ranking itself, which is the only thing pass one is for.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { JevUsageError, type JevVerdict } from "./judge";
import type { JevQuestions } from "./mask";

/** How many candidates pass one hands to pass two. */
export const JEV_CAPABILITY_SHORTLIST = 3;
/** How much of a capability's own text pass two reads. */
export const JEV_CAPABILITY_EXCERPT_CHARACTERS = 700;
/** Below this mean over the three gate nouls, nothing is suggested at all. */
export const JEV_CAPABILITY_GATE_THRESHOLD = 0.3;
/** A candidate whose fits noul lands below this is not suggested. */
export const JEV_CAPABILITY_FITS_THRESHOLD = 0.3;

export const JEV_CAPABILITY_SKILL_FILE = "SKILL.md";

export type JevCapability = {
  readonly name: string;
  /** The one line an index shows. */
  readonly description: string;
  /** The opening of the capability's own text, read only in pass two. */
  readonly detail?: string;
};

/** Why the second pass answered the way it did. */
export type JevCapabilitySuggestionReason = "suggested" | "nothing_fits" | "judge_unavailable";

export type JevCapabilitySuggestion = {
  /** What to load, the ranking winner first. Empty means nothing here applies. */
  readonly load: readonly string[];
  readonly winner: string | undefined;
  readonly fits: Readonly<Record<string, number>>;
  readonly reason: JevCapabilitySuggestionReason;
};

/**
 * Three ways of asking whether the unit wants something done rather than explained. A question
 * about subject matter would not separate "explain what a mark is" from work that needs a runbook.
 */
export const JEV_CAPABILITY_GATES: Readonly<Record<string, string>> = {
  acts_on_repository:
    "Is the agent being asked to change, run, measure or inspect something in this repository, " +
    "an environment or a third party console, rather than only to explain or advise?",
  would_follow_documented_procedure:
    "Would a careful engineer answering this consult a specific documented procedure, runbook, " +
    "endpoint list or set of commands, rather than answering from general understanding?",
  prose_suffices:
    "Could a knowledgeable generalist fully satisfy this unit of work in prose, with no tools, " +
    "no documentation and no access to the repository or its environments?",
};

/** A yes here points away from needing a capability, so the gate counts it the other way round. */
const INVERTED_GATES = ["prose_suffices"] as const;

const WIDE_INSTRUCTIONS =
  "Which of these capabilities, if any, is the right one to load for the unit of work in `unit`?";
const RERANK_INSTRUCTIONS =
  "Exactly one of these capabilities is the right one to load first for the unit of work in " +
  "`unit`. Which one? Read what each actually does, not just its name.";

function unquoted(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length < 2) return trimmed;
  const first = trimmed[0];
  if ((first === '"' || first === "'") && trimmed.endsWith(first)) return trimmed.slice(1, -1);
  return trimmed;
}

/** Runs of whitespace, including newlines, become one space. A description is one line. */
function oneLine(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}

/** A YAML block scalar (`>-`, `|`) continues on the indented lines under its key. */
function foldedValue(lines: readonly string[], start: number): string {
  const folded: string[] = [];
  for (let index = start; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (line.trim().length > 0 && !/^\s/u.test(line)) break;
    folded.push(line.trim());
  }
  return oneLine(folded.join(" "));
}

function frontMatterField(lines: readonly string[], key: string): string | undefined {
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (!line.startsWith(`${key}:`)) continue;
    const written = line.slice(key.length + 1).trim();
    if (written.length === 0 || written === ">" || written === ">-" || written === "|") {
      return foldedValue(lines, index + 1);
    }
    return oneLine(unquoted(written));
  }
  return undefined;
}

/** One capability read out of one skill file, or nothing when the file has no front matter. */
export function jevCapabilityFrom(
  source: string,
  excerpt: number = JEV_CAPABILITY_EXCERPT_CHARACTERS,
): JevCapability | undefined {
  if (!source.startsWith("---")) return undefined;
  const lines = source.split("\n");
  const close = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (close <= 0) return undefined;
  const name = frontMatterField(lines.slice(1, close), "name");
  const description = frontMatterField(lines.slice(1, close), "description");
  if (name === undefined || name.length === 0) return undefined;
  if (description === undefined || description.length === 0) return undefined;
  const body = oneLine(lines.slice(close + 1).join("\n")).slice(0, excerpt);
  return body.length === 0 ? { name, description } : { name, description, detail: body };
}

function capabilityAt(directory: string, name: string, excerpt: number): JevCapability | undefined {
  try {
    return jevCapabilityFrom(
      readFileSync(join(directory, name, JEV_CAPABILITY_SKILL_FILE), "utf8"),
      excerpt,
    );
  } catch {
    // A directory with no skill file is not a capability, and never a reason to fail the roster.
    return undefined;
  }
}

/** Every skill on disk, plus the MCP servers, CLIs and databases the caller named. */
export function jevRosterFrom(
  skillsDirectory: string,
  extras: readonly JevCapability[],
  excerpt: number = JEV_CAPABILITY_EXCERPT_CHARACTERS,
): readonly JevCapability[] {
  let names: readonly string[] = [];
  try {
    names = readdirSync(skillsDirectory).sort();
  } catch {
    names = [];
  }
  const skills = names.flatMap((name) => {
    const capability = capabilityAt(skillsDirectory, name, excerpt);
    return capability === undefined ? [] : [capability];
  });
  return [...skills, ...extras];
}

/** The extras file: a list of capabilities that are not skills on disk. */
export function jevExtraCapabilitiesFrom(
  parsed: Record<string, unknown>,
): readonly JevCapability[] {
  const listed = parsed.capabilities ?? parsed;
  if (!Array.isArray(listed)) {
    throw new JevUsageError("the extras file must hold a `capabilities` list");
  }
  return listed.map((entry) => {
    const one = entry as Partial<JevCapability>;
    if (typeof one?.name !== "string" || typeof one?.description !== "string") {
      throw new JevUsageError("every extra capability needs a `name` and a `description`");
    }
    return one.detail === undefined
      ? { name: one.name, description: one.description }
      : { name: one.name, description: one.description, detail: one.detail };
  });
}

function validatedRoster(roster: readonly JevCapability[]): void {
  if (roster.length >= 2) return;
  throw new JevUsageError("a capability ranking needs at least two capabilities");
}

/** Pass one: rank the whole roster, and ask whether the unit wants a capability at all. */
export function jevWideQuestions(roster: readonly JevCapability[]): JevQuestions {
  validatedRoster(roster);
  const gates = Object.entries(JEV_CAPABILITY_GATES).map(([id, instructions]) => [
    `gate::${id}`,
    { type: "noul" as const, instructions, stakes: "passive" as const },
  ]);
  return {
    which: {
      type: "choice",
      instructions: WIDE_INSTRUCTIONS,
      criteria: Object.fromEntries(roster.map((one) => [one.name, one.description])),
      stakes: "passive",
    },
    ...Object.fromEntries(gates),
  };
}

/** A noul the service actually answered. A transport failure reports 0, which is not a reading. */
function answeredProbability(verdict: JevVerdict | undefined): number | undefined {
  if (verdict === undefined) return undefined;
  if (verdict.reason !== undefined && verdict.reason !== "below_threshold") return undefined;
  return verdict.value;
}

/**
 * The mean of the three gate nouls, with `prose_suffices` counted the other way round. It is a mean
 * of probabilities, not of verdicts: a 0.4 that no bar would call yes still carries its 0.4 here.
 *
 * All three or nothing. The judge answers each question on its own, so one can come back missing
 * while the others hold, and a mean of two read against a bar calibrated for three is a different
 * measurement wearing the same name. No reading means nothing is suggested, which is the safe way
 * for this to fail.
 */
export function jevGateFrom(verdicts: Readonly<Record<string, JevVerdict>>): number | undefined {
  const ids = Object.keys(JEV_CAPABILITY_GATES);
  const oriented = ids.flatMap((id) => {
    const probability = answeredProbability(verdicts[`gate::${id}`]);
    if (probability === undefined) return [];
    return [
      INVERTED_GATES.includes(id as (typeof INVERTED_GATES)[number])
        ? 1 - probability
        : probability,
    ];
  });
  if (oriented.length < ids.length) return undefined;
  return oriented.reduce((sum, value) => sum + value, 0) / oriented.length;
}

/**
 * The top of the ranking distribution, which is what pass one is for. The chosen option alone would
 * throw away the second and third candidates, and those are where the lookalikes sit.
 */
export function jevShortlistFrom(verdict: JevVerdict | undefined, size: number): readonly string[] {
  if (verdict === undefined) return [];
  if (verdict.reason !== undefined && verdict.reason !== "below_threshold") return [];
  const distribution = verdict.probabilities;
  if (distribution === undefined) return verdict.leaning === undefined ? [] : [verdict.leaning];
  return Object.entries(distribution)
    .filter(([, probability]) => probability > 0)
    .sort(([, left], [, right]) => right - left)
    .slice(0, size)
    .map(([name]) => name);
}

function criterionFor(capability: JevCapability, excerpt: number): string {
  if (capability.detail === undefined) return capability.description;
  return `${capability.description} — ${capability.detail.slice(0, excerpt)}`;
}

/** Pass two: the same question over the shortlist, with each candidate's own text behind it. */
export function jevRerankQuestions(
  roster: readonly JevCapability[],
  shortlist: readonly string[],
  excerpt: number = JEV_CAPABILITY_EXCERPT_CHARACTERS,
): JevQuestions {
  const byName = new Map(roster.map((one) => [one.name, one]));
  const chosen = shortlist.map((name) => {
    const capability = byName.get(name);
    if (capability === undefined) throw new JevUsageError(`the roster has no capability ${name}`);
    return capability;
  });
  const fits = chosen.map((capability) => [
    `fits::${capability.name}`,
    {
      type: "noul" as const,
      instructions: {
        capability: capability.description,
        question:
          `Does the capability named in \`capability\` do the specific thing the unit of work ` +
          `in \`unit\` asks for?`,
      },
      stakes: "passive" as const,
    },
  ]);
  return {
    which: {
      type: "choice",
      instructions: RERANK_INSTRUCTIONS,
      criteria: Object.fromEntries(
        chosen.map((capability) => [capability.name, criterionFor(capability, excerpt)]),
      ),
      stakes: "passive",
    },
    ...Object.fromEntries(fits),
  };
}

/**
 * Whether the service answered this call at all. A probability that did not clear its bar is an
 * answer; a timeout, a 5xx, a refused key or a schema mismatch is not. Every verdict carrying a
 * transport reason means the call never happened, which is a different thing from what it said.
 */
export function jevCallFailed(verdicts: Readonly<Record<string, JevVerdict>>): boolean {
  const all = Object.values(verdicts);
  if (all.length === 0) return false;
  return all.every(
    (verdict) => verdict.reason !== undefined && verdict.reason !== "below_threshold",
  );
}

/**
 * What to load. Every candidate whose own fits noul clears the bar is suggested, because a unit of
 * work in this repository usually needs two or three; the ranking winner leads the list when it
 * cleared the bar too. Nothing clearing it means nothing here applies, which is a real answer.
 *
 * A pass that did not happen is not that answer. Reporting `nothing_fits` for a timeout would strip
 * every capability out of a brief and read exactly like a considered judgment, so a failed call
 * says `judge_unavailable` and leaves the ranking leader standing as the safe default: pass one
 * already named the matching domain skill, and a transport failure is no reason to drop it.
 */
export function jevCapabilitySuggestion(
  shortlist: readonly string[],
  verdicts: Readonly<Record<string, JevVerdict>>,
  fitsThreshold: number = JEV_CAPABILITY_FITS_THRESHOLD,
): JevCapabilitySuggestion {
  if (jevCallFailed(verdicts)) {
    return {
      load: shortlist.slice(0, 1),
      winner: undefined,
      fits: {},
      reason: "judge_unavailable",
    };
  }
  const fits = Object.fromEntries(
    shortlist.flatMap((name) => {
      const probability = answeredProbability(verdicts[`fits::${name}`]);
      return probability === undefined ? [] : [[name, probability] as const];
    }),
  );
  const passing = shortlist.filter((name) => (fits[name] ?? 0) >= fitsThreshold);
  const chosen = verdicts.which?.leaning;
  const winner = chosen !== undefined && passing.includes(chosen) ? chosen : undefined;
  const load =
    winner === undefined ? passing : [winner, ...passing.filter((one) => one !== winner)];
  return { load, winner, fits, reason: load.length === 0 ? "nothing_fits" : "suggested" };
}
