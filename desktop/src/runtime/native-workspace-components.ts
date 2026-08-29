import {
  WORKSPACE_COMPONENT_FAMILIES,
  WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS,
  WORKSPACE_COMPONENT_OPERATIONS,
  type DesktopWorkspaceComponentActionResult,
  type DesktopWorkspaceComponentAuditEvent,
  type DesktopWorkspaceComponentBeginResult,
  type DesktopWorkspaceComponentCatalogItem,
  type DesktopWorkspaceComponentDecisionResult,
  type DesktopWorkspaceComponentEffect,
  type DesktopWorkspaceComponentEmergencyStopPrepareResult,
  type DesktopWorkspaceComponentEmergencyStopSettleResult,
  type DesktopWorkspaceComponentEmergencyStopTicket,
  type DesktopWorkspaceComponentExecutionTicket,
  type DesktopWorkspaceComponentGrantRequest,
  type DesktopWorkspaceComponentGrant,
  type DesktopWorkspaceComponentInstallation,
  type DesktopWorkspaceComponentJsonValue,
  type DesktopWorkspaceComponentLifecycleTicket,
  type DesktopWorkspaceComponentOperationRecord,
  type DesktopWorkspaceComponentPackageAttestationResult,
  type DesktopWorkspaceComponentOwnerPackageRegistration,
  type DesktopWorkspaceComponentProposal,
  type DesktopWorkspaceComponentProposalResult,
  type DesktopWorkspaceComponentRecovery,
  type DesktopWorkspaceComponentRecoverySettleResult,
  type DesktopWorkspaceComponentReconcileResult,
  type DesktopWorkspaceComponentReconciliation,
  type DesktopWorkspaceComponentRevocation,
  type DesktopWorkspaceComponentSettingsProperty,
  type DesktopWorkspaceComponentSettingsSchema,
  type DesktopWorkspaceComponentSlotDescriptor,
  type DesktopWorkspaceComponentSettleResult,
  type DesktopWorkspaceComponentSnapshot,
} from "../shared/workspace-components.ts";

const SHA256 = /^[a-f0-9]{64}$/u;
const WORKSPACE_ID = /^workspace_[a-f0-9]{32}$/u;
const COMPONENT_ID = /^[a-z][a-z0-9.-]{2,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const PROPOSAL_ID = /^proposal_[a-f0-9]{32}$/u;
const INSTALLATION_ID = /^installation_[a-f0-9]{32}$/u;
const OPERATION_ID = /^compop_[a-f0-9]{32}$/u;
const EFFECT_ID = /^effect_[a-f0-9]{32}$/u;
const RECONCILIATION_ID = /^reconcile_[a-f0-9]{32}$/u;
const RUNTIME_ID = /^runtime_[a-f0-9]{32}$/u;
const GRANT_ID = /^grant_[a-f0-9]{32}$/u;
const REVOCATION_ID = /^revocation_[a-f0-9]{32}$/u;
const RECOVERY_ID = /^recovery_[a-f0-9]{32}$/u;
const ERROR_CODE = /^[a-z][a-z0-9_]{2,95}$/u;
const LOGICAL_ID = /^[A-Za-z][A-Za-z0-9._:-]{1,127}$/u;
const ADAPTER_IDS = new Set([
  "builtin-ui.v1",
  "instruction-skill.v1",
  "readonly-mcp.v1",
  "p34-sandbox.v1",
  "trusted-local-app.v1",
]);
const FAMILIES = new Set<string>(WORKSPACE_COMPONENT_FAMILIES);
const ACTIONS = new Set<string>(WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS);
const OPERATIONS = new Set<string>(WORKSPACE_COMPONENT_OPERATIONS);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 1;
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 20 &&
    value.length <= 40 &&
    Number.isFinite(Date.parse(value))
  );
}

function isNullablePattern(
  value: unknown,
  pattern: RegExp,
): value is string | null {
  return value === null || (typeof value === "string" && pattern.test(value));
}

function parseStringArray(
  value: unknown,
  pattern: RegExp,
  maximum = 64,
): readonly string[] | null {
  if (
    !Array.isArray(value) ||
    value.length > maximum ||
    value.some((item) => typeof item !== "string" || !pattern.test(item)) ||
    new Set(value).size !== value.length
  ) {
    return null;
  }
  return Object.freeze([...value]) as readonly string[];
}

function parseJsonValue(
  value: unknown,
  depth = 0,
): DesktopWorkspaceComponentJsonValue | null | undefined {
  if (depth > 16) return undefined;
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") {
    return value.length <= 65_536 && !value.includes("\0") ? value : undefined;
  }
  if (typeof value === "number")
    return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) {
    if (value.length > 1024) return undefined;
    const parsed = value.map((item) => parseJsonValue(item, depth + 1));
    if (parsed.some((item) => item === undefined)) return undefined;
    return Object.freeze(
      parsed,
    ) as readonly DesktopWorkspaceComponentJsonValue[];
  }
  if (!isRecord(value) || Object.keys(value).length > 1024) return undefined;
  const parsed: Record<string, DesktopWorkspaceComponentJsonValue> = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      key.length === 0 ||
      key.length > 128 ||
      key === "__proto__" ||
      key === "prototype" ||
      key === "constructor"
    ) {
      return undefined;
    }
    const child = parseJsonValue(item, depth + 1);
    if (child === undefined) return undefined;
    parsed[key] = child;
  }
  return Object.freeze(parsed);
}

function parseGrantRequest(
  value: unknown,
): DesktopWorkspaceComponentGrantRequest | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "action",
      "logical_resource_id",
      "resource_version",
      "logical_service_id",
      "expires_in_seconds",
      "maximum_invocations",
      "maximum_bytes_in",
      "maximum_bytes_out",
      "maximum_tokens",
      "maximum_wall_time_ms",
      "maximum_cost_units",
    ]) ||
    typeof value.action !== "string" ||
    !LOGICAL_ID.test(value.action) ||
    !isNullablePattern(value.logical_resource_id, LOGICAL_ID) ||
    (value.resource_version !== null &&
      !isPositiveInteger(value.resource_version)) ||
    !isNullablePattern(value.logical_service_id, LOGICAL_ID) ||
    !isPositiveInteger(value.expires_in_seconds) ||
    !isPositiveInteger(value.maximum_invocations) ||
    !isNonNegativeInteger(value.maximum_bytes_in) ||
    !isNonNegativeInteger(value.maximum_bytes_out) ||
    !isNonNegativeInteger(value.maximum_tokens) ||
    !isPositiveInteger(value.maximum_wall_time_ms) ||
    !isPositiveInteger(value.maximum_cost_units)
  ) {
    return null;
  }
  return Object.freeze({
    action: value.action,
    logicalResourceId: value.logical_resource_id,
    resourceVersion: value.resource_version,
    logicalServiceId: value.logical_service_id,
    expiresInSeconds: value.expires_in_seconds,
    maximumInvocations: value.maximum_invocations,
    maximumBytesIn: value.maximum_bytes_in,
    maximumBytesOut: value.maximum_bytes_out,
    maximumTokens: value.maximum_tokens,
    maximumWallTimeMs: value.maximum_wall_time_ms,
    maximumCostUnits: value.maximum_cost_units,
  });
}

