import { contextBridge, ipcRenderer } from "electron";

import type {
  DesktopAgentRole,
  DesktopAgentRoleIdInput,
  DesktopAgentRoleList,
  DesktopAgentRoleTestResult,
  DesktopAgentRoleUpdateInput,
  DesktopApplicationPreference,
  DesktopApplicationPreferenceUpdateInput,
  DesktopConversationArchiveInput,
  DesktopConversationCancelInput,
  DesktopConversationCreateInput,
  DesktopConversationDetail,
  DesktopConversationEvent,
  DesktopConversationGetInput,
  DesktopConversationList,
  DesktopConversationSendInput,
  DesktopConversation,
  DesktopOperationResult,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopParentAgent,
  DesktopProviderIdInput,
  DesktopProviderList,
  DesktopProviderMutationResult,
  DesktopProviderTestResult,
  DesktopProviderUpsertInput,
  DesktopTeamCollaborationInput,
  DesktopTeamCollaborationRequest,
  DesktopTeamRun,
  DesktopTeamRunAppendBudgetInput,
  DesktopTeamRunEvent,
  DesktopTeamRunExecuteInput,
  DesktopTeamRunIdInput,
  DesktopTeamRunProof,
  DesktopTeamRunProposalResult,
  DesktopTeamRunStartInput,
  DesktopTeamRunSubmitProposalInput,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCompositionAssistantProposalInput,
  DesktopWorkspaceCompositionDecisionInput,
  DesktopWorkspaceCompositionDecisionResult,
  DesktopWorkspaceCompositionOwnerProposalInput,
  DesktopWorkspaceCompositionProposalResult,
  DesktopWorkspaceCompositionRollbackProposalInput,
  DesktopWorkspaceCompositionSnapshot,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceFileAuthorization,
  DesktopWorkspaceFileAuthorizeInput,
  DesktopWorkspaceFileList,
  DesktopWorkspaceFileListInput,
  DesktopWorkspaceFileReadInput,
  DesktopWorkspaceFileReadResult,
  DesktopWorkspaceFileReleaseInput,
  DesktopWorkspaceIdInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
  OmniBaseDesktopApi,
  PersonalTeamBlackboard,
  RuntimeStatus,
} from "./shared/ipc-contract.ts";

const PRELOAD_IPC_CHANNELS = Object.freeze({
  appGetVersion: "omnibase:app:get-version",
  runtimeGetStatus: "omnibase:runtime:get-status",
  runtimeRetryStartup: "omnibase:runtime:retry-startup",
  ownerGetStatus: "omnibase:owner:get-status",
  ownerBootstrap: "omnibase:owner:bootstrap",
  workspacesList: "omnibase:workspaces:list",
  workspacesCreate: "omnibase:workspaces:create",
  workspacesArchive: "omnibase:workspaces:archive",
  workspaceAgent: "omnibase:workspace:agent",
  workbenchSettingsGet: "omnibase:workbench-settings:get",
  workbenchSettingsUpdate: "omnibase:workbench-settings:update",
  workspaceCompositionGet: "omnibase:workspace-composition:get",
  workspaceCompositionPropose: "omnibase:workspace-composition:propose",
  workspaceCompositionProposeFromAssistant:
    "omnibase:workspace-composition:propose-from-assistant",
  workspaceCompositionProposeRollback:
    "omnibase:workspace-composition:propose-rollback",
  workspaceCompositionDecide: "omnibase:workspace-composition:decide",
  workspaceFilesAuthorize: "omnibase:workspace-files:authorize",
  workspaceFilesRelease: "omnibase:workspace-files:release",
  workspaceFilesList: "omnibase:workspace-files:list",
  workspaceFilesRead: "omnibase:workspace-files:read",
  providersList: "omnibase:providers:list",
  providersUpsert: "omnibase:providers:upsert",
  providersDelete: "omnibase:providers:delete",
  providersTest: "omnibase:providers:test",
  conversationsList: "omnibase:conversations:list",
  conversationsCreate: "omnibase:conversations:create",
  conversationsArchive: "omnibase:conversations:archive",
  conversationsGet: "omnibase:conversations:get",
  conversationSend: "omnibase:conversation:send",
  conversationCancel: "omnibase:conversation:cancel",
  conversationAbortInFlightSend: "omnibase:conversation:abort-in-flight-send",
  agentsRolesList: "omnibase:agents:roles:list",
  agentsRolesGet: "omnibase:agents:roles:get",
  agentsRolesUpdate: "omnibase:agents:roles:update",
  agentsRolesTest: "omnibase:agents:roles:test",
  teamRunsStart: "omnibase:team-runs:start",
  teamRunsCancel: "omnibase:team-runs:cancel",
  teamRunsGet: "omnibase:team-runs:get",
  teamRunsList: "omnibase:team-runs:list",
  teamRunsSubmitProposal: "omnibase:team-runs:submit-proposal",
  teamRunsGetBlackboard: "omnibase:team-runs:get-blackboard",
  teamRunsRecordCollaboration: "omnibase:team-runs:record-collaboration",
  teamRunsExecute: "omnibase:team-runs:execute",
  teamRunsAppendBudget: "omnibase:team-runs:append-budget",
} as const);

