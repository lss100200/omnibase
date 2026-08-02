# P34.5D disposable mTLS Gateway Gate

- Result: **PASS**
- Database: `omnibase_test_p345d_20260802085532` (guarded `omnibase_test_*`, tmpfs)
- TLS: CA verified, client certificate required, minimum TLS 1.2
- Identity source: client certificate DER from the Uvicorn/asyncio TLS transport
- Browser headers/cookies cannot create trusted peer evidence
- Business database migration: not performed
- Root `.env`: not read
- Source manifest SHA-256: `fd01d13463b9a5f83d50031873c1ce085fb9f6c4293b6685348f000a4529619f`
- Gateway image: built from `backend/pyproject.toml` + `backend/uv.lock` + checkout source
- Broker client image: contains only the stdlib client; no host/source bind mount

## Read and rejection matrix

- `citation`: `200`
- `client_environment_forbidden_keys`: `[]`
- `client_forbidden_mounts_present`: `[]`
- `credential`: `200`
- `credential_cache_control`: `no-store`
- `credential_expires_at`: `2026-08-02T09:14:46.926137+00:00`
- `credential_parameter_body`: `422`
- `cross-tenant`: `{'credential': 401, 'read': 401}`
- `header_cookie_spoof`: `tls_rejected`
- `lease-revoked`: `{'credential': 401, 'read': 401}`
- `missing_certificate`: `tls_rejected`
- `node-attestation-expired`: `{'credential': 401, 'read': 401}`
- `node-attestation-revoked`: `{'credential': 401, 'read': 401}`
- `node-fencing`: `{'credential': 401, 'read': 401}`
- `rag_search`: `200`
- `registry-revoked`: `{'credential': 401, 'read': 401}`
- `rows`: `200`
- `run-fencing`: `{'credential': 401, 'read': 401}`
- `schema`: `200`
- `tls_below_1_2`: `tls_rejected`
- `workspace-generation`: `{'credential': 401, 'read': 401}`
- `wrong_certificate`: `401`

## Containment

- Physical locator exposed: `False`
- Signing private key exposed: `False`
- Direct database route present: `False`
- Secret scan findings: `[]`
- Cleanup: `{"containers": 0, "networks": 0, "temporary_env_removed": true, "volumes": 0}`

JSON SHA-256: `cc4540cb4f977bbf85103722e8b048493ffbafb37fe97a55493b932a1961599b`
