import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { loadSourceComponentAttestations } from "../src/runtime/component-package-registry.ts";
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
