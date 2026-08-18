// The release builder must replace this exact token with the SHA-256 of the
// packaged runtime-manifest.json before compiling the signed desktop binary.
// Keeping the placeholder makes an unpackaged development build fail closed.
export const PINNED_RUNTIME_MANIFEST_SHA256 =
  "__OMNIBASE_RUNTIME_MANIFEST_SHA256__";
