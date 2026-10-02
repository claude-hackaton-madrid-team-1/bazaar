import { describe, expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  JEV_FINDING_ROUTES,
  type JevFindingCitation,
  jevCommentCitation,
  jevFindingCitationFrom,
  jevFindingQuestions,
  jevFindingRoute,
  jevFindingState,
  jevGitSaysPathIsAbsent,
  jevReviewCommentPath,
  jevSafeCitationPath,
} from "../src/finding";
import { jevQuestionsFrom } from "../src/jev-judge";
import { JevUsageError, type JevVerdict, maskedJevRequest } from "../src/judge";

const OUTSIDE_DIFF = [
  '- <img alt="P1" src="https://example.invalid/p1.svg" align="top">&nbsp;**Outcomes Conflate ',
  "Separate Calls** `src/decision-log.ts:2060` ",
  '<a href="https://github.com/sample-org/sample-repo/blob/4e8931ed0/src/',
  'decision-log.ts#L2051-L2060">x</a>',
  "",
  "  The capability selector logs two calls that reuse the same state but ask different questions.",
].join("\n");

function noul(id: "real" | "pre_existing", verdict: string, value: number): JevVerdict {
  return {
    type: "noul",
    verdict,
    value,
    valueKind: "noul-probability",
    threshold: id === "real" ? 0.6 : 0.75,
    ...(verdict === "undecided" ? { reason: "below_threshold" as const } : {}),
    leaning: value >= 0.5 ? "yes" : "no",
    margin: Math.abs(value * 2 - 1),
  };
}

function verdicts(real: JevVerdict, preExisting?: JevVerdict): Readonly<Record<string, JevVerdict>> {
  return preExisting === undefined
    ? { finding_is_real: real }
    : { finding_is_real: real, finding_is_pre_existing: preExisting };
}

describe("the lines a finding cites", () => {
  test("reads the range out of the link the reviewer attached", () => {
    expect(jevFindingCitationFrom(OUTSIDE_DIFF)).toEqual({
      path: "src/decision-log.ts",
      from: 2051,
      to: 2060,
    });
  });

  test("reads a single line link as a range of one", () => {
    const body = 'see <a href="https://github.com/o/r/blob/abc/src/mask.ts#L42">x</a>';
    expect(jevFindingCitationFrom(body)).toEqual({
      path: "src/mask.ts",
      from: 42,
      to: 42,
    });
  });

  test("falls back to the backticked path and line when there is no link", () => {
    expect(jevFindingCitationFrom("the guard in `src/judge.ts:118` is wrong")).toEqual({
      path: "src/judge.ts",
      from: 118,
      to: 118,
    });
  });

  test("a body that cites nothing gives nothing, rather than a guessed path", () => {
    expect(jevFindingCitationFrom("this looks risky to me")).toBeUndefined();
  });

  test("a link to another repository's blob is still a citation of that path", () => {
    const body = "https://github.com/other/repo/blob/deadbeef/packages/contracts/src/a.ts#L1-L4";
    expect(jevFindingCitationFrom(body)?.path).toBe("packages/contracts/src/a.ts");
  });
});

describe("the state one finding is judged over", () => {
  const state = jevFindingState({
    finding: "The capability selector logs two calls that reuse the same state.",
    code: "indexed(calls, entry.callDigest, call);",
    baseCode: "const asked = askedBy.get(entry.stateDigest ?? '');",
    diff: "+  indexed(calls, entry.callDigest, call);",
    repositoryFacts: "AGENTS.md: an unread or unanswered Greptile comment blocks the review.",
  }) as Record<string, unknown>;

  // `finding_is_real` asks whether the code in `code` has the defect. A state with `base_code` and
  // no `code` hands that question an absent field, so it answers about nothing.
  test("names every field the two questions read, and nothing else", () => {
    expect(Object.keys(state).sort()).toEqual([
      "base_code",
      "code",
      "diff",
      "finding",
      "repository_facts",
    ]);
  });

  test("`code` is the cited lines as the branch has them now", () => {
    expect(state.code).toBe("indexed(calls, entry.callDigest, call);");
    expect(state.base_code).toBe("const asked = askedBy.get(entry.stateDigest ?? '');");
  });

  test("quotes the claim as written, with no adjective added to it", () => {
    expect(state.finding).toBe("The capability selector logs two calls that reuse the same state.");
  });

  test("the facts are left out when the finding cites no rule", () => {
    const bare = jevFindingState({
      finding: "a claim",
      code: "now",
      baseCode: "before",
      diff: "a hunk",
    }) as Record<string, unknown>;
    expect(Object.keys(bare).sort()).toEqual(["base_code", "code", "diff", "finding"]);
  });

  test.each([
    ["no claim", { finding: "  ", code: "n", baseCode: "b", diff: "d" }],
    ["no code as it stands now", { finding: "a claim", code: " ", baseCode: "b", diff: "d" }],
    ["no base code", { finding: "a claim", code: "n", baseCode: "", diff: "d" }],
  ])("a finding with %s is a caller mistake, never a judgment", (_label, input) => {
    expect(() => jevFindingState(input)).toThrow(JevUsageError);
  });
});

