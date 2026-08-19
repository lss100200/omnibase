import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { lstat, mkdir } from "node:fs/promises";
import path from "node:path";

import type {
  DesktopOperationResult,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
  RuntimeStatus,
} from "../shared/ipc-contract.ts";
import { verifyRuntimeBundle } from "./manifest.ts";
import { DesktopNativeClient } from "./native-client.ts";
import { redactRuntimeError, RuntimeSupervisor } from "./supervisor.ts";

export interface RuntimeManagerOptions {
  readonly runtimeRoot: string;
  readonly expectedManifestSha256: string;
  readonly uiOrigin: string;
  readonly dataRoot: string;
  readonly hostEnvironment?: Readonly<Record<string, string | undefined>>;
}

const SAFE_HOST_ENVIRONMENT_KEYS = Object.freeze([
  "SystemRoot",
  "WINDIR",
  "TEMP",
  "TMP",
] as const);

export function buildRuntimeEnvironment(
  nativeProofKey: string,
  nativeControlToken: string,
  dataRoot: string,
  hostEnvironment: Readonly<Record<string, string | undefined>> = process.env,
): Readonly<Record<string, string>> {
  if (
    !/^[a-f0-9]{64}$/u.test(nativeProofKey) ||
    !/^[a-f0-9]{64}$/u.test(nativeControlToken) ||
    !path.isAbsolute(dataRoot)
  ) {
    throw new Error("runtime_environment_invalid");
  }
  const environment: Record<string, string> = {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_BIND_HOST: "127.0.0.1",
    OMNIBASE_DESKTOP_NATIVE_PROOF_KEY: nativeProofKey,
    OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN: nativeControlToken,
    OMNIBASE_DESKTOP_DATA_ROOT: dataRoot,
  };
  for (const key of SAFE_HOST_ENVIRONMENT_KEYS) {
    const value = hostEnvironment[key];
    if (
      typeof value === "string" &&
      value.length > 0 &&
      value.length <= 32_767 &&
      !value.includes("\0") &&
      !value.includes("\r") &&
      !value.includes("\n")
    ) {
      environment[key] = value;
    }
  }
  return Object.freeze(environment);
}

export function matchesRuntimeInstanceProof(
  actual: string | null,
  challenge: string,
  instanceToken: string,
): boolean {
  if (
    actual === null ||
    !/^[a-f0-9]{64}$/u.test(actual) ||
    !/^[a-f0-9]{64}$/u.test(challenge) ||
    !/^[a-f0-9]{64}$/u.test(instanceToken)
  ) {
    return false;
  }
  const expected = createHmac("sha256", Buffer.from(instanceToken, "hex"))
    .update(challenge, "ascii")
    .digest();
  return timingSafeEqual(Buffer.from(actual, "hex"), expected);
}

export async function prepareRuntimeDataRoot(dataRoot: string): Promise<void> {
  if (
    !path.isAbsolute(dataRoot) ||
    path.normalize(dataRoot) === path.parse(dataRoot).root
  ) {
    throw new Error("runtime_data_root_invalid");
  }
  try {
    await mkdir(dataRoot, { recursive: false, mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
      throw new Error("runtime_data_root_unavailable");
    }
  }
  try {
    const metadata = await lstat(dataRoot);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
      throw new Error("runtime_data_root_identity_invalid");
    }
  } catch (error) {
    if (
      error instanceof Error &&
      error.message === "runtime_data_root_identity_invalid"
    ) {
      throw error;
    }
    throw new Error("runtime_data_root_unavailable");
  }
}

