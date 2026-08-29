import type {
  DesktopWorkspaceComponentCatalogItem,
  DesktopWorkspaceComponentInstallation,
  DesktopWorkspaceComponentLifecycleAction,
} from './desktop-bridge'

export type P7WorkspaceComponentsLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

function p7NextWorkspaceComponentLifecycleAction(
  installation: Pick<DesktopWorkspaceComponentInstallation, 'state'>,
): DesktopWorkspaceComponentLifecycleAction | null {
  switch (installation.state) {
    case 'installed':
      return 'bind'
    case 'bound':
    case 'disabled':
      return 'activate'
    case 'active':
      return 'disable'
    case 'revoked':
      return 'uninstall'
    default:
      return null
  }
}

function p7SemanticVersionParts(version: string): readonly [number, number, number] | null {
  const matched = /^(\d+)\.(\d+)\.(\d+)$/u.exec(version)
  if (matched === null) return null
  const major = Number(matched[1])
  const minor = Number(matched[2])
  const patch = Number(matched[3])
  return [major, minor, patch].every(Number.isSafeInteger)
    ? Object.freeze([major, minor, patch])
    : null
}

export function p7WorkspaceComponentVersionChangeAction(
  currentVersion: string,
  targetVersion: string,
): 'upgrade' | 'rollback' | null {
  if (currentVersion === targetVersion) return null
  const current = p7SemanticVersionParts(currentVersion)
  const target = p7SemanticVersionParts(targetVersion)
  if (current === null || target === null) return null
  for (let index = 0; index < current.length; index += 1) {
    if (target[index]! > current[index]!) return 'upgrade'
    if (target[index]! < current[index]!) return 'rollback'
  }
  return null
}

export function p7WorkspaceComponentLifecycleActions(
  catalog: Pick<
    DesktopWorkspaceComponentCatalogItem,
    'available' | 'manifestSha256' | 'packageSha256' | 'version'
  >,
  installation: Pick<DesktopWorkspaceComponentInstallation, 'state' | 'version'> | null,
): readonly DesktopWorkspaceComponentLifecycleAction[] {
  if (!catalog.available || catalog.manifestSha256 === null || catalog.packageSha256 === null) {
    return Object.freeze([])
  }
  if (installation === null) return Object.freeze(['install'])
  const versionAction = p7WorkspaceComponentVersionChangeAction(
    installation.version,
    catalog.version,
  )
  if (versionAction !== null) return Object.freeze([versionAction])

  const actions: DesktopWorkspaceComponentLifecycleAction[] = []
  const next = p7NextWorkspaceComponentLifecycleAction(installation)
  if (next !== null) actions.push(next)
  if (
    installation.state === 'installed' ||
    installation.state === 'bound' ||
    installation.state === 'active' ||
    installation.state === 'disabled'
  ) {
    actions.push('revoke')
  }
  if (installation.state === 'disabled') actions.push('uninstall')
  return Object.freeze(actions)
}

export interface P7ComponentAssistantSnapshot {
  readonly workspaceId: string
  readonly catalog: readonly Readonly<{
    componentId: string
    version: string
    family: string
    policyManifestSha256: string
    manifestSha256: string | null
    packageSha256: string | null
    available: boolean
    operations: readonly string[]
    slots: readonly Readonly<{
      slotId: string
      cardinality: 'one' | 'many'
      minimumOrder: number
      maximumOrder: number
    }>[]
    dependencies: readonly Readonly<{
      componentId: string
      version: string
      policyManifestSha256: string
      manifestSha256: string
      packageSha256: string
    }>[]
    settingsSchema: unknown
    budgets: Readonly<{
      maxCalls: number
      maxBytesIn: number
      maxBytesOut: number
      maxTokens: number
      maxWallTimeMs: number
      maxCostUnits: number
    }>
    network: Readonly<{ required: boolean; serviceClasses: readonly string[] }>
  }>[]
  readonly installations: readonly Readonly<{
    componentId: string
    version: string
    state: string
    revision: number
    desiredConfiguration: unknown
    currentSlotBindings: readonly unknown[]
    dependencyGraph: readonly unknown[]
  }>[]
}

export interface P7CompletedAssistantMessage {
  readonly id: string
  readonly role: 'user' | 'assistant'
  readonly content: string
  readonly status: string
  readonly invocationId: string | null
  readonly invocation: Readonly<{ readonly id: string; readonly status: string }> | null
}

export function p7FindNewCompletedComponentAssistantMessage(
  messages: readonly P7CompletedAssistantMessage[],
  previousMessageIds: ReadonlySet<string>,
): P7CompletedAssistantMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (
      message !== undefined &&
      !previousMessageIds.has(message.id) &&
      message.role === 'assistant' &&
      message.status === 'completed' &&
      message.content.length >= 2 &&
      message.content.length <= 32_768 &&
      message.invocationId !== null &&
      message.invocation?.id === message.invocationId &&
      message.invocation.status === 'succeeded'
    ) {
      return message
    }
  }
  return null
}

export interface P7AssistantDeclarativePackageReview {
  readonly workspaceId: string
  readonly conversationId: string
  readonly messageId: string
  readonly componentId: string
  readonly version: string
  readonly publisherId: string
  readonly packageJson: string
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly slots: readonly string[]
  readonly sections: readonly Readonly<{
    id: string
    label: string
    source: 'installation' | 'health' | 'grants' | 'configuration'
  }>[]
}

const ASSISTANT_PACKAGE_MANIFEST_KEYS = Object.freeze([
  'budgets',
  'compatibility',
  'component_id',
  'configuration_schema',
  'conflicts',
  'dependencies',
  'entrypoint',
  'family',
  'health',
  'manifest_schema_version',
  'network',
  'operations',
  'permissions',
  'publisher',
  'quiesce_timeout_ms',
  'recovery',
  'slots',
  'state_migration',
  'state_schema',
  'uninstall',
  'version',
])
const ASSISTANT_PACKAGE_SLOTS = new Set([
  'editor.component',
  'sidebar.component',
  'settings.component',
  'status.component',
])
const ASSISTANT_PACKAGE_ID = /^[a-z][a-z0-9.-]{2,127}$/u
const ASSISTANT_PACKAGE_VERSION = /^\d+\.\d+\.\d+$/u
const ASSISTANT_PACKAGE_FIELD = /^[a-z][a-z0-9_]{0,63}$/u
const ASSISTANT_PACKAGE_FORBIDDEN_FIELD =
  /(?:^|_)(?:url|uri|path|command|argv|script|executable|api_key|password|credential|bearer|token|secret|private_key)(?:_|$)/u

