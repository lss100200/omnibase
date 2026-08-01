import type { JsonValue } from "./models.js";

export interface WorkloadCredential {
  readonly token: string;
  readonly workloadIdentity: string;
  readonly expiresAt: Date;
}

export interface WorkloadCredentialProvider {
  getCredential(): WorkloadCredential | Promise<WorkloadCredential>;
}

export interface TransportResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: unknown;
}

export interface Transport {
  request(
    method: "POST",
    path: `/gateway/v1/${string}`,
    body: Record<string, JsonValue>,
  ): Promise<TransportResponse>;
}

export class GatewayError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(`${code}: ${message} (request_id=${requestId ?? "unavailable"})`);
    this.name = "GatewayError";
    this.status = status;
    this.code = code;
    if (requestId !== undefined) this.requestId = requestId;
  }
}

export interface FetchTransportOptions {
  baseUrl: string;
  credentialProvider: WorkloadCredentialProvider;
  fetch?: typeof globalThis.fetch;
  allowInsecureLocalhost?: boolean;
  maxResponseBytes?: number;
  requestTimeoutMs?: number;
}

export class FetchTransport implements Transport {
  readonly #baseUrl: string;
  readonly #credentialProvider: WorkloadCredentialProvider;
  readonly #fetch: typeof globalThis.fetch;
  readonly #maxResponseBytes: number;
  readonly #requestTimeoutMs: number;

  constructor(options: FetchTransportOptions) {
    const url = new URL(options.baseUrl);
    const localhost = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
    if (url.protocol !== "https:" && !(options.allowInsecureLocalhost === true && localhost)) {
      throw new TypeError("Gateway transport requires HTTPS except explicit localhost development");
    }
    if (url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new TypeError("baseUrl must be an origin without credentials, path, query, or fragment");
    }
    this.#baseUrl = options.baseUrl.replace(/\/$/u, "");
    this.#credentialProvider = options.credentialProvider;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#maxResponseBytes = options.maxResponseBytes ?? 1_100_000;
    if (!Number.isInteger(this.#maxResponseBytes) || this.#maxResponseBytes < 1 || this.#maxResponseBytes > 2_000_000) {
      throw new TypeError("maxResponseBytes must be between 1 and 2000000");
    }
    this.#requestTimeoutMs = options.requestTimeoutMs ?? 6000;
    if (!Number.isInteger(this.#requestTimeoutMs) || this.#requestTimeoutMs < 1 || this.#requestTimeoutMs > 30_000) {
      throw new TypeError("requestTimeoutMs must be between 1 and 30000");
    }
  }

  async request(
    method: "POST",
    path: `/gateway/v1/${string}`,
    body: Record<string, JsonValue>,
  ): Promise<TransportResponse> {
    if (method !== "POST" || !path.startsWith("/gateway/v1/")) {
      throw new TypeError("P34.2 transport only permits POST requests to /gateway/v1");
    }
    const credential = await this.#credentialProvider.getCredential();
    if (
      !credential.token ||
      /\s/u.test(credential.token) ||
      !credential.workloadIdentity ||
      credential.workloadIdentity.length > 128 ||
      credential.expiresAt <= new Date()
    ) {
      throw new TypeError("Capability credential is expired or malformed");
    }
    const response = await this.#fetch(`${this.#baseUrl}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Capability ${credential.token}`,
        "Content-Type": "application/json",
        "X-Omnibase-Workload-Identity": credential.workloadIdentity,
        "X-Request-Id": crypto.randomUUID(),
      },
      body: JSON.stringify(body),
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(this.#requestTimeoutMs),
    });
    const headers: Record<string, string> = {};
    response.headers.forEach((value, key) => {
      headers[key.toLowerCase()] = value;
    });
    return { status: response.status, headers, body: await readJsonBounded(response, this.#maxResponseBytes) };
  }
}

async function readJsonBounded(response: Response, limit: number): Promise<unknown> {
  if (response.body === null) return null;
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw new TypeError("Gateway response exceeded the configured byte limit");
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

export function raiseForError(response: TransportResponse): void {
  if (response.status >= 200 && response.status < 300) return;
  let code = "invalid_gateway_response";
  let message = "Gateway returned an invalid error envelope";
  if (isExactErrorEnvelope(response.body)) {
    code = response.body.error.code;
    message = response.body.error.message;
  }
  const candidate = response.headers["x-request-id"];
  const requestId = candidate !== undefined && /^[A-Za-z0-9._-]{1,64}$/u.test(candidate) ? candidate : undefined;
  throw new GatewayError(response.status, code, message, requestId);
}

function isExactErrorEnvelope(
  value: unknown,
): value is { error: { code: string; message: string } } {
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
