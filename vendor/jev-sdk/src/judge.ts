// Typed Jev judgments for consumer-owned operational decisions. A verdict never authorizes action.
import {
  JEV_QUESTION_TYPES,
  JEV_STAKES,
  type JevJsonValue,
  type JevQuestion,
  type JevQuestions,
  type JevQuestionType,
  type JevStakes,
  maskJevQuestions,
  maskJevState,
} from "./mask";

export {
  JEV_QUESTION_TYPES,
  JEV_STAKES,
  type JevJsonValue,
  type JevQuestion,
  type JevQuestions,
  type JevQuestionType,
  type JevStakes,
} from "./mask";

export const JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone";
export const JEV_MODEL = "jev-latest";
export const JEV_API_KEY_VARIABLE = "TYPESAFE_API_KEY";
export const JEV_DEFAULT_THRESHOLD = 0.8;
/**
 * The bar per stakes level. A floor at 0.6 catches genuine uncertainty, a recoverable routing move
 * acts just above it, and a decision that costs a round of work or names a money path waits for 0.9.
 * One flat bar for every question sent low-stakes routing calls to a human at 0.64 and 0.79.
 */
export const JEV_STAKES_THRESHOLDS: Readonly<Record<JevStakes, number>> = {
  passive: 0.6,
  design: 0.75,
  critical: 0.9,
};
export const JEV_DEFAULT_TIMEOUT_MS = 10_000;
export const JEV_MAX_TIMEOUT_MS = 120_000;
/** The documented budget is 32k tokens for the state plus the longest question. */
export const JEV_MAX_STATE_CHARACTERS = 96_000;
export const JEV_MAX_RATE_LIMIT_RETRIES = 2;
/** The least time worth spending on another attempt once a rate limit wait has been paid for. */
export const JEV_MIN_ATTEMPT_MS = 1_000;
export const JEV_RATE_LIMIT_FALLBACK_MS = 1_000;
export const JEV_UNDECIDED = "undecided";

/** Every reason a verdict can be `undecided`. Nothing outside this list closes a decision. */
export const JEV_UNDECIDED_REASONS = [
  "typesafe_api_key_missing",
  "request_timeout",
  "network_error",
  "http_client_error",
  "http_server_error",
  "rate_limited",
  "response_schema_mismatch",
  "answer_missing",
  "answer_type_mismatch",
  "below_threshold",
] as const;
export type JevUndecidedReason = (typeof JEV_UNDECIDED_REASONS)[number];

/** What the number in a verdict means: a noul carries a probability, the others a confidence. */
export type JevValueKind = "noul-probability" | "confidence";

export type JevVerdict = {
  readonly type: JevQuestionType;
  /** `yes`, `no`, the chosen option, the score, or `undecided`. */
  readonly verdict: string;
  readonly value: number;
  readonly valueKind: JevValueKind;
  readonly threshold: number;
  readonly reason?: JevUndecidedReason;
  /**
   * The answer the model gave, whatever the bar did with it. A decided verdict repeats it; an
   * `undecided` one is the only place the leaning survives. Absent when nothing was answered.
   */
  readonly leaning?: string;
  /**
   * The distribution as the service returned it: every option for a `choice`, every level for a
   * `score`. A `noul` has none, because its one probability is already `value`.
   */
  readonly probabilities?: Readonly<Record<string, number>>;
  /**
   * Top minus second. Confidence measures how concentrated the whole distribution is, so five
   * options with a clear leader can still read 0.5; the margin says how far ahead that leader is.
   * For a `noul` it is the distance from the coin flip.
   */
  readonly margin?: number;
};

export type JevJudgeRequest = {
  readonly state: JevJsonValue;
  readonly questions: JevQuestions;
  readonly thresholds?: Readonly<Record<string, number>>;
  readonly timeoutMs?: number;
};

export type JevJudgeResult = {
  /** The model version the service reported, or "" when no answer arrived. */
  readonly model: string;
  readonly latencyMs: number;
  readonly verdicts: Readonly<Record<string, JevVerdict>>;
};

export type JevDependencies = {
  readonly apiKey: string;
  readonly fetch: (url: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  readonly now: () => number;
  readonly sleep: (milliseconds: number) => Promise<void>;
};

/** A caller mistake. It is never a verdict: a bad request must be fixed, not judged. */
export class JevUsageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JevUsageError";
  }
}

/** A request with every string masked, as it will leave the machine. */
export type JevMaskedRequest = {
  readonly state: JevJsonValue;
  readonly questions: JevQuestions;
  readonly thresholds: Readonly<Record<string, number>>;
  readonly timeoutMs: number;
};

/** What the request ends as once the rate limit retries are spent. */
type SendOutcome =
  | { readonly kind: "answered"; readonly model: string; readonly answers: JsonObject }
  | { readonly kind: "failed"; readonly reason: JevUndecidedReason };

