import { createHash, timingSafeEqual } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";

const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const MAX_MANIFEST_BYTES = 1024 * 1024;
const MAX_RUNTIME_FILES = 512;
const MAX_DECLARED_RUNTIME_BYTES = 8 * 1024 * 1024 * 1024;
const MANIFEST_KEYS = new Set(["schemaVersion", "entrypoint", "files"]);
const ENTRYPOINT_KEYS = new Set(["path", "args"]);
const FILE_KEYS = new Set(["path", "size", "sha256"]);

export interface RuntimeManifestFile {
  readonly path: string;
  readonly size: number;
  readonly sha256: string;
}

export interface RuntimeManifest {
  readonly schemaVersion: 1;
  readonly entrypoint: {
    readonly path: string;
    readonly args: readonly string[];
  };
  readonly files: readonly RuntimeManifestFile[];
}

export interface VerifiedRuntimeBundle {
  readonly root: string;
  readonly command: string;
  readonly args: readonly string[];
  readonly manifestSha256: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

export function isSafeRuntimeRelativePath(value: string): boolean {
  if (
    value.length === 0 ||
    value.length > 240 ||
    value.includes("\\") ||
    value.includes(":") ||
    value.startsWith("/") ||
    value.endsWith("/")
  ) {
    return false;
  }
  const parts = value.split("/");
  return parts.every(
    (part) => part !== "" && part !== "." && part !== ".." && part.length <= 100,
  );
}

function parseManifest(raw: Buffer): RuntimeManifest {
  if (raw.byteLength === 0 || raw.byteLength > MAX_MANIFEST_BYTES) {
    throw new Error("runtime_manifest_size_invalid");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.toString("utf8"));
  } catch {
    throw new Error("runtime_manifest_json_invalid");
  }

  if (!isRecord(parsed) || !hasOnlyKeys(parsed, MANIFEST_KEYS)) {
    throw new Error("runtime_manifest_shape_invalid");
  }
  if (parsed.schemaVersion !== 1) {
    throw new Error("runtime_manifest_schema_unsupported");
  }
  if (
    !isRecord(parsed.entrypoint) ||
    !hasOnlyKeys(parsed.entrypoint, ENTRYPOINT_KEYS) ||
    typeof parsed.entrypoint.path !== "string" ||
    !isSafeRuntimeRelativePath(parsed.entrypoint.path) ||
    !Array.isArray(parsed.entrypoint.args) ||
    parsed.entrypoint.args.length > 32 ||
    !parsed.entrypoint.args.every(
      (arg) => typeof arg === "string" && arg.length <= 1024 && !arg.includes("\0"),
    )
  ) {
    throw new Error("runtime_manifest_entrypoint_invalid");
  }
  if (
    !Array.isArray(parsed.files) ||
    parsed.files.length === 0 ||
    parsed.files.length > MAX_RUNTIME_FILES
  ) {
    throw new Error("runtime_manifest_files_invalid");
  }

  const files: RuntimeManifestFile[] = [];
  const seen = new Set<string>();
  let totalSize = 0;
  for (const value of parsed.files) {
    if (
      !isRecord(value) ||
      !hasOnlyKeys(value, FILE_KEYS) ||
      typeof value.path !== "string" ||
      !isSafeRuntimeRelativePath(value.path) ||
      typeof value.size !== "number" ||
      !Number.isSafeInteger(value.size) ||
      value.size < 0 ||
      typeof value.sha256 !== "string" ||
      !SHA256_PATTERN.test(value.sha256) ||
      seen.has(value.path)
    ) {
      throw new Error("runtime_manifest_file_invalid");
    }
    totalSize += value.size;
    if (!Number.isSafeInteger(totalSize) || totalSize > MAX_DECLARED_RUNTIME_BYTES) {
      throw new Error("runtime_manifest_total_size_invalid");
    }
    seen.add(value.path);
    files.push({ path: value.path, size: value.size, sha256: value.sha256 });
  }
  if (!seen.has(parsed.entrypoint.path)) {
    throw new Error("runtime_manifest_entrypoint_unlisted");
  }

