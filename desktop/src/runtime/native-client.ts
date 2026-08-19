import type {
  DesktopOperationResult,
  DesktopOwner,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopWorkspace,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
} from "../shared/ipc-contract.ts";

const TOKEN_PATTERN = /^[a-f0-9]{64}$/u;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{2,95}$/u;
const OWNER_ID_PATTERN = /^owner_[a-f0-9]{32}$/u;
const WORKSPACE_ID_PATTERN = /^workspace_[a-f0-9]{32}$/u;
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_WORKSPACES = 256;

type FetchLike = typeof fetch;
type NativeMethod = "GET" | "POST";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === [...expected].sort()[index])
  );
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maximum
  );
}

function failure<T>(code: string): DesktopOperationResult<T> {
  return Object.freeze({
    ok: false,
    error: Object.freeze({ code }),
  });
}

function success<T>(value: T): DesktopOperationResult<T> {
  return Object.freeze({ ok: true, value });
}

function parseOwner(value: unknown): DesktopOwner | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created_at", "display_name", "id", "updated_at"]) ||
    typeof value.id !== "string" ||
    !OWNER_ID_PATTERN.test(value.id) ||
    !isBoundedString(value.display_name, 256) ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    displayName: value.display_name,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseOwnerStatus(value: unknown): DesktopOwnerStatus | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["initialized", "owner"]) ||
    typeof value.initialized !== "boolean"
  ) {
    return null;
  }
  const owner = value.owner === null ? null : parseOwner(value.owner);
  if (
    (value.initialized && owner === null) ||
    (!value.initialized && value.owner !== null)
  ) {
    return null;
  }
  return Object.freeze({ initialized: value.initialized, owner });
}

function parseOwnerBootstrap(
  value: unknown,
): DesktopOwnerBootstrapResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created", "initialized", "owner"]) ||
    value.initialized !== true ||
    typeof value.created !== "boolean"
  ) {
    return null;
  }
  const owner = parseOwner(value.owner);
  if (owner === null) return null;
  return Object.freeze({
    initialized: true,
    created: value.created,
    owner,
  });
}

function parseWorkspace(value: unknown): DesktopWorkspace | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "id",
      "name",
      "owner_id",
      "row_version",
      "state",
      "updated_at",
    ]) ||
    typeof value.id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.id) ||
    typeof value.owner_id !== "string" ||
    !OWNER_ID_PATTERN.test(value.owner_id) ||
    !isBoundedString(value.name, 256) ||
    (value.state !== "active" && value.state !== "archived") ||
    typeof value.row_version !== "number" ||
    !Number.isInteger(value.row_version) ||
    value.row_version < 1 ||
    value.row_version > 2_147_483_647 ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    ownerId: value.owner_id,
    name: value.name,
    state: value.state,
    rowVersion: value.row_version,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseWorkspaceList(value: unknown): DesktopWorkspaceList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items) ||
    value.items.length > MAX_WORKSPACES
  ) {
    return null;
  }
  const items: DesktopWorkspace[] = [];
  const identifiers = new Set<string>();
  for (const candidate of value.items) {
    const workspace = parseWorkspace(candidate);
    if (workspace === null || identifiers.has(workspace.id)) return null;
    identifiers.add(workspace.id);
    items.push(workspace);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseWorkspaceCreate(
  value: unknown,
): DesktopWorkspaceMutationResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created", "workspace"]) ||
    value.created !== true
  ) {
    return null;
  }
  const workspace = parseWorkspace(value.workspace);
  return workspace === null ? null : Object.freeze({ workspace });
}

function parseWorkspaceMutation(
  value: unknown,
): DesktopWorkspaceMutationResult | null {
  if (!isRecord(value) || !hasExactKeys(value, ["workspace"])) return null;
  const workspace = parseWorkspace(value.workspace);
  return workspace === null ? null : Object.freeze({ workspace });
}

function parseErrorCode(value: unknown): string | null {
  if (
    !isRecord(value) ||
    !isRecord(value.error) ||
    typeof value.error.code !== "string" ||
    !ERROR_CODE_PATTERN.test(value.error.code)
  ) {
    return null;
  }
  return value.error.code;
}

