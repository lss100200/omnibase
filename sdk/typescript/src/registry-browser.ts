/** P5.1C Browser Agent Registry control-plane SDK (logical Browser surface only). */

export interface RegistryBrowserErrorEnvelope {
  readonly error: { readonly code: string; readonly message: string };
}

export class RegistryBrowserError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(`${code}: ${message} (request_id=${requestId ?? "unavailable"})`);
    this.name = "RegistryBrowserError";
    this.status = status;
    this.code = code;
    if (requestId !== undefined) this.requestId = requestId;
  }
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
const DIGEST_RE = /^[0-9a-f]{64}$/u;
const SCOPE_RE = /^[a-z][a-z0-9_]{1,63}$/u;
const RISK_LEVELS = new Set(["low", "medium", "high", "critical"]);
const DEFINITION_STATES = new Set(["draft", "active", "disabled", "revoked"]);
const VERSION_STATES = new Set(["draft", "sealed", "deprecated", "revoked"]);
const BINDING_STATES = new Set([
  "pending_approval",
  "installed",
  "disabled",
  "superseded",
  "revoked",
]);

function validateBrowserPath(path: string): `/api/v1/${string}` {
  if (!path.startsWith("/api/v1/")) {
    throw new TypeError("Browser transport only permits requests under /api/v1");
  }
  if (
    path.includes("\\") ||
    path.includes("%") ||
    path.includes("?") ||
    path.includes("#") ||
    path.includes("\0") ||
    path.includes("//") ||
    path.split("/").some((segment) => segment === "." || segment === "..")
  ) {
    throw new TypeError("Browser transport path contains forbidden normalization syntax");
  }
  const parsed = new URL(path, "https://sdk.invalid");
  if (
    parsed.origin !== "https://sdk.invalid" ||
    parsed.pathname !== path ||
    parsed.search !== "" ||
    parsed.hash !== ""
  ) {
    throw new TypeError("Browser transport path is not confined to /api/v1");
  }
  return path as `/api/v1/${string}`;
}

export interface DefaultBudgetPolicyRead {
  readonly max_tokens: number;
  readonly max_cost_units: number;
  readonly max_wall_clock_seconds: number;
  readonly max_tool_calls: number;
}

export interface AgentDefinitionRead {
  readonly agent_definition_id: string;
  readonly stable_logical_key: string;
  readonly display_name: string;
  readonly description: string | null;
  readonly risk_level: string;
  readonly definition_state: string;
  readonly metadata_version: number;
  readonly created_at: string | null;
}

export interface AgentVersionRead {
  readonly agent_version_id: string;
  readonly agent_definition_id: string;
  readonly version: string;
  readonly version_state: string;
  readonly manifest_digest: string;
  readonly instructions_digest: string;
  readonly risk_level: string;
  readonly max_context_tokens: number;
  readonly allowed_tool_ids: readonly string[];
  readonly max_concurrency: number;
  readonly created_at: string | null;
}

export interface AgentInstallationRead {
  readonly binding_id: string;
  readonly workspace_id: string;
  readonly workspace_generation: number;
  readonly agent_definition_id: string;
  readonly agent_version_id: string;
  readonly agent_version_digest: string;
  readonly binding_state: string;
  readonly resource_scopes: readonly string[];
  readonly default_budget_policy: DefaultBudgetPolicyRead;
  readonly created_at: string | null;
  readonly disabled_at: string | null;
  readonly superseded_by: string | null;
}

export interface AgentInstallCreate {
  readonly agent_definition_id: string;
  readonly agent_version_id: string;
  readonly agent_version_digest: string;
  readonly workspace_generation: number;
  readonly resource_scopes: readonly string[];
  readonly default_budget_policy: DefaultBudgetPolicyRead;
  readonly approval_id?: string;
}

export interface AgentUpgradeRequest {
  readonly target_agent_version_id: string;
  readonly target_agent_version_digest: string;
  readonly expected_binding_id?: string;
  readonly approval_id?: string;
}

