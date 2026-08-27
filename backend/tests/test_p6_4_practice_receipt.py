from __future__ import annotations

import copy

import pytest

from omnibase.agent_practice.receipt import (
    RECEIPT_SCHEMA,
    PracticeReceiptError,
    validate_personal_practice_receipt,
)


def _node(journey: str, ordinal: int, role: str) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "role": role,
        "invocation_id": f"invocation-{journey}-{role}-{ordinal}",
        "task_id": f"task-{journey}-{role}-{ordinal}",
        "requested_model_id": "deepseek-v4-flash",
        "actual_model_id": "deepseek-v4-flash",
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "reasoning_tokens": 2,
            "cached_input_tokens": 4,
            "cache_miss_input_tokens": 16,
        },
        "latency_ms": 250,
        "answer_sha256": f"{ordinal:x}".rjust(64, "0"),
        "citations": [
            {
                "index": 1,
                "chunk_id": f"chunk-{role}-{ordinal}",
                "document_id": "document-acceptance",
                "page_number": 1,
            }
        ],
    }


def _journey(
    name: str, scenario: str, roles: list[str], result: dict[str, object]
) -> dict[str, object]:
    return {
        "scenario": scenario,
        "participant_count": len(roles),
        "roles": roles,
        "provider_call_count": len(roles),
        "nodes": [_node(name, index, role) for index, role in enumerate(roles, start=1)],
        "result": result,
        "passed": True,
    }


def _rag_result() -> dict[str, object]:
    return {
        "browser_upload_completed": True,
        "workspace_binding_verified": True,
        "index_ready": True,
        "decoy_workspace_excluded": True,
        "expected_fact_count": 2,
        "supported_claim_count": 2,
        "unsupported_claim_count": 0,
        "missing_fact_count": 0,
        "wrong_chunk_count": 0,
        "unknown_chunk_count": 0,
        "statement_mismatch_count": 0,
        "fact_precision": 1.0,
        "fact_recall": 1.0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
    }


def _artifact_result(*, slides: bool) -> dict[str, object]:
    return {
        "artifact_type": "slides_html" if slides else "clock_html",
        "filename": "slides.html" if slides else "clock.html",
        "media_type": "text/html; charset=utf-8",
        "byte_length": 1024,
        "sha256": "a" * 64,
        "digest_verified": True,
        "offline_dependency_free": True,
        "dom_loaded": True,
        "clock_time_changed": None if slides else True,
    }


def _workspace_result(path: str) -> dict[str, object]:
    return {
        "logical_path": path,
        "before_sha256": "b" * 64,
        "after_sha256": "c" * 64,
        "tree_before_sha256": "d" * 64,
        "tree_applied_sha256": "e" * 64,
        "tree_rollback_sha256": "d" * 64,
        "disposable_root_verified": True,
        "cas_applied": True,
        "post_write_verified": True,
        "project_check_passed": True,
        "rollback_verified": True,
        "original_tree_restored": True,
    }


def _receipt() -> dict[str, object]:
    closed = {
        "personal_practice_enabled": False,
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "enterprise_multi_agent_enabled": False,
        "mcp_runtime_enabled": False,
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": "2026-08-15T12:00:00Z",
        "source_head": "f" * 40,
        "provider": {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "models_preflight_passed": True,
        },
        "posture": {
            "before": closed,
            "during": {
                "environment": "production",
                "runtime_profile": "personal_single_owner",
                "personal_practice_enabled": True,
                "agent_runtime_enabled": True,
                "agent_planner_enabled": False,
                "enterprise_multi_agent_enabled": False,
                "mcp_runtime_enabled": False,
                "max_concurrent_invocations": 1,
            },
            "after": dict(closed),
        },
        "journeys": {
            "rag_single": _journey("rag_single", "rag", ["parent"], _rag_result()),
            "rag_three": _journey("rag_three", "rag", ["data", "qa", "parent"], _rag_result()),
            "artifact_single": _journey(
                "artifact_single", "artifact", ["parent"], _artifact_result(slides=False)
            ),
            "artifact_four": _journey(
                "artifact_four",
                "artifact",
                ["product", "ux", "frontend", "parent"],
                _artifact_result(slides=True),
            ),
            "workspace_single": _journey(
                "workspace_single",
                "workspace",
                ["parent"],
                _workspace_result("src/single.txt"),
            ),
            "workspace_six": _journey(
                "workspace_six",
                "workspace",
                ["product", "frontend", "backend", "security", "qa", "parent"],
                _workspace_result("src/team.txt"),
            ),
        },
        "cleanup": {
            "disposable_documents_removed": True,
            "disposable_workspaces_removed": True,
            "provider_credential_revoked": True,
            "runtime_canary_closed": True,
            "all_feature_gates_closed": True,
        },
        "production_accepted": True,
    }


