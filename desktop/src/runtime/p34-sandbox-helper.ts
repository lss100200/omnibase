import { createHash } from "node:crypto";

const MAX_INPUT_BYTES = 64 * 1024;
const MAX_ARTIFACTS = 32;
const MAX_WORKLOAD_BYTES = 32 * 1024;
const LOGICAL_ID = /^[a-z][a-z0-9_.:-]{0,127}$/u;
const COMPONENT_ID = /^[a-z][a-z0-9_.-]{1,127}$/u;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const RUNTIME_INSTANCE_ID = /^runtime_[a-f0-9]{32}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const BASE64 =
  /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u;
const WORKLOAD_ID = "bounded-transform";
const WORKLOAD_ENTRYPOINT = "transform";
const WORKLOAD_MEMORY_MAX_BYTES = 64 * 1024;

type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | Readonly<{ readonly [key: string]: JsonValue }>;

type WorkloadEnvelope = Readonly<{
  entrypoint: typeof WORKLOAD_ENTRYPOINT;
  memory_max_bytes: typeof WORKLOAD_MEMORY_MAX_BYTES;
  network: "no_imports";
  workload_base64: string;
  workload_sha256: string;
}>;

type HelperRequest =
  | (Readonly<{ kind: "probe"; schema_version: 1 }> & WorkloadEnvelope)
  | (Readonly<{
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
    }> &
      WorkloadEnvelope);

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

function validWorkloadEnvelope(value: Record<string, unknown>): boolean {
  return (
    value.entrypoint === WORKLOAD_ENTRYPOINT &&
    value.memory_max_bytes === WORKLOAD_MEMORY_MAX_BYTES &&
    value.network === "no_imports" &&
    typeof value.workload_base64 === "string" &&
    value.workload_base64.length > 0 &&
    value.workload_base64.length <= Math.ceil(MAX_WORKLOAD_BYTES / 3) * 4 &&
    BASE64.test(value.workload_base64) &&
    typeof value.workload_sha256 === "string" &&
    SHA256.test(value.workload_sha256)
  );
}

function workloadEnvelope(value: Record<string, unknown>): WorkloadEnvelope {
  return Object.freeze({
    entrypoint: WORKLOAD_ENTRYPOINT,
    memory_max_bytes: WORKLOAD_MEMORY_MAX_BYTES,
    network: "no_imports",
    workload_base64: String(value.workload_base64),
    workload_sha256: String(value.workload_sha256),
  });
}

function parseRequest(value: unknown): HelperRequest {
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new Error("sandbox_helper_request_invalid");
  }
  if (
    value.kind === "probe" &&
    exact(value, [
      "entrypoint",
      "kind",
      "memory_max_bytes",
      "network",
      "schema_version",
      "workload_base64",
      "workload_sha256",
    ]) &&
    validWorkloadEnvelope(value)
  ) {
    return Object.freeze({
      kind: "probe",
      schema_version: 1,
      ...workloadEnvelope(value),
    });
  }
  if (
    value.kind !== "run" ||
    !exact(value, [
      "component_id",
      "component_version",
      "entrypoint",
      "input_artifact_ids",
      "kind",
      "memory_max_bytes",
      "network",
      "network_fencing_token",
      "operation_id",
      "request_sha256",
      "runtime_instance_id",
      "schema_version",
      "workload_base64",
      "workload_fencing_token",
      "workload_id",
      "workload_sha256",
      "workspace_id",
    ]) ||
    !validWorkloadEnvelope(value) ||
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
    ...workloadEnvelope(value),
  });
}

function instantiatePackageWorkload(request: WorkloadEnvelope): Readonly<{
  transform: (artifactCount: number) => number;
}> {
  const bytes = Buffer.from(request.workload_base64, "base64");
  if (
    bytes.byteLength < 8 ||
    bytes.byteLength > MAX_WORKLOAD_BYTES ||
    bytes.toString("base64") !== request.workload_base64 ||
    createHash("sha256").update(bytes).digest("hex") !== request.workload_sha256
  ) {
    throw new Error("sandbox_helper_workload_identity_invalid");
  }
  let module: WebAssembly.Module;
  try {
    module = new WebAssembly.Module(bytes);
  } catch {
    throw new Error("sandbox_helper_workload_contract_invalid");
  }
  if (
    WebAssembly.Module.imports(module).length !== 0 ||
    JSON.stringify(WebAssembly.Module.exports(module)) !==
      JSON.stringify([{ name: WORKLOAD_ENTRYPOINT, kind: "function" }])
  ) {
    throw new Error("sandbox_helper_workload_contract_invalid");
  }
  const instance = new WebAssembly.Instance(module, Object.freeze({}));
  const transform = instance.exports[WORKLOAD_ENTRYPOINT];
  if (typeof transform !== "function") {
    throw new Error("sandbox_helper_workload_contract_invalid");
  }
  return Object.freeze({
    transform: (artifactCount: number): number => {
      let result: unknown;
      try {
        result = transform(artifactCount);
      } catch {
        throw new Error("sandbox_helper_workload_execution_failed");
      }
      if (
        !Number.isInteger(result) ||
        Number(result) < -2_147_483_648 ||
        Number(result) > 2_147_483_647
      ) {
        throw new Error("sandbox_helper_workload_result_invalid");
      }
      return Number(result);
    },
  });
}

export function runP34SandboxHelperRequest(value: unknown): JsonValue {
  const request = parseRequest(value);
  const workload = instantiatePackageWorkload(request);
  if (request.kind === "probe") {
    workload.transform(0);
    return Object.freeze({
      adapter: "p34-sandbox.v1",
      isolation: Object.freeze({
        execution: "package_bound_zero_import_webassembly",
        host_capabilities: "none",
        memory_max_bytes: request.memory_max_bytes,
        network: request.network,
        workload_sha256: request.workload_sha256,
      }),
      schema_version: 1,
      status: "ready",
    });
  }

  const startedAt = performance.now();
  const transformValue = workload.transform(request.input_artifact_ids.length);
  const fingerprintSha256 = createHash("sha256")
    .update(
      canonicalJson({
        component_id: request.component_id,
        component_version: request.component_version,
        input_artifact_ids: request.input_artifact_ids,
        transform_value: transformValue,
        workload_id: request.workload_id,
        workload_sha256: request.workload_sha256,
      }),
      "utf8",
    )
    .digest("hex");
  const result = Object.freeze({
    artifact_count: request.input_artifact_ids.length,
    fingerprint_sha256: fingerprintSha256,
    kind: "artifact_inventory",
    transform_value: transformValue,
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
      workload_sha256: request.workload_sha256,
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
