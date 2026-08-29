import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { link, mkdtemp, mkdir, rename, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  WORKSPACE_FILE_MAX_DEPTH,
  WORKSPACE_FILE_MAX_LIST_ENTRIES,
  WORKSPACE_FILE_MAX_LOGICAL_PATH_CHARACTERS,
  WORKSPACE_FILE_MAX_NAME_CHARACTERS,
  WORKSPACE_FILE_MAX_READ_BYTES,
  WORKSPACE_FILE_MAX_VISITED_ENTRIES,
  WorkspaceFiles,
} from "../src/runtime/workspace-files.ts";
import type { DesktopParentAgent } from "../src/shared/ipc-contract.ts";

const WORKSPACE_A = `workspace_${"a".repeat(32)}`;
const WORKSPACE_B = `workspace_${"b".repeat(32)}`;

function agent(workspaceId: string): DesktopParentAgent {
  return {
    id: `agent_${"c".repeat(32)}`,
    workspaceId,
    role: "parent",
    displayName: "Parent",
    createdAt: "2026-08-29T00:00:00Z",
    updatedAt: "2026-08-29T00:00:00Z",
  };
}

async function fixture(): Promise<{
  readonly base: string;
  readonly root: string;
  readonly service: WorkspaceFiles;
  readonly verified: string[];
}> {
  const base = await mkdtemp(path.join(os.tmpdir(), "omnibase-workspace-files-"));
  const root = path.join(base, "project");
  await mkdir(path.join(root, "src"), { recursive: true });
  await writeFile(path.join(root, "src", "main.ts"), "export const value = 1;\n", "utf8");
  await writeFile(path.join(root, ".env"), "SECRET=must-not-leak\n", "utf8");
  const verified: string[] = [];
  return {
    base,
    root,
    verified,
    service: new WorkspaceFiles({
      chooseDirectory: async () => root,
      homeDirectory: path.join(base, "home"),
      getWorkspaceAgent: async ({ workspaceId }) => {
        verified.push(workspaceId);
        return { ok: true, value: { agent: agent(workspaceId) } };
      },
    }),
  };
}

test("Owner-picked root lists and reads bounded logical paths without leaking the host path", async (t) => {
  const { base, root, service, verified } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));

  const authorized = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorized.ok, true);
  if (!authorized.ok) return;
  assert.deepEqual(authorized.value, {
    workspaceId: WORKSPACE_A,
    rootName: "project",
    authorizationGeneration: 1,
  });

  const listed = await service.list({
    workspaceId: WORKSPACE_A,
    authorizationGeneration: 1,
    directoryPath: "",
  });
  assert.equal(listed.ok, true);
  if (!listed.ok) return;
  assert.deepEqual(listed.value.entries.map((entry) => entry.path), ["src"]);
  assert.equal(JSON.stringify(listed).includes(root), false);

  const read = await service.read({
    workspaceId: WORKSPACE_A,
    authorizationGeneration: 1,
    path: "src/main.ts",
  });
  assert.equal(read.ok, true);
  if (!read.ok) return;
  assert.deepEqual(read.value, {
    path: "src/main.ts",
    content: "export const value = 1;\n",
    sizeBytes: 24,
    lastModifiedMs: read.value.lastModifiedMs,
    sha256: createHash("sha256").update("export const value = 1;\n").digest("hex"),
  });
  assert.equal(JSON.stringify(read).includes(root), false);
  assert.deepEqual(verified, [WORKSPACE_A, WORKSPACE_A, WORKSPACE_A, WORKSPACE_A]);
});

test("UTF-8 BOM content remains byte-faithful across the read contract", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const content = '\ufeffexport const value = "bom";\n';
  const bytes = Buffer.from(content, "utf8");
  await writeFile(path.join(root, "src", "bom.ts"), bytes);

  const authorized = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorized.ok, true);
  if (!authorized.ok) return;
  const read = await service.read({
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorized.value.authorizationGeneration,
    path: "src/bom.ts",
  });

  assert.equal(read.ok, true);
  if (!read.ok) return;
  assert.equal(read.value.content, content);
  assert.equal(Buffer.byteLength(read.value.content, "utf8"), read.value.sizeBytes);
  assert.equal(read.value.sizeBytes, bytes.byteLength);
  assert.equal(read.value.sha256, createHash("sha256").update(bytes).digest("hex"));
});

