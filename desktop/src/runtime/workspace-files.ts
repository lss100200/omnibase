import { createHash } from "node:crypto";
import { constants, type BigIntStats } from "node:fs";
import { lstat, open, opendir, realpath } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import type {
  DesktopOperationResult,
  DesktopParentAgent,
  DesktopWorkspaceFileAuthorization,
  DesktopWorkspaceFileAuthorizeInput,
  DesktopWorkspaceFileList,
  DesktopWorkspaceFileListInput,
  DesktopWorkspaceFileReadInput,
  DesktopWorkspaceFileReadResult,
  DesktopWorkspaceFileReleaseInput,
  DesktopWorkspaceIdInput,
} from "../shared/ipc-contract.ts";

export const WORKSPACE_FILE_MAX_NAME_CHARACTERS = 255;
export const WORKSPACE_FILE_MAX_LOGICAL_PATH_CHARACTERS = 4_096;
export const WORKSPACE_FILE_MAX_DEPTH = 32;
export const WORKSPACE_FILE_MAX_LIST_ENTRIES = 500;
export const WORKSPACE_FILE_MAX_VISITED_ENTRIES = 2_048;
export const WORKSPACE_FILE_MAX_READ_BYTES = 1_048_576;

const READ_CHUNK_BYTES = 64 * 1024;
const CONTROL_OR_BIDI = /[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/u;
const ENCODED_PATH_TOKEN = /%(?:2e|2f|3a|5c)/iu;
const WINDOWS_RESERVED_NAME = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu;
const WINDOWS_DRIVE = /^[A-Za-z]:/u;

const SENSITIVE_NAMES = new Set([
  ".aws",
  ".azure",
  ".docker",
  ".git",
  ".git-credentials",
  ".gcloud",
  ".gnupg",
  ".hg",
  ".kube",
  ".netrc",
  ".npmrc",
  ".pypirc",
  ".ssh",
  ".svn",
  "auth.json",
  "authorized_keys",
  "credentials",
  "credentials.json",
  "credentials.yaml",
  "credentials.yml",
  "id_dsa",
  "id_ecdsa",
  "id_ed25519",
  "id_rsa",
  "known_hosts",
  "secrets.json",
  "service-account.json",
  "service_account.json",
]);

const SENSITIVE_SUFFIXES = [
  ".db",
  ".der",
  ".jks",
  ".key",
  ".keystore",
  ".kdbx",
  ".p12",
  ".pem",
  ".pfx",
  ".pkcs12",
  ".sqlite",
] as const;

type WorkspaceVerifier = (
  input: DesktopWorkspaceIdInput,
) => Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>>;

interface WorkspaceFilesDependencies {
  readonly chooseDirectory: () => Promise<string | null>;
  readonly getWorkspaceAgent: WorkspaceVerifier;
  readonly homeDirectory?: string;
}

interface StableIdentity {
  readonly device: bigint;
  readonly inode: bigint;
  readonly fileType: bigint;
  readonly size: bigint;
  readonly modifiedMs: bigint;
  readonly changedMs: bigint;
}

interface WorkspaceFileBinding {
  readonly workspaceId: string;
  readonly authorizationGeneration: number;
  readonly root: string;
  readonly rootName: string;
  readonly rootIdentity: StableIdentity;
}

class WorkspaceFilesError extends Error {
  constructor(readonly code: string) {
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

function fileType(metadata: BigIntStats): bigint {
  return metadata.mode & BigInt(constants.S_IFMT);
}

function identity(metadata: BigIntStats): StableIdentity {
  return Object.freeze({
    device: metadata.dev,
    inode: metadata.ino,
    fileType: fileType(metadata),
    size: metadata.size,
    modifiedMs: metadata.mtimeMs,
    changedMs: metadata.ctimeMs,
  });
}

function sameDirectoryIdentity(left: StableIdentity, right: StableIdentity): boolean {
  return (
    left.device === right.device &&
    left.inode === right.inode &&
    left.fileType === right.fileType
  );
}

function sameFileIdentity(left: StableIdentity, right: StableIdentity): boolean {
  return (
    sameDirectoryIdentity(left, right) &&
    left.size === right.size &&
    left.modifiedMs === right.modifiedMs &&
    left.changedMs === right.changedMs
  );
}

function samePhysicalPath(left: string, right: string): boolean {
  const normalizedLeft = path.normalize(left);
  const normalizedRight = path.normalize(right);
  return process.platform === "win32"
    ? normalizedLeft.toLocaleLowerCase("en-US") ===
        normalizedRight.toLocaleLowerCase("en-US")
    : normalizedLeft === normalizedRight;
}

function hasAlternateDataStreamSyntax(candidate: string): boolean {
  const withoutDrivePrefix = WINDOWS_DRIVE.test(candidate) ? candidate.slice(2) : candidate;
  return withoutDrivePrefix.includes(":");
}

function isContained(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative))
  );
}

