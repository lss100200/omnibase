import { Buffer } from "node:buffer";
import http from "node:http";
import https from "node:https";
import { URL } from "node:url";

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

const MAX_BODY = 2 * 1024 * 1024;

function assertLoopbackPolicy(target: URL, allowLoopbackHttp: boolean): void {
  const hostname = target.hostname;
  const loopback = hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
  if (target.protocol === "http:") {
    if (!allowLoopbackHttp || !loopback) {
      throw Object.assign(new Error("desktop_provider_endpoint_invalid"), {
        code: "desktop_provider_endpoint_invalid",
      });
    }
    return;
  }
  if (target.protocol !== "https:") {
    throw Object.assign(new Error("desktop_provider_endpoint_invalid"), {
      code: "desktop_provider_endpoint_invalid",
    });
  }
}

function chatCompletionsUrl(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/+$/u, "");
  return trimmed.endsWith("/chat/completions")
    ? trimmed
    : `${trimmed}/chat/completions`;
}

function readSseText(body: string): { text: string; model: string | null; usage: TeamChatResult } {
  let text = "";
  let model: string | null = null;
  let inputTokens: number | null = null;
  let outputTokens: number | null = null;
  let totalTokens: number | null = null;
  for (const block of body.split("\n\n")) {
    const line = block
      .split("\n")
      .filter((item) => item.startsWith("data:"))
      .map((item) => item.slice(5).trim())
      .join("");
    if (line === "" || line === "[DONE]") continue;
    try {
      const parsed = JSON.parse(line) as {
        model?: string;
        choices?: { delta?: { content?: string }; message?: { content?: string } }[];
        usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
      };
      if (typeof parsed.model === "string") model = parsed.model;
      const delta = parsed.choices?.[0]?.delta?.content ?? parsed.choices?.[0]?.message?.content;
      if (typeof delta === "string") text += delta;
      if (parsed.usage) {
        inputTokens = parsed.usage.prompt_tokens ?? inputTokens;
        outputTokens = parsed.usage.completion_tokens ?? outputTokens;
        totalTokens = parsed.usage.total_tokens ?? totalTokens;
      }
    } catch {
      continue;
    }
  }
  return {
    text,
    model,
    usage: {
      text,
      actualModel: model,
      inputTokens,
      outputTokens,
      totalTokens,
    },
  };
}

function parseJsonCompletion(body: string): TeamChatResult {
  const parsed = JSON.parse(body) as {
    model?: string;
    choices?: { message?: { content?: string }; delta?: { content?: string } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  };
  const text =
    parsed.choices?.[0]?.message?.content ?? parsed.choices?.[0]?.delta?.content ?? "";
  if (typeof text !== "string" || text.length === 0) {
    throw Object.assign(new Error("desktop_provider_stream_incomplete"), {
      code: "desktop_provider_stream_incomplete",
    });
  }
  return {
    text,
    actualModel: typeof parsed.model === "string" ? parsed.model : null,
    inputTokens: parsed.usage?.prompt_tokens ?? null,
    outputTokens: parsed.usage?.completion_tokens ?? null,
    totalTokens: parsed.usage?.total_tokens ?? null,
  };
}

export function createOpenAiCompatibleTransport(): TeamChatTransport {
  return {
    complete(request, signal) {
      return new Promise((resolve, reject) => {
        if (signal.aborted) {
          reject(
            Object.assign(new Error("desktop_invocation_cancelled"), {
              code: "desktop_invocation_cancelled",
            }),
          );
          return;
        }
        let target: URL;
        try {
          target = new URL(chatCompletionsUrl(request.baseUrl));
          assertLoopbackPolicy(target, request.allowLoopbackHttp);
        } catch (error) {
          reject(error);
          return;
        }
        const payload = JSON.stringify({
          model: request.model,
          stream: false,
          messages: request.messages,
        });
        const client = target.protocol === "https:" ? https : http;
        const req = client.request(
          {
            protocol: target.protocol,
            hostname: target.hostname,
            port: target.port,
            path: `${target.pathname}${target.search}`,
            method: "POST",
            headers: {
              Accept: "application/json, text/event-stream",
              Authorization: `Bearer ${request.secret}`,
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
            },
            timeout: request.timeoutMs,
          },
          (response) => {
            const chunks: Buffer[] = [];
            let size = 0;
            response.on("data", (chunk: Buffer) => {
              size += chunk.length;
              if (size > MAX_BODY) {
                req.destroy();
                reject(
                  Object.assign(new Error("desktop_provider_stream_incomplete"), {
                    code: "desktop_provider_stream_incomplete",
                  }),
                );
                return;
              }
              chunks.push(chunk);
            });
            response.on("end", () => {
              const body = Buffer.concat(chunks).toString("utf8");
              const contentType = String(response.headers["content-type"] ?? "");
              const status = response.statusCode ?? 0;
              try {
                if (status < 200 || status >= 300) {
                  throw Object.assign(new Error("desktop_invocation_failed"), {
                    code: "desktop_invocation_failed",
                  });
                }
                if (contentType.includes("text/event-stream")) {
                  const parsed = readSseText(body);
                  if (parsed.text.length === 0) {
                    throw Object.assign(new Error("desktop_provider_stream_incomplete"), {
                      code: "desktop_provider_stream_incomplete",
                    });
                  }
                  resolve({
                    text: parsed.text,
                    actualModel: parsed.model,
                    inputTokens: parsed.usage.inputTokens,
                    outputTokens: parsed.usage.outputTokens,
                    totalTokens: parsed.usage.totalTokens,
                  });
                  return;
                }
                resolve(parseJsonCompletion(body));
              } catch (error) {
                reject(error);
              }
            });
          },
        );
        const onAbort = () => {
          req.destroy();
          reject(
            Object.assign(new Error("desktop_invocation_cancelled"), {
              code: "desktop_invocation_cancelled",
            }),
          );
        };
        signal.addEventListener("abort", onAbort, { once: true });
        req.on("error", () => {
          signal.removeEventListener("abort", onAbort);
          reject(
            Object.assign(new Error("desktop_invocation_failed"), {
              code: "desktop_invocation_failed",
            }),
          );
        });
        req.on("timeout", () => {
          req.destroy();
          reject(
            Object.assign(new Error("desktop_invocation_failed"), {
              code: "desktop_invocation_failed",
            }),
          );
        });
        req.write(payload);
        req.end();
      });
    },
  };
}