type Attempt = SendOutcome | { readonly kind: "rate-limited"; readonly retryAfterMs: number };

type JsonObject = { readonly [key: string]: unknown };

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFraction(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

/** Reads the key from the process environment only. No .env file, and the value is never logged. */
export function jevDependenciesFrom(
  environment: Readonly<Record<string, string | undefined>>,
): JevDependencies {
  return {
    apiKey: environment[JEV_API_KEY_VARIABLE]?.trim() ?? "",
    fetch,
    now: () => Date.now(),
    sleep: (milliseconds) =>
      new Promise((resolve) => {
        setTimeout(resolve, milliseconds);
      }),
  };
}

function thresholdFloorOf(type: JevQuestionType): number {
  // A noul threshold at or below 0.5 would call the same probability both yes and no.
  return type === "noul" ? 0.5 : 0;
}

function validatedCriteria(id: string, question: JevQuestion): void {
  const { type, criteria } = question;
  switch (type) {
    case "noul":
      return;
    case "choice": {
      const options = isJsonObject(criteria) ? Object.keys(criteria) : [];
      if (options.length < 2) {
        throw new JevUsageError(`question ${id}: a choice needs at least two criteria options`);
      }
      return;
    }
    case "score": {
      const levels = Array.isArray(criteria) ? criteria : [];
      if (levels.length < 2 || levels.length > 10) {
        throw new JevUsageError(
          `question ${id}: a score needs between two and ten criteria levels`,
        );
      }
      return;
    }
    default:
      throw new JevUsageError(`question ${id}: unknown question type`);
  }
}

function validatedQuestion(id: string, question: JevQuestion): void {
  if (!isJsonObject(question)) throw new JevUsageError(`question ${id} is not an object`);
  if (!JEV_QUESTION_TYPES.includes(question.type)) {
    throw new JevUsageError(`question ${id}: type must be one of ${JEV_QUESTION_TYPES.join(", ")}`);
  }
  if (question.stakes !== undefined && !JEV_STAKES.includes(question.stakes)) {
    throw new JevUsageError(`question ${id}: stakes must be one of ${JEV_STAKES.join(", ")}`);
  }
  const written = JSON.stringify(question.instructions ?? "");
  if (written.length < 3) throw new JevUsageError(`question ${id}: instructions are empty`);
  validatedCriteria(id, question);
}

/**
 * The bar one question has to clear. An explicit threshold wins, then the stakes the question
 * declares, then the flat default. A caller that names neither is judged exactly as before.
 */
export function jevThresholdFor(question: JevQuestion, override?: number): number {
  if (override !== undefined) return override;
  if (question.stakes !== undefined) return JEV_STAKES_THRESHOLDS[question.stakes];
  return JEV_DEFAULT_THRESHOLD;
}

function validatedThreshold(id: string, question: JevQuestion, value: number | undefined): number {
  const threshold = jevThresholdFor(question, value);
  const floor = thresholdFloorOf(question.type);
  if (!Number.isFinite(threshold) || threshold > 1 || threshold <= floor) {
    throw new JevUsageError(`question ${id}: threshold must be above ${floor} and at most 1`);
  }
  return threshold;
}

export function maskedJevRequest(request: JevJudgeRequest): JevMaskedRequest {
  const ids = Object.keys(request.questions ?? {});
  if (ids.length === 0) throw new JevUsageError("at least one question is required");
  for (const id of ids) validatedQuestion(id, request.questions[id] as JevQuestion);

  const timeoutMs = request.timeoutMs ?? JEV_DEFAULT_TIMEOUT_MS;
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > JEV_MAX_TIMEOUT_MS) {
    throw new JevUsageError(`timeoutMs must be between 1 and ${JEV_MAX_TIMEOUT_MS}`);
  }

  const state = maskJevState(request.state);
  const written = typeof state === "string" ? state : JSON.stringify(state);
  if (written.trim().length === 0) throw new JevUsageError("state is empty");
  if (written.length > JEV_MAX_STATE_CHARACTERS) {
    throw new JevUsageError(`state is larger than ${JEV_MAX_STATE_CHARACTERS} characters`);
  }

  const thresholds = Object.fromEntries(
    ids.map((id) => [
      id,
      validatedThreshold(id, request.questions[id] as JevQuestion, request.thresholds?.[id]),
    ]),
  );
  return { state, questions: maskJevQuestions(request.questions), thresholds, timeoutMs };
}

function transportReason(error: unknown): JevUndecidedReason {
  const name = error instanceof Error ? error.name : "";
  return name === "AbortError" || name === "TimeoutError" ? "request_timeout" : "network_error";
}

