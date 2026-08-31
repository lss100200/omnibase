import type { DesktopWorkspaceComponentJsonValue } from "../shared/workspace-components.ts";
import type { WorkspaceFiles } from "./workspace-files.ts";

const SERVER_ID = "workspace-files-readonly";
const TRANSPORT = "host_native";
const SUPPORTED_TOOLS = new Set([
  "omnibase_files_list",
  "omnibase_files_read",
  "omnibase_files_hash",
  "omnibase_text_search",
]);

type WorkspaceFileBoundary = Pick<
  WorkspaceFiles,
  "captureAuthorization" | "list" | "read"
>;

export interface ClosedMcpHostCallInput {
  readonly workspaceId: string;
  readonly allowedTools: ReadonlySet<string>;
  readonly request: unknown;
  readonly signal: AbortSignal;
}

export type ClosedMcpHostCallResult =
  | Readonly<{ ok: true; value: DesktopWorkspaceComponentJsonValue }>
  | Readonly<{ ok: false; error: Readonly<{ code: string }> }>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function failure(code: string): ClosedMcpHostCallResult {
  return Object.freeze({ ok: false as const, error: Object.freeze({ code }) });
}

function boundedText(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" &&
    value.length <= maximum &&
    !value.includes("\0")
  );
}

export class ClosedMcpHost {
  readonly #workspaceFiles: WorkspaceFileBoundary;

  constructor(workspaceFiles: WorkspaceFileBoundary) {
    this.#workspaceFiles = workspaceFiles;
  }

  async call(input: ClosedMcpHostCallInput): Promise<ClosedMcpHostCallResult> {
    if (input.signal.aborted) {
      return failure("desktop_component_invocation_cancelled");
    }
    const request = input.request;
    if (
      !isRecord(request) ||
      !exact(request, ["id", "jsonrpc", "method", "params"]) ||
      request.jsonrpc !== "2.0" ||
      typeof request.id !== "string" ||
      request.id.length < 1 ||
      request.id.length > 128 ||
      request.method !== "tools/call" ||
      !isRecord(request.params) ||
      !exact(request.params, ["arguments", "name"]) ||
      typeof request.params.name !== "string" ||
      !SUPPORTED_TOOLS.has(request.params.name) ||
      !input.allowedTools.has(request.params.name) ||
      !isRecord(request.params.arguments)
    ) {
      return failure("desktop_component_mcp_request_invalid");
    }
    const name = request.params.name;
    const args = request.params.arguments;
    const authorization = this.#workspaceFiles.captureAuthorization(
      input.workspaceId,
    );
    if (!authorization.ok) return failure(authorization.error.code);
    const base = {
      workspaceId: input.workspaceId,
      authorizationGeneration: authorization.value.authorizationGeneration,
    };
    let result: DesktopWorkspaceComponentJsonValue;
    if (name === "omnibase_files_list") {
      if (!exact(args, ["directory"]) || !boundedText(args.directory, 4_096)) {
        return failure("desktop_component_mcp_arguments_invalid");
      }
      const listed = await this.#workspaceFiles.list({
        ...base,
        directoryPath: args.directory,
      });
      if (!listed.ok) return failure(listed.error.code);
      result = Object.freeze({
        directory_path: listed.value.directoryPath,
        entries: listed.value.entries.map((entry) =>
          Object.freeze({
            kind: entry.kind,
            name: entry.name,
            path: entry.path,
            size_bytes: entry.sizeBytes,
          }),
        ),
        truncated: listed.value.truncated,
      });
    } else if (
      name === "omnibase_files_read" ||
      name === "omnibase_files_hash"
    ) {
      if (
        !exact(args, ["path"]) ||
        !boundedText(args.path, 4_096) ||
        args.path.length < 1
      ) {
        return failure("desktop_component_mcp_arguments_invalid");
      }
      const read = await this.#workspaceFiles.read({
        ...base,
        path: args.path,
      });
      if (!read.ok) return failure(read.error.code);
      result = Object.freeze({
        ...(name === "omnibase_files_read"
          ? { content: read.value.content }
          : {}),
        path: read.value.path,
        sha256: read.value.sha256,
        size_bytes: read.value.sizeBytes,
      });
    } else {
      if (
        !exact(args, ["path", "query"]) ||
        !boundedText(args.path, 4_096) ||
        args.path.length < 1 ||
        !boundedText(args.query, 32_768) ||
        args.query.length < 1
      ) {
        return failure("desktop_component_mcp_arguments_invalid");
      }
      const read = await this.#workspaceFiles.read({
        ...base,
        path: args.path,
      });
      if (!read.ok) return failure(read.error.code);
      const matches: Array<Readonly<{ line: number; snippet: string }>> = [];
      for (const [index, line] of read.value.content
        .split(/\r?\n/u)
        .entries()) {
        if (line.includes(args.query)) {
          matches.push(
            Object.freeze({ line: index + 1, snippet: line.slice(0, 512) }),
          );
          if (matches.length >= 100) break;
        }
      }
      result = Object.freeze({
        matches: Object.freeze(matches),
        path: read.value.path,
        truncated: matches.length >= 100,
      });
    }
    if (input.signal.aborted) {
      return failure("desktop_component_invocation_cancelled");
    }
    return Object.freeze({
      ok: true as const,
      value: Object.freeze({
        id: request.id,
        jsonrpc: "2.0",
        result: Object.freeze({
          output: result,
          server_id: SERVER_ID,
          tool: name,
          transport: TRANSPORT,
        }),
      }),
    });
  }
}