export interface AgentRollbackRequest {
  readonly rollback_agent_version_id: string;
  readonly rollback_agent_version_digest: string;
  readonly expected_binding_id?: string;
  readonly approval_id?: string;
}

export interface AccessTokenProvider {
  getAccessToken(): string | Promise<string>;
}

export interface BrowserFetchTransportOptions {
  baseUrl: string;
  accessTokenProvider: AccessTokenProvider;
  fetch?: typeof globalThis.fetch;
  allowInsecureLocalhost?: boolean;
  maxResponseBytes?: number;
  requestTimeoutMs?: number;
}

export class BrowserFetchTransport {
  readonly #baseUrl: string;
  readonly #tokenProvider: AccessTokenProvider;
  readonly #fetch: typeof globalThis.fetch;
  readonly #maxResponseBytes: number;
  readonly #requestTimeoutMs: number;

  constructor(options: BrowserFetchTransportOptions) {
    const url = new URL(options.baseUrl);
    const localhost = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
    if (url.protocol !== "https:" && !(options.allowInsecureLocalhost === true && localhost)) {
      throw new TypeError("Browser transport requires HTTPS except explicit localhost development");
    }
    if (url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new TypeError("baseUrl must be an origin without credentials, path, query, or fragment");
    }
    this.#baseUrl = options.baseUrl.replace(/\/$/u, "");
    this.#tokenProvider = options.accessTokenProvider;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#maxResponseBytes = options.maxResponseBytes ?? 1_000_000;
    if (
      !Number.isInteger(this.#maxResponseBytes) ||
      this.#maxResponseBytes < 1 ||
      this.#maxResponseBytes > 2_000_000
    ) {
      throw new TypeError("maxResponseBytes must be between 1 and 2000000");
    }
    this.#requestTimeoutMs = options.requestTimeoutMs ?? 6000;
    if (
      !Number.isInteger(this.#requestTimeoutMs) ||
      this.#requestTimeoutMs < 1 ||
      this.#requestTimeoutMs > 30_000
    ) {
      throw new TypeError("requestTimeoutMs must be between 1 and 30000");
    }
  }

  async request(
    method: "GET" | "POST",
    path: `/api/v1/${string}`,
    body: unknown,
    idempotencyKey?: string,
  ): Promise<{ status: number; headers: Record<string, string>; body: unknown }> {
    const confinedPath = validateBrowserPath(path);
    const token = await this.#tokenProvider.getAccessToken();
    if (typeof token !== "string" || token.length === 0 || /\s/u.test(token)) {
      throw new TypeError("Access token is empty or malformed");
    }
    if (idempotencyKey !== undefined && (idempotencyKey.length < 8 || idempotencyKey.length > 128)) {
      throw new TypeError("idempotencyKey must contain between 8 and 128 characters");
    }
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "X-Request-Id": crypto.randomUUID(),
    };
    let bodyValue: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      bodyValue = JSON.stringify(body);
    }
    if (idempotencyKey !== undefined) headers["Idempotency-Key"] = idempotencyKey;
    const response = await this.#fetch(`${this.#baseUrl}${confinedPath}`, {
      method,
      headers,
      body: bodyValue ?? null,
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(this.#requestTimeoutMs),
    });
    const responseHeaders: Record<string, string> = {};
    response.headers.forEach((value, key) => {
      responseHeaders[key.toLowerCase()] = value;
    });
    return { status: response.status, headers: responseHeaders, body: await readJsonBounded(response, this.#maxResponseBytes) };
  }
}

async function readJsonBounded(response: Response, limit: number): Promise<unknown> {
  if (response.body === null) return null;
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw new TypeError("Browser response exceeded the configured byte limit");
    }
    chunks.push(value);
  }
  const combined = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder().decode(combined)) as unknown;
}