describe("the two questions the rule asks, taken from the shipped packs", () => {
  const packs = join(import.meta.dir, "..", "questions");
  function packAt(name: string) {
    const path = join(packs, name);
    return jevQuestionsFrom(readFileSync(path, "utf8"), path);
  }
  const operations = packAt("agent-operations.json");
  const plan = packAt("plan-decisions.json");
  const questions = jevFindingQuestions(operations, plan);

  test("is finding_is_real first, then finding_is_pre_existing", () => {
    expect(Object.keys(questions)).toEqual(["finding_is_real", "finding_is_pre_existing"]);
  });

  test("takes each question from its own pack rather than holding a copy", () => {
    expect(questions.finding_is_real).toEqual(operations.finding_is_real);
    expect(questions.finding_is_pre_existing).toEqual(plan.finding_is_pre_existing);
  });

  test("is real judged at the passive bar, because a wrong read costs one reply", () => {
    expect(questions.finding_is_real?.stakes).toBe("passive");
    const masked = maskedJevRequest({ state: { finding: "a claim" }, questions });
    expect(masked.thresholds.finding_is_real).toBe(0.6);
    expect(masked.thresholds.finding_is_pre_existing).toBe(0.75);
  });

  test("a pack that lost one of the two is a caller mistake", () => {
    expect(() => jevFindingQuestions({}, plan)).toThrow(JevUsageError);
    expect(() => jevFindingQuestions(operations, {})).toThrow(JevUsageError);
  });
});

describe("what the two answers route the finding to", () => {
  test("the routes are a closed vocabulary", () => {
    expect([...JEV_FINDING_ROUTES]).toEqual([
      "fix_this_round",
      "follow_up",
      "answer_in_the_reply",
      "decide_by_hand",
    ]);
  });

  test("real and introduced by this change is fixed in this round", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "yes", 0.92), noul("pre_existing", "no", 0.08)),
    );
    expect(routed.route).toBe("fix_this_round");
  });

  test("real but already on the base is a follow-up, not work inside this slice", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "yes", 0.92), noul("pre_existing", "yes", 0.9)),
    );
    expect(routed.route).toBe("follow_up");
  });

  test("not real is answered with one sentence of evidence in the reply", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "no", 0.05), noul("pre_existing", "yes", 0.9)),
    );
    expect(routed.route).toBe("answer_in_the_reply");
  });

  test("real with unknown causality is fixed in this round, because unknown escalates", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "yes", 0.92), noul("pre_existing", "undecided", 0.6)),
    );
    expect(routed.route).toBe("fix_this_round");
    expect(routed.why).toContain("unknown");
  });

  test("an unsure judge hands the finding back to a person, never to a default", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "undecided", 0.5), noul("pre_existing", "yes", 0.9)),
    );
    expect(routed.route).toBe("decide_by_hand");
  });

  test("a missing second answer is unknown causality, not an absent one", () => {
    expect(jevFindingRoute(verdicts(noul("real", "yes", 0.92))).route).toBe("fix_this_round");
  });

  test("every route carries one line saying why, for the reply", () => {
    for (const routed of [
      jevFindingRoute(verdicts(noul("real", "yes", 0.92), noul("pre_existing", "no", 0.08))),
      jevFindingRoute(verdicts(noul("real", "no", 0.05))),
      jevFindingRoute(verdicts(noul("real", "undecided", 0.5))),
    ]) {
      expect(routed.why.length).toBeGreaterThan(20);
    }
  });

  test("the numbers behind the route come back with it, for the reply", () => {
    const routed = jevFindingRoute(
      verdicts(noul("real", "yes", 0.92), noul("pre_existing", "no", 0.08)),
    );
    expect(routed.real).toBe(0.92);
    expect(routed.preExisting).toBe(0.08);
  });
});

