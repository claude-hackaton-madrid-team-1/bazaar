// Every review finding passes the judge before anybody edits anything.
//
// Two questions, in one call, over one state: `finding_is_real` says whether the cited code has the
// defect the claim describes, and `finding_is_pre_existing` says whether the base already had it.
// The pair routes the finding: fix it now, record it as a follow-up, or answer it with one sentence
// of evidence. Without that, a reviewer's confident wrong claim buys a round of work, and a real
// defect that was already on `main` gets fixed inside a slice that never touched it.
//
// The judge does not read the repository, so the caller reads it: the cited lines at the base go in
// `base_code` and the touched hunk goes in `diff`. A question whose answer is a fact gets a guess.
import { type JevQuestion, type JevQuestions, JevUsageError, type JevVerdict } from "./judge";
import type { JevJsonValue } from "./mask";

/** Where a finding says the defect is. */
export type JevFindingCitation = {
  readonly path: string;
  readonly from: number;
  readonly to: number;
};

export type JevFindingInput = {
  /** The claim, quoted as the reviewer wrote it. */
  readonly finding: string;
  /** The cited lines as the branch has them now. `finding_is_real` reads this one. */
  readonly code: string;
  /** The same lines as they stand on the base. `finding_is_pre_existing` reads this one. */
  readonly baseCode: string;
  /** The hunk this branch changed in the cited file. */
  readonly diff: string;
  /** The rule the claim cites, when it cites one. */
  readonly repositoryFacts?: string;
};

/** A GitHub review comment, as far as its coordinates go. */
export type JevCommentCoordinates = {
  readonly path?: string | null;
  readonly line?: number | null;
  readonly start_line?: number | null;
  readonly original_line?: number | null;
  readonly original_start_line?: number | null;
};

/** What the pair of answers says to do with the finding. */
export const JEV_FINDING_ROUTES = [
  "fix_this_round",
  "follow_up",
  "answer_in_the_reply",
  "decide_by_hand",
] as const;
export type JevFindingRoute = (typeof JEV_FINDING_ROUTES)[number];

export type JevRoutedFinding = {
  readonly route: JevFindingRoute;
  /** One line for the reply, naming the two answers that produced the route. */
  readonly why: string;
  readonly real: number;
  readonly preExisting: number | undefined;
};

export const JEV_FINDING_REAL_QUESTION = "finding_is_real";
export const JEV_FINDING_PRE_EXISTING_QUESTION = "finding_is_pre_existing";

/**
 * A GitHub blob link carries the path and the line range the reviewer meant, so it is read first.
 * `path:line` in the prose is the fallback, and it names one line rather than a range.
 */
