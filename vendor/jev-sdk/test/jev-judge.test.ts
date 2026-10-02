import { describe, expect, test } from "bun:test";
import { jevDecisionLine } from "../src/decision-log";

import {
  JEV_ENDPOINT,
  JEV_MODEL,
  type JevDependencies,
  type JevJudgeRequest,
  JevUsageError,
  judge,
} from "../src/judge";

type Call = { url: string; init: RequestInit };

function recorder(responses: readonly (Response | Error)[]) {
  const calls: Call[] = [];
  const slept: number[] = [];
  let index = 0;
  let clock = 1_000;
  const dependencies: JevDependencies = {
    apiKey: ["fixture", "key", "value", "0001"].join("-"),
    fetch: (url, init) => {
      calls.push({ url: String(url), init: init ?? {} });
      clock += 12;
      const next = responses[Math.min(index, responses.length - 1)];
      index += 1;
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next.clone());
    },
    now: () => clock,
    sleep: (milliseconds) => {
      slept.push(milliseconds);
      clock += milliseconds; // a sleep spends the call's budget, so the clock has to move
      return Promise.resolve();
    },
  };
  return { calls, slept, dependencies };
}

function answered(answers: unknown): Response {
  return new Response(JSON.stringify({ model: "jev-1.13.0", answers }), { status: 200 });
}

const NOUL: JevJudgeRequest = {
  state: "the worker refused to settle",
  questions: { needs_owner: { type: "noul", instructions: "Does the owner have to act?" } },
  thresholds: { needs_owner: 0.8 },
  timeoutMs: 5_000,
};

describe("the request that leaves the machine", () => {
  test("posts one masked request to the documented endpoint", async () => {
    const { calls, dependencies } = recorder([
      answered({ needs_owner: { type: "noul", noul: 0.9 } }),
    ]);
    await judge(
      {
        ...NOUL,
        state: "worker Bearer fixture-token-value-0001 refused",
        questions: {
          needs_owner: {
            type: "noul",
            instructions: "Does operator.fixture@example.com have to act?",
          },
        },
      },
      dependencies,
    );

    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(JEV_ENDPOINT);
    expect(calls[0]?.init.method).toBe("POST");
    const headers = new Headers(calls[0]?.init.headers);
    expect(headers.get("authorization")).toBe("Bearer fixture-key-value-0001");
    expect(headers.get("content-type")).toBe("application/json");
    const body = JSON.parse(String(calls[0]?.init.body));
    expect(body.model).toBe(JEV_MODEL);
    expect(body.state).toBe("worker [redacted] refused");
    expect(body.questions.needs_owner.instructions).toBe("Does [redacted] have to act?");
    expect(String(calls[0]?.init.body)).not.toContain("fixture-token-value-0001");
  });

  test("never sends structured credentials in state or question fields", async () => {
    const secret = ["synthetic", "credential", "value"].join("-");
    const request: JevJudgeRequest = {
      state: { nested: { apiKey: secret }, items: [{ password: secret }, { refreshToken: secret }] },
      questions: {
        needs_owner: {
          type: "noul",
          instructions: { context: [{ authorization: secret }] },
          criteria: { true: { privateKey: secret }, false: "no action" },
        },
      },
    };
    const { calls, dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.9 } })]);
    const result = await judge(request, dependencies);
    const outbound = String(calls[0]?.init.body);
    const logged = jevDecisionLine(request, result, new Date().toISOString());
    expect(outbound).not.toContain(secret);
    expect(logged).not.toContain(secret);
    const body = JSON.parse(outbound);
    expect(body.state.nested.apiKey).toBe("[redacted]");
    expect(body.state.items).toEqual([{ password: "[redacted]" }, { refreshToken: "[redacted]" }]);
    expect(body.questions.needs_owner.instructions.context[0].authorization).toBe("[redacted]");
    expect(body.questions.needs_owner.criteria.true.privateKey).toBe("[redacted]");
  });

  test("measures one latency for the call", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.9 } })]);
    expect((await judge(NOUL, dependencies)).latencyMs).toBe(12);
  });
});