describe("a citation the base cannot show", () => {
  const citation: JevFindingCitation = {
    path: "src/decision-log.ts",
    from: 2051,
    to: 2060,
  };

  test("is still a citation, and the caller reports the empty read rather than judging it", () => {
    expect(citation.from).toBeGreaterThan(0);
    expect(() =>
      jevFindingState({ finding: "a claim", code: "now", baseCode: "", diff: "a hunk" }),
    ).toThrow(JevUsageError);
  });

  // A field a question names, handed over as undefined, is the same caller mistake as an empty one.
  test("a state built without the field a question names is a usage error, not a crash", () => {
    expect(() =>
      jevFindingState({ finding: "a claim", baseCode: "b", diff: "d" } as never),
    ).toThrow(JevUsageError);
  });
});

describe("the command line that judges one finding", () => {
  const repositoryRoot = join(import.meta.dir, "..");
  const cli = join(repositoryRoot, "src", "jev-finding.ts");

  async function run(argv: readonly string[]): Promise<{ code: number; out: string; err: string }> {
    const child = Bun.spawn(["bun", cli, ...argv], {
      cwd: repositoryRoot,
      // No key: every verdict is undecided, so the route is the one that asks a person.
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdout: "pipe",
      stderr: "pipe",
    });
    const [out, err, code] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    return { code, out, err };
  }

  const CLAIM = "The guard reads elapsed milliseconds alone, so a same-tick wake ends the retry.";

  test("sends the code as it stands now beside the code at the base", async () => {
    const result = await run([
      "--file",
      "src/judge.ts",
      "--lines",
      "30-60",
      "--finding",
      CLAIM,
      "--base",
      "HEAD",
      "--json",
      "--print-state",
    ]);
    expect(result.code).toBe(0);
    const state = JSON.parse(result.out).state;
    expect(Object.keys(state).sort()).toEqual(["base_code", "code", "diff", "finding"]);
    expect(String(state.code).length).toBeGreaterThan(20);
  });

  test("reads the cited lines at the base and the touched hunk, and routes the finding", async () => {
    const result = await run([
      "--file",
      "src/judge.ts",
      "--lines",
      "30-60",
      "--finding",
      CLAIM,
      "--base",
      "HEAD",
      "--json",
    ]);
    expect(result.code).toBe(0);
    const answer = JSON.parse(result.out);
    expect(answer.citation).toEqual({ path: "src/judge.ts", from: 30, to: 60 });
    expect(JEV_FINDING_ROUTES).toContain(answer.route);
  });

  test("an unsure judge routes the finding to a person, never to an edit", async () => {
    const answer = JSON.parse(
      (
        await run([
          "--file",
          "src/judge.ts",
          "--lines",
          "30-60",
          "--finding",
          CLAIM,
          "--base",
          "HEAD",
          "--json",
        ])
      ).out,
    );
    expect(answer.route).toBe("decide_by_hand");
  });

  test("takes the citation out of a pasted comment body instead of two flags", async () => {
    const answer = JSON.parse(
      (
        await run([
          "--body",
          "the guard in `src/judge.ts:40` is wrong",
          "--base",
          "HEAD",
          "--json",
        ])
      ).out,
    );
    expect(answer.citation.path).toBe("src/judge.ts");
    expect(answer.citation.from).toBe(40);
  });

  test("says so when the cited lines are not in the file at the base", async () => {
    const answer = JSON.parse(
      (
        await run([
          "--file",
          "src/judge.ts",
          "--lines",
          "9000-9010",
          "--finding",
          CLAIM,
          "--base",
          "HEAD",
          "--json",
        ])
      ).out,
    );
    expect(answer.citationOutOfRange).toBe(true);
  });

  test("prints a readable route without --json", async () => {
    const result = await run([
      "--file",
      "src/judge.ts",
      "--lines",
      "30-60",
      "--finding",
      CLAIM,
      "--base",
      "HEAD",
    ]);
    expect(result.out).toContain("decide_by_hand");
    expect(result.out).toContain("src/judge.ts:30-60");
  });

  test.each([
    ["no claim at all", ["--file", "src/judge.ts", "--lines", "30-60"]],
    ["no citation at all", ["--finding", "something is wrong"]],
    ["a line range that is not a range", ["--file", "a.ts", "--lines", "later", "--finding", "x"]],
    [
      "a file that is in neither the base nor the branch",
      ["--file", "src/absent.ts", "--lines", "1-5", "--finding", "x", "--base", "HEAD"],
    ],
  ])("refuses %s", async (_label, argv) => {
    const result = await run(argv);
    expect(result.code).toBe(2);
    expect(result.out).toBe("");
  });

  // A citation is text somebody else wrote. An untracked or ignored file in the checkout (an
  // `.env`, a key file, the decision log) must not be reachable at all, so the read goes through
  // git and a path the branch commit does not hold is refused before anything is opened.
  test("refuses a path that is on disk but not in the branch commit, and never opens it", async () => {
    const untracked = join("src", `untracked-fixture-${process.pid}.ts`);
    writeFileSync(join(repositoryRoot, untracked), 'export const key = "canary-42";\n', "utf8");
    try {
      const argv = ["--file", untracked, "--lines", "1-5", "--finding", CLAIM];
      const result = await run([...argv, "--base", "HEAD", "--json"]);
      expect(result.code).toBe(2);
      expect(result.out).toBe("");
      expect(result.err).toContain(untracked);
      expect(`${result.out}${result.err}`).not.toContain("canary-42");
    } finally {
      rmSync(join(repositoryRoot, untracked), { force: true });
    }
  });

  // The commonest finding of all sits on a file the change created, and nothing in a file the base
  // does not have can be pre-existing. The base is a commit whose tree is empty and whose parent is
  // `HEAD`: git genuinely lacks the path at it, no ref and no file move, and the case holds in the
  // shallow checkout CI runs, where no older commit exists.
  test("a file the base does not have is judged, with a base that says the file is new", async () => {
    const at = { cwd: repositoryRoot, encoding: "utf8" } as const;
    const who = "jev-fixture";
    const tree = execFileSync("git", ["hash-object", "-w", "-t", "tree", "/dev/null"], at).trim();
    const base = execFileSync("git", ["commit-tree", tree, "-p", "HEAD", "-m", "empty base"], {
      ...at,
      // A CI runner has no git identity, and git will not guess an email for a commit.
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: who,
        GIT_AUTHOR_EMAIL: `${who}@example.invalid`,
        GIT_COMMITTER_NAME: who,
        GIT_COMMITTER_EMAIL: `${who}@example.invalid`,
      },
    }).trim();
    const argv = ["--file", "src/judge.ts", "--lines", "1-20", "--finding", CLAIM];
    const result = await run([...argv, "--base", base, "--json"]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.out).fileIsNew).toBe(true);
  });

  test("a base that is not a ref is refused by name, not read as a missing file", async () => {
    const result = await run([
      "--file",
      "src/judge.ts",
      "--lines",
      "30-60",
      "--finding",
      CLAIM,
      "--base",
      "no-such-ref-in-any-checkout",
      "--json",
    ]);
    expect(result.code).toBe(2);
    expect(result.err).toContain("no-such-ref-in-any-checkout");
    expect(result.out).toBe("");
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(["--help"]);
    expect(help.code).toBe(0);
    expect(help.out).toContain("--comment");
    expect(help.out).toContain("fix_this_round");
  });
});

