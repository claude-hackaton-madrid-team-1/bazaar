import { afterAll, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const repositoryRoot = join(import.meta.dir, "..");
const outcomeCli = join(repositoryRoot, "src", "jev-outcome.ts");
const reportCli = join(repositoryRoot, "src", "jev-report.ts");
const workspace = mkdtempSync(join(tmpdir(), "jev-outcome-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

type Run = { code: number; stdout: string; stderr: string };

async function run(cli: string, argv: readonly string[]): Promise<Run> {
  const child = Bun.spawn(["bun", cli, ...argv], {
    cwd: repositoryRoot,
    env: { ...Bun.env, TYPESAFE_API_KEY: "" },
    stdout: "pipe",
    stderr: "pipe",
  });
  const [stdout, stderr, code] = await Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
    child.exited,
  ]);
  return { code, stdout, stderr };
}

const DIGEST = "a1b2c3d4".repeat(8);
const OTHER = "b2c3d4e5".repeat(8);

function decisionLine(digest: string, verdict: string, id: string, callDigest = digest): string {
  return JSON.stringify({
    kind: "decision",
    at: "2026-09-22T01:00:00.000Z",
    model: "jev-1.13.0",
    latencyMs: 500,
    stateDigest: digest,
    callDigest,
    stateCharacters: 40,
    questions: { [id]: { type: "noul", instructions: "?" } },
    thresholds: { [id]: 0.6 },
    stakes: { [id]: "passive" },
    verdicts: {
      [id]: {
        type: "noul",
        verdict,
        value: 0.72,
        valueKind: "noul-probability",
        threshold: 0.6,
        leaning: "yes",
        margin: 0.44,
      },
    },
  });
}

/** An outcome names a call the log holds, so every directory a test writes to is seeded first. */
function seed(directory: string, ...lines: readonly string[]): string {
  mkdirSync(directory, { recursive: true });
  const day = `${new Date().toISOString().slice(0, 10)}.jsonl`;
  writeFileSync(join(directory, day), `${lines.join("\n")}\n`, "utf8");
  return directory;
}

describe("recording how a decision turned out", () => {
  test("appends one outcome line to the day's file and prints it", async () => {
    const directory = seed(
      join(workspace, "record"),
      decisionLine(DIGEST, "yes", "worker_profile"),
    );
    const result = await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "wrong",
      "--note",
      "round 6 did not move the halt rate",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(0);
    const printed = JSON.parse(result.stdout);
    expect(printed.kind).toBe("outcome");
    expect(printed.outcome).toBe("wrong");
    expect(printed.stateDigest).toBe(DIGEST);
    const day = `${String(printed.at).slice(0, 10)}.jsonl`;
    expect(readFileSync(join(directory, day), "utf8")).toContain('"kind":"outcome"');
  });

  test("masks the note before it reaches the file", async () => {
    const directory = seed(
      join(workspace, "masked"),
      decisionLine(DIGEST, "yes", "worker_profile"),
    );
    await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "right",
      "--note",
      "asked operator.fixture@example.com to confirm",
      "--directory",
      directory,
    ]);
    const files = readFileSync(
      join(directory, `${new Date().toISOString().slice(0, 10)}.jsonl`),
      "utf8",
    );
    expect(files).toContain("[redacted]");
    expect(files).not.toContain("operator.fixture@example.com");
  });

  test("can name the one question whose answer turned out wrong", async () => {
    const directory = seed(
      join(workspace, "one-question"),
      decisionLine(DIGEST, "yes", "worker_profile"),
    );
    const result = await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "right",
      "--question",
      "worker_profile",
      "--directory",
      directory,
    ]);
    expect(JSON.parse(result.stdout).question).toBe("worker_profile");
  });

  test.each([
    ["no digest", ["--outcome", "right"]],
    ["no outcome", ["--digest", DIGEST]],
    ["an outcome outside the closed vocabulary", ["--digest", DIGEST, "--outcome", "maybe"]],
    ["a digest that is not a sha256", ["--digest", "abc", "--outcome", "right"]],
  ])("refuses %s", async (_label, argv) => {
    const result = await run(outcomeCli, argv);
    expect(result.code).toBe(2);
    expect(result.stdout).toBe("");
  });

  test("refuses a digest that names no call in the log, instead of recording nothing", async () => {
    const directory = seed(
      join(workspace, "unknown-digest"),
      decisionLine(DIGEST, "yes", "worker_profile"),
    );
    const result = await run(outcomeCli, [
      "--digest",
      OTHER,
      "--outcome",
      "right",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("no decision line");
    expect(result.stdout).toBe("");
  });

  test("refuses a question that the call it names never asked", async () => {
    const directory = seed(
      join(workspace, "unknown-question"),
      decisionLine(DIGEST, "yes", "worker_profile"),
    );
    const result = await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "right",
      "--question",
      "task_size",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("task_size");
  });

  test("refuses a state digest shared by two calls, and names the call digests to use", async () => {
    const directory = seed(
      join(workspace, "ambiguous"),
      decisionLine(DIGEST, "yes", "gate::acts_on_repository", `${"1".repeat(64)}`),
      decisionLine(DIGEST, "yes", "fits::cpp-pro", `${"2".repeat(64)}`),
    );
    const result = await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "right",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("two calls");
    expect(result.stderr).toContain("1".repeat(64));
    expect(result.stderr).toContain("2".repeat(64));
  });

  test("a call digest resolves that same ambiguity", async () => {
    const directory = seed(
      join(workspace, "by-call"),
      decisionLine(DIGEST, "yes", "gate::acts_on_repository", `${"1".repeat(64)}`),
      decisionLine(DIGEST, "yes", "fits::cpp-pro", `${"2".repeat(64)}`),
    );
    const result = await run(outcomeCli, [
      "--digest",
      "2".repeat(64),
      "--outcome",
      "right",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).stateDigest).toBe("2".repeat(64));
  });

  test("a named question resolves it too, because it says which call is meant", async () => {
    const directory = seed(
      join(workspace, "by-question"),
      decisionLine(DIGEST, "yes", "gate::acts_on_repository", `${"1".repeat(64)}`),
      decisionLine(DIGEST, "yes", "fits::cpp-pro", `${"2".repeat(64)}`),
    );
    const result = await run(outcomeCli, [
      "--digest",
      DIGEST,
      "--outcome",
      "wrong",
      "--question",
      "fits::cpp-pro",
      "--directory",
      directory,
    ]);
    expect(result.code).toBe(0);
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(outcomeCli, ["--help"]);
    expect(help.code).toBe(0);
    expect(help.stdout).toContain("--digest");
    expect(help.stdout).toContain("right");
  });
});

