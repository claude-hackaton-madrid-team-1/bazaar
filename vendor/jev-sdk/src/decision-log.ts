// The one decision log every Jev surface writes to and the report reads back.
//
// Two kinds of line share the file. A `decision` line is one call: the masked state digest, the
// masked questions, the bar each one had to clear and the verdict it produced. An `outcome` line is
// what happened afterwards, keyed on the same digest. Without the second kind the thresholds stay
// guesses, because nothing records that round 6 was wrong and round 7 was right.
//
// The raw state never reaches this file. Only the digest of the masked text does.
import { appendFileSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { type JevJudgeRequest, type JevJudgeResult, maskedJevRequest } from "./judge";
import { type JevQuestion, type JevStakes, maskedDigest, maskJevText } from "./mask";

/** Consumer-relative default; the consumer must ignore `.local/` in Git. */
export const JEV_DECISION_LOG_DIRECTORY = ".local/jev-decisions";

/** How a decision turned out, once the work it routed finished. */
export const JEV_OUTCOMES = ["right", "wrong", "unknown"] as const;
export type JevOutcome = (typeof JEV_OUTCOMES)[number];

export type JevDecisionEntry = {
  readonly kind: "decision";
  readonly at?: string;
  readonly stateDigest?: string;
  /** The state AND the question ids, so two calls over one state stay two calls. */
  readonly callDigest?: string;
  readonly questions?: Readonly<Record<string, unknown>>;
  readonly thresholds?: Readonly<Record<string, number>>;
  readonly stakes?: Readonly<Record<string, JevStakes>>;
  readonly verdicts: Readonly<Record<string, { readonly verdict?: string }>>;
};

export type JevOutcomeEntry = {
  readonly kind: "outcome";
  readonly at?: string;
  readonly stateDigest: string;
  readonly outcome: JevOutcome;
  /** Absent when the whole call turned out right or wrong, not one answer in it. */
  readonly question?: string;
  readonly note?: string;
};

export type JevLogEntry = JevDecisionEntry | JevOutcomeEntry;

export type JevOutcomeInput = {
  readonly stateDigest: string;
  readonly outcome: JevOutcome;
  readonly question?: string;
  readonly note?: string;
};

/** What a report prints for one question id, so a bar can be tuned on counted results. */
export type JevQuestionTally = {
  readonly question: string;
  readonly calls: number;
  readonly decided: number;
  readonly undecided: number;
  readonly right: number;
  readonly wrong: number;
  readonly unknown: number;
};

type JsonObject = { readonly [key: string]: unknown };

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The day a line belongs to, which is the file it is appended to. */
export function jevLogDate(at: string): string {
  return at.slice(0, 10);
}

/**
 * One call's identity: the state it judged and the questions it asked. The state digest alone is
 * not enough, because the capability selector sends two calls over the same state and asks
 * different questions in each; an outcome keyed on the state would credit both.
 */
export function jevCallDigest(stateDigest: string, questionIds: readonly string[]): string {
  return maskedDigest(`${stateDigest}\n${[...questionIds].sort().join("\n")}`);
}

/** The stakes each question declared, so a tuning pass can group by what the bar was meant to be. */
function declaredStakes(questions: Readonly<Record<string, JevQuestion>>) {
  const named = Object.entries(questions).filter(([, question]) => question.stakes !== undefined);
  if (named.length === 0) return {};
  return { stakes: Object.fromEntries(named.map(([id, question]) => [id, question.stakes])) };
}

/** One call: the masked digest of the state, the masked questions, the bars and the verdicts. */
export function jevDecisionLine(
  request: JevJudgeRequest,
  result: JevJudgeResult,
  at: string,
): string {
  const masked = maskedJevRequest(request);
  const written = typeof masked.state === "string" ? masked.state : JSON.stringify(masked.state);
  const stateDigest = maskedDigest(written);
  return JSON.stringify({
    kind: "decision",
    at,
    model: result.model,
    latencyMs: result.latencyMs,
    stateDigest,
    callDigest: jevCallDigest(stateDigest, Object.keys(masked.questions)),
    stateCharacters: written.length,
    questions: masked.questions,
    thresholds: masked.thresholds,
    ...declaredStakes(request.questions),
    verdicts: result.verdicts,
  });
}

/** What the call turned out to be. The note is masked, exactly like a state. */
export function jevOutcomeLine(entry: JevOutcomeInput, at: string): string {
  return JSON.stringify({
    kind: "outcome",
    at,
    stateDigest: entry.stateDigest,
    outcome: entry.outcome,
    ...(entry.question === undefined ? {} : { question: entry.question }),
    ...(entry.note === undefined ? {} : { note: maskJevText(entry.note) }),
  });
}

export function appendJevDecision(directory: string, date: string, line: string): void {
  mkdirSync(directory, { recursive: true });
  appendFileSync(join(directory, `${date}.jsonl`), `${line}\n`, "utf8");
}

function dayFilesIn(directory: string): readonly string[] {
  try {
    return readdirSync(directory)
      .filter((name) => name.endsWith(".jsonl"))
      .sort();
  } catch {
    return [];
  }
}

/** A line written before `kind` existed is a decision, because outcomes came later. */
function entryFrom(parsed: JsonObject): JevLogEntry | undefined {
  if (parsed.kind !== "outcome") {
    return isJsonObject(parsed.verdicts)
      ? ({ ...parsed, kind: "decision" } as JevDecisionEntry)
      : undefined;
  }
  if (typeof parsed.stateDigest !== "string") return undefined;
  if (!JEV_OUTCOMES.includes(parsed.outcome as JevOutcome)) return undefined;
  return parsed as unknown as JevOutcomeEntry;
}

function entriesIn(path: string): readonly JevLogEntry[] {
  const lines = readFileSync(path, "utf8").split("\n");
  return lines.flatMap((line) => {
    if (line.trim().length === 0) return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      // A truncated or hand-edited line loses itself, never the rest of the day.
      return [];
    }
    if (!isJsonObject(parsed)) return [];
    const entry = entryFrom(parsed);
    return entry === undefined ? [] : [entry];
  });
}

