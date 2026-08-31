import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  loadSourceComponentAttestations,
  readSourceComponentBinaryAsset,
} from "../src/runtime/component-package-registry.ts";
import {
  canonicalJson,
  createSourceComponentRuntimeFixture,
  digestRaw,
} from "./component-package-fixture.ts";

function encoded(value: unknown): Buffer {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

test("source registry admits the exact ten source-owned package versions", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);

  const attestations = await loadSourceComponentAttestations(fixture.bundle);

  assert.equal(attestations.length, 10);
  assert.deepEqual(
    attestations.map((item) => `${item.componentId}@${item.version}`).sort(),
    [...fixture.entries.keys()].sort(),
  );
  for (const attestation of attestations) {
    const expected = fixture.entries.get(
      `${attestation.componentId}@${attestation.version}`,
    );
    assert.ok(expected);
    assert.equal(attestation.adapterId, expected.adapterId);
    assert.equal(attestation.policyManifestSha256, expected.manifestSha256);
    assert.equal(attestation.manifestSha256, expected.manifestSha256);
    assert.equal(attestation.packageSha256, expected.packageSha256);
    assert.equal(attestation.inventorySha256, expected.inventorySha256);
  }
});

test("source registry returns the exact inventory-bound sandbox bytes", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const first = fixture.entries.get("builtin.sandbox-workload@1.0.0");
  const second = fixture.entries.get("builtin.sandbox-workload@1.1.0");
  assert.ok(first);
  assert.ok(second);

  const read = async (entry: typeof first) =>
    await readSourceComponentBinaryAsset({
      runtimeRoot: fixture.root,
      getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
      componentId: entry.componentId,
      version: entry.version,
      manifestSha256: entry.manifestSha256,
      packageSha256: entry.packageSha256,
      payloadName: "workload.wasm",
    });
  const firstAsset = await read(first);
  const secondAsset = await read(second);

  assert.equal(firstAsset.sha256, digestRaw(firstAsset.bytes));
  assert.equal(secondAsset.sha256, digestRaw(secondAsset.bytes));
  assert.notEqual(firstAsset.sha256, secondAsset.sha256);
  assert.deepEqual(
    WebAssembly.Module.imports(new WebAssembly.Module(firstAsset.bytes)),
    [],
  );
  assert.deepEqual(
    WebAssembly.Module.exports(new WebAssembly.Module(firstAsset.bytes)),
    [{ name: "transform", kind: "function" }],
  );
});

test("source registry rejects a sandbox binary changed after inventory review", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const entry = fixture.entries.get("builtin.sandbox-workload@1.0.0");
  assert.ok(entry);
  const runtimePath = `components/builtin-sandbox-workload/1.0.0/payload/workload.wasm`;
  const original = await readFile(
    path.join(fixture.root, ...runtimePath.split("/")),
  );
  await fixture.replaceFile(
    runtimePath,
    Buffer.concat([original, Buffer.from([0])]),
    true,
  );

  await assert.rejects(
    readSourceComponentBinaryAsset({
      runtimeRoot: fixture.root,
      getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
      componentId: entry.componentId,
      version: entry.version,
      manifestSha256: entry.manifestSha256,
      packageSha256: entry.packageSha256,
      payloadName: "workload.wasm",
    }),
    /runtime_component_asset_unverified/u,
  );
});

for (const target of ["manifest", "package", "inventory", "payload"] as const) {
  test(`source registry rejects physical ${target} tampering`, async (t) => {
    const fixture = await createSourceComponentRuntimeFixture();
    t.after(fixture.dispose);
    const entry = fixture.entries.get("builtin.workspace-canvas@1.0.0");
    assert.ok(entry);
    const runtimePath =
      target === "manifest"
        ? `components/${entry.manifestPath}`
        : target === "package"
          ? `components/${entry.packagePath}`
          : target === "inventory"
            ? `components/${entry.inventoryPath}`
            : `components/${entry.componentId.replaceAll(".", "-")}/${entry.version}/${entry.payloadPaths[0]}`;
    const original = await readFile(
      path.join(fixture.root, ...runtimePath.split("/")),
    );
    await fixture.replaceFile(
      runtimePath,
      Buffer.concat([original, Buffer.from(" ")]),
    );

    await assert.rejects(
      loadSourceComponentAttestations(fixture.bundle),
      /runtime_component_file_changed/u,
    );
  });
}

test("source registry rejects an undeclared component tree member", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  await fixture.addDeclaredFile(
    "components/builtin-workspace-canvas/1.0.0/payload/undeclared.json",
    encoded({ injected: true }),
  );

  await assert.rejects(
    loadSourceComponentAttestations(fixture.bundle),
    /runtime_component_tree_not_closed/u,
  );
});

test("source registry rejects a missing member from the exact identity set", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const index = await fixture.readIndex();
  assert.ok(Array.isArray(index.packages));
  index.packages.pop();
  await fixture.replaceFile("components/index.json", encoded(index), true);

  await assert.rejects(
    loadSourceComponentAttestations(fixture.bundle),
    /runtime_component_index_shape_invalid/u,
  );
});

test("source registry rejects duplicate package identities", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const index = await fixture.readIndex();
  assert.ok(Array.isArray(index.packages));
  index.packages[9] = index.packages[0];
  await fixture.replaceFile("components/index.json", encoded(index), true);

  await assert.rejects(
    loadSourceComponentAttestations(fixture.bundle),
    /runtime_component_identity_set_invalid/u,
  );
});

test("source registry rejects attestation identity drift", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const index = await fixture.readIndex();
  assert.ok(Array.isArray(index.packages));
  const first = index.packages[0];
  assert.ok(
    first !== null && typeof first === "object" && !Array.isArray(first),
  );
  first.policy_manifest_sha256 = digestRaw("drifted-policy-manifest");
  await fixture.replaceFile("components/index.json", encoded(index), true);

  await assert.rejects(
    loadSourceComponentAttestations(fixture.bundle),
    /runtime_component_index_entry_invalid/u,
  );
});