function raiseForError(response: { status: number; headers: Record<string, string>; body: unknown }): void {
  if (response.status >= 200 && response.status < 300) return;
  let code = "invalid_browser_response";
  let message = "Browser returned an invalid error envelope";
  if (isExactErrorEnvelope(response.body)) {
    code = response.body.error.code;
    message = response.body.error.message;
  }
  const candidate = response.headers["x-request-id"];
  const requestId =
    candidate !== undefined && /^[A-Za-z0-9._-]{1,64}$/u.test(candidate) ? candidate : undefined;
  throw new RegistryBrowserError(response.status, code, message, requestId);
}

function isExactErrorEnvelope(value: unknown): value is RegistryBrowserErrorEnvelope {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  if (Object.keys(value).length !== 1 || !("error" in value)) return false;
  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    !Array.isArray(error) &&
    Object.keys(error).length === 2 &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

function requireUuid(value: string, label: string): string {
  if (!UUID_RE.test(value)) throw new TypeError(`${label} must be a lowercase UUID`);
  return value;
}

function requireDigest(value: string, label: string): string {
  if (!DIGEST_RE.test(value)) throw new TypeError(`${label} must be a lowercase 64-character SHA-256`);
  return value;
}

function requireScopes(value: readonly string[]): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 32) {
    throw new TypeError("resource_scopes must be a non-empty array of at most 32 scopes");
  }
  const seen = new Set<string>();
  for (const scope of value) {
    if (scope === "*" || scope === "all" || scope === "any" || !SCOPE_RE.test(scope)) {
      throw new TypeError("resource_scopes contains a wildcard or invalid scope");
    }
    if (seen.has(scope)) throw new TypeError("resource_scopes must not contain duplicates");
    seen.add(scope);
  }
  return [...value];
}

function requireBudget(value: unknown): DefaultBudgetPolicyRead {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("default_budget_policy must be an object");
  }
  const budget = value as Record<string, unknown>;
  const keys = ["max_tokens", "max_cost_units", "max_wall_clock_seconds", "max_tool_calls"];
  if (Object.keys(budget).length !== 4 || keys.some((key) => !(key in budget))) {
    throw new TypeError("default_budget_policy must contain exactly the four budget keys");
  }
  const out: Record<string, number> = {};
  for (const key of keys) {
    const number = budget[key];
    if (typeof number !== "number" || !Number.isInteger(number) || number < 1) {
      throw new TypeError(`${key} must be a positive integer`);
    }
    out[key] = number;
  }
  return {
    max_tokens: out.max_tokens!,
    max_cost_units: out.max_cost_units!,
    max_wall_clock_seconds: out.max_wall_clock_seconds!,
    max_tool_calls: out.max_tool_calls!,
  };
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requireExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value);
  if (actual.length !== expected.length || expected.some((key) => !(key in value))) {
    throw new TypeError(`${label} contains missing or extra fields`);
  }
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string") throw new TypeError(`${label} must be a string`);
  return value;
}

function requireNullableString(value: unknown, label: string): string | null {
  if (value === null) return null;
  return requireString(value, label);
}

function requirePositiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new TypeError(`${label} must be a positive integer`);
  }
  return value;
}

function requireNonnegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return value;
}

function requireClosedString(value: unknown, allowed: ReadonlySet<string>, label: string): string {
  const text = requireString(value, label);
  if (!allowed.has(text)) throw new TypeError(`${label} is outside the closed set`);
  return text;
}

function requireStringArray(value: unknown, label: string, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new TypeError(`${label} must be an array of at most ${maximum} strings`);
  }
  return value.map((item) => requireString(item, label));
}

