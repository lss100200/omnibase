import type { ChildProcessWithoutNullStreams, SpawnOptionsWithoutStdio } from "node:child_process";
import { spawn } from "node:child_process";
import path from "node:path";
import type { Readable } from "node:stream";

import type { RuntimeStatus } from "../shared/ipc-contract.ts";

const MAX_CAPTURED_OUTPUT = 4096;
const MAX_PUBLIC_ERROR = 512;

export interface RuntimeChild {
  readonly stdout: Pick<Readable, "on">;
  readonly stderr: Pick<Readable, "on">;
  readonly killed: boolean;
  once(event: "error", listener: (error: Error) => void): this;
  once(
    event: "exit",
    listener: (code: number | null, signal: NodeJS.Signals | null) => void,
  ): this;
  kill(): boolean;
}

export type SpawnRuntime = (
  command: string,
  args: readonly string[],
  options: SpawnOptionsWithoutStdio & { readonly stdio: "pipe" },
) => RuntimeChild;

export interface RuntimeSupervisorOptions {
  readonly command: string;
  readonly args?: readonly string[];
  readonly cwd: string;
  readonly environment: Readonly<Record<string, string>>;
  readonly readinessProbe: () => Promise<boolean>;
  readonly maxAttempts?: number;
  readonly startupTimeoutMs?: number;
  readonly probeIntervalMs?: number;
  readonly retryDelayMs?: number;
}

export interface RuntimeSupervisorDependencies {
  readonly spawnRuntime?: SpawnRuntime;
  readonly now?: () => number;
  readonly sleep?: (delayMs: number) => Promise<void>;
}

function appendBounded(current: string, value: unknown): string {
  const next = current + String(value);
  return next.slice(-MAX_CAPTURED_OUTPUT);
}

export function redactRuntimeError(
  value: unknown,
  sensitiveValues: readonly string[] = [],
): string {
  let output = value instanceof Error ? value.message : String(value);
  output = output
    .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/giu, "Bearer [REDACTED]")
    .replace(
      /\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+/giu,
      "$1=[REDACTED]",
    )
    .replace(/([a-z][a-z0-9+.-]*:\/\/)[^/@\s]+@/giu, "$1[REDACTED]@");
  for (const sensitive of sensitiveValues) {
    if (sensitive.length > 0) {
      output = output.split(sensitive).join("[PATH]");
    }
  }
  output = output.replace(/[\r\n\t]+/gu, " ").trim();
  return output.slice(0, MAX_PUBLIC_ERROR) || "runtime_start_failed";
}

function defaultSpawnRuntime(
  command: string,
  args: readonly string[],
  options: SpawnOptionsWithoutStdio & { readonly stdio: "pipe" },
): RuntimeChild {
  return spawn(command, [...args], options) as ChildProcessWithoutNullStreams;
}

