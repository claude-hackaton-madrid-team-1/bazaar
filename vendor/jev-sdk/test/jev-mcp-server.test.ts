import { describe, expect, test } from "bun:test";
import { join } from "node:path";

import {
  JEV_MCP_PROTOCOL_VERSION,
  JEV_MCP_SERVER_NAME,
  JEV_MCP_TOOL,
  jevMcpResponse,
} from "../src/jev-mcp-server";

const repositoryRoot = join(import.meta.dir, "..");
const server = join(repositoryRoot, "src", "jev-mcp-server.ts");

async function exchange(requests: readonly unknown[]): Promise<Record<string, unknown>[]> {
  const child = Bun.spawn(["bun", server], {
    cwd: repositoryRoot,
    env: { ...Bun.env, TYPESAFE_API_KEY: "" },
    stdin: new TextEncoder().encode(`${requests.map((one) => JSON.stringify(one)).join("\n")}\n`),
    stdout: "pipe",
    stderr: "pipe",
  });
  const stdout = await new Response(child.stdout).text();
  await child.exited;
  return stdout
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

describe("the one tool the server exposes", () => {
  test("is jev_judge, and its schema names the module's own input", () => {
    expect(JEV_MCP_TOOL.name).toBe("jev_judge");
    expect(Object.keys(JEV_MCP_TOOL.inputSchema.properties)).toEqual([
      "state",
      "questions",
      "thresholds",
      "timeoutMs",
    ]);
    expect(JEV_MCP_TOOL.inputSchema.required).toEqual(["state", "questions"]);
  });
});

describe("the JSON-RPC surface, with no client library", () => {
  test("answers initialize with the protocol version and the tool capability", async () => {
    const [response] = await exchange([
      { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" } },
    ]);
    expect(response?.jsonrpc).toBe("2.0");
    expect(response?.id).toBe(1);
    const result = response?.result as Record<string, unknown>;
    expect(result.protocolVersion).toBe(JEV_MCP_PROTOCOL_VERSION);
    expect((result.serverInfo as { name: string }).name).toBe(JEV_MCP_SERVER_NAME);
    expect(result.capabilities).toEqual({ tools: { listChanged: false } });
  });

  test("lists exactly one tool", async () => {
    const [, listed] = await exchange([
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} },
    ]);
    const tools = (listed?.result as { tools: { name: string }[] }).tools;
    expect(tools.map((tool) => tool.name)).toEqual(["jev_judge"]);
  });

  test("answers nothing to a notification", async () => {
    const responses = await exchange([
      { jsonrpc: "2.0", method: "notifications/initialized" },
      { jsonrpc: "2.0", id: 7, method: "ping" },
    ]);
    expect(responses).toHaveLength(1);
    expect(responses[0]?.id).toBe(7);
  });

  test("refuses an unknown method with the JSON-RPC code", async () => {
    const [response] = await exchange([{ jsonrpc: "2.0", id: 3, method: "resources/list" }]);
    expect((response?.error as { code: number }).code).toBe(-32601);
  });

  test("refuses a line that is not JSON with the parse error code", async () => {
    const child = Bun.spawn(["bun", server], {
      cwd: repositoryRoot,
      env: { ...Bun.env, TYPESAFE_API_KEY: "" },
      stdin: new TextEncoder().encode("not json\n"),
      stdout: "pipe",
      stderr: "pipe",
    });
    const stdout = await new Response(child.stdout).text();
    await child.exited;
    expect((JSON.parse(stdout.trim()).error as { code: number }).code).toBe(-32700);
  });

  test("a tool call returns the module's result, masked, and never a secret", async () => {
    const [, called] = await exchange([
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: {
          name: "jev_judge",
          arguments: {
            state: "worker Bearer fixture-token-value-0001 refused",
            questions: { needs_owner: { type: "noul", instructions: "Does the owner act?" } },
          },
        },
      },
    ]);
    const result = called?.result as {
      isError: boolean;
      structuredContent: { verdicts: Record<string, { verdict: string; reason: string }> };
      content: { type: string; text: string }[];
    };
    expect(result.isError).toBe(false);
    expect(result.structuredContent.verdicts.needs_owner.verdict).toBe("undecided");
    expect(result.structuredContent.verdicts.needs_owner.reason).toBe("typesafe_api_key_missing");
    expect(result.content[0]?.type).toBe("text");
    expect(JSON.stringify(result)).not.toContain("fixture-token-value-0001");
  });

  test("a malformed tool call is a tool error, not a transport error", async () => {
    const [, called] = await exchange([
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "jev_judge", arguments: { state: "x", questions: {} } },
      },
    ]);
    const result = called?.result as { isError: boolean; content: { text: string }[] };
    expect(result.isError).toBe(true);
    expect(result.content[0]?.text).toContain("question");
  });

  test("an unknown tool name is an error", async () => {
    const [, called] = await exchange([
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "jev_write", arguments: {} } },
    ]);
    expect((called?.error as { code: number }).code).toBe(-32602);
  });
});

describe("the response builder", () => {
  test("carries the id it was given", () => {
    expect(jevMcpResponse(9, { ok: true })).toEqual({
      jsonrpc: "2.0",
      id: 9,
      result: { ok: true },
    });
  });
});