describe("a noul answer", () => {
  test.each([
    [0.96, "yes", 0.92],
    [0.8, "yes", 0.6],
    [0.04, "no", 0.92],
    [0.2, "no", 0.6],
  ])("probability %p is %s", async (noul, verdict, margin) => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul } })]);
    const result = await judge(NOUL, dependencies);
    expect(result.verdicts.needs_owner).toEqual({
      type: "noul",
      verdict,
      value: noul,
      valueKind: "noul-probability",
      threshold: 0.8,
      leaning: verdict,
      margin,
    });
  });

  test("a probability inside the uncertain band is undecided, never yes", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.55 } })]);
    const result = await judge(NOUL, dependencies);
    expect(result.verdicts.needs_owner).toEqual({
      type: "noul",
      verdict: "undecided",
      value: 0.55,
      valueKind: "noul-probability",
      threshold: 0.8,
      reason: "below_threshold",
      leaning: "yes",
      margin: 0.1,
    });
  });
});

describe("a choice and a score answer", () => {
  const request: JevJudgeRequest = {
    state: "the api returned 500 for every order",
    questions: {
      failure_layer: {
        type: "choice",
        instructions: "Which layer failed?",
        criteria: { frontend: null, api: null, data: null, blockchain: null },
      },
    },
    thresholds: { failure_layer: 0.7 },
    timeoutMs: 5_000,
  };

  test("a confident choice is its option", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: {
          type: "choice",
          choice: "api",
          probabilities: { frontend: 0.05, api: 0.85, data: 0.1, blockchain: 0 },
          confidence: 0.82,
        },
      }),
    ]);
    expect((await judge(request, dependencies)).verdicts.failure_layer).toEqual({
      type: "choice",
      verdict: "api",
      value: 0.82,
      valueKind: "confidence",
      threshold: 0.7,
      leaning: "api",
      probabilities: { frontend: 0.05, api: 0.85, data: 0.1, blockchain: 0 },
      margin: 0.75,
    });
  });

  test("an unconfident choice is undecided", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: {
          type: "choice",
          choice: "api",
          probabilities: { frontend: 0.3, api: 0.35, data: 0.35, blockchain: 0 },
          confidence: 0.4,
        },
      }),
    ]);
    expect((await judge(request, dependencies)).verdicts.failure_layer).toEqual({
      type: "choice",
      verdict: "undecided",
      value: 0.4,
      valueKind: "confidence",
      threshold: 0.7,
      reason: "below_threshold",
      leaning: "api",
      probabilities: { frontend: 0.3, api: 0.35, data: 0.35, blockchain: 0 },
      margin: 0,
    });
  });

  test("a chosen option outside the declared criteria is a schema mismatch", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: { type: "choice", choice: "engine", probabilities: {}, confidence: 0.99 },
      }),
    ]);
    expect((await judge(request, dependencies)).verdicts.failure_layer.reason).toBe(
      "response_schema_mismatch",
    );
  });

  test("a confident score is the score it returned", async () => {
    const scored: JevJudgeRequest = {
      state: "three retries, still failing",
      questions: {
        task_risk: { type: "score", instructions: "How risky?", criteria: ["low", "high"] },
      },
      thresholds: { task_risk: 0.6 },
      timeoutMs: 5_000,
    };
    const { dependencies } = recorder([
      answered({
        task_risk: {
          type: "score",
          score: 1.25,
          legend: { "0": "low", "1": "high" },
          probabilities: { "0": 0.25, "1": 0.75 },
          confidence: 0.75,
        },
      }),
    ]);
    expect((await judge(scored, dependencies)).verdicts.task_risk).toEqual({
      type: "score",
      verdict: "1.25",
      value: 0.75,
      valueKind: "confidence",
      threshold: 0.6,
      leaning: "1.25",
      probabilities: { "0": 0.25, "1": 0.75 },
      margin: 0.5,
    });
  });
});

