# P7.3 Complete Workspace Hot-Plug Platform

Status: source implementation and local source gates complete from accepted
`main@5ca173a741aca2e176f36f1f1869234bba8deb5b`; source commit identity and
installed-product evidence are still pending. Production acceptance is not
signed off.

## 1. Status and delivery rule

P7.3 is the single implementation phase for the complete personal Workspace
hot-plug platform. It starts only after P7.2 is independently accepted. P7.3 is
not complete when it has only schemas, mocked adapters, UI placeholders or one
special-case plugin path. Completion requires the common registry, lifecycle,
security and recovery paths plus real end-to-end journeys for every admitted
component family.

P7.4 may harden, scale and certify the resulting platform. It cannot be the
first phase to implement a registry, lifecycle transition, adapter family,
permission path, revocation path or recovery path listed here.

## 2. Product outcome

Within a derived Workspace, an Owner or assistant can assemble and change the
workbench without mutating the application shell or the immutable
`standard-workbench`. The assistant may create a proposal, package declarative
components and request capabilities. Only the Owner may approve the exact
package, dependency graph, Slot diff and capability grant.

An approved component can be installed, activated, deactivated, upgraded,
rolled back, revoked and removed without restarting or corrupting another
Workspace. A failed component degrades to a bounded unavailable surface. The
standard workbench, settings center and emergency controls always remain
reachable.

"Hot-plug" means a common manifest and lifecycle cover every extension-relevant
personal-workbench component family. It does not mean executing arbitrary code
inside the Electron renderer or granting ambient host authority.

## 3. Admitted component families

P7.3 must support these families through the same registry and lifecycle:

1. **UI and canvas components**: host-rendered declarative views, editors,
   panels, inspectors, commands and status items bound to typed Slots. Package
   data is interpreted by trusted host renderers; packages cannot inject
   renderer JavaScript, CSS globals, remote iframes, CDN assets or Electron
   APIs.
2. **Instruction Skills**: exact immutable first-party or Owner-reviewed Skill
   versions with bounded instructions and declared dependencies. Workflow or
   executable behavior uses a Sandbox adapter, never prompt text as authority.
3. **MCP connectors**: explicit server identity, transport, tool allowlist,
   schemas, timeout, concurrency, network and secret references. Tool discovery
   never grants invocation authority; each call revalidates the live grant.
4. **Sandbox workloads**: packaged executable or workflow components run out of
   process under a disposable workload identity with bounded resources,
   logical-resource capabilities, lease/fencing and a kill path. They receive
   no host path, database locator, raw Provider key or ambient network access.
5. **Trusted local adapters**: source-owned packaged bridges to local
   applications or operating-system facilities. They use a closed native IPC
   catalog and the same grant, audit, health and revocation rules; no arbitrary
   command line is accepted from a renderer or manifest.

Adding a future family must extend this typed kernel. It must not create an
unreviewed parallel installer, authority model or direct renderer/native path.

## 4. Immutable package and registry

Every component version has one canonical manifest and content digest. The
manifest binds at least:

- logical component ID, family, semantic version and package SHA-256;
- publisher/source classification and compatibility range;
- declared entrypoint selected from the family-specific closed schema;
- target Slot kinds, cardinality and ordering constraints;
- exact dependencies and conflicts;
- logical resource actions, data scope, network allowlist and secret-reference
  classes;
- wall, invocation, byte, token, concurrency and retry budgets;
- state schema, migration declaration and recovery posture;
- health probe, quiesce timeout and uninstall data-retention policy.

Desktop-local SQLite owns immutable definitions and versions plus
Workspace-scoped installations, bindings, grants, health, effect journal and
audit identities. Content changes always create a new version and digest. The
renderer never becomes registry, approval or installation authority.

## 5. Lifecycle

The common lifecycle is:

```text
discover -> stage -> verify -> propose -> Owner approve
         -> install -> bind -> activate -> health
         -> quiesce -> disable | upgrade | rollback | revoke | uninstall
```

Each mutation uses Workspace identity, expected revision, exact manifest and
package digests, an idempotency key and a monotonic operation generation.
Activation first establishes grants and workload identity, then starts the
adapter, then commits the active binding only after a successful exact health
result. Deactivation closes new calls, fences the old generation, waits a
bounded quiesce interval and terminates remaining work before releasing grants.

