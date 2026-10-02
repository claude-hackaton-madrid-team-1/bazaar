import { afterAll, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  JEV_CAPABILITY_EXCERPT_CHARACTERS,
  JEV_CAPABILITY_FITS_THRESHOLD,
  JEV_CAPABILITY_GATE_THRESHOLD,
  JEV_CAPABILITY_SHORTLIST,
  type JevCapability,
  jevCallFailed,
  jevCapabilityFrom,
  jevCapabilitySuggestion,
  jevExtraCapabilitiesFrom,
  jevGateFrom,
  jevRerankQuestions,
  jevRosterFrom,
  jevShortlistFrom,
  jevWideQuestions,
} from "../src/capabilities";
import { JevUsageError, type JevVerdict, maskedJevRequest } from "../src/judge";

const workspace = mkdtempSync(join(tmpdir(), "jev-caps-"));
afterAll(() => {
  rmSync(workspace, { recursive: true, force: true });
});

const ROSTER: readonly JevCapability[] = [
  {
    name: "native-engine",
    description: "The native engine, its boot, its readiness and its refusals.",
    detail: "Read the engine's composition root before changing a refusal name.",
  },
  {
    name: "cpp-pro",
    description: "Modern C++ language, lifetime and tooling questions.",
    detail: "Concepts, ranges, coroutines and CMake.",
  },
  {
    name: "ledger-reader",
    description: "A ledger reader and its readiness checks.",
    detail: "Name the interface before diagnosing the request.",
  },
  {
    name: "railway-mcp",
    description: "The Railway MCP server: services, variables, logs and deploys.",
  },
];

function noul(probability: number, reason?: "below_threshold" | "network_error"): JevVerdict {
  return {
    type: "noul",
    verdict: reason === undefined ? "yes" : "undecided",
    value: reason === "network_error" ? 0 : probability,
    valueKind: "noul-probability",
    threshold: 0.6,
    ...(reason === undefined ? {} : { reason }),
    ...(reason === "network_error" ? {} : { leaning: probability >= 0.5 ? "yes" : "no" }),
  };
}

describe("the roster a call ranks", () => {
  const skills = join(workspace, "skills");
  mkdirSync(join(skills, "transport-skill"), { recursive: true });
  mkdirSync(join(skills, "not-a-skill"), { recursive: true });
  writeFileSync(
    join(skills, "transport-skill", "SKILL.md"),
    `---\nname: transport-skill\ndescription: "Aeron transport, its media driver and its counters."\nlicense: Apache-2.0\n---\n\n# Aeron\n\nThe media driver runs in its own process.\n`,
    "utf8",
  );
  writeFileSync(join(skills, "not-a-skill", "README.md"), "no front matter here", "utf8");

  test("reads a skill's name, its one line description and the opening of its body", () => {
    const capability = jevCapabilityFrom(
      `---\nname: transport-skill\ndescription: "Aeron transport."\n---\n\n# Aeron\n\nThe media driver runs\nin its own process.\n`,
    );
    expect(capability).toEqual({
      name: "transport-skill",
      description: "Aeron transport.",
      detail: "# Aeron The media driver runs in its own process.",
    });
  });

  test("reads a folded description written over several lines", () => {
    expect(
      jevCapabilityFrom(`---\nname: wide\ndescription: >-\n  one line\n  and a second\n---\nbody\n`)
        ?.description,
    ).toBe("one line and a second");
  });

  test.each([
    ["no front matter", "# just a heading\n"],
    ["no name", `---\ndescription: "something"\n---\nbody\n`],
    ["no description", "---\nname: something\n---\nbody\n"],
  ])("a file with %s is not a capability", (_label, source) => {
    expect(jevCapabilityFrom(source)).toBeUndefined();
  });

  test("the roster is every skill directory that holds a readable SKILL.md", () => {
    expect(jevRosterFrom(skills, []).map((one) => one.name)).toEqual(["transport-skill"]);
  });

  test("extra capabilities join the roster, so an MCP server or a CLI can be ranked too", () => {
    const roster = jevRosterFrom(skills, [
      { name: "railway-mcp", description: "Services, variables, logs and deploys." },
    ]);
    expect(roster.map((one) => one.name)).toEqual(["transport-skill", "railway-mcp"]);
  });

  test("an extras file must hold a list of named, described capabilities", () => {
    expect(
      jevExtraCapabilitiesFrom({ capabilities: [{ name: "gh", description: "the GitHub CLI" }] }),
    ).toEqual([{ name: "gh", description: "the GitHub CLI" }]);
    expect(() => jevExtraCapabilitiesFrom({ capabilities: [{ name: "gh" }] })).toThrow(
      JevUsageError,
    );
  });

  test("a directory that is not there is an empty roster, not a crash", () => {
    expect(jevRosterFrom(join(workspace, "absent"), [])).toEqual([]);
  });
});

