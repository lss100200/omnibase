import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, realpath } from "node:fs/promises";
import path from "node:path";

import type {
  DesktopWorkspaceComponentExecutionTicket,
  DesktopWorkspaceComponentJsonValue,
  DesktopWorkspaceComponentLifecycleTicket,
} from "../shared/ipc-contract.ts";
import type {
  TrustedSandboxComponentAdapter,
  TrustedSandboxWorkload,
} from "./component-runtime-broker.ts";

const SHA256 = /^[a-f0-9]{64}$/u;
const COMPONENT_ID = /^[a-z][a-z0-9_.-]{1,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const LOGICAL_ID = /^[a-z][a-z0-9_.:-]{0,127}$/u;
const RUNTIME_INSTANCE_ID = /^runtime_[a-f0-9]{32}$/u;
const WORKLOAD_ID = "bounded-transform";
const MAX_ARTIFACTS = 32;
const MAX_WORKLOAD_BYTES = 32 * 1024;
const DEFAULT_TIMEOUT_MS = 5_000;
const DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024;
const MAX_RUNTIME_FILE_BYTES = 256 * 1024 * 1024;
const NODE_ARGUMENTS = Object.freeze([
  "--max-old-space-size=32",
  "--max-semi-space-size=1",
  "--stack-size=256",
  "--no-addons",
  "--permission",
]);
const HELPER_RELATIVE_PATH = "component-host/p34-sandbox-helper.js";
const NODE_RELATIVE_PATH =
  process.platform === "win32" ? "node/node.exe" : "node/node";

type AdapterOutput = DesktopWorkspaceComponentJsonValue;

export class P34SandboxAdapterError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

interface ActiveChild {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly child: ChildProcessWithoutNullStreams;
  readonly settled: Promise<void>;
  readonly terminate: (code: string) => void;
}

export interface P34SandboxComponentAdapterOptions {
  readonly runtimeRoot: string;
  readonly getVerifiedRuntimeFileSha256: (
    relativePath: string,
  ) => string | null;
  readonly executableRelativePath?: string;
  readonly helperRelativePath?: string;
  readonly timeoutMs?: number;
  readonly maxOutputBytes?: number;
  readonly spawnProcess?: typeof spawn;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exact(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function safeInteger(value: unknown, maximum: number): value is number {
  return (
    Number.isSafeInteger(value) &&
    Number(value) >= 0 &&
    Number(value) <= maximum
  );
}

function samePath(left: string, right: string): boolean {
  return process.platform === "win32"
    ? path.normalize(left).toLowerCase() === path.normalize(right).toLowerCase()
    : path.normalize(left) === path.normalize(right);
}

async function digestFile(file: string): Promise<string> {
  const digest = createHash("sha256");
  const stream = createReadStream(file);
  for await (const chunk of stream) digest.update(chunk);
  return digest.digest("hex");
}

async function verifyOrdinaryRuntimeFile(
  runtimeRoot: string,
  relativePath: string,
  expectedSha256: string,
): Promise<string> {
  if (!SHA256.test(expectedSha256)) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_runtime_unavailable",
    );
  }
  const target = path.resolve(runtimeRoot, ...relativePath.split("/"));
  const relative = path.relative(runtimeRoot, target);
  if (
    relative.startsWith("..") ||
    path.isAbsolute(relative) ||
    relative.length === 0
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_runtime_unavailable",
    );
  }
  let metadata;
  let resolved;
  try {
    [metadata, resolved] = await Promise.all([lstat(target), realpath(target)]);
  } catch {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_runtime_unavailable",
    );
  }
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    metadata.nlink !== 1 ||
    metadata.size <= 0 ||
    metadata.size > MAX_RUNTIME_FILE_BYTES ||
    !samePath(target, resolved) ||
    (await digestFile(target)) !== expectedSha256
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_runtime_unavailable",
    );
  }
  return target;
}

function validateWorkload(workload: TrustedSandboxWorkload): Buffer {
  const bytes = Buffer.from(workload.bytes);
  if (
    workload.entrypoint !== "transform" ||
    workload.memoryMaxBytes !== 64 * 1024 ||
    workload.network !== "no_imports" ||
    !SHA256.test(workload.sha256) ||
    bytes.byteLength < 8 ||
    bytes.byteLength > MAX_WORKLOAD_BYTES ||
    createHash("sha256").update(bytes).digest("hex") !== workload.sha256
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_workload_invalid",
    );
  }
  let module: WebAssembly.Module;
  try {
    module = new WebAssembly.Module(bytes);
  } catch {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_workload_invalid",
    );
  }
  if (
    WebAssembly.Module.imports(module).length !== 0 ||
    JSON.stringify(WebAssembly.Module.exports(module)) !==
      JSON.stringify([{ name: "transform", kind: "function" }])
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_workload_invalid",
    );
  }
  return bytes;
}

