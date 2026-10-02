#!/usr/bin/env -S bun --no-env-file
// Judges one review finding before anybody edits anything.
//
//   jev-finding --comment <review comment id> --log
//   jev-finding --file <path> --lines 118-130 --finding "<the claim>" --log
//
// It reads the cited lines at the base and the hunk this branch changed in that file, asks the two
// shipped questions over them, and prints what to do with the finding: fix it in this round, record
// it as a follow-up, answer it in the reply, or hand it to a person.
import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { jevNumberFlag, jevTextFileAt, parseJevFlags, runJevCli } from "./cli";
import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
  jevLogDate,
} from "./decision-log";
import {
  JEV_CITED_FILE_MAX_BYTES,
  JEV_FINDING_ROUTES,
  type JevCommentCoordinates,
  type JevFindingCitation,
  jevCommentCitation,
  jevFindingCitationFrom,
  jevFindingQuestions,
  jevFindingRoute,
  jevFindingState,
  jevGitSaysPathIsAbsent,
  jevReviewCommentPath,
  jevSafeCitationPath,
} from "./finding";
import { jevQuestionsFrom } from "./jev-judge";
import { type JevJudgeRequest, JevUsageError, jevDependenciesFrom, judge } from "./judge";

/** Lines either side of the citation, so the judge sees what the cited lines sit in. */
export const JEV_FINDING_CONTEXT_LINES = 12;
/** The committed branch state, never the working tree. */
const BRANCH_REVISION = "HEAD";
const QUESTIONS_DIRECTORY = "questions";

export const JEV_FINDING_USAGE = `Judge one review finding before anybody edits anything.

  jev-finding --comment <id> [options]
  jev-finding --file <path> --lines <from-to> --finding <text> [options]

  --comment <id>        a pull request review comment id; its body, path and lines become the state
  --repo <owner/name>   which repository the comment is in; default: the one gh resolves here
  --body <text>         a pasted comment body; the citation is read out of it
  --body-file <path>    the same, from a file
  --finding <text>      the claim, when it is not the whole comment body
  --finding-file <path> the same, from a file
  --file <path>         the cited file, when no comment names it
  --lines <from-to>     the cited lines at the base, such as 118-130 or 118
  --base <ref>          what this branch is measured against (default origin/main)
  --facts <path>        the repository rule the claim cites, as text
  --questions-directory <path>  directory with agent-operations.json and plan-decisions.json
  --context <n>         lines either side of the citation (default ${JEV_FINDING_CONTEXT_LINES})
  --timeout-ms <n>      how long the one call may take
  --json                print the answer as data instead of text
  --print-state         include the state that was judged, for checking what the judge saw
  --directory <path>    override the consumer-relative decision-log directory
  --log                 append one JSON line to ${JEV_DECISION_LOG_DIRECTORY}/<date>.jsonl
  --help                print this text

  The route is one of: ${JEV_FINDING_ROUTES.join(", ")}. Real and introduced by this change is fixed
  in this round; real and already on the base is a recorded follow-up; not real is answered with one
  sentence of evidence in the reply; unsure goes to a person. Exit 0 for any route, 2 for a usage
  error. A route is an input to the reply, never permission to leave a comment unanswered.
`;

const FLAGS = {
  withValue: [
    "--comment",
    "--repo",
    "--body",
    "--body-file",
    "--finding",
    "--finding-file",
    "--file",
    "--lines",
    "--base",
    "--facts",
    "--context",
    "--timeout-ms",
    "--questions-directory",
    "--directory",
  ],
  switches: ["--json", "--log", "--print-state", "--help", "-h"],
  usage: JEV_FINDING_USAGE,
} as const;

type Comment = { readonly body: string; readonly coordinates: JevCommentCoordinates };

function git(argv: readonly string[]): string {
  try {
    return execFileSync("git", [...argv], { cwd: process.cwd(), encoding: "utf8" });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new JevUsageError(`git ${argv.join(" ")} failed: ${message.split("\n")[0]}`);
  }
}

/**
 * The base has to be a ref this checkout actually has. Without this, an unfetched ref reads as "the
 * base does not have the file" for every path, and every finding would route as newly created.
 */