describe("pass one: rank the whole roster and ask whether anything is needed", () => {
  const questions = jevWideQuestions(ROSTER);

  test("one choice carries every capability, with its one line description as the criterion", () => {
    expect(questions.which?.type).toBe("choice");
    expect(Object.keys(questions.which?.criteria as object)).toEqual([
      "native-engine",
      "cpp-pro",
      "ledger-reader",
      "railway-mcp",
    ]);
    expect((questions.which?.criteria as Record<string, string>)["cpp-pro"]).toBe(
      "Modern C++ language, lifetime and tooling questions.",
    );
  });

  test("three gate nouls ask whether the unit wants a capability at all", () => {
    const gates = Object.keys(questions).filter((id) => id.startsWith("gate::"));
    expect(gates).toHaveLength(3);
    expect(gates).toContain("gate::prose_suffices");
  });

  test("the ranking call is judged at the passive bar, because it only shortlists", () => {
    expect(questions.which?.stakes).toBe("passive");
  });

  test("the whole call validates through the judge's own validator", () => {
    const masked = maskedJevRequest({ state: { unit: "a unit" }, questions });
    expect(Object.keys(masked.questions)).toHaveLength(4);
  });

  test("a roster with fewer than two capabilities is a usage error, not a choice of one", () => {
    expect(() => jevWideQuestions(ROSTER.slice(0, 1))).toThrow(JevUsageError);
  });
});

describe("the gate that decides whether to suggest anything", () => {
  test("is the mean of the three, with prose_suffices counted the other way round", () => {
    const gate = jevGateFrom({
      "gate::acts_on_repository": noul(0.9),
      "gate::would_follow_documented_procedure": noul(0.9),
      "gate::prose_suffices": noul(0.3),
    });
    // (0.9 + 0.9 + 0.7) / 3
    expect(gate).toBeCloseTo(0.833333, 5);
  });

  test("a probability under the bar still counts, because the gate is a mean, not a verdict", () => {
    expect(
      jevGateFrom({
        "gate::acts_on_repository": noul(0.4, "below_threshold"),
        "gate::would_follow_documented_procedure": noul(0.4, "below_threshold"),
        "gate::prose_suffices": noul(0.4, "below_threshold"),
      }),
    ).toBeCloseTo(0.466666, 5);
  });

  test("a judge that never answered has no gate at all, so nothing is suggested", () => {
    expect(
      jevGateFrom({
        "gate::acts_on_repository": noul(0, "network_error"),
        "gate::would_follow_documented_procedure": noul(0, "network_error"),
        "gate::prose_suffices": noul(0, "network_error"),
      }),
    ).toBeUndefined();
  });

  test("the two thresholds are the measured ones from the cookbook", () => {
    expect(JEV_CAPABILITY_GATE_THRESHOLD).toBe(0.3);
    expect(JEV_CAPABILITY_FITS_THRESHOLD).toBe(0.3);
    expect(JEV_CAPABILITY_SHORTLIST).toBe(3);
    expect(JEV_CAPABILITY_EXCERPT_CHARACTERS).toBe(700);
  });
});