const CONVERSATION_EVENT = "omnibase:conversation:event";
const TEAM_RUN_EVENT = "omnibase:team-runs:event";

const api: OmniBaseDesktopApi = Object.freeze({
  app: Object.freeze({
    getVersion: (): Promise<string> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.appGetVersion) as Promise<string>,
  }),
  runtime: Object.freeze({
    getStatus: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.runtimeGetStatus,
      ) as Promise<RuntimeStatus>,
    retryStartup: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.runtimeRetryStartup,
      ) as Promise<RuntimeStatus>,
  }),
  owner: Object.freeze({
    getStatus: (): Promise<DesktopOperationResult<DesktopOwnerStatus>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.ownerGetStatus) as Promise<
        DesktopOperationResult<DesktopOwnerStatus>
      >,
    bootstrap: (
      input: DesktopOwnerBootstrapInput,
    ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.ownerBootstrap, input) as Promise<
        DesktopOperationResult<DesktopOwnerBootstrapResult>
      >,
  }),
  workspaces: Object.freeze({
    list: (): Promise<DesktopOperationResult<DesktopWorkspaceList>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.workspacesList) as Promise<
        DesktopOperationResult<DesktopWorkspaceList>
      >,
    create: (
      input: DesktopWorkspaceCreateInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspacesCreate,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>,
    archive: (
      input: DesktopWorkspaceArchiveInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspacesArchive,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>,
    agent: (
      input: DesktopWorkspaceIdInput,
    ): Promise<
      DesktopOperationResult<{ readonly agent: DesktopParentAgent }>
    > =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.workspaceAgent, input) as Promise<
        DesktopOperationResult<{ readonly agent: DesktopParentAgent }>
      >,
  }),
  workbenchSettings: Object.freeze({
    get: (): Promise<
      DesktopOperationResult<{
        readonly preference: DesktopApplicationPreference;
      }>
    > =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.workbenchSettingsGet) as Promise<
        DesktopOperationResult<{
          readonly preference: DesktopApplicationPreference;
        }>
      >,
    update: (
      input: DesktopApplicationPreferenceUpdateInput,
    ): Promise<
      DesktopOperationResult<{
        readonly preference: DesktopApplicationPreference;
      }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workbenchSettingsUpdate,
        input,
      ) as Promise<
        DesktopOperationResult<{
          readonly preference: DesktopApplicationPreference;
        }>
      >,
  }),
  workspaceComposition: Object.freeze({
    get: (
      input: DesktopWorkspaceIdInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceCompositionSnapshot>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceCompositionGet,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceCompositionSnapshot>>,
    propose: (
      input: DesktopWorkspaceCompositionOwnerProposalInput,
    ): Promise<
      DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceCompositionPropose,
        input,
      ) as Promise<
        DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
      >,
    proposeFromAssistant: (
      input: DesktopWorkspaceCompositionAssistantProposalInput,
    ): Promise<
      DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceCompositionProposeFromAssistant,
        input,
      ) as Promise<
        DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
      >,
    proposeRollback: (
      input: DesktopWorkspaceCompositionRollbackProposalInput,
    ): Promise<
      DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceCompositionProposeRollback,
        input,
      ) as Promise<
        DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
      >,
    decide: (
      input: DesktopWorkspaceCompositionDecisionInput,
    ): Promise<
      DesktopOperationResult<DesktopWorkspaceCompositionDecisionResult>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceCompositionDecide,
        input,
      ) as Promise<
        DesktopOperationResult<DesktopWorkspaceCompositionDecisionResult>
      >,
  }),
  workspaceFiles: Object.freeze({
    authorize: (
      input: DesktopWorkspaceFileAuthorizeInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceFileAuthorization>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceFilesAuthorize,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceFileAuthorization>>,
    release: (
      input: DesktopWorkspaceFileReleaseInput,
    ): Promise<DesktopOperationResult<{ readonly released: true }>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceFilesRelease,
        input,
      ) as Promise<DesktopOperationResult<{ readonly released: true }>>,
    list: (
      input: DesktopWorkspaceFileListInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceFileList>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceFilesList,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceFileList>>,
    read: (
      input: DesktopWorkspaceFileReadInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceFileReadResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspaceFilesRead,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceFileReadResult>>,
  }),
  providers: Object.freeze({
    list: (): Promise<DesktopOperationResult<DesktopProviderList>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.providersList) as Promise<
        DesktopOperationResult<DesktopProviderList>
      >,
    upsert: (
      input: DesktopProviderUpsertInput,
    ): Promise<DesktopOperationResult<DesktopProviderMutationResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.providersUpsert,
        input,
      ) as Promise<DesktopOperationResult<DesktopProviderMutationResult>>,
    delete: (
      input: DesktopProviderIdInput,
    ): Promise<
      DesktopOperationResult<{ readonly deleted: true; readonly id: string }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.providersDelete,
        input,
      ) as Promise<
        DesktopOperationResult<{ readonly deleted: true; readonly id: string }>
      >,
    test: (
      input: DesktopProviderIdInput,
    ): Promise<DesktopOperationResult<DesktopProviderTestResult>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.providersTest, input) as Promise<
        DesktopOperationResult<DesktopProviderTestResult>
      >,
  }),
  conversations: Object.freeze({
    list: (
      input: DesktopWorkspaceIdInput,
    ): Promise<DesktopOperationResult<DesktopConversationList>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationsList,
        input,
      ) as Promise<DesktopOperationResult<DesktopConversationList>>,
    create: (
      input: DesktopConversationCreateInput,
    ): Promise<
      DesktopOperationResult<{
        readonly created: true;
        readonly conversation: DesktopConversation;
      }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationsCreate,
        input,
      ) as Promise<
        DesktopOperationResult<{
          readonly created: true;
          readonly conversation: DesktopConversation;
        }>
      >,
    archive: (
      input: DesktopConversationArchiveInput,
    ): Promise<
      DesktopOperationResult<{ readonly conversation: DesktopConversation }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationsArchive,
        input,
      ) as Promise<
        DesktopOperationResult<{ readonly conversation: DesktopConversation }>
      >,
    get: (
      input: DesktopConversationGetInput,
    ): Promise<DesktopOperationResult<DesktopConversationDetail>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationsGet,
        input,
      ) as Promise<DesktopOperationResult<DesktopConversationDetail>>,
    send: (
      input: DesktopConversationSendInput,
    ): Promise<DesktopOperationResult<DesktopConversationEvent>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationSend,
        input,
      ) as Promise<DesktopOperationResult<DesktopConversationEvent>>,
    cancel: (
      input: DesktopConversationCancelInput,
    ): Promise<
      DesktopOperationResult<{
        readonly cancelled: boolean;
        readonly id: string;
        readonly accepted: boolean;
      }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationCancel,
        input,
      ) as Promise<
        DesktopOperationResult<{
          readonly cancelled: boolean;
          readonly id: string;
          readonly accepted: boolean;
        }>
      >,
    abortInFlightSend: (): Promise<
      DesktopOperationResult<{ readonly aborted: boolean }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.conversationAbortInFlightSend,
      ) as Promise<DesktopOperationResult<{ readonly aborted: boolean }>>,
    subscribe: (listener: (event: DesktopConversationEvent) => void) => {
      const wrapped = (_event: unknown, payload: DesktopConversationEvent) => {
        listener(payload);
      };
      ipcRenderer.on(CONVERSATION_EVENT, wrapped);
      return () => {
        ipcRenderer.removeListener(CONVERSATION_EVENT, wrapped);
      };
    },
  }),
  agents: Object.freeze({
    roles: Object.freeze({
      list: (
        input: DesktopWorkspaceIdInput,
      ): Promise<DesktopOperationResult<DesktopAgentRoleList>> =>
        ipcRenderer.invoke(
          PRELOAD_IPC_CHANNELS.agentsRolesList,
          input,
        ) as Promise<DesktopOperationResult<DesktopAgentRoleList>>,
      get: (
        input: DesktopAgentRoleIdInput,
      ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> =>
        ipcRenderer.invoke(
          PRELOAD_IPC_CHANNELS.agentsRolesGet,
          input,
        ) as Promise<
          DesktopOperationResult<{ readonly role: DesktopAgentRole }>
        >,
      update: (
        input: DesktopAgentRoleUpdateInput,
      ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> =>
        ipcRenderer.invoke(
          PRELOAD_IPC_CHANNELS.agentsRolesUpdate,
          input,
        ) as Promise<
          DesktopOperationResult<{ readonly role: DesktopAgentRole }>
        >,
      test: (
        input: DesktopAgentRoleIdInput,
      ): Promise<DesktopOperationResult<DesktopAgentRoleTestResult>> =>
        ipcRenderer.invoke(
          PRELOAD_IPC_CHANNELS.agentsRolesTest,
          input,
        ) as Promise<DesktopOperationResult<DesktopAgentRoleTestResult>>,
    }),
  }),
  teamRuns: Object.freeze({
    start: (
      input: DesktopTeamRunStartInput,
    ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.teamRunsStart, input) as Promise<
        DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>
      >,
    cancel: (
      input: DesktopTeamRunIdInput,
    ): Promise<
      DesktopOperationResult<{
        readonly cancelled: boolean;
        readonly accepted: boolean;
        readonly teamRun: DesktopTeamRun;
      }>
    > =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.teamRunsCancel, input) as Promise<
        DesktopOperationResult<{
          readonly cancelled: boolean;
          readonly accepted: boolean;
          readonly teamRun: DesktopTeamRun;
        }>
      >,
    get: (
      input: DesktopTeamRunIdInput,
    ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.teamRunsGet, input) as Promise<
        DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>
      >,
    list: (
      input: DesktopWorkspaceIdInput,
    ): Promise<
      DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>
    > =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.teamRunsList, input) as Promise<
        DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>
      >,
    submitProposal: (
      input: DesktopTeamRunSubmitProposalInput,
    ): Promise<DesktopOperationResult<DesktopTeamRunProposalResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.teamRunsSubmitProposal,
        input,
      ) as Promise<DesktopOperationResult<DesktopTeamRunProposalResult>>,
    getBlackboard: (
      input: DesktopTeamRunIdInput,
    ): Promise<
      DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.teamRunsGetBlackboard,
        input,
      ) as Promise<
        DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>
      >,
    recordCollaboration: (
      input: DesktopTeamCollaborationInput,
    ): Promise<
      DesktopOperationResult<{
        readonly collaborationRequest: DesktopTeamCollaborationRequest;
      }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.teamRunsRecordCollaboration,
        input,
      ) as Promise<
        DesktopOperationResult<{
          readonly collaborationRequest: DesktopTeamCollaborationRequest;
        }>
      >,
    execute: (
      input: DesktopTeamRunExecuteInput,
    ): Promise<
      DesktopOperationResult<{ readonly proof: DesktopTeamRunProof }>
    > =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.teamRunsExecute,
        input,
      ) as Promise<
        DesktopOperationResult<{ readonly proof: DesktopTeamRunProof }>
      >,
    appendBudget: (
      input: DesktopTeamRunAppendBudgetInput,
    ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.teamRunsAppendBudget,
        input,
      ) as Promise<
        DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>
      >,
    subscribe: (listener: (event: DesktopTeamRunEvent) => void) => {
      const wrapped = (_event: unknown, payload: DesktopTeamRunEvent) => {
        listener(payload);
      };
      ipcRenderer.on(TEAM_RUN_EVENT, wrapped);
      return () => {
        ipcRenderer.removeListener(TEAM_RUN_EVENT, wrapped);
      };
    },
  }),
});

contextBridge.exposeInMainWorld("omnibaseDesktop", api);
