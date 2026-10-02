import { afterAll, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  appendJevDecision,
  JEV_DECISION_LOG_DIRECTORY,
  JEV_OUTCOMES,
  type JevLogEntry,
  jevDecisionLine,
  jevLogTally,
  jevOutcomeLine,
  readJevLog,
} from "../src/decision-log";

const workspace = mkdtempSync(join(tmpdir(), "jev-log-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

describe("a decision line", () => {
  const line = JSON.parse(
    jevDecisionLine(
      {
        state: { note: "operator.fixture@example.com asked for a second round" },
        questions: {
          cheap: { type: "noul", instructions: "Is the line an error?", stakes: "passive" },
          dear: { type: "noul", instructions: "Does it touch money?", stakes: "critical" },
        },
      },
      {
        model: "jev-1.13.0",
        latencyMs: 640,
        verdicts: {
          cheap: {
            type: "noul",
            verdict: "yes",
            value: 0.72,
            valueKind: "noul-probability",
            threshold: 0.6,
            leaning: "yes",
            margin: 0.44,
          },
          dear: {
            type: "noul",
            verdict: "undecided",
            value: 0.72,
            valueKind: "noul-probability",
            threshold: 0.9,
            reason: "below_threshold",
            leaning: "yes",
            margin: 0.44,
          },
        },
      },
      "2026-09-22T12:00:00.000Z",
    ),
  );

  test("says which kind of line it is, so an outcome cannot be read as a call", () => {
    expect(line.kind).toBe("decision");
  });

  test("records the stakes each question named beside the bar it produced", () => {
    expect(line.stakes).toEqual({ cheap: "passive", dear: "critical" });
    expect(line.thresholds).toEqual({ cheap: 0.6, dear: 0.9 });
  });

  test("carries the leaning and the margin, so an undecided row still says which way it went", () => {
    expect(line.verdicts.dear.verdict).toBe("undecided");
    expect(line.verdicts.dear.leaning).toBe("yes");
    expect(line.verdicts.dear.margin).toBe(0.44);
  });

  test("holds the masked digest and never the state it came from", () => {
    expect(line.stateDigest).toMatch(/^[0-9a-f]{64}$/u);
    expect(JSON.stringify(line)).not.toContain("operator.fixture@example.com");
  });
});

describe("an outcome line", () => {
  test("names the call it judges, the outcome and the masked note", () => {
    const line = JSON.parse(
      jevOutcomeLine(
        {
          stateDigest: "a".repeat(64),
          outcome: "wrong",
          note: "round 6 did not move the halt rate; mail operator.fixture@example.com",
        },
        "2026-09-22T13:00:00.000Z",
      ),
    );
    expect(line).toEqual({
      kind: "outcome",
      at: "2026-09-22T13:00:00.000Z",
      stateDigest: "a".repeat(64),
      outcome: "wrong",
      note: "round 6 did not move the halt rate; mail [redacted]",
    });
  });

  test("can name one question id when only that answer turned out wrong", () => {
    const line = JSON.parse(
      jevOutcomeLine(
        { stateDigest: "b".repeat(64), outcome: "right", question: "worker_profile" },
        "2026-09-22T13:00:00.000Z",
      ),
    );
    expect(line.question).toBe("worker_profile");
    expect(line.note).toBeUndefined();
  });

  test("the outcome words are a closed vocabulary", () => {
    expect([...JEV_OUTCOMES]).toEqual(["right", "wrong", "unknown"]);
  });
});

describe("reading the log back", () => {
  const directory = join(workspace, "read");

  appendJevDecision(
    directory,
    "2026-09-21",
    jevDecisionLine(
      {
        state: "the first call",
        questions: { worker_profile: { type: "noul", instructions: "Is it one writer?" } },
      },
      {
        model: "jev-1.13.0",
        latencyMs: 500,
        verdicts: {
          worker_profile: {
            type: "noul",
            verdict: "yes",
            value: 0.95,
            valueKind: "noul-probability",
            threshold: 0.8,
          },
        },
      },
      "2026-09-21T09:00:00.000Z",
    ),
  );
  appendJevDecision(directory, "2026-09-21", "not json at all");
  appendJevDecision(
    directory,
    "2026-09-22",
    jevOutcomeLine({ stateDigest: "c".repeat(64), outcome: "right" }, "2026-09-22T09:00:00.000Z"),
  );

  test("reads every day's file, oldest first", () => {
    const entries = readJevLog(directory);
    expect(entries.map((entry: JevLogEntry) => entry.kind)).toEqual(["decision", "outcome"]);
  });

  test("skips a line it cannot parse instead of losing the file", () => {
    expect(readJevLog(directory)).toHaveLength(2);
  });

  test("a directory that was never written reads as no entries", () => {
    expect(readJevLog(join(workspace, "absent"))).toEqual([]);
  });

  test("a line from before the kind field existed still counts as a decision", () => {
    const older = join(workspace, "older");
    appendJevDecision(older, "2026-09-20", JSON.stringify({ at: "x", verdicts: {} }));
    expect(readJevLog(older)[0]?.kind).toBe("decision");
  });

  test("the log directory is the one git ignores", () => {
    expect(JEV_DECISION_LOG_DIRECTORY).toBe(".local/jev-decisions");
  });
});

describe("the tally a report prints", () => {
  const directory = join(workspace, "tally");
  const digest = "d".repeat(64);

  function decision(at: string, stateDigest: string, verdict: string, id = "worker_profile") {
    return JSON.stringify({
      kind: "decision",
      at,
      model: "jev-1.13.0",
      latencyMs: 500,
      stateDigest,
      stateCharacters: 40,
      questions: { [id]: { type: "noul", instructions: "?" } },
      thresholds: { [id]: 0.8 },
      stakes: { [id]: "passive" },
      verdicts: {
        [id]: {
          type: "noul",
          verdict,
          value: 0.9,
          valueKind: "noul-probability",
          threshold: 0.8,
          ...(verdict === "undecided" ? { reason: "below_threshold" } : {}),
        },
      },
    });
  }

  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, "2026-09-22.jsonl"),
    `${[
      decision("2026-09-22T01:00:00.000Z", digest, "yes"),
      decision("2026-09-22T02:00:00.000Z", "e".repeat(64), "undecided"),
      decision("2026-09-22T03:00:00.000Z", "f".repeat(64), "no", "task_size"),
      jevOutcomeLine({ stateDigest: digest, outcome: "right" }, "2026-09-22T04:00:00.000Z"),
      jevOutcomeLine(
        { stateDigest: "f".repeat(64), outcome: "wrong", question: "task_size" },
        "2026-09-22T05:00:00.000Z",
      ),
    ].join("\n")}\n`,
    "utf8",
  );

  const tally = jevLogTally(readJevLog(directory));

  test("counts calls, decided and undecided per question id", () => {
    const profile = tally.find((row) => row.question === "worker_profile");
    expect(profile).toMatchObject({ calls: 2, decided: 1, undecided: 1 });
  });

  test("an outcome on a call counts for every question that call asked", () => {
    expect(tally.find((row) => row.question === "worker_profile")?.right).toBe(1);
  });

  test("an outcome that names one question counts only for that question", () => {
    expect(tally.find((row) => row.question === "task_size")).toMatchObject({
      calls: 1,
      decided: 1,
      wrong: 1,
      right: 0,
    });
    expect(tally.find((row) => row.question === "worker_profile")?.wrong).toBe(0);
  });

  test("an outcome for a call that is not in the log counts for nothing", () => {
    const orphan = jevLogTally([
      JSON.parse(
        jevOutcomeLine({ stateDigest: "9".repeat(64), outcome: "right" }, "2026-09-22T06:00Z"),
      ),
    ]);
    expect(orphan).toEqual([]);
  });

  test("the rows are ordered by question id, so two runs read the same", () => {
    expect(tally.map((row) => row.question)).toEqual(["task_size", "worker_profile"]);
  });

  test("the file the tally read is the one on disk", () => {
    expect(readFileSync(join(directory, "2026-09-22.jsonl"), "utf8")).toContain('"kind":"outcome"');
  });
});

