// Composite scoring: one atomic score per dimension per option, combined by this code with weights
// the caller wrote. `https://docs.typesafe.ai/patterns/composite-scoring.md` is the pattern.
//
// Five options scored against one criterion that mixed six dimensions came back undecided every
// time, because a criterion that asks six things at once has no concentrated answer to give. The
// same judgment split into one question per dimension per option answers each one on its own, and
// the weighting that used to hide inside the criterion is visible here instead.
import { JevUsageError, type JevVerdict } from "./judge";
import type { JevJsonValue, JevQuestion, JevQuestions, JevStakes } from "./mask";

/** How far ahead the leader has to be before this tool calls it a winner rather than a tie. */
export const JEV_SCORE_DEFAULT_MARGIN = 0.05;
/** The docs' rule: a level names a situation, not a word on a scale. */
export const JEV_SCORE_MIN_LEVEL_CHARACTERS = 20;
/** The judge itself refuses fewer than two or more than ten. */
export const JEV_SCORE_MIN_LEVELS = 2;

export type JevScoreDimension = {
  /** Relative importance. Weights are normalised in code, so any positive scale works. */
  readonly weight?: number;
  readonly question: JevJsonValue;
  readonly criteria: readonly JevJsonValue[];
  readonly stakes?: JevStakes;
};

export type JevScorePlan = {
  readonly state: JevJsonValue;
  readonly options: Readonly<Record<string, JevJsonValue>>;
  readonly dimensions: Readonly<Record<string, JevScoreDimension>>;
};

export type JevScoreCell = {
  readonly dimension: string;
  /** The score as the model returned it, whatever the bar then did with it. */
  readonly score: number | undefined;
  /** The score divided by its own top level, so two dimensions of different length compare. */
  readonly normalized: number | undefined;
  readonly confidence: number;
  readonly decided: boolean;
};

export type JevScoreRow = {
  readonly option: string;
  readonly cells: readonly JevScoreCell[];
  readonly weighted: number;
  /** The least certain cell behind this row. One wrong dimension is enough to spoil the total. */
  readonly weakest: number;
  readonly answeredCells: number;
  readonly undecidedCells: number;
};

/** Why the table named a winner, or why it did not. */
export type JevScoreReason =
  | "decided"
  | "cells_undecided"
  | "cells_unanswered"
  | "margin_too_small";