describe("the report over the decision log", () => {
  const directory = join(workspace, "report");
  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, "2026-09-22.jsonl"),
    `${[
      decisionLine(DIGEST, "yes", "worker_profile"),
      decisionLine("b".repeat(64), "undecided", "worker_profile"),
      decisionLine("c".repeat(64), "no", "task_size"),
      JSON.stringify({
        kind: "outcome",
        at: "2026-09-22T05:00:00.000Z",
        stateDigest: DIGEST,
        outcome: "right",
      }),
    ].join("\n")}\n`,
    "utf8",
  );

  test("prints one row per question id with its counts", async () => {
    const result = await run(reportCli, ["--directory", directory]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("worker_profile");
    expect(result.stdout).toContain("task_size");
    expect(result.stdout).toMatch(/worker_profile\s+2\s+1\s+1\s+1\s+0\s+0/u);
  });

  test("--json prints the same counts as data", async () => {
    const result = await run(reportCli, ["--directory", directory, "--json"]);
    const rows = JSON.parse(result.stdout);
    expect(rows).toContainEqual({
      question: "worker_profile",
      calls: 2,
      decided: 1,
      undecided: 1,
      right: 1,
      wrong: 0,
      unknown: 0,
    });
  });

  test("an empty log is not a failure, it is a report with no rows", async () => {
    const result = await run(reportCli, ["--directory", join(workspace, "never-written")]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("no decisions");
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(reportCli, ["--help"]);
    expect(help.code).toBe(0);
    expect(help.stdout).toContain("--directory");
  });
});
