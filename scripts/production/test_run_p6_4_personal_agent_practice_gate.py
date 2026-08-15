"""Offline contract tests for the complete P6.4 acceptance controller."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import run_p6_4_personal_agent_practice_gate as gate


def _controller(tmp_path: Path) -> gate.PracticeGateController:
    work_root = tmp_path / "omnibase-p64-controller-test"
    return gate.PracticeGateController(
        repo_root=gate.REPO_ROOT,
        work_root=work_root,
        output=work_root / "receipt.json",
        model_id="deepseek-v4-flash",
        deepseek_key="synthetic-deepseek-value",
    )


def _usage() -> dict[str, int]:
    return {
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
        "reasoning_tokens": 2,
        "cached_input_tokens": 4,
        "cache_miss_input_tokens": 16,
    }


def _node(journey: str, ordinal: int, role: str, *, rag: bool) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "role": role,
        "invocation_id": f"invocation-{journey}-{role}-{ordinal}",
        "task_id": f"task-{journey}-{role}-{ordinal}",
        "requested_model_id": "deepseek-v4-flash",
        "actual_model_id": "deepseek-v4-flash",
        "usage": _usage(),
        "latency_ms": 50,
        "answer_sha256": f"{ordinal:x}".rjust(64, "0"),
        "citations": (
            [
                {
                    "index": 1,
                    "chunk_id": f"chunk-{ordinal}",
                    "document_id": "document-main",
                    "page_number": 1,
                }
            ]
            if rag
            else []
        ),
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
        "byte_length": 100,
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


def _journey(
    name: str, scenario: str, roles: list[str], result: dict[str, object]
) -> dict[str, object]:
    return {
        "scenario": scenario,
        "participant_count": len(roles),
        "roles": roles,
        "provider_call_count": len(roles),
        "nodes": [
            _node(name, index, role, rag=scenario == "rag")
            for index, role in enumerate(roles, start=1)
        ],
        "result": result,
        "passed": True,
    }


def _matrix() -> dict[str, object]:
    return {
        "source_head": "f" * 40,
        "provider": {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "models_preflight_passed": True,
        },
        "during_posture": {
            "environment": "production",
            "runtime_profile": "personal_single_owner",
            "personal_practice_enabled": True,
            "agent_runtime_enabled": True,
            "agent_planner_enabled": False,
            "enterprise_multi_agent_enabled": False,
            "mcp_runtime_enabled": False,
            "max_concurrent_invocations": 1,
        },
        "journeys": {
            "rag_single": _journey("rag_single", "rag", ["parent"], _rag_result()),
            "rag_three": _journey(
                "rag_three", "rag", ["data", "qa", "parent"], _rag_result()
            ),
            "artifact_single": _journey(
                "artifact_single",
                "artifact",
                ["parent"],
                _artifact_result(slides=False),
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
                _workspace_result("src/six.txt"),
            ),
        },
    }


def test_operator_env_is_run_scoped_and_contains_no_deepseek_secret(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.work_root.mkdir()
    gate._write_operator_env(controller.target.env_file, target=controller.target)

    raw = controller.target.env_file.read_text(encoding="utf-8")
    assert "synthetic-deepseek-value" not in raw
    assert 'PROVIDER_ENDPOINT_ALLOWLIST=["api.deepseek.com"]' in raw
    assert "OMNIBASE_P64_DEEPSEEK_API_KEY" not in raw


def test_compose_command_is_fixed_to_explicit_env_project_and_overlays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path)
    observed: list[str] = []

    def _fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        observed.extend(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(gate, "_run", _fake_run)
    controller.compose(["config", "--quiet"], during=True)

    assert observed[:2] == ["docker", "compose"]
    assert observed[2:4] == ["--env-file", str(controller.target.env_file)]
    assert controller.target.project in observed
    assert str(controller.base_compose) in observed
    assert str(controller.practice_compose) in observed
    assert str(controller.runtime_compose) in observed
    assert ".env" not in observed


def test_final_receipt_can_only_be_built_after_closure_and_target_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    controller.matrix = _matrix()
    controller.source_head = "f" * 40
    controller.before = dict(gate._CLOSED)
    controller.after = dict(gate._CLOSED)
    controller.canary_closed = True
    controller.target_removed = True
    monkeypatch.setattr(gate, "_clean_source_head", lambda _root: "f" * 40)

    receipt = controller._receipt()

    assert receipt["production_accepted"] is True
    assert receipt["cleanup"]["all_feature_gates_closed"] is True


def test_final_receipt_rejects_missing_target_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path)
    controller.matrix = _matrix()
    controller.source_head = "f" * 40
    controller.before = dict(gate._CLOSED)
    controller.after = dict(gate._CLOSED)
    controller.canary_closed = True
    monkeypatch.setattr(gate, "_clean_source_head", lambda _root: "f" * 40)

    with pytest.raises(ValueError, match="disposable_workspaces_removed"):
        controller._receipt()


def test_live_matrix_failure_preserves_only_the_stable_reason_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    controller.coordinates = gate.ProductCoordinates(
        access_token="synthetic-browser-token",
        tenant_id="00000000-0000-0000-0000-000000000101",
        owner_user_id="00000000-0000-0000-0000-000000000103",
        workspace_id="00000000-0000-0000-0000-000000000102",
        decoy_workspace_id="00000000-0000-0000-0000-000000000104",
        agent_version_id="00000000-0000-0000-0000-000000000105",
    )

    class _FailingRunner:
        def __init__(self, **_kwargs: object) -> None:
            return

        def execute(self) -> dict[str, object]:
            raise gate.LiveMatrixError("deepseek_requested_model_unavailable")

        def cleanup_browser_state(
            self, _matrix: dict[str, object] | None
        ) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(gate, "LiveMatrixRunner", _FailingRunner)

    with pytest.raises(
        gate.PracticeGateError,
        match="live_matrix_failed:deepseek_requested_model_unavailable",
    ):
        controller._run_matrix()


def test_bounded_cleanup_removes_only_named_child_tree(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.work_root.mkdir()
    disposable = controller.work_root / "model-cache"
    nested = disposable / "nested"
    nested.mkdir(parents=True)
    (nested / "weights.bin").write_bytes(b"bounded")
    preserved = controller.work_root / "preserved.txt"
    preserved.write_text("keep", encoding="utf-8")

    controller._purge_bounded_tree(disposable)

    assert not disposable.exists()
    assert preserved.read_text(encoding="utf-8") == "keep"
    with pytest.raises(gate.PracticeGateError, match="outside_run"):
        controller._purge_bounded_tree(controller.work_root)


def test_bounded_cleanup_rejects_a_link_root_without_following_it(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.work_root.mkdir()
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    marker = outside / "preserved.bin"
    marker.write_bytes(b"preserve")
    linked = controller.work_root / "model-cache"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(gate.PracticeGateError, match="root_is_link"):
        controller._purge_bounded_tree(linked)
    assert marker.read_bytes() == b"preserve"


def test_clean_source_preflight_rejects_a_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = iter(
        (
            subprocess.CompletedProcess(["git"], 0, "f" * 40 + "\n", ""),
            subprocess.CompletedProcess(["git"], 0, " M tracked.py\n", ""),
        )
    )
    monkeypatch.setattr(gate, "_run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(gate.PracticeGateError, match="source_worktree_not_clean"):
        gate._clean_source_head(tmp_path)


def test_docker_preflight_requires_a_healthy_linux_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = iter(
        (
            subprocess.CompletedProcess(["docker", "version"], 0, "linux\n", ""),
            subprocess.CompletedProcess(["docker", "info"], 0, "27.0.0\n", ""),
        )
    )
    monkeypatch.setattr(gate, "_run", lambda *_args, **_kwargs: next(results))

    gate._require_healthy_docker(tmp_path)


def test_run_decodes_utf8_independently_of_the_windows_host_locale(
    tmp_path: Path,
) -> None:
    result = gate._run(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write('构建完成 ✓'.encode('utf-8'))",
        ],
        cwd=tmp_path,
    )

    assert result.stdout == "构建完成 ✓"


def test_run_nonzero_exit_uses_a_stable_error_without_output_disclosure(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        gate.PracticeGateError,
        match=r"^acceptance_command_failed:python(?:\.exe)?$",
    ) as raised:
        gate._run(
            [
                sys.executable,
                "-c",
                "import sys;sys.stderr.buffer.write(b'sensitive-output');sys.exit(7)",
            ],
            cwd=tmp_path,
        )

    assert "sensitive-output" not in str(raised.value)


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(["docker", "version"], 1, "", "unavailable"),
        subprocess.CompletedProcess(["docker", "version"], 0, "windows\n", ""),
    ],
)
def test_docker_preflight_fails_before_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(gate, "_run", lambda *_args, **_kwargs: result)

    with pytest.raises(gate.PracticeGateError, match="docker_linux_engine_not_healthy"):
        gate._require_healthy_docker(tmp_path)


def _cli_args(work_root: Path) -> list[str]:
    return [
        "--repo-root",
        str(gate.REPO_ROOT),
        "--work-root",
        str(work_root),
        "--output",
        str(work_root / "receipt.json"),
        "--model-id",
        "deepseek-v4-flash",
    ]


def test_cli_rejects_secret_arguments(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gate._args(
            [
                *_cli_args(tmp_path / "omnibase-p64-cli"),
                "--api-key",
                "forbidden",
            ]
        )


def test_output_must_be_directly_inside_new_run_root(tmp_path: Path) -> None:
    work_root = tmp_path / "omnibase-p64-output"
    args = gate._args(_cli_args(work_root))
    assert gate._validate_paths(args)[2] == work_root / "receipt.json"
    nested = gate._args(
        [
            "--repo-root",
            str(gate.REPO_ROOT),
            "--work-root",
            str(work_root),
            "--output",
            str(work_root / "nested" / "receipt.json"),
        ]
    )
    with pytest.raises(gate.PracticeGateError, match="output_must_be_new_json"):
        gate._validate_paths(nested)


def test_failure_output_never_contains_deepseek_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    work_root = tmp_path / "omnibase-p64-main-failure"
    monkeypatch.setenv(gate.DEEPSEEK_KEY_ENV, "synthetic-deepseek-value")

    class _FailingController:
        def __init__(self, **_kwargs: object) -> None:
            return

        def execute(self) -> dict[str, object]:
            raise gate.PracticeGateError("synthetic_failure")

        def cleanup_after_failure(self) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(gate, "PracticeGateController", _FailingController)

    assert gate.main(_cli_args(work_root)) == 1
    output = capsys.readouterr().out
    assert "synthetic-deepseek-value" not in output
    assert json.loads(output)["production_accepted"] is False
