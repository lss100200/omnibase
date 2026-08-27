from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _builder():
    path = BACKEND_ROOT / "build_backend.py"
    spec = importlib.util.spec_from_file_location(
        "build_omnibase_desktop_backend", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_dependencies_are_exact_and_intentionally_minimal() -> None:
    requirements = (BACKEND_ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert requirements.splitlines() == [
        "altgraph==0.17.5",
        "annotated-types==0.8.0",
        "anyio==4.14.2",
        "click==8.4.2",
        "colorama==0.4.6",
        "fastapi==0.116.2",
        "h11==0.16.0",
        "idna==3.19",
        "packaging==26.3",
        "pefile==2024.8.26",
        "pydantic==2.13.4",
        "pydantic_core==2.46.4",
        "pyinstaller==6.22.2",
        "pyinstaller-hooks-contrib==2026.6",
        "pywin32-ctypes==0.2.3",
        "setuptools==84.0.0",
        "starlette==0.48.0",
        "typing-inspection==0.4.4",
        "typing_extensions==4.16.0",
        "uvicorn==0.33.0",
    ]
    lowered = requirements.casefold()
    for optional in (
        "bge",
        "docker",
        "numpy",
        "pgvector",
        "postgres",
        "sentence-transformers",
        "torch",
        "wsl",
    ):
        assert optional not in lowered


def test_pyinstaller_spec_has_fixed_entrypoint_and_runtime_implementation() -> None:
    authoring = (BACKEND_ROOT / "OmniBase.Desktop.Backend.spec").read_text(
        encoding="utf-8"
    )
    assert '"omnibase" / "desktop_local" / "app.py"' in authoring
    assert 'name="OmniBase.Desktop.Backend"' in authoring
    for required in (
        "uvicorn.lifespan.on",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
    ):
        assert required in authoring
    for optional in (
        "celery",
        "pgvector",
        "psycopg",
        "sentence_transformers",
        "torch",
    ):
        assert f'"{optional}"' in authoring


def test_publish_validation_accepts_only_closed_ordinary_output(tmp_path: Path) -> None:
    builder = _builder()
    publish = tmp_path / builder.EXPECTED_OUTPUT_NAME
    (publish / "_internal").mkdir(parents=True)
    (publish / builder.EXPECTED_EXECUTABLE).write_bytes(b"backend executable")
    (publish / "_internal/runtime.dll").write_bytes(b"runtime")

    assert builder._validate_output(publish) == (2, 25)

    (publish / "_internal/state.sqlite3").write_bytes(b"must not package")
    with pytest.raises(
        builder.BackendBuildError, match="publish_sensitive_path_forbidden"
    ):
        builder._validate_output(publish)


def test_build_outputs_are_forbidden_inside_repository(tmp_path: Path) -> None:
    builder = _builder()
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(builder.BackendBuildError, match="output_inside_repository"):
        builder._require_outside_repository(repository / "dist", repository)
    builder._require_outside_repository(tmp_path / "external-dist", repository)


def test_build_subprocess_environment_does_not_forward_provider_or_database_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    builder = _builder()
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\Builder")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    monkeypatch.setenv("DATABASE_URL", "must-not-forward")

    environment = builder._build_environment(tmp_path / "work")
    assert set(environment) == {
        "PATH",
        "PYINSTALLER_CONFIG_DIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONUTF8",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    assert "must-not-forward" not in repr(environment)
