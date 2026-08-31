import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, cp, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { packageLinux } from "../scripts/package-linux.mjs";

const ELECTRON_ZIP_BYTES = "verified linux electron archive";
const ELECTRON_ZIP_SHA256 = createHash("sha256")
  .update(ELECTRON_ZIP_BYTES)
  .digest("hex");

async function write(file, value) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, value);
}

async function fixture(label) {
  const root = await mkdtemp(
    path.join(os.tmpdir(), `omnibase-linux-packager-${label}-`),
  );
  const appDir = path.join(root, "app");
  const electronZipDir = path.join(root, "electron");
  const runtimeDir = path.join(root, "runtime");
  const outputDir = path.join(root, "output");
  await write(path.join(appDir, "dist", "main.js"), "main");
  await write(
    path.join(appDir, "package.json"),
    '{"name":"@omnibase/desktop"}\n',
  );
  await write(
    path.join(runtimeDir, "runtime-manifest.json"),
    '{"schemaVersion":1}\n',
  );
  await write(path.join(runtimeDir, "runtime-host.mjs"), "host");
  await write(
    path.join(electronZipDir, "electron-v43.4.0-linux-x64.zip"),
    ELECTRON_ZIP_BYTES,
  );
  await mkdir(outputDir);
  return {
    appDir,
    electronZipDir,
    outputDir,
    root,
    runtimeDir,
    argv: [
      "--app-dir",
      appDir,
      "--electron-zip-dir",
      electronZipDir,
      "--electron-zip-sha256",
      ELECTRON_ZIP_SHA256,
      "--runtime-dir",
      runtimeDir,
      "--output-dir",
      outputDir,
      "--version",
      "1.0.0",
    ],
  };
}

async function writePackagedOutput(options) {
  const target = path.join(options.out, "OmniBase-linux-x64");
  await write(path.join(target, "OmniBase"), "electron");
  await chmod(path.join(target, "OmniBase"), 0o755);
  await write(path.join(target, "resources", "app.asar"), "asar");
  await cp(
    options.extraResource[0],
    path.join(target, "resources", "runtime"),
    {
      recursive: true,
      errorOnExist: true,
      force: false,
    },
  );
  return [target];
}

async function writePackagedOutputWithRuntimeModeDrift(options) {
  const paths = await writePackagedOutput(options);
  await chmod(
    path.join(paths[0], "resources", "runtime", "runtime-host.mjs"),
    0o600,
  );
  return paths;
}

test("Linux packager targets linux-x64 and preserves the verified runtime tree", async () => {
  const current = await fixture("valid");
  let seenOptions;
  const target = await packageLinux(current.argv, {
    packager: async (options) => {
      seenOptions = options;
      return writePackagedOutput(options);
    },
  });
  assert.equal(target, path.join(current.outputDir, "OmniBase-linux-x64"));
  assert.equal(seenOptions.platform, "linux");
  assert.equal(seenOptions.arch, "x64");
  assert.equal(seenOptions.asar, true);
  assert.equal(seenOptions.prune, false);
  assert.equal(seenOptions.derefSymlinks, false);
});

test("Linux packager requires an exact Electron archive digest", async () => {
  const current = await fixture("digest");
  await assert.rejects(
    () =>
      packageLinux(
        current.argv.map((value) =>
          value === ELECTRON_ZIP_SHA256 ? "0".repeat(64) : value,
        ),
      ),
    /desktop_linux_packager_electron_zip_digest_mismatch/u,
  );
});

test("Linux packager rejects runtime mode drift in the packaged AppDir", async (t) => {
  if (process.platform !== "linux") {
    t.skip("POSIX mode verification requires Linux");
    return;
  }
  const current = await fixture("mode-drift");
  await chmod(path.join(current.runtimeDir, "runtime-host.mjs"), 0o700);
  await assert.rejects(
    () =>
      packageLinux(current.argv, {
        packager: writePackagedOutputWithRuntimeModeDrift,
      }),
    /desktop_linux_packager_output_runtime_mismatch/u,
  );
});

test("Linux packager rejects a pre-existing target without overwriting it", async () => {
  const current = await fixture("existing");
  await mkdir(path.join(current.outputDir, "OmniBase-linux-x64"));
  await assert.rejects(
    () => packageLinux(current.argv, { packager: async () => [] }),
    /desktop_linux_packager_target_exists/u,
  );
});
