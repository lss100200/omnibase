# OmniBase Linux Desktop R0

This directory owns the first Linux desktop packaging lane. It is intentionally
separate from the Windows WiX/Burn lane.

The Linux package target is an Electron `linux-x64` AppDir produced by
`desktop/scripts/package-linux.mjs`. The packager requires an exact SHA-256 for
the pinned `electron-v43.4.0-linux-x64.zip` archive and copies a closed runtime
tree without following links or overwriting an existing target.

`omnibase-runtime-host.mjs` is the Linux RuntimeHost prototype. It launches the
desktop-local backend and Next server in separate process groups, proves backend
health with the native challenge/HMAC contract, and converges both groups on
SIGTERM/SIGINT. It must be included in a separately generated Linux runtime
manifest; a Windows `.exe` host or PyInstaller payload is not a Linux artifact.

The offline staging builder at
`scripts/release/build_p7_5_linux_desktop_payload.py` creates the closed
`runtime/` tree consumed by the AppDir packager. Its manifest launches the
verified bundled `node/node` with `omnibase-runtime-host.mjs`, while the
backend and Node descriptors remain separately digest-pinned. The builder
preserves POSIX executable bits and performs trusted-manifest replacement only
in a copied desktop project.

R0 does not claim a supported Linux distribution, independent P3.4 Runner
attestation, AppImage signing, `.deb` packaging, or production readiness. The
next gate must build the Linux backend/Node/runtime payload on Ubuntu 22.04 and
24.04, verify executable bits and manifest digests, then run a real Electron
smoke and lifecycle receipt in a disposable Linux VM.
