export const IPC_CHANNELS = Object.freeze({
  appGetVersion: "omnibase:app:get-version",
  runtimeGetStatus: "omnibase:runtime:get-status",
  runtimeRetryStartup: "omnibase:runtime:retry-startup",
  ownerGetStatus: "omnibase:owner:get-status",
  ownerBootstrap: "omnibase:owner:bootstrap",
  workspacesList: "omnibase:workspaces:list",
  workspacesCreate: "omnibase:workspaces:create",
  workspacesArchive: "omnibase:workspaces:archive",
} as const);

export type RuntimePhase = "stopped" | "starting" | "ready" | "failed";

export interface RuntimeStatus {
  readonly phase: RuntimePhase;
  readonly attempts: number;
  readonly lastError: string | null;
}

export interface DesktopOwner {
  readonly id: string;
  readonly displayName: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopOwnerStatus {
  readonly initialized: boolean;
  readonly owner: DesktopOwner | null;
}

export interface DesktopOwnerBootstrapResult extends DesktopOwnerStatus {
  readonly initialized: true;
  readonly created: boolean;
  readonly owner: DesktopOwner;
}

export type DesktopWorkspaceState = "active" | "archived";

export interface DesktopWorkspace {
  readonly id: string;
  readonly ownerId: string;
  readonly name: string;
  readonly state: DesktopWorkspaceState;
  readonly rowVersion: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopWorkspaceList {
  readonly items: readonly DesktopWorkspace[];
}

export interface DesktopWorkspaceMutationResult {
  readonly workspace: DesktopWorkspace;
}

export type DesktopOperationResult<T> =
  | Readonly<{ ok: true; value: T }>
  | Readonly<{ ok: false; error: Readonly<{ code: string }> }>;

export interface DesktopOwnerBootstrapInput {
  readonly displayName: string;
}

export interface DesktopWorkspaceCreateInput {
  readonly name: string;
}

export interface DesktopWorkspaceArchiveInput {
  readonly workspaceId: string;
  readonly expectedRowVersion: number;
}

export interface OmniBaseDesktopApi {
  readonly app: {
    readonly getVersion: () => Promise<string>;
  };
  readonly runtime: {
    readonly getStatus: () => Promise<RuntimeStatus>;
    readonly retryStartup: () => Promise<RuntimeStatus>;
  };
  readonly owner: {
    readonly getStatus: () => Promise<
      DesktopOperationResult<DesktopOwnerStatus>
    >;
    readonly bootstrap: (
      input: DesktopOwnerBootstrapInput,
    ) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>;
  };
  readonly workspaces: {
    readonly list: () => Promise<DesktopOperationResult<DesktopWorkspaceList>>;
    readonly create: (
      input: DesktopWorkspaceCreateInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
    readonly archive: (
      input: DesktopWorkspaceArchiveInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
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