function workloadRequest(
  workload: TrustedSandboxWorkload,
  bytes: Buffer,
): Readonly<Record<string, unknown>> {
  return Object.freeze({
    entrypoint: workload.entrypoint,
    memory_max_bytes: workload.memoryMaxBytes,
    network: workload.network,
    workload_base64: bytes.toString("base64"),
    workload_sha256: workload.sha256,
  });
}

function parseProbe(
  value: unknown,
  expectedWorkloadSha256: string,
): AdapterOutput {
  if (
    !isRecord(value) ||
    !exact(value, ["adapter", "isolation", "schema_version", "status"]) ||
    value.adapter !== "p34-sandbox.v1" ||
    value.schema_version !== 1 ||
    value.status !== "ready" ||
    !isRecord(value.isolation) ||
    !exact(value.isolation, [
      "execution",
      "host_capabilities",
      "memory_max_bytes",
      "network",
      "workload_sha256",
    ]) ||
    value.isolation.execution !== "package_bound_zero_import_webassembly" ||
    value.isolation.host_capabilities !== "none" ||
    value.isolation.memory_max_bytes !== 64 * 1024 ||
    value.isolation.network !== "no_imports" ||
    typeof value.isolation.workload_sha256 !== "string" ||
    value.isolation.workload_sha256 !== expectedWorkloadSha256
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_response_invalid",
    );
  }
  return value as AdapterOutput;
}

function parseCompleted(
  value: unknown,
  ticket: DesktopWorkspaceComponentExecutionTicket,
  workloadId: string,
  workloadSha256: string,
  inputArtifactIds: readonly string[],
): AdapterOutput {
  if (
    !isRecord(value) ||
    !exact(value, ["binding", "output"]) ||
    !isRecord(value.binding) ||
    !exact(value.binding, [
      "network_fencing_token",
      "operation_id",
      "request_sha256",
      "runtime_instance_id",
      "workload_fencing_token",
      "workspace_id",
    ]) ||
    value.binding.network_fencing_token !== ticket.networkFencingToken ||
    value.binding.operation_id !== ticket.operationId ||
    value.binding.request_sha256 !== ticket.requestSha256 ||
    value.binding.runtime_instance_id !== ticket.runtimeInstanceId ||
    value.binding.workload_fencing_token !== ticket.workloadFencingToken ||
    value.binding.workspace_id !== ticket.workspaceId
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_response_invalid",
    );
  }
  const output = value.output;
  if (
    !isRecord(output) ||
    !exact(output, [
      "adapter",
      "component_id",
      "input_artifact_ids",
      "result",
      "runtime_instance_id",
      "schema_version",
      "status",
      "usage",
      "workload_id",
      "workload_sha256",
    ]) ||
    output.adapter !== "p34-sandbox.v1" ||
    output.component_id !== ticket.componentId ||
    output.runtime_instance_id !== ticket.runtimeInstanceId ||
    output.schema_version !== 1 ||
    output.status !== "completed" ||
    output.workload_id !== workloadId ||
    output.workload_sha256 !== workloadSha256 ||
    !Array.isArray(output.input_artifact_ids) ||
    output.input_artifact_ids.length !== inputArtifactIds.length ||
    output.input_artifact_ids.some(
      (item, index) => item !== inputArtifactIds[index],
    ) ||
    !isRecord(output.result) ||
    !exact(output.result, [
      "artifact_count",
      "fingerprint_sha256",
      "kind",
      "transform_value",
    ]) ||
    output.result.kind !== "artifact_inventory" ||
    output.result.artifact_count !== inputArtifactIds.length ||
    typeof output.result.fingerprint_sha256 !== "string" ||
    !SHA256.test(output.result.fingerprint_sha256) ||
    !Number.isInteger(output.result.transform_value) ||
    Number(output.result.transform_value) < -2_147_483_648 ||
    Number(output.result.transform_value) > 2_147_483_647 ||
    !isRecord(output.usage) ||
    !exact(output.usage, ["bytes_in", "bytes_out", "wall_time_ms"]) ||
    !safeInteger(output.usage.bytes_in, 64 * 1024) ||
    !safeInteger(output.usage.bytes_out, 64 * 1024) ||
    !safeInteger(output.usage.wall_time_ms, 60_000)
  ) {
    throw new P34SandboxAdapterError(
      "desktop_component_sandbox_response_invalid",
    );
  }
  return output as AdapterOutput;
}

