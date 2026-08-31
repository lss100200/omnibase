import { createHash } from "node:crypto";
import path from "node:path";

import type {
  DesktopOperationResult,
  DesktopWorkspaceComponentActionInput,
  DesktopWorkspaceComponentActionResult,
  DesktopWorkspaceComponentBeginInput,
  DesktopWorkspaceComponentBeginResult,
  DesktopWorkspaceComponentEmergencyStopInput,
  DesktopWorkspaceComponentEmergencyStopPrepareResult,
  DesktopWorkspaceComponentEmergencyStopResult,
  DesktopWorkspaceComponentNativeEmergencyStopInput,
  DesktopWorkspaceComponentNativeEmergencyStopResult,
  DesktopWorkspaceComponentExecutionTicket,
  DesktopWorkspaceComponentInvokeInput,
  DesktopWorkspaceComponentInvokeResult,
  DesktopWorkspaceComponentJsonValue,
  DesktopWorkspaceComponentLifecycleTicket,
  DesktopWorkspaceComponentNativeActionInput,
  DesktopWorkspaceComponentSettleInput,
  DesktopWorkspaceComponentSettleResult,
} from "../shared/ipc-contract.ts";
import type { WorkspaceFiles } from "./workspace-files.ts";
import type { RuntimeManagerComponentRecoveryContext } from "./runtime-manager.ts";
import { ClosedMcpHost } from "./closed-mcp-host.ts";
import {
  readSourceComponentBinaryAsset,
  readSourceComponentPayload,
  readSourceComponentPayloadAsset,
  type SourceComponentBinaryAsset,
  type SourceComponentPayloadAsset,
} from "./component-package-registry.ts";

const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const MAX_ADAPTER_OUTPUT_BYTES = 4 * 1024 * 1024;
const INVOCATION_FENCING_ACTIONS = new Set<
  DesktopWorkspaceComponentActionInput["action"]
>(["disable", "upgrade", "rollback", "revoke", "uninstall"]);

type AdapterOutput = DesktopWorkspaceComponentJsonValue;
type ComponentTicketIdentity =
  | DesktopWorkspaceComponentExecutionTicket
  | DesktopWorkspaceComponentLifecycleTicket;
type SourceComponentIdentity = Pick<
  DesktopWorkspaceComponentExecutionTicket,
  "componentId" | "manifestSha256" | "packageSha256" | "version"
>;
type DeclarativeViewDescriptor = Readonly<{
  kind: "workspace_summary";
  title: string;
  sections: readonly Readonly<{
    id: string;
    label: string;
    source: "installation" | "health" | "grants" | "configuration";
  }>[];
}>;

const SEALED_MCP_TOOL_DESCRIPTORS = Object.freeze({
  omnibase_files_list: Object.freeze({
    input: Object.freeze({ directory: "logical_relative_directory" }),
    operation: "workspace.files.list",
    output: "bounded_logical_file_inventory",
    tool_id: "omnibase_files_list",
  }),
  omnibase_files_read: Object.freeze({
    input: Object.freeze({ path: "logical_relative_file" }),
    operation: "workspace.files.read",
    output: "bounded_utf8_file",
    tool_id: "omnibase_files_read",
  }),
  omnibase_files_hash: Object.freeze({
    input: Object.freeze({ path: "logical_relative_file" }),
    operation: "workspace.files.hash",
    output: "bounded_file_identity",
    tool_id: "omnibase_files_hash",
  }),
  omnibase_text_search: Object.freeze({
    input: Object.freeze({
      path: "logical_relative_file",
      query: "bounded_text",
    }),
    operation: "workspace.text.search",
    output: "bounded_match_inventory",
    tool_id: "omnibase_text_search",
  }),
});