function parseSlotBinding(value: unknown) {
  if (
    !isRecord(value) ||
    !exact(value, ["binding_key", "configuration", "order_index", "slot_id"]) ||
    typeof value.slot_id !== "string" ||
    !LOGICAL_ID.test(value.slot_id) ||
    typeof value.binding_key !== "string" ||
    !LOGICAL_ID.test(value.binding_key) ||
    !isNonNegativeInteger(value.order_index) ||
    value.order_index > 10_000
  ) {
    return null;
  }
  const configuration = parseJsonValue(value.configuration);
  return configuration === undefined
    ? null
    : Object.freeze({
        slotId: value.slot_id,
        bindingKey: value.binding_key,
        orderIndex: value.order_index,
        configuration,
      });
}

function parseDependency(value: unknown) {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "policy_manifest_sha256",
      "manifest_sha256",
      "package_sha256",
      "version",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.policy_manifest_sha256 !== "string" ||
    !SHA256.test(value.policy_manifest_sha256) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256)
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.component_id,
    version: value.version,
    policyManifestSha256: value.policy_manifest_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
  });
}

function parseSlotDescriptor(
  value: unknown,
): DesktopWorkspaceComponentSlotDescriptor | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "cardinality",
      "maximum_order",
      "minimum_order",
      "slot_id",
    ]) ||
    typeof value.slot_id !== "string" ||
    !LOGICAL_ID.test(value.slot_id) ||
    (value.cardinality !== "one" && value.cardinality !== "many") ||
    !isNonNegativeInteger(value.minimum_order) ||
    !isNonNegativeInteger(value.maximum_order) ||
    value.minimum_order > value.maximum_order ||
    value.maximum_order > 10_000
  ) {
    return null;
  }
  return Object.freeze({
    slotId: value.slot_id,
    cardinality: value.cardinality,
    minimumOrder: value.minimum_order,
    maximumOrder: value.maximum_order,
  });
}

function parseSettingsProperty(
  value: unknown,
): DesktopWorkspaceComponentSettingsProperty | null {
  const allowed = new Set([
    "default",
    "enum",
    "max_length",
    "maximum",
    "minimum",
    "type",
  ]);
  if (
    !isRecord(value) ||
    !Object.keys(value).every((key) => allowed.has(key)) ||
    !Object.hasOwn(value, "type") ||
    !["boolean", "integer", "number", "string"].includes(String(value.type)) ||
    (value.minimum !== undefined &&
      (typeof value.minimum !== "number" || !Number.isFinite(value.minimum))) ||
    (value.maximum !== undefined &&
      (typeof value.maximum !== "number" || !Number.isFinite(value.maximum))) ||
    (value.max_length !== undefined &&
      (!isNonNegativeInteger(value.max_length) || value.max_length > 4096)) ||
    (value.enum !== undefined &&
      (!Array.isArray(value.enum) ||
        value.enum.length < 1 ||
        value.enum.length > 64))
  ) {
    return null;
  }
  const parsedDefault =
    value.default === undefined ? undefined : parseJsonValue(value.default);
  const parsedEnum =
    value.enum === undefined
      ? undefined
      : value.enum.map((item) => parseJsonValue(item));
  if (
    (parsedDefault === undefined && value.default !== undefined) ||
    parsedEnum?.some((item) => item === undefined)
  ) {
    return null;
  }
  return Object.freeze({
    type: value.type as DesktopWorkspaceComponentSettingsProperty["type"],
    ...(value.default === undefined ? {} : { default: parsedDefault }),
    ...(parsedEnum === undefined
      ? {}
      : {
          enum: Object.freeze(
            parsedEnum,
          ) as readonly DesktopWorkspaceComponentJsonValue[],
        }),
    ...(value.minimum === undefined ? {} : { minimum: value.minimum }),
    ...(value.maximum === undefined ? {} : { maximum: value.maximum }),
    ...(value.max_length === undefined ? {} : { maxLength: value.max_length }),
  });
}

function parseSettingsSchema(
  value: unknown,
): DesktopWorkspaceComponentSettingsSchema | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "additional_properties",
      "kind",
      "properties",
      "required",
      "version",
    ]) ||
    value.additional_properties !== false ||
    value.kind !== "closed_object" ||
    !isPositiveInteger(value.version) ||
    !isRecord(value.properties) ||
    Object.keys(value.properties).length > 128 ||
    !Array.isArray(value.required) ||
    value.required.length > 128 ||
    value.required.some(
      (item) =>
        typeof item !== "string" ||
        !/^[a-z][a-z0-9_]{0,63}$/u.test(item) ||
        !Object.hasOwn(value.properties as object, item),
    ) ||
    new Set(value.required).size !== value.required.length
  ) {
    return null;
  }
  const properties: Record<string, DesktopWorkspaceComponentSettingsProperty> =
    {};
  for (const [key, raw] of Object.entries(value.properties)) {
    if (!/^[a-z][a-z0-9_]{0,63}$/u.test(key)) return null;
    const property = parseSettingsProperty(raw);
    if (property === null) return null;
    properties[key] = property;
  }
  return Object.freeze({
    kind: "closed_object" as const,
    version: value.version,
    additionalProperties: false as const,
    properties: Object.freeze(properties),
    required: Object.freeze([...value.required]) as readonly string[],
  });
}

