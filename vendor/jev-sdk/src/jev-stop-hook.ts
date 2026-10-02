#!/usr/bin/env -S bun --no-env-file
// A Claude Code Stop hook that asks Jev ONE question: can the assistant still make real progress on
// the goal without the owner? It is NOT installed by this repository; `README.md` holds
// the lines the owner adds to install it and the line to remove.
//
// Exit 0 lets the assistant stop. Exit 2 blocks the stop and hands the reason back to the assistant,
// which is the harness contract for a Stop hook. `no` and `undecided` both let the assistant stop,
// so a judge that is unsure, refused, timed out or has no key never traps a session.
import { readFileSync } from "node:fs";

import {
  type JevJudgeResult,
  type JevQuestion,
  type JevVerdict,
  jevDependenciesFrom,
  judge,
} from "./judge";
import { readAllText } from "./stdin";

export const JEV_STOP_QUESTION_ID = "can_make_progress";

export const JEV_STOP_QUESTION: JevQuestion = {
  type: "noul",
  instructions: {
    question:
      "Reading `goal` and `last_assistant_message`, can the assistant make real progress on the goal right now, by itself, without an answer or an action from the owner?",
    note: "Real progress means a concrete next step in the work: reading, writing, running a command, or checking a result. Asking the owner something, waiting for a person, waiting for a deploy, or reporting that the work is finished is not progress.",
  },
  criteria: {
    true: "A concrete next step is available to the assistant alone, and the goal is not finished.",
    false:
      "The goal is finished, or the next step needs the owner: an answer, a permission, a credential, a decision, or an action only a person can take.",
  },
};

/** Every reason the hook answers without asking the judge. Each one lets the assistant stop. */
export const JEV_STOP_REFUSALS = ["stop_hook_active", "goal_missing", "input_not_json"] as const;
export type JevStopRefusal = (typeof JEV_STOP_REFUSALS)[number];

export type JevStopInput = {
  readonly goal: string;
  readonly lastAssistantMessage: string;
  readonly stopHookActive: boolean;
};

type TranscriptEntry = { type?: unknown; message?: { content?: unknown } };

function textOfContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      typeof part === "object" &&
      part !== null &&
      typeof (part as { text?: unknown }).text === "string"
        ? String((part as { text: string }).text)
        : "",
    )
    .filter((text) => text.length > 0)
    .join("\n");
}

/** The last assistant text in a Claude Code transcript, or "" when there is none to read. */
export function lastAssistantMessageIn(path: string): string {
  let contents: string;
  try {
    contents = readFileSync(path, "utf8");
  } catch {
    return "";
  }
  const messages = contents
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .flatMap((line): string[] => {
      try {
        const entry = JSON.parse(line) as TranscriptEntry;
        if (entry.type !== "assistant") return [];
        return [textOfContent(entry.message?.content)];
      } catch {
        return [];
      }
    })
    .filter((text) => text.length > 0);
  return messages[messages.length - 1] ?? "";
}

export function jevStopInputFrom(payload: unknown): JevStopInput {
  const record = (typeof payload === "object" && payload !== null ? payload : {}) as {
    goal?: unknown;
    last_assistant_message?: unknown;
    transcript_path?: unknown;
    stop_hook_active?: unknown;
  };
  const fromPayload =
    typeof record.last_assistant_message === "string" ? record.last_assistant_message : "";
  const fromTranscript =
    typeof record.transcript_path === "string"
      ? lastAssistantMessageIn(record.transcript_path)
      : "";
  return {
    goal: typeof record.goal === "string" ? record.goal : "",
    lastAssistantMessage: fromPayload.length > 0 ? fromPayload : fromTranscript,
    stopHookActive: record.stop_hook_active === true,
  };
}

/** Only a `yes` blocks the stop. Everything else, uncertainty included, lets the assistant stop. */
export function jevStopExit(verdict: JevVerdict): {
  readonly code: number;
  readonly message: string;
} {
  switch (verdict.verdict) {
    case "yes":
      return {
        code: 2,
        message: `Jev judged that real progress is still possible without the owner (probability ${verdict.value}). Continue the work instead of stopping.`,
      };
    default:
      return { code: 0, message: "" };
  }
}

function refuse(refusal: JevStopRefusal): number {
  process.stdout.write(`${JSON.stringify({ refusal })}\n`);
  return 0;
}

function parsedPayload(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

async function main(): Promise<number> {
  const payload = parsedPayload(await readAllText(process.stdin));
  if (payload === undefined) return refuse("input_not_json");

  const input = jevStopInputFrom(payload);
  // A hook that already blocked once must never block again, or the session cannot end.
  if (input.stopHookActive) return refuse("stop_hook_active");
  if (input.goal.trim().length === 0) return refuse("goal_missing");

  const result: JevJudgeResult = await judge(
    {
      state: { goal: input.goal, last_assistant_message: input.lastAssistantMessage },
      questions: { [JEV_STOP_QUESTION_ID]: JEV_STOP_QUESTION },
      thresholds: { [JEV_STOP_QUESTION_ID]: 0.8 },
      timeoutMs: 8_000,
    },
    jevDependenciesFrom(process.env),
  );

  const verdict = result.verdicts[JEV_STOP_QUESTION_ID];
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (verdict === undefined) return 0;
  const exit = jevStopExit(verdict);
  if (exit.message.length > 0) process.stderr.write(`${exit.message}\n`);
  return exit.code;
}

if (import.meta.main) {
  main()
    .then((code) => {
      process.exit(code);
    })
    .catch(() => {
      // A hook that crashes must not trap the session either.
      process.stdout.write(`${JSON.stringify({ refusal: "input_not_json" })}\n`);
      process.exit(0);
    });
}