function validateName(name: string): boolean {
  const normalized = name.normalize("NFKC");
  return (
    normalized === name &&
    normalized.length >= 1 &&
    normalized.length <= WORKSPACE_FILE_MAX_NAME_CHARACTERS &&
    normalized.trim() === normalized &&
    !normalized.endsWith(".") &&
    normalized !== "." &&
    normalized !== ".." &&
    !WINDOWS_DRIVE.test(normalized) &&
    !WINDOWS_RESERVED_NAME.test(normalized) &&
    !CONTROL_OR_BIDI.test(normalized) &&
    !ENCODED_PATH_TOKEN.test(normalized) &&
    !/[\\/:]/u.test(normalized)
  );
}

function isSensitiveName(name: string): boolean {
  const lowered = name.normalize("NFKC").toLocaleLowerCase("en-US");
  return (
    lowered === ".env" ||
    lowered.startsWith(".env.") ||
    SENSITIVE_NAMES.has(lowered) ||
    SENSITIVE_SUFFIXES.some((suffix) => lowered.endsWith(suffix)) ||
    /(?:^|[-_.])(?:private[-_.]?key|service[-_.]?account)(?:[-_.]|$)/u.test(lowered) ||
    /(?:^|[-_.])credentials?(?:[-_.][a-z0-9]+)*\.(?:json|ya?ml|toml)$/u.test(lowered)
  );
}

function logicalParts(value: string, allowRoot: boolean): readonly string[] {
  if (
    typeof value !== "string" ||
    value.length > WORKSPACE_FILE_MAX_LOGICAL_PATH_CHARACTERS ||
    (!allowRoot && value.length === 0)
  ) {
    throw new WorkspaceFilesError("desktop_workspace_files_path_invalid");
  }
  if (value === "" && allowRoot) return [];
  if (
    value.startsWith("/") ||
    value.startsWith("\\") ||
    value.endsWith("/") ||
    value.includes("//") ||
    value.includes("\\") ||
    WINDOWS_DRIVE.test(value) ||
    ENCODED_PATH_TOKEN.test(value)
  ) {
    throw new WorkspaceFilesError("desktop_workspace_files_path_invalid");
  }
  const parts = value.split("/");
  if (parts.length > WORKSPACE_FILE_MAX_DEPTH || parts.some((part) => !validateName(part))) {
    throw new WorkspaceFilesError("desktop_workspace_files_path_invalid");
  }
  return parts;
}

function rejectSensitiveParts(parts: readonly string[]): void {
  if (parts.some(isSensitiveName)) {
    throw new WorkspaceFilesError("desktop_workspace_files_sensitive_forbidden");
  }
}

function safeNumber(value: bigint, code: string): number {
  const converted = Number(value);
  if (!Number.isSafeInteger(converted) || converted < 0) {
    throw new WorkspaceFilesError(code);
  }
  return converted;
}

function errorCode(error: unknown): string {
  if (error instanceof WorkspaceFilesError) return error.code;
  if (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { readonly code?: unknown }).code === "ENOENT"
  ) {
    return "desktop_workspace_files_path_not_found";
  }
  return "desktop_workspace_files_unavailable";
}

async function safeLstat(candidate: string): Promise<BigIntStats> {
  try {
    return await lstat(candidate, { bigint: true });
  } catch (error) {
    throw new WorkspaceFilesError(errorCode(error));
  }
}

function rejectLink(metadata: BigIntStats): void {
  if (metadata.isSymbolicLink()) {
    throw new WorkspaceFilesError("desktop_workspace_files_link_forbidden");
  }
}

async function captureDirectoryIdentity(candidate: string): Promise<StableIdentity> {
  const metadata = await safeLstat(candidate);
  rejectLink(metadata);
  if (!metadata.isDirectory()) {
    throw new WorkspaceFilesError("desktop_workspace_files_type_forbidden");
  }
  return identity(metadata);
}