function validateBackendOrigin(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("desktop_native_origin_invalid");
  }
  const port = Number(parsed.port);
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    parsed.port === "" ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65_535
  ) {
    throw new Error("desktop_native_origin_invalid");
  }
  return parsed.origin;
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    const parsed = Number(declared);
    if (
      !Number.isSafeInteger(parsed) ||
      parsed < 0 ||
      parsed > MAX_RESPONSE_BYTES
    ) {
      throw new Error("desktop_native_response_invalid");
    }
  }
  if (response.body === null)
    throw new Error("desktop_native_response_invalid");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Error("desktop_native_response_invalid");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const payload = Buffer.concat(chunks, total).toString("utf8");
  try {
    return JSON.parse(payload) as unknown;
  } catch {
    throw new Error("desktop_native_response_invalid");
  }
}

export class DesktopNativeClient {
  readonly #backendOrigin: string;
  readonly #nativeControlToken: string;
  readonly #fetch: FetchLike;

  constructor(options: {
    readonly backendOrigin: string;
    readonly nativeControlToken: string;
    readonly fetch?: FetchLike;
  }) {
    if (!TOKEN_PATTERN.test(options.nativeControlToken)) {
      throw new Error("desktop_native_control_token_invalid");
    }
    this.#backendOrigin = validateBackendOrigin(options.backendOrigin);
    this.#nativeControlToken = options.nativeControlToken;
    this.#fetch = options.fetch ?? fetch;
  }

  getOwnerStatus(): Promise<DesktopOperationResult<DesktopOwnerStatus>> {
    return this.#request(
      "GET",
      "/desktop/v1/owner",
      undefined,
      parseOwnerStatus,
    );
  }

  bootstrapOwner(
    input: DesktopOwnerBootstrapInput,
  ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> {
    return this.#request(
      "POST",
      "/desktop/v1/owner/bootstrap",
      { display_name: input.displayName },
      parseOwnerBootstrap,
    );
  }

  listWorkspaces(): Promise<DesktopOperationResult<DesktopWorkspaceList>> {
    return this.#request(
      "GET",
      "/desktop/v1/workspaces",
      undefined,
      parseWorkspaceList,
    );
  }

  createWorkspace(
    input: DesktopWorkspaceCreateInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    return this.#request(
      "POST",
      "/desktop/v1/workspaces",
      { name: input.name },
      parseWorkspaceCreate,
    );
  }

  archiveWorkspace(
    input: DesktopWorkspaceArchiveInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/archive`,
      { expected_row_version: input.expectedRowVersion },
      parseWorkspaceMutation,
    );
  }

  async #request<T>(
    method: NativeMethod,
    requestPath: string,
    body: Readonly<Record<string, unknown>> | undefined,
    parse: (value: unknown) => T | null,
  ): Promise<DesktopOperationResult<T>> {
    try {
      const response = await this.#fetch(
        `${this.#backendOrigin}${requestPath}`,
        {
          method,
          headers: {
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
            "x-omnibase-desktop-native-control": this.#nativeControlToken,
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          cache: "no-store",
          redirect: "error",
          signal: AbortSignal.timeout(5_000),
        },
      );
      if (
        response.headers.has("x-omnibase-desktop-native-control") ||
        response.headers.has("x-omnibase-desktop-instance") ||
        response.headers.has("x-omnibase-desktop-challenge") ||
        response.headers.has("x-omnibase-desktop-proof")
      ) {
        return failure("desktop_native_response_invalid");
      }
      if (
        response.headers
          .get("content-type")
          ?.split(";", 1)[0]
          ?.trim()
          .toLowerCase() !== "application/json"
      ) {
        return failure("desktop_native_response_invalid");
      }
      const payload = await readBoundedJson(response);
      if (!response.ok) {
        return failure(
          parseErrorCode(payload) ?? "desktop_native_request_failed",
        );
      }
      const parsed = parse(payload);
      return parsed === null
        ? failure("desktop_native_response_invalid")
        : success(parsed);
    } catch {
      return failure("desktop_native_request_failed");
    }
  }
}