def test_complete_redacted_live_matrix_receipt_is_accepted() -> None:
    receipt = _receipt()

    assert validate_personal_practice_receipt(receipt) is receipt


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"api_key": "must-not-appear"}),
        lambda value: value["journeys"].pop("workspace_six"),
        lambda value: value["posture"]["after"].update({"agent_runtime_enabled": True}),
        lambda value: value["journeys"]["rag_three"]["result"].update({"citation_precision": 0.5}),
        lambda value: value["journeys"]["artifact_four"]["result"].update(
            {"artifact_type": "pptx"}
        ),
        lambda value: value["journeys"]["workspace_six"]["result"].update(
            {"tree_rollback_sha256": "0" * 64}
        ),
        lambda value: value["journeys"]["rag_single"]["nodes"][0].update(
            {"actual_model_id": "deepseek-v4-pro"}
        ),
        lambda value: value["journeys"]["workspace_single"]["result"].update(
            {"logical_path": "E:\\user\\project.txt"}
        ),
    ],
)
def test_incomplete_unsafe_or_drifted_receipts_fail_closed(mutate) -> None:
    receipt = copy.deepcopy(_receipt())
    mutate(receipt)

    with pytest.raises(PracticeReceiptError):
        validate_personal_practice_receipt(receipt)


def test_receipt_rejects_secret_locator_hidden_in_a_value() -> None:
    receipt = _receipt()
    receipt["provider"]["provider_id"] = "Bearer hidden-token"

    with pytest.raises(PracticeReceiptError, match="secret or connection locator"):
        validate_personal_practice_receipt(receipt)


def test_receipt_rejects_secret_shaped_key_under_benign_field() -> None:
    receipt = _receipt()
    receipt["provider"]["provider_id"] = "fixture-sk-abcdefghijklmnop"

    with pytest.raises(PracticeReceiptError, match="secret-shaped"):
        validate_personal_practice_receipt(receipt)


def test_receipt_rejects_roster_drift_even_when_count_is_unchanged() -> None:
    receipt = _receipt()
    journey = receipt["journeys"]["rag_three"]
    journey["roles"] = ["data", "security", "parent"]
    journey["nodes"][1]["role"] = "security"

    with pytest.raises(PracticeReceiptError, match="roster"):
        validate_personal_practice_receipt(receipt)


def test_receipt_rejects_call_identity_reuse_across_journeys() -> None:
    receipt = _receipt()
    receipt["journeys"]["artifact_single"]["nodes"][0]["invocation_id"] = receipt["journeys"][
        "rag_single"
    ]["nodes"][0]["invocation_id"]

    with pytest.raises(PracticeReceiptError, match="reuses a durable call identity"):
        validate_personal_practice_receipt(receipt)


def test_receipt_rejects_node_model_that_differs_from_provider_preflight() -> None:
    receipt = _receipt()
    node = receipt["journeys"]["rag_single"]["nodes"][0]
    node["requested_model_id"] = "deepseek-v4-pro"
    node["actual_model_id"] = "deepseek-v4-pro"

    with pytest.raises(PracticeReceiptError, match="model identity"):
        validate_personal_practice_receipt(receipt)
