import { Buffer } from "node:buffer";
import { lookup as dnsLookup } from "node:dns/promises";
import http from "node:http";
import https from "node:https";
import { isIP } from "node:net";
import { URL } from "node:url";

import { isGlobalUnicastAddress, isLoopbackConnectAddress, unwrapIpv4MappedAddress } from "./global-unicast.ts";

export interface TeamChatMessage {
  readonly role: "system" | "user" | "assistant";
  readonly content: string;
}

export interface TeamChatRequest {
  readonly baseUrl: string;
  readonly secret: string;
  readonly model: string;
  readonly messages: readonly TeamChatMessage[];
  readonly timeoutMs: number;
  readonly allowLoopbackHttp: boolean;
}

export interface TeamChatResult {
  readonly text: string;
  readonly actualModel: string | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly totalTokens: number | null;
}

export interface TeamChatTransport {
  complete(request: TeamChatRequest, signal: AbortSignal): Promise<TeamChatResult>;
}

export interface PinnedTeamEndpoint {
  readonly scheme: "http" | "https";
  readonly hostname: string;
  readonly port: number;
  readonly chatPath: string;
  readonly connectAddrs: readonly string[];
  readonly loopback: boolean;
}

export interface TeamTransportHooks {
  readonly lookup?: (hostname: string, port: number) => Promise<readonly string[]>;
}

const MAX_BODY = 2 * 1024 * 1024;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function coded(code: string): Error {
  return Object.assign(new Error(code), { code });
}

export function classifyConnectAddress(address: string): "loopback" | "private" | "link-local" | "unsafe" | "public" {
  const raw = unwrapIpv4MappedAddress(address);
  if (isLoopbackConnectAddress(raw)) return "loopback";
  if (isGlobalUnicastAddress(raw)) return "public";
  return "unsafe";
}

export function assertPinnedConnectAddress(address: string, allowLoopback: boolean): void {
  const raw = unwrapIpv4MappedAddress(address);
  if (isLoopbackConnectAddress(raw)) {
    if (!allowLoopback) throw coded("desktop_provider_endpoint_invalid");
    return;
  }
  if (!isGlobalUnicastAddress(raw)) throw coded("desktop_provider_endpoint_invalid");
}

function hostnameIsLoopback(hostname: string): boolean {
  if (LOOPBACK_HOSTS.has(hostname)) return true;
  return isLoopbackConnectAddress(hostname);
}

async function defaultLookup(hostname: string, port: number): Promise<readonly string[]> {
  const answers = await dnsLookup(hostname, { all: true, verbatim: true });
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const item of answers) {
    if (seen.has(item.address)) continue;
    seen.add(item.address);
    ordered.push(item.address);
  }
  if (ordered.length === 0) {
    const fallback = await dnsLookup(hostname, { verbatim: true });
    if (typeof fallback.address === "string" && fallback.address.length > 0) {
      return [fallback.address];
    }
  }
  void port;
  return ordered;
}