function parseDefinition(value: unknown): AgentDefinitionRead {
  const data = requireRecord(value, "agent definition");
  requireExactKeys(
    data,
    [
      "agent_definition_id",
      "stable_logical_key",
      "display_name",
      "description",
      "risk_level",
      "definition_state",
      "metadata_version",
      "created_at",
    ],
    "agent definition",
  );
  return {
    agent_definition_id: requireUuid(
      requireString(data.agent_definition_id, "agent_definition_id"),
      "agent_definition_id",
    ),
    stable_logical_key: requireString(data.stable_logical_key, "stable_logical_key"),
    display_name: requireString(data.display_name, "display_name"),
    description: requireNullableString(data.description, "description"),
    risk_level: requireClosedString(data.risk_level, RISK_LEVELS, "risk_level"),
    definition_state: requireClosedString(
      data.definition_state,
      DEFINITION_STATES,
      "definition_state",
    ),
    metadata_version: requirePositiveInteger(data.metadata_version, "metadata_version"),
    created_at: requireNullableString(data.created_at, "created_at"),
  };
}

function parseVersion(value: unknown): AgentVersionRead {
  const data = requireRecord(value, "agent version");
  requireExactKeys(
    data,
    [
      "agent_version_id",
      "agent_definition_id",
      "version",
      "version_state",
      "manifest_digest",
      "instructions_digest",
      "risk_level",
      "max_context_tokens",
      "allowed_tool_ids",
      "max_concurrency",
      "created_at",
    ],
    "agent version",
  );
  return {
    agent_version_id: requireUuid(
      requireString(data.agent_version_id, "agent_version_id"),
      "agent_version_id",
    ),
    agent_definition_id: requireUuid(
      requireString(data.agent_definition_id, "agent_definition_id"),
      "agent_definition_id",
    ),
    version: requireString(data.version, "version"),
    version_state: requireClosedString(data.version_state, VERSION_STATES, "version_state"),
    manifest_digest: requireDigest(
      requireString(data.manifest_digest, "manifest_digest"),
      "manifest_digest",
    ),
    instructions_digest: requireDigest(
      requireString(data.instructions_digest, "instructions_digest"),
      "instructions_digest",
    ),
    risk_level: requireClosedString(data.risk_level, RISK_LEVELS, "risk_level"),
    max_context_tokens: requirePositiveInteger(data.max_context_tokens, "max_context_tokens"),
    allowed_tool_ids: requireStringArray(data.allowed_tool_ids, "allowed_tool_ids", 64),
    max_concurrency: requirePositiveInteger(data.max_concurrency, "max_concurrency"),
    created_at: requireNullableString(data.created_at, "created_at"),
  };
}

function parseBinding(value: unknown): AgentInstallationRead {
  const data = requireRecord(value, "agent installation");
  requireExactKeys(
    data,
    [
      "binding_id",
      "workspace_id",
      "workspace_generation",
      "agent_definition_id",
      "agent_version_id",
      "agent_version_digest",
      "binding_state",
      "resource_scopes",
      "default_budget_policy",
      "created_at",
      "disabled_at",
      "superseded_by",
    ],
    "agent installation",
  );
  return {
    binding_id: requireUuid(requireString(data.binding_id, "binding_id"), "binding_id"),
    workspace_id: requireUuid(requireString(data.workspace_id, "workspace_id"), "workspace_id"),
    workspace_generation: requirePositiveInteger(data.workspace_generation, "workspace_generation"),
    agent_definition_id: requireUuid(
      requireString(data.agent_definition_id, "agent_definition_id"),
      "agent_definition_id",
    ),
    agent_version_id: requireUuid(
      requireString(data.agent_version_id, "agent_version_id"),
      "agent_version_id",
    ),
    agent_version_digest: requireDigest(
      requireString(data.agent_version_digest, "agent_version_digest"),
      "agent_version_digest",
    ),
    binding_state: requireClosedString(data.binding_state, BINDING_STATES, "binding_state"),
    resource_scopes: requireScopes(
      requireStringArray(data.resource_scopes, "resource_scopes", 32),
    ),
    default_budget_policy: requireBudget(data.default_budget_policy),
    created_at: requireNullableString(data.created_at, "created_at"),
    disabled_at: requireNullableString(data.disabled_at, "disabled_at"),
    superseded_by:
      data.superseded_by === null
        ? null
        : requireUuid(requireString(data.superseded_by, "superseded_by"), "superseded_by"),
  };
}