export type JevScoreTable = {
  readonly weights: Readonly<Record<string, number>>;
  /** Highest weighted total first. */
  readonly rows: readonly JevScoreRow[];
  readonly winner: string | undefined;
  /** The leader, named even when it is not a winner. */
  readonly leaning: string | undefined;
  readonly margin: number;
  readonly minimumMargin: number;
  readonly reason: JevScoreReason;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validatedLevels(name: string, criteria: readonly JevJsonValue[]): void {
  if (!Array.isArray(criteria) || criteria.length < JEV_SCORE_MIN_LEVELS) {
    throw new JevUsageError(
      `dimension ${name}: a score needs at least ${JEV_SCORE_MIN_LEVELS} levels`,
    );
  }
  for (const level of criteria) {
    if (typeof level !== "string") continue;
    if (level.length >= JEV_SCORE_MIN_LEVEL_CHARACTERS) continue;
    throw new JevUsageError(
      `dimension ${name}: every level describes a concrete situation, so "${level}" is too short`,
    );
  }
}

function validatedWeight(name: string, weight: number | undefined): number {
  if (weight === undefined) return 1;
  if (!Number.isFinite(weight) || weight <= 0) {
    throw new JevUsageError(`dimension ${name}: weight must be a positive number`);
  }
  return weight;
}

function validatedPlan(plan: JevScorePlan): void {
  const options = Object.keys(plan.options ?? {});
  const dimensions = Object.entries(plan.dimensions ?? {});
  if (options.length < 2) throw new JevUsageError("a comparison needs at least two options");
  if (dimensions.length === 0) throw new JevUsageError("a comparison needs at least one dimension");
  for (const [name, dimension] of dimensions) {
    validatedLevels(name, dimension.criteria);
    validatedWeight(name, dimension.weight);
  }
}

/** The plan file, checked for the three parts it has to name. */
export function jevScorePlanFrom(parsed: Record<string, unknown>): JevScorePlan {
  if (parsed.state === undefined) throw new JevUsageError("the plan must name a `state`");
  if (!isRecord(parsed.options)) throw new JevUsageError("the plan must name an `options` object");
  if (!isRecord(parsed.dimensions)) {
    throw new JevUsageError("the plan must name a `dimensions` object");
  }
  const plan = parsed as unknown as JevScorePlan;
  validatedPlan(plan);
  return plan;
}

/** One question id per cell. The id never reaches the model, so it may carry the pair. */
export function jevScoreCellId(dimension: string, option: string): string {
  return `${dimension}::${option}`;
}

function cellQuestion(
  dimension: JevScoreDimension,
  option: JevJsonValue,
  optionName: string,
): JevQuestion {
  return {
    type: "score",
    instructions: {
      option,
      question: dimension.question,
      note:
        `Judge only the option in \`option\`, against this one criterion. The same question is ` +
        `asked once per option over the same state, so do not compare it with another option ` +
        `here, and do not read the option name "${optionName}" as an argument for it.`,
    },
    criteria: dimension.criteria as JevJsonValue,
    stakes: dimension.stakes ?? "design",
  };
}

/** Every dimension against every option, for one fan-out call. */
export function jevScoreQuestions(plan: JevScorePlan): JevQuestions {
  validatedPlan(plan);
  return Object.fromEntries(
    Object.entries(plan.dimensions).flatMap(([name, dimension]) =>
      Object.entries(plan.options).map(([optionName, option]) => [
        jevScoreCellId(name, optionName),
        cellQuestion(dimension, option, optionName),
      ]),
    ),
  );
}

function normalisedWeights(plan: JevScorePlan): Readonly<Record<string, number>> {
  const raw = Object.entries(plan.dimensions).map(
    ([name, dimension]) => [name, validatedWeight(name, dimension.weight)] as const,
  );
  const total = raw.reduce((sum, [, weight]) => sum + weight, 0);
  return Object.fromEntries(raw.map(([name, weight]) => [name, rounded(weight / total)]));
}

/** Binary floats again: 3 / 4 is exact, but 1 / 3 * 3 is not, and a weight is read by a human. */
function rounded(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

/** The score survives the bar in `leaning`, so an undecided cell still says where it pointed. */
function scoreOf(verdict: JevVerdict | undefined): number | undefined {
  if (verdict === undefined) return undefined;
  const written = verdict.verdict === "undecided" ? verdict.leaning : verdict.verdict;
  if (written === undefined) return undefined;
  const score = Number(written);
  return Number.isFinite(score) ? score : undefined;
}

function cellOf(dimension: string, levels: number, verdict: JevVerdict | undefined): JevScoreCell {
  const score = scoreOf(verdict);
  const top = Math.max(levels - 1, 1);
  return {
    dimension,
    score,
    normalized: score === undefined ? undefined : rounded(Math.min(Math.max(score / top, 0), 1)),
    confidence: verdict?.value ?? 0,
    decided: verdict !== undefined && verdict.verdict !== "undecided",
  };
}

/**
 * The weighted total over the cells that answered, with their weights renormalised over those
 * cells. A cell the service never answered is unknown, not a zero: reading it as zero would rank an
 * option down for a transport failure.
 */
function weightedOf(
  cells: readonly JevScoreCell[],
  weights: Readonly<Record<string, number>>,
): number {
  const answered = cells.filter((cell) => cell.normalized !== undefined);
  const weight = answered.reduce((sum, cell) => sum + (weights[cell.dimension] ?? 0), 0);
  if (weight === 0) return 0;
  const total = answered.reduce(
    (sum, cell) => sum + (weights[cell.dimension] ?? 0) * (cell.normalized ?? 0),
    0,
  );
  return rounded(total / weight);
}

function rowOf(
  option: string,
  plan: JevScorePlan,
  weights: Readonly<Record<string, number>>,
  verdicts: Readonly<Record<string, JevVerdict>>,
): JevScoreRow {
  const cells = Object.entries(plan.dimensions).map(([name, dimension]) =>
    cellOf(name, dimension.criteria.length, verdicts[jevScoreCellId(name, option)]),
  );
  const answered = cells.filter((cell) => cell.normalized !== undefined);
  return {
    option,
    cells,
    weighted: weightedOf(cells, weights),
    weakest: answered.length === 0 ? 0 : Math.min(...answered.map((cell) => cell.confidence)),
    answeredCells: answered.length,
    undecidedCells: cells.filter((cell) => !cell.decided).length,
  };
}

/**
 * A row that is missing a cell is weighted over the cells it does have, so its total is not on the
 * same scale as a complete one and the missing cell could put it in front. One hole anywhere means
 * the ranking is not known, so the leader of an incomplete table is a leaning and never a winner.
 */
function reasonFor(
  rows: readonly JevScoreRow[],
  margin: number,
  minimumMargin: number,
  dimensions: number,
): JevScoreReason {
  if (rows.some((row) => row.answeredCells < dimensions)) return "cells_unanswered";
  if (rows[0] !== undefined && rows[0].undecidedCells > 0) return "cells_undecided";
  if (margin < minimumMargin) return "margin_too_small";
  return "decided";
}

/**
 * The table this code builds from the answers. A winner is named only when every cell behind the
 * leader decided and its lead is at least `minimumMargin`; otherwise the leader is a leaning, which
 * is what a coordinator needs in order to bring the choice to a person instead of guessing.
 */
export function jevScoreTable(
  plan: JevScorePlan,
  verdicts: Readonly<Record<string, JevVerdict>>,
  minimumMargin: number = JEV_SCORE_DEFAULT_MARGIN,
): JevScoreTable {
  const weights = normalisedWeights(plan);
  const rows = Object.keys(plan.options)
    .map((option) => rowOf(option, plan, weights, verdicts))
    .sort((left, right) => right.weighted - left.weighted);
  const leader = rows[0];
  if (leader === undefined) {
    return {
      weights,
      rows,
      winner: undefined,
      leaning: undefined,
      margin: 0,
      minimumMargin,
      reason: "cells_unanswered",
    };
  }
  const margin = rounded(leader.weighted - (rows[1]?.weighted ?? 0));
  const reason = reasonFor(rows, margin, minimumMargin, Object.keys(plan.dimensions).length);
  return {
    weights,
    rows,
    winner: reason === "decided" ? leader.option : undefined,
    leaning: leader.option,
    margin,
    minimumMargin,
    reason,
  };
}

function cellText(cell: JevScoreCell): string {
  if (cell.score === undefined) return "     -      ";
  const mark = cell.decided ? " " : "*";
  return `${cell.score.toFixed(2)} (${cell.confidence.toFixed(2)})${mark}`;
}

/** A fixed-width table, plus the line that says what it decided and why. */
export function jevScoreTableText(table: JevScoreTable): string {
  const dimensions = Object.keys(table.weights);
  const width = Math.max(8, ...table.rows.map((row) => row.option.length));
  const header = [
    "option".padEnd(width),
    ...dimensions.map((name) => `${name} ${table.weights[name]?.toFixed(2)}`.padStart(26)),
    "weighted".padStart(10),
    "weakest".padStart(10),
  ].join("");
  const body = table.rows.map((row) =>
    [
      row.option.padEnd(width),
      ...dimensions.map((name) =>
        cellText(row.cells.find((cell) => cell.dimension === name) as JevScoreCell).padStart(26),
      ),
      row.weighted.toFixed(3).padStart(10),
      row.weakest.toFixed(2).padStart(10),
    ].join(""),
  );
  const decision =
    table.winner === undefined
      ? `no winner (${table.reason}); leaning ${table.leaning ?? "nothing"}`
      : `winner ${table.winner}`;
  return `${[header, ...body].join("\n")}\n\n${decision}, margin ${table.margin.toFixed(
    3,
  )} against a minimum of ${table.minimumMargin.toFixed(3)}\n* marks a cell below its own bar\n`;
}
