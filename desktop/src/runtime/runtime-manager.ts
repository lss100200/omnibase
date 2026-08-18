import { randomBytes, timingSafeEqual } from "node:crypto";
import path from "node:path";

import type { RuntimeStatus } from "../shared/ipc-contract.ts";
import { verifyRuntimeBundle } from "./manifest.ts";
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
  instanceToken: string,
  dataRoot: string,
  hostEnvironment: Readonly<Record<string, string | undefined>> = process.env,
): Readonly<Record<string, string>> {
  if (!/^[a-f0-9]{64}$/u.test(instanceToken) || !path.isAbsolute(dataRoot)) {
    throw new Error("runtime_environment_invalid");
  }
  const environment: Record<string, string> = {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_BIND_HOST: "127.0.0.1",
    OMNIBASE_DESKTOP_INSTANCE_TOKEN: instanceToken,
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

export function matchesRuntimeInstanceToken(
  actual: string | null,
  expected: string,
): boolean {
  if (
    actual === null ||
    !/^[a-f0-9]{64}$/u.test(actual) ||
    !/^[a-f0-9]{64}$/u.test(expected)
  ) {
    return false;
  }
  return timingSafeEqual(Buffer.from(actual, "hex"), Buffer.from(expected, "hex"));
}

export class RuntimeManager {
  readonly #options: RuntimeManagerOptions;
  #supervisor: RuntimeSupervisor | null = null;
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

  async start(): Promise<RuntimeStatus> {
    this.#status = Object.freeze({
      phase: "starting",
      attempts: 0,
      lastError: null,
    });
    try {
      const bundle = await verifyRuntimeBundle({
        bundleRoot: this.#options.runtimeRoot,
        manifestPath: path.join(this.#options.runtimeRoot, "runtime-manifest.json"),
        expectedManifestSha256: this.#options.expectedManifestSha256,
      });
      const instanceToken = randomBytes(32).toString("hex");
      this.#supervisor = new RuntimeSupervisor({
        command: bundle.command,
        args: bundle.args,
        cwd: bundle.root,
        environment: buildRuntimeEnvironment(
          instanceToken,
          this.#options.dataRoot,
          this.#options.hostEnvironment,
        ),
        readinessProbe: async () => {
          const response = await fetch(`${this.#options.uiOrigin}/health`, {
            method: "GET",
            cache: "no-store",
            redirect: "error",
            signal: AbortSignal.timeout(1_000),
          });
          return (
            response.ok &&
            matchesRuntimeInstanceToken(
              response.headers.get("x-omnibase-desktop-instance"),
              instanceToken,
            )
          );
        },
      });
      this.#status = await this.#supervisor.start();
    } catch (error) {
      this.#status = Object.freeze({
        phase: "failed",
        attempts: 0,
        lastError: redactRuntimeError(error, [this.#options.runtimeRoot]),
      });
    }
    return this.#status;
  }

  stop(): RuntimeStatus {
    this.#status = this.#supervisor?.stop() ?? Object.freeze({
      phase: "stopped",
      attempts: this.#status.attempts,
      lastError: null,
    });
    this.#supervisor = null;
    return this.#status;
  }
}
