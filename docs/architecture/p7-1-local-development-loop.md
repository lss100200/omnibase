# P7.1 Local Development Loop

Status: **Wave 1 read-only local-file engineering evidence ready for source
review; real Electron and Windows package evidence not yet recorded**.

P7.1 starts the real local-development loop inside the P7 editor-first desktop
workbench. Wave 1 is intentionally smaller than the complete loop: the Owner
may select one local directory, browse a bounded tree, and open bounded UTF-8
text in the central editor as read-only content. Save/write, Agent file tools,
Terminal, Git, search, rename, deletion and release packaging remain separate
later gates.

## 1. Forward-only authority

P6.7 and P7.0 Wave 1 correctly kept files and direct filesystem access closed.
This document supersedes those restrictions only for the exact P7.1 Wave 1
read-only capability. It does not retroactively change the evidence or claims
of either accepted phase.

The authority chain is:

`Owner picker gesture -> origin-checked preload -> exact Electron IPC ->
Electron main filesystem service`.

There is no Browser/Next/backend file route. The renderer has no Node.js
filesystem authority, physical or absolute root path, native handle, launch
identity or backend control token. The existing desktop SQLite schema remains
version 9; P7.1 Wave 1 adds no database migration.

## 2. Authorization lifecycle

`workspaceFiles.authorize({workspaceId})` is the only operation that may open
the native directory picker. Electron main accepts one selected root at a time
and stores its handle/path identity only in memory. A successful authorization
returns only `{workspaceId, rootName, authorizationGeneration}`. Authorizing a
new root replaces the prior authorization and advances one monotonic generation.

`workspaceFiles.release({workspaceId, authorizationGeneration})` clears the
authorization and advances the generation. Workspace switch, renderer release,
window destruction and application shutdown clear the current authorization.
Pending or replayed work from an older generation must fail closed and may not
populate the new Workspace view.

Before authorization and every list/read operation, Electron main revalidates
the exact Workspace through the existing native `getWorkspaceAgent` authority.
An absent, archived, mismatched or otherwise inactive Workspace invalidates the
authorization. A Workspace id supplied by the renderer is a scope claim, not
filesystem authority.

## 3. Closed read-only IPC

The exact Wave 1 channels and preload methods are:

| IPC channel | Preload method | Exact request | Secret-free result |
|---|---|---|---|
| `omnibase:workspace-files:authorize` | `workspaceFiles.authorize` | `{workspaceId}` | `{workspaceId, rootName, authorizationGeneration}` |
| `omnibase:workspace-files:release` | `workspaceFiles.release` | `{workspaceId, authorizationGeneration}` | `{released: true}` |
| `omnibase:workspace-files:list` | `workspaceFiles.list` | `{workspaceId, authorizationGeneration, directoryPath}` | `{directoryPath, entries, truncated}` |
| `omnibase:workspace-files:read` | `workspaceFiles.read` | `{workspaceId, authorizationGeneration, path}` | `{path, content, sizeBytes, lastModifiedMs, sha256}` |

List entries have the exact shape
`{path, name, kind: "file" | "directory", sizeBytes: number | null,
lastModifiedMs: number}`. Root `directoryPath` is the empty string. Every other
path is a normalized logical relative path. Unknown request or response keys,
unsupported types and arbitrary IPC channel names fail closed.

## 4. Path and content boundary

Electron main validates before filesystem access and revalidates the opened
object identity before returning data:

- the selected directory may not be a filesystem/drive root, the user's home
  directory, or another broad ambient root;
- logical paths are at most 4,096 characters and 32 components; each name is
  at most 255 characters;
- absolute, drive-relative, UNC, device namespace, traversal, empty/dot,
  alternate-data-stream, control-character, trailing-dot/space and reserved
  Windows-name forms are rejected;
- `.git`, `.ssh`, cloud credential directories, `.env*`, private-key names and
  equivalent secret-shaped components are rejected before enumeration/open;
- symlinks, junctions, reparse points, other links and non-regular file or
  directory objects are rejected; canonical containment and stable identity
  are checked at every component and final handle;
- directory enumeration is lazy, returns at most 500 entries, visits at most
  2,048 entries, and reports truncation or a stable bounded error rather than
  continuing a hidden scan; and
- reads accept only strict UTF-8 regular files up to 1 MiB and return the byte
  count, modification time and SHA-256 computed from the returned bytes.

Physical roots, native error strings and file content must not enter logs,
SQLite, local storage, Agent prompts, team blackboards or audit payloads.

Stable public failures include `desktop_native_input_invalid`,
`desktop_workspace_files_not_authorized`,
`desktop_workspace_files_generation_conflict`,
`desktop_workspace_files_generation_exhausted`,
`desktop_workspace_files_picker_cancelled`,
`desktop_workspace_files_root_unsafe`,
`desktop_workspace_files_path_invalid`,
`desktop_workspace_files_path_not_found`,
`desktop_workspace_files_link_forbidden`,
`desktop_workspace_files_type_forbidden`,
`desktop_workspace_files_sensitive_forbidden`,
`desktop_workspace_files_directory_too_large`,
`desktop_workspace_files_file_too_large`,
`desktop_workspace_files_not_utf8`,
`desktop_workspace_files_identity_drift` and
`desktop_workspace_files_unavailable`.

## 5. Explicitly closed later gates

Wave 1 has no save/write/patch/rollback API and no Agent filesystem tool. It
does not authorize file context injection, automatic edits, Terminal or process
execution, Git status/diff/commit, global search, watchers, rename, deletion,
directory creation, system-default opening, shell integration or persistence of
the selected root. Disabled controls and unavailable states must remain honest.

Later write admission requires a separate product law covering reviewed buffers,
expected-digest compare-and-swap, atomic replacement, post-write verification,
conflict/recovery states and Agent-change review. Terminal and Git each require
their own execution/argument/working-directory/output/cancellation or repository
identity/mutation boundaries. A Wave 1 pass proves none of them.

## 6. Verification and recovery

Wave 1 acceptance requires:

- Electron IPC tests for exact origin, channel, argument and response shape;
- authorization-generation tests covering replace, release, Workspace switch,
  window destruction and stale list/read completion;
- Windows path attacks covering roots/home, traversal, UNC/device/ADS/reserved
  names, secret names, links/junctions/reparse points, non-regular objects,
  containment and identity drift;
- list/read budget, strict UTF-8, SHA-256 and deterministic ordering tests;
- frontend bridge and workbench tests proving old-generation data cannot paint
  a new Workspace and no write/Terminal/Git/search action is exposed;
- desktop/frontend test, typecheck and production-build gates, payload/freeze
  contract tests, both maintainer validators and `git diff --check`; and
- a real Electron journey against an explicitly disposable local root before
  any installed-product claim.

On ambiguity, release the in-memory authorization, clear the visible tree and
buffers, and require a new Owner picker gesture. Preserve user files untouched.
Never repair this lane by persisting a physical path, weakening link/secret
checks, adding a Next/backend file route, rewriting SQLite, or enabling writes,
Terminal, Git or Agent tools.
