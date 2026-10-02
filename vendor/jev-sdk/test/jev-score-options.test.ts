import { afterAll, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { JevUsageError, type JevVerdict, maskedJevRequest } from "../src/judge";
import {
  JEV_SCORE_DEFAULT_MARGIN,
  type JevScorePlan,
  jevScorePlanFrom,
  jevScoreQuestions,
  jevScoreTable,
  jevScoreTableText,
} from "../src/score-options";

const PLAN: JevScorePlan = {
  state: { symptom: "the halt gate engaged 52 times in an hour" },
  options: {
    core_per_reader: "One retained snapshot per reader per account, inside the engine core.",
    realtime_timer_removed: "Remove the one second book snapshot read from the realtime producer.",
  },
  dimensions: {
    boot_image_risk: {
      weight: 3,
      question: "How safe is the option in `option` for restoring the boot image?",
      criteria: [
        "The option changes the boot image format, so an existing image cannot be restored.",
        "The option changes what the image holds, and a migration has to be written for it.",
        "The option leaves the boot image untouched, so any existing image restores as it is.",
      ],
    },
    memory: {
      weight: 1,
      question: "How much resident memory does the option in `option` add to the engine?",
      criteria: [
        "The option adds memory that grows with the number of accounts and is never released.",
        "The option adds a bounded amount of memory that does not grow with the venue.",
        "The option adds no memory at all, or releases more than it takes.",
      ],
    },
  },
};

function scored(score: number, confidence: number, verdict = String(score)): JevVerdict {
  return {
    type: "score",
    verdict: confidence >= 0.75 ? verdict : "undecided",
    value: confidence,
    valueKind: "confidence",
    threshold: 0.75,
    leaning: String(score),
    probabilities: { "0": 0.1, "1": 0.2, "2": 0.7 },
    margin: 0.5,
    ...(confidence >= 0.75 ? {} : { reason: "below_threshold" as const }),
  };
}

describe("the questions one composite scoring call carries", () => {
  const questions = jevScoreQuestions(PLAN);

  test("asks every dimension once per option, in one call", () => {
    expect(Object.keys(questions).sort()).toEqual([
      "boot_image_risk::core_per_reader",
      "boot_image_risk::realtime_timer_removed",
      "memory::core_per_reader",
      "memory::realtime_timer_removed",
    ]);
  });

  test("every question is one atomic score over one dimension's own levels", () => {
    const question = questions["boot_image_risk::core_per_reader"];
    expect(question?.type).toBe("score");
    expect(question?.criteria).toEqual(PLAN.dimensions.boot_image_risk?.criteria);
  });

  test("the option under judgment travels in the instructions, not in the state", () => {
    const instructions = questions["memory::core_per_reader"]?.instructions as Record<
      string,
      unknown
    >;
    expect(instructions.option).toBe(PLAN.options.core_per_reader);
    expect(instructions.question).toBe(PLAN.dimensions.memory?.question);
    expect(String(instructions.note)).toContain("only");
  });

  test("a design score is judged at the design bar unless the dimension says otherwise", () => {
    expect(questions["memory::core_per_reader"]?.stakes).toBe("design");
    const strict = jevScoreQuestions({
      ...PLAN,
      dimensions: {
        ...PLAN.dimensions,
        memory: { ...PLAN.dimensions.memory, stakes: "critical" },
      },
    } as JevScorePlan);
    expect(strict["memory::core_per_reader"]?.stakes).toBe("critical");
  });

  test("the whole call validates through the judge's own validator", () => {
    const masked = maskedJevRequest({ state: PLAN.state, questions });
    expect(Object.keys(masked.questions)).toHaveLength(4);
    expect(masked.thresholds["memory::core_per_reader"]).toBe(0.75);
  });
});

describe("a plan the tool refuses", () => {
  test.each([
    ["one option", { ...PLAN, options: { only: "the one option" } }],
    ["no dimension", { ...PLAN, dimensions: {} }],
    [
      "a level that names no concrete situation",
      {
        ...PLAN,
        dimensions: { memory: { question: "How much memory?", criteria: ["low", "high"] } },
      },
    ],
    [
      "one level",
      {
        ...PLAN,
        dimensions: {
          memory: {
            question: "How much memory?",
            criteria: ["The option adds memory that grows with the number of accounts."],
          },
        },
      },
    ],
    [
      "a weight that is not a positive number",
      { ...PLAN, dimensions: { memory: { ...PLAN.dimensions.memory, weight: 0 } } },
    ],
  ])("refuses %s", (_label, plan) => {
    expect(() => jevScoreQuestions(plan as JevScorePlan)).toThrow(JevUsageError);
  });

  test("a plan file must name state, options and dimensions", () => {
    expect(() => jevScorePlanFrom({ options: {}, dimensions: {} })).toThrow(JevUsageError);
  });

  test("a plan file that names all three is read as written", () => {
    expect(jevScorePlanFrom(PLAN as unknown as Record<string, unknown>)).toEqual(PLAN);
  });
});

describe("the weighted table code builds from the answers", () => {
  test("weights are normalised in code, and printed", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(2, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    expect(table.weights).toEqual({ boot_image_risk: 0.75, memory: 0.25 });
  });

  test("each cell is normalised over its own levels before it is weighted", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    // core: 0.75 * (2/2) + 0.25 * (1/2) = 0.875. realtime: 0.75 * 0 + 0.25 * 1 = 0.25.
    expect(table.rows[0]).toMatchObject({ option: "core_per_reader", weighted: 0.875 });
    expect(table.rows[1]).toMatchObject({ option: "realtime_timer_removed", weighted: 0.25 });
  });

  test("the winner is named when every cell decided and the lead is wide enough", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    expect(table.winner).toBe("core_per_reader");
    expect(table.reason).toBe("decided");
    expect(table.margin).toBeCloseTo(0.625, 6);
  });

  test("a lead narrower than the margin is a tie, with the leader named as a leaning", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(2, 0.9),
      "memory::core_per_reader": scored(2, 0.9),
      "memory::realtime_timer_removed": scored(1.9, 0.9),
    });
    expect(table.winner).toBeUndefined();
    expect(table.leaning).toBe("core_per_reader");
    expect(table.reason).toBe("margin_too_small");
  });

  test("one undecided cell stops the winner, because the least certain cell owns the call", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.4),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    expect(table.winner).toBeUndefined();
    expect(table.reason).toBe("cells_undecided");
    expect(table.leaning).toBe("core_per_reader");
    expect(table.rows[0]?.weakest).toBe(0.4);
    expect(table.rows[0]?.undecidedCells).toBe(1);
  });

  test("an undecided cell still carries its score, so the leaning survives the bar", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.4),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    const cell = table.rows[0]?.cells.find((one) => one.dimension === "boot_image_risk");
    expect(cell?.score).toBe(2);
    expect(cell?.decided).toBe(false);
  });

  test("a cell the service never answered is left out of the weighting, not read as zero", () => {
    const missing: JevVerdict = {
      type: "score",
      verdict: "undecided",
      value: 0,
      valueKind: "confidence",
      threshold: 0.75,
      reason: "answer_missing",
    };
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(2, 0.9),
      "memory::core_per_reader": missing,
      "memory::realtime_timer_removed": scored(0, 0.9),
    });
    const core = table.rows.find((row) => row.option === "core_per_reader");
    expect(core?.weighted).toBe(1);
    expect(core?.answeredCells).toBe(1);
    expect(table.winner).toBeUndefined();
  });

  test("the default margin is the one the table uses when none is given", () => {
    expect(JEV_SCORE_DEFAULT_MARGIN).toBe(0.05);
  });

  test("a caller can widen the margin it needs before it calls a winner", () => {
    const answers = {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(1, 0.9),
      "memory::core_per_reader": scored(2, 0.9),
      "memory::realtime_timer_removed": scored(1, 0.9),
    };
    expect(jevScoreTable(PLAN, answers).winner).toBe("core_per_reader");
    expect(jevScoreTable(PLAN, answers, 0.8).winner).toBeUndefined();
  });
});

