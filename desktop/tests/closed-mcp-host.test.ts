import assert from "node:assert/strict";
import test from "node:test";

import { ClosedMcpHost } from "../src/runtime/closed-mcp-host.ts";
import type { WorkspaceFiles } from "../src/runtime/workspace-files.ts";

type Boundary = Pick<WorkspaceFiles, "captureAuthorization" | "list" | "read">;

function boundary(overrides: Partial<Boundary> = {}): Boundary {
  return {
    captureAuthorization: () => ({
      ok: true as const,
      value: {
        workspaceId: "workspace-a",
        rootName: "repo",
        authorizationGeneration: 7,
      },
    }),
    list: async (input) => ({
      ok: true as const,
      value: {
        directoryPath: input.directoryPath,
        entries: [
          {
            kind: "file" as const,
            name: "README.md",
            path: "README.md",
            sizeBytes: 12,
            lastModifiedMs: 1,
          },
        ],
        truncated: false,
      },
    }),
    read: async (input) => ({
      ok: true as const,
      value: {
        path: input.path,
        content: "alpha\nbeta alpha",
        sizeBytes: 16,
        lastModifiedMs: 1,
        sha256: "a".repeat(64),
      },
    }),
    ...overrides,
  };
}

function callRequest(
  name: string,
  args: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  return {
    id: `operation_${"1".repeat(32)}`,
    jsonrpc: "2.0",
    method: "tools/call",
    params: { arguments: args, name },
  };
}

test("closed MCP host returns an exact host-native JSON-RPC result", async () => {
  const host = new ClosedMcpHost(boundary());
  const result = await host.call({
    workspaceId: "workspace-a",
    allowedTools: new Set(["omnibase_text_search"]),
    request: callRequest("omnibase_text_search", {
      path: "README.md",
      query: "alpha",
    }),
    signal: new AbortController().signal,
  });
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.value, {
    id: `operation_${"1".repeat(32)}`,
    jsonrpc: "2.0",
    result: {
      output: {
        matches: [
          { line: 1, snippet: "alpha" },
          { line: 2, snippet: "beta alpha" },
        ],
        path: "README.md",
        truncated: false,
      },
      server_id: "workspace-files-readonly",
      tool: "omnibase_text_search",
      transport: "host_native",
    },
  });
});

test("closed MCP host rejects an undeclared tool before file authority", async () => {
  let captured = false;
  const host = new ClosedMcpHost(
    boundary({
      captureAuthorization: () => {
        captured = true;
        return {
          ok: false as const,
          error: { code: "unexpected_capture" },
        };
      },
    }),
  );
  const result = await host.call({
    workspaceId: "workspace-a",
    allowedTools: new Set(["omnibase_files_read"]),
    request: callRequest("omnibase_files_hash", { path: "README.md" }),
    signal: new AbortController().signal,
  });
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_mcp_request_invalid" },
  });
  assert.equal(captured, false);
});

test("closed MCP host rejects extra JSON-RPC fields and malformed arguments", async () => {
  const host = new ClosedMcpHost(boundary());
  const extraField = await host.call({
    workspaceId: "workspace-a",
    allowedTools: new Set(["omnibase_files_read"]),
    request: {
      ...callRequest("omnibase_files_read", { path: "README.md" }),
      token: "x",
    },
    signal: new AbortController().signal,
  });
  assert.equal(extraField.ok, false);
  const extraArgument = await host.call({
    workspaceId: "workspace-a",
    allowedTools: new Set(["omnibase_files_read"]),
    request: callRequest("omnibase_files_read", {
      path: "README.md",
      command: "whoami",
    }),
    signal: new AbortController().signal,
  });
  assert.deepEqual(extraArgument, {
    ok: false,
    error: { code: "desktop_component_mcp_arguments_invalid" },
  });
});

test("closed MCP host preserves Workspace authorization failure", async () => {
  const host = new ClosedMcpHost(
    boundary({
      captureAuthorization: () => ({
        ok: false as const,
        error: { code: "desktop_workspace_files_not_authorized" },
      }),
    }),
  );
  const result = await host.call({
    workspaceId: "workspace-b",
    allowedTools: new Set(["omnibase_files_list"]),
    request: callRequest("omnibase_files_list", { directory: "" }),
    signal: new AbortController().signal,
  });
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_workspace_files_not_authorized" },
  });
});
