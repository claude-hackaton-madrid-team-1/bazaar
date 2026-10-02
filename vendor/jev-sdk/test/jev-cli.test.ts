import { afterAll, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { jevFractionFlag, jevJsonFileAt, jevNumberFlag, parseJevFlags } from "../src/cli";
import { JevUsageError } from "../src/judge";

const workspace = mkdtempSync(join(tmpdir(), "jev-flags-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

const SPEC = {
  withValue: ["--plan", "--top"],
  switches: ["--json", "--help", "-h"],
  usage: "usage text",
};

describe("one flag parser for every jev command", () => {
  test("reads a value flag and a switch", () => {
    const flags = parseJevFlags(["--plan", "p.json", "--json"], SPEC);
    expect(flags.value("--plan")).toBe("p.json");
    expect(flags.has("--json")).toBe(true);
    expect(flags.has("--help")).toBe(false);
    expect(flags.value("--top")).toBeUndefined();
  });

  test("a value that looks like a flag is refused, so a missing value cannot be swallowed", () => {
    expect(() => parseJevFlags(["--plan", "--json"], SPEC)).toThrow(JevUsageError);
  });

  test.each([
    ["an unknown flag", ["--fast"]],
    ["a value flag at the end", ["--plan"]],
  ])("refuses %s", (_label, argv) => {
    expect(() => parseJevFlags(argv, SPEC)).toThrow(JevUsageError);
  });

  test("the usage text travels with the refusal", () => {
    expect(() => parseJevFlags(["--fast"], SPEC)).toThrow(/usage text/u);
  });

  test("the last value wins when a flag is written twice", () => {
    expect(parseJevFlags(["--top", "3", "--top", "5"], SPEC).value("--top")).toBe("5");
  });
});

describe("reading a number off a flag", () => {
  test("parses a number and keeps an absent flag absent", () => {
    const flags = parseJevFlags(["--top", "4"], SPEC);
    expect(jevNumberFlag(flags, "--top")).toBe(4);
    expect(jevNumberFlag(flags, "--plan")).toBeUndefined();
  });

  test("a value that is not a number is a usage error", () => {
    expect(() => jevNumberFlag(parseJevFlags(["--top", "many"], SPEC), "--top")).toThrow(
      JevUsageError,
    );
  });

  test("a fraction flag refuses a number outside zero to one", () => {
    expect(jevFractionFlag(parseJevFlags(["--top", "0.3"], SPEC), "--top")).toBe(0.3);
    expect(() => jevFractionFlag(parseJevFlags(["--top", "1.4"], SPEC), "--top")).toThrow(
      JevUsageError,
    );
  });
});

describe("reading a JSON file a command was pointed at", () => {
  test("parses the object it holds", () => {
    const path = join(workspace, "plan.json");
    writeFileSync(path, JSON.stringify({ options: { a: "one" } }), "utf8");
    expect(jevJsonFileAt(path)).toEqual({ options: { a: "one" } });
  });

  test.each([
    ["a file that is not there", "absent.json", "{}", false],
    ["a file that is not JSON", "broken.json", "not json", true],
    ["a file that holds no object", "array.json", "[1, 2]", true],
  ])("refuses %s", (_label, name, contents, write) => {
    const path = join(workspace, name);
    if (write) writeFileSync(path, contents, "utf8");
    expect(() => jevJsonFileAt(path)).toThrow(JevUsageError);
  });

  test("the refusal names the path, never the contents", () => {
    const path = join(workspace, "broken.json");
    expect(() => jevJsonFileAt(path)).toThrow(/broken\.json/u);
  });
});