describe("the shortlist pass one hands to pass two", () => {
  const ranked: JevVerdict = {
    type: "choice",
    verdict: "undecided",
    value: 0.42,
    valueKind: "confidence",
    threshold: 0.6,
    reason: "below_threshold",
    leaning: "native-engine",
    probabilities: {
      "native-engine": 0.5,
      "cpp-pro": 0.3,
      "ledger-reader": 0.15,
      "railway-mcp": 0.05,
    },
    margin: 0.2,
  };

  test("is the top of the distribution, not the one option the bar let through", () => {
    expect(jevShortlistFrom(ranked, 3)).toEqual(["native-engine", "cpp-pro", "ledger-reader"]);
  });

  test("drops an option the model gave no probability at all", () => {
    expect(
      jevShortlistFrom({ ...ranked, probabilities: { "cpp-pro": 1, "ledger-reader": 0 } }, 3),
    ).toEqual(["cpp-pro"]);
  });

  test("falls back to the one answer when the service sent no distribution", () => {
    const bare = { ...ranked, probabilities: undefined, margin: undefined };
    expect(jevShortlistFrom(bare, 3)).toEqual(["native-engine"]);
  });

  test("an unanswered ranking shortlists nothing", () => {
    expect(
      jevShortlistFrom(
        {
          type: "choice",
          verdict: "undecided",
          value: 0,
          valueKind: "confidence",
          threshold: 0.6,
          reason: "request_timeout",
        },
        3,
      ),
    ).toEqual([]);
  });
});

describe("pass two: re-read the shortlist with its full text", () => {
  const shortlist = ["native-engine", "cpp-pro", "ledger-reader"];
  const questions = jevRerankQuestions(ROSTER, shortlist, 40);

  test("the choice now carries the description and the opening of the body", () => {
    const criteria = questions.which?.criteria as Record<string, string>;
    expect(Object.keys(criteria)).toEqual(shortlist);
    expect(criteria["native-engine"]).toContain("Read the engine's composition root");
  });

  test("the excerpt is cut to the length the caller asked for", () => {
    const criteria = questions.which?.criteria as Record<string, string>;
    const detail = String(criteria["native-engine"]).split(" — ")[1] ?? "";
    expect(detail.length).toBeLessThanOrEqual(40);
  });

  test("one fits noul per candidate, each answered on its own so all three can be low", () => {
    expect(Object.keys(questions).sort()).toEqual([
      "fits::cpp-pro",
      "fits::ledger-reader",
      "fits::native-engine",
      "which",
    ]);
  });

  test("a capability with no body still gets a criterion, from its description alone", () => {
    const criteria = jevRerankQuestions(ROSTER, ["railway-mcp"], 40).which?.criteria as Record<
      string,
      string
    >;
    expect(criteria["railway-mcp"]).toBe(
      "The Railway MCP server: services, variables, logs and deploys.",
    );
  });

  test("a shortlist naming a capability the roster does not hold is a usage error", () => {
    expect(() => jevRerankQuestions(ROSTER, ["absent"], 40)).toThrow(JevUsageError);
  });
});

describe("what the two passes suggest", () => {
  const shortlist = ["native-engine", "cpp-pro", "ledger-reader"];
  const winner: JevVerdict = {
    type: "choice",
    verdict: "native-engine",
    value: 0.7,
    valueKind: "confidence",
    threshold: 0.6,
    leaning: "native-engine",
  };

  test("every candidate that fits is suggested, the choice winner first", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.62),
      "fits::cpp-pro": noul(0.44),
      "fits::ledger-reader": noul(0.03),
    });
    expect(suggestion.load).toEqual(["native-engine", "cpp-pro"]);
    expect(suggestion.winner).toBe("native-engine");
  });

  test("a shortlist where nothing clears the fits bar suggests nothing", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.2),
      "fits::cpp-pro": noul(0.1),
      "fits::ledger-reader": noul(0.05),
    });
    expect(suggestion.load).toEqual([]);
    expect(suggestion.winner).toBeUndefined();
  });

  test("the winner is suggested even when another candidate fits harder", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.35),
      "fits::cpp-pro": noul(0.9),
      "fits::ledger-reader": noul(0.05),
    });
    expect(suggestion.load[0]).toBe("native-engine");
    expect(suggestion.load).toContain("cpp-pro");
  });

  test("the fits probabilities come back with the suggestion, so a brief can quote them", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.62),
      "fits::cpp-pro": noul(0.44),
      "fits::ledger-reader": noul(0.03),
    });
    expect(suggestion.fits).toEqual({
      "native-engine": 0.62,
      "cpp-pro": 0.44,
      "ledger-reader": 0.03,
    });
  });
});

