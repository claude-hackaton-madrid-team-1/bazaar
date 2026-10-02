import { afterAll, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  JEV_STOP_QUESTION,
  JEV_STOP_QUESTION_ID,
  jevStopExit,
  jevStopInputFrom,
  lastAssistantMessageIn,
} from "../src/jev-stop-hook";

const repositoryRoot = join(import.meta.dir, "..");
const hook = join(repositoryRoot, "src", "jev-stop-hook.ts");
const workspace = mkdtempSync(join(tmpdir(), "jev-hook-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

async function run(payload: unknown) {
  const child = Bun.spawn(["bun", hook], {
    cwd: repositoryRoot,
    env: { ...Bun.env, TYPESAFE_API_KEY: "" },
    stdin: new TextEncoder().encode(JSON.stringify(payload)),
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

describe("the question the hook asks", () => {
  test("is one noul about progress without the owner", () => {
    expect(JEV_STOP_QUESTION_ID).toBe("can_make_progress");
    expect(JEV_STOP_QUESTION.type).toBe("noul");
    expect(JSON.stringify(JEV_STOP_QUESTION.instructions)).toContain("owner");
  });
});

describe("the exit code", () => {
  test.each([
    ["yes", 2],
    ["no", 0],
    ["undecided", 0],
  ])("a verdict of %s exits %p", (verdict, code) => {
    expect(
      jevStopExit({
        type: "noul",
        verdict,
        value: 0.9,
        valueKind: "noul-probability",
        threshold: 0.8,
      }).code,
    ).toBe(code);
  });
});

describe("the input", () => {
  test("takes the goal and the last assistant message from the payload", () => {
    expect(
      jevStopInputFrom({ goal: "ship the tool", last_assistant_message: "I opened the PR" }),
    ).toEqual({
      goal: "ship the tool",
      lastAssistantMessage: "I opened the PR",
      stopHookActive: false,
    });
  });

  test("reads the last assistant message from a transcript when the payload has none", async () => {
    const transcript = join(workspace, "transcript.jsonl");
    await Bun.write(
      transcript,
      [
        JSON.stringify({ type: "user", message: { content: "do the thing" } }),
        JSON.stringify({
          type: "assistant",
          message: { content: [{ type: "text", text: "first" }] },
        }),
        JSON.stringify({
          type: "assistant",
          message: { content: [{ type: "text", text: "second" }] },
        }),
      ].join("\n"),
    );
    expect(lastAssistantMessageIn(transcript)).toBe("second");
    expect(jevStopInputFrom({ goal: "g", transcript_path: transcript }).lastAssistantMessage).toBe(
      "second",
    );
  });

  test("a transcript that is not there leaves the message empty", () => {
    expect(lastAssistantMessageIn(join(workspace, "absent.jsonl"))).toBe("");
  });
});

describe("the hook as the harness runs it", () => {
  test("without a key it answers undecided and lets the assistant stop", async () => {
    const result = await run({ goal: "ship the tool", last_assistant_message: "I opened the PR" });
    expect(result.code).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.verdicts[JEV_STOP_QUESTION_ID].verdict).toBe("undecided");
    expect(payload.verdicts[JEV_STOP_QUESTION_ID].reason).toBe("typesafe_api_key_missing");
  });

  test("a second pass never blocks again", async () => {
    const result = await run({ goal: "g", last_assistant_message: "m", stop_hook_active: true });
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).refusal).toBe("stop_hook_active");
  });

  test("no goal lets the assistant stop", async () => {
    const result = await run({ last_assistant_message: "m" });
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).refusal).toBe("goal_missing");
  });

  test("never echoes a secret the transcript carried", async () => {
    const result = await run({
      goal: "settle the account",
      last_assistant_message: "used Bearer fixture-token-value-0001 to call the api",
    });
    expect(result.stdout).not.toContain("fixture-token-value-0001");
    expect(result.stderr).not.toContain("fixture-token-value-0001");
  });

  test("stdin that is not JSON lets the assistant stop", async () => {
    const child = Bun.spawn(["bun", hook], {
      cwd: repositoryRoot,
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdin: new TextEncoder().encode("not json"),
      stdout: "pipe",
      stderr: "pipe",
    });
    const [stdout, code] = await Promise.all([new Response(child.stdout).text(), child.exited]);
    expect(code).toBe(0);
    expect(JSON.parse(stdout).refusal).toBe("input_not_json");
  });
});