describe("the coordinates a review comment carries", () => {
  // A GitHub review comment on several lines carries `start_line` and `line`; one that went stale
  // after a push carries `line: null` and keeps `original_start_line` and `original_line`. Reading
  // only `line` judges one line of a block, or nothing at all.
  test.each([
    [
      "a multi line comment",
      { path: "a.ts", start_line: 10, line: 20 },
      { path: "a.ts", from: 10, to: 20 },
    ],
    ["a single line comment", { path: "a.ts", line: 20 }, { path: "a.ts", from: 20, to: 20 }],
    [
      "an outdated comment that kept its original coordinates",
      { path: "a.ts", line: null, original_start_line: 5, original_line: 9 },
      { path: "a.ts", from: 5, to: 9 },
    ],
    [
      "an outdated single line comment",
      { path: "a.ts", line: null, start_line: null, original_line: 9 },
      { path: "a.ts", from: 9, to: 9 },
    ],
  ])("%s becomes the range it covers", (_label, comment, expected) => {
    expect(jevCommentCitation(comment)).toEqual(expected);
  });

  test("a comment with a path but no line at all names no range", () => {
    expect(jevCommentCitation({ path: "a.ts" })).toBeUndefined();
  });

  test("a comment with no path names no range, whatever lines it carries", () => {
    expect(jevCommentCitation({ line: 20 })).toBeUndefined();
  });

  test("a start after the end is read the way round it was meant", () => {
    expect(jevCommentCitation({ path: "a.ts", start_line: 20, line: 10 })).toEqual({
      path: "a.ts",
      from: 10,
      to: 20,
    });
  });
});