test("authorization replacement and release invalidate stale generations", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));

  const first = await service.authorize({ workspaceId: WORKSPACE_A });
  const second = await service.authorize({ workspaceId: WORKSPACE_B });
  assert.equal(first.ok && second.ok, true);
  if (!first.ok || !second.ok) return;
  assert.ok(second.value.authorizationGeneration > first.value.authorizationGeneration);

  assert.deepEqual(
    await service.list({
      workspaceId: WORKSPACE_A,
      authorizationGeneration: first.value.authorizationGeneration,
      directoryPath: "",
    }),
    { ok: false, error: { code: "desktop_workspace_files_generation_conflict" } },
  );
  assert.deepEqual(
    await service.release({
      workspaceId: WORKSPACE_B,
      authorizationGeneration: second.value.authorizationGeneration,
    }),
    { ok: true, value: { released: true } },
  );
  assert.deepEqual(
    await service.list({
      workspaceId: WORKSPACE_B,
      authorizationGeneration: second.value.authorizationGeneration,
      directoryPath: "",
    }),
    { ok: false, error: { code: "desktop_workspace_files_not_authorized" } },
  );
  assert.equal(root.length > 0, true);
});

test("main lifecycle invalidation clears authorization without a renderer IPC", async (t) => {
  const { base, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  service.invalidate();
  assert.deepEqual(
    await service.list({
      workspaceId: WORKSPACE_A,
      authorizationGeneration: authorization.value.authorizationGeneration,
      directoryPath: "",
    }),
    { ok: false, error: { code: "desktop_workspace_files_not_authorized" } },
  );
});

test("unsafe root, unsafe logical paths, secrets, binary and oversized files fail closed", async (t) => {
  const { base, root } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));

  const homeService = new WorkspaceFiles({
    chooseDirectory: async () => root,
    homeDirectory: root,
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: { agent: agent(workspaceId) },
    }),
  });
  assert.deepEqual(await homeService.authorize({ workspaceId: WORKSPACE_A }), {
    ok: false,
    error: { code: "desktop_workspace_files_root_unsafe" },
  });

  const homeAncestorService = new WorkspaceFiles({
    chooseDirectory: async () => base,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: { agent: agent(workspaceId) },
    }),
  });
  assert.deepEqual(await homeAncestorService.authorize({ workspaceId: WORKSPACE_A }), {
    ok: false,
    error: { code: "desktop_workspace_files_root_unsafe" },
  });

  const sensitiveRoot = path.join(base, ".ssh", "project");
  await mkdir(sensitiveRoot, { recursive: true });
  const sensitiveAncestorService = new WorkspaceFiles({
    chooseDirectory: async () => sensitiveRoot,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: { agent: agent(workspaceId) },
    }),
  });
  assert.deepEqual(await sensitiveAncestorService.authorize({ workspaceId: WORKSPACE_A }), {
    ok: false,
    error: { code: "desktop_workspace_files_root_unsafe" },
  });

  const adsRootService = new WorkspaceFiles({
    chooseDirectory: async () => path.join(base, "parent:stream", "project"),
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: { agent: agent(workspaceId) },
    }),
  });
  assert.deepEqual(await adsRootService.authorize({ workspaceId: WORKSPACE_A }), {
    ok: false,
    error: { code: "desktop_workspace_files_root_unsafe" },
  });

  await writeFile(path.join(root, "binary.bin"), Buffer.from([0xff, 0xfe, 0x00]));
  await writeFile(
    path.join(root, "large.txt"),
    Buffer.alloc(WORKSPACE_FILE_MAX_READ_BYTES + 1, 0x61),
  );
  const service = new WorkspaceFiles({
    chooseDirectory: async () => root,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: { agent: agent(workspaceId) },
    }),
  });
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const common = {
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
  };
  for (const unsafe of ["../outside.txt", "/absolute.txt", "C:/drive.txt", "a\\b.txt", "a:b"]) {
    assert.deepEqual(await service.read({ ...common, path: unsafe }), {
      ok: false,
      error: { code: "desktop_workspace_files_path_invalid" },
    });
  }
  assert.deepEqual(await service.read({ ...common, path: ".env" }), {
    ok: false,
    error: { code: "desktop_workspace_files_sensitive_forbidden" },
  });
  assert.deepEqual(await service.read({ ...common, path: "binary.bin" }), {
    ok: false,
    error: { code: "desktop_workspace_files_not_utf8" },
  });
  assert.deepEqual(await service.read({ ...common, path: "large.txt" }), {
    ok: false,
    error: { code: "desktop_workspace_files_file_too_large" },
  });
});