function parseCatalog(
  value: unknown,
): DesktopWorkspaceComponentCatalogItem | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "version",
      "family",
      "publisher_class",
      "display_name",
      "adapter_id",
      "policy_manifest_sha256",
      "manifest_sha256",
      "package_sha256",
      "operations",
      "slots",
      "dependencies",
      "conflicts",
      "budgets",
      "network",
      "recovery",
      "state_schema",
      "settings_schema",
      "available",
      "unavailable_reason",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.family !== "string" ||
    !FAMILIES.has(value.family) ||
    (value.publisher_class !== "source_owned" &&
      value.publisher_class !== "owner_reviewed") ||
    typeof value.display_name !== "string" ||
    value.display_name.length < 1 ||
    value.display_name.length > 128 ||
    typeof value.adapter_id !== "string" ||
    !ADAPTER_IDS.has(value.adapter_id) ||
    typeof value.policy_manifest_sha256 !== "string" ||
    !SHA256.test(value.policy_manifest_sha256) ||
    !isNullablePattern(value.manifest_sha256, SHA256) ||
    !isNullablePattern(value.package_sha256, SHA256) ||
    typeof value.available !== "boolean" ||
    (value.unavailable_reason !== null &&
      value.unavailable_reason !== "package_not_attested") ||
    value.available === (value.unavailable_reason !== null) ||
    (value.available
      ? value.manifest_sha256 === null || value.package_sha256 === null
      : value.manifest_sha256 !== null || value.package_sha256 !== null) ||
    !isRecord(value.budgets) ||
    !exact(value.budgets, [
      "max_calls",
      "max_bytes_in",
      "max_bytes_out",
      "max_tokens",
      "max_wall_time_ms",
      "max_cost_units",
      "max_retries",
      "max_concurrency",
    ]) ||
    !isRecord(value.network) ||
    !exact(value.network, ["required", "service_classes"]) ||
    typeof value.network.required !== "boolean" ||
    !isRecord(value.recovery) ||
    !exact(value.recovery, ["auto_replay_unknown", "retention", "safe_mode"]) ||
    value.recovery.auto_replay_unknown !== false ||
    !isRecord(value.state_schema) ||
    !exact(value.state_schema, ["kind", "version"]) ||
    value.state_schema.kind !== "canonical_json" ||
    !isPositiveInteger(value.state_schema.version)
  ) {
    return null;
  }
  const operations = parseStringArray(
    value.operations,
    /^[a-z][a-z0-9_.]{2,63}$/u,
    16,
  );
  const slots = parseArray(value.slots, parseSlotDescriptor, 64);
  const dependencies = parseArray(value.dependencies, parseDependency, 64);
  const conflicts = parseStringArray(value.conflicts, COMPONENT_ID, 64);
  const serviceClasses = parseStringArray(
    value.network.service_classes,
    LOGICAL_ID,
    32,
  );
  const settingsSchema = parseSettingsSchema(value.settings_schema);
  if (
    operations === null ||
    operations.length === 0 ||
    operations.some((item) => !OPERATIONS.has(item)) ||
    slots === null ||
    dependencies === null ||
    conflicts === null ||
    serviceClasses === null ||
    settingsSchema === null ||
    !isPositiveInteger(value.budgets.max_calls) ||
    !isNonNegativeInteger(value.budgets.max_bytes_in) ||
    !isNonNegativeInteger(value.budgets.max_bytes_out) ||
    !isNonNegativeInteger(value.budgets.max_tokens) ||
    !isPositiveInteger(value.budgets.max_wall_time_ms) ||
    !isPositiveInteger(value.budgets.max_cost_units) ||
    !isNonNegativeInteger(value.budgets.max_retries) ||
    !isPositiveInteger(value.budgets.max_concurrency) ||
    (value.recovery.retention !== "retain_workspace_data" &&
      value.recovery.retention !== "delete_component_data") ||
    value.recovery.safe_mode !== "disable_component"
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.component_id,
    version: value.version,
    family: value.family as DesktopWorkspaceComponentCatalogItem["family"],
    publisherClass: value.publisher_class,
    displayName: value.display_name,
    adapterId:
      value.adapter_id as DesktopWorkspaceComponentCatalogItem["adapterId"],
    policyManifestSha256: value.policy_manifest_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    operations:
      operations as DesktopWorkspaceComponentCatalogItem["operations"],
    slots,
    dependencies,
    conflicts,
    budgets: Object.freeze({
      maxCalls: value.budgets.max_calls,
      maxBytesIn: value.budgets.max_bytes_in,
      maxBytesOut: value.budgets.max_bytes_out,
      maxTokens: value.budgets.max_tokens,
      maxWallTimeMs: value.budgets.max_wall_time_ms,
      maxCostUnits: value.budgets.max_cost_units,
      maxRetries: value.budgets.max_retries,
      maxConcurrency: value.budgets.max_concurrency,
    }),
    network: Object.freeze({
      required: value.network.required,
      serviceClasses,
    }),
    recovery: Object.freeze({
      autoReplayUnknown: false as const,
      retention: value.recovery.retention,
      safeMode: "disable_component" as const,
    }),
    stateSchema: Object.freeze({
      kind: "canonical_json" as const,
      version: value.state_schema.version,
    }),
    settingsSchema,
    available: value.available,
    unavailableReason: value.unavailable_reason,
  });
}

function parseInstallation(
  value: unknown,
): DesktopWorkspaceComponentInstallation | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "installation_id",
      "workspace_id",
      "component_id",
      "version",
      "manifest_sha256",
      "package_sha256",
      "state",
      "revision",
      "binding_generation",
      "desired_configuration",
      "current_slot_bindings",
      "dependency_graph",
      "health",
      "last_error_code",
      "updated_at",
    ]) ||
    typeof value.installation_id !== "string" ||
    !INSTALLATION_ID.test(value.installation_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    ![
      "installed",
      "bound",
      "active",
      "blocked",
      "disabled",
      "revoked",
      "uninstalled",
    ].includes(String(value.state)) ||
    !isPositiveInteger(value.revision) ||
    !isPositiveInteger(value.binding_generation) ||
    !["unknown", "healthy", "degraded", "unavailable"].includes(
      String(value.health),
    ) ||
    !isNullablePattern(value.last_error_code, ERROR_CODE) ||
    !isTimestamp(value.updated_at)
  ) {
    return null;
  }
  const desiredConfiguration = parseJsonValue(value.desired_configuration);
  const currentSlotBindings = parseArray(
    value.current_slot_bindings,
    parseSlotBinding,
    64,
  );
  const dependencyGraph = parseArray(
    value.dependency_graph,
    parseDependency,
    64,
  );
  if (
    desiredConfiguration === undefined ||
    currentSlotBindings === null ||
    dependencyGraph === null
  ) {
    return null;
  }
  return Object.freeze({
    installationId: value.installation_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    version: value.version,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    state: value.state as DesktopWorkspaceComponentInstallation["state"],
    revision: value.revision,
    bindingGeneration: value.binding_generation,
    desiredConfiguration,
    currentSlotBindings,
    dependencyGraph,
    health: value.health as DesktopWorkspaceComponentInstallation["health"],
    lastErrorCode: value.last_error_code,
    updatedAt: value.updated_at,
  });
}

