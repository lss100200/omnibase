/**
 * UI-only admission for the exact personal single-Owner production canary.
 *
 * This helper never authorizes a request. It mirrors the server posture so the
 * workbench does not remain artificially locked after the server has already
 * revalidated the active canary, live Owner and exact Workspace scope.
 */

import {
  canInvokeLiteAgent,
  liteInvokeConditionsMet,
  type LiteInvokePosture,
} from './lite-gate'

export interface PersonalRuntimeInvokePosture extends LiteInvokePosture {
  readonly production_activation_allowed: boolean
  readonly runtime_profile: string
  readonly personal_runtime_state: string
  readonly personal_runtime_active: boolean
  readonly tools_enabled: boolean
  readonly multi_agent_enabled: boolean
  readonly supported_invocation_modes: readonly string[]
}

export function personalRuntimeInvokeConditionsMet(
  posture: PersonalRuntimeInvokePosture | null | undefined,
): boolean {
  if (!posture) return false
  return Boolean(
    posture.runtime_profile === 'personal_single_owner' &&
      posture.personal_runtime_state === 'active' &&
      posture.personal_runtime_active &&
      posture.production_activation_allowed &&
      !posture.tools_enabled &&
      !posture.multi_agent_enabled &&
      posture.supported_invocation_modes.length === 1 &&
      posture.supported_invocation_modes[0] === 'no_tool',
  )
}

export function agentInvokeConditionsMet(
  posture: PersonalRuntimeInvokePosture | null | undefined,
): boolean {
  return Boolean(
    liteInvokeConditionsMet(posture) || personalRuntimeInvokeConditionsMet(posture),
  )
}

export function canInvokeAgent(
  posture: PersonalRuntimeInvokePosture | null | undefined,
  input: string,
  workspaceId: string,
  bindingId: string,
): boolean {
  if (personalRuntimeInvokeConditionsMet(posture)) {
    return Boolean(input.trim().length > 0 && workspaceId.length > 0 && bindingId.length > 0)
  }
  return canInvokeLiteAgent(posture, input, workspaceId, bindingId)
}