function validatedBase(base: string): string {
  try {
    execFileSync("git", ["rev-parse", "--verify", "--quiet", `${base}^{commit}`], {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return base;
  } catch {
    throw new JevUsageError(
      `${base} is not a ref in this checkout; fetch it or pass a --base that is`,
    );
  }
}

/**
 * The file at one revision, and never off the disk: git hands over only what the revision holds,
 * so a citation out of somebody else's text cannot reach an untracked or ignored file in the
 * checkout. Nothing back means the revision does not hold the path, and every other failure stops
 * the command, because the callers turn a missing file into a sentence about what the change did.
 */
function fileAt(ref: string, path: string): string | undefined {
  try {
    return execFileSync("git", ["show", `${ref}:${path}`], {
      cwd: process.cwd(),
      encoding: "utf8",
      maxBuffer: JEV_CITED_FILE_MAX_BYTES,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    if (jevGitSaysPathIsAbsent(error)) return undefined;
    const reason = error instanceof Error ? error.message.split("\n")[0] : String(error);
    throw new JevUsageError(
      `git cannot read ${path} at ${ref} (at most ${JEV_CITED_FILE_MAX_BYTES} bytes): ${reason}`,
    );
  }
}

/** The comment as GitHub holds it. An inline review comment already names its own path and line. */
function commentAt(repo: string | undefined, id: string): Comment {
  const written = execFileSync("gh", ["api", jevReviewCommentPath(repo, id)], {
    encoding: "utf8",
  });
  const parsed: unknown = JSON.parse(written);
  const record = parsed as { body?: unknown } & JevCommentCoordinates;
  if (typeof record.body !== "string") {
    throw new JevUsageError(`review comment ${id} carries no body`);
  }
  // Every coordinate the comment carries travels on, because a multi line comment needs
  // `start_line` and an outdated one has only its original pair.
  return {
    body: record.body,
    coordinates: {
      path: record.path,
      line: record.line,
      start_line: record.start_line,
      original_line: record.original_line,
      original_start_line: record.original_start_line,
    },
  };
}

function rangeFrom(raw: string): { readonly from: number; readonly to: number } {
  const matched = /^(\d+)(?:\s*-\s*(\d+))?$/u.exec(raw.trim());
  if (matched === null) throw new JevUsageError("--lines takes a range such as 118-130, or 118");
  const from = Number(matched[1]);
  return { from, to: Number(matched[2] ?? from) };
}

/**
 * What the flags and the comment together say the finding points at.
 *
 * Every way in ends at one `jevSafeCitationPath`, on the way out rather than in each branch: a
 * comment body is text somebody else wrote, the caller reads the cited file off the disk, and the
 * state goes to a third party judge. A branch that forgot the check would send an arbitrary file.
 */
function citationOf(
  flags: ReturnType<typeof parseJevFlags>,
  comment: Comment | undefined,
  body: string,
): JevFindingCitation {
  const citation = citationIn(flags, comment, body);
  return { ...citation, path: jevSafeCitationPath(citation.path) };
}

function citationIn(
  flags: ReturnType<typeof parseJevFlags>,
  comment: Comment | undefined,
  body: string,
): JevFindingCitation {
  const file = flags.value("--file");
  const lines = flags.value("--lines");
  if (file !== undefined && lines !== undefined) return { path: file, ...rangeFrom(lines) };
  const fromComment = comment === undefined ? undefined : jevCommentCitation(comment.coordinates);
  if (fromComment !== undefined) {
    return file === undefined ? fromComment : { ...fromComment, path: file };
  }
  if (file !== undefined && lines === undefined && comment !== undefined) {
    throw new JevUsageError("comment coordinates are missing; pass --lines with --file");
  }
  const read = jevFindingCitationFrom(body);
  if (read !== undefined) return read;
  throw new JevUsageError(
    `no file and lines to read: pass --file with --lines, or a body that cites them\n\n${JEV_FINDING_USAGE}`,
  );
}

type BaseRead = {
  readonly text: string;
  readonly outOfRange: boolean;
  /** The base does not have the file at all, so nothing in it can be pre-existing. */
  readonly fileIsNew: boolean;
};

/**
 * The cited lines as the base holds them, with context either side.
 *
 * Two cases the reviewer's own numbers produce. A range that lands past the end of the file falls
 * back to the whole file and says so, rather than sending the judge nothing and calling the answer
 * a judgment. A file the base does not have at all is the commonest finding of the lot, because a
 * change that creates a file is what gets reviewed; that is a sentence saying so, not a refusal,
 * and it is what lets `finding_is_pre_existing` honestly answer no. A path neither revision has is
 * a typo, and that IS a refusal.
 */
/**
 * What `base_code` says when the base does not have the file. A path the branch does not hold
 * either is a typo, and `currentCodeAt` refuses it there rather than in both places.
 */
function newFileRead(base: string, path: string): BaseRead {
  return {
    text: `${base} does not have ${path}: this change created the file, so nothing in it existed before the change.`,
    outOfRange: false,
    fileIsNew: true,
  };
}

function baseCodeAt(base: string, citation: JevFindingCitation, context: number): BaseRead {
  const atBase = fileAt(base, citation.path);
  if (atBase === undefined) return newFileRead(base, citation.path);
  const whole = atBase.split("\n");
  const from = Math.max(citation.from - context, 1);
  const to = Math.min(citation.to + context, whole.length);
  const sliced = from > whole.length ? [] : whole.slice(from - 1, to);
  if (sliced.length > 0) {
    return { text: sliced.join("\n"), outOfRange: false, fileIsNew: false };
  }
  return { text: whole.join("\n"), outOfRange: true, fileIsNew: false };
}

/**
 * The cited lines as the branch has them now, which is what `finding_is_real` is asked about. A
 * path the branch commit does not hold is refused: reading it off the disk would send an untracked
 * file to the judge, and calling it deleted would invent a fact. Commit it first, then judge it.
 */
function currentCodeAt(citation: JevFindingCitation, context: number): string {
  const written = fileAt(BRANCH_REVISION, citation.path);
  if (written === undefined) {
    throw new JevUsageError(
      `${BRANCH_REVISION} does not hold ${citation.path}; a citation names a committed file`,
    );
  }
  const whole = written.split("\n");
  const from = Math.max(citation.from - context, 1);
  const to = Math.min(citation.to + context, whole.length);
  const sliced = from > whole.length ? [] : whole.slice(from - 1, to);
  return sliced.length > 0 ? sliced.join("\n") : whole.join("\n");
}

function questionsFrom(directory: string): ReturnType<typeof jevFindingQuestions> {
  const packAt = (name: string) => {
    const path = join(directory, name);
    return jevQuestionsFrom(jevTextFileAt(path), path);
  };
  return jevFindingQuestions(packAt("agent-operations.json"), packAt("plan-decisions.json"));
}

function textFrom(
  flags: ReturnType<typeof parseJevFlags>,
  value: string,
  file: string,
): string | undefined {
  const path = flags.value(file);
  return path === undefined ? flags.value(value) : jevTextFileAt(path);
}

async function main(argv: readonly string[]): Promise<number> {
  const flags = parseJevFlags(argv, FLAGS);
  if (flags.has("--help") || flags.has("-h")) {
    process.stdout.write(JEV_FINDING_USAGE);
    return 0;
  }

  const commentId = flags.value("--comment");
  const comment = commentId === undefined ? undefined : commentAt(flags.value("--repo"), commentId);

  const body = textFrom(flags, "--body", "--body-file") ?? comment?.body ?? "";
  const finding = textFrom(flags, "--finding", "--finding-file") ?? body;
  if (finding.trim().length === 0) {
    throw new JevUsageError(`no claim to judge: pass --finding or --body\n\n${JEV_FINDING_USAGE}`);
  }

  const citation = citationOf(flags, comment, body);
  const base = validatedBase(flags.value("--base") ?? "origin/main");
  const context = jevNumberFlag(flags, "--context") ?? JEV_FINDING_CONTEXT_LINES;
  const read = baseCodeAt(base, citation, context);
  const factsPath = flags.value("--facts");
  const state = jevFindingState({
    finding,
    code: currentCodeAt(citation, context),
    baseCode: read.text,
    diff:
      git(["diff", `${base}...HEAD`, "--", citation.path]) ||
      "no hunk: the branch never touched this file",
    ...(factsPath === undefined ? {} : { repositoryFacts: jevTextFileAt(factsPath) }),
  });
  const request: JevJudgeRequest = {
    state,
    questions: questionsFrom(flags.value("--questions-directory") ?? join(process.cwd(), QUESTIONS_DIRECTORY)),
    timeoutMs: jevNumberFlag(flags, "--timeout-ms"),
  };

  const result = await judge(request, jevDependenciesFrom(process.env));
  const answer = {
    citation,
    citationOutOfRange: read.outOfRange,
    fileIsNew: read.fileIsNew,
    ...(flags.has("--print-state") ? { state } : {}),
    ...jevFindingRoute(result.verdicts),
    model: result.model,
    latencyMs: result.latencyMs,
  };
  const where = `${citation.path}:${citation.from}-${citation.to}`;
  process.stdout.write(
    flags.has("--json")
      ? `${JSON.stringify(answer, null, 2)}\n`
      : `${where}\n  ${answer.route}\n  ${answer.why}\n  model ${result.model || "none"}, ${result.latencyMs} ms\n`,
  );

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
