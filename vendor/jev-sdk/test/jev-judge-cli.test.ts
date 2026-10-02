import { afterAll, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  jevDecisionLine,
  parseJevArguments,
} from "../src/jev-judge";
import { JevUsageError } from "../src/judge";

const repositoryRoot = join(import.meta.dir, "..");
const cli = join(repositoryRoot, "src", "jev-judge.ts");
const workspace = mkdtempSync(join(tmpdir(), "jev-cli-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

const questionsPath = join(workspace, "questions.json");
Bun.write(
  questionsPath,
  JSON.stringify({
    questions: {
      needs_owner: { type: "noul", instructions: "Does the owner have to act?" },
    },
  }),
);

type Run = { code: number; stdout: string; stderr: string };

async function run(argv: readonly string[], stdin = ""): Promise<Run> {
  const child = Bun.spawn(["bun", cli, ...argv], {
    cwd: repositoryRoot,
    // The key is deliberately absent: the CLI must answer a closed refusal, never crash.
    env: { ...Bun.env, TYPESAFE_API_KEY: "" },
    stdin: new TextEncoder().encode(stdin),
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

describe("the command line", () => {
  test("does not load the consumer cwd .env implicitly", async () => {
    const consumer = join(workspace, "consumer-env");
    mkdirSync(consumer, { recursive: true });
    writeFileSync(join(consumer, ".env"), ["TYPESAFE_API_KEY", "synthetic-never-send"].join("=") + "\n");
    const { TYPESAFE_API_KEY: _ignored, ...environment } = Bun.env;
    const child = Bun.spawn([cli, "--state", "-", "--questions", questionsPath], {
      cwd: consumer,
      env: environment,
      stdin: new TextEncoder().encode("sample state"),
      stdout: "pipe",
      stderr: "pipe",
    });
    const stdout = await new Response(child.stdout).text();
    expect(await child.exited).toBe(0);
    expect(JSON.parse(stdout).verdicts.needs_owner.reason).toBe("typesafe_api_key_missing");
  });
  test("reads the state from a file and prints one JSON object", async () => {
    const statePath = join(workspace, "state.txt");
    await Bun.write(statePath, "the worker refused to settle");
    const result = await run(["--state", statePath, "--questions", questionsPath]);
    expect(result.code).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts.needs_owner.verdict).toBe("undecided");
    expect(payload.verdicts.needs_owner.reason).toBe("typesafe_api_key_missing");
  });

  test("reads the state from stdin, and never echoes a secret it was given", async () => {
    const result = await run(
      ["--state", "-", "--questions", questionsPath, "--threshold", "0.9"],
      "worker Bearer fixture-token-value-0001 refused",
    );
    expect(result.code).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts.needs_owner.threshold).toBe(0.9);
    expect(result.stdout).not.toContain("fixture-token-value-0001");
  });

  test("exits 0 for an undecided verdict and non-zero only for a usage error", async () => {
    const usage = await run(["--state", "-", "--unknown"], "text");
    expect(usage.code).not.toBe(0);
    expect(usage.stdout).toBe("");
    expect(usage.stderr).toContain("--unknown");
  });

  test("refuses a state file that does not exist", async () => {
    const missing = await run([
      "--state",
      join(workspace, "absent.txt"),
      "--questions",
      questionsPath,
    ]);
    expect(missing.code).not.toBe(0);
    expect(missing.stderr).toContain("absent.txt");
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(["--help"]);
    expect(help.code).toBe(0);
    expect(help.stdout).toContain("--questions");
  });
});

describe("argument parsing", () => {
  test("defaults questions to the consumer cwd", () => {
    expect(parseJevArguments(["--state", "-"]).questionsPath).toBe("questions/agent-operations.json");
  });
  test("takes the documented flags", () => {
    expect(
      parseJevArguments(["--state", "-", "--questions", "q.json", "--threshold", "0.7", "--log"]),
    ).toEqual({
      statePath: "-",
      questionsPath: "q.json",
      threshold: 0.7,
      stakes: undefined,
      log: true,
      directory: undefined,
      timeoutMs: undefined,
      help: false,
    });
  });

  test("takes the stakes word that sets the bar for the whole call", () => {
    expect(
      parseJevArguments(["--state", "-", "--questions", "q.json", "--stakes", "critical"]).stakes,
    ).toBe("critical");
  });

  test.each([
    ["no state", ["--questions", "q.json"]],
    ["an unknown flag", ["--state", "-", "--questions", "q.json", "--fast"]],
    ["a flag with no value", ["--state", "-", "--questions"]],
    [
      "a threshold that is not a number",
      ["--state", "-", "--questions", "q.json", "--threshold", "high"],
    ],
    [
      "a stakes word outside the closed vocabulary",
      ["--state", "-", "--questions", "q.json", "--stakes", "low"],
    ],
  ])("refuses %s", (_label, argv) => {
    expect(() => parseJevArguments(argv)).toThrow(JevUsageError);
  });
});

describe("the decision log", () => {
  test("is written under .local, which git ignores", () => {
    expect(JEV_DECISION_LOG_DIRECTORY).toBe(".local/jev-decisions");
    expect(readFileSync(join(repositoryRoot, ".gitignore"), "utf8")).toContain(".local/");
  });

  test("holds the masked digest, the masked questions, the verdicts and the latency", () => {
    const line = JSON.parse(
      jevDecisionLine(
        {
          state: "worker Bearer fixture-token-value-0001 refused",
          questions: {
            needs_owner: {
              type: "noul",
              instructions: "Does operator.fixture@example.com have to act?",
            },
          },
          thresholds: { needs_owner: 0.8 },
          timeoutMs: 5_000,
        },
        {
          model: "jev-1.13.0",
          latencyMs: 42,
          verdicts: {
            needs_owner: {
              type: "noul",
              verdict: "yes",
              value: 0.93,
              valueKind: "noul-probability",
              threshold: 0.8,
            },
          },
        },
        "2026-09-21T22:00:00.000Z",
      ),
    );
    expect(line.at).toBe("2026-09-21T22:00:00.000Z");
    expect(line.model).toBe("jev-1.13.0");
    expect(line.latencyMs).toBe(42);
    expect(line.stateDigest).toMatch(/^[0-9a-f]{64}$/u);
    expect(line.questions.needs_owner.instructions).toBe("Does [redacted] have to act?");
    expect(line.verdicts.needs_owner.value).toBe(0.93);
    expect(JSON.stringify(line)).not.toContain("fixture-token-value-0001");
    expect(JSON.stringify(line)).not.toContain("worker");
  });

  test("appends one line per call to the day's file", () => {
    const directory = join(workspace, "decisions");
    appendJevDecision(directory, "2026-09-21", '{"at":"one"}');
    appendJevDecision(directory, "2026-09-21", '{"at":"two"}');
    expect(readFileSync(join(directory, "2026-09-21.jsonl"), "utf8")).toBe(
      '{"at":"one"}\n{"at":"two"}\n',
    );
  });
});

describe("the bar the command line applies", () => {
  const stakedPath = join(workspace, "staked.json");
  Bun.write(
    stakedPath,
    JSON.stringify({
      questions: {
        cheap: { type: "noul", instructions: "Is the log line an error?", stakes: "passive" },
        dear: { type: "noul", instructions: "Does it touch a money path?", stakes: "critical" },
      },
    }),
  );

  test("each question keeps the bar its own stakes name", async () => {
    const result = await run(["--state", "-", "--questions", stakedPath], "a state to judge");
    expect(result.code).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts.cheap.threshold).toBe(0.6);
    expect(payload.verdicts.dear.threshold).toBe(0.9);
  });

  test("--stakes sets one bar for every question in the call", async () => {
    const result = await run(
      ["--state", "-", "--questions", stakedPath, "--stakes", "design"],
      "a state to judge",
    );
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts.cheap.threshold).toBe(0.75);
    expect(payload.verdicts.dear.threshold).toBe(0.75);
  });

  test("--threshold still wins over both", async () => {
    const result = await run(
      ["--state", "-", "--questions", stakedPath, "--stakes", "passive", "--threshold", "0.95"],
      "a state to judge",
    );
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts.cheap.threshold).toBe(0.95);
    expect(payload.verdicts.dear.threshold).toBe(0.95);
  });

  test("a question that names no stakes keeps the flat default", async () => {
    const result = await run(["--state", "-", "--questions", questionsPath], "a state to judge");
    expect(JSON.parse(result.stdout).verdicts.needs_owner.threshold).toBe(0.8);
  });

  test("the usage text names the stakes flag and its three words", async () => {
    const help = await run(["--help"]);
    expect(help.stdout).toContain("--stakes");
    expect(help.stdout).toContain("passive");
    expect(help.stdout).toContain("critical");
  });
});
