"""Run the deterministic P7.4 desktop component scale and latency matrix.

This is an engineering source gate. It writes a closed JSON report and never
authorizes signing, Marketplace publication or a production release.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "omnibase.p7-4.hardening-matrix.v1"


@dataclass(frozen=True, slots=True)
class MatrixProfile:
    components: int
    versions_per_component: int
    workspaces: int
    installations_per_workspace: int
    snapshot_samples: int
    invocation_samples: int
    soak_cycles: int


PROFILES = {
    "test": MatrixProfile(4, 2, 2, 2, 5, 3, 2),
    "pr": MatrixProfile(50, 3, 4, 20, 30, 20, 20),
    "certification": MatrixProfile(500, 3, 20, 100, 50, 50, 100),
}

THRESHOLDS_MS = {
    "snapshot_p95": 250.0,
    "mutation_p95": 100.0,
    "begin_p95": 50.0,
    "settle_p95": 50.0,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _p95(samples: list[float]) -> float:
    if not samples:
        raise ValueError("p74_hardening_samples_empty")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _process_rss_bytes() -> int:
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        if not get_process_memory_info(
            get_current_process(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("p74_process_memory_probe_failed")
        return int(counters.working_set_size)
    statm = Path("/proc/self/statm")
    if statm.is_file():
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    import resource  # noqa: PLC0415

    maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return maximum_rss if sys.platform == "darwin" else maximum_rss * 1024


def _direct_child_process_ids() -> list[int]:
    parent_id = os.getpid()
    if sys.platform == "win32":
        command = (
            f"$selfId=$PID; Get-CimInstance Win32_Process -Filter "
            f"'ParentProcessId = {parent_id}' | Where-Object ProcessId -ne $selfId | "
            "ForEach-Object ProcessId; exit 0"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return sorted(
            int(value) for value in completed.stdout.split() if value.isdigit()
        )
    children: list[int] = []
    for candidate in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = candidate.read_text(encoding="ascii").split()
            if int(fields[3]) == parent_id:
                children.append(int(candidate.parent.name))
        except (FileNotFoundError, IndexError, PermissionError, ValueError):
            continue
    return sorted(children)


def _owned_tcp_listener_ports() -> list[int]:
    process_id = os.getpid()
    if sys.platform == "win32":
        command = (
            "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
            f"Where-Object OwningProcess -eq {process_id} | ForEach-Object LocalPort; exit 0"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return sorted(
            {int(value) for value in completed.stdout.split() if value.isdigit()}
        )
    socket_inodes: set[str] = set()
    for descriptor in Path(f"/proc/{process_id}/fd").iterdir():
        try:
            target = os.readlink(descriptor)
        except (FileNotFoundError, PermissionError):
            continue
        if target.startswith("socket:["):
            socket_inodes.add(target.removeprefix("socket:[").removesuffix("]"))
    ports: set[int] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not table.is_file():
            continue
        for line in table.read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 10 and fields[3] == "0A" and fields[9] in socket_inodes:
                ports.add(int(fields[1].split(":")[1], 16))
    return sorted(ports)


def _git(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _load_backend(repo_root: Path) -> dict[str, Any]:
    backend_source = str(repo_root / "backend" / "src")
    if backend_source not in sys.path:
        sys.path.insert(0, backend_source)
    from omnibase.desktop_local import (  # noqa: PLC0415
        DESKTOP_SCHEMA_VERSION,
        DesktopLocalConfig,
        create_owner,
        create_workspace,
        initialized_database,
    )
    from omnibase.desktop_local.components.catalog import (  # noqa: PLC0415
        SEEDED_BY_ID_VERSION,
        digest_json,
    )
    from omnibase.desktop_local.components.service import (  # noqa: PLC0415
        apply_component_action_v2,
        begin_component_invocation,
        create_component_proposal,
        decide_component_proposal,
        emergency_stop_components,
        get_component_snapshot,
        recover_component_kernel,
        register_owner_reviewed_component,
        settle_component_invocation,
        settle_component_recovery,
    )

    return {
        "DESKTOP_SCHEMA_VERSION": DESKTOP_SCHEMA_VERSION,
        "DesktopLocalConfig": DesktopLocalConfig,
        "SEEDED_BY_ID_VERSION": SEEDED_BY_ID_VERSION,
        "apply_component_action_v2": apply_component_action_v2,
        "begin_component_invocation": begin_component_invocation,
        "create_component_proposal": create_component_proposal,
        "create_owner": create_owner,
        "create_workspace": create_workspace,
        "decide_component_proposal": decide_component_proposal,
        "digest_json": digest_json,
        "emergency_stop_components": emergency_stop_components,
        "get_component_snapshot": get_component_snapshot,
        "initialized_database": initialized_database,
        "recover_component_kernel": recover_component_kernel,
        "register_owner_reviewed_component": register_owner_reviewed_component,
        "settle_component_invocation": settle_component_invocation,
        "settle_component_recovery": settle_component_recovery,
    }


def _workspace_id(index: int) -> str:
    return f"workspace_{index + 1:032x}"


def _version(index: int) -> str:
    return f"1.{index}.0"


def _grant() -> dict[str, object]:
    return {
        "action": "ui.render",
        "logical_resource_id": "workspace.component.input",
        "resource_version": 1,
        "logical_service_id": None,
        "expires_in_seconds": 3_600,
        "maximum_invocations": 64,
        "maximum_bytes_in": 1_048_576,
        "maximum_bytes_out": 4_194_304,
        "maximum_tokens": 131_072,
        "maximum_wall_time_ms": 600_000,
        "maximum_cost_units": 1_000,
    }


def _register(
    api: dict[str, Any],
    connection: Any,
    *,
    workspace_id: str,
    manifest: dict[str, object],
) -> tuple[str, str, str]:
    manifest_sha = api["digest_json"](manifest)
    package_sha = api["digest_json"](
        {
            "component_id": manifest["component_id"],
            "payload": "p74",
            "version": manifest["version"],
        }
    )
    inventory_sha = api["digest_json"](
        {"manifest.json": manifest_sha, "view.json": package_sha}
    )
    api["register_owner_reviewed_component"](
        connection,
        workspace_id=workspace_id,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=inventory_sha,
    )
    return manifest_sha, package_sha, inventory_sha


def _transition(
    api: dict[str, Any],
    connection: Any,
    *,
    workspace_id: str,
    component_id: str,
    version: str,
    action: str,
    expected_revision: int,
    manifest_sha: str,
    package_sha: str,
) -> dict[str, object]:
    proposed = api["create_component_proposal"](
        connection,
        workspace_id=workspace_id,
        component_id=component_id,
        target_version=version,
        change_kind=action,
        expected_revision=expected_revision,
        requested_grants=[_grant()],
        desired_configuration={},
        desired_slot_bindings=[],
        dependency_graph=[],
        source_kind="owner",
        source_reference=None,
        idempotency_key=(
            f"p74:proposal:{workspace_id[-8:]}:{component_id}:{version}:{action}:{expected_revision}"
        ),
    )
    proposal = proposed["proposal"]
    api["decide_component_proposal"](
        connection,
        workspace_id=workspace_id,
        proposal_id=str(proposal["proposal_id"]),
        decision="approve",
        request_sha256=str(proposal["request_sha256"]),
    )
    common = {
        "workspace_id": workspace_id,
        "component_id": component_id,
        "action": action,
        "proposal_id": str(proposal["proposal_id"]),
        "request_sha256": str(proposal["request_sha256"]),
        "expected_revision": expected_revision,
        "manifest_sha256": manifest_sha,
        "package_sha256": package_sha,
        "idempotency_key": (
            f"p74:action:{workspace_id[-8:]}:{component_id}:{version}:{action}:{expected_revision}"
        ),
    }
    prepared = api["apply_component_action_v2"](
        connection,
        **common,
        phase="prepare",
        operation_id=None,
        outcome=None,
        evidence_sha256=None,
        health_state=None,
    )
    ticket = prepared["lifecycle_ticket"]
    return api["apply_component_action_v2"](
        connection,
        **common,
        phase="settle",
        operation_id=str(ticket["operation_id"]),
        outcome="succeeded",
        evidence_sha256=api["digest_json"](
            {
                "action": action,
                "component_id": component_id,
                "profile": "p74",
                "version": version,
            }
        ),
        health_state="healthy" if action == "activate" else None,
        runtime_instance_id=ticket["runtime_instance_id"],
        workload_identity_digest=ticket["workload_identity_digest"],
    )


def run_hardening_matrix(
    *,
    repo_root: Path,
    output_path: Path,
    profile_name: str,
) -> dict[str, object]:
    if profile_name not in PROFILES:
        raise ValueError("p74_hardening_profile_invalid")
    repo_root = repo_root.resolve(strict=True)
    output_path = output_path.absolute()
    profile = PROFILES[profile_name]
    api = _load_backend(repo_root)
    source_commit = _git(repo_root, "rev-parse", "HEAD")
    source_clean = _git(repo_root, "status", "--porcelain") == ""
    generated: list[tuple[dict[str, object], str, str, str]] = []
    mutation_samples: list[float] = []
    snapshot_samples: list[float] = []
    begin_samples: list[float] = []
    settle_samples: list[float] = []
    soak_rss_samples: list[int] = []
    soak_replays = 0
    child_processes_before = _direct_child_process_ids()
    listeners_before = _owned_tcp_listener_ports()

    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="omnibase-p74-hardening-") as temporary:
        config = api["DesktopLocalConfig"](
            data_root=Path(temporary) / "data", application_version="1.0.0"
        )
        with api["initialized_database"](config) as connection:
            api["create_owner"](connection, "owner-p74", "P7.4 Matrix Owner")
            workspace_ids = [
                _workspace_id(index) for index in range(profile.workspaces)
            ]
            for index, workspace_id in enumerate(workspace_ids):
                api["create_workspace"](
                    connection, workspace_id, "owner-p74", f"P7.4 Workspace {index + 1}"
                )

            template = api["SEEDED_BY_ID_VERSION"][
                ("builtin.workspace-canvas", "1.0.0")
            ].manifest
            for component_index in range(profile.components):
                for version_index in range(profile.versions_per_component):
                    manifest = copy.deepcopy(template)
                    manifest["component_id"] = f"owner.cert{component_index:04d}"
                    manifest["version"] = _version(version_index)
                    manifest["publisher"] = {
                        "classification": "owner_reviewed",
                        "id": "owner.p74",
                    }
                    manifest["dependencies"] = []
                    manifest["conflicts"] = []
                    identities = _register(
                        api,
                        connection,
                        workspace_id=workspace_ids[0],
                        manifest=manifest,
                    )
                    generated.append((manifest, *identities))

            first_version = {
                str(manifest["component_id"]): (
                    manifest,
                    manifest_sha,
                    package_sha,
                    inventory_sha,
                )
                for manifest, manifest_sha, package_sha, inventory_sha in generated
                if manifest["version"] == "1.0.0"
            }
            selected_ids = sorted(first_version)[: profile.installations_per_workspace]
            for workspace_id in workspace_ids:
                for component_id in selected_ids:
                    manifest, manifest_sha, package_sha, _ = first_version[component_id]
                    _register(
                        api, connection, workspace_id=workspace_id, manifest=manifest
                    )
                    started = time.perf_counter_ns()
                    _transition(
                        api,
                        connection,
                        workspace_id=workspace_id,
                        component_id=component_id,
                        version="1.0.0",
                        action="install",
                        expected_revision=0,
                        manifest_sha=manifest_sha,
                        package_sha=package_sha,
                    )
                    mutation_samples.append(
                        (time.perf_counter_ns() - started) / 1_000_000
                    )

            benchmark_component = selected_ids[0]
            manifest, manifest_sha, package_sha, _ = first_version[benchmark_component]
            del manifest
            revision = 1
            for action in ("bind", "activate"):
                settled = _transition(
                    api,
                    connection,
                    workspace_id=workspace_ids[0],
                    component_id=benchmark_component,
                    version="1.0.0",
                    action=action,
                    expected_revision=revision,
                    manifest_sha=manifest_sha,
                    package_sha=package_sha,
                )
                revision = int(settled["installation"]["revision"])

            for sample in range(profile.invocation_samples):
                started = time.perf_counter_ns()
                begun = api["begin_component_invocation"](
                    connection,
                    workspace_id=workspace_ids[0],
                    component_id=benchmark_component,
                    action="ui.render",
                    expected_revision=revision,
                    binding_generation=1,
                    manifest_sha256=manifest_sha,
                    package_sha256=package_sha,
                    idempotency_key=f"p74:invoke:{sample:04d}",
                    arguments_sha256=api["digest_json"]({"sample": sample}),
                    logical_resource_id="workspace.component.input",
                    resource_version=1,
                    logical_service_id=None,
                    bytes_in=1,
                    bytes_out_reserved=1,
                    tokens_reserved=0,
                    wall_time_ms=1,
                    cost_units=1,
                )
                begin_samples.append((time.perf_counter_ns() - started) / 1_000_000)
                ticket = begun["ticket"]
                started = time.perf_counter_ns()
                api["settle_component_invocation"](
                    connection,
                    workspace_id=workspace_ids[0],
                    operation_id=str(ticket["operation_id"]),
                    request_sha256=str(ticket["request_sha256"]),
                    state="succeeded",
                    result_sha256=api["digest_json"]({"result": sample}),
                    evidence_sha256=api["digest_json"]({"receipt": sample}),
                    error_code=None,
                    actual_bytes_out=1,
                    actual_tokens=0,
                    actual_wall_time_ms=1,
                )
                settle_samples.append((time.perf_counter_ns() - started) / 1_000_000)

            for cycle, component_id in enumerate(selected_ids[: profile.soak_cycles]):
                manifest, manifest_sha, package_sha, _ = first_version[component_id]
                del manifest
                if component_id == benchmark_component:
                    cycle_revision = revision
                else:
                    bound = _transition(
                        api,
                        connection,
                        workspace_id=workspace_ids[0],
                        component_id=component_id,
                        version="1.0.0",
                        action="bind",
                        expected_revision=1,
                        manifest_sha=manifest_sha,
                        package_sha=package_sha,
                    )
                    cycle_revision = int(bound["installation"]["revision"])
                    activated = _transition(
                        api,
                        connection,
                        workspace_id=workspace_ids[0],
                        component_id=component_id,
                        version="1.0.0",
                        action="activate",
                        expected_revision=cycle_revision,
                        manifest_sha=manifest_sha,
                        package_sha=package_sha,
                    )
                    cycle_revision = int(activated["installation"]["revision"])

                begun = api["begin_component_invocation"](
                    connection,
                    workspace_id=workspace_ids[0],
                    component_id=component_id,
                    action="ui.render",
                    expected_revision=cycle_revision,
                    binding_generation=1,
                    manifest_sha256=manifest_sha,
                    package_sha256=package_sha,
                    idempotency_key=f"p74:soak:invoke:{cycle:04d}",
                    arguments_sha256=api["digest_json"](
                        {"cycle": cycle, "profile": profile_name}
                    ),
                    logical_resource_id="workspace.component.input",
                    resource_version=1,
                    logical_service_id=None,
                    bytes_in=1,
                    bytes_out_reserved=1,
                    tokens_reserved=0,
                    wall_time_ms=1,
                    cost_units=1,
                )
                soak_replays += int(bool(begun["replayed"]))
                ticket = begun["ticket"]
                settled = api["settle_component_invocation"](
                    connection,
                    workspace_id=workspace_ids[0],
                    operation_id=str(ticket["operation_id"]),
                    request_sha256=str(ticket["request_sha256"]),
                    state="succeeded",
                    result_sha256=api["digest_json"]({"cycle_result": cycle}),
                    evidence_sha256=api["digest_json"]({"cycle_receipt": cycle}),
                    error_code=None,
                    actual_bytes_out=1,
                    actual_tokens=0,
                    actual_wall_time_ms=1,
                )
                soak_replays += int(bool(settled["replayed"]))

                if cycle % 2 == 0:
                    api["recover_component_kernel"](connection)
                    snapshot = api["get_component_snapshot"](
                        connection, workspace_ids[0]
                    )
                    recoveries = [
                        recovery
                        for recovery in snapshot["recoveries"]
                        if recovery["component_id"] == component_id
                        and recovery["state"] == "pending"
                    ]
                    if len(recoveries) != 1:
                        raise RuntimeError("p74_soak_recovery_identity_invalid")
                    recovery = recoveries[0]
                    recovered = api["settle_component_recovery"](
                        connection,
                        workspace_id=workspace_ids[0],
                        recovery_id=str(recovery["recovery_id"]),
                        operation_id=str(recovery["operation_id"]),
                        outcome="succeeded",
                        evidence_sha256=api["digest_json"]({"cycle_recovery": cycle}),
                        health_state="healthy",
                        runtime_instance_id=str(recovery["runtime_instance_id"]),
                        workload_identity_digest=str(
                            recovery["workload_identity_digest"]
                        ),
                        error_code=None,
                    )
                    soak_replays += int(bool(recovered["replayed"]))
                    installation = next(
                        item
                        for item in api["get_component_snapshot"](
                            connection, workspace_ids[0]
                        )["installations"]
                        if item["component_id"] == component_id
                    )
                    disabled = _transition(
                        api,
                        connection,
                        workspace_id=workspace_ids[0],
                        component_id=component_id,
                        version="1.0.0",
                        action="disable",
                        expected_revision=int(installation["revision"]),
                        manifest_sha=manifest_sha,
                        package_sha=package_sha,
                    )
                    soak_replays += int(bool(disabled["replayed"]))
                elif cycle == profile.soak_cycles - 1:
                    emergency = api["emergency_stop_components"](
                        connection,
                        workspace_id=workspace_ids[0],
                        phase="prepare",
                        idempotency_key=f"p74:soak:emergency:{cycle:04d}",
                        reason_code="p74_bounded_soak",
                    )
                    soak_replays += int(bool(emergency["replayed"]))
                    tickets = emergency["tickets"]
                    if len(tickets) != profile.installations_per_workspace:
                        raise RuntimeError("p74_soak_emergency_identity_invalid")
                    for emergency_ticket in tickets:
                        emergency_settled = api["emergency_stop_components"](
                            connection,
                            workspace_id=workspace_ids[0],
                            phase="settle",
                            idempotency_key=f"p74:soak:emergency:{cycle:04d}",
                            reason_code="p74_bounded_soak",
                            component_id=str(emergency_ticket["component_id"]),
                            operation_id=str(emergency_ticket["operation_id"]),
                            effect_id=str(emergency_ticket["effect_id"]),
                            request_sha256=str(emergency_ticket["request_sha256"]),
                            outcome="succeeded",
                            evidence_sha256=api["digest_json"](
                                {
                                    "component_id": emergency_ticket["component_id"],
                                    "cycle_emergency": cycle,
                                }
                            ),
                            error_code=None,
                        )
                        soak_replays += int(bool(emergency_settled["replayed"]))
                else:
                    disabled = _transition(
                        api,
                        connection,
                        workspace_id=workspace_ids[0],
                        component_id=component_id,
                        version="1.0.0",
                        action="disable",
                        expected_revision=cycle_revision,
                        manifest_sha=manifest_sha,
                        package_sha=package_sha,
                    )
                    soak_replays += int(bool(disabled["replayed"]))
                soak_rss_samples.append(_process_rss_bytes())

            for sample in range(profile.snapshot_samples):
                started = time.perf_counter_ns()
                api["get_component_snapshot"](
                    connection, workspace_ids[sample % len(workspace_ids)]
                )
                snapshot_samples.append((time.perf_counter_ns() - started) / 1_000_000)

            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_key_violations = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            counts = {
                "catalog_versions": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM component_catalog_version"
                    ).fetchone()[0]
                ),
                "installations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workspace_component_installation"
                    ).fetchone()[0]
                ),
                "operations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workspace_component_operation"
                    ).fetchone()[0]
                ),
                "workspaces": int(
                    connection.execute("SELECT COUNT(*) FROM workspace").fetchone()[0]
                ),
            }
            unresolved_effects = int(
                connection.execute(
                    "SELECT COUNT(*) FROM workspace_component_effect WHERE state IN "
                    "('pending', 'unknown', 'reconciliation_required')"
                ).fetchone()[0]
            )
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    child_processes_after = _direct_child_process_ids()
    listeners_after = _owned_tcp_listener_ports()

    memory_baseline = soak_rss_samples[0]
    memory_growth = soak_rss_samples[-1] - memory_baseline
    memory_growth_ceiling = max(int(memory_baseline * 0.10), 128 * 1024 * 1024)
    soak_passed = (
        soak_replays == 0
        and unresolved_effects == 0
        and memory_growth <= memory_growth_ceiling
        and child_processes_after == child_processes_before
        and listeners_after == listeners_before
    )

    p95 = {
        "snapshot_p95": _p95(snapshot_samples),
        "mutation_p95": _p95(mutation_samples),
        "begin_p95": _p95(begin_samples),
        "settle_p95": _p95(settle_samples),
    }
    threshold_results = {
        name: p95[name] <= ceiling for name, ceiling in THRESHOLDS_MS.items()
    }
    passed = (
        schema_version == api["DESKTOP_SCHEMA_VERSION"] == 12
        and integrity == "ok"
        and foreign_key_violations == 0
        and counts["workspaces"] == profile.workspaces
        and counts["installations"]
        == profile.workspaces * profile.installations_per_workspace
        and soak_passed
        and all(threshold_results.values())
    )
    report: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "source_commit": source_commit,
        "source_clean": source_clean,
        "profile": profile_name,
        "profile_parameters": asdict(profile),
        "dataset_sha256": _digest({"profile": asdict(profile), "seed": 74}),
        "desktop_schema_version": schema_version,
        "counts": counts,
        "integrity": integrity,
        "foreign_key_violations": foreign_key_violations,
        "samples_ms": {
            "snapshot": snapshot_samples,
            "mutation": mutation_samples,
            "begin": begin_samples,
            "settle": settle_samples,
        },
        "p95_ms": p95,
        "thresholds_ms": THRESHOLDS_MS,
        "threshold_results": threshold_results,
        "python_peak_traced_bytes": peak_bytes,
        "bounded_soak": {
            "cycles": profile.soak_cycles,
            "rss_samples_bytes": soak_rss_samples,
            "memory_growth_bytes": memory_growth,
            "memory_growth_ceiling_bytes": memory_growth_ceiling,
            "automatic_replays": soak_replays,
            "unresolved_effects": unresolved_effects,
            "child_processes_before": child_processes_before,
            "child_processes_after": child_processes_after,
            "listener_ports_before": listeners_before,
            "listener_ports_after": listeners_after,
            "passed": soak_passed,
            "nightly_8h_completed": False,
            "release_candidate_24h_completed": False,
        },
        "engineering_gate_passed": passed,
        "authenticode_verified": False,
        "marketplace_verified": False,
        "human_visual_reviewed": False,
        "production_ready": False,
        "release_authorized": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical_json(report) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent, delete=False
    ) as temporary_file:
        temporary_file.write(encoded)
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(output_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("pr", "certification"), default="pr")
    arguments = parser.parse_args()
    report = run_hardening_matrix(
        repo_root=arguments.repo_root,
        output_path=arguments.output,
        profile_name=arguments.profile,
    )
    print(
        _canonical_json(
            {
                key: report[key]
                for key in ("schema", "profile", "p95_ms", "engineering_gate_passed")
            }
        )
    )
    return 0 if report["engineering_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