describe("every failure is the closed verdict undecided", () => {
  test("a missing key refuses without one request", async () => {
    const { calls, dependencies } = recorder([answered({})]);
    const result = await judge(NOUL, { ...dependencies, apiKey: "" });
    expect(calls).toHaveLength(0);
    expect(result.model).toBe("");
    expect(result.verdicts.needs_owner).toEqual({
      type: "noul",
      verdict: "undecided",
      value: 0,
      valueKind: "noul-probability",
      threshold: 0.8,
      reason: "typesafe_api_key_missing",
    });
  });

  test.each([
    ["a timeout", Object.assign(new Error("aborted"), { name: "TimeoutError" }), "request_timeout"],
    ["an abort", Object.assign(new Error("aborted"), { name: "AbortError" }), "request_timeout"],
    ["a network error", new Error("connect ECONNREFUSED"), "network_error"],
  ])("%s is %s", async (_label, failure, reason) => {
    const { dependencies } = recorder([failure]);
    const result = await judge(NOUL, dependencies);
    expect(result.verdicts.needs_owner.verdict).toBe("undecided");
    expect(String(result.verdicts.needs_owner.reason)).toBe(reason);
  });

  test.each([
    [401, "http_client_error"],
    [422, "http_client_error"],
    [500, "http_server_error"],
    [529, "http_server_error"],
  ])("status %p is %s", async (status, reason) => {
    const { dependencies } = recorder([new Response("{}", { status })]);
    expect(String((await judge(NOUL, dependencies)).verdicts.needs_owner.reason)).toBe(reason);
  });

  test.each([
    ["a body that is not an object", new Response("[]", { status: 200 })],
    [
      "a body with no answers",
      new Response(JSON.stringify({ model: "jev-1.13.0" }), { status: 200 }),
    ],
    ["a body that is not JSON", new Response("not json", { status: 200 })],
  ])("%s is a schema mismatch", async (_label, response) => {
    const { dependencies } = recorder([response]);
    expect((await judge(NOUL, dependencies)).verdicts.needs_owner.reason).toBe(
      "response_schema_mismatch",
    );
  });

  test("an answer that is absent for a question id is undecided for that id only", async () => {
    const two: JevJudgeRequest = {
      ...NOUL,
      questions: {
        needs_owner: { type: "noul", instructions: "Does the owner have to act?" },
        touches_money_path: { type: "noul", instructions: "Does it touch money?" },
      },
      thresholds: { needs_owner: 0.8, touches_money_path: 0.8 },
    };
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.95 } })]);
    const result = await judge(two, dependencies);
    expect(result.verdicts.needs_owner.verdict).toBe("yes");
    expect(result.verdicts.touches_money_path).toEqual({
      type: "noul",
      verdict: "undecided",
      value: 0,
      valueKind: "noul-probability",
      threshold: 0.8,
      reason: "answer_missing",
    });
  });

  test("an answer of the wrong type for its question is undecided", async () => {
    const { dependencies } = recorder([
      answered({ needs_owner: { type: "choice", choice: "yes", confidence: 0.99 } }),
    ]);
    expect((await judge(NOUL, dependencies)).verdicts.needs_owner.reason).toBe(
      "answer_type_mismatch",
    );
  });

  test("a probability outside 0 to 1 is a schema mismatch, not a yes", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 1.4 } })]);
    const verdict = (await judge(NOUL, dependencies)).verdicts.needs_owner;
    expect(verdict.verdict).toBe("undecided");
    expect(verdict.reason).toBe("response_schema_mismatch");
  });
});

