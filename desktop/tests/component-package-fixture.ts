import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import type {
  RuntimeManifestFile,
  VerifiedRuntimeBundle,
} from "../src/runtime/manifest.ts";

type Family =
  | "declarative_ui"
  | "instruction_skill"
  | "mcp_connector"
  | "sandbox_workload"
  | "trusted_local_adapter";

type Adapter =
  | "builtin-ui.v1"
  | "instruction-skill.v1"
  | "readonly-mcp.v1"
  | "p34-sandbox.v1"
  | "trusted-local-app.v1";

interface ComponentDefinition {
  readonly componentId: string;
  readonly family: Family;
  readonly adapterId: Adapter;
}

export interface SourcePackageFixtureEntry {
  readonly componentId: string;
  readonly version: string;
  readonly family: Family;
  readonly adapterId: Adapter;
  readonly manifestPath: string;
  readonly packagePath: string;
  readonly inventoryPath: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly inventorySha256: string;
  readonly payloadPaths: readonly string[];
}

export interface SourceComponentRuntimeFixture {
  readonly root: string;
  readonly bundle: VerifiedRuntimeBundle;
  readonly entries: ReadonlyMap<string, SourcePackageFixtureEntry>;
  readonly verifiedSha256: (relativePath: string) => string | null;
  readonly readIndex: () => Promise<Record<string, unknown>>;
  readonly replaceFile: (
    runtimePath: string,
    raw: Buffer,
    updateDeclaration?: boolean,
  ) => Promise<void>;
  readonly addDeclaredFile: (runtimePath: string, raw: Buffer) => Promise<void>;
  readonly dispose: () => Promise<void>;
}

const COMPONENTS: readonly ComponentDefinition[] = Object.freeze([
  Object.freeze({
    componentId: "builtin.workspace-canvas",
    family: "declarative_ui",
    adapterId: "builtin-ui.v1",
  }),
  Object.freeze({
    componentId: "builtin.instruction-skill",
    family: "instruction_skill",
    adapterId: "instruction-skill.v1",
  }),
  Object.freeze({
    componentId: "builtin.readonly-mcp",
    family: "mcp_connector",
    adapterId: "readonly-mcp.v1",
  }),
  Object.freeze({
    componentId: "builtin.sandbox-workload",
    family: "sandbox_workload",
    adapterId: "p34-sandbox.v1",
  }),
  Object.freeze({
    componentId: "knowledge.ebook",
    family: "trusted_local_adapter",
    adapterId: "trusted-local-app.v1",
  }),
]);

const VERSIONS = Object.freeze(["1.0.0", "1.1.0"] as const);

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<Record<string, unknown>>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function digestRaw(raw: Buffer | string): string {
  return createHash("sha256").update(raw).digest("hex");
}

function encoded(value: unknown): Buffer {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

function payloads(
  component: ComponentDefinition,
  version: string,
): Readonly<Record<string, Buffer>> {
  switch (component.family) {
    case "declarative_ui":
      return Object.freeze({
        "payload/view.json": encoded({
          component_id: component.componentId,
          schema_version: 1,
          version,
          view: {
            kind: "workspace_summary",
            sections: [{ id: "health", label: "Health", source: "health" }],
            title: version === "1.0.0" ? "Canvas 1.0" : "Canvas 1.1",
          },
        }),
      });
    case "instruction_skill":
      return Object.freeze({
        "payload/instruction.json": encoded({
          component_id: component.componentId,
          instruction:
            version === "1.0.0"
              ? "Return an Owner-reviewable observation."
              : "Return evidence, uncertainty, and an Owner-reviewable observation.",
          schema_version: 1,
          version,
        }),
      });
    case "mcp_connector":
      return Object.freeze({
        "payload/mcp.json": encoded({
          component_id: component.componentId,
          schema_version: 1,
          server: {
            server_id: "workspace-files-readonly",
            transport: "host_native",
          },
          tools: [
            {
              input: { directory: "logical_relative_directory" },
              operation: "workspace.files.list",
              output: "bounded_logical_file_inventory",
              tool_id: "omnibase_files_list",
            },
            {
              input: { path: "logical_relative_file" },
              operation: "workspace.files.read",
              output: "bounded_utf8_file",
              tool_id: "omnibase_files_read",
            },
            {
              input: { path: "logical_relative_file" },
              operation: "workspace.files.hash",
              output: "bounded_file_identity",
              tool_id: "omnibase_files_hash",
            },
            {
              input: {
                path: "logical_relative_file",
                query: "bounded_text",
              },
              operation: "workspace.text.search",
              output: "bounded_match_inventory",
              tool_id: "omnibase_text_search",
            },
          ],
          version,
        }),
      });
    case "sandbox_workload":
      return Object.freeze({
        "payload/workload.json": encoded({
          component_id: component.componentId,
          input_contract: "logical_artifact_ids",
          output_contract: "artifact_inventory",
          provider: "p34-sandbox.v1",
          schema_version: 1,
          version,
          workload_id: "bounded-transform",
        }),
      });
    case "trusted_local_adapter":
      return Object.freeze({
        "payload/adapter.json": encoded({
          adapter_id: "trusted-local-app.v1",
          catalog_path: "payload/catalog.json",
          component_id: component.componentId,
          operation: "local_adapter.open",
          schema_version: 1,
          version,
        }),
        "payload/catalog.json": encoded({
          component_id: component.componentId,
          component_version: version,
          documents: [],
          glossary: [],
          invariants: [],
          modules: [],
          schema_version: 1,
          source_snapshot_sha256: digestRaw(`ebook-${version}`),
        }),
      });
  }
}

async function writeRuntimeFile(
  root: string,
  runtimePath: string,
  raw: Buffer,
): Promise<RuntimeManifestFile> {
  const target = path.join(root, ...runtimePath.split("/"));
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, raw);
  return Object.freeze({
    path: runtimePath,
    size: raw.byteLength,
    sha256: digestRaw(raw),
  });
}

