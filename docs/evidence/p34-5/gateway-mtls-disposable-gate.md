# P34.5D disposable mTLS Gateway Gate

- Result: **PASS**
- Database: `omnibase_test_p345d_20260801211802` (guarded `omnibase_test_*`, tmpfs)
- TLS: CA verified, client certificate required, minimum TLS 1.2
- Identity source: client certificate DER from the Uvicorn/asyncio TLS transport
- Browser headers/cookies cannot create trusted peer evidence
- Business database migration: not performed
- Root `.env`: not read

## Read and rejection matrix

- `citation`: `200`
- `client_environment_forbidden_keys`: `[]`
- `client_forbidden_mounts_present`: `[]`
- `credential`: `200`
- `credential_cache_control`: `no-store`
- `credential_expires_at`: `2026-08-01T21:22:17.801693+00:00`
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

JSON SHA-256: `fb44fc8380b8c753232190348594ab420535d2abd6e2b6af34f9c24428054ade`