export async function resolvePinnedTeamEndpoint(
  baseUrl: string,
  allowLoopbackHttp: boolean,
  hooks: TeamTransportHooks = {},
): Promise<PinnedTeamEndpoint> {
  const candidate = baseUrl.trim();
  if (candidate.length === 0 || /[\r\n\t]/u.test(candidate)) {
    throw coded("desktop_provider_endpoint_invalid");
  }
  let target: URL;
  try {
    target = new URL(chatCompletionsUrl(candidate));
  } catch {
    throw coded("desktop_provider_endpoint_invalid");
  }
  const hostname = target.hostname.toLowerCase().replace(/\.+$/u, "");
  if (
    target.username !== "" ||
    target.password !== "" ||
    target.search !== "" ||
    target.hash !== "" ||
    hostname.length === 0 ||
    (target.protocol !== "http:" && target.protocol !== "https:")
  ) {
    throw coded("desktop_provider_endpoint_invalid");
  }
  const loopback = hostnameIsLoopback(hostname);
  if (target.protocol === "http:") {
    if (!allowLoopbackHttp || !loopback) throw coded("desktop_provider_endpoint_invalid");
    const port = target.port === "" ? 80 : Number(target.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw coded("desktop_provider_endpoint_invalid");
    }
    const connectHost = hostname === "::1" ? "::1" : "127.0.0.1";
    return {
      scheme: "http",
      hostname,
      port,
      chatPath: `${target.pathname}${target.search}`,
      connectAddrs: [connectHost],
      loopback: true,
    };
  }
  if (loopback && !allowLoopbackHttp) throw coded("desktop_provider_endpoint_invalid");
  const port = target.port === "" ? 443 : Number(target.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw coded("desktop_provider_endpoint_invalid");
  }
  if (loopback) {
    const connectHost = hostname === "::1" ? "::1" : "127.0.0.1";
    return {
      scheme: "https",
      hostname,
      port,
      chatPath: `${target.pathname}${target.search}`,
      connectAddrs: [connectHost],
      loopback: true,
    };
  }
  if (isIP(hostname) !== 0) {
    assertPinnedConnectAddress(hostname, false);
    return {
      scheme: "https",
      hostname,
      port,
      chatPath: `${target.pathname}${target.search}`,
      connectAddrs: [hostname],
      loopback: false,
    };
  }
  const lookup = hooks.lookup ?? defaultLookup;
  let addresses: readonly string[];
  try {
    addresses = await lookup(hostname, port);
  } catch {
    throw coded("desktop_provider_unreachable");
  }
  if (addresses.length === 0) throw coded("desktop_provider_unreachable");
  for (const address of addresses) assertPinnedConnectAddress(address, false);
  return {
    scheme: "https",
    hostname,
    port,
    chatPath: `${target.pathname}${target.search}`,
    connectAddrs: addresses,
    loopback: false,
  };
}

function chatCompletionsUrl(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/+$/u, "");
  return trimmed.endsWith("/chat/completions") ? trimmed : `${trimmed}/chat/completions`;
}

function usableAssistantContent(parsed: unknown): string {
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw coded("desktop_provider_response_invalid");
  }
  const record = parsed as {
    choices?: unknown;
  };
  if (!Array.isArray(record.choices) || record.choices.length === 0) {
    throw coded("desktop_provider_response_invalid");
  }
  const first = record.choices[0];
  if (typeof first !== "object" || first === null) {
    throw coded("desktop_provider_response_invalid");
  }
  const message = (first as { message?: unknown }).message;
  if (typeof message !== "object" || message === null) {
    throw coded("desktop_provider_response_invalid");
  }
  const role = (message as { role?: unknown }).role;
  if (role !== undefined && role !== "assistant") {
    throw coded("desktop_provider_response_invalid");
  }
  const content = (message as { content?: unknown }).content;
  if (typeof content !== "string" || content.length === 0) {
    throw coded("desktop_provider_response_invalid");
  }
  return content;
}

export function assertRequestedModelIdentity(requested: string, actual: string | null): void {
  if (typeof actual !== "string" || actual.trim().length === 0 || actual !== requested) {
    throw coded("desktop_provider_model_identity_drift");
  }
}

function parseJsonCompletion(body: string, requestedModel: string): TeamChatResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw coded("desktop_provider_response_invalid");
  }
  const text = usableAssistantContent(parsed);
  const record = parsed as { model?: unknown; usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } };
  const actualModel = typeof record.model === "string" && record.model.trim().length > 0 ? record.model : null;
  assertRequestedModelIdentity(requestedModel, actualModel);
  return {
    text,
    actualModel,
    inputTokens: record.usage?.prompt_tokens ?? null,
    outputTokens: record.usage?.completion_tokens ?? null,
    totalTokens: record.usage?.total_tokens ?? null,
  };
}

function readSseText(body: string, requestedModel: string): TeamChatResult {
  let text = "";
  let model: string | null = null;
  let inputTokens: number | null = null;
  let outputTokens: number | null = null;
  let totalTokens: number | null = null;
  let sawChoice = false;
  for (const block of body.split("\n\n")) {
    const line = block
      .split("\n")
      .filter((item) => item.startsWith("data:"))
      .map((item) => item.slice(5).trim())
      .join("");
    if (line === "" || line === "[DONE]") continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw coded("desktop_provider_response_invalid");
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw coded("desktop_provider_response_invalid");
    }
    const record = parsed as {
      model?: unknown;
      choices?: { delta?: { content?: string }; message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
    };
    if (typeof record.model === "string" && record.model.trim().length > 0) {
      if (record.model !== requestedModel || (model !== null && model !== record.model)) {
        throw coded("desktop_provider_model_identity_drift");
      }
      model = record.model;
    }
    if (Array.isArray(record.choices) && record.choices.length > 0) {
      sawChoice = true;
      const delta = record.choices[0]?.delta?.content ?? record.choices[0]?.message?.content;
      if (typeof delta === "string") text += delta;
    }
    if (record.usage) {
      inputTokens = record.usage.prompt_tokens ?? inputTokens;
      outputTokens = record.usage.completion_tokens ?? outputTokens;
      totalTokens = record.usage.total_tokens ?? totalTokens;
    }
  }
  if (!sawChoice || text.length === 0) {
    throw coded("desktop_provider_stream_incomplete");
  }
  assertRequestedModelIdentity(requestedModel, model);
  return {
    text,
    actualModel: model,
    inputTokens,
    outputTokens,
    totalTokens,
  };
}