/** Every entry in the log, oldest day first. A line it cannot read is skipped, not thrown. */
export function readJevLog(directory: string): readonly JevLogEntry[] {
  return dayFilesIn(directory).flatMap((name) => entriesIn(join(directory, name)));
}

type Counters = { calls: number; decided: number; undecided: number } & Record<JevOutcome, number>;

/** One call in the log: the questions it asked, under both of the digests that name it. */
type LoggedCall = { readonly ids: ReadonlySet<string> };

function emptyCounters(): Counters {
  return { calls: 0, decided: 0, undecided: 0, right: 0, wrong: 0, unknown: 0 };
}

function indexed(calls: Map<string, LoggedCall[]>, digest: string | undefined, call: LoggedCall) {
  if (digest === undefined) return;
  calls.set(digest, [...(calls.get(digest) ?? []), call]);
}

function countDecision(
  entry: JevDecisionEntry,
  counters: Map<string, Counters>,
  calls: Map<string, LoggedCall[]>,
): void {
  const ids = new Set<string>();
  for (const [id, verdict] of Object.entries(entry.verdicts)) {
    const row = counters.get(id) ?? emptyCounters();
    row.calls += 1;
    if (verdict.verdict === "undecided") row.undecided += 1;
    else row.decided += 1;
    counters.set(id, row);
    ids.add(id);
  }
  const call: LoggedCall = { ids };
  indexed(calls, entry.callDigest, call);
  // A line written before `callDigest` existed is reachable by its state digest alone.
  if (entry.callDigest !== entry.stateDigest) indexed(calls, entry.stateDigest, call);
}

/** Every question id the matched calls asked, or nothing when the matches disagree. */
function everyIdOf(matched: readonly LoggedCall[]): readonly string[] {
  const first = matched[0];
  if (first === undefined) return [];
  const disagrees = matched.some(
    (call) => call.ids.size !== first.ids.size || [...call.ids].some((id) => !first.ids.has(id)),
  );
  // A digest that covers two calls with different questions names no single call, so an outcome
  // that does not name a question has nothing it can honestly be counted for.
  return disagrees ? [] : [...first.ids];
}

/** Which question ids one outcome speaks for: the named one, or every id its call asked. */
function idsFor(entry: JevOutcomeEntry, calls: Map<string, LoggedCall[]>): readonly string[] {
  const matched = calls.get(entry.stateDigest);
  if (matched === undefined) return [];
  if (entry.question === undefined) return everyIdOf(matched);
  const named = entry.question;
  return matched.some((call) => call.ids.has(named)) ? [named] : [];
}

/**
 * Calls, decided, undecided and the recorded outcomes per question id, ordered by id. An outcome
 * with no `question` counts once for every question its call asked, because the call is what was
 * right or wrong. An outcome for a call that is not in the log counts for nothing, and so does one
 * whose digest covers two calls that asked different questions: that digest names no single call.
 */
export function jevLogTally(entries: readonly JevLogEntry[]): readonly JevQuestionTally[] {
  const counters = new Map<string, Counters>();
  const calls = new Map<string, LoggedCall[]>();
  for (const entry of entries) {
    if (entry.kind === "decision") countDecision(entry, counters, calls);
  }
  for (const entry of entries) {
    if (entry.kind !== "outcome") continue;
    for (const id of idsFor(entry, calls)) {
      const row = counters.get(id);
      if (row !== undefined) row[entry.outcome] += 1;
    }
  }
  return [...counters.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([question, row]) => ({ question, ...row }));
}
