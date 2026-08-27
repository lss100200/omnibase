import type { AgentModelSettingRead, P6EmployeeRoleId } from './types'

export interface ModelSettingsScope {
  readonly key: string | null
  readonly generation: number
}

export interface ModelSettingsProjection {
  readonly scope: ModelSettingsScope
  readonly items: AgentModelSettingRead[]
}

export interface ModelSettingPreparationSnapshot {
  readonly loadedKey: string
  readonly scopeGeneration: number
  readonly employeeRoleId: P6EmployeeRoleId
  readonly settingIdentity: string
}

export function modelSettingsScopeKey(workspaceId: string, agentVersionId: string): string | null {
  if (!workspaceId || !agentVersionId) return null
  return JSON.stringify([workspaceId, agentVersionId])
}

export function advanceModelSettingsScope(
  current: ModelSettingsScope,
  key: string | null,
): ModelSettingsScope {
  return current.key === key ? current : { key, generation: current.generation + 1 }
}

export function projectionForScope(
  projection: ModelSettingsProjection | null,
  scope: ModelSettingsScope,
): ModelSettingsProjection | null {
  return projection?.scope.key === scope.key &&
    projection.scope.generation === scope.generation &&
    scope.key !== null
    ? projection
    : null
}

export function modelSettingIdentity(setting: AgentModelSettingRead): string {
  return JSON.stringify([
    setting.employee_role_id,
    setting.inherit_default,
    setting.override_credential_id,
    setting.requested_model_id,
    setting.effective_provider_id,
    setting.effective_model_id,
    setting.family,
    setting.family_source,
    setting.state,
    setting.test_status,
    setting.tested_at,
    setting.version,
  ])
}

export function captureModelSettingPreparation(
  projection: ModelSettingsProjection,
  employeeRoleId: P6EmployeeRoleId,
): ModelSettingPreparationSnapshot | null {
  if (projection.scope.key === null) return null
  const setting = projection.items.find((item) => item.employee_role_id === employeeRoleId)
  if (!setting) return null
  return {
    loadedKey: projection.scope.key,
    scopeGeneration: projection.scope.generation,
    employeeRoleId,
    settingIdentity: modelSettingIdentity(setting),
  }
}

export function modelSettingPreparationIsCurrent(
  snapshot: ModelSettingPreparationSnapshot,
  projection: ModelSettingsProjection | null,
  scope: ModelSettingsScope,
): boolean {
  const current = projectionForScope(projection, scope)
  if (
    !current ||
    current.scope.key !== snapshot.loadedKey ||
    current.scope.generation !== snapshot.scopeGeneration
  ) {
    return false
  }
  const setting = current.items.find((item) => item.employee_role_id === snapshot.employeeRoleId)
  return setting !== undefined && modelSettingIdentity(setting) === snapshot.settingIdentity
}
