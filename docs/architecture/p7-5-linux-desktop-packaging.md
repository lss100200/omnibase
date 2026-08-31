# P7.5 Linux Desktop Packaging R0

Status: Linux packaging lane opened from clean `main@0956146`; INV-090, the AppDir
packaging contract, offline Linux payload staging contract and platform path
tests are implemented, but no Linux artifact or Linux lifecycle acceptance has
been issued.

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

## Not yet supported

R0 does not provide a Linux backend/Node build toolchain or payload artifact,
distribution-specific
dependencies, AppImage or `.deb` metadata, package signing, update/rollback
semantics, or a real Linux VM receipt. A Windows `.exe` RuntimeHost and Windows
backend cannot be placed in a Linux package. The next implementation must build
the backend, Node runtime and host on Ubuntu 22.04 and 24.04, preserve executable
bits, generate and pin a Linux manifest, then run a disposable Linux Electron
smoke and lifecycle test before Linux support is announced.