describe("rate limits retry, and nothing else does", () => {
  test("honours retry-after twice, then answers", async () => {
    const limited = new Response("{}", { status: 429, headers: { "retry-after": "2" } });
    const { calls, slept, dependencies } = recorder([
      limited,
      limited.clone(),
      answered({ needs_owner: { type: "noul", noul: 0.93 } }),
    ]);
    const result = await judge({ ...NOUL, timeoutMs: 10_000 }, dependencies);
    expect(calls).toHaveLength(3);
    expect(slept).toEqual([2_000, 2_000]);
    expect(result.verdicts.needs_owner.verdict).toBe("yes");
  });

  test("stops after two retries and stays undecided", async () => {
    const { calls, slept, dependencies } = recorder([
      new Response("{}", { status: 429, headers: { "retry-after": "1" } }),
    ]);
    const result = await judge(NOUL, dependencies);
    expect(calls).toHaveLength(3);
    expect(slept).toEqual([1_000, 1_000]);
    expect(result.verdicts.needs_owner.reason).toBe("rate_limited");
  });

  test("a wait longer than the call budget is refused at once, with no sleep", async () => {
    const { calls, slept, dependencies } = recorder([
      new Response("{}", { status: 429, headers: { "retry-after": "60" } }),
    ]);
    const result = await judge({ ...NOUL, timeoutMs: 8_000 }, dependencies);
    expect(calls).toHaveLength(1);
    expect(slept).toEqual([]);
    expect(result.latencyMs).toBeLessThan(8_000);
    expect(result.verdicts.needs_owner.reason).toBe("rate_limited");
  });

  test("stops retrying when the next wait plus an attempt no longer fits", async () => {
    const { slept, dependencies } = recorder([
      new Response("{}", { status: 429, headers: { "retry-after": "2" } }),
    ]);
    const result = await judge({ ...NOUL, timeoutMs: 5_000 }, dependencies);
    expect(slept).toEqual([2_000]);
    expect(result.latencyMs).toBeLessThanOrEqual(5_000);
    expect(result.verdicts.needs_owner.reason).toBe("rate_limited");
  });

  test("a 500 is never retried", async () => {
    const { calls, dependencies } = recorder([new Response("{}", { status: 500 })]);
    await judge(NOUL, dependencies);
    expect(calls).toHaveLength(1);
  });
});

describe("a caller mistake is a usage error, never a verdict", () => {
  const { dependencies } = recorder([answered({})]);
  test.each([
    ["no question", { ...NOUL, questions: {}, thresholds: {} }],
    [
      "an unknown question type",
      { ...NOUL, questions: { q: { type: "guess", instructions: "?" } } as never },
    ],
    [
      "a choice with one option",
      {
        ...NOUL,
        questions: { q: { type: "choice", instructions: "?", criteria: { only: null } } },
        thresholds: { q: 0.8 },
      },
    ],
    [
      "a score with one level",
      {
        ...NOUL,
        questions: { q: { type: "score", instructions: "?", criteria: ["one"] } },
        thresholds: { q: 0.8 },
      },
    ],
    ["a threshold above one", { ...NOUL, thresholds: { needs_owner: 1.2 } }],
    ["a threshold at zero", { ...NOUL, thresholds: { needs_owner: 0 } }],
    ["an empty state", { ...NOUL, state: "   " }],
    ["a timeout of zero", { ...NOUL, timeoutMs: 0 }],
  ])("%s throws", async (_label, request) => {
    await expect(judge(request as JevJudgeRequest, dependencies)).rejects.toThrow(JevUsageError);
  });

  test("a state larger than the documented budget throws", async () => {
    await expect(judge({ ...NOUL, state: "x".repeat(96_001) }, dependencies)).rejects.toThrow(
      JevUsageError,
    );
  });

  test("a question with no threshold uses the default", async () => {
    const { dependencies: fresh } = recorder([
      answered({ needs_owner: { type: "noul", noul: 0.9 } }),
    ]);
    const result = await judge({ ...NOUL, thresholds: {} }, fresh);
    expect(result.verdicts.needs_owner.threshold).toBe(0.8);
    expect(result.verdicts.needs_owner.verdict).toBe("yes");
  });
});