function validateExecutionInput(
  ticket: DesktopWorkspaceComponentExecutionTicket,
  workloadId: string,
  inputArtifactIds: readonly string[],
): void {
  if (
    ticket.adapterId !== "p34-sandbox.v1" ||
    !COMPONENT_ID.test(ticket.componentId) ||
    !VERSION.test(ticket.version) ||
    !RUNTIME_INSTANCE_ID.test(ticket.runtimeInstanceId) ||
    !SHA256.test(ticket.requestSha256) ||
    !Number.isSafeInteger(ticket.workloadFencingToken) ||
    ticket.workloadFencingToken < 1 ||
    ticket.networkFencingToken !== null ||
    workloadId !== WORKLOAD_ID ||
    inputArtifactIds.length > MAX_ARTIFACTS ||
    inputArtifactIds.some((item) => !LOGICAL_ID.test(item)) ||
    new Set(inputArtifactIds).size !== inputArtifactIds.length
  ) {
    throw new P34SandboxAdapterError("desktop_component_sandbox_input_invalid");
  }
}

export class P34SandboxComponentAdapter
  implements TrustedSandboxComponentAdapter
{
  readonly #options: Required<
    Pick<P34SandboxComponentAdapterOptions, "timeoutMs" | "maxOutputBytes">
  > &
    P34SandboxComponentAdapterOptions;
  #runtimeFiles: Promise<
    Readonly<{ executable: string; helper: string }>
  > | null = null;
  #active: ActiveChild | null = null;
  #reserved = false;

  constructor(options: P34SandboxComponentAdapterOptions) {
    if (
      !path.isAbsolute(options.runtimeRoot) ||
      !Number.isSafeInteger(options.timeoutMs ?? DEFAULT_TIMEOUT_MS) ||
      (options.timeoutMs ?? DEFAULT_TIMEOUT_MS) < 100 ||
      (options.timeoutMs ?? DEFAULT_TIMEOUT_MS) > 60_000 ||
      !Number.isSafeInteger(
        options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES,
      ) ||
      (options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES) < 1024 ||
      (options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES) > 1024 * 1024
    ) {
      throw new Error("p34_sandbox_adapter_options_invalid");
    }
    this.#options = Object.freeze({
      ...options,
      timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      maxOutputBytes: options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES,
    });
  }

  async preflight(
    workload: TrustedSandboxWorkload,
    signal: AbortSignal = new AbortController().signal,
  ): Promise<void> {
    const bytes = validateWorkload(workload);
    parseProbe(
      await this.#invokeHelper(
        Object.freeze({
          kind: "probe",
          schema_version: 1,
          ...workloadRequest(workload, bytes),
        }),
        "preflight",
        "p34-sandbox.v1",
        signal,
      ),
      workload.sha256,
    );
  }

  async activate(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentLifecycleTicket;
      workload: TrustedSandboxWorkload;
      signal: AbortSignal;
    }>,
  ): Promise<Readonly<{ health: "healthy"; evidence: AdapterOutput }>> {
    if (
      input.ticket.adapterId !== "p34-sandbox.v1" ||
      input.ticket.runtimeInstanceId === null ||
      !RUNTIME_INSTANCE_ID.test(input.ticket.runtimeInstanceId)
    ) {
      throw new P34SandboxAdapterError(
        "desktop_component_sandbox_input_invalid",
      );
    }
    await this.preflight(input.workload, input.signal);
    return Object.freeze({
      health: "healthy" as const,
      evidence: Object.freeze({
        adapter: "p34-sandbox.v1",
        component_id: input.ticket.componentId,
        isolation: "zero_import_webassembly",
        runtime_instance_id: input.ticket.runtimeInstanceId,
        status: "ready",
        workload_sha256: input.workload.sha256,
      }),
    });
  }

  async stop(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentLifecycleTicket;
      signal: AbortSignal;
    }>,
  ): Promise<Readonly<{ evidence: AdapterOutput }>> {
    if (input.signal.aborted) {
      throw new P34SandboxAdapterError("desktop_component_sandbox_cancelled");
    }
    const active = this.#active;
    if (
      active !== null &&
      active.workspaceId === input.ticket.workspaceId &&
      active.componentId === input.ticket.componentId
    ) {
      active.terminate("desktop_component_sandbox_stopped");
      let stopTimer: NodeJS.Timeout | null = null;
      try {
        await Promise.race([
          active.settled,
          new Promise<void>((_resolve, reject) => {
            stopTimer = setTimeout(
              () =>
                reject(
                  new P34SandboxAdapterError(
                    "desktop_component_sandbox_kill_failed",
                  ),
                ),
              1_000,
            );
          }),
        ]);
      } finally {
        if (stopTimer !== null) clearTimeout(stopTimer);
      }
    }
    return Object.freeze({
      evidence: Object.freeze({
        adapter: "p34-sandbox.v1",
        component_id: input.ticket.componentId,
        status: "stopped",
      }),
    });
  }

  async execute(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentExecutionTicket;
      workloadId: string;
      workload: TrustedSandboxWorkload;
      inputArtifactIds: readonly string[];
      signal: AbortSignal;
    }>,
  ): Promise<AdapterOutput> {
    validateExecutionInput(
      input.ticket,
      input.workloadId,
      input.inputArtifactIds,
    );
    const workloadBytes = validateWorkload(input.workload);
    const response = await this.#invokeHelper(
      Object.freeze({
        component_id: input.ticket.componentId,
        component_version: input.ticket.version,
        input_artifact_ids: Object.freeze([...input.inputArtifactIds]),
        kind: "run",
        network_fencing_token: input.ticket.networkFencingToken,
        operation_id: input.ticket.operationId,
        request_sha256: input.ticket.requestSha256,
        runtime_instance_id: input.ticket.runtimeInstanceId,
        schema_version: 1,
        workload_fencing_token: input.ticket.workloadFencingToken,
        workload_id: WORKLOAD_ID,
        workspace_id: input.ticket.workspaceId,
        ...workloadRequest(input.workload, workloadBytes),
      }),
      input.ticket.workspaceId,
      input.ticket.componentId,
      input.signal,
    );
    return parseCompleted(
      response,
      input.ticket,
      input.workloadId,
      input.workload.sha256,
      input.inputArtifactIds,
    );
  }

  async #verifiedRuntimeFiles(): Promise<
    Readonly<{ executable: string; helper: string }>
  > {
    this.#runtimeFiles ??= (async () => {
      const executableRelativePath =
        this.#options.executableRelativePath ?? NODE_RELATIVE_PATH;
      const helperRelativePath =
        this.#options.helperRelativePath ?? HELPER_RELATIVE_PATH;
      const executableSha256 = this.#options.getVerifiedRuntimeFileSha256(
        executableRelativePath,
      );
      const helperSha256 =
        this.#options.getVerifiedRuntimeFileSha256(helperRelativePath);
      if (executableSha256 === null || helperSha256 === null) {
        throw new P34SandboxAdapterError(
          "desktop_component_sandbox_runtime_unavailable",
        );
      }
      return Object.freeze({
        executable: await verifyOrdinaryRuntimeFile(
          this.#options.runtimeRoot,
          executableRelativePath,
          executableSha256,
        ),
        helper: await verifyOrdinaryRuntimeFile(
          this.#options.runtimeRoot,
          helperRelativePath,
          helperSha256,
        ),
      });
    })().catch((error: unknown) => {
      this.#runtimeFiles = null;
      throw error;
    });
    return await this.#runtimeFiles;
  }

  async #invokeHelper(
    request: Readonly<Record<string, unknown>>,
    workspaceId: string,
    componentId: string,
    signal: AbortSignal,
  ): Promise<unknown> {
    if (signal.aborted) {
      throw new P34SandboxAdapterError("desktop_component_sandbox_cancelled");
    }
    if (this.#active !== null || this.#reserved) {
      throw new P34SandboxAdapterError(
        "desktop_component_sandbox_concurrency_exceeded",
      );
    }
    this.#reserved = true;
    let runtime: Readonly<{ executable: string; helper: string }>;
    try {
      runtime = await this.#verifiedRuntimeFiles();
      if (signal.aborted) {
        throw new P34SandboxAdapterError("desktop_component_sandbox_cancelled");
      }
      if (this.#active !== null) {
        throw new P34SandboxAdapterError(
          "desktop_component_sandbox_concurrency_exceeded",
        );
      }
    } finally {
      this.#reserved = false;
    }
    const spawnProcess = this.#options.spawnProcess ?? spawn;
    const child = spawnProcess(
      runtime.executable,
      [
        ...NODE_ARGUMENTS,
        `--allow-fs-read=${runtime.helper}`,
        runtime.helper,
        "--p7-sandbox-helper",
      ],
      {
        cwd: this.#options.runtimeRoot,
        detached: false,
        env: Object.freeze({}),
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    const rawRequest = `${JSON.stringify(request)}\n`;
    if (Buffer.byteLength(rawRequest, "utf8") > 64 * 1024) {
      child.kill();
      throw new P34SandboxAdapterError(
        "desktop_component_sandbox_input_invalid",
      );
    }

    return await new Promise<unknown>((resolve, reject) => {
      let stdout = Buffer.alloc(0);
      let stderr = Buffer.alloc(0);
      let completed = false;
      let failureCode: string | null = null;
      let killTimer: NodeJS.Timeout | null = null;
      let settleActive: () => void = () => {};
      const settled = new Promise<void>((settle) => {
        settleActive = settle;
      });
      const terminate = (code: string): void => {
        failureCode ??= code;
        if (!child.killed) child.kill();
        killTimer ??= setTimeout(() => {
          finish(
            new P34SandboxAdapterError("desktop_component_sandbox_kill_failed"),
          );
        }, 1_000);
      };
      const active: ActiveChild = Object.freeze({
        workspaceId,
        componentId,
        child,
        settled,
        terminate,
      });
      this.#active = active;

      const finish = (
        error: P34SandboxAdapterError | null,
        value?: unknown,
      ): void => {
        if (completed) return;
        completed = true;
        clearTimeout(timer);
        if (killTimer !== null) clearTimeout(killTimer);
        signal.removeEventListener("abort", onAbort);
        if (this.#active === active) this.#active = null;
        settleActive();
        if (error === null) resolve(value);
        else reject(error);
      };
      const append = (current: Buffer, chunk: Buffer | string): Buffer => {
        const bytes = Buffer.isBuffer(chunk)
          ? chunk
          : Buffer.from(chunk, "utf8");
        if (
          current.byteLength + bytes.byteLength >
          this.#options.maxOutputBytes
        ) {
          terminate("desktop_component_sandbox_output_limit_exceeded");
          return current;
        }
        return Buffer.concat(
          [current, bytes],
          current.byteLength + bytes.byteLength,
        );
      };
      const onAbort = (): void => {
        terminate("desktop_component_sandbox_cancelled");
      };
      const timer = setTimeout(
        () => terminate("desktop_component_sandbox_timeout"),
        this.#options.timeoutMs,
      );
      signal.addEventListener("abort", onAbort, { once: true });
      child.stdout.on("data", (chunk: Buffer | string) => {
        stdout = append(stdout, chunk);
      });
      child.stderr.on("data", (chunk: Buffer | string) => {
        stderr = append(stderr, chunk);
      });
      child.once("error", () => {
        finish(
          new P34SandboxAdapterError(
            failureCode ?? "desktop_component_sandbox_start_failed",
          ),
        );
      });
      child.once("close", (code, childSignal) => {
        if (failureCode !== null) {
          finish(new P34SandboxAdapterError(failureCode));
          return;
        }
        if (
          code !== 0 ||
          childSignal !== null ||
          stderr.byteLength !== 0 ||
          stdout.byteLength === 0 ||
          stdout.includes(0) ||
          stdout[stdout.byteLength - 1] !== 0x0a ||
          stdout.subarray(0, stdout.byteLength - 1).includes(0x0a)
        ) {
          finish(
            new P34SandboxAdapterError(
              "desktop_component_sandbox_response_invalid",
            ),
          );
          return;
        }
        let decoded: unknown;
        try {
          decoded = JSON.parse(
            stdout.subarray(0, stdout.byteLength - 1).toString("utf8"),
          );
        } catch {
          finish(
            new P34SandboxAdapterError(
              "desktop_component_sandbox_response_invalid",
            ),
          );
          return;
        }
        finish(null, decoded);
      });
      child.stdin.once("error", () => {
        terminate("desktop_component_sandbox_input_failed");
      });
      child.stdin.end(rawRequest, "utf8");
    });
  }
}

export const P34_SANDBOX_RUNTIME_FILES = Object.freeze({
  executable: NODE_RELATIVE_PATH,
  helper: HELPER_RELATIVE_PATH,
});
