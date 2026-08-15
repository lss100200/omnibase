"""Strict redacted receipt validation for the live P6.4 practice matrix."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

RECEIPT_SCHEMA = "omnibase.p6-4.personal-agent-practice.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
_SECRET_VALUE = re.compile(r"(?i)(?:^|[^a-z0-9])sk-[a-z0-9_-]{16,}(?:$|[^a-z0-9])")
_FORBIDDEN_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "document_text",
        "file_content",
        "full_answer",
        "jwt",
        "password",
        "physical_path",
        "prompt",
        "provider_raw_error",
        "raw_provider_error",
        "refresh_token",
        "secret",
        "source_text",
        "token",
    }
)
_EXPECTED_JOURNEYS = {
    "rag_single": ("rag", 1),
    "rag_three": ("rag", 3),
    "artifact_single": ("artifact", 1),
    "artifact_four": ("artifact", 4),
    "workspace_single": ("workspace", 1),
    "workspace_six": ("workspace", 6),
}
_EXPECTED_ROLES = {
    "rag_single": ("parent",),
    "rag_three": ("data", "qa", "parent"),
    "artifact_single": ("parent",),
    "artifact_four": ("product", "ux", "frontend", "parent"),
    "workspace_single": ("parent",),
    "workspace_six": ("product", "frontend", "backend", "security", "qa", "parent"),
}
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
    "cache_miss_input_tokens",
)


class PracticeReceiptError(ValueError):
    """The receipt cannot support a P6.4 production acceptance claim."""


def _record(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PracticeReceiptError(f"{label} must be an object")
    return value


def _list(value: object, *, label: str) -> Sequence[object]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise PracticeReceiptError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], *, label: str) -> None:
    observed = set(value)
    if observed != expected:
        raise PracticeReceiptError(
            f"{label} keys drifted: missing={sorted(expected - observed)}, "
            f"unknown={sorted(observed - expected)}"
        )


def _safe_receipt(value: object, *, label: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).lower()
            if key in _FORBIDDEN_KEYS or key.endswith(("_api_key", "_password", "_secret")):
                raise PracticeReceiptError(f"{label} contains forbidden key {raw_key!r}")
            _safe_receipt(item, label=f"{label}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            _safe_receipt(item, label=f"{label}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "bearer ",
                "postgresql://",
                "postgresql+psycopg://",
                "redis://",
                "-----begin private key-----",
            )
        ):
            raise PracticeReceiptError(f"{label} contains a secret or connection locator")
        if re.match(r"^(?:[a-zA-Z]:[\\/]|/|\\\\)", value):
            raise PracticeReceiptError(f"{label} contains a physical path")
        if _SECRET_VALUE.search(value):
            raise PracticeReceiptError(f"{label} contains secret-shaped key material")


def _utc_timestamp(value: object, *, label: str) -> None:
    if not isinstance(value, str):
        raise PracticeReceiptError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PracticeReceiptError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise PracticeReceiptError(f"{label} must be UTC")


def _bool(value: object, *, label: str) -> None:
    if not isinstance(value, bool):
        raise PracticeReceiptError(f"{label} must be boolean")


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PracticeReceiptError(f"{label} must be a non-negative integer")
    return value


def _validate_gates(receipt: Mapping[str, object]) -> None:
    posture = _record(receipt.get("posture"), label="receipt.posture")
    _exact_keys(posture, {"before", "during", "after"}, label="receipt.posture")
    before = _record(posture["before"], label="receipt.posture.before")
    after = _record(posture["after"], label="receipt.posture.after")
    closed = {
        "personal_practice_enabled": False,
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "enterprise_multi_agent_enabled": False,
        "mcp_runtime_enabled": False,
    }
    for label, observed in (("before", before), ("after", after)):
        if dict(observed) != closed:
            raise PracticeReceiptError(f"receipt.posture.{label} is not fully closed")
    during = _record(posture["during"], label="receipt.posture.during")
    expected_during: dict[str, object] = {
        "environment": "production",
        "runtime_profile": "personal_single_owner",
        "personal_practice_enabled": True,
        "agent_runtime_enabled": True,
        "agent_planner_enabled": False,
        "enterprise_multi_agent_enabled": False,
        "mcp_runtime_enabled": False,
        "max_concurrent_invocations": 1,
    }
    if dict(during) != expected_during:
        raise PracticeReceiptError("receipt.posture.during is not the exact P6.4 window")


def _validate_citations(value: object, *, label: str) -> None:
    citations = _list(value, label=label)
    if len(citations) > 8:
        raise PracticeReceiptError(f"{label} exceeds the citation budget")
    for offset, raw in enumerate(citations, start=1):
        citation = _record(raw, label=f"{label}[{offset - 1}]")
        _exact_keys(
            citation,
            {"index", "chunk_id", "document_id", "page_number"},
            label=f"{label}[{offset - 1}]",
        )
        if citation["index"] != offset:
            raise PracticeReceiptError(f"{label} index order drifted")
        for key in ("chunk_id", "document_id"):
            item = citation[key]
            if not isinstance(item, str) or not item or len(item) > 128:
                raise PracticeReceiptError(f"{label}.{key} is invalid")
        page = _nonnegative_int(citation["page_number"], label=f"{label}.page_number")
        if page < 1:
            raise PracticeReceiptError(f"{label}.page_number must be positive")


def _validate_node(
    raw: object,
    *,
    ordinal: int,
    role: str,
    expected_model_id: str,
    label: str,
) -> Mapping[str, object]:
    node = _record(raw, label=label)
    _exact_keys(
        node,
        {
            "ordinal",
            "role",
            "invocation_id",
            "task_id",
            "requested_model_id",
            "actual_model_id",
            "usage",
            "latency_ms",
            "answer_sha256",
            "citations",
        },
        label=label,
    )
    if node["ordinal"] != ordinal or node["role"] != role:
        raise PracticeReceiptError(f"{label} identity/order drifted")
    for key in ("invocation_id", "task_id"):
        value = node[key]
        if not isinstance(value, str) or not value or len(value) > 128:
            raise PracticeReceiptError(f"{label}.{key} is invalid")
    requested = node["requested_model_id"]
    actual = node["actual_model_id"]
    if (
        not isinstance(requested, str)
        or _MODEL_ID.fullmatch(requested) is None
        or actual != requested
        or requested != expected_model_id
    ):
        raise PracticeReceiptError(f"{label} model identity is unproved")
    usage = _record(node["usage"], label=f"{label}.usage")
    _exact_keys(usage, set(_USAGE_FIELDS), label=f"{label}.usage")
    parsed_usage = {
        key: _nonnegative_int(usage[key], label=f"{label}.usage.{key}") for key in _USAGE_FIELDS
    }
    if parsed_usage["total_tokens"] < (
        parsed_usage["input_tokens"] + parsed_usage["output_tokens"]
    ):
        raise PracticeReceiptError(f"{label}.usage.total_tokens is inconsistent")
    _nonnegative_int(node["latency_ms"], label=f"{label}.latency_ms")
    if (
        not isinstance(node["answer_sha256"], str)
        or _SHA256.fullmatch(node["answer_sha256"]) is None
    ):
        raise PracticeReceiptError(f"{label}.answer_sha256 is invalid")
    _validate_citations(node["citations"], label=f"{label}.citations")
    return node


def _validate_rag_result(value: object, *, label: str) -> None:
    result = _record(value, label=label)
    _exact_keys(
        result,
        {
            "browser_upload_completed",
            "workspace_binding_verified",
            "index_ready",
            "decoy_workspace_excluded",
            "expected_fact_count",
            "supported_claim_count",
            "unsupported_claim_count",
            "missing_fact_count",
            "wrong_chunk_count",
            "unknown_chunk_count",
            "statement_mismatch_count",
            "fact_precision",
            "fact_recall",
            "citation_precision",
            "citation_recall",
        },
        label=label,
    )
    for key in (
        "browser_upload_completed",
        "workspace_binding_verified",
        "index_ready",
        "decoy_workspace_excluded",
    ):
        if result[key] is not True:
            raise PracticeReceiptError(f"{label}.{key} is not proved")
    expected_count = _nonnegative_int(
        result["expected_fact_count"], label=f"{label}.expected_fact_count"
    )
    supported = _nonnegative_int(
        result["supported_claim_count"], label=f"{label}.supported_claim_count"
    )
    if expected_count < 1 or supported != expected_count:
        raise PracticeReceiptError(f"{label} did not support every expected fact")
    for key in (
        "unsupported_claim_count",
        "missing_fact_count",
        "wrong_chunk_count",
        "unknown_chunk_count",
        "statement_mismatch_count",
    ):
        if result[key] != 0:
            raise PracticeReceiptError(f"{label}.{key} must be zero")
    for key in ("fact_precision", "fact_recall", "citation_precision", "citation_recall"):
        if result[key] != 1.0:
            raise PracticeReceiptError(f"{label}.{key} must equal 1.0")


def _validate_artifact_result(value: object, *, label: str) -> None:
    result = _record(value, label=label)
    _exact_keys(
        result,
        {
            "artifact_type",
            "filename",
            "media_type",
            "byte_length",
            "sha256",
            "digest_verified",
            "offline_dependency_free",
            "dom_loaded",
            "clock_time_changed",
        },
        label=label,
    )
    if result["artifact_type"] not in {"clock_html", "slides_html"}:
        raise PracticeReceiptError(f"{label}.artifact_type is unsupported")
    expected_filename = "clock.html" if result["artifact_type"] == "clock_html" else "slides.html"
    if (
        result["filename"] != expected_filename
        or result["media_type"] != "text/html; charset=utf-8"
    ):
        raise PracticeReceiptError(f"{label} misrepresents the artifact")
    byte_length = _nonnegative_int(result["byte_length"], label=f"{label}.byte_length")
    if byte_length < 1 or byte_length > 512 * 1024:
        raise PracticeReceiptError(f"{label}.byte_length is outside the budget")
    if not isinstance(result["sha256"], str) or _SHA256.fullmatch(result["sha256"]) is None:
        raise PracticeReceiptError(f"{label}.sha256 is invalid")
    for key in ("digest_verified", "offline_dependency_free", "dom_loaded"):
        if result[key] is not True:
            raise PracticeReceiptError(f"{label}.{key} is not proved")
    if result["artifact_type"] == "clock_html" and result["clock_time_changed"] is not True:
        raise PracticeReceiptError(f"{label}.clock_time_changed is not proved")
    if result["artifact_type"] == "slides_html" and result["clock_time_changed"] is not None:
        raise PracticeReceiptError(f"{label}.clock_time_changed must be null for slides")


def _validate_workspace_result(value: object, *, label: str) -> None:
    result = _record(value, label=label)
    _exact_keys(
        result,
        {
            "logical_path",
            "before_sha256",
            "after_sha256",
            "tree_before_sha256",
            "tree_applied_sha256",
            "tree_rollback_sha256",
            "disposable_root_verified",
            "cas_applied",
            "post_write_verified",
            "project_check_passed",
            "rollback_verified",
            "original_tree_restored",
        },
        label=label,
    )
    logical_path = result["logical_path"]
    if (
        not isinstance(logical_path, str)
        or not logical_path
        or "\\" in logical_path
        or logical_path.startswith("/")
        or ".." in logical_path.split("/")
    ):
        raise PracticeReceiptError(f"{label}.logical_path is invalid")
    for key in (
        "before_sha256",
        "after_sha256",
        "tree_before_sha256",
        "tree_applied_sha256",
        "tree_rollback_sha256",
    ):
        digest = result[key]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise PracticeReceiptError(f"{label}.{key} is invalid")
    if result["before_sha256"] == result["after_sha256"]:
        raise PracticeReceiptError(f"{label} did not change the target file")
    if result["tree_before_sha256"] != result["tree_rollback_sha256"]:
        raise PracticeReceiptError(f"{label} did not restore the original tree")
    for key in (
        "disposable_root_verified",
        "cas_applied",
        "post_write_verified",
        "project_check_passed",
        "rollback_verified",
        "original_tree_restored",
    ):
        if result[key] is not True:
            raise PracticeReceiptError(f"{label}.{key} is not proved")


def _validate_journey(
    name: str,
    raw: object,
    *,
    expected_model_id: str,
    all_invocation_ids: set[str],
    all_task_ids: set[str],
) -> None:
    journey = _record(raw, label=f"receipt.journeys.{name}")
    _exact_keys(
        journey,
        {
            "scenario",
            "participant_count",
            "roles",
            "provider_call_count",
            "nodes",
            "result",
            "passed",
        },
        label=f"receipt.journeys.{name}",
    )
    expected_scenario, expected_count = _EXPECTED_JOURNEYS[name]
    if journey["scenario"] != expected_scenario or journey["participant_count"] != expected_count:
        raise PracticeReceiptError(f"receipt.journeys.{name} scope drifted")
    if journey["provider_call_count"] != expected_count or journey["passed"] is not True:
        raise PracticeReceiptError(f"receipt.journeys.{name} is incomplete")
    roles = _list(journey["roles"], label=f"receipt.journeys.{name}.roles")
    nodes = _list(journey["nodes"], label=f"receipt.journeys.{name}.nodes")
    if tuple(roles) != _EXPECTED_ROLES[name] or len(nodes) != expected_count:
        raise PracticeReceiptError(f"receipt.journeys.{name} roster is incomplete")
    for ordinal, (role, node) in enumerate(zip(roles, nodes, strict=True), start=1):
        if not isinstance(role, str):
            raise PracticeReceiptError(f"receipt.journeys.{name}.roles is invalid")
        node_record = _validate_node(
            node,
            ordinal=ordinal,
            role=role,
            expected_model_id=expected_model_id,
            label=f"receipt.journeys.{name}.nodes[{ordinal - 1}]",
        )
        invocation_id = str(node_record["invocation_id"])
        task_id = str(node_record["task_id"])
        if invocation_id in all_invocation_ids or task_id in all_task_ids:
            raise PracticeReceiptError(f"receipt.journeys.{name} reuses a durable call identity")
        all_invocation_ids.add(invocation_id)
        all_task_ids.add(task_id)
    if expected_scenario == "rag":
        _validate_rag_result(journey["result"], label=f"receipt.journeys.{name}.result")
    elif expected_scenario == "artifact":
        _validate_artifact_result(journey["result"], label=f"receipt.journeys.{name}.result")
    else:
        _validate_workspace_result(journey["result"], label=f"receipt.journeys.{name}.result")


def validate_personal_practice_receipt(value: object) -> Mapping[str, object]:
    """Return the receipt only when it proves the complete redacted live matrix."""

    _safe_receipt(value)
    receipt = _record(value, label="receipt")
    _exact_keys(
        receipt,
        {
            "schema",
            "generated_at",
            "source_head",
            "provider",
            "posture",
            "journeys",
            "cleanup",
            "production_accepted",
        },
        label="receipt",
    )
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise PracticeReceiptError("receipt schema is unsupported")
    _utc_timestamp(receipt["generated_at"], label="receipt.generated_at")
    if (
        not isinstance(receipt["source_head"], str)
        or _GIT_SHA.fullmatch(receipt["source_head"]) is None
    ):
        raise PracticeReceiptError("receipt.source_head is invalid")
    provider = _record(receipt["provider"], label="receipt.provider")
    _exact_keys(
        provider, {"provider_id", "model_id", "models_preflight_passed"}, label="receipt.provider"
    )
    for key in ("provider_id", "model_id"):
        identifier = provider[key]
        if not isinstance(identifier, str) or _MODEL_ID.fullmatch(identifier) is None:
            raise PracticeReceiptError(f"receipt.provider.{key} is invalid")
    if provider["models_preflight_passed"] is not True:
        raise PracticeReceiptError("receipt.provider models preflight is not proved")
    _validate_gates(receipt)
    journeys = _record(receipt["journeys"], label="receipt.journeys")
    _exact_keys(journeys, set(_EXPECTED_JOURNEYS), label="receipt.journeys")
    all_invocation_ids: set[str] = set()
    all_task_ids: set[str] = set()
    for name in _EXPECTED_JOURNEYS:
        _validate_journey(
            name,
            journeys[name],
            expected_model_id=str(provider["model_id"]),
            all_invocation_ids=all_invocation_ids,
            all_task_ids=all_task_ids,
        )
    cleanup = _record(receipt["cleanup"], label="receipt.cleanup")
    _exact_keys(
        cleanup,
        {
            "disposable_documents_removed",
            "disposable_workspaces_removed",
            "provider_credential_revoked",
            "runtime_canary_closed",
            "all_feature_gates_closed",
        },
        label="receipt.cleanup",
    )
    for key, item in cleanup.items():
        if item is not True:
            raise PracticeReceiptError(f"receipt.cleanup.{key} is not proved")
    if receipt["production_accepted"] is not True:
        raise PracticeReceiptError("receipt does not declare completed production acceptance")
    return receipt


__all__ = [
    "RECEIPT_SCHEMA",
    "PracticeReceiptError",
    "validate_personal_practice_receipt",
]