function parseProposal(
  value: unknown,
): DesktopWorkspaceComponentProposal | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "proposal_id",
      "workspace_id",
      "component_id",
      "target_version",
      "change_kind",
      "base_revision",
      "manifest_sha256",
      "package_sha256",
      "request_sha256",
      "requested_grants",
      "desired_configuration",
      "desired_slot_bindings",
      "dependency_graph",
      "source_kind",
      "source_reference",
      "decision",
      "created_at",
    ]) ||
    typeof value.proposal_id !== "string" ||
    !PROPOSAL_ID.test(value.proposal_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.target_version !== "string" ||
    !VERSION.test(value.target_version) ||
    typeof value.change_kind !== "string" ||
    !ACTIONS.has(value.change_kind) ||
    !isNonNegativeInteger(value.base_revision) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    !Array.isArray(value.requested_grants) ||
    value.requested_grants.length > 64 ||
    !Array.isArray(value.desired_slot_bindings) ||
    value.desired_slot_bindings.length > 64 ||
    !Array.isArray(value.dependency_graph) ||
    value.dependency_graph.length > 64 ||
    (value.source_kind !== "owner" && value.source_kind !== "assistant") ||
    (value.source_reference !== null &&
      (typeof value.source_reference !== "string" ||
        !LOGICAL_ID.test(value.source_reference))) ||
    (value.decision !== null &&
      value.decision !== "approved" &&
      value.decision !== "rejected") ||
    !isTimestamp(value.created_at)
  ) {
    return null;
  }
  const requestedGrants = value.requested_grants.map(parseGrantRequest);
  const desiredConfiguration = parseJsonValue(value.desired_configuration);
  const desiredSlotBindings = value.desired_slot_bindings.map(parseSlotBinding);
  const dependencyGraph = value.dependency_graph.map(parseDependency);
  if (
    requestedGrants.some((grant) => grant === null) ||
    desiredConfiguration === undefined ||
    desiredSlotBindings.some((binding) => binding === null) ||
    dependencyGraph.some((dependency) => dependency === null) ||
    (value.source_kind === "owner" && value.source_reference !== null) ||
    (value.source_kind === "assistant" && value.source_reference === null)
  ) {
    return null;
  }
  return Object.freeze({
    proposalId: value.proposal_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    targetVersion: value.target_version,
    changeKind:
      value.change_kind as DesktopWorkspaceComponentProposal["changeKind"],
    baseRevision: value.base_revision,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    requestSha256: value.request_sha256,
    requestedGrants: Object.freeze(
      requestedGrants,
    ) as readonly DesktopWorkspaceComponentGrantRequest[],
    desiredConfiguration,
    desiredSlotBindings: Object.freeze(
      desiredSlotBindings,
    ) as DesktopWorkspaceComponentProposal["desiredSlotBindings"],
    dependencyGraph: Object.freeze(
      dependencyGraph,
    ) as DesktopWorkspaceComponentProposal["dependencyGraph"],
    sourceKind: value.source_kind,
    sourceReference: value.source_reference,
    decision: value.decision,
    createdAt: value.created_at,
  });
}

function parseOperation(
  value: unknown,
): DesktopWorkspaceComponentOperationRecord | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "operation_id",
      "workspace_id",
      "component_id",
      "installation_id",
      "action",
      "request_sha256",
      "binding_generation",
      "state",
      "result_sha256",
      "evidence_sha256",
      "error_code",
      "created_at",
      "updated_at",
    ]) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    !isNullablePattern(value.installation_id, INSTALLATION_ID) ||
    typeof value.action !== "string" ||
    (!ACTIONS.has(value.action) &&
      !OPERATIONS.has(value.action) &&
      value.action !== "emergency_stop" &&
      value.action !== "recovery") ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    !isNonNegativeInteger(value.binding_generation) ||
    !["pending", "succeeded", "failed", "cancelled", "unknown"].includes(
      String(value.state),
    ) ||
    !isNullablePattern(value.result_sha256, SHA256) ||
    !isNullablePattern(value.evidence_sha256, SHA256) ||
    !isNullablePattern(value.error_code, ERROR_CODE) ||
    !isTimestamp(value.created_at) ||
    !isTimestamp(value.updated_at)
  ) {
    return null;
  }
  return Object.freeze({
    operationId: value.operation_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    installationId: value.installation_id,
    action: value.action,
    requestSha256: value.request_sha256,
    bindingGeneration: value.binding_generation,
    state: value.state as DesktopWorkspaceComponentOperationRecord["state"],
    resultSha256: value.result_sha256,
    evidenceSha256: value.evidence_sha256,
    errorCode: value.error_code,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseEffect(value: unknown): DesktopWorkspaceComponentEffect | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "effect_id",
      "operation_id",
      "workspace_id",
      "component_id",
      "state",
      "evidence_sha256",
      "created_at",
      "updated_at",
    ]) ||
    typeof value.effect_id !== "string" ||
    !EFFECT_ID.test(value.effect_id) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    !["none", "pending", "succeeded", "failed", "unknown"].includes(
      String(value.state),
    ) ||
    !isNullablePattern(value.evidence_sha256, SHA256) ||
    !isTimestamp(value.created_at) ||
    !isTimestamp(value.updated_at)
  ) {
    return null;
  }
  return Object.freeze({
    effectId: value.effect_id,
    operationId: value.operation_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    state: value.state as DesktopWorkspaceComponentEffect["state"],
    evidenceSha256: value.evidence_sha256,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseReconciliation(
  value: unknown,
): DesktopWorkspaceComponentReconciliation | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "reconciliation_id",
      "operation_id",
      "effect_id",
      "workspace_id",
      "outcome",
      "evidence_sha256",
      "created_at",
    ]) ||
    typeof value.reconciliation_id !== "string" ||
    !RECONCILIATION_ID.test(value.reconciliation_id) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.effect_id !== "string" ||
    !EFFECT_ID.test(value.effect_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    (value.outcome !== "succeeded" && value.outcome !== "failed") ||
    typeof value.evidence_sha256 !== "string" ||
    !SHA256.test(value.evidence_sha256) ||
    !isTimestamp(value.created_at)
  ) {
    return null;
  }
  return Object.freeze({
    reconciliationId: value.reconciliation_id,
    operationId: value.operation_id,
    effectId: value.effect_id,
    workspaceId: value.workspace_id,
    outcome: value.outcome,
    evidenceSha256: value.evidence_sha256,
    createdAt: value.created_at,
  });
}

