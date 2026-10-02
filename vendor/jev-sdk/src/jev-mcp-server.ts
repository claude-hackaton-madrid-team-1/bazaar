#!/usr/bin/env -S bun --no-env-file
// Dependency-free MCP stdio server exposing one jev_judge tool.
import { type JevJudgeRequest, JevUsageError, jevDependenciesFrom, judge } from "./judge";
import { forEachLine } from "./stdin";

export const JEV_MCP_PROTOCOL_VERSION = "2025-06-18";
export const JEV_MCP_SERVER_NAME = "jev-judge";
export const JEV_MCP_SERVER_VERSION = "0.1.0";

export const JEV_MCP_TOOL = {
  name: "jev_judge",
  title: "Ask Jev for typed verdicts",
  description:
    "Ask TypeSafe's Jev model a set of typed questions about operations state and get one closed verdict per question id: yes, no, a choice option, a score, or undecided. Operations tooling only: it decides nothing about money, risk, auth, settlement, withdrawals, orders, fills, marks or signatures. Every value is masked before the request leaves the machine, and every failure is undecided.",
  inputSchema: {
    type: "object",
    properties: {
      state: {
        description: "The state to judge: text, or JSON structure with named fields.",
      },
      questions: {
        type: "object",
        description:
          "A map of question id to { type: noul | choice | score, instructions, criteria }. A choice needs at least two criteria options; a score needs between two and ten criteria levels.",
        additionalProperties: { type: "object" },
      },
      thresholds: {
        type: "object",
        description:
          "Per question id, the certainty a verdict needs. Below it the verdict is undecided. Default 0.8; a noul threshold must be above 0.5.",
        additionalProperties: { type: "number" },
      },
      timeoutMs: { type: "integer", description: "How long one call may take. Default 10000." },
    },
    required: ["state", "questions"],
    additionalProperties: false,
  },
} as const;

type JsonRpcId = string | number | null;

type JsonRpcMessage = {
  readonly jsonrpc?: unknown;
  readonly id?: JsonRpcId;
  readonly method?: unknown;
  readonly params?: unknown;
};

export function jevMcpResponse(id: JsonRpcId, result: unknown) {
  return { jsonrpc: "2.0", id, result };
}

export function jevMcpError(id: JsonRpcId, code: number, message: string) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function toolResult(payload: unknown, isError: boolean) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    isError,
  };
}

async function callTool(params: unknown, id: JsonRpcId): Promise<unknown> {
  const call = (typeof params === "object" && params !== null ? params : {}) as {
    name?: unknown;
    arguments?: unknown;
  };
  if (call.name !== JEV_MCP_TOOL.name) {
    return jevMcpError(id, -32602, `unknown tool ${String(call.name)}`);
  }
  const request = (
    typeof call.arguments === "object" && call.arguments !== null ? call.arguments : {}
  ) as JevJudgeRequest;
  try {
    const result = await judge(request, jevDependenciesFrom(process.env));
    return jevMcpResponse(id, toolResult(result, false));
  } catch (error) {
    // A caller mistake is the tool's error, not a transport failure: the session stays usable.
    const message = error instanceof JevUsageError ? error.message : "the judge could not run";
    return jevMcpResponse(id, toolResult({ error: message }, true));
  }
}

async function handle(message: JsonRpcMessage): Promise<unknown> {
  const id = message.id ?? null;
  switch (message.method) {
    case "initialize":
      return jevMcpResponse(id, {
        protocolVersion: JEV_MCP_PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: JEV_MCP_SERVER_NAME, version: JEV_MCP_SERVER_VERSION },
      });
    case "ping":
      return jevMcpResponse(id, {});
    case "tools/list":
      return jevMcpResponse(id, { tools: [JEV_MCP_TOOL] });
    case "tools/call":
      return await callTool(message.params, id);
    default:
      return jevMcpError(id, -32601, `unknown method ${String(message.method)}`);
  }
}

function write(payload: unknown): void {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function answer(line: string): Promise<void> {
  let message: JsonRpcMessage;
  try {
    message = JSON.parse(line) as JsonRpcMessage;
  } catch {
    write(jevMcpError(null, -32700, "parse error"));
    return;
  }
  // A notification carries no id and takes no answer.
  const isNotification =
    (message.id === undefined || message.id === null) &&
    typeof message.method === "string" &&
    message.method.startsWith("notifications/");
  if (isNotification) return;
  write(await handle(message));
}

async function main(): Promise<void> {
  await forEachLine(process.stdin, answer);
}

if (import.meta.main) {
  main().catch(() => {
    process.exit(1);
  });
}