export interface ComponentNativeExecutionBoundary {
  applyWorkspaceComponentAction(
    input: DesktopWorkspaceComponentNativeActionInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentActionResult>>;
  beginWorkspaceComponentInvocation(
    input: DesktopWorkspaceComponentBeginInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentBeginResult>>;
  settleWorkspaceComponentInvocation(
    input: DesktopWorkspaceComponentSettleInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentSettleResult>>;
  emergencyStopWorkspaceComponents(
    input: DesktopWorkspaceComponentNativeEmergencyStopInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentNativeEmergencyStopResult>
  >;
}

export interface TrustedSandboxWorkload {
  readonly bytes: Buffer;
  readonly entrypoint: "transform";
  readonly memoryMaxBytes: 65_536;
  readonly network: "no_imports";
  readonly sha256: string;
}

export interface TrustedSandboxComponentAdapter {
  activate?(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentLifecycleTicket;
      workload: TrustedSandboxWorkload;
      signal: AbortSignal;
    }>,
  ): Promise<Readonly<{ health: "healthy"; evidence: AdapterOutput }>>;
  stop?(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentLifecycleTicket;
      signal: AbortSignal;
    }>,
  ): Promise<Readonly<{ evidence: AdapterOutput }>>;
  execute(
    input: Readonly<{
      ticket: DesktopWorkspaceComponentExecutionTicket;
      workloadId: string;
      workload: TrustedSandboxWorkload;
      inputArtifactIds: readonly string[];
      signal: AbortSignal;
    }>,
  ): Promise<AdapterOutput>;
}

export interface ComponentRuntimeBrokerOptions {
  readonly native: ComponentNativeExecutionBoundary;
  readonly workspaceFiles: WorkspaceFiles;
  readonly runtimeRoot: string;
  readonly getVerifiedRuntimeFileSha256?: (
    relativePath: string,
  ) => string | null;
  readonly readSourceComponentPayload?: typeof readSourceComponentPayload;
  readonly readSourceComponentPayloadAsset?: typeof readSourceComponentPayloadAsset;
  readonly readSourceComponentBinaryAsset?: typeof readSourceComponentBinaryAsset;
  readonly sandboxAdapter?: TrustedSandboxComponentAdapter;
  readonly ownerPackageStore?: Readonly<{
    readView(
      packageSha256: string,
      manifestSha256: string,
      componentId: string,
    ): Promise<Readonly<{
      kind: "workspace_summary";
      title: string;
      sections: readonly Readonly<{
        id: string;
        label: string;
        source: "installation" | "health" | "grants" | "configuration";
      }>[];
    }> | null>;
  }>;
  readonly now?: () => number;
}

interface ActiveExecution {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly controller: AbortController;
  readonly kind: "admission" | "invocation" | "lifecycle";
  readonly settled: Promise<void>;
  readonly complete: () => void;
}

class ComponentAdapterError extends Error {
  constructor(
    readonly code: string,
    readonly outcomeUnknown = false,
  ) {
    super(code);
  }
}

function success<T>(value: T): DesktopOperationResult<T> {
  return Object.freeze({ ok: true as const, value: Object.freeze(value) });
}

function failure<T>(code: string): DesktopOperationResult<T> {
  return Object.freeze({
    ok: false as const,
    error: Object.freeze({ code }),
  });
}

function canonicalJson(value: DesktopWorkspaceComponentJsonValue): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<
      Record<string, DesktopWorkspaceComponentJsonValue>
    >;
    return `{${Object.keys(record)
      .sort()
      .map(
        (key) => `${JSON.stringify(key)}:${canonicalJson(record[key] ?? null)}`,
      )
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digestJson(value: DesktopWorkspaceComponentJsonValue): string {
  return createHash("sha256")
    .update(canonicalJson(value), "utf8")
    .digest("hex");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  return (
    actual.length === keys.length &&
    actual.every((key, index) => key === keys[index])
  );
}

function boundedText(value: unknown, required = false): boolean {
  return (
    value === null ||
    (typeof value === "string" &&
      value.length <= 524_288 &&
      !value.includes("\0") &&
      (!required || value.trim().length > 0))
  );
}

function stringList(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.length <= 256 &&
    value.every((item) => boundedText(item, true))
  );
}

function validKnowledgeEbookCatalog(
  value: unknown,
  expectedVersion: string,
): value is DesktopWorkspaceComponentJsonValue {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "component_id",
      "component_version",
      "documents",
      "glossary",
      "invariants",
      "modules",
      "schema_version",
      "source_snapshot_sha256",
    ]) ||
    value.component_id !== "knowledge.ebook" ||
    value.component_version !== expectedVersion ||
    value.schema_version !== 1 ||
    typeof value.source_snapshot_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.source_snapshot_sha256) ||
    !Array.isArray(value.documents) ||
    value.documents.length > 1024 ||
    !Array.isArray(value.glossary) ||
    value.glossary.length > 10_000 ||
    !Array.isArray(value.invariants) ||
    value.invariants.length > 10_000 ||
    !Array.isArray(value.modules) ||
    value.modules.length > 10_000
  ) {
    return false;
  }
  let sectionCount = 0;
  for (const document of value.documents) {
    if (
      !isRecord(document) ||
      !hasExactKeys(document, [
        "content",
        "file_hash",
        "id",
        "sections",
        "summary",
        "title",
        "type",
      ]) ||
      !boundedText(document.content) ||
      !boundedText(document.file_hash) ||
      !boundedText(document.id, true) ||
      !boundedText(document.summary) ||
      !boundedText(document.title, true) ||
      !boundedText(document.type) ||
      !Array.isArray(document.sections)
    ) {
      return false;
    }
    sectionCount += document.sections.length;
    if (sectionCount > 20_000) return false;
    for (const section of document.sections) {
      if (
        !isRecord(section) ||
        !hasExactKeys(section, [
          "content",
          "explanation",
          "heading",
          "id",
          "level",
          "position",
          "theme",
        ]) ||
        !boundedText(section.content) ||
        !boundedText(section.explanation) ||
        !boundedText(section.heading) ||
        !boundedText(section.id, true) ||
        !Number.isSafeInteger(section.level) ||
        !Number.isSafeInteger(section.position) ||
        !boundedText(section.theme)
      ) {
        return false;
      }
    }
  }
  return (
    value.glossary.every(
      (item) =>
        isRecord(item) &&
        hasExactKeys(item, ["category", "definition", "explanation", "term"]) &&
        boundedText(item.category) &&
        boundedText(item.definition) &&
        boundedText(item.explanation) &&
        boundedText(item.term, true),
    ) &&
    value.invariants.every(
      (item) =>
        isRecord(item) &&
        hasExactKeys(item, [
          "content",
          "explanation",
          "id",
          "modules",
          "phase",
          "severity",
          "title",
        ]) &&
        boundedText(item.content) &&
        boundedText(item.explanation) &&
        boundedText(item.id, true) &&
        stringList(item.modules) &&
        boundedText(item.phase) &&
        boundedText(item.severity) &&
        boundedText(item.title, true),
    ) &&
    value.modules.every(
      (item) =>
        isRecord(item) &&
        hasExactKeys(item, [
          "dependencies",
          "description",
          "id",
          "invariants",
          "name",
          "summary",
          "verification",
        ]) &&
        stringList(item.dependencies) &&
        boundedText(item.description) &&
        boundedText(item.id, true) &&
        stringList(item.invariants) &&
        boundedText(item.name, true) &&
        boundedText(item.summary) &&
        stringList(item.verification),
    )
  );
}

function adapterErrorCode(error: unknown, aborted: boolean): string {
  if (aborted) return "desktop_component_invocation_cancelled";
  if (error instanceof ComponentAdapterError) return error.code;
  return "desktop_component_adapter_failed";
}

function ticketMatches(
  ticket: DesktopWorkspaceComponentExecutionTicket,
  input: DesktopWorkspaceComponentInvokeInput,
): boolean {
  return (
    ticket.workspaceId === input.workspaceId &&
    ticket.componentId === input.componentId &&
    ticket.action === input.operation &&
    ticket.adapterId === expectedInvocationAdapter(input.operation) &&
    ticket.manifestSha256 === input.manifestSha256 &&
    ticket.packageSha256 === input.packageSha256 &&
    ticket.configurationSha256 === digestJson(ticket.configuration) &&
    ticket.slotBindingsSha256 ===
      digestJson(
        ticket.slotBindings.map((binding) => ({
          binding_key: binding.bindingKey,
          configuration: binding.configuration,
          order_index: binding.orderIndex,
          slot_id: binding.slotId,
        })),
      ) &&
    ticket.dependencyGraphSha256 ===
      digestJson(
        ticket.dependencyGraph.map((dependency) => ({
          component_id: dependency.componentId,
          manifest_sha256: dependency.manifestSha256,
          package_sha256: dependency.packageSha256,
          policy_manifest_sha256: dependency.policyManifestSha256,
          version: dependency.version,
        })),
      ) &&
    ticket.bindingGeneration === input.bindingGeneration &&
    ticket.argumentsSha256 === digestJson(input.arguments) &&
    ticket.requestSha256 === expectedRequestSha256(input) &&
    Date.parse(ticket.expiresAt) > 0
  );
}

function expectedRequestSha256(
  input: DesktopWorkspaceComponentInvokeInput,
): string {
  return digestJson({
    action: input.operation,
    arguments_sha256: digestJson(input.arguments),
    binding_generation: input.bindingGeneration,
    bytes_in: Buffer.byteLength(canonicalJson(input.arguments), "utf8"),
    bytes_out_reserved: input.bytesOutReserved,
    component_id: input.componentId,
    cost_units: input.costUnits,
    expected_revision: input.expectedRevision,
    logical_resource_id: input.logicalResourceId ?? null,
    logical_service_id: input.logicalServiceId ?? null,
    manifest_sha256: input.manifestSha256,
    package_sha256: input.packageSha256,
    resource_version: input.resourceVersion ?? null,
    tokens_reserved: input.tokensReserved,
    wall_time_ms: input.wallTimeMs,
    workspace_id: input.workspaceId,
  });
}

function expectedComponent(
  operation: DesktopWorkspaceComponentInvokeInput["operation"],
): string | null {
  switch (operation) {
    case "ui.render":
      return null;
    case "skill.resolve":
      return "builtin.instruction-skill";
    case "mcp.call":
      return "builtin.readonly-mcp";
    case "sandbox.run":
      return "builtin.sandbox-workload";
    case "local_adapter.open":
      return "knowledge.ebook";
  }
}

function expectedInvocationAdapter(
  operation: DesktopWorkspaceComponentInvokeInput["operation"],
): DesktopWorkspaceComponentExecutionTicket["adapterId"] {
  switch (operation) {
    case "ui.render":
      return "builtin-ui.v1";
    case "skill.resolve":
      return "instruction-skill.v1";
    case "mcp.call":
      return "readonly-mcp.v1";
    case "sandbox.run":
      return "p34-sandbox.v1";
    case "local_adapter.open":
      return "trusted-local-app.v1";
  }
}

function expectedLifecycleRequestSha256(
  input: DesktopWorkspaceComponentActionInput,
): string {
  return digestJson({
    action: input.action,
    component_id: input.componentId,
    expected_revision: input.expectedRevision,
    manifest_sha256: input.manifestSha256,
    package_sha256: input.packageSha256,
    proposal_id: input.proposalId,
    request_sha256: input.requestSha256,
    workspace_id: input.workspaceId,
  });
}

function lifecycleTicketMatches(
  input: DesktopWorkspaceComponentActionInput,
  result: DesktopWorkspaceComponentActionResult,
): boolean {
  const ticket = result.lifecycleTicket;
  return (
    ticket.operationId === result.operation.operationId &&
    ticket.workspaceId === input.workspaceId &&
    ticket.componentId === input.componentId &&
    ticket.action === input.action &&
    ticket.requestSha256 === expectedLifecycleRequestSha256(input) &&
    ticket.manifestSha256 === input.manifestSha256 &&
    ticket.packageSha256 === input.packageSha256 &&
    ticket.configurationSha256 === digestJson(ticket.configuration) &&
    ticket.slotBindingsSha256 ===
      digestJson(
        ticket.slotBindings.map((binding) => ({
          binding_key: binding.bindingKey,
          configuration: binding.configuration,
          order_index: binding.orderIndex,
          slot_id: binding.slotId,
        })),
      ) &&
    ticket.dependencyGraphSha256 ===
      digestJson(
        ticket.dependencyGraph.map((dependency) => ({
          component_id: dependency.componentId,
          manifest_sha256: dependency.manifestSha256,
          package_sha256: dependency.packageSha256,
          policy_manifest_sha256: dependency.policyManifestSha256,
          version: dependency.version,
        })),
      ) &&
    !thisActiveMismatch(result)
  );
}

function sameLifecycleTicket(
  left: DesktopWorkspaceComponentLifecycleTicket,
  right: DesktopWorkspaceComponentLifecycleTicket,
): boolean {
  return (
    canonicalJson(left as unknown as DesktopWorkspaceComponentJsonValue) ===
    canonicalJson(right as unknown as DesktopWorkspaceComponentJsonValue)
  );
}

function thisActiveMismatch(
  result: DesktopWorkspaceComponentActionResult,
): boolean {
  const ticket = result.lifecycleTicket;
  const installation = result.installation;
  if (ticket.action === "install") {
    return ticket.installationId !== null || ticket.bindingGeneration !== null;
  }
  if (ticket.action === "uninstall" && result.operation.state === "succeeded") {
    return installation !== null;
  }
  return (
    installation === null ||
    ticket.installationId !== installation.installationId ||
    ticket.bindingGeneration !== installation.bindingGeneration
  );
}

function beginInput(
  input: DesktopWorkspaceComponentInvokeInput,
): DesktopWorkspaceComponentBeginInput {
  const bytesIn = Buffer.byteLength(canonicalJson(input.arguments), "utf8");
  return Object.freeze({
    workspaceId: input.workspaceId,
    componentId: input.componentId,
    action: input.operation,
    argumentsSha256: digestJson(input.arguments),
    expectedRevision: input.expectedRevision,
    bindingGeneration: input.bindingGeneration,
    manifestSha256: input.manifestSha256,
    packageSha256: input.packageSha256,
    idempotencyKey: input.idempotencyKey,
    ...(input.logicalResourceId === undefined
      ? {}
      : { logicalResourceId: input.logicalResourceId }),
    ...(input.resourceVersion === undefined
      ? {}
      : { resourceVersion: input.resourceVersion }),
    ...(input.logicalServiceId === undefined
      ? {}
      : { logicalServiceId: input.logicalServiceId }),
    bytesIn,
    bytesOutReserved: input.bytesOutReserved,
    tokensReserved: input.tokensReserved,
    wallTimeMs: input.wallTimeMs,
    costUnits: input.costUnits,
  });
}

export class ComponentRuntimeBroker {
  readonly #options: ComponentRuntimeBrokerOptions;
  readonly #active = new Map<string, ActiveExecution>();
  readonly #componentFences = new Map<string, number>();
  readonly #activated = new Map<
    string,
    DesktopWorkspaceComponentLifecycleTicket
  >();
  readonly #mcpHost: ClosedMcpHost;
  readonly #unsubscribeWorkspaceInvalidation: () => void;
  #admissionSequence = 0;

  constructor(options: ComponentRuntimeBrokerOptions) {
    if (!path.isAbsolute(options.runtimeRoot)) {
      throw new Error("component_runtime_root_invalid");
    }
    this.#options = options;
    this.#mcpHost = new ClosedMcpHost(options.workspaceFiles);
    this.#unsubscribeWorkspaceInvalidation =
      options.workspaceFiles.onInvalidate((workspaceId) => {
        if (workspaceId !== null) this.stopWorkspace(workspaceId);
      });
  }

  async recoverStartup(
    input: RuntimeManagerComponentRecoveryContext,
  ): Promise<void> {
    const { recovery, snapshot } = input;
    const installation = snapshot.installations.find(
      (item) =>
        item.installationId === recovery.installationId &&
        item.workspaceId === recovery.workspaceId &&
        item.componentId === recovery.componentId &&
        item.bindingGeneration === recovery.bindingGeneration &&
        item.manifestSha256 === recovery.manifestSha256 &&
        item.packageSha256 === recovery.packageSha256,
    );
    const catalog = snapshot.catalog.find(
      (item) =>
        item.componentId === recovery.componentId &&
        item.version === installation?.version &&
        item.adapterId === recovery.adapterId &&
        item.available &&
        item.manifestSha256 === recovery.manifestSha256 &&
        item.packageSha256 === recovery.packageSha256,
    );
    const ticket: DesktopWorkspaceComponentLifecycleTicket | null =
      installation === undefined || catalog === undefined
        ? null
        : Object.freeze({
            operationId: recovery.operationId,
            effectId: recovery.effectId,
            workspaceId: recovery.workspaceId,
            componentId: recovery.componentId,
            version: installation.version,
            action: "activate" as const,
            adapterId: recovery.adapterId,
            installationId: recovery.installationId,
            bindingGeneration: recovery.bindingGeneration,
            runtimeInstanceId: recovery.runtimeInstanceId,
            workloadIdentityDigest: recovery.workloadIdentityDigest,
            configuration: installation.desiredConfiguration,
            configurationSha256: digestJson(installation.desiredConfiguration),
            slotBindings: installation.currentSlotBindings,
            slotBindingsSha256: digestJson(
              installation.currentSlotBindings.map((binding) => ({
                binding_key: binding.bindingKey,
                configuration: binding.configuration,
                order_index: binding.orderIndex,
                slot_id: binding.slotId,
              })),
            ),
            dependencyGraph: installation.dependencyGraph,
            dependencyGraphSha256: digestJson(
              installation.dependencyGraph.map((dependency) => ({
                component_id: dependency.componentId,
                manifest_sha256: dependency.manifestSha256,
                package_sha256: dependency.packageSha256,
                policy_manifest_sha256: dependency.policyManifestSha256,
                version: dependency.version,
              })),
            ),
            quiesceTimeoutMs: 5_000,
            requestSha256: recovery.requestSha256,
            manifestSha256: recovery.manifestSha256,
            packageSha256: recovery.packageSha256,
          });
    let outcome: "succeeded" | "failed" | "unknown" = "succeeded";
    let healthState: "healthy" | "unhealthy" | "unknown" = "healthy";
    let errorCode: string | null = null;
    let adapterEvidence: AdapterOutput = null;
    let sandboxDispatched = false;
    try {
      if (
        snapshot.workspaceId !== recovery.workspaceId ||
        recovery.state !== "pending" ||
        ticket === null
      ) {
        throw new ComponentAdapterError(
          "desktop_component_recovery_identity_invalid",
        );
      }
      const identity: SourceComponentIdentity = ticket;
      switch (recovery.adapterId) {
        case "builtin-ui.v1":
          if (
            catalog?.publisherClass === "owner_reviewed"
              ? (await this.#options.ownerPackageStore?.readView(
                  recovery.packageSha256,
                  recovery.manifestSha256,
                  recovery.componentId,
                )) == null
              : (await this.#sourceView(identity)) == null
          ) {
            throw new ComponentAdapterError(
              "desktop_component_owner_package_asset_unavailable",
            );
          }
          adapterEvidence = Object.freeze({ adapter: recovery.adapterId });
          break;
        case "instruction-skill.v1":
          await this.#sourceInstruction(identity);
          adapterEvidence = Object.freeze({ adapter: recovery.adapterId });
          break;
        case "readonly-mcp.v1":
          await this.#sourceMcpTools(identity);
          adapterEvidence = Object.freeze({ adapter: recovery.adapterId });
          break;
        case "trusted-local-app.v1": {
          const ebook = await this.#loadKnowledgeEbook(
            identity,
            new AbortController().signal,
          );
          adapterEvidence = Object.freeze({
            adapter: recovery.adapterId,
            asset_sha256: ebook.sha256,
          });
          break;
        }
        case "p34-sandbox.v1":
          if (
            this.#options.sandboxAdapter?.activate === undefined ||
            this.#options.sandboxAdapter.stop === undefined
          ) {
            throw new ComponentAdapterError(
              "desktop_component_sandbox_runtime_unavailable",
            );
          }
          const workload = await this.#sourceSandboxWorkload(identity);
          sandboxDispatched = true;
          adapterEvidence = (
            await this.#options.sandboxAdapter.activate({
              ticket,
              workload,
              signal: new AbortController().signal,
            })
          ).evidence;
          break;
      }
    } catch (error) {
      outcome = sandboxDispatched ? "unknown" : "failed";
      healthState = sandboxDispatched ? "unknown" : "unhealthy";
      errorCode = sandboxDispatched
        ? "desktop_component_recovery_outcome_unknown"
        : adapterErrorCode(error, false);
      adapterEvidence = Object.freeze({ error_code: errorCode });
    }
    const evidence = Object.freeze({
      adapter_evidence: adapterEvidence,
      component_id: recovery.componentId,
      effect_id: recovery.effectId,
      operation_id: recovery.operationId,
      outcome,
      recovery_id: recovery.recoveryId,
      request_sha256: recovery.requestSha256,
      runtime_instance_id: recovery.runtimeInstanceId,
      workload_identity_digest: recovery.workloadIdentityDigest,
    });
    const settled = await input.settle({
      workspaceId: recovery.workspaceId,
      recoveryId: recovery.recoveryId,
      operationId: recovery.operationId,
      outcome,
      evidenceSha256: digestJson(evidence),
      healthState,
      runtimeInstanceId: recovery.runtimeInstanceId,
      workloadIdentityDigest: recovery.workloadIdentityDigest,
      errorCode,
    });
    if (
      !settled.ok ||
      settled.value.recoveryId !== recovery.recoveryId ||
      settled.value.operation.operationId !== recovery.operationId ||
      settled.value.effect.effectId !== recovery.effectId
    ) {
      if (
        sandboxDispatched &&
        ticket !== null &&
        this.#options.sandboxAdapter?.stop !== undefined
      ) {
        await this.#options.sandboxAdapter.stop({
          ticket,
          signal: new AbortController().signal,
        });
      }
      throw new Error(
        settled.ok
          ? "desktop_component_recovery_settle_identity_mismatch"
          : settled.error.code,
      );
    }
    if (outcome === "succeeded" && ticket !== null) {
      this.#activated.set(
        `${recovery.workspaceId}:${recovery.componentId}`,
        ticket,
      );
    }
  }

  async invoke(
    input: DesktopWorkspaceComponentInvokeInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentInvokeResult>> {
    const fixedComponentId = expectedComponent(input.operation);
    if (fixedComponentId !== null && input.componentId !== fixedComponentId) {
      return failure("desktop_component_adapter_identity_mismatch");
    }
    const admissionId = `admission:${++this.#admissionSequence}`;
    const admissionFence = this.#componentFence(
      input.workspaceId,
      input.componentId,
    );
    const controller = new AbortController();
    let completeActive: () => void = () => {};
    const admissionSettled = new Promise<void>((resolve) => {
      completeActive = resolve;
    });
    this.#active.set(
      admissionId,
      Object.freeze({
        workspaceId: input.workspaceId,
        componentId: input.componentId,
        controller,
        kind: "admission",
        settled: admissionSettled,
        complete: completeActive,
      }),
    );
    let activeId = admissionId;
    let timeout: NodeJS.Timeout | null = null;
    let ticket: DesktopWorkspaceComponentExecutionTicket | null = null;
    let externalDispatch = false;
    const now = this.#options.now ?? Date.now;
    const startedAt = now();
    try {
      const begun =
        await this.#options.native.beginWorkspaceComponentInvocation(
          beginInput(input),
        );
      if (!begun.ok) return begun;
      if (begun.value.replayed) {
        return failure("desktop_component_invocation_reconciliation_required");
      }
      ticket = begun.value.ticket;
      if (
        !ticketMatches(ticket, input) ||
        this.#active.has(ticket.operationId)
      ) {
        return failure("desktop_component_ticket_identity_mismatch");
      }
      if (
        controller.signal.aborted ||
        admissionFence !==
          this.#componentFence(input.workspaceId, input.componentId)
      ) {
        return await this.#settleFailure(
          ticket,
          input,
          "unknown",
          "desktop_component_invocation_fenced_before_dispatch",
          Math.max(0, now() - startedAt),
        );
      }
      if (Date.parse(ticket.expiresAt) <= now()) {
        return await this.#settleFailure(
          ticket,
          input,
          "unknown",
          "desktop_component_ticket_expired",
          Math.max(0, now() - startedAt),
        );
      }
      completeActive();
      this.#active.delete(admissionId);
      let completeExecution: () => void = () => {};
      const executionSettled = new Promise<void>((resolve) => {
        completeExecution = resolve;
      });
      completeActive = completeExecution;
      activeId = ticket.operationId;
      this.#active.set(
        activeId,
        Object.freeze({
          workspaceId: input.workspaceId,
          componentId: input.componentId,
          controller,
          kind: "invocation",
          settled: executionSettled,
          complete: completeExecution,
        }),
      );
      timeout = setTimeout(
        () => controller.abort(),
        Math.min(
          input.wallTimeMs,
          Math.max(1, Date.parse(ticket.expiresAt) - startedAt),
        ),
      );
      externalDispatch =
        input.operation === "sandbox.run" &&
        this.#options.sandboxAdapter !== undefined;
      const output = await this.#execute(input, ticket, controller.signal);
      if (controller.signal.aborted) {
        return await this.#settleFailure(
          ticket,
          input,
          "cancelled",
          "desktop_component_invocation_cancelled",
          Math.max(0, now() - startedAt),
        );
      }
      const raw = canonicalJson(output);
      const actualBytesOut = Buffer.byteLength(raw, "utf8");
      if (
        actualBytesOut > input.bytesOutReserved ||
        actualBytesOut > MAX_ADAPTER_OUTPUT_BYTES
      ) {
        return await this.#settleFailure(
          ticket,
          input,
          "failed",
          "desktop_component_output_budget_exceeded",
          Math.max(0, now() - startedAt),
        );
      }
      const resultSha256 = createHash("sha256")
        .update(raw, "utf8")
        .digest("hex");
      const evidenceSha256 = digestJson({
        action: ticket.action,
        component_id: ticket.componentId,
        operation_id: ticket.operationId,
        request_sha256: ticket.requestSha256,
        result_sha256: resultSha256,
        workload_fencing_token: ticket.workloadFencingToken,
      });
      const settled =
        await this.#options.native.settleWorkspaceComponentInvocation({
          workspaceId: ticket.workspaceId,
          operationId: ticket.operationId,
          requestSha256: ticket.requestSha256,
          state: "succeeded",
          resultSha256,
          evidenceSha256,
          actualBytesOut,
          actualTokens: 0,
          actualWallTimeMs: Math.max(0, now() - startedAt),
        });
      if (!settled.ok) return settled;
      return success({
        operationId: ticket.operationId,
        state: "succeeded",
        output,
        settlement: settled.value,
      });
    } catch (error) {
      if (ticket === null) {
        return failure(adapterErrorCode(error, controller.signal.aborted));
      }
      const state = externalDispatch
        ? "unknown"
        : controller.signal.aborted
          ? "cancelled"
          : "failed";
      return await this.#settleFailure(
        ticket,
        input,
        state,
        externalDispatch
          ? "desktop_component_adapter_outcome_unknown"
          : adapterErrorCode(error, controller.signal.aborted),
        Math.max(0, now() - startedAt),
      );
    } finally {
      if (timeout !== null) clearTimeout(timeout);
      completeActive();
      this.#active.delete(activeId);
      this.#active.delete(admissionId);
    }
  }

  async emergencyStop(
    input: DesktopWorkspaceComponentEmergencyStopInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentEmergencyStopResult>
  > {
    const prepared =
      await this.#options.native.emergencyStopWorkspaceComponents({
        ...input,
        phase: "prepare",
      });
    if (!prepared.ok) return prepared;
    if (!("tickets" in prepared.value)) {
      return failure("desktop_component_emergency_prepare_invalid");
    }
    const prepare =
      prepared.value as DesktopWorkspaceComponentEmergencyStopPrepareResult;
    for (const componentId of new Set(prepare.fencedComponentIds)) {
      this.#advanceComponentFence(input.workspaceId, componentId);
    }
    if (prepare.replayed) {
      return failure(
        "desktop_component_emergency_stop_reconciliation_required",
      );
    }
    const stoppedComponentIds: string[] = [];
    let firstCleanupError: string | null = null;
    let firstSettleFailure: DesktopOperationResult<never> | null = null;
    for (const ticket of prepare.tickets) {
      const activationKey = `${input.workspaceId}:${ticket.componentId}`;
      const activated = this.#activated.get(activationKey);
      const quiesce = await this.#quiesceEmergency(
        input.workspaceId,
        ticket.componentId,
        activated?.quiesceTimeoutMs ?? 5_000,
      );
      let outcome: "succeeded" | "failed" | "unknown" = "succeeded";
      let errorCode: string | null = null;
      let adapterEvidence: AdapterOutput = null;
      if (quiesce.timed_out > 0) {
        outcome = "unknown";
        errorCode = "desktop_component_emergency_quiesce_timeout";
      }
      if (activated?.adapterId === "p34-sandbox.v1") {
        if (this.#options.sandboxAdapter?.stop === undefined) {
          outcome = "unknown";
          errorCode = "desktop_component_sandbox_runtime_unavailable";
        } else {
          try {
            adapterEvidence = (
              await this.#options.sandboxAdapter.stop({
                ticket: activated,
                signal: new AbortController().signal,
              })
            ).evidence;
          } catch {
            outcome = "unknown";
            errorCode = "desktop_component_emergency_host_stop_failed";
          }
        }
      }
      const evidence = Object.freeze({
        adapter_evidence: adapterEvidence,
        component_id: ticket.componentId,
        effect_id: ticket.effectId,
        operation_id: ticket.operationId,
        quiesce,
        request_sha256: ticket.requestSha256,
      });
      const settled =
        await this.#options.native.emergencyStopWorkspaceComponents({
          ...input,
          phase: "settle",
          ...ticket,
          outcome,
          evidenceSha256: digestJson(evidence),
          errorCode,
        });
      if (!settled.ok) {
        firstSettleFailure ??= settled;
      } else if (
        !("componentId" in settled.value) ||
        settled.value.componentId !== ticket.componentId ||
        settled.value.operation.operationId !== ticket.operationId ||
        settled.value.effect.effectId !== ticket.effectId
      ) {
        firstSettleFailure ??= failure(
          "desktop_component_emergency_settle_identity_mismatch",
        );
      }
      if (outcome === "succeeded" && settled.ok) {
        this.#activated.delete(activationKey);
        stoppedComponentIds.push(ticket.componentId);
      } else {
        firstCleanupError ??=
          errorCode ?? "desktop_component_emergency_host_stop_failed";
      }
    }
    if (firstSettleFailure !== null) return firstSettleFailure;
    if (firstCleanupError !== null) return failure(firstCleanupError);
    return success({
      workspaceId: input.workspaceId,
      operationIds: prepare.tickets.map((ticket) => ticket.operationId),
      stoppedComponentIds: Object.freeze(stoppedComponentIds),
      replayed: prepare.replayed,
    });
  }

  async applyAction(
    input: DesktopWorkspaceComponentActionInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentActionResult>> {
    const prepared = await this.#options.native.applyWorkspaceComponentAction({
      ...input,
      phase: "prepare",
      operationId: null,
      outcome: null,
      evidenceSha256: null,
      healthState: null,
      runtimeInstanceId: null,
      workloadIdentityDigest: null,
      errorCode: null,
    });
    if (!prepared.ok || prepared.value.replayed) return prepared;
    if (prepared.value.operation.state !== "pending") return prepared;
    if (INVOCATION_FENCING_ACTIONS.has(input.action)) {
      this.#advanceComponentFence(input.workspaceId, input.componentId);
    }

    if (
      !lifecycleTicketMatches(input, prepared.value) ||
      this.#active.has(prepared.value.operation.operationId)
    ) {
      return failure("desktop_component_lifecycle_ticket_identity_mismatch");
    }

    const lifecycleTicket = prepared.value.lifecycleTicket;
    const operationId = lifecycleTicket.operationId;
    const controller = new AbortController();
    let completeExecution: () => void = () => {};
    const executionSettled = new Promise<void>((resolve) => {
      completeExecution = resolve;
    });
    this.#active.set(
      operationId,
      Object.freeze({
        workspaceId: input.workspaceId,
        componentId: input.componentId,
        controller,
        kind: "lifecycle",
        settled: executionSettled,
        complete: completeExecution,
      }),
    );
    let outcome: "succeeded" | "failed" | "unknown" = "succeeded";
    let healthState: "healthy" | "unhealthy" | "unknown" = "unknown";
    let evidence: AdapterOutput;
    let errorCode: string | null = null;
    let sandboxActivated = false;
    try {
      try {
        const result = await this.#executeLifecycle(
          input,
          lifecycleTicket,
          controller.signal,
        );
        healthState = result.health;
        evidence = result.evidence;
        sandboxActivated =
          input.action === "activate" &&
          lifecycleTicket.adapterId === "p34-sandbox.v1";
      } catch (error) {
        const external =
          error instanceof ComponentAdapterError && error.outcomeUnknown;
        outcome = external ? "unknown" : "failed";
        healthState = external ? "unknown" : "unhealthy";
        errorCode = external
          ? "desktop_component_lifecycle_outcome_unknown"
          : adapterErrorCode(error, controller.signal.aborted);
        evidence = Object.freeze({
          action: input.action,
          component_id: input.componentId,
          error_code: errorCode,
          operation_id: operationId,
        });
      }
      const settled = await this.#options.native.applyWorkspaceComponentAction({
        ...input,
        phase: "settle",
        operationId,
        outcome,
        evidenceSha256: digestJson(evidence),
        healthState,
        runtimeInstanceId: lifecycleTicket.runtimeInstanceId,
        workloadIdentityDigest: lifecycleTicket.workloadIdentityDigest,
        errorCode,
      });
      if (
        !settled.ok ||
        !lifecycleTicketMatches(input, settled.value) ||
        !sameLifecycleTicket(lifecycleTicket, settled.value.lifecycleTicket)
      ) {
        if (sandboxActivated) {
          try {
            await this.#options.sandboxAdapter!.stop!({
              ticket: lifecycleTicket,
              signal: new AbortController().signal,
            });
          } catch {
            return failure("desktop_component_lifecycle_compensation_failed");
          }
        }
        return settled.ok
          ? failure(
              "desktop_component_lifecycle_settle_ticket_identity_mismatch",
            )
          : settled;
      }
      const activationKey = `${input.workspaceId}:${input.componentId}`;
      if (outcome === "succeeded" && input.action === "activate") {
        this.#activated.set(activationKey, lifecycleTicket);
      } else if (
        outcome === "succeeded" &&
        ["disable", "upgrade", "rollback", "revoke", "uninstall"].includes(
          input.action,
        )
      ) {
        this.#activated.delete(activationKey);
      }
      return settled;
    } finally {
      completeExecution();
      this.#active.delete(operationId);
    }
  }

  stopWorkspace(workspaceId: string): void {
    for (const componentId of new Set(
      [...this.#active.values()]
        .filter((execution) => execution.workspaceId === workspaceId)
        .map((execution) => execution.componentId),
    )) {
      this.#advanceComponentFence(workspaceId, componentId);
    }
    for (const execution of this.#active.values()) {
      if (execution.workspaceId === workspaceId) {
        this.#abortExecution(execution);
      }
    }
  }

  stopComponent(workspaceId: string, componentId: string): void {
    this.#advanceComponentFence(workspaceId, componentId);
    for (const execution of this.#active.values()) {
      if (
        execution.workspaceId === workspaceId &&
        execution.componentId === componentId
      ) {
        this.#abortExecution(execution);
      }
    }
  }

  stopAll(): void {
    const scopes = new Set(
      [...this.#active.values()].map(
        (execution) => `${execution.workspaceId}\u0000${execution.componentId}`,
      ),
    );
    for (const scope of scopes) {
      const [workspaceId, componentId] = scope.split("\u0000");
      if (workspaceId !== undefined && componentId !== undefined) {
        this.#advanceComponentFence(workspaceId, componentId);
      }
    }
    for (const execution of this.#active.values()) {
      this.#abortExecution(execution);
    }
  }

  dispose(): void {
    this.stopAll();
    this.#unsubscribeWorkspaceInvalidation();
  }

  #componentFence(workspaceId: string, componentId: string): number {
    return this.#componentFences.get(`${workspaceId}:${componentId}`) ?? 0;
  }

  #advanceComponentFence(workspaceId: string, componentId: string): void {
    const key = `${workspaceId}:${componentId}`;
    this.#componentFences.set(key, (this.#componentFences.get(key) ?? 0) + 1);
    for (const execution of this.#active.values()) {
      if (
        execution.kind === "admission" &&
        execution.workspaceId === workspaceId &&
        execution.componentId === componentId
      ) {
        this.#abortExecution(execution);
      }
    }
  }

  #abortExecution(execution: ActiveExecution): void {
    execution.controller.abort();
    if (execution.kind === "admission") execution.complete();
  }

  async #executeLifecycle(
    input: DesktopWorkspaceComponentActionInput,
    ticket: DesktopWorkspaceComponentLifecycleTicket,
    signal: AbortSignal,
  ): Promise<
    Readonly<{
      health: "healthy" | "unhealthy" | "unknown";
      evidence: AdapterOutput;
    }>
  > {
    if (signal.aborted) {
      throw new ComponentAdapterError("desktop_component_lifecycle_cancelled");
    }
    if (
      ["disable", "upgrade", "rollback", "revoke", "uninstall"].includes(
        input.action,
      )
    ) {
      const quiesce = await this.#quiesceComponent(
        input.workspaceId,
        input.componentId,
        ticket.operationId,
        ticket.quiesceTimeoutMs,
      );
      if (ticket.adapterId === "p34-sandbox.v1") {
        if (this.#options.sandboxAdapter?.stop === undefined) {
          throw new ComponentAdapterError(
            "desktop_component_sandbox_runtime_unavailable",
          );
        }
        let stopped: Readonly<{ evidence: AdapterOutput }>;
        try {
          stopped = await this.#options.sandboxAdapter.stop({ ticket, signal });
        } catch {
          throw new ComponentAdapterError(
            "desktop_component_lifecycle_outcome_unknown",
            true,
          );
        }
        if (quiesce.timed_out > 0) {
          throw new ComponentAdapterError(
            "desktop_component_lifecycle_quiesce_timeout",
            true,
          );
        }
        return Object.freeze({
          health: "unknown",
          evidence: Object.freeze({
            adapter_evidence: stopped.evidence,
            quiesce,
          }),
        });
      }
      if (quiesce.timed_out > 0) {
        throw new ComponentAdapterError(
          "desktop_component_lifecycle_quiesce_timeout",
          true,
        );
      }
      return Object.freeze({
        health: "unknown",
        evidence: Object.freeze({
          action: input.action,
          adapter: "host_lifecycle.v1",
          component_id: input.componentId,
          quiesce,
        }),
      });
    }
    if (input.action !== "activate") {
      return Object.freeze({
        health: "unknown",
        evidence: Object.freeze({
          action: input.action,
          adapter: "host_lifecycle.v1",
          component_id: input.componentId,
          verified: true,
        }),
      });
    }
    if (ticket.adapterId === "p34-sandbox.v1") {
      if (
        this.#options.sandboxAdapter?.activate === undefined ||
        this.#options.sandboxAdapter.stop === undefined
      ) {
        throw new ComponentAdapterError(
          "desktop_component_sandbox_runtime_unavailable",
        );
      }
      const workload = await this.#sourceSandboxWorkload(ticket);
      try {
        return await this.#options.sandboxAdapter.activate({
          ticket,
          workload,
          signal,
        });
      } catch {
        throw new ComponentAdapterError(
          "desktop_component_lifecycle_outcome_unknown",
          true,
        );
      }
    }
    if (ticket.adapterId === "trusted-local-app.v1") {
      const catalog = await this.#loadKnowledgeEbook(ticket, signal);
      return Object.freeze({
        health: "healthy",
        evidence: Object.freeze({
          adapter: "trusted-local-app.v1",
          component_id: ticket.componentId,
          source_snapshot_sha256: catalog.value.source_snapshot_sha256,
          version: ticket.version,
        }),
      });
    }
    if (
      ticket.adapterId !== "builtin-ui.v1" &&
      ticket.adapterId !== "instruction-skill.v1" &&
      ticket.adapterId !== "readonly-mcp.v1"
    ) {
      throw new ComponentAdapterError(
        "desktop_component_lifecycle_adapter_unknown",
      );
    }
    if (
      ticket.adapterId === "builtin-ui.v1" &&
      (input.componentId === "builtin.workspace-canvas"
        ? (await this.#sourceView(ticket)) == null
        : (await this.#options.ownerPackageStore?.readView(
            ticket.packageSha256,
            ticket.manifestSha256,
            ticket.componentId,
          )) == null)
    ) {
      throw new ComponentAdapterError(
        "desktop_component_owner_package_asset_unavailable",
      );
    }
    if (ticket.adapterId === "instruction-skill.v1") {
      await this.#sourceInstruction(ticket);
    }
    if (ticket.adapterId === "readonly-mcp.v1") {
      await this.#sourceMcpTools(ticket);
    }
    return Object.freeze({
      health: "healthy",
      evidence: Object.freeze({
        action: input.action,
        adapter: "host_lifecycle.v1",
        component_id: input.componentId,
        health: "healthy",
      }),
    });
  }

  async #quiesceComponent(
    workspaceId: string,
    componentId: string,
    excludedOperationId: string,
    timeoutMs: number,
  ): Promise<
    Readonly<{ requested: number; settled: number; timed_out: number }>
  > {
    const targets = [...this.#active.entries()].filter(
      ([operationId, execution]) =>
        operationId !== excludedOperationId &&
        execution.kind !== "lifecycle" &&
        execution.workspaceId === workspaceId &&
        execution.componentId === componentId,
    );
    const completed = new Set<string>();
    for (const [operationId, execution] of targets) {
      this.#abortExecution(execution);
      void execution.settled.then(() => completed.add(operationId));
    }
    if (targets.length > 0) {
      let timeout: NodeJS.Timeout | null = null;
      try {
        await Promise.race([
          Promise.all(targets.map(([, execution]) => execution.settled)),
          new Promise<void>((resolve) => {
            timeout = setTimeout(resolve, timeoutMs);
          }),
        ]);
      } finally {
        if (timeout !== null) clearTimeout(timeout);
      }
    }
    return Object.freeze({
      requested: targets.length,
      settled: completed.size,
      timed_out: targets.length - completed.size,
    });
  }

  async #quiesceEmergency(
    workspaceId: string,
    componentId: string,
    timeoutMs: number,
  ): Promise<
    Readonly<{ requested: number; settled: number; timed_out: number }>
  > {
    const targets = [...this.#active.entries()].filter(
      ([, execution]) =>
        execution.workspaceId === workspaceId &&
        execution.componentId === componentId,
    );
    const completed = new Set<string>();
    for (const [operationId, execution] of targets) {
      this.#abortExecution(execution);
      void execution.settled.then(() => completed.add(operationId));
    }
    if (targets.length > 0) {
      let timeout: NodeJS.Timeout | null = null;
      try {
        await Promise.race([
          Promise.all(targets.map(([, execution]) => execution.settled)),
          new Promise<void>((resolve) => {
            timeout = setTimeout(resolve, timeoutMs);
          }),
        ]);
      } finally {
        if (timeout !== null) clearTimeout(timeout);
      }
    }
    return Object.freeze({
      requested: targets.length,
      settled: completed.size,
      timed_out: targets.length - completed.size,
    });
  }

  async #execute(
    input: DesktopWorkspaceComponentInvokeInput,
    ticket: DesktopWorkspaceComponentExecutionTicket,
    signal: AbortSignal,
  ): Promise<AdapterOutput> {
    if (signal.aborted)
      throw new ComponentAdapterError("desktop_component_invocation_cancelled");
    switch (input.operation) {
      case "ui.render":
        if (input.arguments.viewId !== input.componentId) {
          throw new ComponentAdapterError(
            "desktop_component_ui_descriptor_mismatch",
          );
        }
        const slot = ticket.slotBindings.find(
          (binding) => binding.slotId === input.arguments.slotId,
        );
        if (slot === undefined) {
          throw new ComponentAdapterError(
            "desktop_component_ui_descriptor_mismatch",
          );
        }
        const configuration = isRecord(ticket.configuration)
          ? ticket.configuration
          : {};
        const packageView =
          ticket.componentId === "builtin.workspace-canvas"
            ? await this.#sourceView(ticket)
            : await this.#options.ownerPackageStore?.readView(
                ticket.packageSha256,
                ticket.manifestSha256,
                ticket.componentId,
              );
        const configuredTitle =
          typeof configuration.title === "string" &&
          configuration.title.trim().length > 0 &&
          configuration.title.length <= 128
            ? configuration.title
            : input.componentId;
        const title = packageView?.title ?? configuredTitle;
        const configuredSections = Object.entries(configuration)
          .filter(
            ([key, value]) =>
              key !== "title" &&
              key.length <= 64 &&
              (typeof value === "string" ||
                typeof value === "number" ||
                typeof value === "boolean"),
          )
          .slice(0, 16)
          .map(([label, value]) =>
            Object.freeze({
              kind: "status" as const,
              label,
              value: String(value).slice(0, 512),
            }),
          );
        const packageSections = packageView?.sections.map((section) =>
          Object.freeze({
            kind: "status" as const,
            label: section.label,
            value:
              section.source === "installation"
                ? `generation ${ticket.bindingGeneration}`
                : section.source === "health"
                  ? "healthy"
                  : section.source === "grants"
                    ? ticket.action
                    : canonicalJson(ticket.configuration).slice(0, 512),
          }),
        );
        const sections = packageSections ?? configuredSections;
        return Object.freeze({
          adapter: "builtin-ui.v1",
          component_id: input.componentId,
          schema_version: 1,
          slot_id: input.arguments.slotId,
          view_id: input.arguments.viewId,
          renderer: "host_declarative",
          view: Object.freeze({
            kind: "workspace_component_overview",
            title,
            sections: Object.freeze(
              sections.length > 0
                ? sections
                : [
                    Object.freeze({
                      kind: "status" as const,
                      label: "Binding",
                      value: slot.bindingKey,
                    }),
                    Object.freeze({
                      kind: "status" as const,
                      label: "Renderer posture",
                      value: "Host declarative",
                    }),
                  ],
            ),
          }),
        });
      case "skill.resolve":
        if (
          input.arguments.skillId !== input.componentId ||
          input.arguments.task.length < 1 ||
          input.arguments.task.length > 32_768
        ) {
          throw new ComponentAdapterError(
            "desktop_component_skill_input_invalid",
          );
        }
        return Object.freeze({
          adapter: "instruction-skill.v1",
          authority: "instruction_only",
          component_id: input.componentId,
          instructions: await this.#sourceInstruction(ticket),
          skill_id: input.arguments.skillId,
          task_sha256: createHash("sha256")
            .update(input.arguments.task, "utf8")
            .digest("hex"),
        });
      case "mcp.call": {
        const allowedTools = await this.#sourceMcpTools(ticket);
        if (!allowedTools.has(input.arguments.toolName)) {
          throw new ComponentAdapterError(
            "desktop_component_mcp_tool_not_declared",
          );
        }
        return await this.#executeMcp(input, ticket, allowedTools, signal);
      }
      case "sandbox.run":
        if (this.#options.sandboxAdapter === undefined) {
          throw new ComponentAdapterError(
            "desktop_component_sandbox_runtime_unavailable",
          );
        }
        const workload = await this.#sourceSandboxWorkload(ticket);
        if (input.arguments.workloadId !== workload.workloadId) {
          throw new ComponentAdapterError(
            "desktop_component_sandbox_workload_mismatch",
          );
        }
        return await this.#options.sandboxAdapter.execute({
          ticket,
          workloadId: input.arguments.workloadId,
          workload,
          inputArtifactIds: input.arguments.inputArtifactIds,
          signal,
        });
      case "local_adapter.open":
        return await this.#openKnowledgeEbook(input, ticket, signal);
    }
  }

  async #executeMcp(
    input: Extract<
      DesktopWorkspaceComponentInvokeInput,
      { operation: "mcp.call" }
    >,
    ticket: DesktopWorkspaceComponentExecutionTicket,
    allowedTools: ReadonlySet<string>,
    signal: AbortSignal,
  ): Promise<AdapterOutput> {
    const toolArguments =
      input.arguments.toolName === "omnibase_files_list"
        ? { directory: input.arguments.path ?? "" }
        : input.arguments.toolName === "omnibase_text_search"
          ? { path: input.arguments.path, query: input.arguments.query }
          : { path: input.arguments.path };
    const result = await this.#mcpHost.call({
      workspaceId: input.workspaceId,
      allowedTools,
      request: {
        id: ticket.operationId,
        jsonrpc: "2.0",
        method: "tools/call",
        params: { arguments: toolArguments, name: input.arguments.toolName },
      },
      signal,
    });
    if (!result.ok) throw new ComponentAdapterError(result.error.code);
    const response = result.value;
    if (
      !isRecord(response) ||
      !hasExactKeys(response, ["id", "jsonrpc", "result"]) ||
      response.id !== ticket.operationId ||
      response.jsonrpc !== "2.0" ||
      !isRecord(response.result) ||
      !hasExactKeys(response.result, [
        "output",
        "server_id",
        "tool",
        "transport",
      ]) ||
      response.result.server_id !== "workspace-files-readonly" ||
      response.result.transport !== "host_native" ||
      response.result.tool !== input.arguments.toolName ||
      !isRecord(response.result.output) ||
      "tool" in response.result.output
    ) {
      throw new ComponentAdapterError("desktop_component_mcp_response_invalid");
    }
    return Object.freeze({
      tool: input.arguments.toolName,
      ...(response.result.output as Readonly<
        Record<string, DesktopWorkspaceComponentJsonValue>
      >),
    });
  }

  async #sourcePayload(
    ticket: SourceComponentIdentity,
    payloadName: string,
  ): Promise<unknown> {
    return (await this.#sourcePayloadAsset(ticket, payloadName)).value;
  }

  async #sourcePayloadAsset(
    ticket: SourceComponentIdentity,
    payloadName: string,
  ): Promise<SourceComponentPayloadAsset> {
    const verifier = this.#options.getVerifiedRuntimeFileSha256;
    const reader = this.#options.readSourceComponentPayload;
    const assetReader = this.#options.readSourceComponentPayloadAsset;
    if (
      verifier === undefined &&
      reader === undefined &&
      assetReader === undefined
    ) {
      throw new ComponentAdapterError(
        "desktop_component_source_package_unavailable",
      );
    }
    try {
      const options = {
        runtimeRoot: this.#options.runtimeRoot,
        getVerifiedRuntimeFileSha256: verifier ?? (() => null),
        componentId: ticket.componentId,
        version: ticket.version,
        manifestSha256: ticket.manifestSha256,
        packageSha256: ticket.packageSha256,
        payloadName,
      };
      if (assetReader !== undefined) return await assetReader(options);
      if (reader !== undefined) {
        const value = await reader(options);
        const raw = Buffer.from(
          `${canonicalJson(value as DesktopWorkspaceComponentJsonValue)}\n`,
        );
        return Object.freeze({
          value,
          sha256: createHash("sha256").update(raw).digest("hex"),
          size: raw.byteLength,
        });
      }
      return await readSourceComponentPayloadAsset(options);
    } catch {
      throw new ComponentAdapterError(
        "desktop_component_source_package_invalid",
      );
    }
  }

  async #sourceBinaryAsset(
    ticket: SourceComponentIdentity,
    payloadName: string,
  ): Promise<SourceComponentBinaryAsset> {
    const verifier = this.#options.getVerifiedRuntimeFileSha256;
    const reader = this.#options.readSourceComponentBinaryAsset;
    if (verifier === undefined && reader === undefined) {
      throw new ComponentAdapterError(
        "desktop_component_source_package_unavailable",
      );
    }
    try {
      const options = {
        runtimeRoot: this.#options.runtimeRoot,
        getVerifiedRuntimeFileSha256: verifier ?? (() => null),
        componentId: ticket.componentId,
        version: ticket.version,
        manifestSha256: ticket.manifestSha256,
        packageSha256: ticket.packageSha256,
        payloadName,
      };
      const asset =
        reader === undefined
          ? await readSourceComponentBinaryAsset(options)
          : await reader(options);
      return Object.freeze({
        bytes: Buffer.from(asset.bytes),
        sha256: asset.sha256,
        size: asset.size,
      });
    } catch {
      throw new ComponentAdapterError(
        "desktop_component_source_package_invalid",
      );
    }
  }

  async #sourceView(
    ticket: SourceComponentIdentity,
  ): Promise<DeclarativeViewDescriptor> {
    const value = await this.#sourcePayload(ticket, "view.json");
    if (
      !isRecord(value) ||
      !hasExactKeys(value, [
        "component_id",
        "schema_version",
        "version",
        "view",
      ]) ||
      value.component_id !== ticket.componentId ||
      value.version !== ticket.version ||
      value.schema_version !== 1 ||
      !isRecord(value.view) ||
      !hasExactKeys(value.view, ["kind", "sections", "title"]) ||
      value.view.kind !== "workspace_summary" ||
      typeof value.view.title !== "string" ||
      value.view.title.length < 1 ||
      value.view.title.length > 128 ||
      !Array.isArray(value.view.sections) ||
      value.view.sections.length < 1 ||
      value.view.sections.length > 16
    ) {
      throw new ComponentAdapterError("desktop_component_ui_package_invalid");
    }
    for (const section of value.view.sections) {
      if (
        !isRecord(section) ||
        !hasExactKeys(section, ["id", "label", "source"]) ||
        typeof section.id !== "string" ||
        typeof section.label !== "string" ||
        section.label.length < 1 ||
        section.label.length > 96 ||
        !["installation", "health", "grants", "configuration"].includes(
          String(section.source),
        )
      ) {
        throw new ComponentAdapterError("desktop_component_ui_package_invalid");
      }
    }
    return value.view as unknown as DeclarativeViewDescriptor;
  }

  async #sourceInstruction(ticket: SourceComponentIdentity): Promise<string> {
    const value = await this.#sourcePayload(ticket, "instruction.json");
    if (
      !isRecord(value) ||
      !hasExactKeys(value, [
        "component_id",
        "instruction",
        "schema_version",
        "version",
      ]) ||
      value.component_id !== ticket.componentId ||
      value.version !== ticket.version ||
      value.schema_version !== 1 ||
      typeof value.instruction !== "string" ||
      value.instruction.length < 1 ||
      value.instruction.length > 32_768
    ) {
      throw new ComponentAdapterError(
        "desktop_component_skill_package_invalid",
      );
    }
    return value.instruction;
  }

  async #sourceMcpTools(
    ticket: SourceComponentIdentity,
  ): Promise<ReadonlySet<string>> {
    const value = await this.#sourcePayload(ticket, "mcp.json");
    if (
      !isRecord(value) ||
      !hasExactKeys(value, [
        "component_id",
        "schema_version",
        "server",
        "tools",
        "version",
      ]) ||
      value.component_id !== ticket.componentId ||
      value.version !== ticket.version ||
      value.schema_version !== 1 ||
      !isRecord(value.server) ||
      !hasExactKeys(value.server, ["server_id", "transport"]) ||
      value.server.server_id !== "workspace-files-readonly" ||
      value.server.transport !== "host_native" ||
      !Array.isArray(value.tools) ||
      value.tools.length !== Object.keys(SEALED_MCP_TOOL_DESCRIPTORS).length
    ) {
      throw new ComponentAdapterError("desktop_component_mcp_package_invalid");
    }
    const tools = new Set<string>();
    for (const tool of value.tools) {
      if (
        !isRecord(tool) ||
        !hasExactKeys(tool, ["input", "operation", "output", "tool_id"]) ||
        typeof tool.tool_id !== "string" ||
        !(tool.tool_id in SEALED_MCP_TOOL_DESCRIPTORS) ||
        tools.has(tool.tool_id)
      ) {
        throw new ComponentAdapterError(
          "desktop_component_mcp_package_invalid",
        );
      }
      const expected =
        SEALED_MCP_TOOL_DESCRIPTORS[
          tool.tool_id as keyof typeof SEALED_MCP_TOOL_DESCRIPTORS
        ];
      if (
        canonicalJson(tool as DesktopWorkspaceComponentJsonValue) !==
        canonicalJson(expected)
      ) {
        throw new ComponentAdapterError(
          "desktop_component_mcp_package_invalid",
        );
      }
      tools.add(tool.tool_id);
    }
    if (
      Object.keys(SEALED_MCP_TOOL_DESCRIPTORS).some((tool) => !tools.has(tool))
    ) {
      throw new ComponentAdapterError("desktop_component_mcp_package_invalid");
    }
    return tools;
  }

  async #sourceSandboxWorkload(
    ticket: SourceComponentIdentity,
  ): Promise<TrustedSandboxWorkload & Readonly<{ workloadId: string }>> {
    const [value, moduleAsset] = await Promise.all([
      this.#sourcePayload(ticket, "workload.json"),
      this.#sourceBinaryAsset(ticket, "workload.wasm"),
    ]);
    if (
      !isRecord(value) ||
      !hasExactKeys(value, [
        "component_id",
        "entrypoint",
        "input_contract",
        "memory_max_bytes",
        "module_format",
        "module_path",
        "module_sha256",
        "network",
        "output_contract",
        "provider",
        "schema_version",
        "version",
        "workload_id",
      ]) ||
      value.component_id !== ticket.componentId ||
      value.version !== ticket.version ||
      value.schema_version !== 1 ||
      value.provider !== "p34-sandbox.v1" ||
      value.module_format !== "webassembly_v1" ||
      value.module_path !== "payload/workload.wasm" ||
      value.module_sha256 !== moduleAsset.sha256 ||
      value.entrypoint !== "transform" ||
      value.memory_max_bytes !== 64 * 1024 ||
      value.network !== "no_imports" ||
      value.input_contract !== "logical_artifact_ids" ||
      value.output_contract !== "artifact_inventory" ||
      value.workload_id !== "bounded-transform"
    ) {
      throw new ComponentAdapterError(
        "desktop_component_sandbox_package_invalid",
      );
    }
    let module: WebAssembly.Module;
    try {
      module = new WebAssembly.Module(moduleAsset.bytes);
    } catch {
      throw new ComponentAdapterError(
        "desktop_component_sandbox_package_invalid",
      );
    }
    if (
      WebAssembly.Module.imports(module).length !== 0 ||
      JSON.stringify(WebAssembly.Module.exports(module)) !==
        JSON.stringify([{ name: "transform", kind: "function" }])
    ) {
      throw new ComponentAdapterError(
        "desktop_component_sandbox_package_invalid",
      );
    }
    return Object.freeze({
      bytes: Buffer.from(moduleAsset.bytes),
      entrypoint: "transform" as const,
      memoryMaxBytes: 65_536 as const,
      network: "no_imports" as const,
      sha256: moduleAsset.sha256,
      workloadId: value.workload_id,
    });
  }

  async #loadKnowledgeEbook(
    ticket: SourceComponentIdentity,
    signal: AbortSignal,
  ): Promise<
    Readonly<{
      sha256: string;
      value: Readonly<
        Record<string, DesktopWorkspaceComponentJsonValue> & {
          source_snapshot_sha256: string;
        }
      >;
    }>
  > {
    const adapter = await this.#sourcePayload(ticket, "adapter.json");
    if (
      signal.aborted ||
      !isRecord(adapter) ||
      !hasExactKeys(adapter, [
        "adapter_id",
        "catalog_path",
        "component_id",
        "operation",
        "schema_version",
        "version",
      ]) ||
      adapter.adapter_id !== "trusted-local-app.v1" ||
      adapter.catalog_path !== "payload/catalog.json" ||
      adapter.component_id !== ticket.componentId ||
      adapter.operation !== "local_adapter.open" ||
      adapter.schema_version !== 1 ||
      adapter.version !== ticket.version
    ) {
      throw new ComponentAdapterError(
        "desktop_component_local_adapter_package_invalid",
      );
    }
    const catalogAsset = await this.#sourcePayloadAsset(ticket, "catalog.json");
    const catalog = catalogAsset.value;
    if (signal.aborted) {
      throw new ComponentAdapterError("desktop_component_invocation_cancelled");
    }
    if (!validKnowledgeEbookCatalog(catalog, ticket.version)) {
      throw new ComponentAdapterError(
        "desktop_component_local_adapter_asset_invalid",
      );
    }
    return Object.freeze({
      sha256: catalogAsset.sha256,
      value: catalog as Readonly<
        Record<string, DesktopWorkspaceComponentJsonValue> & {
          source_snapshot_sha256: string;
        }
      >,
    });
  }

  async #openKnowledgeEbook(
    input: Extract<
      DesktopWorkspaceComponentInvokeInput,
      { operation: "local_adapter.open" }
    >,
    ticket: DesktopWorkspaceComponentExecutionTicket,
    signal: AbortSignal,
  ): Promise<AdapterOutput> {
    if (input.arguments.adapterId !== "knowledge.ebook") {
      throw new ComponentAdapterError(
        "desktop_component_local_adapter_unknown",
      );
    }
    const catalog = await this.#loadKnowledgeEbook(ticket, signal);
    return Object.freeze({
      adapter: "trusted-local-app.v1",
      asset_id: `knowledge.ebook/${ticket.version}/catalog`,
      asset_sha256: catalog.sha256,
      component_manifest_sha256: ticket.manifestSha256,
      component_package_sha256: ticket.packageSha256,
      destination: input.arguments.destination,
      logical_id: input.arguments.logicalId ?? null,
      catalog: catalog.value,
      renderer: "host_declarative",
    });
  }

  async #settleFailure(
    ticket: DesktopWorkspaceComponentExecutionTicket,
    input: DesktopWorkspaceComponentInvokeInput,
    state: "failed" | "cancelled" | "unknown",
    errorCode: string,
    actualWallTimeMs: number,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentInvokeResult>> {
    const evidenceSha256 = digestJson({
      action: ticket.action,
      component_id: ticket.componentId,
      error_code: errorCode,
      operation_id: ticket.operationId,
      request_sha256: ticket.requestSha256,
      state,
      workload_fencing_token: ticket.workloadFencingToken,
    });
    const settled =
      await this.#options.native.settleWorkspaceComponentInvocation({
        workspaceId: ticket.workspaceId,
        operationId: ticket.operationId,
        requestSha256: ticket.requestSha256,
        state,
        evidenceSha256,
        errorCode,
        actualBytesOut: state === "unknown" ? input.bytesOutReserved : 0,
        actualTokens: state === "unknown" ? input.tokensReserved : 0,
        actualWallTimeMs:
          state === "unknown" ? input.wallTimeMs : actualWallTimeMs,
      });
    if (!settled.ok) return settled;
    return success({
      operationId: ticket.operationId,
      state,
      output: null,
      settlement: settled.value,
    });
  }
}