Upgrade creates a new binding generation. It never edits an installed version
or running record in place. Rollback selects a previously verified version but
still creates a new generation and re-runs verification and Owner review when
the requested capability set differs.

## 6. P3.4 security mapping

P7.3 reuses P3.4 principles in the personal desktop composition without
claiming that the enterprise production control plane is activated:

- all external DTOs use logical component, Workspace, resource and action IDs;
- capabilities bind Workspace, component version, workload identity, action,
  resource version, operation generation, expiry and budget;
- Run/workload leases and network leases are separate and independently
  fenced;
- every invocation revalidates active installation, binding generation, grant,
  revocation, lease/fencing and remaining budget;
- writes use reviewed ChangeSet/digest CAS/atomic commit/post-write verify and
  recovery contracts rather than ambient file or database authority;
- secret values remain in their owning vault and are referenced by logical ID;
- revoke immediately rejects new calls, invalidates grants and leases, closes
  transports and terminates owned processes;
- audit is append-only and records proposal, decision, package, dependency,
  grant, activation, invocation, external effect, health, revocation and
  recovery identities;
- `pending` and `unknown` external effects enter explicit reconciliation and
  are never replayed automatically.

The immutable application shell, standard workbench, central settings,
emergency stop and audit viewer are outside component write authority. A
Workspace package cannot grant itself authority or modify another Workspace.

## 7. Owner and assistant workflow

The central settings center must expose Workspace-scoped Catalog, Installed,
Slots, Skills, MCP, Sandbox, Local Adapters, Permissions, Health, Review, Audit
and Recovery views. It must support:

- exact package and publisher identity inspection;
- dependency and conflict preview;
- before/after Slot, configuration and permission Diff;
- full request SHA-256 and expected base revision;
- approve, reject, enable, disable, upgrade, rollback, revoke and uninstall;
- per-component budget, network and logical-resource controls;
- health and last-failure details without secret or physical-locator leakage;
- one emergency action that fences and stops all non-core Workspace
  components while preserving the standard workbench.

Assistant output is always a proposal. It cannot approve, install, grant,
activate, dismiss a warning or reconcile an ambiguous effect. Stale proposals
remain rejectable but cannot be approved.

## 8. Isolation and failure behavior

One component failure cannot collapse the shell, settings center, another
component or another Workspace. UI components render through host-owned error
boundaries. Connector and workload processes have bounded output, memory,
time, concurrency and restart policy. Crash loops open a circuit breaker and
require Owner action after the bounded retry budget is exhausted.

On startup, the host reconstructs only committed active generations. It first
revalidates packages, grants and compatibility. It does not infer success from
files, PIDs, ports or a previous rendered state. Ambiguous activation,
deactivation, upgrade or external effects remain blocked until reconciliation.

Safe mode loads the immutable standard workbench with all non-core components
disabled. Uninstall follows the manifest's reviewed retention policy and never
deletes unbound paths or data belonging to another component or Workspace.

## 9. Definition of complete

P7.3 requires all of the following before acceptance:

- schema upgrades and immutable/CAS/append-only enforcement;
- strict native response parsing and exact renderer/preload/IPC/native/backend
  contracts for every operation;
- hostile manifest, package, dependency, scope, stale generation, replay,
  budget, revocation and cross-Workspace tests;
- real install/activate/invoke/quiesce/disable/upgrade/rollback/revoke/uninstall
  journeys for each of the five component families;
- renderer crash, adapter crash, timeout, malformed output, health failure,
  partial activation and restart reconciliation tests;
- first-frame Workspace isolation and standard-workbench safe-mode recovery;
- complete settings-center journeys with no placeholder success states;
- frontend, desktop and backend full gates, maintainer validators, sealed
  contracts, formatting and `git diff --check`;
- a fresh unsigned Windows package and exactly one controlled real Electron
  acceptance run covering all five families, Owner review, emergency stop,
  restart recovery and P7.1 file-reading non-regression.

An adapter may show `unavailable` only when the target honestly lacks its
declared prerequisite. The family implementation, lifecycle and negative path
must still exist, and at least one controlled target must prove its real
positive journey before P7.3 is accepted.

## 10. Reserved for P7.4

P7.4 owns broad compatibility matrices, large catalogs and dependency graphs,
performance and soak testing, extended crash and attack campaigns, migration
and backup drills, publisher signing, distribution and Marketplace production
evidence, accessibility and visual polish. Those tasks may strengthen P7.3;
they cannot substitute for or postpone its core platform.