test("logical path budgets and Windows name attacks fail closed", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const maxDepthParts = Array.from(
    { length: WORKSPACE_FILE_MAX_DEPTH },
    (_, index) => `d${index}`,
  );
  await mkdir(path.join(root, ...maxDepthParts), { recursive: true });
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const common = {
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
  };
  const maxDepthPath = maxDepthParts.join("/");
  const maxDepthListing = await service.list({
    ...common,
    directoryPath: maxDepthPath,
  });
  assert.equal(maxDepthListing.ok, true);

  const attacks = [
    "CON",
    "con.txt",
    "LPT1.log",
    "trailing.",
    "trailing ",
    " leading",
    "safe%2eescape",
    "safe%2Fescape",
    "safe%3aescape",
    "safe%5Cescape",
    "safe\u202etxt",
    "safe\u2066txt",
    "\uff43\uff4f\uff4e.txt",
    "a".repeat(WORKSPACE_FILE_MAX_NAME_CHARACTERS + 1),
    Array.from({ length: WORKSPACE_FILE_MAX_DEPTH + 1 }, () => "d").join("/"),
    "a".repeat(WORKSPACE_FILE_MAX_LOGICAL_PATH_CHARACTERS + 1),
  ];
  for (const attack of attacks) {
    assert.deepEqual(await service.read({ ...common, path: attack }), {
      ok: false,
      error: { code: "desktop_workspace_files_path_invalid" },
    });
  }
});

test("authorization revalidates the Workspace after the directory picker returns", async (t) => {
  const { base, root } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  let verificationCount = 0;
  const service = new WorkspaceFiles({
    chooseDirectory: async () => root,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => {
      verificationCount += 1;
      if (verificationCount === 1) {
        return { ok: true, value: { agent: agent(workspaceId) } };
      }
      return { ok: false, error: { code: "desktop_workspace_inactive" } };
    },
  });

  assert.deepEqual(await service.authorize({ workspaceId: WORKSPACE_A }), {
    ok: false,
    error: { code: "desktop_workspace_inactive" },
  });
  assert.equal(verificationCount, 2);
});

test("Workspace loss during list or read clears the matching authorization", async (t) => {
  const { base, root } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));

  for (const operation of ["list", "read"] as const) {
    let verificationCount = 0;
    const service = new WorkspaceFiles({
      chooseDirectory: async () => root,
      homeDirectory: path.join(base, "home"),
      getWorkspaceAgent: async ({ workspaceId }) => {
        verificationCount += 1;
        return verificationCount <= 2
          ? { ok: true, value: { agent: agent(workspaceId) } }
          : { ok: false, error: { code: "desktop_workspace_inactive" } };
      },
    });
    const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
    assert.equal(authorization.ok, true);
    if (!authorization.ok) continue;
    const common = {
      workspaceId: WORKSPACE_A,
      authorizationGeneration: authorization.value.authorizationGeneration,
    };
    const lost =
      operation === "list"
        ? await service.list({ ...common, directoryPath: "" })
        : await service.read({ ...common, path: "src/main.ts" });
    assert.deepEqual(lost, {
      ok: false,
      error: { code: "desktop_workspace_files_not_authorized" },
    });
    assert.deepEqual(await service.list({ ...common, directoryPath: "" }), {
      ok: false,
      error: { code: "desktop_workspace_files_not_authorized" },
    });
    assert.equal(verificationCount, 3);
  }
});

test("late Workspace loss from a stale request cannot clear a replacement authorization", async (t) => {
  const { base, root } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const replacementRoot = path.join(base, "replacement-project");
  await mkdir(replacementRoot);
  let selectedRoot = root;
  let delayWorkspaceA = false;
  let markVerificationStarted!: () => void;
  let releaseVerification!: () => void;
  const verificationStarted = new Promise<void>((resolve) => {
    markVerificationStarted = resolve;
  });
  const verificationRelease = new Promise<void>((resolve) => {
    releaseVerification = resolve;
  });
  const service = new WorkspaceFiles({
    chooseDirectory: async () => selectedRoot,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => {
      if (workspaceId === WORKSPACE_A && delayWorkspaceA) {
        markVerificationStarted();
        await verificationRelease;
        return { ok: false, error: { code: "desktop_workspace_inactive" } };
      }
      return { ok: true, value: { agent: agent(workspaceId) } };
    },
  });
  const first = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(first.ok, true);
  if (!first.ok) return;

  delayWorkspaceA = true;
  const staleList = service.list({
    workspaceId: WORKSPACE_A,
    authorizationGeneration: first.value.authorizationGeneration,
    directoryPath: "",
  });
  await verificationStarted;
  selectedRoot = replacementRoot;
  const replacement = await service.authorize({ workspaceId: WORKSPACE_B });
  assert.equal(replacement.ok, true);
  if (!replacement.ok) return;
  releaseVerification();

  assert.deepEqual(await staleList, {
    ok: false,
    error: { code: "desktop_workspace_files_generation_conflict" },
  });
  const replacementList = await service.list({
    workspaceId: WORKSPACE_B,
    authorizationGeneration: replacement.value.authorizationGeneration,
    directoryPath: "",
  });
  assert.equal(replacementList.ok, true);
});