describe("the command line in front of the two passes", () => {
  const repositoryRoot = join(import.meta.dir, "..");
  const cli = join(repositoryRoot, "src", "jev-capabilities.ts");
  const cliSkills = join(workspace, "cli-skills");
  for (const name of ["sample-one", "sample-two"]) {
    mkdirSync(join(cliSkills, name), { recursive: true });
    writeFileSync(join(cliSkills, name, "SKILL.md"), `---\nname: ${name}\ndescription: Example skill.\n---\n`);
  }

  async function run(
    argv: readonly string[],
    stdin = "rename the engine's refusal codes",
  ): Promise<{ code: number; out: string; err: string }> {
    const child = Bun.spawn(["bun", cli, "--skills", cliSkills, ...argv], {
      cwd: repositoryRoot,
      // No key: pass one answers nothing, so the tool must suggest nothing and say why.
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdin: new TextEncoder().encode(stdin),
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

  test("suggests nothing and names the judge when it could not answer", async () => {
    const result = await run(["--unit", "-", "--json"]);
    expect(result.code).toBe(0);
    const answer = JSON.parse(result.out);
    expect(answer.reason).toBe("judge_unavailable");
    expect(answer.load).toEqual([]);
    expect(answer.calls).toBe(1);
  });

  test("ranks the consumer-provided skill directory", async () => {
    const answer = JSON.parse((await run(["--unit", "-", "--json"])).out);
    expect(answer.roster).toBe(2);
  });

  test("the extras file adds capabilities that are not skills on disk", async () => {
    const extras = join(workspace, "extras.json");
    writeFileSync(
      extras,
      JSON.stringify({
        capabilities: [
          { name: "railway-mcp", description: "Services, variables, logs and deploys." },
          { name: "gh", description: "The GitHub CLI." },
        ],
      }),
      "utf8",
    );
    const bare = JSON.parse((await run(["--unit", "-", "--json"])).out);
    const wide = JSON.parse((await run(["--unit", "-", "--extras", extras, "--json"])).out);
    expect(wide.roster).toBe(bare.roster + 2);
  });

  test("prints a readable answer without --json", async () => {
    const result = await run(["--unit", "-"]);
    expect(result.out).toContain("load nothing");
    expect(result.out).toContain("judge_unavailable");
  });

  test("refuses a call with no unit", async () => {
    const result = await run([]);
    expect(result.code).toBe(2);
    expect(result.err).toContain("--unit");
  });

  test("prints the usage text for --help and exits 0", async () => {
    const help = await run(["--help"]);
    expect(help.code).toBe(0);
    expect(help.out).toContain("--extras");
  });
});

describe("the gate needs all three answers, not whichever arrived", () => {
  // The judge answers each question on its own, so one can come back `answer_missing` while the
  // others hold. Averaging the two that arrived reads them against a bar calibrated for three.
  test.each([
    ["one missing", ["gate::prose_suffices"]],
    ["two missing", ["gate::prose_suffices", "gate::acts_on_repository"]],
  ])("a gate with %s has no reading at all", (_label, missing) => {
    const answered = {
      "gate::acts_on_repository": noul(0.9),
      "gate::would_follow_documented_procedure": noul(0.9),
      "gate::prose_suffices": noul(0.1),
    } as Record<string, JevVerdict>;
    for (const id of missing) delete answered[id];
    expect(jevGateFrom(answered)).toBeUndefined();
  });

  test("a gate whose third answer is present but malformed also has no reading", () => {
    expect(
      jevGateFrom({
        "gate::acts_on_repository": noul(0.9),
        "gate::would_follow_documented_procedure": noul(0.9),
        "gate::prose_suffices": {
          type: "noul",
          verdict: "undecided",
          value: 0,
          valueKind: "noul-probability",
          threshold: 0.6,
          reason: "response_schema_mismatch",
        },
      }),
    ).toBeUndefined();
  });

  test("all three present still reads the mean", () => {
    expect(
      jevGateFrom({
        "gate::acts_on_repository": noul(0.9),
        "gate::would_follow_documented_procedure": noul(0.9),
        "gate::prose_suffices": noul(0.3),
      }),
    ).toBeCloseTo(0.833333, 5);
  });
});

describe("pass two failing as a whole is not the same as nothing fitting", () => {
  const shortlist = ["native-engine", "cpp-pro", "ledger-reader"];
  const winner: JevVerdict = {
    type: "choice",
    verdict: "native-engine",
    value: 0.7,
    valueKind: "confidence",
    threshold: 0.6,
    leaning: "native-engine",
  };

  function unanswered(reason: "request_timeout" | "http_server_error"): JevVerdict {
    return {
      type: "noul",
      verdict: "undecided",
      value: 0,
      valueKind: "noul-probability",
      threshold: 0.6,
      reason,
    };
  }

  function failedCall(reason: "request_timeout" | "http_server_error") {
    return {
      which: { ...unanswered(reason), type: "choice" as const, valueKind: "confidence" as const },
      "fits::native-engine": unanswered(reason),
      "fits::cpp-pro": unanswered(reason),
      "fits::ledger-reader": unanswered(reason),
    };
  }

  test.each([
    ["a timeout", "request_timeout"],
    ["a 5xx", "http_server_error"],
  ])("%s on every answer is a call that did not happen", (_label, reason) => {
    expect(jevCallFailed(failedCall(reason as "request_timeout"))).toBe(true);
  });

  test("a call where every answer sits under its bar did happen", () => {
    expect(
      jevCallFailed({
        which: winner,
        "fits::native-engine": noul(0.2, "below_threshold"),
        "fits::cpp-pro": noul(0.1, "below_threshold"),
        "fits::ledger-reader": noul(0.05, "below_threshold"),
      }),
    ).toBe(false);
  });

  test("a call with no answers at all is not called a failure, because nothing was asked", () => {
    expect(jevCallFailed({})).toBe(false);
  });

  // Reporting nothing_fits for a timeout strips every skill out of the brief and reads as a real
  // answer. The ranking already named the domain skill, so that stands until the judge answers.
  test("a failed pass two says the judge was unavailable, not that nothing fits", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, failedCall("request_timeout"));
    expect(suggestion.reason).toBe("judge_unavailable");
  });

  test("a failed pass two loads the ranking leader rather than an empty list", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, failedCall("http_server_error"));
    expect(suggestion.load).toEqual(["native-engine"]);
    expect(suggestion.winner).toBeUndefined();
    expect(suggestion.fits).toEqual({});
  });

  test("a real answer where nothing clears the bar still says nothing fits", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.2, "below_threshold"),
      "fits::cpp-pro": noul(0.1, "below_threshold"),
      "fits::ledger-reader": noul(0.05, "below_threshold"),
    });
    expect(suggestion.reason).toBe("nothing_fits");
    expect(suggestion.load).toEqual([]);
  });

  test("a real answer that fits says so", () => {
    const suggestion = jevCapabilitySuggestion(shortlist, {
      which: winner,
      "fits::native-engine": noul(0.62),
      "fits::cpp-pro": noul(0.44),
      "fits::ledger-reader": noul(0.03),
    });
    expect(suggestion.reason).toBe("suggested");
    expect(suggestion.load).toEqual(["native-engine", "cpp-pro"]);
  });

  test("a failed pass two with no shortlist behind it loads nothing, and says why", () => {
    const suggestion = jevCapabilitySuggestion([], failedCall("request_timeout"));
    expect(suggestion.reason).toBe("judge_unavailable");
    expect(suggestion.load).toEqual([]);
  });
});
