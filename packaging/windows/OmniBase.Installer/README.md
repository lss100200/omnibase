# OmniBase Windows Installer

This directory contains the P6.5 Windows installer authoring skeleton. It is
deliberately separate from the frozen `OmniBase.Setup` Companion.

## Fixed product contract

- Distribution bundle: one WiX Burn EXE containing one MSI.
- MSI scope: current user only; no UAC or machine-wide fallback.
- Install directory: `%LOCALAPPDATA%\Programs\OmniBase`.
- User data directory: `%LOCALAPPDATA%\OmniBase`.
- Normal uninstall removes installer-owned application files and shortcuts but
  does not author, remove, or traverse the user data directory.
- Major upgrades are transactional and lower-version installs are rejected.
- Docker, WSL, PostgreSQL/pgvector, BGE-M3, and hardened sandbox packages are
  not prerequisites or chain packages.

The MSI only consumes an already-built desktop payload. It does not build the
backend, Next standalone server, RuntimeHost, Electron application, or models.
`tools/validate_payload.py` rejects links/reparse points, local databases,
secret-bearing file types, virtual disks, unsafe Windows names, and payloads
that do not contain `OmniBase.exe`.

## Build

WiX Toolset 7 and the Bal extension are restored through the project SDK and
NuGet package references. A .NET SDK and Python 3.11+ are required on the build
machine.

```powershell
dotnet build .\bundle\OmniBase.Bundle.wixproj -c Release `
  -p:PayloadRoot='C:\absolute\verified\omnibase-payload' `
  -p:ProductVersion=1.0.0
```

The payload path must be absolute and must refer to a closed, already-verified
Electron application directory. The bundle project builds the MSI through its
project reference and embeds it in the resulting EXE.

## Verification

Fast contract and payload-validator tests do not install anything:

```powershell
python -m pytest .\tests\test_installer_contract.py `
  .\tests\test_validate_payload.py -q
```

`tests\Test-InstallerLifecycle.ps1` is the mutating acceptance harness for a
dedicated disposable Windows account or clean VM. It covers:

1. clean per-user installation;
2. transactional major upgrade with retained user data;
3. blocked downgrade;
4. a deliberately failing two-MSI transaction that must roll back to the
   previously installed version; and
5. normal uninstall with retained user data.

The rollback probe is test-only authoring under `tests\fixtures\RollbackProbe`;
it is not referenced by the production bundle.

Build two normal bundles from independently staged payloads, then build the
rollback probe at a higher version:

```powershell
dotnet build .\bundle\OmniBase.Bundle.wixproj -c Release `
  -p:PayloadRoot='C:\payloads\omnibase-1.0.0' -p:ProductVersion=1.0.0
dotnet build .\bundle\OmniBase.Bundle.wixproj -c Release `
  -p:PayloadRoot='C:\payloads\omnibase-1.0.1' -p:ProductVersion=1.0.1
dotnet build .\tests\fixtures\RollbackProbe\OmniBase.RollbackProbe.wixproj `
  -c Release -p:PayloadRoot='C:\payloads\omnibase-1.0.2' `
  -p:ProductVersion=1.0.2
```

Run the harness only after setting `OMNIBASE_INSTALLER_E2E=1` and passing
`-DisposableAccountAcknowledged`. It refuses an existing OmniBase install or
data root and never deletes either as test cleanup.

## Honest release boundary

This authoring is not a distributable OmniBase 1.0.0 release by itself. A
release still requires a complete launchable payload, successful WiX builds,
the lifecycle harness on a clean Windows target, release-manifest verification,
Authenticode signing from an external trusted signing stage, and signature
verification. No certificate, private key, PFX, signing command, or
`production_ready=true` claim belongs in this directory.
