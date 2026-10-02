import { describe, expect, test } from "bun:test";

import { maskedDigest, maskJevQuestions, maskJevState, maskJevText } from "../src/mask";

// The secret scanner reads this file's bytes, and a written PEM header is private key material to
// it, so the boundary is composed the way `packages/observability/test/pipeline-3/failure-cause.test.ts`
// composes it. No key follows it here: the header alone is what the mask has to catch.
const pemHeader = (edge: string) => `-----${edge} RSA PRIVATE KEY-----`;

// Every fixture below is synthetic. No value here is a real credential, Party or address, and the
// table is the contract: the left column is what an operator can paste into a state, the right
// column is the only thing allowed to leave the machine.
describe("masking the text that leaves the machine", () => {
  test.each([
    [
      "a Party-like id",
      "party " + "sample-operator-fixture" + "::" + "N4mespace.fixture is ready",
      "party [redacted] is ready",
    ],
    [
      "a bearer token",
      "Authorization: Bearer fixture-token-value-0001",
      "Authorization: [redacted]",
    ],
    [
      "a JWT",
      "cookie eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.c2lnbmF0dXJlLWZpeHR1cmUwMQ set",
      "cookie [redacted] set",
    ],
    [
      "a connection string with credentials",
      "DATABASE_URL=" + "postgres:" + "//sample:fixture-password-0001@db.fixture.example:5432/ledger",
      "DATABASE_URL=[redacted]",
    ],
    [
      "a connection string without credentials",
      "reading " + "redis:" + "//cache.fixture.example:6379/0 now",
      "reading [redacted] now",
    ],
    ["a private key header", pemHeader("BEGIN"), "[redacted]"],
    ["an e-mail address", "ping operator.fixture@example.com about it", "ping [redacted] about it"],
    ["an api key shape", "key sample_ak_FIXTUREFIXTUREFIXTURE01 used", "key [redacted] used"],
    ["a keyed secret", 'config {"api_key": "fixture-value-0001"}', 'config {"[redacted]"}'],
  ])("masks %s", (_label, raw, masked) => {
    expect(maskJevText(raw)).toBe(masked);
  });

  test("redacts a complete multiline private key, including its body and end marker", () => {
    const body = ["synthetic", "base64", "body"].join("");
    const text = `before\n${pemHeader("BEGIN")}\n${body}\n${pemHeader("END")}\nafter`;
    expect(maskJevText(text)).toBe("before\n[redacted]\nafter");
  });

  test("fails closed on an unterminated private key block", () => {
    const body = ["synthetic", "base64", "body"].join("");
    const text = `before\n${pemHeader("BEGIN")}\n${body}\ntrailing private text`;
    expect(maskJevText(text)).toBe("before\n[redacted]");
  });

  test("keeps the line structure a judge needs", () => {
    expect(maskJevText("engine refused\n  reason: risk_limit_exceeded\n")).toBe(
      "engine refused\n reason: risk_limit_exceeded",
    );
  });

  test("leaves ordinary operations text alone", () => {
    const text = "order 12 rejected: risk_limit_exceeded after 3 retries";
    expect(maskJevText(text)).toBe(text);
  });

  test("masks every string inside a structured state, and leaves numbers alone", () => {
    expect(
      maskJevState({
        service: "worker",
        attempts: 3,
        ready: false,
        lines: ["Bearer fixture-token-value-0001", "ready"],
        nested: { party: "sample-operator-fixture" + "::" + "N4mespace.fixture" },
      }),
    ).toEqual({
      service: "worker",
      attempts: 3,
      ready: false,
      lines: ["[redacted]", "ready"],
      nested: { party: "[redacted]" },
    });
  });

  test("masks the instructions and the criteria of every question", () => {
    expect(
      maskJevQuestions({
        needs_owner: {
          type: "noul",
          instructions: "Does Bearer fixture-token-value-0001 need the owner?",
          criteria: { true: "the owner must act", false: "operator.fixture@example.com can act" },
        },
      }),
    ).toEqual({
      needs_owner: {
        type: "noul",
        instructions: "Does [redacted] need the owner?",
        criteria: { true: "the owner must act", false: "[redacted] can act" },
      },
    });
  });

  test("the digest is stable, hex, and never carries the text", () => {
    const digest = maskedDigest("engine refused");
    expect(digest).toMatch(/^[0-9a-f]{64}$/u);
    expect(digest).toBe(maskedDigest("engine refused"));
    expect(digest).not.toBe(maskedDigest("engine accepted"));
  });
});