async function captureFileIdentity(candidate: string): Promise<StableIdentity> {
  const metadata = await safeLstat(candidate);
  rejectLink(metadata);
  if (!metadata.isFile()) {
    throw new WorkspaceFilesError("desktop_workspace_files_type_forbidden");
  }
  if (metadata.nlink !== 1n) {
    throw new WorkspaceFilesError("desktop_workspace_files_link_forbidden");
  }
  return identity(metadata);
}

async function validateRoot(selected: string, homeDirectory: string): Promise<{
  readonly root: string;
  readonly rootName: string;
  readonly rootIdentity: StableIdentity;
}> {
  if (
    typeof selected !== "string" ||
    !path.isAbsolute(selected) ||
    selected.includes("\0") ||
    hasAlternateDataStreamSyntax(selected) ||
    (process.platform === "win32" && selected.startsWith("\\\\"))
  ) {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  const lexical = path.resolve(selected);
  const rootIdentity = await captureDirectoryIdentity(lexical);
  let canonical: string;
  try {
    canonical = await realpath(lexical);
  } catch {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  if (!samePhysicalPath(lexical, canonical)) {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  const driveRoot = path.parse(canonical).root;
  if (samePhysicalPath(canonical, driveRoot)) {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  const rootParts = path.relative(driveRoot, canonical).split(path.sep);
  if (rootParts.some((part) => !validateName(part) || isSensitiveName(part))) {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  let canonicalHome = path.resolve(homeDirectory);
  try {
    canonicalHome = await realpath(canonicalHome);
  } catch {
    // A missing launcher-supplied home cannot make a selected root trusted.
  }
  if (isContained(canonical, canonicalHome)) {
    throw new WorkspaceFilesError("desktop_workspace_files_root_unsafe");
  }
  const rootName = path.basename(canonical);
  return Object.freeze({ root: canonical, rootName, rootIdentity });
}

async function verifyRoot(binding: WorkspaceFileBinding): Promise<void> {
  const observed = await captureDirectoryIdentity(binding.root);
  if (!sameDirectoryIdentity(observed, binding.rootIdentity)) {
    throw new WorkspaceFilesError("desktop_workspace_files_identity_drift");
  }
}

async function resolveLogicalPath(
  binding: WorkspaceFileBinding,
  logicalPath: string,
  allowRoot: boolean,
): Promise<{ readonly candidate: string; readonly parts: readonly string[] }> {
  const parts = logicalParts(logicalPath, allowRoot);
  rejectSensitiveParts(parts);
  await verifyRoot(binding);
  let candidate = binding.root;
  for (const part of parts) {
    candidate = path.join(candidate, part);
    const metadata = await safeLstat(candidate);
    rejectLink(metadata);
    let canonical: string;
    try {
      canonical = await realpath(candidate);
    } catch {
      throw new WorkspaceFilesError("desktop_workspace_files_path_not_found");
    }
    if (!samePhysicalPath(candidate, canonical) || !isContained(binding.root, canonical)) {
      throw new WorkspaceFilesError("desktop_workspace_files_link_forbidden");
    }
  }
  return Object.freeze({ candidate, parts });
}

function compareEntries(
  left: DesktopWorkspaceFileList["entries"][number],
  right: DesktopWorkspaceFileList["entries"][number],
): number {
  if (left.kind !== right.kind) return left.kind === "directory" ? -1 : 1;
  const leftName = left.name.toLocaleLowerCase("en-US");
  const rightName = right.name.toLocaleLowerCase("en-US");
  return leftName < rightName ? -1 : leftName > rightName ? 1 : left.name < right.name ? -1 : 1;
}

export class WorkspaceFiles {
  readonly #dependencies: WorkspaceFilesDependencies;
  #authorization: WorkspaceFileBinding | null = null;
  #authorizationGeneration = 0;

  constructor(dependencies: WorkspaceFilesDependencies) {
    this.#dependencies = dependencies;
  }

  async authorize(
    input: DesktopWorkspaceFileAuthorizeInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceFileAuthorization>> {
    const verified = await this.#dependencies.getWorkspaceAgent(input);
    if (!verified.ok) return failure(verified.error.code);
    let generation: number;
    try {
      generation = this.#invalidate();
    } catch (error) {
      return failure(errorCode(error));
    }
    let selected: string | null;
    try {
      selected = await this.#dependencies.chooseDirectory();
    } catch {
      return failure("desktop_workspace_files_unavailable");
    }
    if (this.#authorizationGeneration !== generation) {
      return failure("desktop_workspace_files_generation_conflict");
    }
    if (selected === null) {
      return failure("desktop_workspace_files_picker_cancelled");
    }
    try {
      const root = await validateRoot(
        selected,
        this.#dependencies.homeDirectory ?? os.homedir(),
      );
      if (this.#authorizationGeneration !== generation) {
        return failure("desktop_workspace_files_generation_conflict");
      }
      const reverified = await this.#dependencies.getWorkspaceAgent(input);
      if (!reverified.ok) return failure(reverified.error.code);
      if (this.#authorizationGeneration !== generation) {
        return failure("desktop_workspace_files_generation_conflict");
      }
      this.#authorization = Object.freeze({
        workspaceId: input.workspaceId,
        authorizationGeneration: generation,
        ...root,
      });
      return success({
        workspaceId: input.workspaceId,
        rootName: root.rootName,
        authorizationGeneration: generation,
      });
    } catch (error) {
      return failure(errorCode(error));
    }
  }

  invalidate(): void {
    try {
      this.#invalidate();
    } catch (error) {
      if (errorCode(error) !== "desktop_workspace_files_generation_exhausted") throw error;
    }
  }

  async release(
    input: DesktopWorkspaceFileReleaseInput,
  ): Promise<DesktopOperationResult<{ readonly released: true }>> {
    try {
      this.#requireAuthorization(input);
      this.#invalidate();
      return success({ released: true as const });
    } catch (error) {
      return failure(errorCode(error));
    }
  }

  async list(
    input: DesktopWorkspaceFileListInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceFileList>> {
    try {
      const binding = await this.#requireActiveAuthorization(input);
      const { candidate, parts } = await resolveLogicalPath(
        binding,
        input.directoryPath,
        true,
      );
      const directoryIdentity = await captureDirectoryIdentity(candidate);
      const entries: Array<DesktopWorkspaceFileList["entries"][number]> = [];
      let visited = 0;
      let truncated = false;
      const directory = await opendir(candidate);
      try {
        for await (const entry of directory) {
          visited += 1;
          if (visited > WORKSPACE_FILE_MAX_VISITED_ENTRIES) {
            throw new WorkspaceFilesError("desktop_workspace_files_directory_too_large");
          }
          if (!validateName(entry.name) || isSensitiveName(entry.name)) continue;
          const childPath = path.join(candidate, entry.name);
          const metadata = await safeLstat(childPath);
          if (
            metadata.isSymbolicLink() ||
            (!metadata.isDirectory() && !metadata.isFile()) ||
            (metadata.isFile() && metadata.nlink !== 1n)
          ) {
            continue;
          }
          let childCanonical: string;
          try {
            childCanonical = await realpath(childPath);
          } catch {
            continue;
          }
          if (
            !samePhysicalPath(childPath, childCanonical) ||
            !isContained(binding.root, childCanonical)
          ) {
            continue;
          }
          const logicalPath = [...parts, entry.name].join("/");
          entries.push(
            Object.freeze({
              path: logicalPath,
              name: entry.name,
              kind: metadata.isDirectory() ? ("directory" as const) : ("file" as const),
              sizeBytes: metadata.isFile()
                ? safeNumber(metadata.size, "desktop_workspace_files_file_too_large")
                : null,
              lastModifiedMs: safeNumber(
                metadata.mtimeMs,
                "desktop_workspace_files_unavailable",
              ),
            }),
          );
          if (entries.length > WORKSPACE_FILE_MAX_LIST_ENTRIES) {
            entries.pop();
            truncated = true;
            break;
          }
        }
      } finally {
        await directory.close().catch(() => undefined);
      }
      const observedDirectory = await captureDirectoryIdentity(candidate);
      if (!sameDirectoryIdentity(observedDirectory, directoryIdentity)) {
        throw new WorkspaceFilesError("desktop_workspace_files_identity_drift");
      }
      await verifyRoot(binding);
      this.#requireAuthorization(input);
      return success({
        directoryPath: input.directoryPath,
        entries: Object.freeze(entries.sort(compareEntries)),
        truncated,
      });
    } catch (error) {
      return failure(errorCode(error));
    }
  }

  async read(
    input: DesktopWorkspaceFileReadInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceFileReadResult>> {
    try {
      const binding = await this.#requireActiveAuthorization(input);
      const { candidate } = await resolveLogicalPath(binding, input.path, false);
      const before = await captureFileIdentity(candidate);
      if (before.size > BigInt(WORKSPACE_FILE_MAX_READ_BYTES)) {
        throw new WorkspaceFilesError("desktop_workspace_files_file_too_large");
      }
      const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0);
      const handle = await open(candidate, flags);
      const chunks: Buffer[] = [];
      let total = 0;
      try {
        const openedMetadata = await handle.stat({ bigint: true });
        if (!openedMetadata.isFile() || !sameFileIdentity(identity(openedMetadata), before)) {
          throw new WorkspaceFilesError("desktop_workspace_files_identity_drift");
        }
        while (total <= WORKSPACE_FILE_MAX_READ_BYTES) {
          const remaining = WORKSPACE_FILE_MAX_READ_BYTES + 1 - total;
          const buffer = Buffer.allocUnsafe(Math.min(READ_CHUNK_BYTES, remaining));
          const result = await handle.read(buffer, 0, buffer.length, total);
          if (result.bytesRead === 0) break;
          chunks.push(buffer.subarray(0, result.bytesRead));
          total += result.bytesRead;
          if (total > WORKSPACE_FILE_MAX_READ_BYTES) {
            throw new WorkspaceFilesError("desktop_workspace_files_file_too_large");
          }
        }
        const afterOpened = await handle.stat({ bigint: true });
        if (!sameFileIdentity(identity(afterOpened), before)) {
          throw new WorkspaceFilesError("desktop_workspace_files_identity_drift");
        }
      } finally {
        await handle.close().catch(() => undefined);
      }
      const observed = await captureFileIdentity(candidate);
      if (!sameFileIdentity(observed, before)) {
        throw new WorkspaceFilesError("desktop_workspace_files_identity_drift");
      }
      const bytes = Buffer.concat(chunks, total);
      let content: string;
      try {
        content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        throw new WorkspaceFilesError("desktop_workspace_files_not_utf8");
      }
      await verifyRoot(binding);
      this.#requireAuthorization(input);
      return success({
        path: input.path,
        content,
        sizeBytes: total,
        lastModifiedMs: safeNumber(
          before.modifiedMs,
          "desktop_workspace_files_unavailable",
        ),
        sha256: createHash("sha256").update(bytes).digest("hex"),
      });
    } catch (error) {
      return failure(errorCode(error));
    }
  }

  #invalidate(): number {
    this.#authorization = null;
    if (this.#authorizationGeneration >= Number.MAX_SAFE_INTEGER) {
      throw new WorkspaceFilesError("desktop_workspace_files_generation_exhausted");
    }
    this.#authorizationGeneration += 1;
    return this.#authorizationGeneration;
  }

  async #requireActiveAuthorization(input: {
    readonly workspaceId: string;
    readonly authorizationGeneration: number;
  }): Promise<WorkspaceFileBinding> {
    const authorization = this.#requireAuthorization(input);
    let verified: DesktopOperationResult<{ readonly agent: DesktopParentAgent }>;
    try {
      verified = await this.#dependencies.getWorkspaceAgent({ workspaceId: input.workspaceId });
    } catch {
      this.#requireAuthorization(input);
      this.#invalidate();
      throw new WorkspaceFilesError("desktop_workspace_files_not_authorized");
    }
    this.#requireAuthorization(input);
    if (!verified.ok) {
      this.#invalidate();
      throw new WorkspaceFilesError("desktop_workspace_files_not_authorized");
    }
    return authorization;
  }

  #requireAuthorization(input: {
    readonly workspaceId: string;
    readonly authorizationGeneration: number;
  }): WorkspaceFileBinding {
    const authorization = this.#authorization;
    if (authorization === null) {
      throw new WorkspaceFilesError("desktop_workspace_files_not_authorized");
    }
    if (
      authorization.workspaceId !== input.workspaceId ||
      authorization.authorizationGeneration !== input.authorizationGeneration
    ) {
      throw new WorkspaceFilesError("desktop_workspace_files_generation_conflict");
    }
    return authorization;
  }
}