describe("the bar a question clears is set by its stakes", () => {
  const OPTIONS = { criteria: { frontend: null, api: null, data: null, blockchain: null } };

  test.each([
    ["passive", 0.6],
    ["design", 0.75],
    ["critical", 0.9],
  ])("a %s question is judged at %p", async (stakes, threshold) => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.95 } })]);
    const result = await judge(
      {
        state: "the worker refused to settle",
        questions: {
          needs_owner: { type: "noul", instructions: "Does the owner have to act?", stakes },
        },
      } as JevJudgeRequest,
      dependencies,
    );
    expect(result.verdicts.needs_owner.threshold).toBe(threshold);
  });

  test("a question with no stakes keeps the default bar", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.95 } })]);
    const result = await judge({ ...NOUL, thresholds: {} }, dependencies);
    expect(result.verdicts.needs_owner.threshold).toBe(0.8);
  });

  test("an explicit threshold overrides the stakes the question declares", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.95 } })]);
    const result = await judge(
      {
        state: "the worker refused to settle",
        questions: {
          needs_owner: {
            type: "noul",
            instructions: "Does the owner have to act?",
            stakes: "passive",
          },
        },
        thresholds: { needs_owner: 0.92 },
      } as JevJudgeRequest,
      dependencies,
    );
    expect(result.verdicts.needs_owner.threshold).toBe(0.92);
  });

  test("the passive bar decides a routing question the flat bar left undecided", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: {
          type: "choice",
          choice: "api",
          probabilities: { frontend: 0.1, api: 0.72, data: 0.14, blockchain: 0.04 },
          confidence: 0.64,
        },
      }),
    ]);
    const result = await judge(
      {
        state: "the api returned 500 for every order",
        questions: {
          failure_layer: {
            type: "choice",
            instructions: "Which layer failed?",
            stakes: "passive",
            ...OPTIONS,
          },
        },
      } as JevJudgeRequest,
      dependencies,
    );
    expect(result.verdicts.failure_layer.verdict).toBe("api");
    expect(result.verdicts.failure_layer.threshold).toBe(0.6);
  });

  test("the critical bar leaves the same confidence undecided", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: {
          type: "choice",
          choice: "api",
          probabilities: { frontend: 0.1, api: 0.72, data: 0.14, blockchain: 0.04 },
          confidence: 0.64,
        },
      }),
    ]);
    const result = await judge(
      {
        state: "the api returned 500 for every order",
        questions: {
          failure_layer: {
            type: "choice",
            instructions: "Which layer failed?",
            stakes: "critical",
            ...OPTIONS,
          },
        },
      } as JevJudgeRequest,
      dependencies,
    );
    expect(result.verdicts.failure_layer.verdict).toBe("undecided");
    expect(result.verdicts.failure_layer.reason).toBe("below_threshold");
  });

  test("an unknown stakes word is a usage error, never a quietly lowered bar", async () => {
    const { dependencies } = recorder([answered({})]);
    await expect(
      judge(
        {
          state: "a state",
          questions: { q: { type: "noul", instructions: "?", stakes: "low" } },
        } as unknown as JevJudgeRequest,
        dependencies,
      ),
    ).rejects.toThrow(JevUsageError);
  });

  test("the stakes word never reaches the model", async () => {
    const { calls, dependencies } = recorder([
      answered({ needs_owner: { type: "noul", noul: 0.95 } }),
    ]);
    await judge(
      {
        state: "the worker refused to settle",
        questions: {
          needs_owner: {
            type: "noul",
            instructions: "Does the owner have to act?",
            stakes: "critical",
          },
        },
      } as JevJudgeRequest,
      dependencies,
    );
    expect(String(calls[0]?.init.body)).not.toContain("critical");
    expect(String(calls[0]?.init.body)).not.toContain("stakes");
  });
});

