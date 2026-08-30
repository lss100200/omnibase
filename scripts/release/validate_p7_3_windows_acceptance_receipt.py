"""Validate one P7.3 Windows/Electron engineering acceptance receipt offline.

The receipt is evidence supplied by an external acceptance controller. This
validator checks its closed shape, identity bindings and engineering claims;
it does not launch Windows Sandbox, inspect screenshots, verify Authenticode or
authorize a production release.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

RECEIPT_SCHEMA = "omnibase.p7-3.windows-electron-acceptance-receipt.v1"
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_SCREENSHOTS = 32
MAX_BUILD_REPORT_BYTES = 2 * 1024 * 1024
MAX_RUNTIME_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 64 * 1024 * 1024
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_FAMILIES = {
    "declarative_ui": "builtin.workspace-canvas",
    "instruction_skill": "builtin.instruction-skill",
    "mcp_connector": "builtin.readonly-mcp",
    "sandbox_workload": "builtin.sandbox-workload",
    "trusted_local_adapter": "knowledge.ebook",
}
_LIFECYCLE = (
    ("install", "1.0.0", 0, 0, 1),
    ("bind", "1.0.0", 1, 1, 2),
    ("activate", "1.0.0", 1, 2, 3),
    ("invoke", "1.0.0", 1, 3, 3),
    ("disable", "1.0.0", 1, 3, 4),
    ("upgrade", "1.1.0", 2, 4, 5),
    ("activate", "1.1.0", 2, 5, 6),
    ("invoke", "1.1.0", 2, 6, 6),
    ("rollback", "1.0.0", 3, 6, 7),
    ("activate", "1.0.0", 3, 7, 8),
    ("invoke", "1.0.0", 3, 8, 8),
    ("revoke", "1.0.0", 3, 8, 9),
    ("uninstall", "1.0.0", 3, 9, 10),
)
_MUTATIONS = frozenset(
    {
        "activate",
        "bind",
        "disable",
        "install",
        "revoke",
        "rollback",
        "uninstall",
        "upgrade",
    }
)
_QUIESCE_ACTIONS = frozenset({"disable", "revoke", "rollback", "uninstall", "upgrade"})
_MCP_TOOLS = (
    "omnibase_files_list",
    "omnibase_files_read",
    "omnibase_files_hash",
    "omnibase_text_search",
)


class P73AcceptanceReceiptError(ValueError):
    """A stable, path-redacted receipt validation failure."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _exact(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise P73AcceptanceReceiptError(code)
    return value


def _strict_bool(value: object, expected: bool, code: str) -> None:
    if type(value) is not bool or value is not expected:
        raise P73AcceptanceReceiptError(code)


