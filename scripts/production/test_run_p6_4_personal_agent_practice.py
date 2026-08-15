"""Offline attack tests for the P6.4 live-matrix runner."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest
import run_p6_4_personal_agent_practice as acceptance


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(name: str, payload: dict[str, object]) -> bytes:
    return (
        f"event: {name}\n" f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    ).encode()


def _usage() -> dict[str, int]:
    return {
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
        "reasoning_tokens": 2,
        "cached_input_tokens": 4,
        "cache_miss_input_tokens": 16,
    }


def _citations(*, document_id: str = "document-main") -> list[dict[str, object]]:
    return [
        {
            "index": 1,
            "chunk_id": "chunk-main",
            "document_id": document_id,
            "page_number": 1,
        }
    ]


def _stream(
    *,
    roles: tuple[str, ...],
    scenario: str,
    final_payload: dict[str, object],
    mutations: dict[str, object] | None = None,
) -> io.BytesIO:
    settings = mutations or {}
    chunks = [
        _event(
            "practice_started",
            {
                "scenario": scenario,
                "participant_count": len(roles),
                "roles": list(roles),
                "serial": True,
                "enterprise_multi_agent": False,
            },
        )
    ]
    parent_answer = json.dumps(final_payload, separators=(",", ":"))
    for ordinal, role in enumerate(roles, start=1):
        answer = parent_answer if role == "parent" else '{"observations":[]}'
        identity = {
            "invocation_id": f"invocation-{ordinal}",
            "task_id": f"task-{ordinal}",
            "requested_model_id": "deepseek-v4-flash",
        }
        citations = _citations() if scenario == "rag" else []
        chunks.extend(
            [
                _event("node_started", {"ordinal": ordinal, "role": role}),
                _event(
                    "node_event",
                    {"ordinal": ordinal, "role": role, "event": "meta", **identity},
                ),
                _event(
                    "node_event",
                    {
                        "ordinal": ordinal,
                        "role": role,
                        "event": "citations",
                        "citations": citations,
                    },
                ),
            ]
        )
        if settings.get("omit_usage_for") != ordinal:
            chunks.append(
                _event(
                    "node_event",
                    {"ordinal": ordinal, "role": role, "event": "usage"},
                )
            )
        completed = {
            "ordinal": ordinal,
            "role": role,
            **identity,
            "actual_model_id": "deepseek-v4-flash",
            "usage": _usage(),
            "answer_sha256": _digest(answer),
            "citations": citations,
        }
        if settings.get("identity_drift_for") == ordinal:
            completed["task_id"] = "task-drifted"
        if settings.get("citation_drift_for") == ordinal:
            completed["citations"] = _citations(document_id="document-drifted")
        chunks.append(_event("node_completed", completed))
    final_digest = _digest(parent_answer)
    if settings.get("final_digest_drift"):
        final_digest = "0" * 64
    chunks.append(
        _event(
            "practice_completed",
            {
                "scenario": scenario,
                "participant_count": len(roles),
                "provider_call_count": len(roles),
                "parent_invocation_id": f"invocation-{len(roles)}",
                "parent_task_id": f"task-{len(roles)}",
                "final_answer": parent_answer,
                "final_answer_sha256": final_digest,
            },
        )
    )
    if settings.get("extra_after_terminal"):
        chunks.append(_event("node_started", {"ordinal": 99, "role": "parent"}))
    return io.BytesIO(b"".join(chunks))


@pytest.mark.parametrize(
    ("roles", "scenario"),
    [
        (("parent",), "rag"),
        (("data", "qa", "parent"), "rag"),
        (("product", "ux", "frontend", "parent"), "artifact"),
        (("product", "frontend", "backend", "security", "qa", "parent"), "workspace"),
    ],
)
def test_stream_accepts_independent_ordered_one_three_four_and_six_agent_runs(
    roles: tuple[str, ...], scenario: str
) -> None:
    nodes, payload, answer = acceptance.collect_practice_stream(
        _stream(roles=roles, scenario=scenario, final_payload={"ok": True}),
        expected_roles=roles,
        expected_scenario=scenario,
    )

    assert len(nodes) == len(roles)
    assert len({node["invocation_id"] for node in nodes}) == len(roles)
    assert payload == {"ok": True}
    assert json.loads(answer) == payload


@pytest.mark.parametrize(
    ("mutations", "error"),
    [
        ({"identity_drift_for": 2}, "identity_drift"),
        ({"citation_drift_for": 2}, "citation_drift"),
        ({"omit_usage_for": 2}, "receipt_incomplete"),
        ({"final_digest_drift": True}, "final_answer_digest_invalid"),
        ({"extra_after_terminal": True}, "after_terminal"),
    ],
)
def test_stream_fails_closed_on_identity_citation_usage_digest_or_terminal_drift(
    mutations: dict[str, object], error: str
) -> None:
    with pytest.raises(acceptance.LiveMatrixError, match=error):
        acceptance.collect_practice_stream(
            _stream(
                roles=("data", "qa", "parent"),
                scenario="rag",
                final_payload={"ok": True},
                mutations=mutations,
            ),
            expected_roles=("data", "qa", "parent"),
            expected_scenario="rag",
        )


def test_incomplete_sse_frame_is_rejected() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="incomplete_frame"):
        list(
            acceptance._sse_events(
                io.BytesIO(b'event: practice_started\ndata: {"x":1}')
            )
        )


def test_terminal_error_preserves_only_a_stable_node_failure_code() -> None:
    stream = io.BytesIO(
        _event(
            "error",
            {
                "code": (
                    "practice_node_terminal_failure:parent:"
                    "agent_alpha_provider_unavailable"
                )
            },
        )
    )

    with pytest.raises(
        acceptance.LiveMatrixError,
        match=(
            "practice_stream_terminal_error:practice_node_terminal_failure:"
            "parent:agent_alpha_provider_unavailable"
        ),
    ):
        acceptance.collect_practice_stream(
            stream,
            expected_roles=("parent",),
            expected_scenario="rag",
        )


def test_terminal_error_preserves_a_stable_task_ledger_failure_code() -> None:
    stream = io.BytesIO(
        _event(
            "error",
            {"code": "practice_node_terminal_failure:parent:task_lease_expired"},
        )
    )

    with pytest.raises(
        acceptance.LiveMatrixError,
        match=(
            "practice_stream_terminal_error:practice_node_terminal_failure:"
            "parent:task_lease_expired"
        ),
    ):
        acceptance.collect_practice_stream(
            stream,
            expected_roles=("parent",),
            expected_scenario="rag",
        )


def test_terminal_error_rejects_untrusted_detail_from_the_diagnostic_code() -> None:
    stream = io.BytesIO(
        _event(
            "error",
            {
                "code": (
                    "practice_node_terminal_failure:parent:"
                    "agent_alpha_provider_unavailable:untrusted-detail"
                )
            },
        )
    )

    with pytest.raises(
        acceptance.LiveMatrixError,
        match=r"^practice_stream_terminal_error$",
    ) as raised:
        acceptance.collect_practice_stream(
            stream,
            expected_roles=("parent",),
            expected_scenario="rag",
        )

    assert "untrusted-detail" not in str(raised.value)


def _rag_payload(
    *,
    statement_codename: str = "ORCHID-417",
    codename_indices: list[int] | None = None,
    answer: str = "ORCHID-417 [1] and LANTERN-82 [2]",
) -> dict[str, object]:
    return {
        "answer": answer,
        "claims": [
            {
                "fact_id": "project_codename",
                "statement": statement_codename,
                "citation_indices": codename_indices or [1],
            },
            {
                "fact_id": "release_channel",
                "statement": "LANTERN-82",
                "citation_indices": [2],
            },
        ],
        "abstained": False,
    }


def _rag_nodes(*, decoy: bool = False) -> list[dict[str, object]]:
    if decoy:
        return [{"citations": _citations(document_id="document-decoy")}]
    return [
        {
            "citations": [
                {
                    "index": 1,
                    "chunk_id": "chunk-codename",
                    "document_id": "document-codename",
                    "page_number": 1,
                },
                {
                    "index": 2,
                    "chunk_id": "chunk-release",
                    "document_id": "document-release",
                    "page_number": 1,
                },
            ]
        }
    ]


def _fact_documents() -> dict[str, str]:
    return {
        "project_codename": "document-codename",
        "release_channel": "document-release",
    }


def test_rag_decoy_document_is_rejected() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="citation isolation"):
        acceptance._rag_result(
            payload=_rag_payload(),
            nodes=_rag_nodes(decoy=True),
            fact_document_ids=_fact_documents(),
            decoy_document_ids=frozenset({"document-decoy"}),
        )


def test_rag_correct_chunk_with_wrong_statement_is_rejected() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="citation score failed"):
        acceptance._rag_result(
            payload=_rag_payload(statement_codename="COBALT-992"),
            nodes=_rag_nodes(),
            fact_document_ids=_fact_documents(),
            decoy_document_ids=frozenset({"document-decoy"}),
        )


def test_rag_fact_citing_the_other_fact_chunk_is_rejected() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="citation score failed"):
        acceptance._rag_result(
            payload=_rag_payload(codename_indices=[2]),
            nodes=_rag_nodes(),
            fact_document_ids=_fact_documents(),
            decoy_document_ids=frozenset({"document-decoy"}),
        )


def test_rag_answer_must_contain_the_claimed_citation_label() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="citation label missing"):
        acceptance._rag_result(
            payload=_rag_payload(answer="ORCHID-417 and LANTERN-82 [2]"),
            nodes=_rag_nodes(),
            fact_document_ids=_fact_documents(),
            decoy_document_ids=frozenset({"document-decoy"}),
        )


def test_pptx_or_other_untrusted_artifact_type_is_rejected() -> None:
    with pytest.raises(acceptance.LiveMatrixError, match="artifact type unsupported"):
        acceptance._render_artifact(
            {
                "artifact_type": "pptx",
                "title": "unsafe",
                "specification": {},
                "acceptance_checks": [],
            }
        )


def _workspace_payload(*, path: str, before: str, after: str) -> dict[str, object]:
    return {
        "summary": "bounded",
        "changes": [
            {
                "path": path,
                "expected_before_sha256": before,
                "after_text": after,
            }
        ],
        "tests": [],
    }


def test_workspace_path_and_before_cas_are_enforced(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    target = root / "src" / "acceptance.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8", newline="\n")
    before = _digest("before\n")
    with pytest.raises((acceptance.LiveMatrixError, ValueError)):
        acceptance._workspace_result(
            payload=_workspace_payload(
                path="../escape.txt", before=before, after="after\n"
            ),
            root=root,
            path="../escape.txt",
            expected_after="after\n",
        )
    with pytest.raises((acceptance.LiveMatrixError, ValueError)):
        acceptance._workspace_result(
            payload=_workspace_payload(
                path="src/acceptance.txt", before="0" * 64, after="after\n"
            ),
            root=root,
            path="src/acceptance.txt",
            expected_after="after\n",
        )


def test_rollback_conflict_preserves_external_edit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    target = root / "src" / "acceptance.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8", newline="\n")
    applied = acceptance.apply_text_change(
        root=root,
        proposal=acceptance.TextChangeProposal(
            path="src/acceptance.txt",
            expected_before_sha256=_digest("before\n"),
            after_text="after\n",
        ),
    )
    target.write_text("external edit\n", encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="rollback_conflict"):
        acceptance.rollback_text_change(root=root, applied=applied)
    assert target.read_text(encoding="utf-8") == "external edit\n"


def _main_args(work_root: Path, output: Path) -> list[str]:
    return [
        "--repo-root",
        str(acceptance.REPO_ROOT),
        "--work-root",
        str(work_root),
        "--base-url",
        "http://127.0.0.1:39164",
        "--workspace-id",
        "00000000-0000-4000-8000-000000000001",
        "--decoy-workspace-id",
        "00000000-0000-4000-8000-000000000002",
        "--agent-version-id",
        "00000000-0000-4000-8000-000000000003",
        "--model-id",
        "deepseek-v4-flash",
        "--output",
        str(output),
    ]


def test_cli_has_no_secret_argument(tmp_path: Path) -> None:
    work_root = tmp_path / "omnibase-p64-no-secret-cli"
    output = work_root / "matrix.json"
    with pytest.raises(SystemExit):
        acceptance._args([*_main_args(work_root, output), "--api-key", "forbidden"])


def test_loopback_target_is_mandatory() -> None:
    for value in (
        "https://127.0.0.1:8000",
        "http://example.com:8000",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
    ):
        with pytest.raises(acceptance.LiveMatrixError):
            acceptance._validate_loopback_url(value)


def test_standalone_runner_rejects_dirty_source_before_live_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = iter(
        (
            subprocess.CompletedProcess(["git"], 0, "f" * 40 + "\n", ""),
            subprocess.CompletedProcess(["git"], 0, "?? untracked.txt\n", ""),
        )
    )
    monkeypatch.setattr(
        acceptance.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )
    runner = object.__new__(acceptance.LiveMatrixRunner)
    runner.repo_root = tmp_path

    with pytest.raises(acceptance.LiveMatrixError, match="source_worktree_not_clean"):
        runner._source_head()


def test_cleanup_failure_cannot_be_written_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "omnibase-p64-cleanup-failure"
    output = work_root / "matrix.json"

    class _FakeRunner:
        def __init__(self, **_kwargs: object) -> None:
            return

        def execute(self) -> dict[str, object]:
            return {
                "schema": acceptance.MATRIX_SCHEMA,
                "cleanup": {
                    "disposable_documents_removed": False,
                    "provider_credential_revoked": False,
                    "disposable_target_cleanup_pending": True,
                },
                "production_accepted": True,
            }

        def cleanup_browser_state(
            self, _matrix: dict[str, object] | None
        ) -> tuple[str, ...]:
            return ("provider_credential_cleanup_failed",)

    monkeypatch.setattr(acceptance, "LiveMatrixRunner", _FakeRunner)
    monkeypatch.setenv(acceptance.ACCESS_TOKEN_ENV, "synthetic-access-token")
    monkeypatch.setenv(acceptance.DEEPSEEK_KEY_ENV, "synthetic-deepseek-key")

    assert acceptance.main(_main_args(work_root, output)) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["state"] == "failed/veto"
    assert payload["production_accepted"] is False


def test_matrix_fragment_is_forced_to_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "omnibase-p64-fragment"
    output = work_root / "matrix.json"

    class _FakeRunner:
        def __init__(self, **_kwargs: object) -> None:
            return

        def execute(self) -> dict[str, object]:
            return {
                "schema": acceptance.MATRIX_SCHEMA,
                "cleanup": {
                    "disposable_documents_removed": True,
                    "provider_credential_revoked": True,
                    "disposable_target_cleanup_pending": True,
                },
                "production_accepted": True,
            }

        def cleanup_browser_state(
            self, _matrix: dict[str, object] | None
        ) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(acceptance, "LiveMatrixRunner", _FakeRunner)
    monkeypatch.setenv(acceptance.ACCESS_TOKEN_ENV, "synthetic-access-token")
    monkeypatch.setenv(acceptance.DEEPSEEK_KEY_ENV, "synthetic-deepseek-key")

    assert acceptance.main(_main_args(work_root, output)) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["production_accepted"] is False


def test_failed_execute_still_attempts_browser_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = tmp_path / "omnibase-p64-failed-execute"
    output = work_root / "matrix.json"
    observed = {"cleanup": False}

    class _FakeRunner:
        def __init__(self, **_kwargs: object) -> None:
            return

        def execute(self) -> dict[str, object]:
            work_root.mkdir()
            raise acceptance.LiveMatrixError("synthetic_failure")

        def cleanup_browser_state(
            self, matrix: dict[str, object] | None
        ) -> tuple[str, ...]:
            observed["cleanup"] = matrix is None
            return ()

    monkeypatch.setattr(acceptance, "LiveMatrixRunner", _FakeRunner)
    monkeypatch.setenv(acceptance.ACCESS_TOKEN_ENV, "synthetic-access-token")
    monkeypatch.setenv(acceptance.DEEPSEEK_KEY_ENV, "synthetic-deepseek-key")

    assert acceptance.main(_main_args(work_root, output)) == 1
    assert observed["cleanup"] is True
    assert (
        json.loads(output.read_text(encoding="utf-8"))["production_accepted"] is False
    )