export class RuntimeManager {
  readonly #options: RuntimeManagerOptions;
  #supervisor: RuntimeSupervisor | null = null;
  #nativeClient: DesktopNativeClient | null = null;
  #generation = 0;
  #startOperation: {
    readonly generation: number;
    readonly promise: Promise<RuntimeStatus>;
  } | null = null;
  #status: RuntimeStatus = Object.freeze({
    phase: "stopped",
    attempts: 0,
    lastError: null,
  });

  constructor(options: RuntimeManagerOptions) {
    if (!path.isAbsolute(options.dataRoot)) {
      throw new Error("runtime_data_root_must_be_absolute");
    }
    this.#options = options;
  }

  getStatus(): RuntimeStatus {
    return this.#supervisor?.getStatus() ?? this.#status;
  }

  getOwnerStatus(): Promise<DesktopOperationResult<DesktopOwnerStatus>> {
    const client = this.#readyNativeClient();
    return (
      client?.getOwnerStatus() ??
      Promise.resolve(this.#nativeUnavailable<DesktopOwnerStatus>())
    );
  }

  bootstrapOwner(
    input: DesktopOwnerBootstrapInput,
  ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.bootstrapOwner(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopOwnerBootstrapResult>())
    );
  }

  listWorkspaces(): Promise<DesktopOperationResult<DesktopWorkspaceList>> {
    const client = this.#readyNativeClient();
    return (
      client?.listWorkspaces() ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceList>())
    );
  }

  createWorkspace(
    input: DesktopWorkspaceCreateInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.createWorkspace(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceMutationResult>())
    );
  }

  archiveWorkspace(
    input: DesktopWorkspaceArchiveInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.archiveWorkspace(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceMutationResult>())
    );
  }

  start(): Promise<RuntimeStatus> {
    if (this.#supervisor?.getStatus().phase === "ready") {
      return Promise.resolve(this.#supervisor.getStatus());
    }
    if (
      this.#startOperation !== null &&
      this.#startOperation.generation === this.#generation
    ) {
      return this.#startOperation.promise;
    }
    const generation = ++this.#generation;
    const promise = this.#start(generation).finally(() => {
      if (this.#startOperation?.generation === generation) {
        this.#startOperation = null;
      }
    });
    this.#startOperation = { generation, promise };
    return promise;
  }

  async #start(generation: number): Promise<RuntimeStatus> {
    this.#nativeClient = null;
    this.#status = Object.freeze({
      phase: "starting",
      attempts: 0,
      lastError: null,
    });
    try {
      const bundle = await verifyRuntimeBundle({
        bundleRoot: this.#options.runtimeRoot,
        manifestPath: path.join(
          this.#options.runtimeRoot,
          "runtime-manifest.json",
        ),
        expectedManifestSha256: this.#options.expectedManifestSha256,
      });
      if (generation !== this.#generation) return this.#status;
      await prepareRuntimeDataRoot(this.#options.dataRoot);
      if (generation !== this.#generation) return this.#status;
      const nativeProofKey = randomBytes(32).toString("hex");
      const nativeControlToken = randomBytes(32).toString("hex");
      const supervisor = new RuntimeSupervisor({
        command: bundle.command,
        args: bundle.args,
        cwd: bundle.root,
        environment: buildRuntimeEnvironment(
          nativeProofKey,
          nativeControlToken,
          this.#options.dataRoot,
          this.#options.hostEnvironment,
        ),
        readinessProbe: async () => {
          const challenge = randomBytes(32).toString("hex");
          const response = await fetch(`${this.#options.uiOrigin}/health`, {
            method: "GET",
            headers: {
              "x-omnibase-desktop-challenge": challenge,
            },
            cache: "no-store",
            redirect: "error",
            signal: AbortSignal.timeout(1_000),
          });
          return (
            response.ok &&
            matchesRuntimeInstanceProof(
              response.headers.get("x-omnibase-desktop-proof"),
              challenge,
              nativeProofKey,
            )
          );
        },
        startupTimeoutMs: bundle.startupTimeoutMs,
      });
      if (generation !== this.#generation) {
        supervisor.stop();
        return this.#status;
      }
      this.#supervisor = supervisor;
      const status = await supervisor.start();
      if (generation !== this.#generation) {
        supervisor.stop();
        return this.#status;
      }
      this.#status = status;
      if (status.phase === "ready") {
        this.#nativeClient = new DesktopNativeClient({
          backendOrigin: `http://127.0.0.1:${bundle.backendPort}`,
          nativeControlToken,
        });
      }
    } catch (error) {
      if (generation !== this.#generation) return this.#status;
      this.#nativeClient = null;
      this.#status = Object.freeze({
        phase: "failed",
        attempts: 0,
        lastError: redactRuntimeError(error, [this.#options.runtimeRoot]),
      });
    }
    return this.#status;
  }

  stop(): RuntimeStatus {
    this.#generation += 1;
    this.#nativeClient = null;
    this.#status =
      this.#supervisor?.stop() ??
      Object.freeze({
        phase: "stopped",
        attempts: this.#status.attempts,
        lastError: null,
      });
    this.#supervisor = null;
    return this.#status;
  }

  #nativeUnavailable<T>(): DesktopOperationResult<T> {
    return Object.freeze({
      ok: false,
      error: Object.freeze({ code: "desktop_runtime_not_ready" }),
    });
  }

  #readyNativeClient(): DesktopNativeClient | null {
    if (this.#supervisor?.getStatus().phase !== "ready") {
      this.#nativeClient = null;
      return null;
    }
    return this.#nativeClient;
  }
}
