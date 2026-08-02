# P34.6 final clean-checkout evidence

> Scope: P34.6 Foundation / Contracts / Fail-closed primitives. These disposable engineering Gates do not prove production provider activation, hostile-code isolation, non-disposable tenant/RAG safety, real member Overlay behavior, capacity/SLA, or the P34.7 production total Gate.

## Source provenance

- Branch: `codex/p34-6-private-data`
- Source commit: `cc48baa9bbd78d8824393311220ba523dfb186de`
- Source tree: `fd6e2b3ef0e390a9879c5cb4fa1b845ff1a42d62`
- Ordinary clone: `C:\tmp\omnibase-p346-final-cc48baa`
- Git state: clean; `core.autocrlf=true`; no initial `__pycache__`
- Checkout contract: non-empty `*.py` and all `*.sh` are LF; `*.ps1` is CRLF. Four zero-byte `__init__.py` files correctly have no line-ending bytes.

## Overlay source-built Gate

- Wrapper tests: `3 passed`
- ValidateOnly report SHA-256: `b92e7ec8805b7417e0189c824f82fe5db605403585dd820e01feff133313cd13`
- Formal report SHA-256: `246f1d9b9a8bddcf9517cc7d0361ec6699660faf7a17785cecf24549216c3f38`
- Source manifest SHA-256: `d0d1f54c08629f7d6158d143f1db928197648403e36b3598e01be54e9a8d8740`
- Source tree SHA-256: `8cce097c80959061cef3f3751979ca99eeea723b9942cc29a68e2dedde02470f`
- Result: lifecycle, offline, reconnect, Headscale, provider mutation, containment, configuration seal, and cleanup Gates passed.
- Cleanup: containers/networks/disposable volumes `0/0/0`.
- Root `.env` accessed by the Gate: `false`.
- Business database accessed: `false`.

## Gateway source-built Gate

- Wrapper tests: `5 passed`
- ValidateOnly report SHA-256: `df6026ca13cc0189c5d960c025e24125f635fa538014a32a1fe15c06cd35895d`
- Formal evidence JSON SHA-256: `ee179a3abfc66219da0aff866737bd256db3fec9ec37e4209239be910a589c62`
- Formal evidence Markdown SHA-256: `646f2bf63d0bf251d708e78e3f088678f1256307309778ee7821343434648f3c`
- Source manifest SHA-256: `cd30967c9337487777baa1634bac3946c0085132ee0bdf2252c03306853b50be`
- Disposable database: guarded `omnibase_test_p345d_20260802111018`, sentinel verified, tmpfs.
- Allowed reads: credential, schema, rows, RAG search, and citation returned `200`.
- Rejections: cross-tenant, revoked/expired attestation, Workspace generation, Run/Node fencing, Lease/registry revoke, wrong/missing certificate, header/cookie spoof, and TLS below 1.2 were rejected as specified.
- Direct database route, physical locator exposure, and private-key exposure: `false`.
- Secret scan findings: none.
- Cleanup: containers/networks/volumes `0/0/0`; temporary env removed.
- Root `.env` accessed: `false`; business database migrated: `false`; real credentials used: `false`.

## P34.6 regression and contract evidence

- P34.6 plus affected Capability/Gateway focused baseline: `127 passed`.
- Final clean-clone related unit set: `164 passed`.
- Backend non-integration baseline after P34.5 hardening rebase: `1121 passed / 14 skipped / 14 deselected`.
- Mypy: `148 source files / 0 issues`.
- OpenAPI/SDK contract: `4 passed`.
- Changed Backend Python Ruff: `50 files`, check passed, format check reports `50 files already formatted`.
- SDK OpenAPI test Ruff: passed.
- Maintainer map: `24 invariants / 19 modules / 232 path specs / 515 matched files / 128 entrypoints / 14 discovered HTTP entrypoints / 76 verification commands` in the clean clone.
- Maintainer benchmark: `3 plans / 8 scenarios / 6 critical scenarios / 9 unsafe vetoes`.
- Compose config used explicit `--env-file .env.example`; `git diff --check` passed.
- Fresh guarded PostgreSQL baseline: empty downgrade/re-upgrade `1 passed`; remaining integration `70 passed / 1 skipped / 1 deselected`; targeted real PostgreSQL `2 passed`.

## Host recovery incident

Repeated source builds expanded Docker Desktop's WSL data VHDX until C: had only `2.52 GiB` free. Docker's Linux backend then became unavailable; those interrupted runs produced no scored evidence and are not counted as repository Gate failures or passes.

- Hugging Face and ModelScope caches were moved intact to `E:\ModelCaches\Administrator\.cache` and junctioned back to their original paths.
- Ollama models and project/business data were not moved or deleted.
- Unused Docker build cache and images were pruned; named volumes were preserved.
- `docker_data.vhdx` was compacted from `225.04 GiB` to `35.72 GiB`.
- C: free space recovered from `2.52 GiB` to `203.29 GiB` before the final rerun.
- Docker restarted successfully; all ten named volumes remained present.
- The final scored Gates above ran only after recovery and are bound to `cc48baa...`.

## Explicitly not proven

- Current-source Hyper-V A4 12/12 target-host Gate: `pending/not_proven`.
- Production Core↔Runner/Broker/Gateway composition.
- Production WorkspaceDataAdapter and provider-backed object copy/restore.
- Promotion or Restore `EffectOutcome.COMMITTED` and `controlled_shared` success visibility.
- Production snapshot barrier and restore rehearsal.
- Non-disposable tenant/RAG, real member Overlay/DERP/node-compromise, capacity/SLA, and the P34.7 production total Gate.