test("links are omitted from listing and rejected before content access", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const outside = path.join(base, "outside.txt");
  const linked = path.join(root, "linked.txt");
  await writeFile(outside, "outside\n", "utf8");
  try {
    await symlink(outside, linked, "file");
  } catch {
    t.skip("file links are unavailable on this host");
    return;
  }
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const common = {
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
  };
  const listing = await service.list({ ...common, directoryPath: "" });
  assert.equal(listing.ok, true);
  if (!listing.ok) return;
  assert.equal(listing.value.entries.some((entry) => entry.name === "linked.txt"), false);
  assert.deepEqual(await service.read({ ...common, path: "linked.txt" }), {
    ok: false,
    error: { code: "desktop_workspace_files_link_forbidden" },
  });
});

test("directory links and Windows junctions are omitted and rejected", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const outside = path.join(base, "outside-directory");
  const linked = path.join(root, "linked-directory");
  await mkdir(outside);
  await writeFile(path.join(outside, "outside.txt"), "outside\n", "utf8");
  try {
    await symlink(outside, linked, process.platform === "win32" ? "junction" : "dir");
  } catch {
    t.skip("directory links are unavailable on this host");
    return;
  }
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const common = {
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
  };
  const listing = await service.list({ ...common, directoryPath: "" });
  assert.equal(listing.ok, true);
  if (!listing.ok) return;
  assert.equal(listing.value.entries.some((entry) => entry.name === "linked-directory"), false);
  assert.deepEqual(await service.list({ ...common, directoryPath: "linked-directory" }), {
    ok: false,
    error: { code: "desktop_workspace_files_link_forbidden" },
  });
});

test("hard-linked files are omitted and rejected before content access", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const outside = path.join(base, "hardlink-source.txt");
  const linked = path.join(root, "hardlinked.txt");
  await writeFile(outside, "outside\n", "utf8");
  try {
    await link(outside, linked);
  } catch {
    t.skip("hard links are unavailable on this host");
    return;
  }
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const common = {
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
  };
  const listing = await service.list({ ...common, directoryPath: "" });
  assert.equal(listing.ok, true);
  if (!listing.ok) return;
  assert.equal(listing.value.entries.some((entry) => entry.name === "hardlinked.txt"), false);
  assert.deepEqual(await service.read({ ...common, path: "hardlinked.txt" }), {
    ok: false,
    error: { code: "desktop_workspace_files_link_forbidden" },
  });
});

test("listing is deterministically capped and reports truncation", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  await Promise.all(
    Array.from({ length: WORKSPACE_FILE_MAX_LIST_ENTRIES + 1 }, (_, index) =>
      writeFile(path.join(root, `item-${String(index).padStart(3, "0")}.txt`), "x", "utf8"),
    ),
  );
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  const listing = await service.list({
    workspaceId: WORKSPACE_A,
    authorizationGeneration: authorization.value.authorizationGeneration,
    directoryPath: "",
  });
  assert.equal(listing.ok, true);
  if (!listing.ok) return;
  assert.equal(listing.value.entries.length, WORKSPACE_FILE_MAX_LIST_ENTRIES);
  assert.equal(listing.value.truncated, true);
});

test("listing stops after the visited-entry budget even when entries are filtered", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const names = Array.from(
    { length: WORKSPACE_FILE_MAX_VISITED_ENTRIES + 1 },
    (_, index) => `filtered-${String(index).padStart(4, "0")}.key`,
  );
  const batchSize = 128;
  for (let offset = 0; offset < names.length; offset += batchSize) {
    await Promise.all(
      names
        .slice(offset, offset + batchSize)
        .map((name) => writeFile(path.join(root, name), "filtered", "utf8")),
    );
  }
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;
  assert.deepEqual(
    await service.list({
      workspaceId: WORKSPACE_A,
      authorizationGeneration: authorization.value.authorizationGeneration,
      directoryPath: "",
    }),
    { ok: false, error: { code: "desktop_workspace_files_directory_too_large" } },
  );
});

test("replacement of an authorized root is detected before enumeration", async (t) => {
  const { base, root, service } = await fixture();
  t.after(() => rm(base, { recursive: true, force: true }));
  const authorization = await service.authorize({ workspaceId: WORKSPACE_A });
  assert.equal(authorization.ok, true);
  if (!authorization.ok) return;

  await rename(root, `${root}-old`);
  await mkdir(root);
  assert.deepEqual(
    await service.list({
      workspaceId: WORKSPACE_A,
      authorizationGeneration: authorization.value.authorizationGeneration,
      directoryPath: "",
    }),
    { ok: false, error: { code: "desktop_workspace_files_identity_drift" } },
  );
});
