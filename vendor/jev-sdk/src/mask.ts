// Masking for Jev tooling that sends operator text to a third party judge.
//
// This package owns one list of values that may never leave the machine
// (`public-text-sanitizer.ts`), so this file reuses that list instead of holding a second
// copy of it. It adds what a judge input carries and a public report does not: an e-mail address, a
// bare connection string, and the API key shapes of common services. It keeps
// line structure, which the report sanitizer deliberately collapses, because a judge reads a log or
// a message and the lines carry meaning.
//
// Masking is pure and total: the masked text is what the request sends AND what the decision log
// stores. The raw text is never written anywhere.
import { createHash } from "node:crypto";

import { prohibitedPublicValueLabel, redactProhibitedValues } from "./public-text-sanitizer";

export const JEV_REDACTION = "[redacted]";

/** Credential-bearing JSON fields are masked before inspecting their values. */
const CREDENTIAL_FIELD_NAME = /(?:api[_-]?key|secret|token|password|passphrase|private[_-]?key|signing[_-]?key|authorization)$/iu;

/** JSON that a state or a question may carry. */
export type JevJsonValue =
  | string
  | number
  | boolean
  | null
  | readonly JevJsonValue[]
  | { readonly [key: string]: JevJsonValue };

export const JEV_QUESTION_TYPES = ["noul", "choice", "score"] as const;
export type JevQuestionType = (typeof JEV_QUESTION_TYPES)[number];

/**
 * What it costs to act on a wrong answer, which is what sets the bar the answer has to clear.
 * `https://docs.typesafe.ai/confidence.md` says a confidence threshold is not one number: a
 * read-only action is gated lower than a destructive one, in the same system.
 */
export const JEV_STAKES = ["passive", "design", "critical"] as const;
export type JevStakes = (typeof JEV_STAKES)[number];

export type JevQuestion = {
  readonly type: JevQuestionType;
  readonly instructions: JevJsonValue;
  readonly criteria?: JevJsonValue;
  /** Caller-side only. `maskJevQuestions` drops it, so it never reaches the model. */
  readonly stakes?: JevStakes;
};

export type JevQuestions = { readonly [id: string]: JevQuestion };

// Values a judge input carries that a public report does not. Each one is replaced whole.
const JEV_ONLY_PATTERNS: readonly RegExp[] = [
  // An e-mail address identifies a person, so it never reaches the judge.
  /\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b/giu,
  // A connection string, with or without credentials in it: the host and the database are enough
  // to locate the store, and the shared list only catches the form that carries a password.
  /\b(?:postgres(?:ql)?|redis|rediss|mysql|mongodb(?:\+srv)?|amqp|amqps|nats|grpc|grpcs):\/\/\S+/giu,
  // Common API-key shapes beyond the shared sanitizer.
  /\b(?:[a-z][a-z0-9]{1,20}_(?:ak|bk)_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|tskey-[A-Za-z0-9-]{10,})\b/gu,
];

/**
 * The masked form of one string. Control characters other than a newline become a space, runs of
 * spaces and tabs collapse to one space, and newlines survive.
 */
export function maskJevText(input: string): string {
  const redacted = JEV_ONLY_PATTERNS.reduce(
    (text, pattern) => text.replace(pattern, JEV_REDACTION),
    input.normalize("NFKC"),
  );
  // A code-point map, not a regex: the repository's own sanitizer does the same, and Biome rejects a
  // control character written inside a pattern. A newline survives; every other control byte does not.
  const printable = redactProhibitedValues(redacted)
    .split("")
    .map((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      const isControl = codePoint < 32 || codePoint === 127;
      return isControl && character !== "\n" ? " " : character;
    })
    .join("");
  const masked = printable.replace(/[^\S\n]+/gu, " ").trim();
  // A value the shared list still recognises means one pattern matched a shape the other left
  // behind. Fail closed on the whole string rather than ship the part that survived.
  if (prohibitedPublicValueLabel(masked) !== undefined) return JEV_REDACTION;
  return masked;
}

/** The masked form of a state: every string is masked, every number and boolean is kept. */
export function maskJevState(state: JevJsonValue): JevJsonValue {
  if (state === null) return null;
  if (typeof state === "string") return maskJevText(state);
  if (typeof state === "number" || typeof state === "boolean") return state;
  if (Array.isArray(state)) return state.map((entry) => maskJevState(entry));
  return Object.fromEntries(
    Object.entries(state).map(([key, entry]) => [
      key,
      CREDENTIAL_FIELD_NAME.test(key) ? JEV_REDACTION : maskJevState(entry),
    ]),
  );
}

/** The masked form of a question map. The closed `type` vocabulary is kept as written. */
export function maskJevQuestions(questions: JevQuestions): JevQuestions {
  return Object.fromEntries(
    Object.entries(questions).map(([id, question]) => [
      id,
      {
        type: question.type,
        instructions: maskJevState(question.instructions),
        ...(question.criteria === undefined ? {} : { criteria: maskJevState(question.criteria) }),
      },
    ]),
  );
}

/** The sha256 of masked text, for a decision log line that identifies a state without holding it. */
export function maskedDigest(masked: string): string {
  return createHash("sha256").update(masked, "utf8").digest("hex");
}