function parseAudit(
  value: unknown,
): DesktopWorkspaceComponentAuditEvent | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "sequence",
      "event_id",
      "event_type",
      "payload",
      "created_at",
    ]) ||
    !isPositiveInteger(value.sequence) ||
    typeof value.event_id !== "string" ||
    !/^[a-z][a-z0-9_]{2,63}_[a-f0-9]{32}$/u.test(value.event_id) ||
    ![
      "workspace_component_proposed",
      "workspace_component_decided",
      "workspace_component_state_changed",
      "workspace_component_invocation_begun",
      "workspace_component_invocation_settled",
      "workspace_component_reconciled",
      "workspace_component_emergency_stopped",
      "workspace_component_recovery_blocked",
    ].includes(String(value.event_type)) ||
    !isTimestamp(value.created_at)
  ) {
    return null;
  }
  const payload = parseJsonValue(value.payload);
  if (payload === undefined) return null;
  return Object.freeze({
    sequence: value.sequence,
    eventId: value.event_id,
    eventType:
      value.event_type as DesktopWorkspaceComponentAuditEvent["eventType"],
    payload,
    createdAt: value.created_at,
  });
}

function parseUsageDimensions(value: unknown, withConcurrency: boolean) {
  const keys = [
    "calls",
    "bytes_in",
    "bytes_out",
    "tokens",
    "wall_time_ms",
    "cost_units",
    "retries",
    ...(withConcurrency ? ["concurrency"] : []),
  ];
  if (
    !isRecord(value) ||
    !exact(value, keys) ||
    keys.some((key) => !isNonNegativeInteger(value[key]))
  ) {
    return null;
  }
  return Object.freeze({
    calls: value.calls as number,
    bytesIn: value.bytes_in as number,
    bytesOut: value.bytes_out as number,
    tokens: value.tokens as number,
    wallTimeMs: value.wall_time_ms as number,
    costUnits: value.cost_units as number,
    retries: value.retries as number,
    ...(withConcurrency ? { concurrency: value.concurrency as number } : {}),
  });
}

function parseGrant(value: unknown): DesktopWorkspaceComponentGrant | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "id",
      "workspace_id",
      "installation_id",
      "binding_generation",
      "runtime_instance_id",
      "component_id",
      "version",
      "actions",
      "scope",
      "requires_network",
      "state",
      "not_before",
      "expires_at",
      "limits",
      "used",
      "remaining",
    ]) ||
    typeof value.id !== "string" ||
    !GRANT_ID.test(value.id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.installation_id !== "string" ||
    !INSTALLATION_ID.test(value.installation_id) ||
    !isPositiveInteger(value.binding_generation) ||
    typeof value.runtime_instance_id !== "string" ||
    !RUNTIME_ID.test(value.runtime_instance_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.requires_network !== "boolean" ||
    !["active", "revoked", "expired"].includes(String(value.state)) ||
    !isTimestamp(value.not_before) ||
    !isTimestamp(value.expires_at)
  ) {
    return null;
  }
  const actions = parseStringArray(
    value.actions,
    /^[a-z][a-z0-9_.]{2,63}$/u,
    16,
  );
  const scope = parseArray(value.scope, parseGrantRequest, 32);
  const limits = parseUsageDimensions(value.limits, true);
  const used = parseUsageDimensions(value.used, false);
  const remaining = parseUsageDimensions(value.remaining, false);
  if (
    actions === null ||
    actions.some((action) => !OPERATIONS.has(action)) ||
    scope === null ||
    limits === null ||
    used === null ||
    remaining === null
  ) {
    return null;
  }
  return Object.freeze({
    grantId: value.id,
    workspaceId: value.workspace_id,
    installationId: value.installation_id,
    bindingGeneration: value.binding_generation,
    runtimeInstanceId: value.runtime_instance_id,
    componentId: value.component_id,
    version: value.version,
    actions: actions as DesktopWorkspaceComponentGrant["actions"],
    scope,
    requiresNetwork: value.requires_network,
    state: value.state as DesktopWorkspaceComponentGrant["state"],
    notBefore: value.not_before,
    expiresAt: value.expires_at,
    limits,
    used,
    remaining,
  });
}

function parseRevocation(
  value: unknown,
): DesktopWorkspaceComponentRevocation | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "id",
      "workspace_id",
      "installation_id",
      "component_id",
      "binding_generation",
      "runtime_instance_id",
      "grant_id",
      "reason_code",
      "actor_type",
      "created_at",
    ]) ||
    typeof value.id !== "string" ||
    !REVOCATION_ID.test(value.id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.installation_id !== "string" ||
    !INSTALLATION_ID.test(value.installation_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    !isPositiveInteger(value.binding_generation) ||
    !isNullablePattern(value.runtime_instance_id, RUNTIME_ID) ||
    !isNullablePattern(value.grant_id, GRANT_ID) ||
    (value.runtime_instance_id === null && value.grant_id === null) ||
    typeof value.reason_code !== "string" ||
    !ERROR_CODE.test(value.reason_code) ||
    (value.actor_type !== "owner" && value.actor_type !== "system") ||
    !isTimestamp(value.created_at)
  ) {
    return null;
  }
  return Object.freeze({
    revocationId: value.id,
    workspaceId: value.workspace_id,
    installationId: value.installation_id,
    componentId: value.component_id,
    bindingGeneration: value.binding_generation,
    runtimeInstanceId: value.runtime_instance_id,
    grantId: value.grant_id,
    reasonCode: value.reason_code,
    actorType: value.actor_type,
    createdAt: value.created_at,
  });
}