describe("a verdict carries the distribution it came from", () => {
  test("a choice keeps every option's probability, the margin and the leaning", async () => {
    const { dependencies } = recorder([
      answered({
        failure_layer: {
          type: "choice",
          choice: "api",
          probabilities: { frontend: 0.05, api: 0.6, data: 0.3, blockchain: 0.05 },
          confidence: 0.47,
        },
      }),
    ]);
    const verdict = (
      await judge(
        {
          state: "the api returned 500 for every order",
          questions: {
            failure_layer: {
              type: "choice",
              instructions: "Which layer failed?",
              criteria: { frontend: null, api: null, data: null, blockchain: null },
            },
          },
        } as JevJudgeRequest,
        dependencies,
      )
    ).verdicts.failure_layer;
    expect(verdict.verdict).toBe("undecided");
    expect(verdict.probabilities).toEqual({
      frontend: 0.05,
      api: 0.6,
      data: 0.3,
      blockchain: 0.05,
    });
    expect(verdict.margin).toBe(0.3);
    expect(verdict.leaning).toBe("api");
  });

  test("a score keeps its level probabilities, its margin and the score it leaned to", async () => {
    const { dependencies } = recorder([
      answered({
        task_risk: {
          type: "score",
          score: 1.25,
          legend: { "0": "low", "1": "high" },
          probabilities: { "0": 0.25, "1": 0.75 },
          confidence: 0.5,
        },
      }),
    ]);
    const verdict = (
      await judge(
        {
          state: "three retries, still failing",
          questions: {
            task_risk: { type: "score", instructions: "How risky?", criteria: ["low", "high"] },
          },
        } as JevJudgeRequest,
        dependencies,
      )
    ).verdicts.task_risk;
    expect(verdict.verdict).toBe("undecided");
    expect(verdict.leaning).toBe("1.25");
    expect(verdict.probabilities).toEqual({ "0": 0.25, "1": 0.75 });
    expect(verdict.margin).toBe(0.5);
  });

  test("a noul leans yes or no and carries its distance from the coin flip", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.72 } })]);
    const verdict = (await judge(NOUL, dependencies)).verdicts.needs_owner;
    expect(verdict.verdict).toBe("undecided");
    expect(verdict.leaning).toBe("yes");
    expect(verdict.margin).toBe(0.44);
    expect(verdict.probabilities).toBeUndefined();
  });

  test("a decided verdict agrees with its own leaning", async () => {
    const { dependencies } = recorder([answered({ needs_owner: { type: "noul", noul: 0.18 } })]);
    const verdict = (await judge(NOUL, dependencies)).verdicts.needs_owner;
    expect(verdict.verdict).toBe("no");
    expect(verdict.leaning).toBe("no");
    expect(verdict.margin).toBe(0.64);
  });

  test("a transport failure carries no leaning, because nothing was answered", async () => {
    const { dependencies } = recorder([new Response("{}", { status: 500 })]);
    const verdict = (await judge(NOUL, dependencies)).verdicts.needs_owner;
    expect(verdict.leaning).toBeUndefined();
    expect(verdict.margin).toBeUndefined();
    expect(verdict.probabilities).toBeUndefined();
  });

  test("a distribution the service did not send is left out, and the verdict still decides", async () => {
    const { dependencies } = recorder([
      answered({ failure_layer: { type: "choice", choice: "api", confidence: 0.92 } }),
    ]);
    const verdict = (
      await judge(
        {
          state: "the api returned 500 for every order",
          questions: {
            failure_layer: {
              type: "choice",
              instructions: "Which layer failed?",
              criteria: { frontend: null, api: null, data: null, blockchain: null },
            },
          },
        } as JevJudgeRequest,
        dependencies,
      )
    ).verdicts.failure_layer;
    expect(verdict.verdict).toBe("api");
    expect(verdict.leaning).toBe("api");
    expect(verdict.probabilities).toBeUndefined();
    expect(verdict.margin).toBeUndefined();
  });

  test("a single-option distribution has the whole probability as its margin", async () => {
    const { dependencies } = recorder([
      answered({
        task_risk: {
          type: "score",
          score: 0,
          legend: { "0": "low", "1": "high" },
          probabilities: { "0": 1 },
          confidence: 1,
        },
      }),
    ]);
    const verdict = (
      await judge(
        {
          state: "nothing failed",
          questions: {
            task_risk: { type: "score", instructions: "How risky?", criteria: ["low", "high"] },
          },
        } as JevJudgeRequest,
        dependencies,
      )
    ).verdicts.task_risk;
    expect(verdict.margin).toBe(1);
  });
});