  return {
    schemaVersion: 1,
    entrypoint: {
      path: parsed.entrypoint.path,
      args: Object.freeze([...parsed.entrypoint.args]) as readonly string[],
    },
    files: Object.freeze(files),
  };
}

function safeEqualHex(actual: string, expected: string): boolean {
  if (!SHA256_PATTERN.test(actual) || !SHA256_PATTERN.test(expected)) {
    return false;
  }
  return timingSafeEqual(Buffer.from(actual, "hex"), Buffer.from(expected, "hex"));
}

function containedPath(root: string, relativePath: string): string {
  const candidate = path.resolve(root, ...relativePath.split("/"));
  const relation = path.relative(root, candidate);
  if (relation === "" || relation.startsWith("..") || path.isAbsolute(relation)) {
    throw new Error("runtime_file_outside_bundle");
  }
  return candidate;
}

async function digestFile(filePath: string): Promise<{ digest: string; size: number }> {
  const hash = createHash("sha256");
  let size = 0;
  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(filePath, { flags: "r" });
    stream.on("data", (chunk: Buffer) => {
      size += chunk.byteLength;
      hash.update(chunk);
    });
    stream.once("error", reject);
    stream.once("end", resolve);
  });
  return { digest: hash.digest("hex"), size };
}

export async function verifyRuntimeBundle(options: {
  readonly bundleRoot: string;
  readonly manifestPath: string;
  readonly expectedManifestSha256: string;
}): Promise<VerifiedRuntimeBundle> {
  if (!path.isAbsolute(options.bundleRoot) || !path.isAbsolute(options.manifestPath)) {
    throw new Error("runtime_bundle_paths_must_be_absolute");
  }
  if (!SHA256_PATTERN.test(options.expectedManifestSha256)) {
    throw new Error("runtime_manifest_trust_not_configured");
  }

  const rootStat = await lstat(options.bundleRoot);
  const manifestStat = await lstat(options.manifestPath);
  if (
    !rootStat.isDirectory() ||
    rootStat.isSymbolicLink() ||
    !manifestStat.isFile() ||
    manifestStat.isSymbolicLink() ||
    manifestStat.size > MAX_MANIFEST_BYTES
  ) {
    throw new Error("runtime_bundle_identity_invalid");
  }

  const root = await realpath(options.bundleRoot);
  const manifestRealPath = await realpath(options.manifestPath);
  const manifestRelation = path.relative(root, manifestRealPath);
  if (
    manifestRelation === "" ||
    manifestRelation.startsWith("..") ||
    path.isAbsolute(manifestRelation)
  ) {
    throw new Error("runtime_manifest_outside_bundle");
  }

  const raw = await readFile(manifestRealPath);
  const manifestSha256 = createHash("sha256").update(raw).digest("hex");
  if (!safeEqualHex(manifestSha256, options.expectedManifestSha256)) {
    throw new Error("runtime_manifest_digest_mismatch");
  }
  const manifest = parseManifest(raw);

  for (const file of manifest.files) {
    const candidate = containedPath(root, file.path);
    const stat = await lstat(candidate);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error("runtime_file_identity_invalid");
    }
    const realCandidate = await realpath(candidate);
    if (realCandidate !== candidate) {
      throw new Error("runtime_file_identity_invalid");
    }
    const actual = await digestFile(realCandidate);
    if (actual.size !== file.size || !safeEqualHex(actual.digest, file.sha256)) {
      throw new Error("runtime_file_digest_mismatch");
    }
  }

  const command = containedPath(root, manifest.entrypoint.path);
  if (!path.isAbsolute(command)) {
    throw new Error("runtime_entrypoint_not_absolute");
  }
  return Object.freeze({
    root,
    command,
    args: manifest.entrypoint.args,
    manifestSha256,
  });
}