function parseRecovery(
  value: unknown,
): DesktopWorkspaceComponentRecovery | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "recovery_id",
      "workspace_id",
      "component_id",
      "installation_id",
      "binding_generation",
      "previous_runtime_instance_id",
      "operation_id",
      "effect_id",
      "adapter_id",
      "runtime_instance_id",
      "workload_identity_digest",
      "configuration",
      "configuration_sha256",
      "slot_bindings",
      "slot_bindings_sha256",
      "dependency_graph",
      "dependency_graph_sha256",
      "quiesce_timeout_ms",
      "request_sha256",
      "manifest_sha256",
      "package_sha256",
      "state",
      "reason_code",
      "created_at",
    ]) ||
    typeof value.recovery_id !== "string" ||
    !RECOVERY_ID.test(value.recovery_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.installation_id !== "string" ||
    !INSTALLATION_ID.test(value.installation_id) ||
    !isPositiveInteger(value.binding_generation) ||
    typeof value.previous_runtime_instance_id !== "string" ||
    !RUNTIME_ID.test(value.previous_runtime_instance_id) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.effect_id !== "string" ||
    !EFFECT_ID.test(value.effect_id) ||
    typeof value.adapter_id !== "string" ||
    !ADAPTER_IDS.has(value.adapter_id) ||
    typeof value.runtime_instance_id !== "string" ||
    !RUNTIME_ID.test(value.runtime_instance_id) ||
    typeof value.workload_identity_digest !== "string" ||
    !SHA256.test(value.workload_identity_digest) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    !["pending", "succeeded", "failed", "unknown"].includes(
      String(value.state),
    ) ||
    typeof value.reason_code !== "string" ||
    !ERROR_CODE.test(value.reason_code) ||
    !isTimestamp(value.created_at)
  ) {
    return null;
  }
  return Object.freeze({
    recoveryId: value.recovery_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    installationId: value.installation_id,
    bindingGeneration: value.binding_generation,
    previousRuntimeInstanceId: value.previous_runtime_instance_id,
    operationId: value.operation_id,
    effectId: value.effect_id,
    adapterId:
      value.adapter_id as DesktopWorkspaceComponentCatalogItem["adapterId"],
    runtimeInstanceId: value.runtime_instance_id,
    workloadIdentityDigest: value.workload_identity_digest,
    requestSha256: value.request_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    state: value.state as DesktopWorkspaceComponentRecovery["state"],
    reasonCode: value.reason_code,
    createdAt: value.created_at,
  });
}

function parseArray<T>(
  value: unknown,
  parser: (item: unknown) => T | null,
  maximum: number,
): readonly T[] | null {
  if (!Array.isArray(value) || value.length > maximum) return null;
  const parsed = value.map(parser);
  if (parsed.some((item) => item === null)) return null;
  return Object.freeze(parsed) as readonly T[];
}

export function parseWorkspaceComponentSnapshot(
  value: unknown,
): DesktopWorkspaceComponentSnapshot | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "workspace_id",
      "catalog",
      "installations",
      "proposals",
      "operations",
      "effects",
      "grants",
      "revocations",
      "recoveries",
      "reconciliations",
      "audit",
    ]) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id)
  ) {
    return null;
  }
  const catalog = parseArray(value.catalog, parseCatalog, 512);
  const installations = parseArray(value.installations, parseInstallation, 512);
  const proposals = parseArray(value.proposals, parseProposal, 1024);
  const operations = parseArray(value.operations, parseOperation, 2048);
  const effects = parseArray(value.effects, parseEffect, 2048);
  const grants = parseArray(value.grants, parseGrant, 1024);
  const revocations = parseArray(value.revocations, parseRevocation, 2048);
  const recoveries = parseArray(value.recoveries, parseRecovery, 2048);
  const reconciliations = parseArray(
    value.reconciliations,
    parseReconciliation,
    2048,
  );
  const audit = parseArray(value.audit, parseAudit, 4096);
  if (
    catalog === null ||
    installations === null ||
    proposals === null ||
    operations === null ||
    effects === null ||
    grants === null ||
    revocations === null ||
    recoveries === null ||
    reconciliations === null ||
    audit === null ||
    [
      ...installations,
      ...proposals,
      ...operations,
      ...effects,
      ...grants,
      ...revocations,
      ...recoveries,
      ...reconciliations,
    ].some((item) => item.workspaceId !== value.workspace_id)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: value.workspace_id,
    catalog,
    installations,
    proposals,
    operations,
    effects,
    grants,
    revocations,
    recoveries,
    reconciliations,
    audit,
  });
}

export function parseWorkspaceComponentProposalResult(
  value: unknown,
): DesktopWorkspaceComponentProposalResult | null {
  if (
    !isRecord(value) ||
    !exact(value, ["proposal", "replayed"]) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const proposal = parseProposal(value.proposal);
  return proposal === null
    ? null
    : Object.freeze({ proposal, replayed: value.replayed });
}

export function parseWorkspaceComponentDecisionResult(
  value: unknown,
): DesktopWorkspaceComponentDecisionResult | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "workspace_id",
      "proposal_id",
      "request_sha256",
      "decision",
      "installation_revision",
    ]) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.proposal_id !== "string" ||
    !PROPOSAL_ID.test(value.proposal_id) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    (value.decision !== "approved" && value.decision !== "rejected") ||
    !isNonNegativeInteger(value.installation_revision)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: value.workspace_id,
    proposalId: value.proposal_id,
    requestSha256: value.request_sha256,
    decision: value.decision,
    installationRevision: value.installation_revision,
  });
}

export function parseWorkspaceComponentActionResult(
  value: unknown,
): DesktopWorkspaceComponentActionResult | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "operation",
      "installation",
      "lifecycle_ticket",
      "replayed",
    ]) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const operation = parseOperation(value.operation);
  const installation =
    value.installation === null ? null : parseInstallation(value.installation);
  const lifecycleTicket = parseLifecycleTicket(value.lifecycle_ticket);
  if (
    operation === null ||
    lifecycleTicket === null ||
    (installation !== null &&
      (operation.workspaceId !== installation.workspaceId ||
        operation.componentId !== installation.componentId)) ||
    operation.operationId !== lifecycleTicket.operationId ||
    operation.workspaceId !== lifecycleTicket.workspaceId ||
    operation.componentId !== lifecycleTicket.componentId ||
    operation.requestSha256 !== lifecycleTicket.requestSha256
  ) {
    return null;
  }
  return Object.freeze({
    operation,
    installation,
    lifecycleTicket,
    replayed: value.replayed,
  });
}