describe("the table a human reads", () => {
  const text = jevScoreTableText(
    jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    }),
  );

  test("names every option, every dimension and the weight it carried", () => {
    expect(text).toContain("core_per_reader");
    expect(text).toContain("boot_image_risk");
    expect(text).toContain("0.75");
  });

  test("prints each cell's confidence beside its score", () => {
    expect(text).toMatch(/2\.00 \(0\.90\)/u);
  });

  test("ends with the winner and the margin that decided it", () => {
    expect(text).toContain("winner core_per_reader");
    expect(text).toContain("margin");
  });
});

describe("the command line in front of it", () => {
  const repositoryRoot = join(import.meta.dir, "..");
  const cli = join(repositoryRoot, "src", "jev-score-options.ts");
  const workspace = mkdtempSync(join(tmpdir(), "jev-score-"));
  afterAll(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  async function run(argv: readonly string[]): Promise<{ code: number; out: string; err: string }> {
    const child = Bun.spawn(["bun", cli, ...argv], {
      cwd: repositoryRoot,
      // No key: every cell comes back undecided, which is the safe shape of this tool.
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

  const planPath = join(workspace, "plan.json");
  writeFileSync(planPath, JSON.stringify(PLAN), "utf8");

  test("prints a table and exits 0 even when nothing could be judged", async () => {
    const result = await run(["--plan", planPath]);
    expect(result.code).toBe(0);
    expect(result.out).toContain("core_per_reader");
    expect(result.out).toContain("no winner");
  });

  test("--json prints the same table as data", async () => {
    const result = await run(["--plan", planPath, "--json"]);
    const table = JSON.parse(result.out);
    expect(table.weights).toEqual({ boot_image_risk: 0.75, memory: 0.25 });
    expect(table.winner).toBeUndefined();
    expect(table.rows).toHaveLength(2);
  });

  test("refuses a plan whose levels are single words", async () => {
    const bad = join(workspace, "bad.json");
    writeFileSync(
      bad,
      JSON.stringify({
        state: "a state",
        options: { one: "the first", two: "the second" },
        dimensions: { risk: { question: "How risky?", criteria: ["low", "high"] } },
      }),
      "utf8",
    );
    const result = await run(["--plan", bad]);
    expect(result.code).toBe(2);
    expect(result.err).toContain("concrete situation");
  });

  test("refuses a call with no plan", async () => {
    const result = await run([]);
    expect(result.code).toBe(2);
    expect(result.err).toContain("--plan");
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(["--help"]);
    expect(help.code).toBe(0);
    expect(help.out).toContain("--min-margin");
  });
});

describe("a table with a hole in it names no winner", () => {
  const unanswered: JevVerdict = {
    type: "score",
    verdict: "undecided",
    value: 0,
    valueKind: "confidence",
    threshold: 0.75,
    reason: "answer_missing",
  };

  // A row with an unanswered cell is weighted over the cells it does have, so its total is not on
  // the same scale as a complete row. The missing cell could put it in front, which means the
  // leader is not known to be the leader.
  test("an unanswered cell anywhere stops the winner, not only under the leader", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(2, 0.9),
      "memory::core_per_reader": scored(2, 0.9),
      "memory::realtime_timer_removed": unanswered,
    });
    expect(table.rows[0]?.option).toBe("core_per_reader");
    expect(table.rows[0]?.undecidedCells).toBe(0);
    expect(table.winner).toBeUndefined();
    expect(table.reason).toBe("cells_unanswered");
    expect(table.leaning).toBe("core_per_reader");
  });

  test("the row that is missing a cell says so in its own count", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(2, 0.9),
      "memory::core_per_reader": scored(2, 0.9),
      "memory::realtime_timer_removed": unanswered,
    });
    expect(table.rows.find((row) => row.option === "realtime_timer_removed")?.answeredCells).toBe(
      1,
    );
  });

  test("a complete table still names its winner", () => {
    const table = jevScoreTable(PLAN, {
      "boot_image_risk::core_per_reader": scored(2, 0.9),
      "boot_image_risk::realtime_timer_removed": scored(0, 0.9),
      "memory::core_per_reader": scored(1, 0.9),
      "memory::realtime_timer_removed": scored(2, 0.9),
    });
    expect(table.winner).toBe("core_per_reader");
  });
});