def _strict_int(value: object, *, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise P73AcceptanceReceiptError(code)
    return value


def _matching_string(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise P73AcceptanceReceiptError(code)
    return value


def _timestamp(value: object, code: str) -> datetime:
    raw = _matching_string(value, _TIMESTAMP, code)
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise P73AcceptanceReceiptError(code) from exc
    return parsed


def _safe_relative_path(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or ":" in value
    ):
        raise P73AcceptanceReceiptError(code)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise P73AcceptanceReceiptError(code)
    return value


def _artifact(value: object, *, expected_name: str, expected_kind: str) -> dict[str, Any]:
    item = _exact(
        value,
        {"kind", "name", "sha256", "size"},
        "p73_acceptance_artifact_fields_invalid",
    )
    if item["kind"] != expected_kind or item["name"] != expected_name:
        raise P73AcceptanceReceiptError("p73_acceptance_artifact_identity_invalid")
    _matching_string(item["sha256"], _SHA256, "p73_acceptance_artifact_digest_invalid")
    _strict_int(
        item["size"],
        minimum=1,
        maximum=2 * 1024 * 1024 * 1024,
        code="p73_acceptance_artifact_size_invalid",
    )
    return item


def _validate_versions(value: object) -> dict[str, tuple[str, str]]:
    if not isinstance(value, list) or len(value) != 2:
        raise P73AcceptanceReceiptError("p73_acceptance_version_set_invalid")
    expected = ("1.0.0", "1.1.0")
    versions: dict[str, tuple[str, str]] = {}
    digests: set[str] = set()
    for item_value, version in zip(value, expected, strict=True):
        item = _exact(
            item_value,
            {"manifest_sha256", "package_sha256", "version"},
            "p73_acceptance_version_fields_invalid",
        )
        if item["version"] != version:
            raise P73AcceptanceReceiptError("p73_acceptance_version_set_invalid")
        manifest_sha256 = _matching_string(
            item["manifest_sha256"],
            _SHA256,
            "p73_acceptance_manifest_digest_invalid",
        )
        package_sha256 = _matching_string(
            item["package_sha256"],
            _SHA256,
            "p73_acceptance_package_digest_invalid",
        )
        if (
            manifest_sha256 == package_sha256
            or {
                manifest_sha256,
                package_sha256,
            }
            & digests
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_version_digest_reused")
        digests.update((manifest_sha256, package_sha256))
        versions[version] = (manifest_sha256, package_sha256)
    return versions


def _validate_owner_review(
    value: object,
    *,
    expected: bool,
    owner_id: str,
    expected_revision: int,
    request_sha256: str,
    proposal_ids: set[str],
    review_ids: set[str],
) -> None:
    if not expected:
        if value is not None:
            raise P73AcceptanceReceiptError("p73_acceptance_unexpected_owner_review")
        return
    review = _exact(
        value,
        {
            "decision",
            "expected_revision",
            "explicit_owner_action",
            "owner_id",
            "proposal_id",
            "request_sha256",
            "review_id",
        },
        "p73_acceptance_owner_review_fields_invalid",
    )
    if (
        review["decision"] != "approved"
        or review["owner_id"] != owner_id
        or review["request_sha256"] != request_sha256
        or review["expected_revision"] != expected_revision
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_owner_review_identity_invalid")
    _strict_bool(review["explicit_owner_action"], True, "p73_acceptance_owner_action_required")
    for field, seen in (("proposal_id", proposal_ids), ("review_id", review_ids)):
        identity = _matching_string(
            review[field], _UUID, "p73_acceptance_owner_review_identity_invalid"
        )
        if identity in seen:
            raise P73AcceptanceReceiptError("p73_acceptance_owner_review_reused")
        seen.add(identity)


def _validate_health(
    value: object,
    *,
    expected: bool,
    generation: int,
    health_by_generation: dict[int, tuple[str, str]],
    runtime_ids: set[str],
    workload_identities: set[str],
) -> None:
    if not expected:
        if value is not None:
            raise P73AcceptanceReceiptError("p73_acceptance_unexpected_health_evidence")
        return
    health = _exact(
        value,
        {
            "binding_generation",
            "evidence_sha256",
            "runtime_identity",
            "state",
            "workload_identity_sha256",
        },
        "p73_acceptance_health_fields_invalid",
    )
    runtime_identity = _matching_string(
        health["runtime_identity"], _UUID, "p73_acceptance_health_identity_invalid"
    )
    workload_identity = _matching_string(
        health["workload_identity_sha256"],
        _SHA256,
        "p73_acceptance_health_identity_invalid",
    )
    _matching_string(health["evidence_sha256"], _SHA256, "p73_acceptance_health_evidence_invalid")
    if (
        health["state"] != "healthy"
        or health["binding_generation"] != generation
        or generation in health_by_generation
        or runtime_identity in runtime_ids
        or workload_identity in workload_identities
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_health_identity_invalid")
    runtime_ids.add(runtime_identity)
    workload_identities.add(workload_identity)
    health_by_generation[generation] = (runtime_identity, workload_identity)


def _validate_quiesce(value: object, *, expected: bool) -> None:
    if not expected:
        if value is not None:
            raise P73AcceptanceReceiptError("p73_acceptance_unexpected_quiesce_evidence")
        return
    quiesce = _exact(
        value,
        {
            "automatic_replay",
            "evidence_sha256",
            "new_calls_closed",
            "owned_processes_zero",
            "pending_effects",
            "prior_generation_fenced",
            "unknown_effects",
        },
        "p73_acceptance_quiesce_fields_invalid",
    )
    _matching_string(
        quiesce["evidence_sha256"],
        _SHA256,
        "p73_acceptance_quiesce_evidence_invalid",
    )
    for field in (
        "new_calls_closed",
        "owned_processes_zero",
        "prior_generation_fenced",
    ):
        _strict_bool(quiesce[field], True, "p73_acceptance_quiesce_incomplete")
    for field in ("pending_effects", "unknown_effects"):
        _strict_int(
            quiesce[field],
            minimum=0,
            maximum=0,
            code="p73_acceptance_quiesce_incomplete",
        )
    _strict_bool(
        quiesce["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )


def _validate_invocation(  # noqa: C901
    value: object,
    *,
    expected: bool,
    expected_generation: int,
    operation_id: str,
    expected_runtime: tuple[str, str] | None,
    authority_ids: set[str],
) -> None:
    if not expected:
        if value is not None:
            raise P73AcceptanceReceiptError("p73_acceptance_unexpected_invocation_evidence")
        return
    if expected_runtime is None:
        raise P73AcceptanceReceiptError("p73_acceptance_invocation_health_missing")
    item = _exact(
        value,
        {
            "budget_reserved",
            "capability_action",
            "grant_id",
            "network_fencing_token",
            "network_lease_id",
            "network_lease_required",
            "operation_generation",
            "remaining_budget",
            "resource_version",
            "result_sha256",
            "revocation_clear",
            "runtime_identity",
            "scope_revalidated",
            "settled",
            "ticket_operation_id",
            "workload_fencing_token",
            "workload_identity_sha256",
            "workload_lease_id",
        },
        "p73_acceptance_invocation_fields_invalid",
    )
    if (
        item["ticket_operation_id"] != operation_id
        or item["runtime_identity"] != expected_runtime[0]
        or item["workload_identity_sha256"] != expected_runtime[1]
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_invocation_identity_invalid")
    for field in ("grant_id", "workload_lease_id"):
        authority_id = _matching_string(
            item[field], _UUID, "p73_acceptance_invocation_identity_invalid"
        )
        if authority_id in authority_ids:
            raise P73AcceptanceReceiptError("p73_acceptance_invocation_identity_reused")
        authority_ids.add(authority_id)
    _matching_string(item["result_sha256"], _SHA256, "p73_acceptance_result_digest_invalid")
    _matching_string(
        item["capability_action"],
        _SAFE_NAME,
        "p73_acceptance_capability_action_invalid",
    )
    _strict_int(
        item["resource_version"],
        minimum=1,
        maximum=2**63 - 1,
        code="p73_acceptance_invocation_generation_invalid",
    )
    for field in ("operation_generation", "workload_fencing_token"):
        _strict_int(
            item[field],
            minimum=expected_generation,
            maximum=expected_generation,
            code="p73_acceptance_invocation_generation_invalid",
        )
    for field in (
        "budget_reserved",
        "revocation_clear",
        "scope_revalidated",
        "settled",
    ):
        _strict_bool(item[field], True, "p73_acceptance_invocation_revalidation_incomplete")
    network_required = item["network_lease_required"]
    if type(network_required) is not bool:
        raise P73AcceptanceReceiptError("p73_acceptance_network_lease_invalid")
    if network_required:
        network_lease_id = _matching_string(
            item["network_lease_id"], _UUID, "p73_acceptance_network_lease_invalid"
        )
        if network_lease_id in authority_ids:
            raise P73AcceptanceReceiptError("p73_acceptance_invocation_identity_reused")
        authority_ids.add(network_lease_id)
        _strict_int(
            item["network_fencing_token"],
            minimum=1,
            maximum=2**63 - 1,
            code="p73_acceptance_network_lease_invalid",
        )
    elif item["network_lease_id"] is not None or item["network_fencing_token"] is not None:
        raise P73AcceptanceReceiptError("p73_acceptance_network_lease_invalid")
    budget = _exact(
        item["remaining_budget"],
        {
            "bytes",
            "calls",
            "concurrency",
            "cost_micros",
            "retries",
            "tokens",
            "wall_ms",
        },
        "p73_acceptance_budget_fields_invalid",
    )
    for remaining in budget.values():
        _strict_int(
            remaining,
            minimum=0,
            maximum=2**63 - 1,
            code="p73_acceptance_budget_value_invalid",
        )


def _validate_lifecycle_step(
    value: object,
    *,
    expected_operation: str,
    expected_version: str,
    expected_binding_generation: int,
    expected_revision: int,
    result_revision: int,
    version_digests: tuple[str, str],
    owner_id: str,
    workspace_id: str,
    run_id: str,
    sandbox_id: str,
    operation_ids: set[str],
    request_shas: set[str],
    proposal_ids: set[str],
    review_ids: set[str],
    authority_ids: set[str],
    health_by_generation: dict[int, tuple[str, str]],
    runtime_ids: set[str],
    workload_identities: set[str],
) -> None:
    step = _exact(
        value,
        {
            "acceptance_run_id",
            "automatic_replay",
            "binding_generation",
            "dispatch_count",
            "effect_state",
            "expected_revision",
            "health",
            "installation_present",
            "installation_revision",
            "invocation",
            "manifest_sha256",
            "operation",
            "operation_id",
            "owner_review",
            "package_sha256",
            "quiesce",
            "request_sha256",
            "sandbox_instance_id",
            "status",
            "version",
            "workspace_id",
        },
        "p73_acceptance_lifecycle_step_fields_invalid",
    )
    if (
        step["operation"] != expected_operation
        or step["version"] != expected_version
        or step["manifest_sha256"] != version_digests[0]
        or step["package_sha256"] != version_digests[1]
        or step["status"] != "succeeded"
        or step["effect_state"] != "committed"
        or step["workspace_id"] != workspace_id
        or step["acceptance_run_id"] != run_id
        or step["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_lifecycle_identity_invalid")
    request_sha256 = _matching_string(
        step["request_sha256"], _SHA256, "p73_acceptance_owner_request_digest_invalid"
    )
    if request_sha256 in request_shas:
        raise P73AcceptanceReceiptError("p73_acceptance_request_digest_reused")
    request_shas.add(request_sha256)
    _strict_int(
        step["expected_revision"],
        minimum=expected_revision,
        maximum=expected_revision,
        code="p73_acceptance_expected_revision_invalid",
    )
    _strict_int(
        step["installation_revision"],
        minimum=result_revision,
        maximum=result_revision,
        code="p73_acceptance_installation_revision_invalid",
    )
    _strict_bool(
        step["installation_present"],
        expected_operation != "uninstall",
        "p73_acceptance_installation_presence_invalid",
    )
    _strict_int(
        step["binding_generation"],
        minimum=expected_binding_generation,
        maximum=expected_binding_generation,
        code="p73_acceptance_binding_generation_invalid",
    )
    _strict_int(
        step["dispatch_count"],
        minimum=1,
        maximum=1,
        code="p73_acceptance_lifecycle_dispatch_count_invalid",
    )
    _strict_bool(step["automatic_replay"], False, "p73_acceptance_automatic_replay_forbidden")
    operation_id = _matching_string(
        step["operation_id"], _UUID, "p73_acceptance_operation_id_invalid"
    )
    if operation_id in operation_ids:
        raise P73AcceptanceReceiptError("p73_acceptance_operation_id_duplicate")
    operation_ids.add(operation_id)
    is_mutation = expected_operation in _MUTATIONS
    _validate_owner_review(
        step["owner_review"],
        expected=is_mutation,
        owner_id=owner_id,
        expected_revision=expected_revision,
        request_sha256=request_sha256,
        proposal_ids=proposal_ids,
        review_ids=review_ids,
    )
    _validate_health(
        step["health"],
        expected=expected_operation == "activate",
        generation=expected_binding_generation,
        health_by_generation=health_by_generation,
        runtime_ids=runtime_ids,
        workload_identities=workload_identities,
    )
    _validate_quiesce(step["quiesce"], expected=expected_operation in _QUIESCE_ACTIONS)
    _validate_invocation(
        step["invocation"],
        expected=expected_operation == "invoke",
        expected_generation=expected_binding_generation,
        operation_id=operation_id,
        expected_runtime=health_by_generation.get(expected_binding_generation),
        authority_ids=authority_ids,
    )


def _validate_families(
    value: object,
    *,
    owner_id: str,
    workspace_id: str,
    run_id: str,
    sandbox_id: str,
    operation_ids: set[str],
) -> None:
    if not isinstance(value, list) or len(value) != len(_FAMILIES):
        raise P73AcceptanceReceiptError("p73_acceptance_family_set_invalid")
    seen: set[str] = set()
    request_shas: set[str] = set()
    proposal_ids: set[str] = set()
    review_ids: set[str] = set()
    authority_ids: set[str] = set()
    runtime_ids: set[str] = set()
    workload_identities: set[str] = set()
    for item_value in value:
        item = _exact(
            item_value,
            {
                "acceptance_run_id",
                "component_id",
                "family",
                "lifecycle",
                "sandbox_instance_id",
                "versions",
                "workspace_id",
            },
            "p73_acceptance_family_fields_invalid",
        )
        family = item["family"]
        if family not in _FAMILIES or family in seen or item["component_id"] != _FAMILIES[family]:
            raise P73AcceptanceReceiptError("p73_acceptance_family_set_invalid")
        if (
            item["workspace_id"] != workspace_id
            or item["acceptance_run_id"] != run_id
            or item["sandbox_instance_id"] != sandbox_id
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
        versions = _validate_versions(item["versions"])
        lifecycle = item["lifecycle"]
        if not isinstance(lifecycle, list) or len(lifecycle) != len(_LIFECYCLE):
            raise P73AcceptanceReceiptError("p73_acceptance_lifecycle_sequence_invalid")
        health_by_generation: dict[int, tuple[str, str]] = {}
        for step, (
            operation,
            version,
            generation,
            expected_revision,
            result_revision,
        ) in zip(lifecycle, _LIFECYCLE, strict=True):
            _validate_lifecycle_step(
                step,
                expected_operation=operation,
                expected_version=version,
                expected_binding_generation=generation,
                expected_revision=expected_revision,
                result_revision=result_revision,
                version_digests=versions[version],
                owner_id=owner_id,
                workspace_id=workspace_id,
                run_id=run_id,
                sandbox_id=sandbox_id,
                operation_ids=operation_ids,
                request_shas=request_shas,
                proposal_ids=proposal_ids,
                review_ids=review_ids,
                authority_ids=authority_ids,
                health_by_generation=health_by_generation,
                runtime_ids=runtime_ids,
                workload_identities=workload_identities,
            )
        if set(health_by_generation) != {1, 2, 3}:
            raise P73AcceptanceReceiptError("p73_acceptance_health_generation_set_invalid")
        seen.add(family)
    if seen != set(_FAMILIES):
        raise P73AcceptanceReceiptError("p73_acceptance_family_set_invalid")


def _validate_operation_id(value: object, operation_ids: set[str]) -> None:
    operation_id = _matching_string(value, _UUID, "p73_acceptance_operation_id_invalid")
    if operation_id in operation_ids:
        raise P73AcceptanceReceiptError("p73_acceptance_operation_id_duplicate")
    operation_ids.add(operation_id)


def _read_regular_file(
    path: Path,
    *,
    maximum: int,
    capture: bool,
    unavailable_code: str = "p73_acceptance_artifact_unavailable",
    identity_code: str = "p73_acceptance_artifact_identity_invalid",
    size_code: str = "p73_acceptance_artifact_size_invalid",
) -> tuple[int, str, bytes | None]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise P73AcceptanceReceiptError(unavailable_code) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or bool(getattr(before, "st_file_attributes", 0) & _REPARSE_FLAG)
        or before.st_nlink != 1
        or not 0 < before.st_size <= maximum
    ):
        raise P73AcceptanceReceiptError(identity_code)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_artifact_identity_changed")
        digest = hashlib.sha256()
        chunks: list[bytes] | None = [] if capture else None
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise P73AcceptanceReceiptError(size_code)
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        if size != before.st_size or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise P73AcceptanceReceiptError("p73_acceptance_artifact_identity_changed")
        raw = None if chunks is None else b"".join(chunks)
        return size, digest.hexdigest(), raw
    except OSError as exc:
        raise P73AcceptanceReceiptError(unavailable_code) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_directory(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise P73AcceptanceReceiptError(code) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
    ):
        raise P73AcceptanceReceiptError(code)


def _validate_parent_chain(root: Path, path: Path, code: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise P73AcceptanceReceiptError(code) from exc
    current = path.parent
    while True:
        _validate_directory(current, code)
        if current == root:
            return
        parent = current.parent
        if parent == current:
            raise P73AcceptanceReceiptError(code)
        current = parent


def _validate_component_bundle_binding(  # noqa: C901
    receipt: dict[str, Any],
    *,
    artifact_root: Path,
    build_report_component_bundle: object,
    runtime_manifest_raw: bytes,
) -> None:
    component_root = artifact_root / "payload" / "runtime" / "components"
    _validate_parent_chain(
        artifact_root,
        component_root / "index.json",
        "p73_acceptance_component_bundle_invalid",
    )
    validator_path = Path(__file__).with_name("export_p7_3_component_bundles.py")
    spec = importlib.util.spec_from_file_location(
        "omnibase_p73_acceptance_component_bundle_validator", validator_path
    )
    if spec is None or spec.loader is None:
        raise P73AcceptanceReceiptError("p73_acceptance_component_bundle_invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        bundle_report = module.validate_component_bundle(component_root)
    except Exception as exc:
        raise P73AcceptanceReceiptError("p73_acceptance_component_bundle_invalid") from exc
    if build_report_component_bundle != bundle_report:
        raise P73AcceptanceReceiptError("p73_acceptance_build_report_component_bundle_invalid")
    try:
        runtime_manifest = json.loads(runtime_manifest_raw)
        runtime_canonical = canonical_json(runtime_manifest)
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise P73AcceptanceReceiptError("p73_acceptance_runtime_manifest_invalid") from exc
    if (
        not isinstance(runtime_manifest, dict)
        or set(runtime_manifest) != {"entrypoint", "files", "schemaVersion"}
        or runtime_manifest["schemaVersion"] != 1
        or not isinstance(runtime_manifest["entrypoint"], str)
        or not runtime_manifest["entrypoint"]
        or not isinstance(runtime_manifest["files"], list)
        or runtime_manifest_raw != runtime_canonical
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_runtime_manifest_invalid")
    components: list[tuple[str, int, str]] = []
    folded_paths: set[str] = set()
    for item in runtime_manifest["files"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or not isinstance(item["path"], str)
            or type(item["size"]) is not int
            or item["size"] < 0
            or not isinstance(item["sha256"], str)
            or _SHA256.fullmatch(item["sha256"]) is None
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_runtime_manifest_invalid")
        if not item["path"].startswith("components/"):
            continue
        relative = item["path"].removeprefix("components/")
        if (
            not relative
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or relative.casefold() in folded_paths
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_runtime_component_set_invalid")
        folded_paths.add(relative.casefold())
        components.append((relative, item["size"], item["sha256"]))
    ordered = sorted(components, key=lambda item: (item[0].casefold(), item[0]))
    tree_raw = b"".join(
        relative.encode("utf-8")
        + b"\0"
        + str(size).encode("ascii")
        + b"\0"
        + digest.encode("ascii")
        + b"\n"
        for relative, size, digest in ordered
    )
    runtime_projection = {
        "file_count": len(ordered),
        "total_bytes": sum(size for _, size, _ in ordered),
        "tree_sha256": hashlib.sha256(tree_raw).hexdigest(),
    }
    if runtime_projection != {
        "file_count": bundle_report.get("file_count"),
        "total_bytes": bundle_report.get("output_bytes"),
        "tree_sha256": bundle_report.get("tree_sha256"),
    }:
        raise P73AcceptanceReceiptError("p73_acceptance_runtime_component_set_invalid")
    _, _, index_raw = _read_regular_file(
        component_root / "index.json", maximum=MAX_ARTIFACT_BYTES, capture=True
    )
    assert index_raw is not None
    try:
        index = json.loads(index_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P73AcceptanceReceiptError("p73_acceptance_component_bundle_index_invalid") from exc
    actual_versions = {
        (item["component_id"], item["version"]): (
            item["manifest_sha256"],
            item["package_sha256"],
        )
        for item in index["packages"]
    }
    claimed_versions = {
        (family["component_id"], version["version"]): (
            version["manifest_sha256"],
            version["package_sha256"],
        )
        for family in receipt["families"]
        for version in family["versions"]
    }
    if claimed_versions != actual_versions:
        raise P73AcceptanceReceiptError("p73_acceptance_component_version_binding_invalid")


def _validate_actual_artifacts(
    receipt: dict[str, Any], *, artifact_root: Path, expected_source_commit: str
) -> None:
    expected_commit = _matching_string(
        expected_source_commit,
        _COMMIT,
        "p73_acceptance_expected_source_commit_invalid",
    )
    _validate_directory(artifact_root, "p73_acceptance_artifact_root_invalid")
    artifact_paths = {
        "setup_exe": artifact_root / "release" / "OmniBase-1.0.0-windows-x64-setup.exe",
        "msi": artifact_root / "release" / "OmniBase-1.0.0-windows-x64.msi",
        "runtime_manifest": artifact_root / "payload" / "runtime" / "runtime-manifest.json",
        "build_report": artifact_root / "desktop-build-report.json",
    }
    actual: dict[str, tuple[int, str]] = {}
    captured: dict[str, bytes] = {}
    for key, path in artifact_paths.items():
        _validate_parent_chain(artifact_root, path, "p73_acceptance_artifact_root_invalid")
        capture = key in {"build_report", "runtime_manifest"}
        maximum = (
            MAX_BUILD_REPORT_BYTES
            if key == "build_report"
            else MAX_RUNTIME_MANIFEST_BYTES
            if key == "runtime_manifest"
            else MAX_ARTIFACT_BYTES
        )
        size, digest, raw = _read_regular_file(path, maximum=maximum, capture=capture)
        actual[key] = (size, digest)
        if raw is not None:
            captured[key] = raw
        claimed = receipt["artifacts"][key]
        if claimed["size"] != size or claimed["sha256"] != digest:
            raise P73AcceptanceReceiptError("p73_acceptance_artifact_digest_mismatch")
    build_report_raw = captured["build_report"]
    try:
        report = json.loads(build_report_raw)
        report_canonical = canonical_json(report)
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise P73AcceptanceReceiptError("p73_acceptance_build_report_invalid") from exc
    if not isinstance(report, dict) or build_report_raw != report_canonical:
        raise P73AcceptanceReceiptError("p73_acceptance_build_report_invalid")
    source = receipt["source"]
    if (
        source["commit"] != expected_commit
        or report.get("source_commit") != expected_commit
        or report.get("source_clean") is not True
        or report.get("source_mode") != "clean-release"
        or report.get("source_tree_sha256") != source["tree_sha256"]
        or report.get("schema_version") != 1
        or report.get("product") != "OmniBase"
        or report.get("version") != "1.0.0"
        or report.get("platform") != "windows-x64"
        or report.get("production_ready") is not False
        or report.get("authenticode_verified") is not False
        or report.get("clean_windows_lifecycle_verified") is not False
        or report.get("required_product_journeys_verified") is not False
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_build_report_binding_invalid")
    _validate_component_bundle_binding(
        receipt,
        artifact_root=artifact_root,
        build_report_component_bundle=report.get("component_bundle"),
        runtime_manifest_raw=captured["runtime_manifest"],
    )
    runtime_pin = report.get("runtime_manifest_pin")
    if (
        not isinstance(runtime_pin, dict)
        or runtime_pin.get("manifest_sha256") != actual["runtime_manifest"][1]
        or runtime_pin.get("packaged_asar_verified") is not True
        or runtime_pin.get("placeholder_absent") is not True
        or runtime_pin.get("staged_dist_verified") is not True
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_runtime_manifest_binding_invalid")
    report_artifacts = report.get("artifacts")
    expected_report_artifacts = [
        {
            "kind": "burn_exe",
            "path": "release/OmniBase-1.0.0-windows-x64-setup.exe",
            "size": actual["setup_exe"][0],
            "sha256": actual["setup_exe"][1],
        },
        {
            "kind": "msi",
            "path": "release/OmniBase-1.0.0-windows-x64.msi",
            "size": actual["msi"][0],
            "sha256": actual["msi"][1],
        },
    ]
    if report_artifacts != expected_report_artifacts:
        raise P73AcceptanceReceiptError("p73_acceptance_build_report_artifacts_invalid")


def _validate_actual_screenshots(receipt: dict[str, Any], *, evidence_root: Path) -> None:
    _validate_directory(evidence_root, "p73_acceptance_evidence_root_invalid")
    for item in receipt["screenshots"]["items"]:
        path = evidence_root.joinpath(*PurePosixPath(item["path"]).parts)
        _validate_parent_chain(evidence_root, path, "p73_acceptance_evidence_root_invalid")
        _, digest, raw = _read_regular_file(path, maximum=MAX_SCREENSHOT_BYTES, capture=True)
        assert raw is not None
        if digest != item["sha256"]:
            raise P73AcceptanceReceiptError("p73_acceptance_screenshot_digest_mismatch")
        if (
            len(raw) < 24
            or raw[:8] != b"\x89PNG\r\n\x1a\n"
            or raw[8:12] != b"\x00\x00\x00\r"
            or raw[12:16] != b"IHDR"
            or int.from_bytes(raw[16:20], "big") != item["width"]
            or int.from_bytes(raw[20:24], "big") != item["height"]
        ):
            raise P73AcceptanceReceiptError("p73_acceptance_screenshot_file_invalid")


def validate_receipt(value: object) -> dict[str, object]:  # noqa: C901
    receipt = _exact(
        value,
        {
            "artifacts",
            "claims",
            "effect_closure",
            "emergency_stop",
            "evidence_class",
            "families",
            "p7_1_regression",
            "restart_recovery",
            "run_window",
            "schema",
            "scope",
            "screenshots",
            "source",
            "target",
            "uninstall",
            "workspace_isolation",
        },
        "p73_acceptance_receipt_fields_invalid",
    )
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise P73AcceptanceReceiptError("p73_acceptance_schema_invalid")
    if receipt["evidence_class"] != "engineering_only":
        raise P73AcceptanceReceiptError("p73_acceptance_evidence_class_invalid")

    run_window = _exact(
        receipt["run_window"],
        {"completed_at", "started_at"},
        "p73_acceptance_run_window_invalid",
    )
    started = _timestamp(run_window["started_at"], "p73_acceptance_run_window_invalid")
    completed = _timestamp(run_window["completed_at"], "p73_acceptance_run_window_invalid")
    if completed <= started:
        raise P73AcceptanceReceiptError("p73_acceptance_run_window_invalid")

    source = _exact(
        receipt["source"],
        {"clean", "commit", "mode", "tree_sha256"},
        "p73_acceptance_source_fields_invalid",
    )
    _matching_string(source["commit"], _COMMIT, "p73_acceptance_source_commit_invalid")
    _matching_string(source["tree_sha256"], _SHA256, "p73_acceptance_source_tree_invalid")
    _strict_bool(source["clean"], True, "p73_acceptance_clean_source_required")
    if source["mode"] != "clean-release":
        raise P73AcceptanceReceiptError("p73_acceptance_clean_source_required")

    artifacts = _exact(
        receipt["artifacts"],
        {"build_report", "msi", "runtime_manifest", "setup_exe"},
        "p73_acceptance_artifacts_fields_invalid",
    )
    _artifact(
        artifacts["setup_exe"],
        expected_name="OmniBase-1.0.0-windows-x64-setup.exe",
        expected_kind="burn_exe",
    )
    _artifact(
        artifacts["msi"],
        expected_name="OmniBase-1.0.0-windows-x64.msi",
        expected_kind="msi",
    )
    _artifact(
        artifacts["runtime_manifest"],
        expected_name="runtime-manifest.json",
        expected_kind="runtime_manifest",
    )
    _artifact(
        artifacts["build_report"],
        expected_name="desktop-build-report.json",
        expected_kind="build_report",
    )

    target = _exact(
        receipt["target"],
        {
            "disposable",
            "host_configuration_modified",
            "instance_count",
            "instance_ids",
            "kind",
            "os_version",
            "preexisting_install",
            "user",
        },
        "p73_acceptance_target_fields_invalid",
    )
    if target["kind"] != "windows_sandbox":
        raise P73AcceptanceReceiptError("p73_acceptance_target_kind_invalid")
    _strict_int(
        target["instance_count"],
        minimum=1,
        maximum=1,
        code="p73_acceptance_single_instance_required",
    )
    instance_ids = target["instance_ids"]
    if not isinstance(instance_ids, list) or len(instance_ids) != 1:
        raise P73AcceptanceReceiptError("p73_acceptance_single_instance_required")
    sandbox_id = _matching_string(instance_ids[0], _UUID, "p73_acceptance_sandbox_identity_invalid")
    _strict_bool(target["disposable"], True, "p73_acceptance_target_not_disposable")
    _strict_bool(
        target["preexisting_install"],
        False,
        "p73_acceptance_preexisting_install_forbidden",
    )
    _strict_bool(
        target["host_configuration_modified"],
        False,
        "p73_acceptance_host_configuration_modified",
    )
    for field in ("os_version", "user"):
        if not isinstance(target[field], str) or not target[field].strip():
            raise P73AcceptanceReceiptError("p73_acceptance_target_identity_invalid")

    scope = _exact(
        receipt["scope"],
        {"acceptance_run_id", "owner_id", "sandbox_instance_id", "workspace_id"},
        "p73_acceptance_scope_fields_invalid",
    )
    owner_id = _matching_string(scope["owner_id"], _UUID, "p73_acceptance_owner_identity_invalid")
    workspace_id = _matching_string(
        scope["workspace_id"], _UUID, "p73_acceptance_workspace_identity_invalid"
    )
    run_id = _matching_string(
        scope["acceptance_run_id"], _UUID, "p73_acceptance_run_identity_invalid"
    )
    if scope["sandbox_instance_id"] != sandbox_id:
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")

    operation_ids: set[str] = set()
    _validate_families(
        receipt["families"],
        owner_id=owner_id,
        workspace_id=workspace_id,
        run_id=run_id,
        sandbox_id=sandbox_id,
        operation_ids=operation_ids,
    )

    isolation = _exact(
        receipt["workspace_isolation"],
        {
            "acceptance_run_id",
            "automatic_replay",
            "cross_workspace_action_rejected",
            "first_frame_projection_empty",
            "operation_id",
            "other_workspace_id",
            "sandbox_instance_id",
            "source_workspace_id",
            "standard_workbench_available",
            "status",
        },
        "p73_acceptance_workspace_isolation_fields_invalid",
    )
    other_workspace_id = _matching_string(
        isolation["other_workspace_id"],
        _UUID,
        "p73_acceptance_workspace_identity_invalid",
    )
    if (
        isolation["status"] != "succeeded"
        or isolation["source_workspace_id"] != workspace_id
        or other_workspace_id == workspace_id
        or isolation["acceptance_run_id"] != run_id
        or isolation["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
    _validate_operation_id(isolation["operation_id"], operation_ids)
    for field in (
        "cross_workspace_action_rejected",
        "first_frame_projection_empty",
        "standard_workbench_available",
    ):
        _strict_bool(isolation[field], True, "p73_acceptance_workspace_isolation_incomplete")
    _strict_bool(
        isolation["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )

    effect_closure = _exact(
        receipt["effect_closure"],
        {
            "automatic_replay",
            "pending_count",
            "reconciliation_required",
            "unknown_count",
        },
        "p73_acceptance_effect_closure_fields_invalid",
    )
    _strict_int(
        effect_closure["pending_count"],
        minimum=0,
        maximum=0,
        code="p73_acceptance_pending_effect_forbidden",
    )
    _strict_int(
        effect_closure["unknown_count"],
        minimum=0,
        maximum=0,
        code="p73_acceptance_unknown_effect_forbidden",
    )
    _strict_bool(
        effect_closure["reconciliation_required"],
        False,
        "p73_acceptance_reconciliation_open",
    )
    _strict_bool(
        effect_closure["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )

    emergency = _exact(
        receipt["emergency_stop"],
        {
            "acceptance_run_id",
            "audit_available",
            "automatic_replay",
            "non_core_fenced",
            "operation_id",
            "pending_effects",
            "sandbox_instance_id",
            "settings_available",
            "standard_workbench_available",
            "status",
            "unknown_effects",
            "workspace_id",
        },
        "p73_acceptance_emergency_stop_fields_invalid",
    )
    if (
        emergency["status"] != "succeeded"
        or emergency["workspace_id"] != workspace_id
        or emergency["acceptance_run_id"] != run_id
        or emergency["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
    _validate_operation_id(emergency["operation_id"], operation_ids)
    for field in (
        "non_core_fenced",
        "standard_workbench_available",
        "settings_available",
        "audit_available",
    ):
        _strict_bool(emergency[field], True, "p73_acceptance_emergency_stop_incomplete")
    for field, code in (
        ("pending_effects", "p73_acceptance_pending_effect_forbidden"),
        ("unknown_effects", "p73_acceptance_unknown_effect_forbidden"),
    ):
        _strict_int(emergency[field], minimum=0, maximum=0, code=code)
    _strict_bool(
        emergency["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )

    restart = _exact(
        receipt["restart_recovery"],
        {
            "acceptance_run_id",
            "active_generations_revalidated",
            "automatic_replay",
            "operation_id",
            "pending_effects",
            "recovered_to_committed_state",
            "sandbox_instance_id",
            "stale_runtimes_fenced",
            "status",
            "unknown_effects",
            "workspace_id",
        },
        "p73_acceptance_restart_recovery_fields_invalid",
    )
    if (
        restart["status"] != "succeeded"
        or restart["workspace_id"] != workspace_id
        or restart["acceptance_run_id"] != run_id
        or restart["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
    _validate_operation_id(restart["operation_id"], operation_ids)
    for field in (
        "active_generations_revalidated",
        "recovered_to_committed_state",
        "stale_runtimes_fenced",
    ):
        _strict_bool(restart[field], True, "p73_acceptance_restart_recovery_incomplete")
    for field, code in (
        ("pending_effects", "p73_acceptance_pending_effect_forbidden"),
        ("unknown_effects", "p73_acceptance_unknown_effect_forbidden"),
    ):
        _strict_int(restart[field], minimum=0, maximum=0, code=code)
    _strict_bool(
        restart["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )

    regression = _exact(
        receipt["p7_1_regression"],
        {
            "acceptance_run_id",
            "automatic_replay",
            "file_list_read",
            "logical_paths_only",
            "mcp_read_only",
            "mcp_tools",
            "operation_id",
            "owner_folder_gesture",
            "sandbox_instance_id",
            "status",
            "utf8_file_read",
            "workspace_id",
            "write_attempt_rejected",
        },
        "p73_acceptance_p71_regression_fields_invalid",
    )
    if (
        regression["status"] != "succeeded"
        or regression["workspace_id"] != workspace_id
        or regression["acceptance_run_id"] != run_id
        or regression["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
    _validate_operation_id(regression["operation_id"], operation_ids)
    for field in (
        "owner_folder_gesture",
        "logical_paths_only",
        "file_list_read",
        "utf8_file_read",
        "mcp_read_only",
        "write_attempt_rejected",
    ):
        _strict_bool(regression[field], True, "p73_acceptance_p71_regression_incomplete")
    if regression["mcp_tools"] != list(_MCP_TOOLS):
        raise P73AcceptanceReceiptError("p73_acceptance_mcp_tool_set_invalid")
    _strict_bool(
        regression["automatic_replay"],
        False,
        "p73_acceptance_automatic_replay_forbidden",
    )

    uninstall = _exact(
        receipt["uninstall"],
        {
            "acceptance_run_id",
            "application_files_removed",
            "exit_code",
            "listening_ports_after_uninstall",
            "registration_removed",
            "remaining_processes",
            "sandbox_instance_id",
            "shortcuts_removed",
            "status",
            "user_data_retained",
            "workspace_id",
        },
        "p73_acceptance_uninstall_fields_invalid",
    )
    if (
        uninstall["status"] != "succeeded"
        or uninstall["workspace_id"] != workspace_id
        or uninstall["acceptance_run_id"] != run_id
        or uninstall["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_scope_identity_drift")
    _strict_int(
        uninstall["exit_code"],
        minimum=0,
        maximum=0,
        code="p73_acceptance_uninstall_failed",
    )
    for field in (
        "application_files_removed",
        "registration_removed",
        "shortcuts_removed",
        "user_data_retained",
    ):
        _strict_bool(uninstall[field], True, "p73_acceptance_uninstall_incomplete")
    if uninstall["remaining_processes"] != []:
        raise P73AcceptanceReceiptError("p73_acceptance_process_cleanup_incomplete")
    if uninstall["listening_ports_after_uninstall"] != []:
        raise P73AcceptanceReceiptError("p73_acceptance_port_cleanup_incomplete")

    screenshots = _exact(
        receipt["screenshots"],
        {
            "acceptance_run_id",
            "items",
            "review",
            "sandbox_instance_id",
            "status",
            "workspace_id",
        },
        "p73_acceptance_screenshot_fields_invalid",
    )
    if (
        screenshots["status"] != "captured"
        or screenshots["review"] != "pending_visual_review"
        or screenshots["workspace_id"] != workspace_id
        or screenshots["acceptance_run_id"] != run_id
        or screenshots["sandbox_instance_id"] != sandbox_id
    ):
        raise P73AcceptanceReceiptError("p73_acceptance_screenshot_posture_invalid")
    items = screenshots["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_SCREENSHOTS:
        raise P73AcceptanceReceiptError("p73_acceptance_screenshot_set_invalid")
    screenshot_paths: set[str] = set()
    for item_value in items:
        item = _exact(
            item_value,
            {"height", "path", "sha256", "width"},
            "p73_acceptance_screenshot_item_invalid",
        )
        path = _safe_relative_path(item["path"], "p73_acceptance_screenshot_path_invalid")
        if path in screenshot_paths:
            raise P73AcceptanceReceiptError("p73_acceptance_screenshot_path_duplicate")
        screenshot_paths.add(path)
        _matching_string(item["sha256"], _SHA256, "p73_acceptance_screenshot_digest_invalid")
        for dimension in ("width", "height"):
            _strict_int(
                item[dimension],
                minimum=1,
                maximum=16384,
                code="p73_acceptance_screenshot_dimension_invalid",
            )

    claims = _exact(
        receipt["claims"],
        {
            "authenticode_verified",
            "human_visual_review_verified",
            "production_ready",
            "publisher_signature_verified",
            "release_authorized",
        },
        "p73_acceptance_claim_fields_invalid",
    )
    for field in claims:
        _strict_bool(claims[field], False, "p73_acceptance_claim_forbidden")

    return {
        "authenticode_verified": False,
        "engineering_evidence_complete": False,
        "evidence_class": "engineering_only",
        "external_evidence_verified": False,
        "family_count": len(_FAMILIES),
        "human_visual_review_verified": False,
        "production_ready": False,
        "release_authorized": False,
        "sandbox_instance_count": 1,
        "valid": True,
        "visual_review": "pending_visual_review",
    }


def load_and_validate_receipt(
    path: Path,
    *,
    artifact_root: Path,
    evidence_root: Path,
    expected_source_commit: str,
) -> dict[str, object]:
    _, _, raw = _read_regular_file(
        path,
        maximum=MAX_RECEIPT_BYTES,
        capture=True,
        unavailable_code="p73_acceptance_receipt_unavailable",
        identity_code="p73_acceptance_receipt_identity_invalid",
        size_code="p73_acceptance_receipt_identity_invalid",
    )
    assert raw is not None
    try:
        value = json.loads(raw)
        canonical = canonical_json(value)
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise P73AcceptanceReceiptError("p73_acceptance_receipt_json_invalid") from exc
    if raw != canonical:
        raise P73AcceptanceReceiptError("p73_acceptance_receipt_not_canonical")
    result = validate_receipt(value)
    _validate_actual_artifacts(
        value,
        artifact_root=artifact_root,
        expected_source_commit=expected_source_commit,
    )
    _validate_actual_screenshots(value, evidence_root=evidence_root)
    result["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    result["engineering_evidence_complete"] = True
    result["external_evidence_verified"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    args = parser.parse_args()
    try:
        result = load_and_validate_receipt(
            args.receipt,
            artifact_root=args.artifact_root,
            evidence_root=args.evidence_root,
            expected_source_commit=args.expected_source_commit,
        )
    except P73AcceptanceReceiptError as exc:
        print(json.dumps({"error": str(exc), "valid": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
