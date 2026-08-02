"""Unit tests for the disposable Overlay Gate source/provenance validator."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest


def _load_validator():
    module_path = Path(__file__).with_name("validate_disposable_gate.py")
    spec = importlib.util.spec_from_file_location("overlay_gate_validator", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("validator module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_static_configuration_and_manifest_round_trip() -> None:
    VALIDATOR.validate_static_configuration(REPO_ROOT)
    manifest = VALIDATOR.build_source_manifest(REPO_ROOT)
    assert manifest["schema"] == "omnibase.p34-5c.source-manifest.v1"
    assert not manifest["root_env_included"]
    assert manifest["real_member_devices_registered"] == 0
    assert manifest["file_count"] >= 150

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "manifest.json"
        VALIDATOR.write_manifest(path, manifest)
        assert VALIDATOR.verify_manifest(REPO_ROOT, path) == manifest


def test_manifest_tamper_is_rejected() -> None:
    manifest = VALIDATOR.build_source_manifest(REPO_ROOT)
    manifest["files"][0]["sha256"] = "0" * 64
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(VALIDATOR.GateValidationError, match="source changed"):
            VALIDATOR.verify_manifest(REPO_ROOT, path)
