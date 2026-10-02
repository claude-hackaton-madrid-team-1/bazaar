import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { jevQuestionsFrom } from "../src/jev-judge";
import { maskedJevRequest } from "../src/judge";

const directory = join(import.meta.dir, "..", "questions");

describe("generic question packs", () => {
  test("every shipped pack is a valid Jev request", () => {
    for (const name of readdirSync(directory).filter((file) => file.endsWith(".json"))) {
      const path = join(directory, name);
      const questions = jevQuestionsFrom(readFileSync(path, "utf8"), path);
      expect(Object.keys(maskedJevRequest({ state: "sample", questions }).questions).length).toBeGreaterThan(0);
    }
  });

  test("finding routing gets both questions from separate packs", () => {
    const operations = JSON.parse(readFileSync(join(directory, "agent-operations.json"), "utf8"));
    const plan = JSON.parse(readFileSync(join(directory, "plan-decisions.json"), "utf8"));
    expect(operations.questions.finding_is_real.type).toBe("noul");
    expect(plan.questions.finding_is_pre_existing.type).toBe("noul");
  });
});
