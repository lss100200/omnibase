export const IPC_CHANNELS = Object.freeze({
  appGetVersion: "omnibase:app:get-version",
  runtimeGetStatus: "omnibase:runtime:get-status",
  runtimeRetryStartup: "omnibase:runtime:retry-startup",
} as const);

export type RuntimePhase = "stopped" | "starting" | "ready" | "failed";

export interface RuntimeStatus {
  readonly phase: RuntimePhase;
  readonly attempts: number;
  readonly lastError: string | null;
}

export interface OmniBaseDesktopApi {
  readonly app: {
    readonly getVersion: () => Promise<string>;
  };
  readonly runtime: {
    readonly getStatus: () => Promise<RuntimeStatus>;
    readonly retryStartup: () => Promise<RuntimeStatus>;
  };
}

export const IPC_CHANNEL_SET: ReadonlySet<string> = new Set(
  Object.values(IPC_CHANNELS),
);

export function requireNoIpcArguments(args: readonly unknown[]): void {
  if (args.length !== 0) {
    throw new Error("ipc_arguments_not_allowed");
  }
}
