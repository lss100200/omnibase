# P7.5 Linux Desktop Packaging R1

Status: Linux packaging lane opened from clean `main@96711e8`; INV-090, the AppDir
packaging contract, offline Linux payload staging contract and platform path
tests are implemented. R1 adds a POSIX-only backend builder, staged desktop
build orchestrator and manual Ubuntu 22.04/24.04 artifact workflow. No Linux
lifecycle acceptance or distribution support claim has been issued.

## Scope

This lane adds a Linux `linux-x64` Electron AppDir target while preserving the
existing Windows WiX/Burn path. The first output is an unsigned engineering
directory produced by `desktop/scripts/package-linux.mjs`. It requires an
explicit SHA-256 for `electron-v43.4.0-linux-x64.zip`, refuses symlinks and
hard-linked payload files, and verifies that the packaged runtime tree is an
exact byte-and-POSIX-mode copy of the input tree. The outer runtime verifier
also refuses a Linux manifest entrypoint without an execute bit.

The desktop data root uses `XDG_DATA_HOME/OmniBase`, then
`$HOME/.local/share/OmniBase`. Linux fails closed when neither absolute root is
available. Electron's user-data path is only a bounded fallback for other
platforms without a standard environment. Windows continues to use
`%LOCALAPPDATA%/OmniBase`.

The Linux runtime-host prototype is
`packaging/linux/omnibase-runtime-host.mjs`. It is source-owned and fail-closed:
it validates the runtime manifest descriptors, launches the desktop backend and
Next server in separate process groups, proves loopback health using the native
challenge/HMAC contract, and converges children on SIGTERM/SIGINT. It does not
claim the independent P3.4 Runner isolation gate.

The offline payload builder is
`scripts/release/build_p7_5_linux_desktop_payload.py`. It consumes a Linux
backend executable, bundled Node executable, Next standalone tree, compiled
desktop project and the source-owned host script; it creates a closed runtime
inventory, preserves POSIX executable bits, pins the host configuration and
injects the manifest digest only into a copied desktop project. It retains
failed staging trees and refuses existing output, links, hard links, sensitive
paths and non-canonical relative paths. The builder must run on Ubuntu for a
real payload because this Windows host cannot authoritatively produce Linux
executable-bit or ELF evidence.

R1 adds `packaging/linux/OmniBase.DesktopBackend/build_backend.py`, which runs
only on 64-bit POSIX Python 3.12 with the exact pinned FastAPI, Uvicorn and
PyInstaller build dependencies. `scripts/release/build_p7_5_linux_desktop.py`
then stages the desktop project offline, invokes the Linux Electron packager
with the published Electron archive digest, and writes an exclusive build
report outside the repository. The AppDir path requires the exact validated
ten-package P7.3 component bundle because RuntimeManager loads its source-owned
attestations before the product becomes ready. The build report records the
bundle and tree SHA-256, package/file counts and byte budget, and the copied
runtime bundle is revalidated before the report is written.

The manual workflow accepts that bundle only as an existing same-repository
Actions artifact selected by explicit `run_id`, artifact name, bundle SHA-256
and tree SHA-256. The artifact archive root must be the bundle root containing
`index.json`; a missing, ambiguous, invalid or digest-mismatched artifact fails
closed. The workflow runs on Ubuntu 22.04 and 24.04 and uploads the raw AppDir
and reports for review. It refuses
repository-local outputs, duplicate output identities and digest values other
than the source-pinned `electron-v43.4.0-linux-x64.zip` archive.

This repository does not currently contain a CI producer for that bundle: the
P7.3 exporter requires the separately maintained knowledge-ebook source root.
An owner-controlled P7.3 release run must therefore publish the validated
artifact before this workflow can be dispatched. A missing artifact is a
blocked prerequisite, not permission to substitute a local path, synthetic
catalog or unvalidated package tree.

## Not yet supported

R1 does not provide distribution-specific dependencies, AppImage or `.deb`
metadata, package signing, update/rollback semantics, or a real Linux VM
receipt. A Windows `.exe` RuntimeHost and Windows backend cannot be placed in a
Linux package. A successful workflow run is an unsigned engineering AppDir
artifact only; a disposable Linux Electron smoke and lifecycle receipt is
still required before Linux support is announced.