function defaultSleep(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

export class RuntimeSupervisor {
  readonly #options: Required<
    Pick<
      RuntimeSupervisorOptions,
      "maxAttempts" | "startupTimeoutMs" | "probeIntervalMs" | "retryDelayMs"
    >
  > &
    Omit<
      RuntimeSupervisorOptions,
      "maxAttempts" | "startupTimeoutMs" | "probeIntervalMs" | "retryDelayMs"
    >;
  readonly #spawnRuntime: SpawnRuntime;
  readonly #now: () => number;
  readonly #sleep: (delayMs: number) => Promise<void>;
  #child: RuntimeChild | null = null;
  #status: RuntimeStatus = Object.freeze({
    phase: "stopped",
    attempts: 0,
    lastError: null,
  });
  #startPromise: Promise<RuntimeStatus> | null = null;

  #sensitiveErrorValues(): readonly string[] {
    return [
      this.#options.command,
      this.#options.cwd,
      ...Object.values(this.#options.environment).filter((value) => value.length >= 8),
    ];
  }

  constructor(
    options: RuntimeSupervisorOptions,
    dependencies: RuntimeSupervisorDependencies = {},
  ) {
    if (!path.isAbsolute(options.command) || !path.isAbsolute(options.cwd)) {
      throw new Error("runtime_command_and_cwd_must_be_absolute");
    }
    if (options.command.includes("\0") || options.cwd.includes("\0")) {
      throw new Error("runtime_path_invalid");
    }
    const maxAttempts = options.maxAttempts ?? 3;
    const startupTimeoutMs = options.startupTimeoutMs ?? 30_000;
    const probeIntervalMs = options.probeIntervalMs ?? 250;
    const retryDelayMs = options.retryDelayMs ?? 500;
    if (
      !Number.isInteger(maxAttempts) ||
      maxAttempts < 1 ||
      maxAttempts > 3 ||
      startupTimeoutMs < 1 ||
      startupTimeoutMs > 120_000 ||
      probeIntervalMs < 1 ||
      retryDelayMs < 0
    ) {
      throw new Error("runtime_retry_policy_invalid");
    }
    this.#options = {
      ...options,
      args: Object.freeze([...(options.args ?? [])]),
      environment: Object.freeze({ ...options.environment }),
      maxAttempts,
      startupTimeoutMs,
      probeIntervalMs,
      retryDelayMs,
    };
    this.#spawnRuntime = dependencies.spawnRuntime ?? defaultSpawnRuntime;
    this.#now = dependencies.now ?? Date.now;
    this.#sleep = dependencies.sleep ?? defaultSleep;
  }

  getStatus(): RuntimeStatus {
    return this.#status;
  }

  start(): Promise<RuntimeStatus> {
    if (this.#status.phase === "ready") {
      return Promise.resolve(this.#status);
    }
    if (this.#startPromise !== null) {
      return this.#startPromise;
    }
    this.#startPromise = this.#runStartLoop().finally(() => {
      this.#startPromise = null;
    });
    return this.#startPromise;
  }

  stop(): RuntimeStatus {
    this.#stopCurrentChild();
    this.#status = Object.freeze({
      phase: "stopped",
      attempts: this.#status.attempts,
      lastError: null,
    });
    return this.#status;
  }

  async #runStartLoop(): Promise<RuntimeStatus> {
    let finalError = "runtime_start_failed";
    for (let attempt = 1; attempt <= this.#options.maxAttempts; attempt += 1) {
      this.#status = Object.freeze({
        phase: "starting",
        attempts: attempt,
        lastError: null,
      });
      try {
        await this.#startAttempt();
        this.#status = Object.freeze({
          phase: "ready",
          attempts: attempt,
          lastError: null,
        });
        return this.#status;
      } catch (error) {
        finalError = redactRuntimeError(error, this.#sensitiveErrorValues());
        this.#stopCurrentChild();
        if (attempt < this.#options.maxAttempts) {
          await this.#sleep(this.#options.retryDelayMs);
        }
      }
    }
    this.#status = Object.freeze({
      phase: "failed",
      attempts: this.#options.maxAttempts,
      lastError: finalError,
    });
    return this.#status;
  }

  async #startAttempt(): Promise<void> {
    let stderr = "";
    let stdout = "";
    let childFailure: Error | null = null;
    let lastProbeError: Error | null = null;
    const child = this.#spawnRuntime(this.#options.command, this.#options.args ?? [], {
      cwd: this.#options.cwd,
      env: { ...this.#options.environment },
      shell: false,
      windowsHide: true,
      stdio: "pipe",
    });
    this.#child = child;
    child.stdout.on("data", (chunk) => {
      stdout = appendBounded(stdout, chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr = appendBounded(stderr, chunk);
    });
    child.once("error", (error) => {
      if (this.#child === child && this.#status.phase === "ready") {
        this.#child = null;
        this.#status = Object.freeze({
          phase: "failed",
          attempts: this.#status.attempts,
          lastError: redactRuntimeError(error, this.#sensitiveErrorValues()),
        });
      } else {
        childFailure = error;
      }
    });
    child.once("exit", (code, signal) => {
      const error = new Error(
        `runtime_exited_before_ready code=${String(code)} signal=${String(signal)}`,
      );
      if (this.#child === child && this.#status.phase === "ready") {
        this.#child = null;
        this.#status = Object.freeze({
          phase: "failed",
          attempts: this.#status.attempts,
          lastError: redactRuntimeError(error),
        });
      } else {
        childFailure = error;
      }
    });

    const deadline = this.#now() + this.#options.startupTimeoutMs;
    while (this.#now() < deadline) {
      if (childFailure !== null) {
        throw childFailure;
      }
      try {
        if (await this.#options.readinessProbe()) {
          return;
        }
      } catch (error) {
        // Connection-refused and similar probe failures are expected while a
        // freshly spawned local runtime is still binding its loopback port.
        // Only an actual child error/exit aborts the attempt immediately;
        // readiness errors remain bounded by startupTimeoutMs.
        lastProbeError = error instanceof Error ? error : new Error(String(error));
      }
      await this.#sleep(this.#options.probeIntervalMs);
    }
    throw new Error(
      `runtime_readiness_timeout probe=${lastProbeError?.name ?? "not_ready"} stderr=${stderr} stdout=${stdout}`,
    );
  }

  #stopCurrentChild(): void {
    const child = this.#child;
    this.#child = null;
    if (child !== null && !child.killed) {
      child.kill();
    }
  }
}
