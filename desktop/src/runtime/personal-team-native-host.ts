import type { DesktopNativeClient } from "./native-client.ts";
import {
  decryptProviderSecret,
  type DesktopSafeStorage,
} from "./secret-vault.ts";
import type { PersonalTeamHost, TeamRoleCredentials } from "./personal-team-coordinator.ts";
import type { PersonalEmployeeId } from "../shared/personal-team.ts";

function unwrap<T>(result: { ok: true; value: T } | { ok: false; error: { code: string } }): T {
  if (!result.ok) {
    throw Object.assign(new Error(result.error.code), { code: result.error.code });
  }
  return result.value;
}

export function createNativePersonalTeamHost(options: {
  readonly client: DesktopNativeClient;
  readonly vault: DesktopSafeStorage;
}): PersonalTeamHost {
  return {
    async startTeamRun(input) {
      return unwrap(
        await options.client.startTeamRun({
          workspaceId: input.workspaceId,
          conversationId: input.conversationId,
          task: input.task,
          teamMode: true,
          budget: input.budget,
          allowedSpecialistRoleIds: input.allowedSpecialistRoleIds,
        }),
      );
    },
    async submitProposal(input) {
      return unwrap(await options.client.submitTeamProposal(input));
    },
    async getBlackboard(input) {
      return unwrap(await options.client.getTeamBlackboard(input));
    },
    async consumeProviderCall(input) {
      return unwrap(await options.client.consumeTeamProviderCall(input));
    },
    async settleParentCall(input) {
      return unwrap(await options.client.settleTeamParentCall(input));
    },
    async setRunState(input) {
      return unwrap(
        await options.client.setTeamRunState({
          workspaceId: input.workspaceId,
          teamRunId: input.teamRunId,
          state: input.state,
          parentFinalAnswer: input.parentFinalAnswer,
        }),
      );
    },
    async createNode(input) {
      return unwrap(await options.client.createTeamNode(input));
    },
    async updateNode(input) {
      unwrap(await options.client.updateTeamNode(input));
    },
    async settleNode(input) {
      unwrap(await options.client.settleTeamNode(input));
    },
    async recordReport(input) {
      unwrap(await options.client.recordTeamReport(input));
    },
    async resolveCollaboration(input) {
      unwrap(
        await options.client.resolveTeamCollaboration({
          workspaceId: input.workspaceId,
          teamRunId: input.teamRunId,
          requestId: input.requestId,
          parentDecision: input.parentDecision,
          resolvedAssignmentId: input.resolvedAssignmentId,
        }),
      );
    },
    async resolveCredentials(workspaceId, roleId: PersonalEmployeeId, signal) {
      if (signal.aborted) {
        throw Object.assign(new Error("desktop_invocation_cancelled"), {
          code: "desktop_invocation_cancelled",
        });
      }
      const role = unwrap(
        await options.client.getAgentRole({ workspaceId, roleId }),
      ).role;
      const listed = unwrap(await options.client.listProviders());
      const explicitId = role.providerId;
      const providerId = explicitId ?? role.resolvedProviderId;
      if (providerId === null || role.resolvedModelName === null) {
        throw Object.assign(new Error("desktop_role_provider_unresolved"), {
          code: "desktop_role_provider_unresolved",
        });
      }
      const provider = listed.items.find((item) => item.id === providerId);
      if (provider === undefined) {
        throw Object.assign(new Error("desktop_provider_not_found"), {
          code: "desktop_provider_not_found",
        });
      }
      if (!provider.isEnabled) {
        throw Object.assign(new Error("desktop_provider_disabled"), {
          code: "desktop_provider_disabled",
        });
      }
      if (explicitId !== null && explicitId !== provider.id) {
        throw Object.assign(new Error("desktop_provider_disabled"), {
          code: "desktop_provider_disabled",
        });
      }
      const material = unwrap(await options.client.getProviderVault(providerId));
      if (signal.aborted) {
        throw Object.assign(new Error("desktop_invocation_cancelled"), {
          code: "desktop_invocation_cancelled",
        });
      }
      const secret = decryptProviderSecret(material.encryptedSecretBlob, options.vault);
      const credentials: TeamRoleCredentials = {
        providerId,
        model: role.resolvedModelName,
        baseUrl: provider.baseUrl,
        secret,
        allowLoopbackHttp: provider.allowLoopbackHttp,
        timeoutMs: provider.timeoutSeconds * 1000,
      };
      return credentials;
    },
  };
}