function parseList(value: unknown): { items: unknown[]; total: number } {
  const data = requireRecord(value, "list response");
  requireExactKeys(data, ["items", "total"], "list response");
  if (!Array.isArray(data.items)) throw new TypeError("list response must contain items");
  return { items: data.items, total: requireNonnegativeInteger(data.total, "total") };
}

export interface BrowserRegistryResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
}

export interface BrowserRegistryTransport {
  request(
    method: "GET" | "POST",
    path: `/api/v1/${string}`,
    body: unknown,
    idempotencyKey?: string,
  ): Promise<BrowserRegistryResponse>;
}

export class AgentRegistryBrowserClient {
  readonly #transport: BrowserRegistryTransport;

  constructor(transport: BrowserRegistryTransport) {
    this.#transport = transport;
  }

  static fromHttp(options: BrowserFetchTransportOptions): AgentRegistryBrowserClient {
    return new AgentRegistryBrowserClient(new BrowserFetchTransport(options));
  }

  async listAgentDefinitions(): Promise<{ items: AgentDefinitionRead[]; total: number }> {
    const response = await this.#transport.request("GET", "/api/v1/agent-definitions", undefined);
    raiseForError(response);
    const list = parseList(response.body);
    return { items: list.items.map(parseDefinition), total: list.total };
  }

  async getAgentDefinition(agentDefinitionId: string): Promise<AgentDefinitionRead> {
    const id = requireUuid(agentDefinitionId, "agent_definition_id");
    const response = await this.#transport.request("GET", `/api/v1/agent-definitions/${id}`, undefined);
    raiseForError(response);
    return parseDefinition(response.body);
  }

  async listAgentVersions(agentDefinitionId: string): Promise<{ items: AgentVersionRead[]; total: number }> {
    const id = requireUuid(agentDefinitionId, "agent_definition_id");
    const response = await this.#transport.request("GET", `/api/v1/agent-definitions/${id}/versions`, undefined);
    raiseForError(response);
    const list = parseList(response.body);
    return { items: list.items.map(parseVersion), total: list.total };
  }