export function createOpenAiCompatibleTransport(hooks: TeamTransportHooks = {}): TeamChatTransport {
  return {
    async complete(request, signal) {
      if (signal.aborted) throw coded("desktop_invocation_cancelled");
      const endpoint = await resolvePinnedTeamEndpoint(request.baseUrl, request.allowLoopbackHttp, hooks);
      const payload = JSON.stringify({
        model: request.model,
        stream: false,
        messages: request.messages,
      });
      let lastError: unknown = coded("desktop_provider_unreachable");
      for (const address of endpoint.connectAddrs) {
        try {
          return await new Promise<TeamChatResult>((resolve, reject) => {
            if (signal.aborted) {
              reject(coded("desktop_invocation_cancelled"));
              return;
            }
            const client = endpoint.scheme === "https" ? https : http;
            const req = client.request(
              {
                protocol: `${endpoint.scheme}:`,
                hostname: address,
                port: endpoint.port,
                path: endpoint.chatPath,
                method: "POST",
                servername: endpoint.hostname,
                headers: {
                  Accept: "application/json, text/event-stream",
                  Authorization: `Bearer ${request.secret}`,
                  "Content-Type": "application/json",
                  "Content-Length": Buffer.byteLength(payload),
                  Host: endpoint.hostname,
                },
                timeout: request.timeoutMs,
                lookup(hostname, options, callback) {
                  const family = isIP(address) === 6 ? 6 : 4;
                  const cb = callback as (err: NodeJS.ErrnoException | null, address: string, family: number) => void;
                  void hostname;
                  void options;
                  cb(null, address, family);
                },
              },
              (response) => {
                const chunks: Buffer[] = [];
                let size = 0;
                response.on("data", (chunk: Buffer) => {
                  size += chunk.length;
                  if (size > MAX_BODY) {
                    req.destroy();
                    reject(coded("desktop_provider_stream_incomplete"));
                  } else {
                    chunks.push(chunk);
                  }
                });
                response.on("end", () => {
                  const body = Buffer.concat(chunks).toString("utf8");
                  const contentType = String(response.headers["content-type"] ?? "");
                  const status = response.statusCode ?? 0;
                  try {
                    if (status < 200 || status >= 300) throw coded("desktop_invocation_failed");
                    if (contentType.includes("text/event-stream")) {
                      resolve(readSseText(body, request.model));
                      return;
                    }
                    resolve(parseJsonCompletion(body, request.model));
                  } catch (error) {
                    reject(error);
                  }
                });
              },
            );
            const onAbort = () => {
              req.destroy();
              reject(coded("desktop_invocation_cancelled"));
            };
            signal.addEventListener("abort", onAbort, { once: true });
            req.on("error", () => {
              signal.removeEventListener("abort", onAbort);
              reject(coded("desktop_invocation_failed"));
            });
            req.on("timeout", () => {
              req.destroy();
              reject(coded("desktop_invocation_failed"));
            });
            req.write(payload);
            req.end();
          });
        } catch (error) {
          lastError = error;
          const code =
            typeof error === "object" && error !== null && "code" in error
              ? String((error as { code?: unknown }).code)
              : "";
          if (
            code === "desktop_invocation_cancelled" ||
            code === "desktop_provider_model_identity_drift" ||
            code === "desktop_provider_response_invalid" ||
            code === "desktop_provider_stream_incomplete" ||
            code === "desktop_invocation_failed"
          ) {
            throw error;
          }
        }
      }
      throw lastError;
    },
  };
}