function p7CanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(p7CanonicalJson).join(',')}]`
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${p7CanonicalJson(value[key])}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

async function p7Sha256Text(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function p7AssistantPackageHasForbiddenAuthority(value: unknown): boolean {
  if (typeof value === 'string') {
    return (
      /(?:javascript|<script|<iframe|:\/\/|@import|electron|node:|command|argv|physical[_-]?path|api[_-]?key|password|credential|bearer|private[_-]?key)/iu.test(
        value,
      ) ||
      /(?:^|[\s"'(])(?:[a-z]:[\\/]|\\\\|\/(?:etc|home|root|users?|var|tmp)(?:[\\/]|$))/iu.test(
        value,
      )
    )
  }
  if (Array.isArray(value)) return value.some(p7AssistantPackageHasForbiddenAuthority)
  if (!isRecord(value)) return false
  return Object.values(value).some(p7AssistantPackageHasForbiddenAuthority)
}

function p7AssistantPackageConfigurationValid(value: unknown): boolean {
  if (
    !exactRecord(value, ['additional_properties', 'kind', 'properties', 'required', 'version']) ||
    value.additional_properties !== false ||
    value.kind !== 'closed_object' ||
    !Number.isSafeInteger(value.version) ||
    Number(value.version) < 1 ||
    !isRecord(value.properties) ||
    Object.keys(value.properties).length > 32 ||
    !Array.isArray(value.required) ||
    value.required.some(
      (item) => typeof item !== 'string' || !ASSISTANT_PACKAGE_FIELD.test(item),
    ) ||
    new Set(value.required).size !== value.required.length
  ) {
    return false
  }
  const properties = value.properties as Record<string, unknown>
  for (const [name, specification] of Object.entries(properties)) {
    if (
      !ASSISTANT_PACKAGE_FIELD.test(name) ||
      ASSISTANT_PACKAGE_FORBIDDEN_FIELD.test(name) ||
      !isRecord(specification) ||
      !exactOptionalKeys(
        specification,
        ['type'],
        ['default', 'enum', 'minimum', 'maximum', 'max_length'],
      ) ||
      !['boolean', 'integer', 'number', 'string'].includes(String(specification.type)) ||
      (specification.enum !== undefined &&
        (!Array.isArray(specification.enum) ||
          specification.enum.length < 1 ||
          specification.enum.length > 32)) ||
      (specification.max_length !== undefined &&
        (!Number.isSafeInteger(specification.max_length) ||
          Number(specification.max_length) > 4096))
    ) {
      return false
    }
  }
  return value.required.every((item) => Object.hasOwn(properties, String(item)))
}

function p7AssistantPackageManifestValid(value: unknown): value is Record<string, unknown> {
  if (
    !exactRecord(value, ASSISTANT_PACKAGE_MANIFEST_KEYS) ||
    typeof value.component_id !== 'string' ||
    !ASSISTANT_PACKAGE_ID.test(value.component_id) ||
    value.component_id.startsWith('builtin.') ||
    typeof value.version !== 'string' ||
    !ASSISTANT_PACKAGE_VERSION.test(value.version) ||
    value.family !== 'declarative_ui' ||
    value.manifest_schema_version !== 1 ||
    !exactRecord(value.publisher, ['classification', 'id']) ||
    value.publisher.classification !== 'owner_reviewed' ||
    typeof value.publisher.id !== 'string' ||
    value.publisher.id.length < 3 ||
    value.publisher.id.length > 64 ||
    !exactRecord(value.entrypoint, ['adapter_id', 'kind']) ||
    value.entrypoint.adapter_id !== 'builtin-ui.v1' ||
    value.entrypoint.kind !== 'host_view_v1' ||
    !Array.isArray(value.operations) ||
    value.operations.length !== 1 ||
    value.operations[0] !== 'ui.render' ||
    !exactRecord(value.compatibility, ['desktop_schema_min', 'host_api']) ||
    value.compatibility.desktop_schema_min !== 11 ||
    value.compatibility.host_api !== 'p7.3.v1' ||
    !p7AssistantPackageConfigurationValid(value.configuration_schema) ||
    !Array.isArray(value.dependencies) ||
    value.dependencies.length !== 0 ||
    !Array.isArray(value.conflicts) ||
    value.conflicts.length !== 0 ||
    !Array.isArray(value.slots) ||
    value.slots.length < 1 ||
    value.slots.length > ASSISTANT_PACKAGE_SLOTS.size ||
    !exactRecord(value.budgets, [
      'max_bytes_in',
      'max_bytes_out',
      'max_calls',
      'max_concurrency',
      'max_cost_units',
      'max_retries',
      'max_tokens',
      'max_wall_time_ms',
    ]) ||
    Object.values(value.budgets).some((item) => !Number.isSafeInteger(item) || Number(item) < 0) ||
    Number(value.budgets.max_calls) < 1 ||
    Number(value.budgets.max_calls) > 100 ||
    Number(value.budgets.max_concurrency) < 1 ||
    Number(value.budgets.max_concurrency) > 4 ||
    Number(value.budgets.max_wall_time_ms) < 1 ||
    Number(value.budgets.max_wall_time_ms) > 60_000 ||
    Number(value.budgets.max_bytes_out) > 1_048_576 ||
    Number(value.budgets.max_tokens) > 32_768 ||
    !Array.isArray(value.permissions) ||
    value.permissions.length !== 1 ||
    !exactRecord(value.permissions[0], [
      'action',
      'data_scope',
      'logical_resource_classes',
      'secret_reference_classes',
    ]) ||
    value.permissions[0].action !== 'ui.render' ||
    !['none', 'workspace_logical'].includes(String(value.permissions[0].data_scope)) ||
    !Array.isArray(value.permissions[0].logical_resource_classes) ||
    value.permissions[0].logical_resource_classes.some((item) => typeof item !== 'string') ||
    !Array.isArray(value.permissions[0].secret_reference_classes) ||
    value.permissions[0].secret_reference_classes.length !== 0 ||
    !exactRecord(value.network, ['required', 'service_classes']) ||
    value.network.required !== false ||
    !Array.isArray(value.network.service_classes) ||
    value.network.service_classes.length !== 0 ||
    !exactRecord(value.health, ['kind', 'required_state', 'timeout_ms']) ||
    value.health.kind !== 'native_receipt_v1' ||
    value.health.required_state !== 'healthy' ||
    value.health.timeout_ms !== 5000 ||
    !exactRecord(value.recovery, ['auto_replay_unknown', 'retention', 'safe_mode']) ||
    value.recovery.auto_replay_unknown !== false ||
    value.recovery.retention !== 'retain_workspace_data' ||
    value.recovery.safe_mode !== 'disable_component' ||
    !exactRecord(value.state_schema, ['kind', 'version']) ||
    value.state_schema.kind !== 'canonical_json' ||
    !Number.isSafeInteger(value.state_schema.version) ||
    !exactRecord(value.state_migration, ['kind', 'requires_owner_review_on_schema_change']) ||
    value.state_migration.kind !== 'host_canonical_v1' ||
    value.state_migration.requires_owner_review_on_schema_change !== true ||
    !exactRecord(value.uninstall, ['retention', 'unbound_delete_forbidden']) ||
    value.uninstall.retention !== 'retain_workspace_data' ||
    value.uninstall.unbound_delete_forbidden !== true ||
    !Number.isSafeInteger(value.quiesce_timeout_ms) ||
    Number(value.quiesce_timeout_ms) < 1 ||
    Number(value.quiesce_timeout_ms) > 60_000 ||
    p7AssistantPackageHasForbiddenAuthority(value)
  ) {
    return false
  }
  const slots = new Set<string>()
  for (const slot of value.slots) {
    if (
      !exactRecord(slot, ['cardinality', 'maximum_order', 'minimum_order', 'slot_id']) ||
      typeof slot.slot_id !== 'string' ||
      !ASSISTANT_PACKAGE_SLOTS.has(slot.slot_id) ||
      slots.has(slot.slot_id) ||
      !['one', 'many'].includes(String(slot.cardinality)) ||
      !Number.isSafeInteger(slot.minimum_order) ||
      !Number.isSafeInteger(slot.maximum_order) ||
      Number(slot.minimum_order) < 0 ||
      Number(slot.maximum_order) > 10_000 ||
      Number(slot.minimum_order) > Number(slot.maximum_order)
    ) {
      return false
    }
    slots.add(slot.slot_id)
  }
  return true
}

export async function p7ParseAssistantDeclarativePackage(
  input: Readonly<{
    workspaceId: string
    conversationId: string
    message: P7CompletedAssistantMessage
  }>,
): Promise<P7AssistantDeclarativePackageReview | null> {
  if (
    input.workspaceId.length < 1 ||
    input.conversationId.length < 1 ||
    input.message.role !== 'assistant' ||
    input.message.status !== 'completed' ||
    input.message.invocationId === null ||
    input.message.invocation?.id !== input.message.invocationId ||
    input.message.invocation.status !== 'succeeded' ||
    input.message.content.length < 2 ||
    new TextEncoder().encode(input.message.content).byteLength > 32_768 ||
    input.message.content.includes('\0')
  ) {
    return null
  }
  let value: unknown
  try {
    value = JSON.parse(input.message.content)
  } catch {
    return null
  }
  if (
    !exactRecord(value, ['manifest', 'schema_version', 'view']) ||
    value.schema_version !== 1 ||
    !p7AssistantPackageManifestValid(value.manifest) ||
    !exactRecord(value.view, ['kind', 'sections', 'title']) ||
    value.view.kind !== 'workspace_summary' ||
    typeof value.view.title !== 'string' ||
    value.view.title.trim().length < 1 ||
    value.view.title.length > 128 ||
    !Array.isArray(value.view.sections) ||
    value.view.sections.length < 1 ||
    value.view.sections.length > 16 ||
    p7AssistantPackageHasForbiddenAuthority(value.view)
  ) {
    return null
  }
  const sections: Array<{
    id: string
    label: string
    source: 'installation' | 'health' | 'grants' | 'configuration'
  }> = []
  const sectionIds = new Set<string>()
  for (const section of value.view.sections) {
    if (
      !exactRecord(section, ['id', 'label', 'source']) ||
      typeof section.id !== 'string' ||
      !/^[a-z][a-z0-9._-]{1,63}$/u.test(section.id) ||
      sectionIds.has(section.id) ||
      typeof section.label !== 'string' ||
      section.label.trim().length < 1 ||
      section.label.length > 96 ||
      !['installation', 'health', 'grants', 'configuration'].includes(String(section.source))
    ) {
      return null
    }
    sectionIds.add(section.id)
    sections.push({
      id: section.id,
      label: section.label,
      source: section.source as P7AssistantDeclarativePackageReview['sections'][number]['source'],
    })
  }
  const packageJson = `${p7CanonicalJson(value)}\n`
  const manifest = value.manifest as Record<string, unknown>
  const publisher = manifest.publisher as Record<string, unknown>
  const slots = manifest.slots as Array<Record<string, unknown>>
  const manifestJson = p7CanonicalJson(manifest)
  const [manifestSha256, packageSha256] = await Promise.all([
    p7Sha256Text(manifestJson),
    p7Sha256Text(packageJson),
  ])
  return Object.freeze({
    workspaceId: input.workspaceId,
    conversationId: input.conversationId,
    messageId: input.message.id,
    componentId: manifest.component_id as string,
    version: manifest.version as string,
    publisherId: publisher.id as string,
    packageJson,
    manifestSha256,
    packageSha256,
    slots: Object.freeze(slots.map((slot) => slot.slot_id as string)),
    sections: Object.freeze(sections.map((section) => Object.freeze(section))),
  })
}

export function p7AssistantDeclarativePackagePrompt(intent: string): string | null {
  const normalized = intent.trim()
  if (normalized.length < 1 || normalized.length > 2_000) return null
  const prompt = [
    'Create one Owner-reviewable OmniBase declarative Workspace component package.',
    `Owner intent: ${normalized}`,
    'Return exactly one JSON object, with no markdown or prose. Top-level keys are exactly manifest, schema_version, view; schema_version is 1.',
    `manifest keys are exactly: ${ASSISTANT_PACKAGE_MANIFEST_KEYS.join(', ')}.`,
    'manifest family=declarative_ui; publisher={classification:"owner_reviewed",id:<3-64 chars>}; entrypoint={adapter_id:"builtin-ui.v1",kind:"host_view_v1"}; operations=["ui.render"]. component_id must not start builtin.; version is semantic x.y.z.',
    'compatibility={desktop_schema_min:11,host_api:"p7.3.v1"}; dependencies=[]; conflicts=[]; network={required:false,service_classes:[]}; permissions contains only ui.render and secret_reference_classes=[].',
    'Use 1-4 unique Slots only from editor.component, sidebar.component, settings.component, status.component. Each Slot has exactly cardinality, maximum_order, minimum_order, slot_id.',
    'configuration_schema is a closed_object with additional_properties=false and bounded boolean/integer/number/string properties only. Never define key, password, credential, command, path, URL, script or secret fields.',
    'health={kind:"native_receipt_v1",required_state:"healthy",timeout_ms:5000}; recovery={auto_replay_unknown:false,retention:"retain_workspace_data",safe_mode:"disable_component"}; state_schema={kind:"canonical_json",version:1}; state_migration={kind:"host_canonical_v1",requires_owner_review_on_schema_change:true}; uninstall={retention:"retain_workspace_data",unbound_delete_forbidden:true}.',
    'view={kind:"workspace_summary",title:<1-128 chars>,sections:[...]}; each section uses exactly id,label,source and source is installation, health, grants, or configuration.',
    'Do not include JavaScript, CSS, iframe, URL, path, command, argv, Electron/Node API, network, secret, token, key, credential, executable content, or unknown fields. Do not install, approve, grant, activate or execute anything.',
  ].join('\n')
  return prompt.length <= 16_384 ? prompt : null
}

export function p7WorkspaceComponentAssistantPrompt(
  intent: string,
  snapshot: P7ComponentAssistantSnapshot,
): string | null {
  const normalizedIntent = intent.trim()
  if (normalizedIntent.length < 1 || normalizedIntent.length > 2_000) return null
  const installations = new Map(
    snapshot.installations.map((installation) => [installation.componentId, installation]),
  )
  const catalog = snapshot.catalog
    .filter((item) => item.available && item.manifestSha256 !== null && item.packageSha256 !== null)
    .slice(0, 32)
    .map((item) => {
      const installation = installations.get(item.componentId)
      return {
        component_id: item.componentId,
        version: item.version,
        family: item.family,
        policy_manifest_sha256: item.policyManifestSha256,
        manifest_sha256: item.manifestSha256,
        package_sha256: item.packageSha256,
        operations: item.operations,
        slots: item.slots.map((slot) => ({
          slot_id: slot.slotId,
          cardinality: slot.cardinality,
          minimum_order: slot.minimumOrder,
          maximum_order: slot.maximumOrder,
        })),
        dependencies: item.dependencies.map((dependency) => ({
          component_id: dependency.componentId,
          version: dependency.version,
          policy_manifest_sha256: dependency.policyManifestSha256,
          manifest_sha256: dependency.manifestSha256,
          package_sha256: dependency.packageSha256,
        })),
        settings_schema: item.settingsSchema,
        budgets: item.budgets,
        network: item.network,
        current_installation:
          installation === undefined
            ? null
            : {
                version: installation.version,
                state: installation.state,
                revision: installation.revision,
                configuration: installation.desiredConfiguration,
                slot_bindings: installation.currentSlotBindings,
                dependency_graph: installation.dependencyGraph,
              },
      }
    })
  if (catalog.length === 0) return null
  const facts = JSON.stringify({ workspace_id: snapshot.workspaceId, catalog })
  const prompt = [
    'Create one Owner-reviewable OmniBase Workspace component proposal.',
    `Owner intent: ${normalizedIntent}`,
    `Trusted host facts: ${facts}`,
    'Return exactly one JSON object and no prose or markdown.',
    'Use exactly these keys: type, component_id, target_version, change_kind, expected_revision, policy_manifest_sha256, manifest_sha256, package_sha256, requested_grants, desired_configuration, desired_slot_bindings, dependency_graph.',
    'type must be omnibase.workspace-component.proposal.v1. Select one available catalog identity exactly. change_kind is install, bind, activate, disable, upgrade, rollback, revoke, or uninstall. expected_revision is 0 when current_installation is null, otherwise its exact revision.',
    'Each requested_grants item uses action, logical_resource_id, resource_version, logical_service_id, expires_in_seconds, maximum_invocations, maximum_bytes_in, maximum_bytes_out, maximum_tokens, maximum_wall_time_ms, maximum_cost_units. Stay within catalog budgets and use logical identifiers only.',
    'Each desired_slot_bindings item uses slot_id, binding_key, order_index, configuration. dependency_graph must exactly reproduce the selected catalog dependency identities. desired_configuration must satisfy settings_schema.',
    'Do not approve, install, execute, reconcile, invent a digest, add an unknown field, output a path, command, URL, secret, token, stdio, or process handle.',
  ].join('\n')
  return prompt.length <= 32_768 ? prompt : null
}

export interface P7WorkspaceComponentsProjection<TSnapshot> {
  readonly status: P7WorkspaceComponentsLoadStatus
  readonly snapshot: TSnapshot | null
}

export function p7WorkspaceComponentsProjection<TSnapshot extends { readonly workspaceId: string }>(
  input: Readonly<{
    loadedWorkspaceId: string | null
    viewWorkspaceId: string | null
    status: P7WorkspaceComponentsLoadStatus
    snapshot: TSnapshot | null
  }>,
): P7WorkspaceComponentsProjection<TSnapshot> {
  if (input.viewWorkspaceId === null || input.loadedWorkspaceId !== input.viewWorkspaceId) {
    return Object.freeze({
      status: input.viewWorkspaceId === null ? 'idle' : 'loading',
      snapshot: null,
    })
  }
  if (input.status !== 'ready') {
    return Object.freeze({ status: input.status, snapshot: null })
  }
  if (input.snapshot?.workspaceId !== input.viewWorkspaceId) {
    return Object.freeze({ status: 'error', snapshot: null })
  }
  return Object.freeze({ status: 'ready', snapshot: input.snapshot })
}

export type P7DeclarativeControl =
  | 'text'
  | 'multiline'
  | 'boolean'
  | 'integer'
  | 'number'
  | 'select'
  | 'secret-ref'
  | 'logical-resource-ref'

export interface P7DeclarativeOption {
  readonly value: string | number | boolean
  readonly label: string
}

export interface P7DeclarativeField {
  readonly id: string
  readonly label: string
  readonly description: string | null
  readonly control: P7DeclarativeControl
  readonly required: boolean
  readonly maxLength: number | null
  readonly minimum: number | null
  readonly maximum: number | null
  readonly step: number | null
  readonly options: readonly P7DeclarativeOption[]
  readonly defaultValue: P7DeclarativeValue | undefined
}

export interface P7DeclarativeSection {
  readonly id: string
  readonly label: string
  readonly description: string | null
  readonly fields: readonly P7DeclarativeField[]
}

export interface P7DeclarativeSettingsSchema {
  readonly schemaVersion: 1
  readonly sections: readonly P7DeclarativeSection[]
}

export type P7DeclarativeValue = string | number | boolean | null
export type P7DeclarativeSettings = Readonly<Record<string, P7DeclarativeValue>>

const DECLARATIVE_CONTROLS: ReadonlySet<string> = new Set([
  'text',
  'multiline',
  'boolean',
  'integer',
  'number',
  'select',
  'secret-ref',
  'logical-resource-ref',
])
const DECLARATIVE_ID = /^[a-z][a-z0-9_.-]{0,63}$/

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exactOptionalKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const keys = Object.keys(value)
  const allowed = new Set([...required, ...optional])
  return required.every((key) => Object.hasOwn(value, key)) && keys.every((key) => allowed.has(key))
}

function boundedText(value: unknown, maxLength: number): string | null {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength ? value : null
}

function optionalDescription(value: unknown): string | null | undefined {
  if (value === undefined || value === null) return null
  if (typeof value !== 'string' || value.length > 240) return undefined
  return value
}

function optionalInteger(value: unknown): number | null | undefined {
  if (value === undefined || value === null) return null
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : undefined
}

function parseOption(value: unknown): P7DeclarativeOption | null {
  if (!isRecord(value) || !exactOptionalKeys(value, ['value', 'label'], [])) return null
  const optionValue = boundedText(value.value, 128)
  const label = boundedText(value.label, 80)
  if (optionValue === null || label === null) return null
  return Object.freeze({ value: optionValue, label })
}

function parseField(value: unknown): P7DeclarativeField | null {
  if (
    !isRecord(value) ||
    !exactOptionalKeys(
      value,
      ['id', 'label', 'control', 'required'],
      ['description', 'maxLength', 'minimum', 'maximum', 'step', 'options'],
    )
  ) {
    return null
  }
  const id = boundedText(value.id, 64)
  const label = boundedText(value.label, 80)
  const description = optionalDescription(value.description)
  const control = value.control
  if (
    id === null ||
    !DECLARATIVE_ID.test(id) ||
    label === null ||
    description === undefined ||
    typeof control !== 'string' ||
    !DECLARATIVE_CONTROLS.has(control) ||
    typeof value.required !== 'boolean'
  ) {
    return null
  }
  const maxLength = optionalInteger(value.maxLength)
  const minimum = optionalInteger(value.minimum)
  const maximum = optionalInteger(value.maximum)
  const step = optionalInteger(value.step)
  if (
    maxLength === undefined ||
    minimum === undefined ||
    maximum === undefined ||
    step === undefined
  ) {
    return null
  }
  const rawOptions = value.options ?? []
  if (!Array.isArray(rawOptions) || rawOptions.length > 32) return null
  const options = rawOptions.map(parseOption)
  if (options.some((option) => option === null)) return null
  const parsedOptions = options as readonly P7DeclarativeOption[]
  if (new Set(parsedOptions.map((option) => option.value)).size !== parsedOptions.length)
    return null

  if (control === 'text' || control === 'multiline') {
    if (maxLength === null || maxLength < 1 || maxLength > 4_096) return null
    if (minimum !== null || maximum !== null || step !== null || parsedOptions.length !== 0)
      return null
  } else if (control === 'integer' || control === 'number') {
    if (
      maxLength !== null ||
      (minimum !== null && maximum !== null && minimum > maximum) ||
      (step !== null && step <= 0) ||
      parsedOptions.length !== 0
    ) {
      return null
    }
  } else if (control === 'select') {
    if (
      maxLength !== null ||
      minimum !== null ||
      maximum !== null ||
      step !== null ||
      parsedOptions.length === 0
    ) {
      return null
    }
  } else if (
    maxLength !== null ||
    minimum !== null ||
    maximum !== null ||
    step !== null ||
    parsedOptions.length !== 0
  ) {
    return null
  }

  return Object.freeze({
    id,
    label,
    description,
    control: control as P7DeclarativeControl,
    required: value.required,
    maxLength,
    minimum,
    maximum,
    step,
    options: Object.freeze([...parsedOptions]),
    defaultValue: undefined,
  })
}

function parseSection(value: unknown): P7DeclarativeSection | null {
  if (
    !isRecord(value) ||
    !exactOptionalKeys(value, ['id', 'label', 'fields'], ['description']) ||
    !Array.isArray(value.fields) ||
    value.fields.length === 0 ||
    value.fields.length > 16
  ) {
    return null
  }
  const id = boundedText(value.id, 64)
  const label = boundedText(value.label, 80)
  const description = optionalDescription(value.description)
  const fields = value.fields.map(parseField)
  if (
    id === null ||
    !DECLARATIVE_ID.test(id) ||
    label === null ||
    description === undefined ||
    fields.some((field) => field === null)
  ) {
    return null
  }
  const parsedFields = fields as readonly P7DeclarativeField[]
  if (new Set(parsedFields.map((field) => field.id)).size !== parsedFields.length) return null
  return Object.freeze({
    id,
    label,
    description,
    fields: Object.freeze([...parsedFields]),
  })
}

function primitiveMatchesType(
  value: unknown,
  type: 'boolean' | 'integer' | 'number' | 'string',
): value is string | number | boolean {
  if (type === 'boolean') return typeof value === 'boolean'
  if (type === 'string') return typeof value === 'string'
  if (type === 'integer') return typeof value === 'number' && Number.isSafeInteger(value)
  return typeof value === 'number' && Number.isFinite(value)
}

function closedSchemaNumber(value: unknown, integer: boolean): number | null | undefined {
  if (value === undefined) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  if (integer && !Number.isSafeInteger(value)) return undefined
  return value
}

function parseClosedSchemaField(
  id: string,
  value: unknown,
  required: boolean,
): P7DeclarativeField | null {
  if (
    !isRecord(value) ||
    !exactOptionalKeys(value, ['type'], ['default', 'enum', 'minimum', 'maximum', 'maxLength']) ||
    (value.type !== 'boolean' &&
      value.type !== 'integer' &&
      value.type !== 'number' &&
      value.type !== 'string')
  ) {
    return null
  }
  const propertyType = value.type
  const integer = propertyType === 'integer'
  const minimum = closedSchemaNumber(value.minimum, integer)
  const maximum = closedSchemaNumber(value.maximum, integer)
  if (
    minimum === undefined ||
    maximum === undefined ||
    (minimum !== null && maximum !== null && minimum > maximum)
  ) {
    return null
  }
  const maxLength =
    value.maxLength === undefined
      ? propertyType === 'string'
        ? 4_096
        : null
      : Number.isSafeInteger(value.maxLength) &&
          (value.maxLength as number) >= 0 &&
          (value.maxLength as number) <= 4_096 &&
          propertyType === 'string'
        ? (value.maxLength as number)
        : undefined
  if (maxLength === undefined) return null

  const rawEnum = value.enum ?? []
  if (
    !Array.isArray(rawEnum) ||
    rawEnum.length > 64 ||
    (value.enum !== undefined && rawEnum.length === 0)
  ) {
    return null
  }
  const enumValues: Array<string | number | boolean> = []
  const enumKeys = new Set<string>()
  for (const item of rawEnum) {
    if (!primitiveMatchesType(item, propertyType)) return null
    const key = `${typeof item}:${String(item)}`
    if (enumKeys.has(key)) return null
    enumKeys.add(key)
    enumValues.push(item)
  }
  const defaultValue = value.default
  if (
    defaultValue !== undefined &&
    (!primitiveMatchesType(defaultValue, propertyType) ||
      (enumValues.length > 0 && !enumValues.some((item) => item === defaultValue)) ||
      (typeof defaultValue === 'string' && defaultValue.length > (maxLength ?? 0)) ||
      (typeof defaultValue === 'number' &&
        ((minimum !== null && defaultValue < minimum) ||
          (maximum !== null && defaultValue > maximum))))
  ) {
    return null
  }

  let control: P7DeclarativeControl
  if (enumValues.length > 0) control = 'select'
  else if (propertyType === 'boolean') control = 'boolean'
  else if (propertyType === 'integer') control = 'integer'
  else if (propertyType === 'number') control = 'number'
  else control = (maxLength ?? 0) > 256 ? 'multiline' : 'text'
  return Object.freeze({
    id,
    label: id.replaceAll('_', ' '),
    description: null,
    control,
    required,
    maxLength: propertyType === 'string' ? maxLength : null,
    minimum,
    maximum,
    step: propertyType === 'integer' ? 1 : null,
    options: Object.freeze(
      enumValues.map((item) => Object.freeze({ value: item, label: String(item) })),
    ),
    defaultValue: defaultValue as P7DeclarativeValue | undefined,
  })
}

function parseClosedObjectSettingsSchema(
  value: Record<string, unknown>,
): P7DeclarativeSettingsSchema | null {
  if (
    !exactOptionalKeys(
      value,
      ['additionalProperties', 'kind', 'properties', 'required', 'version'],
      [],
    ) ||
    value.kind !== 'closed_object' ||
    value.additionalProperties !== false ||
    !Number.isSafeInteger(value.version) ||
    (value.version as number) < 1 ||
    !isRecord(value.properties) ||
    Object.keys(value.properties).length > 32 ||
    !Array.isArray(value.required) ||
    value.required.length > 32 ||
    value.required.some((item) => typeof item !== 'string' || !DECLARATIVE_ID.test(item)) ||
    new Set(value.required).size !== value.required.length
  ) {
    return null
  }
  const required = new Set(value.required as readonly string[])
  if ([...required].some((id) => !Object.hasOwn(value.properties as object, id))) return null
  const fields: P7DeclarativeField[] = []
  for (const [id, specification] of Object.entries(value.properties)) {
    if (!/^[a-z][a-z0-9_]{0,63}$/u.test(id)) return null
    const field = parseClosedSchemaField(id, specification, required.has(id))
    if (field === null) return null
    fields.push(field)
  }
  return Object.freeze({
    schemaVersion: 1,
    sections:
      fields.length === 0
        ? Object.freeze([])
        : Object.freeze([
            Object.freeze({
              id: 'configuration',
              label: 'Configuration',
              description: null,
              fields: Object.freeze(fields),
            }),
          ]),
  })
}

export function p7ParseDeclarativeSettingsSchema(
  value: unknown,
): P7DeclarativeSettingsSchema | null {
  if (isRecord(value) && value.kind === 'closed_object') {
    return parseClosedObjectSettingsSchema(value)
  }
  if (
    !isRecord(value) ||
    !exactOptionalKeys(value, ['schemaVersion', 'sections'], []) ||
    value.schemaVersion !== 1 ||
    !Array.isArray(value.sections) ||
    value.sections.length === 0 ||
    value.sections.length > 8
  ) {
    return null
  }
  const sections = value.sections.map(parseSection)
  if (sections.some((section) => section === null)) return null
  const parsedSections = sections as readonly P7DeclarativeSection[]
  const sectionIds = parsedSections.map((section) => section.id)
  const fields = parsedSections.flatMap((section) => section.fields)
  if (
    new Set(sectionIds).size !== sectionIds.length ||
    fields.length > 32 ||
    new Set(fields.map((field) => field.id)).size !== fields.length
  ) {
    return null
  }
  return Object.freeze({ schemaVersion: 1, sections: Object.freeze([...parsedSections]) })
}

export interface P7DeclarativeValidation {
  readonly valid: boolean
  readonly errors: Readonly<Record<string, string>>
}

export function p7ValidateDeclarativeSettings(
  schema: P7DeclarativeSettingsSchema,
  settings: P7DeclarativeSettings,
): P7DeclarativeValidation {
  const fields = schema.sections.flatMap((section) => section.fields)
  const fieldById = new Map(fields.map((field) => [field.id, field]))
  const errors: Record<string, string> = {}
  for (const key of Object.keys(settings)) {
    if (!fieldById.has(key)) errors[key] = '未知设置项'
  }
  for (const field of fields) {
    const value = settings[field.id]
    if (value === undefined || value === null || value === '') {
      if (field.required) errors[field.id] = '此项必填'
      continue
    }
    if (field.control === 'select') {
      if (!field.options.some((option) => option.value === value)) {
        errors[field.id] = '选项不在允许集合中'
      }
      continue
    }
    if (field.control === 'boolean') {
      if (typeof value !== 'boolean') errors[field.id] = '必须是开关值'
      continue
    }
    if (field.control === 'integer' || field.control === 'number') {
      if (
        typeof value !== 'number' ||
        !Number.isFinite(value) ||
        (field.control === 'integer' && !Number.isSafeInteger(value)) ||
        value < (field.minimum ?? value) ||
        value > (field.maximum ?? value) ||
        (field.step !== null && value % field.step !== 0)
      ) {
        errors[field.id] = '数值超出允许范围'
      }
      continue
    }
    if (typeof value !== 'string') {
      errors[field.id] = '必须是文本值'
      continue
    }
    if (
      (field.control === 'text' || field.control === 'multiline') &&
      value.length > (field.maxLength ?? 0)
    ) {
      errors[field.id] = '文本超过长度限制'
    }
    if (
      (field.control === 'secret-ref' || field.control === 'logical-resource-ref') &&
      !DECLARATIVE_ID.test(value)
    ) {
      errors[field.id] = '引用必须是逻辑标识'
    }
  }
  return Object.freeze({ valid: Object.keys(errors).length === 0, errors: Object.freeze(errors) })
}

export function p7DeclarativeSettingsDefaults(
  schema: P7DeclarativeSettingsSchema,
): P7DeclarativeSettings {
  const settings: Record<string, P7DeclarativeValue> = {}
  for (const field of schema.sections.flatMap((section) => section.fields)) {
    if (field.defaultValue !== undefined) settings[field.id] = field.defaultValue
  }
  return Object.freeze(settings)
}

export interface P7ComponentDiffRow {
  readonly key: string
  readonly label: string
  readonly before: string
  readonly after: string
  readonly sensitive: boolean
}

function displayDeclarativeValue(field: P7DeclarativeField, value: P7DeclarativeValue | undefined) {
  if (field.control === 'secret-ref')
    return value === null || value === undefined ? '未绑定' : '已绑定'
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (field.control === 'select') {
    return field.options.find((option) => option.value === value)?.label ?? '无效选项'
  }
  return String(value)
}

export function p7DeclarativeSettingsDiff(
  schema: P7DeclarativeSettingsSchema,
  before: P7DeclarativeSettings,
  after: P7DeclarativeSettings,
): readonly P7ComponentDiffRow[] {
  return Object.freeze(
    schema.sections.flatMap((section) =>
      section.fields.flatMap((field) => {
        const previous = before[field.id]
        const next = after[field.id]
        if (previous === next) return []
        return [
          Object.freeze({
            key: `configuration.${field.id}`,
            label: field.label,
            before: displayDeclarativeValue(field, previous),
            after: displayDeclarativeValue(field, next),
            sensitive: field.control === 'secret-ref',
          }),
        ]
      }),
    ),
  )
}

export type P7ComponentLifecycleAction =
  | 'install'
  | 'bind'
  | 'activate'
  | 'disable'
  | 'upgrade'
  | 'rollback'
  | 'revoke'
  | 'uninstall'

export type P7ComponentInstallationState =
  | 'installed'
  | 'bound'
  | 'active'
  | 'disabled'
  | 'degraded'
  | 'revoked'
  | 'failed'
  | 'uninstalled'

export interface P7ComponentActionEligibility {
  readonly eligible: boolean
  readonly reason:
    | 'eligible'
    | 'workspace-mismatch'
    | 'snapshot-unavailable'
    | 'operation-active'
    | 'proposal-required'
    | 'proposal-stale'
    | 'state-ineligible'
}

const ACTION_STATES = Object.freeze({
  install: Object.freeze([]),
  bind: Object.freeze(['installed']),
  activate: Object.freeze(['bound', 'disabled']),
  disable: Object.freeze(['active', 'degraded']),
  upgrade: Object.freeze(['installed', 'bound', 'active', 'disabled', 'degraded']),
  rollback: Object.freeze(['installed', 'bound', 'active', 'disabled', 'degraded', 'failed']),
  revoke: Object.freeze(['installed', 'bound', 'active', 'disabled', 'degraded', 'failed']),
  uninstall: Object.freeze(['disabled', 'revoked', 'failed']),
} as const satisfies Readonly<
  Record<P7ComponentLifecycleAction, readonly P7ComponentInstallationState[]>
>)

export function p7ComponentActionEligibility(
  input: Readonly<{
    viewWorkspaceId: string | null
    snapshotWorkspaceId: string | null
    snapshotRevision: number | null
    action: P7ComponentLifecycleAction
    installationState: P7ComponentInstallationState | null
    operationActive: boolean
    proposal: Readonly<{
      workspaceId: string
      changeKind: P7ComponentLifecycleAction
      decision: 'approved' | 'rejected' | null
      expectedRevision: number
      requestSha256: string
    }> | null
  }>,
): P7ComponentActionEligibility {
  if (
    input.viewWorkspaceId === null ||
    input.snapshotWorkspaceId !== input.viewWorkspaceId ||
    input.proposal?.workspaceId !== input.viewWorkspaceId
  ) {
    return Object.freeze({ eligible: false, reason: 'workspace-mismatch' })
  }
  if (input.snapshotRevision === null) {
    return Object.freeze({ eligible: false, reason: 'snapshot-unavailable' })
  }
  if (input.operationActive) {
    return Object.freeze({ eligible: false, reason: 'operation-active' })
  }
  if (
    input.proposal === null ||
    input.proposal.decision !== 'approved' ||
    input.proposal.changeKind !== input.action ||
    !/^[a-f0-9]{64}$/.test(input.proposal.requestSha256)
  ) {
    return Object.freeze({ eligible: false, reason: 'proposal-required' })
  }
  if (input.proposal.expectedRevision !== input.snapshotRevision) {
    return Object.freeze({ eligible: false, reason: 'proposal-stale' })
  }
  const actionStates: readonly P7ComponentInstallationState[] = ACTION_STATES[input.action]
  const stateEligible =
    input.action === 'install'
      ? input.installationState === null || input.installationState === 'uninstalled'
      : input.installationState !== null && actionStates.includes(input.installationState)
  if (!stateEligible) return Object.freeze({ eligible: false, reason: 'state-ineligible' })
  return Object.freeze({ eligible: true, reason: 'eligible' })
}

export type P7ComponentOperation =
  | 'ui.render'
  | 'skill.resolve'
  | 'mcp.call'
  | 'sandbox.run'
  | 'local_adapter.open'

export type P7ComponentFamily = 'ui' | 'instruction-skill' | 'mcp' | 'sandbox' | 'local-adapter'

const FAMILY_OPERATION: Readonly<Record<P7ComponentFamily, P7ComponentOperation>> = Object.freeze({
  ui: 'ui.render',
  'instruction-skill': 'skill.resolve',
  mcp: 'mcp.call',
  sandbox: 'sandbox.run',
  'local-adapter': 'local_adapter.open',
})

export function p7ComponentInvocationEligible(
  input: Readonly<{
    family: P7ComponentFamily
    operation: P7ComponentOperation
    state: P7ComponentInstallationState
    health: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
    bindingGeneration: number
    revoked: boolean
    reconciliationRequired: boolean
  }>,
): boolean {
  return (
    FAMILY_OPERATION[input.family] === input.operation &&
    input.state === 'active' &&
    input.health === 'healthy' &&
    Number.isSafeInteger(input.bindingGeneration) &&
    input.bindingGeneration > 0 &&
    !input.revoked &&
    !input.reconciliationRequired
  )
}

export function p7ComponentEffectNeedsReconciliation(
  state: 'pending' | 'succeeded' | 'failed' | 'cancelled' | 'unknown',
): boolean {
  return state === 'pending' || state === 'unknown'
}

export function p7EmergencyStopEligible(
  input: Readonly<{
    viewWorkspaceId: string | null
    snapshotWorkspaceId: string | null
    activeOperationCount: number
    managedComponentCount: number
    stopInFlight: boolean
  }>,
): boolean {
  return (
    input.viewWorkspaceId !== null &&
    input.snapshotWorkspaceId === input.viewWorkspaceId &&
    Number.isSafeInteger(input.activeOperationCount) &&
    Number.isSafeInteger(input.managedComponentCount) &&
    (input.activeOperationCount > 0 || input.managedComponentCount > 0) &&
    !input.stopInFlight
  )
}

// ---------------------------------------------------------------------------
// Host-rendered component surfaces
// ---------------------------------------------------------------------------

const SHA256 = /^[a-f0-9]{64}$/
const COMPONENT_SURFACE_ID = /^[a-z][a-z0-9_.:-]{1,127}$/

export const P7_WORKSPACE_COMPONENT_HOST_SLOT_IDS = [
  'editor.component',
  'sidebar.component',
  'settings.component',
  'status.component',
] as const
export type P7WorkspaceComponentHostSlotId = (typeof P7_WORKSPACE_COMPONENT_HOST_SLOT_IDS)[number]

export function p7WorkspaceComponentHostSlotId(
  value: string,
): value is P7WorkspaceComponentHostSlotId {
  return P7_WORKSPACE_COMPONENT_HOST_SLOT_IDS.some((slotId) => slotId === value)
}

export interface P7WorkspaceCanvasSection {
  readonly kind: 'status'
  readonly label: string
  readonly value: string
}

export interface P7WorkspaceCanvasSurface {
  readonly kind: 'workspace-canvas'
  readonly workspaceId: string
  readonly componentId: string
  readonly operationId: string
  readonly slotId: P7WorkspaceComponentHostSlotId
  readonly viewId: string
  readonly renderer: 'host_declarative'
  readonly title: string
  readonly sections: readonly P7WorkspaceCanvasSection[]
}

export interface P7InstructionSkillSurface {
  readonly kind: 'instruction-skill'
  readonly workspaceId: string
  readonly componentId: 'builtin.instruction-skill'
  readonly operationId: string
  readonly authority: 'instruction_only'
  readonly skillId: 'builtin.instruction-skill'
  readonly taskSha256: string
  readonly instructions: string
}

export interface P7McpFileEntry {
  readonly kind: 'file' | 'directory'
  readonly name: string
  readonly path: string
  readonly sizeBytes: number | null
}

export interface P7McpListResult {
  readonly kind: 'list'
  readonly tool: 'omnibase_files_list'
  readonly directoryPath: string
  readonly entries: readonly P7McpFileEntry[]
  readonly truncated: boolean
}

export interface P7McpReadResult {
  readonly kind: 'read'
  readonly tool: 'omnibase_files_read'
  readonly path: string
  readonly content: string
  readonly sizeBytes: number
  readonly sha256: string
}

export interface P7McpHashResult {
  readonly kind: 'hash'
  readonly tool: 'omnibase_files_hash'
  readonly path: string
  readonly sizeBytes: number
  readonly sha256: string
}

export interface P7McpSearchMatch {
  readonly line: number
  readonly snippet: string
}

export interface P7McpSearchResult {
  readonly kind: 'search'
  readonly tool: 'omnibase_text_search'
  readonly path: string
  readonly matches: readonly P7McpSearchMatch[]
  readonly truncated: boolean
}

export type P7McpResult = P7McpListResult | P7McpReadResult | P7McpHashResult | P7McpSearchResult

export interface P7McpSurface {
  readonly kind: 'readonly-mcp'
  readonly workspaceId: string
  readonly componentId: 'builtin.readonly-mcp'
  readonly operationId: string
  readonly result: P7McpResult
}

export interface P7SandboxSurface {
  readonly kind: 'sandbox-workload'
  readonly workspaceId: string
  readonly componentId: 'builtin.sandbox-workload'
  readonly operationId: string
  readonly adapter: 'p34-sandbox.v1'
  readonly schemaVersion: 1
  readonly workloadId: 'bounded-transform'
  readonly runtimeInstanceId: string
  readonly status: 'completed'
  readonly inputArtifactIds: readonly string[]
  readonly result: Readonly<{
    kind: 'artifact_inventory'
    artifactCount: number
    fingerprintSha256: string
  }>
  readonly usage: Readonly<{
    bytesIn: number
    bytesOut: number
    wallTimeMs: number
  }>
}

export interface P7KnowledgeEbookSection {
  readonly id: string
  readonly heading: string
  readonly level: number
  readonly position: number
  readonly theme: string
  readonly content: string
  readonly explanation: string
}

export interface P7KnowledgeEbookDocument {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly summary: string
  readonly content: string
  readonly fileHash: string
  readonly sections: readonly P7KnowledgeEbookSection[]
}

export interface P7KnowledgeEbookCatalog {
  readonly schemaVersion: 1
  readonly componentId: 'knowledge.ebook'
  readonly componentVersion: string
  readonly sourceSnapshotSha256: string
  readonly documents: readonly P7KnowledgeEbookDocument[]
  readonly glossaryCount: number
  readonly invariantCount: number
  readonly moduleCount: number
}

export interface P7KnowledgeEbookSurface {
  readonly kind: 'knowledge-ebook'
  readonly workspaceId: string
  readonly componentId: 'knowledge.ebook'
  readonly operationId: string
  readonly slotId: 'editor.component'
  readonly renderer: 'host_declarative'
  readonly assetId: string
  readonly assetSha256: string
  readonly componentManifestSha256: string
  readonly componentPackageSha256: string
  readonly destination: 'workspace'
  readonly logicalId: string | null
  readonly catalog: P7KnowledgeEbookCatalog
}

export type P7WorkspaceComponentSurface =
  | P7WorkspaceCanvasSurface
  | P7InstructionSkillSurface
  | P7McpSurface
  | P7SandboxSurface
  | P7KnowledgeEbookSurface

export type P7WorkspaceComponentSafeModeReason =
  | 'component-inactive'
  | 'emergency-stop'
  | 'invocation-failed'
  | 'malformed-output'

export interface P7WorkspaceComponentSurfaceState {
  readonly workspaceId: string | null
  readonly surface: P7WorkspaceComponentSurface | null
  readonly safeModeReason: P7WorkspaceComponentSafeModeReason | null
}

export interface P7WorkspaceComponentSurfaceProjection {
  readonly status: 'idle' | 'loading' | 'ready' | 'safe-mode'
  readonly surface: P7WorkspaceComponentSurface | null
  readonly safeModeReason: P7WorkspaceComponentSafeModeReason | null
}

function exactRecord(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  )
}

function boundedArray(value: unknown, maximum: number): readonly unknown[] | null {
  return Array.isArray(value) && value.length <= maximum ? value : null
}

function boundedString(value: unknown, maximum: number, allowEmpty = true): string | null {
  if (typeof value !== 'string' || value.length > maximum || (!allowEmpty && value.length === 0)) {
    return null
  }
  return value
}

function parseEbookSection(value: unknown): P7KnowledgeEbookSection | null {
  if (
    !exactRecord(value, ['content', 'explanation', 'heading', 'id', 'level', 'position', 'theme'])
  ) {
    return null
  }
  const id = boundedString(value.id, 128, false)
  const heading = boundedString(value.heading, 8_192)
  const theme = boundedString(value.theme, 512)
  const content = boundedString(value.content, 524_288)
  const explanation = boundedString(value.explanation, 524_288)
  if (
    id === null ||
    !id.startsWith('section:') ||
    heading === null ||
    theme === null ||
    content === null ||
    explanation === null ||
    !Number.isSafeInteger(value.level) ||
    (value.level as number) < 0 ||
    !Number.isSafeInteger(value.position) ||
    (value.position as number) < 0
  ) {
    return null
  }
  return Object.freeze({
    id,
    heading,
    level: value.level as number,
    position: value.position as number,
    theme,
    content,
    explanation,
  })
}

function parseEbookDocument(value: unknown): P7KnowledgeEbookDocument | null {
  if (!exactRecord(value, ['content', 'file_hash', 'id', 'sections', 'summary', 'title', 'type'])) {
    return null
  }
  const sectionsInput = boundedArray(value.sections, 20_000)
  const id = boundedString(value.id, 128, false)
  const title = boundedString(value.title, 8_192, false)
  const type = boundedString(value.type, 512)
  const summary = boundedString(value.summary, 524_288)
  const content = boundedString(value.content, 524_288)
  const fileHash = boundedString(value.file_hash, 128)
  if (
    sectionsInput === null ||
    id === null ||
    !id.startsWith('document:') ||
    title === null ||
    type === null ||
    summary === null ||
    content === null ||
    fileHash === null
  ) {
    return null
  }
  const sections = sectionsInput.map(parseEbookSection)
  if (sections.some((section) => section === null)) return null
  const parsedSections = sections as readonly P7KnowledgeEbookSection[]
  if (new Set(parsedSections.map((section) => section.id)).size !== parsedSections.length)
    return null
  return Object.freeze({
    id,
    title,
    type,
    summary,
    content,
    fileHash,
    sections: Object.freeze([...parsedSections]),
  })
}

function validateClosedCollection(
  value: unknown,
  maximum: number,
  requiredKeys: readonly string[],
): number | null {
  const collection = boundedArray(value, maximum)
  if (collection === null || collection.some((item) => !exactRecord(item, requiredKeys)))
    return null
  return collection.length
}

function parseKnowledgeEbookCatalog(value: unknown): P7KnowledgeEbookCatalog | null {
  if (
    !exactRecord(value, [
      'component_id',
      'component_version',
      'documents',
      'glossary',
      'invariants',
      'modules',
      'schema_version',
      'source_snapshot_sha256',
    ]) ||
    value.component_id !== 'knowledge.ebook' ||
    value.schema_version !== 1 ||
    typeof value.component_version !== 'string' ||
    !/^\d+\.\d+\.\d+$/.test(value.component_version) ||
    typeof value.source_snapshot_sha256 !== 'string' ||
    !SHA256.test(value.source_snapshot_sha256)
  ) {
    return null
  }
  const documentsInput = boundedArray(value.documents, 1_024)
  const glossaryCount = validateClosedCollection(value.glossary, 10_000, [
    'category',
    'definition',
    'explanation',
    'term',
  ])
  const invariantCount = validateClosedCollection(value.invariants, 10_000, [
    'content',
    'explanation',
    'id',
    'modules',
    'phase',
    'severity',
    'title',
  ])
  const moduleCount = validateClosedCollection(value.modules, 10_000, [
    'dependencies',
    'description',
    'id',
    'invariants',
    'name',
    'summary',
    'verification',
  ])
  if (
    documentsInput === null ||
    glossaryCount === null ||
    invariantCount === null ||
    moduleCount === null
  ) {
    return null
  }
  const documents = documentsInput.map(parseEbookDocument)
  if (documents.some((document) => document === null)) return null
  const parsedDocuments = documents as readonly P7KnowledgeEbookDocument[]
  if (new Set(parsedDocuments.map((document) => document.id)).size !== parsedDocuments.length) {
    return null
  }
  return Object.freeze({
    schemaVersion: 1,
    componentId: 'knowledge.ebook',
    componentVersion: value.component_version,
    sourceSnapshotSha256: value.source_snapshot_sha256,
    documents: Object.freeze([...parsedDocuments]),
    glossaryCount,
    invariantCount,
    moduleCount,
  })
}

function safeNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0
}

function safePositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0
}

function parseCanvasView(value: unknown): Readonly<{
  title: string
  sections: readonly P7WorkspaceCanvasSection[]
}> | null {
  if (!exactRecord(value, ['kind', 'sections', 'title'])) return null
  const title = boundedString(value.title, 256, false)
  const sectionsInput = boundedArray(value.sections, 32)
  if (value.kind !== 'workspace_component_overview' || title === null || sectionsInput === null) {
    return null
  }
  const sections: P7WorkspaceCanvasSection[] = []
  for (const section of sectionsInput) {
    if (!exactRecord(section, ['kind', 'label', 'value']) || section.kind !== 'status') return null
    const label = boundedString(section.label, 128, false)
    const content = boundedString(section.value, 2_048)
    if (label === null || content === null) return null
    sections.push(Object.freeze({ kind: 'status', label, value: content }))
  }
  if (new Set(sections.map((section) => section.label)).size !== sections.length) return null
  return Object.freeze({ title, sections: Object.freeze(sections) })
}

function parseMcpLogicalPath(value: unknown, allowRoot = false): string | null {
  const path = boundedString(value, 4_096, allowRoot)
  if (path === null) return null
  if (path === '') return allowRoot ? path : null
  if (
    path.startsWith('/') ||
    path.endsWith('/') ||
    path.includes('\\') ||
    path.includes(':') ||
    path.split('/').some((part) => part === '' || part === '.' || part === '..')
  ) {
    return null
  }
  return path
}

function parseMcpListResult(value: Record<string, unknown>): P7McpListResult | null {
  if (!exactRecord(value, ['directory_path', 'entries', 'tool', 'truncated'])) return null
  const directoryPath = parseMcpLogicalPath(value.directory_path, true)
  const entriesInput = boundedArray(value.entries, 500)
  if (
    value.tool !== 'omnibase_files_list' ||
    directoryPath === null ||
    entriesInput === null ||
    typeof value.truncated !== 'boolean'
  ) {
    return null
  }
  const entries: P7McpFileEntry[] = []
  const paths = new Set<string>()
  for (const candidate of entriesInput) {
    if (!exactRecord(candidate, ['kind', 'name', 'path', 'size_bytes'])) return null
    const name = boundedString(candidate.name, 255, false)
    const path = parseMcpLogicalPath(candidate.path)
    if (
      (candidate.kind !== 'file' && candidate.kind !== 'directory') ||
      name === null ||
      /[\u0000-\u001f\u007f/\\:]/u.test(name) ||
      path === null ||
      path !== (directoryPath === '' ? name : `${directoryPath}/${name}`) ||
      paths.has(path) ||
      (candidate.kind === 'file'
        ? !safeNonNegativeInteger(candidate.size_bytes)
        : candidate.size_bytes !== null)
    ) {
      return null
    }
    paths.add(path)
    entries.push(
      Object.freeze({
        kind: candidate.kind,
        name,
        path,
        sizeBytes: candidate.size_bytes as number | null,
      }),
    )
  }
  return Object.freeze({
    kind: 'list',
    tool: 'omnibase_files_list',
    directoryPath,
    entries: Object.freeze(entries),
    truncated: value.truncated,
  })
}

function parseSandboxResult(
  value: unknown,
  componentId: string,
): P7SandboxSurface['result'] extends never
  ? never
  : Readonly<{
      runtimeInstanceId: string
      inputArtifactIds: readonly string[]
      result: P7SandboxSurface['result']
      usage: P7SandboxSurface['usage']
    }> | null {
  if (
    !exactRecord(value, [
      'adapter',
      'component_id',
      'input_artifact_ids',
      'result',
      'runtime_instance_id',
      'schema_version',
      'status',
      'usage',
      'workload_id',
    ]) ||
    value.adapter !== 'p34-sandbox.v1' ||
    value.component_id !== componentId ||
    value.schema_version !== 1 ||
    value.workload_id !== 'bounded-transform' ||
    value.status !== 'completed' ||
    typeof value.runtime_instance_id !== 'string' ||
    !COMPONENT_SURFACE_ID.test(value.runtime_instance_id) ||
    !Array.isArray(value.input_artifact_ids) ||
    value.input_artifact_ids.length > 256 ||
    value.input_artifact_ids.some(
      (item) => typeof item !== 'string' || !COMPONENT_SURFACE_ID.test(item),
    ) ||
    new Set(value.input_artifact_ids).size !== value.input_artifact_ids.length ||
    !exactRecord(value.result, ['artifact_count', 'fingerprint_sha256', 'kind']) ||
    value.result.kind !== 'artifact_inventory' ||
    !safeNonNegativeInteger(value.result.artifact_count) ||
    value.result.artifact_count > 100_000 ||
    typeof value.result.fingerprint_sha256 !== 'string' ||
    !SHA256.test(value.result.fingerprint_sha256) ||
    !exactRecord(value.usage, ['bytes_in', 'bytes_out', 'wall_time_ms']) ||
    !safeNonNegativeInteger(value.usage.bytes_in) ||
    !safeNonNegativeInteger(value.usage.bytes_out) ||
    !safeNonNegativeInteger(value.usage.wall_time_ms)
  ) {
    return null
  }
  return Object.freeze({
    runtimeInstanceId: value.runtime_instance_id,
    inputArtifactIds: Object.freeze([...value.input_artifact_ids]),
    result: Object.freeze({
      kind: 'artifact_inventory',
      artifactCount: value.result.artifact_count,
      fingerprintSha256: value.result.fingerprint_sha256,
    }),
    usage: Object.freeze({
      bytesIn: value.usage.bytes_in,
      bytesOut: value.usage.bytes_out,
      wallTimeMs: value.usage.wall_time_ms,
    }),
  })
}

function parseMcpReadResult(value: Record<string, unknown>): P7McpReadResult | null {
  if (!exactRecord(value, ['content', 'path', 'sha256', 'size_bytes', 'tool'])) return null
  const path = parseMcpLogicalPath(value.path)
  const content = boundedString(value.content, 1_048_576)
  if (
    value.tool !== 'omnibase_files_read' ||
    path === null ||
    content === null ||
    !safeNonNegativeInteger(value.size_bytes) ||
    value.size_bytes > 1_048_576 ||
    typeof value.sha256 !== 'string' ||
    !SHA256.test(value.sha256)
  ) {
    return null
  }
  return Object.freeze({
    kind: 'read',
    tool: 'omnibase_files_read',
    path,
    content,
    sizeBytes: value.size_bytes,
    sha256: value.sha256,
  })
}

function parseMcpHashResult(value: Record<string, unknown>): P7McpHashResult | null {
  if (!exactRecord(value, ['path', 'sha256', 'size_bytes', 'tool'])) return null
  const path = parseMcpLogicalPath(value.path)
  if (
    value.tool !== 'omnibase_files_hash' ||
    path === null ||
    !safeNonNegativeInteger(value.size_bytes) ||
    value.size_bytes > 1_048_576 ||
    typeof value.sha256 !== 'string' ||
    !SHA256.test(value.sha256)
  ) {
    return null
  }
  return Object.freeze({
    kind: 'hash',
    tool: 'omnibase_files_hash',
    path,
    sizeBytes: value.size_bytes,
    sha256: value.sha256,
  })
}

function parseMcpSearchResult(value: Record<string, unknown>): P7McpSearchResult | null {
  if (!exactRecord(value, ['matches', 'path', 'tool', 'truncated'])) return null
  const path = parseMcpLogicalPath(value.path)
  const matchesInput = boundedArray(value.matches, 100)
  if (
    value.tool !== 'omnibase_text_search' ||
    path === null ||
    matchesInput === null ||
    typeof value.truncated !== 'boolean'
  ) {
    return null
  }
  const matches: P7McpSearchMatch[] = []
  let previousLine = 0
  for (const candidate of matchesInput) {
    if (!exactRecord(candidate, ['line', 'snippet'])) return null
    const snippet = boundedString(candidate.snippet, 512)
    if (!safePositiveInteger(candidate.line) || candidate.line < previousLine || snippet === null) {
      return null
    }
    previousLine = candidate.line
    matches.push(Object.freeze({ line: candidate.line, snippet }))
  }
  return Object.freeze({
    kind: 'search',
    tool: 'omnibase_text_search',
    path,
    matches: Object.freeze(matches),
    truncated: value.truncated,
  })
}

function parseMcpResult(value: unknown): P7McpResult | null {
  if (!isRecord(value) || typeof value.tool !== 'string') return null
  switch (value.tool) {
    case 'omnibase_files_list':
      return parseMcpListResult(value)
    case 'omnibase_files_read':
      return parseMcpReadResult(value)
    case 'omnibase_files_hash':
      return parseMcpHashResult(value)
    case 'omnibase_text_search':
      return parseMcpSearchResult(value)
    default:
      return null
  }
}

export function p7ParseWorkspaceComponentSurface(
  input: Readonly<{
    workspaceId: string
    componentId: string
    operationId: string
    operation: P7ComponentOperation
    output: unknown
  }>,
): P7WorkspaceComponentSurface | null {
  if (
    !COMPONENT_SURFACE_ID.test(input.operationId) ||
    !COMPONENT_SURFACE_ID.test(input.workspaceId)
  ) {
    return null
  }
  if (input.operation === 'ui.render') {
    if (
      !COMPONENT_SURFACE_ID.test(input.componentId) ||
      !exactRecord(input.output, [
        'adapter',
        'component_id',
        'renderer',
        'schema_version',
        'slot_id',
        'view',
        'view_id',
      ]) ||
      input.output.adapter !== 'builtin-ui.v1' ||
      input.output.component_id !== input.componentId ||
      input.output.schema_version !== 1 ||
      input.output.renderer !== 'host_declarative' ||
      typeof input.output.slot_id !== 'string' ||
      !p7WorkspaceComponentHostSlotId(input.output.slot_id) ||
      typeof input.output.view_id !== 'string' ||
      input.output.view_id !== input.componentId
    ) {
      return null
    }
    const view = parseCanvasView(input.output.view)
    if (view === null) return null
    return Object.freeze({
      kind: 'workspace-canvas',
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      operationId: input.operationId,
      slotId: input.output.slot_id,
      viewId: input.output.view_id,
      renderer: 'host_declarative',
      title: view.title,
      sections: view.sections,
    })
  }
  if (input.operation === 'skill.resolve') {
    if (
      input.componentId !== 'builtin.instruction-skill' ||
      !exactRecord(input.output, [
        'adapter',
        'authority',
        'component_id',
        'instructions',
        'skill_id',
        'task_sha256',
      ]) ||
      input.output.adapter !== 'instruction-skill.v1' ||
      input.output.authority !== 'instruction_only' ||
      input.output.component_id !== input.componentId ||
      input.output.skill_id !== input.componentId ||
      boundedString(input.output.instructions, 32_768, false) === null ||
      typeof input.output.task_sha256 !== 'string' ||
      !SHA256.test(input.output.task_sha256)
    ) {
      return null
    }
    return Object.freeze({
      kind: 'instruction-skill',
      workspaceId: input.workspaceId,
      componentId: 'builtin.instruction-skill',
      operationId: input.operationId,
      authority: 'instruction_only',
      skillId: 'builtin.instruction-skill',
      taskSha256: input.output.task_sha256,
      instructions: input.output.instructions as string,
    })
  }
  if (input.operation === 'mcp.call') {
    if (input.componentId !== 'builtin.readonly-mcp') return null
    const result = parseMcpResult(input.output)
    if (result === null) return null
    return Object.freeze({
      kind: 'readonly-mcp',
      workspaceId: input.workspaceId,
      componentId: 'builtin.readonly-mcp',
      operationId: input.operationId,
      result,
    })
  }
  if (input.operation === 'sandbox.run') {
    if (input.componentId !== 'builtin.sandbox-workload') return null
    const parsed = parseSandboxResult(input.output, input.componentId)
    if (parsed === null) return null
    return Object.freeze({
      kind: 'sandbox-workload',
      workspaceId: input.workspaceId,
      componentId: 'builtin.sandbox-workload',
      operationId: input.operationId,
      adapter: 'p34-sandbox.v1',
      schemaVersion: 1,
      workloadId: 'bounded-transform',
      runtimeInstanceId: parsed.runtimeInstanceId,
      status: 'completed',
      inputArtifactIds: parsed.inputArtifactIds,
      result: parsed.result,
      usage: parsed.usage,
    })
  }
  if (input.operation === 'local_adapter.open') {
    if (
      input.componentId !== 'knowledge.ebook' ||
      !exactRecord(input.output, [
        'adapter',
        'asset_id',
        'asset_sha256',
        'catalog',
        'component_manifest_sha256',
        'component_package_sha256',
        'destination',
        'logical_id',
        'renderer',
      ]) ||
      input.output.adapter !== 'trusted-local-app.v1' ||
      typeof input.output.asset_id !== 'string' ||
      !/^knowledge\.ebook\/\d+\.\d+\.\d+\/catalog$/u.test(input.output.asset_id) ||
      typeof input.output.asset_sha256 !== 'string' ||
      !SHA256.test(input.output.asset_sha256) ||
      typeof input.output.component_manifest_sha256 !== 'string' ||
      !SHA256.test(input.output.component_manifest_sha256) ||
      typeof input.output.component_package_sha256 !== 'string' ||
      !SHA256.test(input.output.component_package_sha256) ||
      input.output.destination !== 'workspace' ||
      (input.output.logical_id !== null &&
        (typeof input.output.logical_id !== 'string' ||
          !COMPONENT_SURFACE_ID.test(input.output.logical_id))) ||
      input.output.renderer !== 'host_declarative'
    ) {
      return null
    }
    const catalog = parseKnowledgeEbookCatalog(input.output.catalog)
    if (
      catalog === null ||
      input.output.asset_id !== `knowledge.ebook/${catalog.componentVersion}/catalog`
    ) {
      return null
    }
    return Object.freeze({
      kind: 'knowledge-ebook',
      workspaceId: input.workspaceId,
      componentId: 'knowledge.ebook',
      operationId: input.operationId,
      slotId: 'editor.component',
      renderer: 'host_declarative',
      assetId: input.output.asset_id,
      assetSha256: input.output.asset_sha256,
      componentManifestSha256: input.output.component_manifest_sha256,
      componentPackageSha256: input.output.component_package_sha256,
      destination: 'workspace',
      logicalId: input.output.logical_id,
      catalog,
    })
  }
  return null
}

export function createP7WorkspaceComponentSurfaceState(
  workspaceId: string | null,
): P7WorkspaceComponentSurfaceState {
  return Object.freeze({ workspaceId, surface: null, safeModeReason: null })
}

export function p7SetWorkspaceComponentSurface(
  state: P7WorkspaceComponentSurfaceState,
  input: Readonly<{
    workspaceId: string
    componentId: string
    operationId: string
    operation: P7ComponentOperation
    state: 'succeeded' | 'failed' | 'cancelled' | 'unknown'
    output: unknown
  }>,
): P7WorkspaceComponentSurfaceState {
  if (state.workspaceId !== input.workspaceId) return state
  if (input.state !== 'succeeded') {
    return Object.freeze({
      workspaceId: input.workspaceId,
      surface: null,
      safeModeReason: 'invocation-failed',
    })
  }
  const surface = p7ParseWorkspaceComponentSurface(input)
  return surface === null
    ? Object.freeze({
        workspaceId: input.workspaceId,
        surface: null,
        safeModeReason: 'malformed-output',
      })
    : Object.freeze({ workspaceId: input.workspaceId, surface, safeModeReason: null })
}

export function p7WorkspaceComponentResultEventLogLine(
  surface: P7WorkspaceComponentSurface,
): string {
  const result =
    surface.kind === 'readonly-mcp'
      ? surface.result.tool
      : surface.kind === 'instruction-skill'
        ? surface.authority
        : surface.kind === 'sandbox-workload'
          ? surface.result.kind
          : surface.kind === 'knowledge-ebook'
            ? surface.assetId
            : surface.viewId
  return `component ${surface.operationId} · ${surface.componentId} · ${surface.kind} · result ${result}`
}

export function p7EnterWorkspaceComponentSafeMode(
  workspaceId: string | null,
  reason: P7WorkspaceComponentSafeModeReason,
): P7WorkspaceComponentSurfaceState {
  return Object.freeze({ workspaceId, surface: null, safeModeReason: reason })
}

export function p7WorkspaceComponentSurfaceProjection(
  input: Readonly<{
    state: P7WorkspaceComponentSurfaceState
    viewWorkspaceId: string | null
    activeComponentIds: readonly string[]
  }>,
): P7WorkspaceComponentSurfaceProjection {
  if (input.viewWorkspaceId === null) {
    return Object.freeze({ status: 'idle', surface: null, safeModeReason: null })
  }
  if (input.state.workspaceId !== input.viewWorkspaceId) {
    return Object.freeze({ status: 'loading', surface: null, safeModeReason: null })
  }
  if (input.state.safeModeReason !== null) {
    return Object.freeze({
      status: 'safe-mode',
      surface: null,
      safeModeReason: input.state.safeModeReason,
    })
  }
  if (input.state.surface === null) {
    return Object.freeze({ status: 'idle', surface: null, safeModeReason: null })
  }
  if (!input.activeComponentIds.includes(input.state.surface.componentId)) {
    return Object.freeze({
      status: 'safe-mode',
      surface: null,
      safeModeReason: 'component-inactive',
    })
  }
  return Object.freeze({ status: 'ready', surface: input.state.surface, safeModeReason: null })
}

export type P7WorkspaceComponentHostRegion = 'editor' | 'sidebar' | 'settings' | 'status'

/** Project one validated result into exactly one host-owned region. */
export function p7WorkspaceComponentHostProjection(
  projection: P7WorkspaceComponentSurfaceProjection,
  region: P7WorkspaceComponentHostRegion,
): P7WorkspaceComponentSurfaceProjection {
  if (projection.status === 'safe-mode') {
    return region === 'editor'
      ? projection
      : Object.freeze({ status: 'idle', surface: null, safeModeReason: null })
  }
  if (projection.status !== 'ready' || projection.surface === null) {
    return Object.freeze({ status: 'idle', surface: null, safeModeReason: null })
  }
  const targetRegion =
    projection.surface.kind !== 'workspace-canvas'
      ? 'editor'
      : projection.surface.slotId === 'editor.component'
        ? 'editor'
        : projection.surface.slotId === 'sidebar.component'
          ? 'sidebar'
          : projection.surface.slotId === 'settings.component'
            ? 'settings'
            : 'status'
  return targetRegion === region
    ? projection
    : Object.freeze({ status: 'idle', surface: null, safeModeReason: null })
}