  async getAgentVersion(
    agentDefinitionId: string,
    agentVersionId: string,
  ): Promise<AgentVersionRead> {
    const definition = requireUuid(agentDefinitionId, "agent_definition_id");
    const version = requireUuid(agentVersionId, "agent_version_id");
    const response = await this.#transport.request(
      "GET",
      `/api/v1/agent-definitions/${definition}/versions/${version}`,
      undefined,
    );
    raiseForError(response);
    return parseVersion(response.body);
  }

  async listInstallations(workspaceId: string): Promise<{ items: AgentInstallationRead[]; total: number }> {
    const workspace = requireUuid(workspaceId, "workspace_id");
    const response = await this.#transport.request(
      "GET",
      `/api/v1/workspaces/${workspace}/agent-installations`,
      undefined,
    );
    raiseForError(response);
    const list = parseList(response.body);
    return { items: list.items.map(parseBinding), total: list.total };
  }

  async getInstallation(workspaceId: string, bindingId: string): Promise<AgentInstallationRead> {
    const workspace = requireUuid(workspaceId, "workspace_id");
    const binding = requireUuid(bindingId, "binding_id");
    const response = await this.#transport.request(
      "GET",
      `/api/v1/workspaces/${workspace}/agent-installations/${binding}`,
      undefined,
    );
    raiseForError(response);
    return parseBinding(response.body);
  }

  async install(options: {
    workspaceId: string;
    idempotencyKey: string;
    payload: AgentInstallCreate;
  }): Promise<AgentInstallationRead> {
    const workspace = requireUuid(options.workspaceId, "workspace_id");
    const payload: Record<string, unknown> = {
      agent_definition_id: requireUuid(options.payload.agent_definition_id, "agent_definition_id"),
      agent_version_id: requireUuid(options.payload.agent_version_id, "agent_version_id"),
      agent_version_digest: requireDigest(
        options.payload.agent_version_digest,
        "agent_version_digest",
      ),
      workspace_generation: options.payload.workspace_generation,
      resource_scopes: requireScopes(options.payload.resource_scopes),
      default_budget_policy: requireBudget(options.payload.default_budget_policy),
    };
    if (options.payload.approval_id !== undefined) {
      payload.approval_id = requireUuid(options.payload.approval_id, "approval_id");
    }
    const response = await this.#transport.request(
      "POST",
      `/api/v1/workspaces/${workspace}/agent-installations`,
      payload,
      options.idempotencyKey,
    );
    raiseForError(response);
    return parseBinding(response.body);
  }

  async disable(options: {
    workspaceId: string;
    bindingId: string;
    idempotencyKey: string;
  }): Promise<AgentInstallationRead> {
    const workspace = requireUuid(options.workspaceId, "workspace_id");
    const binding = requireUuid(options.bindingId, "binding_id");
    const response = await this.#transport.request(
      "POST",
      `/api/v1/workspaces/${workspace}/agent-installations/${binding}/disable`,
      undefined,
      options.idempotencyKey,
    );
    raiseForError(response);
    return parseBinding(response.body);
  }

  async upgrade(options: {
    workspaceId: string;
    bindingId: string;
    idempotencyKey: string;
    payload: AgentUpgradeRequest;
  }): Promise<AgentInstallationRead> {
    const workspace = requireUuid(options.workspaceId, "workspace_id");
    const binding = requireUuid(options.bindingId, "binding_id");
    const payload: Record<string, unknown> = {
      target_agent_version_id: requireUuid(
        options.payload.target_agent_version_id,
        "target_agent_version_id",
      ),
      target_agent_version_digest: requireDigest(
        options.payload.target_agent_version_digest,
        "target_agent_version_digest",
      ),
    };
    if (options.payload.expected_binding_id !== undefined) {
      payload.expected_binding_id = requireUuid(
        options.payload.expected_binding_id,
        "expected_binding_id",
      );
    }
    if (options.payload.approval_id !== undefined) {
      payload.approval_id = requireUuid(options.payload.approval_id, "approval_id");
    }
    const response = await this.#transport.request(
      "POST",
      `/api/v1/workspaces/${workspace}/agent-installations/${binding}/upgrade`,
      payload,
      options.idempotencyKey,
    );
    raiseForError(response);
    return parseBinding(response.body);
  }

  async rollback(options: {
    workspaceId: string;
    bindingId: string;
    idempotencyKey: string;
    payload: AgentRollbackRequest;
  }): Promise<AgentInstallationRead> {
    const workspace = requireUuid(options.workspaceId, "workspace_id");
    const binding = requireUuid(options.bindingId, "binding_id");
    const payload: Record<string, unknown> = {
      rollback_agent_version_id: requireUuid(
        options.payload.rollback_agent_version_id,
        "rollback_agent_version_id",
      ),
      rollback_agent_version_digest: requireDigest(
        options.payload.rollback_agent_version_digest,
        "rollback_agent_version_digest",
      ),
    };
    if (options.payload.expected_binding_id !== undefined) {
      payload.expected_binding_id = requireUuid(
        options.payload.expected_binding_id,
        "expected_binding_id",
      );
    }
    if (options.payload.approval_id !== undefined) {
      payload.approval_id = requireUuid(options.payload.approval_id, "approval_id");
    }
    const response = await this.#transport.request(
      "POST",
      `/api/v1/workspaces/${workspace}/agent-installations/${binding}/rollback`,
      payload,
      options.idempotencyKey,
    );
    raiseForError(response);
    return parseBinding(response.body);
  }
}