describe("where one review comment lives in the API", () => {
  // `pulls/{number}/comments/{id}` lists a pull request's comments and answers 404 for one id.
  // Found by pointing the command at a real comment on its own pull request.
  test("is pulls/comments/{id}, never pulls/{number}/comments/{id}", () => {
    expect(jevReviewCommentPath("Sample-Org/sample-repo", "4072213915")).toBe(
      "repos/Sample-Org/sample-repo/pulls/comments/4072213915",
    );
  });

  test("falls back to the repository gh resolves when none is named", () => {
    expect(jevReviewCommentPath(undefined, "1")).toBe("repos/{owner}/{repo}/pulls/comments/1");
    expect(jevReviewCommentPath("", "1")).toBe("repos/{owner}/{repo}/pulls/comments/1");
  });
});

describe("a citation may only name a file inside this repository", () => {
  // A finding body is text somebody else wrote. `currentCodeAt` reads the cited path off the disk
  // and the state goes to a third party service, so a citation that climbs out of the repository
  // would put an arbitrary local file into that request. The masker knows shapes, not files.
  test.each([
    ["a parent segment", "../../secrets.env"],
    ["a parent segment in the middle", "scripts/../../secrets.env"],
    ["an absolute path", "/etc/passwd"],
    ["a home relative path", "~/.ssh/id_rsa"],
    ["a bare parent", ".."],
  ])("refuses %s", (_label, path) => {
    expect(() => jevSafeCitationPath(path)).toThrow(JevUsageError);
  });

  test.each([
    ["an ordinary path", "src/judge.ts"],
    ["a path with a dot segment", "scripts/./jev/judge.ts"],
    ["a file at the root", "AGENTS.md"],
    ["a name that merely starts with two dots", "scripts/..hidden.ts"],
  ])("allows %s", (_label, path) => {
    expect(jevSafeCitationPath(path)).toBe(path);
  });

  test("the refusal says what was refused, without echoing a whole path back", () => {
    expect(() => jevSafeCitationPath("../../secrets.env")).toThrow(/inside the repository/u);
  });

  test("an empty path is refused like any other one that names no file here", () => {
    expect(() => jevSafeCitationPath("")).toThrow(JevUsageError);
  });

  test("a citation read out of a body carries a path this check accepts", () => {
    const citation = jevFindingCitationFrom("the guard in `src/judge.ts:118` is wrong");
    expect(jevSafeCitationPath(citation?.path ?? "")).toBe("src/judge.ts");
  });
});

describe("the command refuses a citation that climbs out of the repository", () => {
  const repositoryRoot = join(import.meta.dir, "..");
  const cli = join(repositoryRoot, "src", "jev-finding.ts");

  async function run(argv: readonly string[]): Promise<{ code: number; out: string; err: string }> {
    const child = Bun.spawn(["bun", cli, ...argv], {
      cwd: repositoryRoot,
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdout: "pipe",
      stderr: "pipe",
    });
    const [out, err, code] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    return { code, out, err };
  }

  test.each([
    ["cited by a pasted body", ["--body", "the bug in `../../../home/secrets.yaml:1` is real"]],
    [
      "cited by a blob link that climbs out",
      ["--body", "https://github.com/o/r/blob/abc/../../../home/secrets.yaml#L1-L4"],
    ],
    [
      "named with --file",
      ["--file", "../../secrets.env", "--lines", "1-5", "--finding", "a claim"],
    ],
  ])("refuses a path %s, before it reads anything", async (_label, argv) => {
    const result = await run([...argv, "--base", "HEAD", "--json"]);
    expect(result.code).toBe(2);
    expect(result.out).toBe("");
    expect(result.err).toContain("inside the repository");
  });
});

describe("what a failed git read of a cited file was", () => {
  // Only git saying the tree lacks the path is an answer. Every other failure read as that answer,
  // and the caller then told the judge this change had created or deleted the file. One mebibyte
  // of file was enough, because that is where the default buffer throws.
  const failed = (thrown: Record<string, unknown>) =>
    Object.assign(new Error("Command failed: git show"), thrown);
  const notInTree = "fatal: path 'a.ts' does not exist in 'HEAD'";
  const onDiskOnly = "fatal: path 'a.ts' exists on disk, but not in 'HEAD'";

  test.each([
    ["the tree not holding the path", { stderr: notInTree }, true],
    ["a path on disk but outside the tree", { stderr: onDiskOnly }, true],
    ["a file over the buffer", { code: "ENOBUFS", stderr: "" }, false],
    ["git not answering at all", { stderr: "fatal: not a git repository" }, false],
  ])("%s reads as an absence: %p", (_label, thrown, absent) => {
    expect(jevGitSaysPathIsAbsent(failed(thrown))).toBe(absent);
  });
});