const BLOB_LINK = /\/blob\/[0-9a-zA-Z._-]+\/([^\s"'#]+)#L(\d+)(?:-L(\d+))?/u;
const PATH_AND_LINE = /`?([\w./-]+\.(?:ts|tsx|js|mjs|cjs|json|md|hpp|cpp|h|sql|yml|yaml)):(\d+)`?/u;

/** The file and lines a finding points at, or nothing when it points at none. */
export function jevFindingCitationFrom(body: string): JevFindingCitation | undefined {
  // A link can be split over two lines in a rendered comment, so newlines inside it are dropped.
  const joined = body.replace(/\n\s*/gu, "");
  const link = BLOB_LINK.exec(joined);
  if (link !== null) {
    const from = Number(link[2]);
    return { path: link[1] ?? "", from, to: Number(link[3] ?? from) };
  }
  const cited = PATH_AND_LINE.exec(body);
  if (cited === null) return undefined;
  const line = Number(cited[2]);
  return { path: cited[1] ?? "", from: line, to: line };
}

/**
 * A citation names a file inside this repository, and nothing else.
 *
 * The path comes out of text somebody else wrote and the cited bytes go to a third party judge,
 * so `../../secrets.env` in a comment body would put an arbitrary local file into that request.
 * The read itself goes through git, which holds only what a revision holds; this refuses the shape
 * too, before any command runs. The masker knows the shapes of secrets, not the contents of files.
 */
export function jevSafeCitationPath(path: string): string {
  const written = path.trim();
  if (written.length === 0) throw new JevUsageError("a citation names no file");
  const escapes =
    written.startsWith("/") ||
    written.startsWith("~") ||
    /^[A-Za-z]:[\\/]/u.test(written) ||
    written.split(/[\\/]/u).includes("..");
  if (escapes) {
    throw new JevUsageError(
      `a citation must name a file inside the repository, and ${written.slice(0, 40)} does not`,
    );
  }
  return path;
}

/**
 * How many bytes of one cited file the tool takes from git. `execFileSync` defaults to one
 * mebibyte and turns anything larger into a throw, which read as "this revision does not hold the
 * path" and told the judge the change had created the file. A file above this is refused by name.
 */
export const JEV_CITED_FILE_MAX_BYTES = 8 * 1024 * 1024;

/** The two sentences git uses to say a tree does not hold a path. */
const GIT_PATH_NOT_IN_TREE = /does not exist in|exists on disk, but not in/u;

/**
 * Whether a failed `git show <revision>:<path>` was git saying the tree lacks the path. Every
 * other failure, an oversized file included, is the tool failing, and reading one of those as an
 * absence invents a sentence about what this change did to the file.
 */
export function jevGitSaysPathIsAbsent(error: unknown): boolean {
  const thrown = error as { code?: unknown; stderr?: unknown };
  if (thrown.code === "ENOBUFS") return false;
  return GIT_PATH_NOT_IN_TREE.test(String(thrown.stderr ?? ""));
}

/**
 * Where one pull request review comment lives in the API. It is `pulls/comments/{id}`, not
 * `pulls/{number}/comments/{id}`: the second path lists a pull request's comments and answers 404
 * for a single id, which is how this was found, by using the command on a real comment.
 */
export function jevReviewCommentPath(repo: string | undefined, id: string): string {
  const owner = repo === undefined || repo.length === 0 ? "{owner}/{repo}" : repo;
  return `repos/${owner}/pulls/comments/${id}`;
}

/**
 * The lines a review comment covers. A comment on several lines carries `start_line` and `line`;
 * one that went stale after a push carries `line: null` and keeps its original pair. Reading `line`
 * alone judges the last line of a block, or nothing at all once the comment is outdated.
 */
export function jevCommentCitation(comment: JevCommentCoordinates): JevFindingCitation | undefined {
  const path = comment.path ?? undefined;
  if (path === undefined || path.length === 0) return undefined;
  const end = comment.line ?? comment.original_line ?? undefined;
  if (end === undefined) return undefined;
  const start =
    (comment.line ?? undefined) === undefined
      ? (comment.original_start_line ?? end)
      : (comment.start_line ?? end);
  return { path, from: Math.min(start, end), to: Math.max(start, end) };
}

/** A field a question names. Absent and empty are the same caller mistake, never a TypeError. */
function required(name: string, value: string | undefined): string {
  if (value !== undefined && value.trim().length > 0) return value;
  throw new JevUsageError(`a finding needs ${name}; the judge does not read the repository`);
}

/**
 * Every field the two questions read, and nothing else. `finding_is_real` asks about the code in
 * `code`, so a state carrying only `base_code` hands that question an absent field and gets an
 * answer about nothing; `finding_is_pre_existing` asks about `base_code` and `diff`.
 */
export function jevFindingState(input: JevFindingInput): JevJsonValue {
  const finding = required("the claim itself", input.finding);
  const code = required("the cited lines as the branch has them now (`code`)", input.code);
  const baseCode = required("the cited lines at the base (`base_code`)", input.baseCode);
  const diff = required("the touched hunk (`diff`)", input.diff);
  const facts = input.repositoryFacts?.trim() ?? "";
  return {
    finding,
    code,
    base_code: baseCode,
    diff,
    ...(facts.length === 0 ? {} : { repository_facts: facts }),
  };
}

function fromPack(pack: JevQuestions, id: string, name: string): JevQuestion {
  const question = pack[id];
  if (question !== undefined) return question;
  throw new JevUsageError(`${name} no longer holds ${id}`);
}

/**
 * The two questions, selected out of the canonical packs at call time. Copying either one into this
 * file would give the repository a second text to keep in step with the pack, and only the pack is
 * validated by `jev-question-pack.test.ts`.
 */
export function jevFindingQuestions(
  operationsPack: JevQuestions,
  planPack: JevQuestions,
): JevQuestions {
  return {
    [JEV_FINDING_REAL_QUESTION]: fromPack(
      operationsPack,
      JEV_FINDING_REAL_QUESTION,
      "agent-operations.json",
    ),
    [JEV_FINDING_PRE_EXISTING_QUESTION]: fromPack(
      planPack,
      JEV_FINDING_PRE_EXISTING_QUESTION,
      "plan-decisions.json",
    ),
  };
}

function routed(
  route: JevFindingRoute,
  why: string,
  real: number,
  preExisting: number | undefined,
): JevRoutedFinding {
  return { route, why, real, preExisting };
}

/**
 * The route, from the two verdicts. A closed lookup rather than a ladder, and every branch says
 * which answers put it there, because that sentence is what goes back to the reviewer.
 *
 * An unsure `finding_is_real` goes to a person: a claim nobody can call true or false is exactly
 * the one an agent must not decide by default, and `AGENTS.md` makes an unanswered comment block
 * the review. An unsure `finding_is_pre_existing` behind a real defect is fixed in this round,
 * because the native review contract escalates unknown causality rather than deferring it.
 */
export function jevFindingRoute(verdicts: Readonly<Record<string, JevVerdict>>): JevRoutedFinding {
  const real = verdicts[JEV_FINDING_REAL_QUESTION];
  const preExisting = verdicts[JEV_FINDING_PRE_EXISTING_QUESTION];
  const realValue = real?.value ?? 0;
  const priorValue = preExisting?.value;

  switch (real?.verdict) {
    case "no":
      return routed(
        "answer_in_the_reply",
        `the cited code does not have the claimed defect (${realValue.toFixed(2)}), so the reply ` +
          "carries one sentence of evidence and no edit",
        realValue,
        priorValue,
      );
    case "yes":
      break;
    default:
      return routed(
        "decide_by_hand",
        `whether the defect is real is undecided (${realValue.toFixed(2)}, leaning ` +
          `${real?.leaning ?? "nothing"}), so a person reads the cited lines and decides`,
        realValue,
        priorValue,
      );
  }

  switch (preExisting?.verdict) {
    case "yes":
      return routed(
        "follow_up",
        `the defect is real (${realValue.toFixed(2)}) and the base already has it ` +
          `(${(priorValue ?? 0).toFixed(2)}), so it is a recorded follow-up, not work in this slice`,
        realValue,
        priorValue,
      );
    case "no":
      return routed(
        "fix_this_round",
        `the defect is real (${realValue.toFixed(2)}) and this change introduced it ` +
          `(${(priorValue ?? 0).toFixed(2)}), so it is fixed in this round`,
        realValue,
        priorValue,
      );
    default:
      return routed(
        "fix_this_round",
        `the defect is real (${realValue.toFixed(2)}) and whose it is came back unknown, so it is ` +
          "fixed in this round: unknown causality escalates, it does not defer",
        realValue,
        priorValue,
      );
  }
}
