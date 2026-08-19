import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, open, rmdir, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  isSafeRuntimeRelativePath,
  verifyRuntimeBundle,
} from "../src/runtime/manifest.ts";

function sha256(value: Buffer | string): string {
  return createHash("sha256").update(value).digest("hex");
}

async function makeBundle(): Promise<{
  root: string;
  manifestPath: string;
  manifestDigest: string;
  runtimePath: string;
  dispose: () => Promise<void>;
}> {
  const root = path.join(
    tmpdir(),
    `omnibase-desktop-manifest-${process.pid}-${Date.now()}-${Math.random()
      .toString(16)
      .slice(2)}`,
  );
  const bin = path.join(root, "bin");
  await mkdir(root, { recursive: false });
  await mkdir(bin, { recursive: false });
  const runtimePath = path.join(bin, "omnibase-runtime.exe");
  const payload = Buffer.from("deterministic-runtime-payload", "utf8");
  await writeFile(runtimePath, payload, { flag: "wx" });
  const configPath = path.join(root, "runtime-host.json");
  const config = Buffer.from('{"startup_timeout_seconds":60}\n', "utf8");
  await writeFile(configPath, config, { flag: "wx" });
  const manifest = JSON.stringify({
    schemaVersion: 1,
    entrypoint: { path: "bin/omnibase-runtime.exe", args: ["serve"] },
    files: [
      {
        path: "bin/omnibase-runtime.exe",
        size: payload.byteLength,
        sha256: sha256(payload),
      },
      {
        path: "runtime-host.json",
        size: config.byteLength,
        sha256: sha256(config),
      },
    ],
  });
  const manifestPath = path.join(root, "runtime-manifest.json");
  await writeFile(manifestPath, manifest, { flag: "wx" });
  return {
    root,
    manifestPath,
    manifestDigest: sha256(manifest),
    runtimePath,
    dispose: async () => {
      await unlink(runtimePath);
      await unlink(configPath);
      await unlink(manifestPath);
      await rmdir(bin);
      await rmdir(root);
    },
  };
}

test("runtime paths form a strict portable relative-path language", () => {
  assert.equal(isSafeRuntimeRelativePath("bin/omnibase-runtime.exe"), true);
  for (const invalid of [
    "../runtime.exe",
    "bin/../runtime.exe",
    "C:/runtime.exe",
    "//server/share/runtime.exe",
    "bin\\runtime.exe",
    "bin/runtime.exe:stream",
    "/runtime.exe",
    "bin//runtime.exe",
  ]) {
    assert.equal(isSafeRuntimeRelativePath(invalid), false, invalid);
  }
});

test("runtime manifest and every declared payload require matching SHA-256", async () => {
  const bundle = await makeBundle();
  try {
    const verified = await verifyRuntimeBundle({
      bundleRoot: bundle.root,
      manifestPath: bundle.manifestPath,
      expectedManifestSha256: bundle.manifestDigest,
    });
    assert.equal(verified.command, bundle.runtimePath);
    assert.deepEqual(verified.args, ["serve"]);
    assert.equal(verified.startupTimeoutMs, 65_000);

    const handle = await open(bundle.runtimePath, "a");
    await handle.write("tamper");
    await handle.close();
    await assert.rejects(
      verifyRuntimeBundle({
        bundleRoot: bundle.root,
        manifestPath: bundle.manifestPath,
        expectedManifestSha256: bundle.manifestDigest,
      }),
      /runtime_file_digest_mismatch/u,
    );
  } finally {
    await bundle.dispose();
  }
});

test("an unpinned or tampered manifest fails before runtime execution", async () => {
  const bundle = await makeBundle();
  try {
    await assert.rejects(
      verifyRuntimeBundle({
        bundleRoot: bundle.root,
        manifestPath: bundle.manifestPath,
        expectedManifestSha256: "__OMNIBASE_RUNTIME_MANIFEST_SHA256__",
      }),
      /runtime_manifest_trust_not_configured/u,
    );
    await assert.rejects(
      verifyRuntimeBundle({
        bundleRoot: bundle.root,
        manifestPath: bundle.manifestPath,
        expectedManifestSha256: "0".repeat(64),
      }),
      /runtime_manifest_digest_mismatch/u,
    );
  } finally {
    await bundle.dispose();
  }
});

test("undeclared files and directories make the runtime tree fail closed", async () => {
  const bundle = await makeBundle();
  const undeclared = path.join(bundle.root, "unlisted.dll");
  const emptyDirectory = path.join(bundle.root, "empty");
  try {
    await writeFile(undeclared, "sideload candidate", { flag: "wx" });
    await assert.rejects(
      verifyRuntimeBundle({
        bundleRoot: bundle.root,
        manifestPath: bundle.manifestPath,
        expectedManifestSha256: bundle.manifestDigest,
      }),
      /runtime_tree_not_closed/u,
    );
    await unlink(undeclared);

    await mkdir(emptyDirectory, { recursive: false });
    await assert.rejects(
      verifyRuntimeBundle({
        bundleRoot: bundle.root,
        manifestPath: bundle.manifestPath,
        expectedManifestSha256: bundle.manifestDigest,
      }),
      /runtime_tree_not_closed/u,
    );
    await rmdir(emptyDirectory);
  } finally {
    try {
      await unlink(undeclared);
    } catch {}
    try {
      await rmdir(emptyDirectory);
    } catch {}
    await bundle.dispose();
  }
});
