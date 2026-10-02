import { describe, expect, test } from "bun:test";
import { join } from "node:path";

import { JEV_SMOKE_DEFAULT_CALLS, jevLatencySummary } from "../src/jev-smoke";

const repositoryRoot = join(import.meta.dir, "..");
const smoke = join(repositoryRoot, "src", "jev-smoke.ts");

describe("the live smoke", () => {
  test("measures five calls by default", () => {
    expect(JEV_SMOKE_DEFAULT_CALLS).toBe(5);
  });

  test.each([
    [[12], { p50: 12, max: 12 }],
    [[30, 10, 20], { p50: 20, max: 30 }],
    [[40, 10, 30, 20], { p50: 30, max: 40 }],
  ])("summarises %p", (samples, summary) => {
    expect(jevLatencySummary(samples)).toEqual(summary);
  });

  test("refuses to report a latency it has no samples for", () => {
    expect(() => jevLatencySummary([])).toThrow();
  });

  test("refuses to run without the key instead of reporting a fake number", async () => {
    const child = Bun.spawn(["bun", smoke], {
      cwd: repositoryRoot,
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdout: "pipe",
      stderr: "pipe",
    });
    const [stderr, code] = await Promise.all([new Response(child.stderr).text(), child.exited]);
    expect(code).toBe(2);
    expect(stderr).toContain("TYPESAFE_API_KEY");
  });
});
