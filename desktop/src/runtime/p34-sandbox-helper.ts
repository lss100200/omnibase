import { createHash } from "node:crypto";

const MAX_INPUT_BYTES = 64 * 1024;
const MAX_ARTIFACTS = 32;
const LOGICAL_ID = /^[a-z][a-z0-9_.:-]{0,127}$/u;
const COMPONENT_ID = /^[a-z][a-z0-9_.-]{1,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const RUNTIME_INSTANCE_ID = /^runtime_[a-f0-9]{32}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const WORKLOAD_ID = "bounded-transform";
const WASM_MEMORY_BYTES = 64 * 1024;
const WASM_SHA256 =
  "99eebd6e301ac000f3e71a4566e72daa99034b005d438b0a7a952f1151fce5ec";

// (module (memory (export "memory") 1 1)
//   (func (export "run") (param i32) (result i32) local.get 0))
// The admitted workload has no imports, so it cannot obtain filesystem,
// network, process, clock or secret authority from its trusted host wrapper.
const BOUNDED_TRANSFORM_WASM = Buffer.from(
  "0061736d0100000001060160017f017f03020100050401010101071002066d656d6f727902000372756e00000a0601040020000b",
  "hex",
);

type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | Readonly<{ readonly [key: string]: JsonValue }>;

type HelperRequest =
  | Readonly<{ kind: "probe"; schema_version: 1 }>
  | Readonly<{
      kind: "run";
      schema_version: 1;
      component_id: string;
      component_version: string;
      input_artifact_ids: readonly string[];
      network_fencing_token: null;
      operation_id: string;
      request_sha256: string;
      runtime_instance_id: string;
      workload_fencing_token: number;
      workload_id: typeof WORKLOAD_ID;
      workspace_id: string;
    }>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exact(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function canonicalJson(value: JsonValue): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<Record<string, JsonValue>>;
    return `{${Object.keys(record)
      .sort()
      .map(
        (key) => `${JSON.stringify(key)}:${canonicalJson(record[key] ?? null)}`,
      )
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function parseRequest(value: unknown): HelperRequest {
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new Error("sandbox_helper_request_invalid");
  }
  if (value.kind === "probe" && exact(value, ["kind", "schema_version"])) {
    return Object.freeze({ kind: "probe", schema_version: 1 });
  }
  if (
    value.kind !== "run" ||
    !exact(value, [
      "component_id",
      "component_version",
      "input_artifact_ids",
      "kind",
      "network_fencing_token",
      "operation_id",
      "request_sha256",
      "runtime_instance_id",
      "schema_version",
      "workload_fencing_token",
      "workload_id",
      "workspace_id",
    ]) ||
    typeof value.component_id !== "string" ||
    !COMPONENT_ID.test(value.component_id) ||
    typeof value.component_version !== "string" ||
    !VERSION.test(value.component_version) ||
    value.workload_id !== WORKLOAD_ID ||
    typeof value.runtime_instance_id !== "string" ||
    !RUNTIME_INSTANCE_ID.test(value.runtime_instance_id) ||
    typeof value.workspace_id !== "string" ||
    !LOGICAL_ID.test(value.workspace_id) ||
    typeof value.operation_id !== "string" ||
    !LOGICAL_ID.test(value.operation_id) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256.test(value.request_sha256) ||
    !Number.isSafeInteger(value.workload_fencing_token) ||
    Number(value.workload_fencing_token) < 1 ||
    value.network_fencing_token !== null ||
    !Array.isArray(value.input_artifact_ids) ||
    value.input_artifact_ids.length > MAX_ARTIFACTS ||
    value.input_artifact_ids.some(
      (item) => typeof item !== "string" || !LOGICAL_ID.test(item),
    ) ||
    new Set(value.input_artifact_ids).size !== value.input_artifact_ids.length
  ) {
    throw new Error("sandbox_helper_request_invalid");
  }
  return Object.freeze({
    kind: "run",
    schema_version: 1,
    component_id: value.component_id,
    component_version: value.component_version,
    input_artifact_ids: Object.freeze(
      value.input_artifact_ids.map((item) => String(item)),
    ),
    network_fencing_token: null,
    operation_id: value.operation_id,
    request_sha256: value.request_sha256,
    runtime_instance_id: value.runtime_instance_id,
    workload_fencing_token: Number(value.workload_fencing_token),
    workload_id: WORKLOAD_ID,
    workspace_id: value.workspace_id,
  });
}

function instantiateBoundedWorkload(): Readonly<{
  memory: WebAssembly.Memory;
  run: (artifactCount: number) => number;
}> {
  if (
    createHash("sha256").update(BOUNDED_TRANSFORM_WASM).digest("hex") !==
    WASM_SHA256
  ) {
    throw new Error("sandbox_helper_workload_identity_invalid");
  }
  const module = new WebAssembly.Module(BOUNDED_TRANSFORM_WASM);
  if (
    WebAssembly.Module.imports(module).length !== 0 ||
    JSON.stringify(WebAssembly.Module.exports(module)) !==
      JSON.stringify([
        { name: "memory", kind: "memory" },
        { name: "run", kind: "function" },
      ])
  ) {
    throw new Error("sandbox_helper_workload_contract_invalid");
  }
  const instance = new WebAssembly.Instance(module);
  const memory = instance.exports.memory;
  const run = instance.exports.run;
  if (
    !(memory instanceof WebAssembly.Memory) ||
    memory.buffer.byteLength !== WASM_MEMORY_BYTES ||
    typeof run !== "function"
  ) {
    throw new Error("sandbox_helper_workload_contract_invalid");
  }
  try {
    memory.grow(1);
    throw new Error("sandbox_helper_workload_memory_unbounded");
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
  }
  return Object.freeze({
    memory,
    run: (artifactCount: number) => Number(run(artifactCount)),
  });
}

export function runP34SandboxHelperRequest(value: unknown): JsonValue {
  const request = parseRequest(value);
  const workload = instantiateBoundedWorkload();
  if (request.kind === "probe") {
    return Object.freeze({
      adapter: "p34-sandbox.v1",
      isolation: Object.freeze({
        execution: "zero_import_webassembly",
        host_capabilities: "none",
        memory_max_bytes: WASM_MEMORY_BYTES,
        network: "no_imports",
        workload_sha256: WASM_SHA256,
      }),
      schema_version: 1,
      status: "ready",
    });
  }

  const startedAt = performance.now();
  const artifactCount = workload.run(request.input_artifact_ids.length);
  if (
    artifactCount !== request.input_artifact_ids.length ||
    workload.memory.buffer.byteLength !== WASM_MEMORY_BYTES
  ) {
    throw new Error("sandbox_helper_workload_result_invalid");
  }
  const fingerprintSha256 = createHash("sha256")
    .update(
      canonicalJson({
        component_id: request.component_id,
        component_version: request.component_version,
        input_artifact_ids: request.input_artifact_ids,
        workload_id: request.workload_id,
      }),
      "utf8",
    )
    .digest("hex");
  const result = Object.freeze({
    artifact_count: artifactCount,
    fingerprint_sha256: fingerprintSha256,
    kind: "artifact_inventory",
  });
  return Object.freeze({
    binding: Object.freeze({
      network_fencing_token: null,
      operation_id: request.operation_id,
      request_sha256: request.request_sha256,
      runtime_instance_id: request.runtime_instance_id,
      workload_fencing_token: request.workload_fencing_token,
      workspace_id: request.workspace_id,
    }),
    output: Object.freeze({
      adapter: "p34-sandbox.v1",
      component_id: request.component_id,
      input_artifact_ids: request.input_artifact_ids,
      result,
      runtime_instance_id: request.runtime_instance_id,
      schema_version: 1,
      status: "completed",
      usage: Object.freeze({
        bytes_in: Buffer.byteLength(
          canonicalJson(request.input_artifact_ids),
          "utf8",
        ),
        bytes_out: Buffer.byteLength(canonicalJson(result), "utf8"),
        wall_time_ms: Math.max(0, Math.ceil(performance.now() - startedAt)),
      }),
      workload_id: request.workload_id,
    }),
  });
}

async function readBoundedStdin(): Promise<Buffer> {
  return await new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    process.stdin.on("data", (chunk: Buffer | string) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, "utf8");
      total += bytes.byteLength;
      if (total > MAX_INPUT_BYTES) {
        reject(new Error("sandbox_helper_input_too_large"));
        process.stdin.destroy();
        return;
      }
      chunks.push(bytes);
    });
    process.stdin.once("error", reject);
    process.stdin.once("end", () => resolve(Buffer.concat(chunks, total)));
  });
}

async function main(): Promise<void> {
  if (process.argv.length !== 3 || process.argv[2] !== "--p7-sandbox-helper") {
    throw new Error("sandbox_helper_arguments_invalid");
  }
  const raw = await readBoundedStdin();
  if (
    raw.byteLength === 0 ||
    raw.includes(0) ||
    raw[raw.byteLength - 1] !== 0x0a ||
    raw.subarray(0, raw.byteLength - 1).includes(0x0a)
  ) {
    throw new Error("sandbox_helper_input_invalid");
  }
  const decoded: unknown = JSON.parse(
    raw.subarray(0, raw.byteLength - 1).toString("utf8"),
  );
  process.stdout.write(
    `${canonicalJson(runP34SandboxHelperRequest(decoded))}\n`,
  );
}

if (require.main === module) {
  main().catch((error: unknown) => {
    const code =
      error instanceof Error && /^sandbox_helper_[a-z_]+$/u.test(error.message)
        ? error.message
        : "sandbox_helper_failed";
    process.stderr.write(`${code}\n`);
    process.exitCode = 2;
  });
}