describe("one state, two calls: an outcome must not reach the other call's questions", () => {
  // The capability selector sends two calls over the same `{ unit }` state and asks different
  // questions in each. Keyed on the state alone, one outcome marked the gate questions of call one
  // and the fits questions of call two together, which is not what either call answered.
  const directory = join(workspace, "two-calls");
  const state = { unit: "rename the engine's refusal codes" };

  function call(questions: Record<string, { type: "noul"; instructions: string }>, at: string) {
    return jevDecisionLine(
      { state, questions },
      {
        model: "jev-1.13.0",
        latencyMs: 500,
        verdicts: Object.fromEntries(
          Object.keys(questions).map((id) => [
            id,
            {
              type: "noul" as const,
              verdict: "yes",
              value: 0.9,
              valueKind: "noul-probability" as const,
              threshold: 0.6,
            },
          ]),
        ),
      },
      at,
    );
  }

  const first = call(
    { "gate::acts_on_repository": { type: "noul", instructions: "Does it act?" } },
    "2026-09-22T01:00:00.000Z",
  );
  const second = call(
    { "fits::cpp-pro": { type: "noul", instructions: "Does cpp-pro fit?" } },
    "2026-09-22T02:00:00.000Z",
  );

  test("each call carries its own digest, beside the state's", () => {
    const one = JSON.parse(first);
    const two = JSON.parse(second);
    expect(one.stateDigest).toBe(two.stateDigest);
    expect(one.callDigest).not.toBe(two.callDigest);
    expect(one.callDigest).toMatch(/^[0-9a-f]{64}$/u);
  });

  test("an outcome on one call's digest counts for that call only", () => {
    mkdirSync(directory, { recursive: true });
    writeFileSync(
      join(directory, "2026-09-22.jsonl"),
      `${[
        first,
        second,
        jevOutcomeLine(
          { stateDigest: JSON.parse(second).callDigest, outcome: "right" },
          "2026-09-22T03:00:00.000Z",
        ),
      ].join("\n")}\n`,
      "utf8",
    );
    const tally = jevLogTally(readJevLog(directory));
    expect(tally.find((row) => row.question === "fits::cpp-pro")?.right).toBe(1);
    expect(tally.find((row) => row.question === "gate::acts_on_repository")?.right).toBe(0);
  });

  test("an outcome on the shared state digest reaches neither, because it names no call", () => {
    const ambiguous = join(workspace, "ambiguous");
    mkdirSync(ambiguous, { recursive: true });
    writeFileSync(
      join(ambiguous, "2026-09-22.jsonl"),
      `${[
        first,
        second,
        jevOutcomeLine(
          { stateDigest: JSON.parse(first).stateDigest, outcome: "right" },
          "2026-09-22T03:00:00.000Z",
        ),
      ].join("\n")}\n`,
      "utf8",
    );
    const tally = jevLogTally(readJevLog(ambiguous));
    expect(tally.find((row) => row.question === "fits::cpp-pro")?.right).toBe(0);
    expect(tally.find((row) => row.question === "gate::acts_on_repository")?.right).toBe(0);
  });

  test("a state digest that covers only one call still counts, as it always did", () => {
    const single = join(workspace, "single");
    mkdirSync(single, { recursive: true });
    writeFileSync(
      join(single, "2026-09-22.jsonl"),
      `${[
        first,
        jevOutcomeLine(
          { stateDigest: JSON.parse(first).stateDigest, outcome: "right" },
          "2026-09-22T03:00:00.000Z",
        ),
      ].join("\n")}\n`,
      "utf8",
    );
    expect(
      jevLogTally(readJevLog(single)).find((row) => row.question === "gate::acts_on_repository")
        ?.right,
    ).toBe(1);
  });

  test("a named question still counts under the shared state digest", () => {
    const named = join(workspace, "named");
    mkdirSync(named, { recursive: true });
    writeFileSync(
      join(named, "2026-09-22.jsonl"),
      `${[
        first,
        second,
        jevOutcomeLine(
          {
            stateDigest: JSON.parse(first).stateDigest,
            outcome: "wrong",
            question: "fits::cpp-pro",
          },
          "2026-09-22T03:00:00.000Z",
        ),
      ].join("\n")}\n`,
      "utf8",
    );
    const tally = jevLogTally(readJevLog(named));
    expect(tally.find((row) => row.question === "fits::cpp-pro")?.wrong).toBe(1);
    expect(tally.find((row) => row.question === "gate::acts_on_repository")?.wrong).toBe(0);
  });
});