function parseLifecycleTicket(
  value: unknown,
): DesktopWorkspaceComponentLifecycleTicket | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "operation_id",
      "effect_id",
      "workspace_id",
      "component_id",
      "version",
      "action",
      "adapter_id",
      "installation_id",
      "binding_generation",
      "runtime_instance_id",
      "workload_identity_digest",
      "configuration",
      "configuration_sha256",
      "slot_bindings",
      "slot_bindings_sha256",
      "dependency_graph",
      "dependency_graph_sha256",
      "quiesce_timeout_ms",
      "request_sha256",
      "manifest_sha256",
      "package_sha256",
    ]) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.effect_id !== "string" ||
    !EFFECT_ID.test(value.effect_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.action !== "string" ||
    !ACTIONS.has(value.action) ||
    typeof value.adapter_id !== "string" ||
    !ADAPTER_IDS.has(value.adapter_id) ||
    !isNullablePattern(value.installation_id, INSTALLATION_ID) ||
    (value.binding_generation !== null &&
      !isPositiveInteger(value.binding_generation)) ||
    !isNullablePattern(value.runtime_instance_id, RUNTIME_ID) ||
    !isNullablePattern(value.workload_identity_digest, SHA256) ||
    typeof value.configuration_sha256 !== "string" ||
    !SHA256.test(value.configuration_sha256) ||
    typeof value.slot_bindings_sha256 !== "string" ||
    !SHA256.test(value.slot_bindings_sha256) ||
    typeof value.dependency_graph_sha256 !== "string" ||
    !SHA256.test(value.dependency_graph_sha256) ||
    !isPositiveInteger(value.quiesce_timeout_ms) ||
    value.quiesce_timeout_ms > 60_000 ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    (value.action === "install" &&
      (value.installation_id !== null || value.binding_generation !== null)) ||
    (value.action !== "install" &&
      (value.installation_id === null || value.binding_generation === null)) ||
    (value.action === "activate" &&
      (value.runtime_instance_id === null ||
        value.workload_identity_digest === null)) ||
    (value.action !== "activate" &&
      (value.runtime_instance_id !== null ||
        value.workload_identity_digest !== null))
  ) {
    return null;
  }
  const configuration = parseJsonValue(value.configuration);
  const slotBindings = parseArray(value.slot_bindings, parseSlotBinding, 64);
  const dependencyGraph = parseArray(
    value.dependency_graph,
    parseDependency,
    64,
  );
  if (
    configuration === undefined ||
    slotBindings === null ||
    dependencyGraph === null
  ) {
    return null;
  }
  return Object.freeze({
    operationId: value.operation_id,
    effectId: value.effect_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    version: value.version,
    action: value.action as DesktopWorkspaceComponentLifecycleTicket["action"],
    adapterId:
      value.adapter_id as DesktopWorkspaceComponentLifecycleTicket["adapterId"],
    installationId: value.installation_id,
    bindingGeneration: value.binding_generation,
    runtimeInstanceId: value.runtime_instance_id,
    workloadIdentityDigest: value.workload_identity_digest,
    configuration,
    configurationSha256: value.configuration_sha256,
    slotBindings,
    slotBindingsSha256: value.slot_bindings_sha256,
    dependencyGraph,
    dependencyGraphSha256: value.dependency_graph_sha256,
    quiesceTimeoutMs: value.quiesce_timeout_ms,
    requestSha256: value.request_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
  });
}

function parseTicket(
  value: unknown,
): DesktopWorkspaceComponentExecutionTicket | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "operation_id",
      "workspace_id",
      "component_id",
      "version",
      "action",
      "request_sha256",
      "arguments_sha256",
      "adapter_id",
      "configuration",
      "configuration_sha256",
      "slot_bindings",
      "slot_bindings_sha256",
      "dependency_graph",
      "dependency_graph_sha256",
      "manifest_sha256",
      "package_sha256",
      "binding_generation",
      "runtime_instance_id",
      "workload_identity_digest",
      "workload_fencing_token",
      "network_fencing_token",
      "expires_at",
    ]) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.action !== "string" ||
    !OPERATIONS.has(value.action) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    typeof value.arguments_sha256 !== "string" ||
    !SHA256.test(value.arguments_sha256) ||
    typeof value.adapter_id !== "string" ||
    !ADAPTER_IDS.has(value.adapter_id) ||
    typeof value.configuration_sha256 !== "string" ||
    !SHA256.test(value.configuration_sha256) ||
    typeof value.slot_bindings_sha256 !== "string" ||
    !SHA256.test(value.slot_bindings_sha256) ||
    typeof value.dependency_graph_sha256 !== "string" ||
    !SHA256.test(value.dependency_graph_sha256) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    !isPositiveInteger(value.binding_generation) ||
    typeof value.runtime_instance_id !== "string" ||
    !RUNTIME_ID.test(value.runtime_instance_id) ||
    typeof value.workload_identity_digest !== "string" ||
    !SHA256.test(value.workload_identity_digest) ||
    !isPositiveInteger(value.workload_fencing_token) ||
    (value.network_fencing_token !== null &&
      !isPositiveInteger(value.network_fencing_token)) ||
    !isTimestamp(value.expires_at)
  ) {
    return null;
  }
  const configuration = parseJsonValue(value.configuration);
  const slotBindings = parseArray(value.slot_bindings, parseSlotBinding, 64);
  const dependencyGraph = parseArray(
    value.dependency_graph,
    parseDependency,
    64,
  );
  if (
    configuration === undefined ||
    slotBindings === null ||
    dependencyGraph === null
  ) {
    return null;
  }
  return Object.freeze({
    operationId: value.operation_id,
    workspaceId: value.workspace_id,
    componentId: value.component_id,
    version: value.version,
    action: value.action as DesktopWorkspaceComponentExecutionTicket["action"],
    requestSha256: value.request_sha256,
    argumentsSha256: value.arguments_sha256,
    adapterId:
      value.adapter_id as DesktopWorkspaceComponentExecutionTicket["adapterId"],
    configuration,
    configurationSha256: value.configuration_sha256,
    slotBindings,
    slotBindingsSha256: value.slot_bindings_sha256,
    dependencyGraph,
    dependencyGraphSha256: value.dependency_graph_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    bindingGeneration: value.binding_generation,
    runtimeInstanceId: value.runtime_instance_id,
    workloadIdentityDigest: value.workload_identity_digest,
    workloadFencingToken: value.workload_fencing_token,
    networkFencingToken: value.network_fencing_token,
    expiresAt: value.expires_at,
  });
}

