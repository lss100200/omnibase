import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

import type { DesktopWorkspaceComponentPackageAttestationInput } from "../shared/workspace-components.ts";
import type { RuntimeManifestFile, VerifiedRuntimeBundle } from "./manifest.ts";
import { isSafeRuntimeRelativePath } from "./manifest.ts";

const SHA256 = /^[a-f0-9]{64}$/u;
const COMPONENT_ID = /^[a-z][a-z0-9.-]{2,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const ADAPTERS = new Set([
  "builtin-ui.v1",
  "instruction-skill.v1",
  "readonly-mcp.v1",
  "p34-sandbox.v1",
  "trusted-local-app.v1",
]);
const FAMILIES = new Set([
  "declarative_ui",
  "instruction_skill",
  "mcp_connector",
  "sandbox_workload",
  "trusted_local_adapter",
]);
const EXPECTED_SOURCE_IDENTITIES = new Set([
  "builtin.workspace-canvas@1.0.0",
  "builtin.workspace-canvas@1.1.0",
  "builtin.instruction-skill@1.0.0",
  "builtin.instruction-skill@1.1.0",
  "builtin.readonly-mcp@1.0.0",
  "builtin.readonly-mcp@1.1.0",
  "builtin.sandbox-workload@1.0.0",
  "builtin.sandbox-workload@1.1.0",
  "knowledge.ebook@1.0.0",
  "knowledge.ebook@1.1.0",
]);

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

function digest(raw: Buffer): string {
  return createHash("sha256").update(raw).digest("hex");
}

function componentPath(relative: string): string {
  if (!isSafeRuntimeRelativePath(relative)) {
    throw new Error("runtime_component_path_invalid");
  }
  return `components/${relative}`;
}

async function verifiedRead(
  root: string,
  files: ReadonlyMap<string, RuntimeManifestFile>,
  relative: string,
  expectedSha256: string,
): Promise<Buffer> {
  const runtimePath = componentPath(relative);
  const declared = files.get(runtimePath);
  if (declared === undefined || declared.sha256 !== expectedSha256) {
    throw new Error("runtime_component_manifest_binding_invalid");
  }
  const raw = await readFile(path.join(root, ...runtimePath.split("/")));
  if (raw.byteLength !== declared.size || digest(raw) !== declared.sha256) {
    throw new Error("runtime_component_file_changed");
  }
  return raw;
}

export async function loadSourceComponentAttestations(
  bundle: VerifiedRuntimeBundle,
): Promise<readonly DesktopWorkspaceComponentPackageAttestationInput[]> {
  const files = new Map(bundle.files.map((file) => [file.path, file] as const));
  const indexFile = files.get("components/index.json");
  if (indexFile === undefined) {
    throw new Error("runtime_component_index_missing");
  }
  const indexRaw = await verifiedRead(
    bundle.root,
    files,
    "index.json",
    indexFile.sha256,
  );
  let decoded: unknown;
  try {
    decoded = JSON.parse(indexRaw.toString("utf8"));
  } catch {
    throw new Error("runtime_component_index_json_invalid");
  }
  if (
    !isRecord(decoded) ||
    !exact(decoded, ["packages", "schema_version"]) ||
    decoded.schema_version !== 1 ||
    !Array.isArray(decoded.packages) ||
    decoded.packages.length !== EXPECTED_SOURCE_IDENTITIES.size
  ) {
    throw new Error("runtime_component_index_shape_invalid");
  }

  const identities = new Set<string>();
  const claimedFiles = new Set(["components/index.json"]);
  const attestations: DesktopWorkspaceComponentPackageAttestationInput[] = [];
  for (const item of decoded.packages) {
    if (
      !isRecord(item) ||
      !exact(item, [
        "adapter_id",
        "component_id",
        "family",
        "inventory_path",
        "inventory_sha256",
        "manifest_path",
        "manifest_sha256",
        "package_path",
        "package_sha256",
        "policy_manifest_sha256",
        "version",
      ]) ||
      typeof item.component_id !== "string" ||
      !COMPONENT_ID.test(item.component_id) ||
      typeof item.version !== "string" ||
      !VERSION.test(item.version) ||
      typeof item.family !== "string" ||
      !FAMILIES.has(item.family) ||
      typeof item.adapter_id !== "string" ||
      !ADAPTERS.has(item.adapter_id) ||
      typeof item.policy_manifest_sha256 !== "string" ||
      !SHA256.test(item.policy_manifest_sha256) ||
      typeof item.manifest_sha256 !== "string" ||
      !SHA256.test(item.manifest_sha256) ||
      item.policy_manifest_sha256 !== item.manifest_sha256 ||
      typeof item.package_sha256 !== "string" ||
      !SHA256.test(item.package_sha256) ||
      typeof item.inventory_sha256 !== "string" ||
      !SHA256.test(item.inventory_sha256) ||
      typeof item.manifest_path !== "string" ||
      typeof item.package_path !== "string" ||
      typeof item.inventory_path !== "string"
    ) {
      throw new Error("runtime_component_index_entry_invalid");
    }
    const identity = `${item.component_id}@${item.version}`;
    if (!EXPECTED_SOURCE_IDENTITIES.has(identity) || identities.has(identity)) {
      throw new Error("runtime_component_identity_set_invalid");
    }
    identities.add(identity);
    const base = `${item.component_id.replaceAll(".", "-")}/${item.version}/`;
    if (
      item.manifest_path !== `${base}manifest.json` ||
      item.package_path !== `${base}package.json` ||
      item.inventory_path !== `${base}inventory.json`
    ) {
      throw new Error("runtime_component_package_layout_invalid");
    }
    const manifestRaw = await verifiedRead(
      bundle.root,
      files,
      item.manifest_path,
      item.manifest_sha256,
    );
    const packageRaw = await verifiedRead(
      bundle.root,
      files,
      item.package_path,
      item.package_sha256,
    );
    const inventoryRaw = await verifiedRead(
      bundle.root,
      files,
      item.inventory_path,
      item.inventory_sha256,
    );
    claimedFiles.add(componentPath(item.manifest_path));
    claimedFiles.add(componentPath(item.package_path));
    claimedFiles.add(componentPath(item.inventory_path));
    let manifest: unknown;
    let packageValue: unknown;
    let inventory: unknown;
    try {
      manifest = JSON.parse(manifestRaw.toString("utf8"));
      packageValue = JSON.parse(packageRaw.toString("utf8"));
      inventory = JSON.parse(inventoryRaw.toString("utf8"));
    } catch {
      throw new Error("runtime_component_package_json_invalid");
    }
    if (
      !isRecord(manifest) ||
      manifest.component_id !== item.component_id ||
      manifest.version !== item.version ||
      manifest.family !== item.family ||
      !isRecord(manifest.entrypoint) ||
      manifest.entrypoint.adapter_id !== item.adapter_id ||
      !isRecord(packageValue) ||
      !exact(packageValue, [
        "adapter_id",
        "component_id",
        "family",
        "inventory_sha256",
        "manifest_sha256",
        "package_schema_version",
        "publisher",
        "version",
      ]) ||
      packageValue.package_schema_version !== 1 ||
      packageValue.component_id !== item.component_id ||
      packageValue.version !== item.version ||
      packageValue.family !== item.family ||
      packageValue.adapter_id !== item.adapter_id ||
      packageValue.manifest_sha256 !== item.manifest_sha256 ||
      packageValue.inventory_sha256 !== item.inventory_sha256 ||
      !isRecord(inventory) ||
      !exact(inventory, [
        "component_id",
        "files",
        "schema_version",
        "version",
      ]) ||
      inventory.schema_version !== 1 ||
      inventory.component_id !== item.component_id ||
      inventory.version !== item.version ||
      !Array.isArray(inventory.files) ||
      inventory.files.length < 1 ||
      inventory.files.length > 128
    ) {
      throw new Error("runtime_component_package_identity_invalid");
    }
    const payloadPaths = new Set<string>();
    for (const file of inventory.files) {
      if (
        !isRecord(file) ||
        !exact(file, ["path", "sha256", "size"]) ||
        typeof file.path !== "string" ||
        !file.path.startsWith("payload/") ||
        typeof file.sha256 !== "string" ||
        !SHA256.test(file.sha256) ||
        typeof file.size !== "number" ||
        !Number.isSafeInteger(file.size) ||
        file.size < 0 ||
        payloadPaths.has(file.path)
      ) {
        throw new Error("runtime_component_inventory_invalid");
      }
      payloadPaths.add(file.path);
      const relative = `${base}${file.path}`;
      const raw = await verifiedRead(bundle.root, files, relative, file.sha256);
      if (raw.byteLength !== file.size) {
        throw new Error("runtime_component_inventory_size_invalid");
      }
      claimedFiles.add(componentPath(relative));
    }
    attestations.push(
      Object.freeze({
        componentId: item.component_id,
        version: item.version,
        adapterId:
          item.adapter_id as DesktopWorkspaceComponentPackageAttestationInput["adapterId"],
        policyManifestSha256: item.policy_manifest_sha256,
        manifestSha256: item.manifest_sha256,
        packageSha256: item.package_sha256,
        inventorySha256: item.inventory_sha256,
      }),
    );
  }
  if (
    identities.size !== EXPECTED_SOURCE_IDENTITIES.size ||
    [...EXPECTED_SOURCE_IDENTITIES].some(
      (identity) => !identities.has(identity),
    ) ||
    [...files].some(
      ([runtimePath]) =>
        runtimePath.startsWith("components/") && !claimedFiles.has(runtimePath),
    )
  ) {
    throw new Error("runtime_component_tree_not_closed");
  }
  return Object.freeze(attestations);
}

export interface SourceComponentPayloadOptions {
  runtimeRoot: string;
  getVerifiedRuntimeFileSha256: (relativePath: string) => string | null;
  componentId: string;
  version: string;
  manifestSha256: string;
  packageSha256: string;
  payloadName: string;
}

export interface SourceComponentPayloadAsset {
  readonly value: unknown;
  readonly sha256: string;
  readonly size: number;
}

export interface SourceComponentBinaryAsset {
  readonly bytes: Buffer;
  readonly sha256: string;
  readonly size: number;
}

async function readSourceComponentPayloadRaw(
  options: Readonly<SourceComponentPayloadOptions>,
): Promise<Readonly<{ raw: Buffer; sha256: string; size: number }>> {
  if (
    !COMPONENT_ID.test(options.componentId) ||
    !VERSION.test(options.version) ||
    !SHA256.test(options.manifestSha256) ||
    !SHA256.test(options.packageSha256) ||
    !/^[a-z][a-z0-9._-]{1,63}\.(?:json|wasm)$/u.test(options.payloadName)
  ) {
    throw new Error("runtime_component_asset_identity_invalid");
  }
  const indexPath = "components/index.json";
  const indexSha = options.getVerifiedRuntimeFileSha256(indexPath);
  if (indexSha === null) throw new Error("runtime_component_index_unavailable");
  const indexRaw = await readFile(
    path.join(options.runtimeRoot, "components", "index.json"),
  );
  if (digest(indexRaw) !== indexSha)
    throw new Error("runtime_component_index_changed");
  let index: unknown;
  try {
    index = JSON.parse(indexRaw.toString("utf8"));
  } catch {
    throw new Error("runtime_component_index_json_invalid");
  }
  if (!isRecord(index) || !Array.isArray(index.packages)) {
    throw new Error("runtime_component_index_shape_invalid");
  }
  const matches = index.packages.filter(
    (item) =>
      isRecord(item) &&
      item.component_id === options.componentId &&
      item.version === options.version &&
      item.manifest_sha256 === options.manifestSha256 &&
      item.package_sha256 === options.packageSha256,
  );
  if (matches.length !== 1 || !isRecord(matches[0])) {
    throw new Error("runtime_component_asset_package_mismatch");
  }
  const entry = matches[0];
  if (
    typeof entry.inventory_path !== "string" ||
    typeof entry.inventory_sha256 !== "string" ||
    !SHA256.test(entry.inventory_sha256)
  ) {
    throw new Error("runtime_component_asset_inventory_invalid");
  }
  const inventoryRuntimePath = componentPath(entry.inventory_path);
  if (
    options.getVerifiedRuntimeFileSha256(inventoryRuntimePath) !==
    entry.inventory_sha256
  ) {
    throw new Error("runtime_component_asset_inventory_unverified");
  }
  const inventoryRaw = await readFile(
    path.join(options.runtimeRoot, ...inventoryRuntimePath.split("/")),
  );
  if (digest(inventoryRaw) !== entry.inventory_sha256) {
    throw new Error("runtime_component_asset_inventory_changed");
  }
  let inventory: unknown;
  try {
    inventory = JSON.parse(inventoryRaw.toString("utf8"));
  } catch {
    throw new Error("runtime_component_asset_inventory_invalid");
  }
  if (!isRecord(inventory) || !Array.isArray(inventory.files)) {
    throw new Error("runtime_component_asset_inventory_invalid");
  }
  const relativePayload = `payload/${options.payloadName}`;
  const files = inventory.files.filter(
    (file) => isRecord(file) && file.path === relativePayload,
  );
  if (
    files.length !== 1 ||
    !isRecord(files[0]) ||
    typeof files[0].sha256 !== "string" ||
    !SHA256.test(files[0].sha256) ||
    typeof files[0].size !== "number" ||
    !Number.isSafeInteger(files[0].size) ||
    files[0].size < 8 ||
    files[0].size > 32 * 1024 * 1024 ||
    typeof entry.manifest_path !== "string"
  ) {
    throw new Error("runtime_component_asset_not_declared");
  }
  const base = entry.manifest_path.slice(0, -"manifest.json".length);
  const payloadRuntimePath = componentPath(`${base}${relativePayload}`);
  if (
    options.getVerifiedRuntimeFileSha256(payloadRuntimePath) !== files[0].sha256
  ) {
    throw new Error("runtime_component_asset_unverified");
  }
  const payloadRaw = await readFile(
    path.join(options.runtimeRoot, ...payloadRuntimePath.split("/")),
  );
  if (
    payloadRaw.byteLength !== files[0].size ||
    digest(payloadRaw) !== files[0].sha256
  ) {
    throw new Error("runtime_component_asset_changed");
  }
  return Object.freeze({
    raw: payloadRaw,
    sha256: files[0].sha256,
    size: files[0].size,
  });
}

export async function readSourceComponentPayloadAsset(
  options: Readonly<SourceComponentPayloadOptions>,
): Promise<SourceComponentPayloadAsset> {
  if (!options.payloadName.endsWith(".json")) {
    throw new Error("runtime_component_asset_identity_invalid");
  }
  const asset = await readSourceComponentPayloadRaw(options);
  try {
    return Object.freeze({
      value: JSON.parse(asset.raw.toString("utf8")),
      sha256: asset.sha256,
      size: asset.size,
    });
  } catch {
    throw new Error("runtime_component_asset_json_invalid");
  }
}

export async function readSourceComponentBinaryAsset(
  options: Readonly<SourceComponentPayloadOptions>,
): Promise<SourceComponentBinaryAsset> {
  if (!options.payloadName.endsWith(".wasm")) {
    throw new Error("runtime_component_asset_identity_invalid");
  }
  const asset = await readSourceComponentPayloadRaw(options);
  return Object.freeze({
    bytes: asset.raw,
    sha256: asset.sha256,
    size: asset.size,
  });
}

export async function readSourceComponentPayload(
  options: Readonly<SourceComponentPayloadOptions>,
): Promise<unknown> {
  return (await readSourceComponentPayloadAsset(options)).value;
}