export async function createSourceComponentRuntimeFixture(): Promise<SourceComponentRuntimeFixture> {
  const root = await mkdtemp(
    path.join(os.tmpdir(), "omnibase-p73-component-packages-"),
  );
  const files: RuntimeManifestFile[] = [];
  const entries = new Map<string, SourcePackageFixtureEntry>();
  const indexPackages: Record<string, unknown>[] = [];

  for (const component of COMPONENTS) {
    for (const version of VERSIONS) {
      const slug = component.componentId.replaceAll(".", "-");
      const base = `${slug}/${version}/`;
      const manifestPath = `${base}manifest.json`;
      const packagePath = `${base}package.json`;
      const inventoryPath = `${base}inventory.json`;
      const manifestRaw = Buffer.from(
        canonicalJson({
          component_id: component.componentId,
          entrypoint: { adapter_id: component.adapterId },
          family: component.family,
          version,
        }),
        "utf8",
      );
      const manifestSha256 = digestRaw(manifestRaw);
      const familyPayloads = payloads(component, version);
      const inventoryFiles = Object.entries(familyPayloads).map(
        ([payloadPath, raw]) => ({
          path: payloadPath,
          sha256: digestRaw(raw),
          size: raw.byteLength,
        }),
      );
      const inventoryRaw = encoded({
        component_id: component.componentId,
        files: inventoryFiles,
        schema_version: 1,
        version,
      });
      const inventorySha256 = digestRaw(inventoryRaw);
      const packageRaw = encoded({
        adapter_id: component.adapterId,
        component_id: component.componentId,
        family: component.family,
        inventory_sha256: inventorySha256,
        manifest_sha256: manifestSha256,
        package_schema_version: 1,
        publisher: { classification: "source_owned", id: "omnibase" },
        version,
      });
      const packageSha256 = digestRaw(packageRaw);
      files.push(
        await writeRuntimeFile(root, `components/${manifestPath}`, manifestRaw),
        await writeRuntimeFile(root, `components/${packagePath}`, packageRaw),
        await writeRuntimeFile(
          root,
          `components/${inventoryPath}`,
          inventoryRaw,
        ),
      );
      for (const [payloadPath, raw] of Object.entries(familyPayloads)) {
        files.push(
          await writeRuntimeFile(root, `components/${base}${payloadPath}`, raw),
        );
      }
      indexPackages.push({
        adapter_id: component.adapterId,
        component_id: component.componentId,
        family: component.family,
        inventory_path: inventoryPath,
        inventory_sha256: inventorySha256,
        manifest_path: manifestPath,
        manifest_sha256: manifestSha256,
        package_path: packagePath,
        package_sha256: packageSha256,
        policy_manifest_sha256: manifestSha256,
        version,
      });
      entries.set(
        `${component.componentId}@${version}`,
        Object.freeze({
          componentId: component.componentId,
          version,
          family: component.family,
          adapterId: component.adapterId,
          manifestPath,
          packagePath,
          inventoryPath,
          manifestSha256,
          packageSha256,
          inventorySha256,
          payloadPaths: Object.freeze(Object.keys(familyPayloads)),
        }),
      );
    }
  }
  const indexRaw = encoded({ packages: indexPackages, schema_version: 1 });
  files.push(await writeRuntimeFile(root, "components/index.json", indexRaw));
  const bundle: VerifiedRuntimeBundle & { files: RuntimeManifestFile[] } = {
    root,
    command: path.join(root, "unused.exe"),
    args: Object.freeze([]),
    files,
    manifestSha256: digestRaw("runtime-manifest"),
    startupTimeoutMs: 1_000,
    backendPort: 8_765,
  };

  const replaceFile = async (
    runtimePath: string,
    raw: Buffer,
    updateDeclaration = false,
  ): Promise<void> => {
    const target = path.join(root, ...runtimePath.split("/"));
    await writeFile(target, raw);
    if (updateDeclaration) {
      const index = files.findIndex((item) => item.path === runtimePath);
      if (index < 0) throw new Error("fixture_runtime_file_missing");
      files[index] = Object.freeze({
        path: runtimePath,
        size: raw.byteLength,
        sha256: digestRaw(raw),
      });
    }
  };

  return Object.freeze({
    root,
    bundle,
    entries,
    verifiedSha256: (relativePath: string) =>
      files.find((item) => item.path === relativePath)?.sha256 ?? null,
    readIndex: async () =>
      JSON.parse(
        (await readFile(path.join(root, "components", "index.json"))).toString(
          "utf8",
        ),
      ) as Record<string, unknown>,
    replaceFile,
    addDeclaredFile: async (runtimePath: string, raw: Buffer) => {
      files.push(await writeRuntimeFile(root, runtimePath, raw));
    },
    dispose: async () => rm(root, { recursive: true, force: true }),
  });
}