export function parseWorkspaceComponentBeginResult(
  value: unknown,
): DesktopWorkspaceComponentBeginResult | null {
  if (
    !isRecord(value) ||
    !exact(value, ["ticket", "replayed"]) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const ticket = parseTicket(value.ticket);
  return ticket === null
    ? null
    : Object.freeze({ ticket, replayed: value.replayed });
}

export function parseWorkspaceComponentSettleResult(
  value: unknown,
): DesktopWorkspaceComponentSettleResult | null {
  if (
    !isRecord(value) ||
    !exact(value, ["operation", "effect", "replayed"]) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const operation = parseOperation(value.operation);
  const effect = parseEffect(value.effect);
  if (
    operation === null ||
    effect === null ||
    operation.operationId !== effect.operationId ||
    operation.workspaceId !== effect.workspaceId ||
    operation.componentId !== effect.componentId
  ) {
    return null;
  }
  return Object.freeze({ operation, effect, replayed: value.replayed });
}

function parseEmergencyStopTicket(
  value: unknown,
): DesktopWorkspaceComponentEmergencyStopTicket | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "effect_id",
      "operation_id",
      "request_sha256",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.effect_id !== "string" ||
    !EFFECT_ID.test(value.effect_id) ||
    typeof value.operation_id !== "string" ||
    !OPERATION_ID.test(value.operation_id) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256)
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.component_id,
    effectId: value.effect_id,
    operationId: value.operation_id,
    requestSha256: value.request_sha256,
  });
}

export function parseWorkspaceComponentEmergencyStopPrepareResult(
  value: unknown,
): DesktopWorkspaceComponentEmergencyStopPrepareResult | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "fenced_component_ids",
      "replayed",
      "tickets",
      "workspace_id",
    ]) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const tickets = parseArray(value.tickets, parseEmergencyStopTicket, 512);
  const fencedComponentIds = parseStringArray(
    value.fenced_component_ids,
    COMPONENT_ID,
    512,
  );
  if (
    tickets === null ||
    fencedComponentIds === null ||
    tickets.length !== fencedComponentIds.length ||
    new Set(tickets.map((ticket) => ticket.componentId)).size !==
      tickets.length ||
    tickets.some((ticket) => !fencedComponentIds.includes(ticket.componentId))
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: value.workspace_id,
    tickets,
    fencedComponentIds,
    replayed: value.replayed,
  });
}

export function parseWorkspaceComponentEmergencyStopSettleResult(
  value: unknown,
): DesktopWorkspaceComponentEmergencyStopSettleResult | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "effect",
      "operation",
      "replayed",
      "workspace_id",
    ]) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const operation = parseOperation(value.operation);
  const effect = parseEffect(value.effect);
  return operation === null ||
    effect === null ||
    operation.workspaceId !== value.workspace_id ||
    operation.componentId !== value.component_id ||
    effect.workspaceId !== value.workspace_id ||
    effect.componentId !== value.component_id ||
    effect.operationId !== operation.operationId
    ? null
    : Object.freeze({
        workspaceId: value.workspace_id,
        componentId: value.component_id,
        operation,
        effect,
        replayed: value.replayed,
      });
}

export function parseWorkspaceComponentReconcileResult(
  value: unknown,
): DesktopWorkspaceComponentReconcileResult | null {
  if (
    !isRecord(value) ||
    !exact(value, ["operation", "effect", "reconciliation_id", "replayed"]) ||
    typeof value.reconciliation_id !== "string" ||
    !RECONCILIATION_ID.test(value.reconciliation_id) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const operation = parseOperation(value.operation);
  const effect = parseEffect(value.effect);
  if (
    operation === null ||
    effect === null ||
    operation.operationId !== effect.operationId ||
    operation.workspaceId !== effect.workspaceId ||
    operation.componentId !== effect.componentId
  ) {
    return null;
  }
  return Object.freeze({
    operation,
    effect,
    reconciliationId: value.reconciliation_id,
    replayed: value.replayed,
  });
}

export function parseWorkspaceComponentRecoverySettleResult(
  value: unknown,
): DesktopWorkspaceComponentRecoverySettleResult | null {
  if (
    !isRecord(value) ||
    !exact(value, ["effect", "operation", "recovery_id", "replayed"]) ||
    typeof value.recovery_id !== "string" ||
    !RECOVERY_ID.test(value.recovery_id) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const operation = parseOperation(value.operation);
  const effect = parseEffect(value.effect);
  if (
    operation === null ||
    effect === null ||
    operation.operationId !== effect.operationId ||
    operation.workspaceId !== effect.workspaceId ||
    operation.componentId !== effect.componentId
  ) {
    return null;
  }
  return Object.freeze({
    recoveryId: value.recovery_id,
    operation,
    effect,
    replayed: value.replayed,
  });
}

export function parseWorkspaceComponentPackageAttestationResult(
  value: unknown,
): DesktopWorkspaceComponentPackageAttestationResult | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "version",
      "adapter_id",
      "policy_manifest_sha256",
      "manifest_sha256",
      "package_sha256",
      "inventory_sha256",
      "attested_by",
      "created_at",
      "replayed",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.adapter_id !== "string" ||
    !ADAPTER_IDS.has(value.adapter_id) ||
    typeof value.policy_manifest_sha256 !== "string" ||
    !SHA256.test(value.policy_manifest_sha256) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    typeof value.inventory_sha256 !== "string" ||
    !SHA256.test(value.inventory_sha256) ||
    value.attested_by !== "runtime_manifest" ||
    !isTimestamp(value.created_at) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.component_id,
    version: value.version,
    adapterId:
      value.adapter_id as DesktopWorkspaceComponentPackageAttestationResult["adapterId"],
    policyManifestSha256: value.policy_manifest_sha256,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    inventorySha256: value.inventory_sha256,
    attestedBy: "runtime_manifest" as const,
    createdAt: value.created_at,
    replayed: value.replayed,
  });
}

export function parseWorkspaceComponentOwnerPackageRegistration(
  value: unknown,
): DesktopWorkspaceComponentOwnerPackageRegistration | null {
  if (
    !isRecord(value) ||
    !exact(value, [
      "component_id",
      "version",
      "manifest_sha256",
      "package_sha256",
      "publisher_class",
      "registered_at",
      "replayed",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    typeof value.manifest_sha256 !== "string" ||
    !SHA256.test(value.manifest_sha256) ||
    typeof value.package_sha256 !== "string" ||
    !SHA256.test(value.package_sha256) ||
    value.publisher_class !== "owner_reviewed" ||
    !isTimestamp(value.registered_at) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.component_id,
    version: value.version,
    manifestSha256: value.manifest_sha256,
    packageSha256: value.package_sha256,
    publisherClass: "owner_reviewed" as const,
    registeredAt: value.registered_at,
    replayed: value.replayed,
  });
}
