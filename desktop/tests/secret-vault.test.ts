import assert from "node:assert/strict";
import test from "node:test";

import {
  decryptProviderSecret,
  encryptProviderSecret,
  fingerprintProviderSecret,
} from "../src/runtime/secret-vault.ts";

const ISOLATION_SECRET = "isolation-provider-secret-not-for-git";

function memoryVault(available = true) {
  const store = new Map<string, string>();
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plainText: string) => {
      const token = Buffer.from(`dpapi:${plainText}`, "utf8");
      store.set(token.toString("base64"), plainText);
      return token;
    },
    decryptString: (encrypted: Buffer) => {
      const restored = store.get(encrypted.toString("base64"));
      if (restored === undefined) throw new Error("vault_miss");
      return restored;
    },
  };
}

test("provider secrets are stored as opaque blobs, never plaintext", () => {
  const vault = memoryVault();
  const encrypted = encryptProviderSecret(ISOLATION_SECRET, vault);
  assert.equal(encrypted.credentialReference, "electron-safe-storage:v1");
  assert.equal(
    encrypted.secretFingerprint,
    fingerprintProviderSecret(ISOLATION_SECRET),
  );
  assert.equal(encrypted.encryptedSecretBlob.includes(ISOLATION_SECRET), false);
  assert.equal(encrypted.encryptedSecretBlob.startsWith("sk-"), false);
  assert.equal(decryptProviderSecret(encrypted.encryptedSecretBlob, vault), ISOLATION_SECRET);
});

test("plaintext-looking ciphertext is rejected", () => {
  const vault = {
    isEncryptionAvailable: () => true,
    encryptString: () => Buffer.from("sk-plaintext-must-fail", "utf8"),
    decryptString: () => ISOLATION_SECRET,
  };
  assert.throws(
    () => encryptProviderSecret(ISOLATION_SECRET, vault),
    /desktop_secret_vault_unavailable/u,
  );
});

test("unavailable encryption fails closed", () => {
  assert.throws(
    () => encryptProviderSecret(ISOLATION_SECRET, memoryVault(false)),
    /desktop_secret_vault_unavailable/u,
  );
});
