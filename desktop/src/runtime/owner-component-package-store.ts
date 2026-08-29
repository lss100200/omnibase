import { createHash, randomBytes } from "node:crypto";
import {
  link,
  lstat,
  mkdir,
  readFile,
  realpath,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

import type {
  DesktopConversationDetail,
  DesktopOperationResult,
  DesktopWorkspaceComponentAssistantPackageImportInput,
  DesktopWorkspaceComponentJsonValue,
  DesktopWorkspaceComponentOwnerPackageImportResult,
  DesktopWorkspaceComponentOwnerPackageRegisterInput,
  DesktopWorkspaceComponentOwnerPackageRegistration,
} from "../shared/ipc-contract.ts";

const COMPONENT_ID = /^[a-z][a-z0-9.-]{2,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const CONFIGURATION_FIELD = /^[a-z][a-z0-9_]{0,63}$/u;
const FORBIDDEN_CONFIGURATION_FIELD =
  /(?:^|_)(?:url|uri|path|command|argv|script|executable|api_key|password|credential|bearer|token|secret|private_key)(?:_|$)/u;
const MAX_PACKAGE_BYTES = 256 * 1024;
const OWNER_COMPONENT_SLOTS = new Set([
  "editor.component",
  "sidebar.component",
  "settings.component",
  "status.component",
]);
const MANIFEST_KEYS = [
  "budgets",
  "compatibility",
  "component_id",
  "configuration_schema",
  "conflicts",
  "dependencies",
  "entrypoint",
  "family",
  "health",
  "manifest_schema_version",
  "network",
  "operations",
  "permissions",
  "publisher",
  "quiesce_timeout_ms",
  "recovery",
  "slots",
  "state_migration",
  "state_schema",
  "uninstall",
  "version",
] as const;

type JsonObject = Readonly<{
  readonly [key: string]: DesktopWorkspaceComponentJsonValue;
}>;

interface OwnerView {
  readonly kind: "workspace_summary";
  readonly title: string;
  readonly sections: readonly Readonly<{
    readonly id: string;
    readonly label: string;
    readonly source: "installation" | "health" | "grants" | "configuration";
  }>[];
}

interface ParsedOwnerPackage {
  readonly manifest: JsonObject;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly inventorySha256: string;
  readonly view: OwnerView;
  readonly canonicalPackage: Buffer;
}

interface MaterializedOwnerPackage {
  readonly finalize: () => Promise<void>;
}

interface StagedOwnerPackage {
  readonly promote: () => Promise<MaterializedOwnerPackage>;
  readonly discard: () => Promise<void>;
}

export interface OwnerComponentPackageNativeBoundary {
  getConversation(
    input: Readonly<{
      workspaceId: string;
      conversationId: string;
    }>,
  ): Promise<DesktopOperationResult<DesktopConversationDetail>>;
  registerOwnerWorkspaceComponentPackage(
    input: DesktopWorkspaceComponentOwnerPackageRegisterInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageRegistration>
  >;
}

export interface OwnerComponentPackageStoreOptions {
  readonly dataRoot: string;
  readonly choosePackage: () => Promise<string | null>;
  readonly native: OwnerComponentPackageNativeBoundary;
}

export interface OwnerComponentPackageFileIdentity {
  readonly dev: number;
  readonly ino: number;
  readonly size: number;
  readonly mtimeMs: number;
  readonly nlink: number;
}

export function ownerComponentPackageFileIdentityMatches(
  before: Readonly<OwnerComponentPackageFileIdentity>,
  after: Readonly<OwnerComponentPackageFileIdentity>,
): boolean {
  return (
    before.dev === after.dev &&
    before.ino === after.ino &&
    before.size === after.size &&
    before.mtimeMs === after.mtimeMs &&
    after.nlink === 1
  );
}

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

function exactOptional(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  );
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(raw: Buffer | string): string {
  return createHash("sha256").update(raw).digest("hex");
}

function containsForbiddenAuthority(value: unknown): boolean {
  if (typeof value === "string") {
    return (
      /(?:javascript|<script|<iframe|:\/\/|@import|electron|node:|command|argv|physical[_-]?path|api[_-]?key|password|credential|bearer|private[_-]?key)/iu.test(
        value,
      ) ||
      /(?:^|[\s"'(])(?:[a-z]:[\\/]|\\\\|\/(?:etc|home|root|users?|var|tmp)(?:[\\/]|$))/iu.test(
        value,
      )
    );
  }
  if (Array.isArray(value)) return value.some(containsForbiddenAuthority);
  if (!isRecord(value)) return false;
  return Object.values(value).some(containsForbiddenAuthority);
}

function configurationSchemaValid(value: unknown): boolean {
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
    !Number.isSafeInteger(value.version) ||
    Number(value.version) < 1 ||
    !isRecord(value.properties) ||
    Object.keys(value.properties).length > 32 ||
    !Array.isArray(value.required) ||
    value.required.some(
      (item) =>
        typeof item !== "string" ||
        !CONFIGURATION_FIELD.test(item) ||
        FORBIDDEN_CONFIGURATION_FIELD.test(item),
    ) ||
    new Set(value.required).size !== value.required.length
  ) {
    return false;
  }
  const properties = value.properties;
  for (const [name, specification] of Object.entries(properties)) {
    if (
      !CONFIGURATION_FIELD.test(name) ||
      FORBIDDEN_CONFIGURATION_FIELD.test(name) ||
      !isRecord(specification) ||
      !exactOptional(
        specification,
        ["type"],
        ["default", "enum", "minimum", "maximum", "max_length"],
      ) ||
      !["boolean", "integer", "number", "string"].includes(
        String(specification.type),
      ) ||
      (specification.enum !== undefined &&
        (!Array.isArray(specification.enum) ||
          specification.enum.length < 1 ||
          specification.enum.length > 32)) ||
      (specification.max_length !== undefined &&
        (!Number.isSafeInteger(specification.max_length) ||
          Number(specification.max_length) > 4_096))
    ) {
      return false;
    }
  }
  return value.required.every((item) =>
    Object.hasOwn(properties, String(item)),
  );
}

function parsePackage(raw: Buffer): ParsedOwnerPackage {
  if (raw.byteLength < 2 || raw.byteLength > MAX_PACKAGE_BYTES) {
    throw new Error("desktop_component_owner_package_size_invalid");
  }
  let value: unknown;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    throw new Error("desktop_component_owner_package_json_invalid");
  }
  if (
    !isRecord(value) ||
    !exact(value, ["manifest", "schema_version", "view"]) ||
    value.schema_version !== 1 ||
    !isRecord(value.manifest) ||
    !exact(value.manifest, MANIFEST_KEYS) ||
    typeof value.manifest.component_id !== "string" ||
    !COMPONENT_ID.test(value.manifest.component_id) ||
    value.manifest.component_id.startsWith("builtin.") ||
    typeof value.manifest.version !== "string" ||
    !VERSION.test(value.manifest.version) ||
    value.manifest.family !== "declarative_ui" ||
    value.manifest.manifest_schema_version !== 1 ||
    !isRecord(value.manifest.publisher) ||
    !exact(value.manifest.publisher, ["classification", "id"]) ||
    value.manifest.publisher.classification !== "owner_reviewed" ||
    typeof value.manifest.publisher.id !== "string" ||
    value.manifest.publisher.id.length < 3 ||
    value.manifest.publisher.id.length > 64 ||
    !isRecord(value.manifest.entrypoint) ||
    !exact(value.manifest.entrypoint, ["adapter_id", "kind"]) ||
    value.manifest.entrypoint.adapter_id !== "builtin-ui.v1" ||
    value.manifest.entrypoint.kind !== "host_view_v1" ||
    !isRecord(value.manifest.compatibility) ||
    !exact(value.manifest.compatibility, ["desktop_schema_min", "host_api"]) ||
    value.manifest.compatibility.desktop_schema_min !== 11 ||
    value.manifest.compatibility.host_api !== "p7.3.v1" ||
    !configurationSchemaValid(value.manifest.configuration_schema) ||
    !Array.isArray(value.manifest.dependencies) ||
    value.manifest.dependencies.length !== 0 ||
    !Array.isArray(value.manifest.conflicts) ||
    value.manifest.conflicts.length !== 0 ||
    !Array.isArray(value.manifest.operations) ||
    value.manifest.operations.length !== 1 ||
    value.manifest.operations[0] !== "ui.render" ||
    !Array.isArray(value.manifest.slots) ||
    value.manifest.slots.length < 1 ||
    value.manifest.slots.length > OWNER_COMPONENT_SLOTS.size ||
    value.manifest.slots.some(
      (slot) =>
        !isRecord(slot) ||
        !exact(slot, [
          "cardinality",
          "maximum_order",
          "minimum_order",
          "slot_id",
        ]) ||
        typeof slot.slot_id !== "string" ||
        !OWNER_COMPONENT_SLOTS.has(slot.slot_id) ||
        (slot.cardinality !== "one" && slot.cardinality !== "many") ||
        !Number.isSafeInteger(slot.minimum_order) ||
        !Number.isSafeInteger(slot.maximum_order) ||
        Number(slot.minimum_order) < 0 ||
        Number(slot.maximum_order) > 10_000 ||
        Number(slot.minimum_order) > Number(slot.maximum_order),
    ) ||
    new Set(
      value.manifest.slots.filter(isRecord).map((slot) => String(slot.slot_id)),
    ).size !== value.manifest.slots.length ||
    !Array.isArray(value.manifest.permissions) ||
    value.manifest.permissions.length !== 1 ||
    !isRecord(value.manifest.permissions[0]) ||
    !exact(value.manifest.permissions[0], [
      "action",
      "data_scope",
      "logical_resource_classes",
      "secret_reference_classes",
    ]) ||
    value.manifest.permissions[0].action !== "ui.render" ||
    !["none", "workspace_logical"].includes(
      String(value.manifest.permissions[0].data_scope),
    ) ||
    !Array.isArray(value.manifest.permissions[0].logical_resource_classes) ||
    value.manifest.permissions[0].logical_resource_classes.some(
      (item) => typeof item !== "string",
    ) ||
    !Array.isArray(value.manifest.permissions[0].secret_reference_classes) ||
    value.manifest.permissions[0].secret_reference_classes.length !== 0 ||
    !isRecord(value.manifest.network) ||
    !exact(value.manifest.network, ["required", "service_classes"]) ||
    value.manifest.network.required !== false ||
    !Array.isArray(value.manifest.network.service_classes) ||
    value.manifest.network.service_classes.length !== 0 ||
    !isRecord(value.manifest.budgets) ||
    !exact(value.manifest.budgets, [
      "max_bytes_in",
      "max_bytes_out",
      "max_calls",
      "max_concurrency",
      "max_cost_units",
      "max_retries",
      "max_tokens",
      "max_wall_time_ms",
    ]) ||
    Object.values(value.manifest.budgets).some(
      (item) => !Number.isSafeInteger(item) || Number(item) < 0,
    ) ||
    Number(value.manifest.budgets.max_calls) < 1 ||
    Number(value.manifest.budgets.max_calls) > 100 ||
    Number(value.manifest.budgets.max_concurrency) < 1 ||
    Number(value.manifest.budgets.max_concurrency) > 4 ||
    Number(value.manifest.budgets.max_wall_time_ms) < 1 ||
    Number(value.manifest.budgets.max_wall_time_ms) > 60_000 ||
    Number(value.manifest.budgets.max_bytes_out) > 1_048_576 ||
    Number(value.manifest.budgets.max_tokens) > 32_768 ||
    !isRecord(value.manifest.health) ||
    !exact(value.manifest.health, ["kind", "required_state", "timeout_ms"]) ||
    value.manifest.health.kind !== "native_receipt_v1" ||
    value.manifest.health.required_state !== "healthy" ||
    value.manifest.health.timeout_ms !== 5_000 ||
    !isRecord(value.manifest.recovery) ||
    !exact(value.manifest.recovery, [
      "auto_replay_unknown",
      "retention",
      "safe_mode",
    ]) ||
    value.manifest.recovery.auto_replay_unknown !== false ||
    value.manifest.recovery.retention !== "retain_workspace_data" ||
    value.manifest.recovery.safe_mode !== "disable_component" ||
    !isRecord(value.manifest.state_schema) ||
    !exact(value.manifest.state_schema, ["kind", "version"]) ||
    value.manifest.state_schema.kind !== "canonical_json" ||
    !Number.isSafeInteger(value.manifest.state_schema.version) ||
    !isRecord(value.manifest.state_migration) ||
    !exact(value.manifest.state_migration, [
      "kind",
      "requires_owner_review_on_schema_change",
    ]) ||
    value.manifest.state_migration.kind !== "host_canonical_v1" ||
    value.manifest.state_migration.requires_owner_review_on_schema_change !==
      true ||
    !isRecord(value.manifest.uninstall) ||
    !exact(value.manifest.uninstall, [
      "retention",
      "unbound_delete_forbidden",
    ]) ||
    value.manifest.uninstall.retention !== "retain_workspace_data" ||
    value.manifest.uninstall.unbound_delete_forbidden !== true ||
    !Number.isSafeInteger(value.manifest.quiesce_timeout_ms) ||
    Number(value.manifest.quiesce_timeout_ms) < 1 ||
    Number(value.manifest.quiesce_timeout_ms) > 60_000 ||
    !isRecord(value.view) ||
    !exact(value.view, ["kind", "sections", "title"]) ||
    value.view.kind !== "workspace_summary" ||
    typeof value.view.title !== "string" ||
    value.view.title.trim().length < 1 ||
    value.view.title.length > 128 ||
    !Array.isArray(value.view.sections) ||
    value.view.sections.length < 1 ||
    value.view.sections.length > 16
  ) {
    throw new Error("desktop_component_owner_package_shape_invalid");
  }
  const sectionIds = new Set<string>();
  for (const section of value.view.sections) {
    if (
      !isRecord(section) ||
      !exact(section, ["id", "label", "source"]) ||
      typeof section.id !== "string" ||
      !/^[a-z][a-z0-9._-]{1,63}$/u.test(section.id) ||
      sectionIds.has(section.id) ||
      typeof section.label !== "string" ||
      section.label.trim().length < 1 ||
      section.label.length > 96 ||
      !["installation", "health", "grants", "configuration"].includes(
        String(section.source),
      )
    ) {
      throw new Error("desktop_component_owner_view_invalid");
    }
    sectionIds.add(section.id);
  }
  const encoded = canonicalJson(value);
  if (containsForbiddenAuthority(value)) {
    throw new Error("desktop_component_owner_package_authority_forbidden");
  }
  const manifest = value.manifest as JsonObject;
  const view = value.view as unknown as OwnerView;
  const manifestRaw = canonicalJson(manifest);
  const viewRaw = Buffer.from(`${canonicalJson(view)}\n`, "utf8");
  const inventoryRaw = `${canonicalJson([
    { path: "view.json", sha256: digest(viewRaw), size: viewRaw.byteLength },
  ])}\n`;
  const canonicalPackage = Buffer.from(`${encoded}\n`, "utf8");
  return Object.freeze({
    manifest,
    manifestSha256: digest(manifestRaw),
    packageSha256: digest(canonicalPackage),
    inventorySha256: digest(inventoryRaw),
    view,
    canonicalPackage,
  });
}

function failure<T>(code: string): DesktopOperationResult<T> {
  return Object.freeze({ ok: false as const, error: Object.freeze({ code }) });
}

async function unlinkIfPresent(file: string): Promise<void> {
  try {
    await unlink(file);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

async function unlinkIfSameFile(source: string, target: string): Promise<void> {
  try {
    const [sourceIdentity, targetIdentity] = await Promise.all([
      lstat(source),
      lstat(target),
    ]);
    if (
      sourceIdentity.isFile() &&
      targetIdentity.isFile() &&
      sourceIdentity.dev === targetIdentity.dev &&
      sourceIdentity.ino === targetIdentity.ino
    ) {
      await unlink(target);
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

export class OwnerComponentPackageStore {
  readonly #options: OwnerComponentPackageStoreOptions;
  readonly #storeRoot: string;

  constructor(options: OwnerComponentPackageStoreOptions) {
    if (!path.isAbsolute(options.dataRoot)) {
      throw new Error("desktop_component_owner_store_root_invalid");
    }
    this.#options = options;
    this.#storeRoot = path.join(options.dataRoot, "component-packages");
  }

  async importPackage(
    workspaceId: string,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>
  > {
    const selected = await this.#options.choosePackage();
    if (selected === null) {
      return Object.freeze({
        ok: true as const,
        value: Object.freeze({ cancelled: true, registration: null }),
      });
    }
    try {
      if (
        !path.isAbsolute(selected) ||
        path.extname(selected).toLowerCase() !== ".json"
      ) {
        return failure("desktop_component_owner_package_path_invalid");
      }
      const parent = path.dirname(selected);
      const metadata = await lstat(selected);
      if (
        !metadata.isFile() ||
        metadata.isSymbolicLink() ||
        metadata.nlink !== 1 ||
        path.normalize(await realpath(parent)).toLowerCase() !==
          path.normalize(parent).toLowerCase() ||
        path.normalize(await realpath(selected)).toLowerCase() !==
          path.normalize(selected).toLowerCase()
      ) {
        return failure("desktop_component_owner_package_identity_invalid");
      }
      const raw = await readFile(selected);
      const after = await lstat(selected);
      if (!ownerComponentPackageFileIdentityMatches(metadata, after)) {
        return failure("desktop_component_owner_package_changed_during_review");
      }
      const parsed = parsePackage(raw);
      return await this.#registerParsed(workspaceId, parsed);
    } catch (error) {
      return failure(
        error instanceof Error && /^[a-z][a-z0-9_]{2,95}$/u.test(error.message)
          ? error.message
          : "desktop_component_owner_package_import_failed",
      );
    }
  }

  async importAssistantPackage(
    input: DesktopWorkspaceComponentAssistantPackageImportInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>
  > {
    try {
      const detail = await this.#options.native.getConversation({
        workspaceId: input.workspaceId,
        conversationId: input.conversationId,
      });
      if (!detail.ok) return failure(detail.error.code);
      if (
        detail.value.conversation.id !== input.conversationId ||
        detail.value.conversation.workspaceId !== input.workspaceId ||
        detail.value.conversation.state !== "active"
      ) {
        return failure("desktop_component_assistant_package_scope_invalid");
      }
      const latest = [...detail.value.messages]
        .reverse()
        .find(
          (message) =>
            message.role === "assistant" &&
            message.status === "completed" &&
            message.invocationId !== null &&
            message.invocation?.id === message.invocationId &&
            message.invocation.status === "succeeded",
        );
      if (latest === undefined || latest.id !== input.messageId) {
        return failure("desktop_component_assistant_package_message_stale");
      }
      const fromMessage = parsePackage(Buffer.from(latest.content, "utf8"));
      const fromOwnerReview = parsePackage(
        Buffer.from(input.packageJson, "utf8"),
      );
      if (
        input.packageJson !==
          fromOwnerReview.canonicalPackage.toString("utf8") ||
        !fromMessage.canonicalPackage.equals(
          fromOwnerReview.canonicalPackage,
        ) ||
        input.manifestSha256 !== fromMessage.manifestSha256 ||
        input.packageSha256 !== fromMessage.packageSha256
      ) {
        return failure("desktop_component_assistant_package_identity_drift");
      }
      return await this.#registerParsed(input.workspaceId, fromMessage);
    } catch (error) {
      return failure(
        error instanceof Error && /^[a-z][a-z0-9_]{2,95}$/u.test(error.message)
          ? error.message
          : "desktop_component_assistant_package_import_failed",
      );
    }
  }

  async readView(
    packageSha256: string,
    manifestSha256: string,
    componentId: string,
  ): Promise<OwnerView | null> {
    if (!/^[a-f0-9]{64}$/u.test(packageSha256)) return null;
    try {
      const file = path.join(this.#storeRoot, `${packageSha256}.json`);
      const parsed = parsePackage(await readFile(file));
      return parsed.packageSha256 === packageSha256 &&
        parsed.manifestSha256 === manifestSha256 &&
        parsed.manifest.component_id === componentId
        ? parsed.view
        : null;
    } catch {
      return null;
    }
  }

  async #registerParsed(
    workspaceId: string,
    parsed: ParsedOwnerPackage,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>
  > {
    let staged: StagedOwnerPackage | null = null;
    let materialized: MaterializedOwnerPackage;
    try {
      staged = await this.#stage(parsed);
      materialized = await staged.promote();
    } catch (error) {
      if (staged !== null) {
        await staged.discard().catch(() => undefined);
      }
      if (
        error instanceof Error &&
        error.message === "desktop_component_owner_package_store_drift"
      ) {
        return failure(error.message);
      }
      return failure("desktop_component_owner_package_promote_failed");
    }
    let registration: DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageRegistration>;
    try {
      registration =
        await this.#options.native.registerOwnerWorkspaceComponentPackage({
          workspaceId,
          manifest: parsed.manifest,
          manifestSha256: parsed.manifestSha256,
          packageSha256: parsed.packageSha256,
          inventorySha256: parsed.inventorySha256,
        });
    } finally {
      // Once registration starts, its commit status can be ambiguous. Keep the
      // content-addressed blob so a committed row can never reference a file
      // that this process removed; an unreferenced blob grants no authority.
      await materialized.finalize().catch(() => undefined);
    }
    if (!registration.ok) {
      return registration;
    }
    return Object.freeze({
      ok: true as const,
      value: Object.freeze({
        cancelled: false,
        registration: registration.value,
      }),
    });
  }

  async #stage(value: ParsedOwnerPackage): Promise<StagedOwnerPackage> {
    await mkdir(this.#storeRoot, { recursive: true, mode: 0o700 });
    const target = path.join(this.#storeRoot, `${value.packageSha256}.json`);
    try {
      const existing = await readFile(target);
      if (!existing.equals(value.canonicalPackage)) {
        throw new Error("desktop_component_owner_package_store_drift");
      }
      return Object.freeze({
        promote: async () => {
          const promoted = await readFile(target);
          if (!promoted.equals(value.canonicalPackage)) {
            throw new Error("desktop_component_owner_package_store_drift");
          }
          return Object.freeze({
            finalize: async () => {},
          });
        },
        discard: async () => {},
      });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const temporary = `${target}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`;
    await writeFile(temporary, value.canonicalPackage, {
      flag: "wx",
      mode: 0o600,
    });
    return Object.freeze({
      promote: async () => {
        let created = false;
        try {
          await link(temporary, target);
          created = true;
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
        }
        try {
          const promoted = await readFile(target);
          if (!promoted.equals(value.canonicalPackage)) {
            throw new Error("desktop_component_owner_package_store_drift");
          }
        } catch (error) {
          if (created) {
            await unlinkIfSameFile(temporary, target).catch(() => undefined);
          }
          throw error;
        }
        return Object.freeze({
          finalize: async () => unlinkIfPresent(temporary),
        });
      },
      discard: async () => unlinkIfPresent(temporary),
    });
  }
}