function retryAfterMs(response: Response): number {
  const header = Number(response.headers.get("retry-after"));
  if (!Number.isFinite(header) || header <= 0) return JEV_RATE_LIMIT_FALLBACK_MS;
  return Math.min(Math.round(header * 1_000), JEV_MAX_TIMEOUT_MS);
}

async function parsedBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function statusFailure(status: number): JevUndecidedReason | undefined {
  if (status >= 500) return "http_server_error";
  if (status >= 400) return "http_client_error";
  if (status >= 300 || status < 200) return "response_schema_mismatch";
  return undefined;
}

async function attempt(
  body: string,
  timeoutMs: number,
  dependencies: JevDependencies,
): Promise<Attempt> {
  let response: Response;
  try {
    response = await dependencies.fetch(JEV_ENDPOINT, {
      method: "POST",
      headers: {
        authorization: `Bearer ${dependencies.apiKey}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    return { kind: "failed", reason: transportReason(error) };
  }

  if (response.status === 429) {
    return { kind: "rate-limited", retryAfterMs: retryAfterMs(response) };
  }
  const failure = statusFailure(response.status);
  if (failure !== undefined) return { kind: "failed", reason: failure };

  const payload = await parsedBody(response);
  if (!isJsonObject(payload) || !isJsonObject(payload.answers)) {
    return { kind: "failed", reason: "response_schema_mismatch" };
  }
  const model = typeof payload.model === "string" ? payload.model : "";
  return { kind: "answered", model, answers: payload.answers };
}

/**
 * `timeoutMs` is the budget for the WHOLE call: `deadline` bounds every attempt and every wait
 * together. A vendor `retry-after` of 60 s used to buy three attempts plus four minutes of sleep on
 * an 8 s budget, which froze the caller to reach the same `rate_limited` it can return at once.
 */
async function send(
  request: JevMaskedRequest,
  dependencies: JevDependencies,
  deadline: number,
): Promise<SendOutcome> {
  const body = JSON.stringify({
    state: request.state,
    model: JEV_MODEL,
    questions: request.questions,
  });
  // Only a rate limit is retried, and only twice. Nothing else is retried at all.
  for (let round = 0; round <= JEV_MAX_RATE_LIMIT_RETRIES; round += 1) {
    const remaining = Math.max(deadline - dependencies.now(), 1);
    const outcome = await attempt(body, remaining, dependencies);
    if (outcome.kind === "answered") return outcome;
    if (outcome.kind === "failed") return outcome;
    if (round === JEV_MAX_RATE_LIMIT_RETRIES) break;
    // Wait only when the wait AND a useful attempt after it both still fit. A shortened wait would
    // only earn another 429, so the alternative to waiting in full is answering now.
    const afterWaiting = deadline - dependencies.now() - outcome.retryAfterMs;
    if (afterWaiting < JEV_MIN_ATTEMPT_MS) break;
    await dependencies.sleep(outcome.retryAfterMs);
  }
  return { kind: "failed", reason: "rate_limited" };
}

function valueKindOf(type: JevQuestionType): JevValueKind {
  return type === "noul" ? "noul-probability" : "confidence";
}

/** What the answer said beyond its one number. Every key is left out when it is not known. */
type JevAnswerDetail = {
  readonly leaning?: string;
  readonly probabilities?: Readonly<Record<string, number>>;
  readonly margin?: number;
};

/** Binary floats: 0.6 - 0.3 is 0.30000000000000004, and a margin is read by a human. */
function roundedFraction(value: number): number {
  return Math.min(Math.max(Math.round(value * 1_000_000) / 1_000_000, 0), 1);
}

/** The distribution the service sent, or nothing when it sent none this call can trust. */
function distributionOf(answer: JsonObject): Readonly<Record<string, number>> | undefined {
  const raw = answer.probabilities;
  if (!isJsonObject(raw)) return undefined;
  const entries = Object.entries(raw);
  if (entries.length === 0) return undefined;
  if (!entries.every(([, value]) => isFraction(value))) return undefined;
  return Object.fromEntries(entries) as Record<string, number>;
}

/** Top minus second. One entry holds the whole distribution, so its margin is itself. */
function marginOf(distribution: Readonly<Record<string, number>>): number {
  const ordered = Object.values(distribution).sort((left, right) => right - left);
  return roundedFraction((ordered[0] ?? 0) - (ordered[1] ?? 0));
}

function detailFor(
  leaning: string,
  distribution: Readonly<Record<string, number>> | undefined,
): JevAnswerDetail {
  if (distribution === undefined) return { leaning };
  return { leaning, probabilities: distribution, margin: marginOf(distribution) };
}

/** A noul carries one probability, so its distribution is that number and its mirror. */
function noulDetail(probability: number): JevAnswerDetail {
  return {
    leaning: probability >= 0.5 ? "yes" : "no",
    margin: roundedFraction(Math.abs(probability * 2 - 1)),
  };
}

function undecided(
  type: JevQuestionType,
  threshold: number,
  reason: JevUndecidedReason,
  value = 0,
  detail: JevAnswerDetail = {},
): JevVerdict {
  return {
    type,
    verdict: JEV_UNDECIDED,
    value,
    valueKind: valueKindOf(type),
    threshold,
    reason,
    ...detail,
  };
}

function decided(
  type: JevQuestionType,
  verdict: string,
  value: number,
  threshold: number,
  detail: JevAnswerDetail = {},
): JevVerdict {
  return { type, verdict, value, valueKind: valueKindOf(type), threshold, ...detail };
}

function noulVerdict(answer: JsonObject, threshold: number): JevVerdict {
  const probability = answer.noul;
  if (!isFraction(probability)) return undecided("noul", threshold, "response_schema_mismatch");
  const detail = noulDetail(probability);
  if (probability >= threshold) return decided("noul", "yes", probability, threshold, detail);
  // The mirror of the yes test on the probability of no. Writing it as `probability <= 1 -
  // threshold` calls 0.2 undecided at a 0.8 threshold, because 1 - 0.8 is 0.19999999999999996.
  if (1 - probability >= threshold) return decided("noul", "no", probability, threshold, detail);
  return undecided("noul", threshold, "below_threshold", probability, detail);
}

function choiceVerdict(answer: JsonObject, question: JevQuestion, threshold: number): JevVerdict {
  const options = isJsonObject(question.criteria) ? Object.keys(question.criteria) : [];
  const chosen = answer.choice;
  const confidence = answer.confidence;
  if (typeof chosen !== "string" || !options.includes(chosen) || !isFraction(confidence)) {
    return undecided("choice", threshold, "response_schema_mismatch");
  }
  const detail = detailFor(chosen, distributionOf(answer));
  if (confidence < threshold) {
    return undecided("choice", threshold, "below_threshold", confidence, detail);
  }
  return decided("choice", chosen, confidence, threshold, detail);
}

function scoreVerdict(answer: JsonObject, threshold: number): JevVerdict {
  const score = answer.score;
  const confidence = answer.confidence;
  if (typeof score !== "number" || !Number.isFinite(score) || !isFraction(confidence)) {
    return undecided("score", threshold, "response_schema_mismatch");
  }
  const detail = detailFor(String(score), distributionOf(answer));
  if (confidence < threshold) {
    return undecided("score", threshold, "below_threshold", confidence, detail);
  }
  return decided("score", String(score), confidence, threshold, detail);
}

function verdictFor(question: JevQuestion, threshold: number, answer: unknown): JevVerdict {
  const { type } = question;
  if (!isJsonObject(answer)) return undecided(type, threshold, "answer_missing");
  if (answer.type !== type) return undecided(type, threshold, "answer_type_mismatch");
  switch (type) {
    case "noul":
      return noulVerdict(answer, threshold);
    case "choice":
      return choiceVerdict(answer, question, threshold);
    case "score":
      return scoreVerdict(answer, threshold);
    default:
      return undecided(type, threshold, "response_schema_mismatch");
  }
}

function allUndecided(
  request: JevMaskedRequest,
  reason: JevUndecidedReason,
): Record<string, JevVerdict> {
  return Object.fromEntries(
    Object.entries(request.questions).map(([id, question]) => [
      id,
      undecided(question.type, request.thresholds[id] ?? JEV_DEFAULT_THRESHOLD, reason),
    ]),
  );
}

/**
 * Asks every question about one state in one request and returns one verdict per question id.
 * Throws `JevUsageError` for a malformed request; every other failure is `undecided`.
 */
export async function judge(
  request: JevJudgeRequest,
  dependencies: JevDependencies,
): Promise<JevJudgeResult> {
  const validated = maskedJevRequest(request);
  if (dependencies.apiKey.length === 0) {
    return {
      model: "",
      latencyMs: 0,
      verdicts: allUndecided(validated, "typesafe_api_key_missing"),
    };
  }

  const started = dependencies.now();
  const outcome = await send(validated, dependencies, started + validated.timeoutMs);
  const latencyMs = dependencies.now() - started;
  if (outcome.kind === "failed") {
    return { model: "", latencyMs, verdicts: allUndecided(validated, outcome.reason) };
  }
  return {
    model: outcome.model,
    latencyMs,
    verdicts: Object.fromEntries(
      Object.entries(validated.questions).map(([id, question]) => [
        id,
        verdictFor(
          question,
          validated.thresholds[id] ?? JEV_DEFAULT_THRESHOLD,
          outcome.answers[id],
        ),
      ]),
    ),
  };
}
