import { createHash } from "node:crypto";

export const DESKTOP_CREDENTIAL_REFERENCE = "electron-safe-storage:v1";

export interface DesktopSafeStorage {
  readonly isEncryptionAvailable: () => boolean;
  readonly encryptString: (plainText: string) => Buffer;
  readonly decryptString: (encrypted: Buffer) => string;
}

export interface EncryptedProviderSecret {
  readonly credentialReference: typeof DESKTOP_CREDENTIAL_REFERENCE;
  readonly encryptedSecretBlob: string;
  readonly secretFingerprint: string;
}

const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;

export function fingerprintProviderSecret(secret: string): string {
  return createHash("sha256").update(secret, "utf8").digest("hex");
}

export function encryptProviderSecret(
  secret: string,
  storage: DesktopSafeStorage,
): EncryptedProviderSecret {
  if (
    typeof secret !== "string" ||
    secret.length < 1 ||
    secret.length > 512 ||
    CONTROL_CHARACTER_PATTERN.test(secret)
  ) {
    throw new Error("desktop_provider_secret_invalid");
  }
  if (!storage.isEncryptionAvailable()) {
    throw new Error("desktop_secret_vault_unavailable");
  }
  const encrypted = storage.encryptString(secret);
  const encryptedSecretBlob = encrypted.toString("base64");
  const encryptedUtf8 = encrypted.toString("utf8");
  if (
    encryptedSecretBlob.length === 0 ||
    encryptedSecretBlob.includes(secret) ||
    encryptedSecretBlob.startsWith("sk-") ||
    encryptedSecretBlob.includes("Bearer ") ||
    encryptedUtf8 === secret ||
    encryptedUtf8.startsWith("sk-") ||
    encryptedUtf8.includes("Bearer ") ||
    secret === encryptedSecretBlob
  ) {
    throw new Error("desktop_secret_vault_unavailable");
  }
  return Object.freeze({
    credentialReference: DESKTOP_CREDENTIAL_REFERENCE,
    encryptedSecretBlob,
    secretFingerprint: fingerprintProviderSecret(secret),
  });
}

export function decryptProviderSecret(
  blob: string,
  storage: DesktopSafeStorage,
): string {
  if (!storage.isEncryptionAvailable()) {
    throw new Error("desktop_secret_vault_unavailable");
  }
  const secret = storage.decryptString(Buffer.from(blob, "base64"));
  if (
    typeof secret !== "string" ||
    secret.length < 1 ||
    secret.length > 512 ||
    CONTROL_CHARACTER_PATTERN.test(secret)
  ) {
    throw new Error("desktop_provider_secret_invalid");
  }
  return secret;
}
