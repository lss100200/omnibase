from __future__ import annotations

import importlib.util
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def _validator():
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_payload.py"
    spec = importlib.util.spec_from_file_location("omnibase_installer_payload", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    resources = payload / "resources"
    resources.mkdir(parents=True)
    (payload / "OmniBase.exe").write_bytes(b"bounded desktop executable fixture")
    (resources / "app.asar").write_bytes(b"bounded renderer fixture")
    return payload


def test_validation_and_wix_authoring_are_deterministic(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)

    first = validator.validate_payload(payload)
    second = validator.validate_payload(payload)
    first_wxs = validator.render_payload_wxs(first)
    second_wxs = validator.render_payload_wxs(second)

    assert first == second
    assert first_wxs == second_wxs
    assert first.file_count == 2
    assert first.total_bytes > 0
    assert len(first.tree_sha256) == 64

    root = ET.fromstring(first_wxs)
    namespace = {"w": validator.WIX_NAMESPACE}
    components = root.findall(".//w:Component", namespace)
    assert len(components) == 2
    assert len({component.attrib["Guid"] for component in components}) == 2
    assert len(root.findall(".//w:RemoveFolder", namespace)) == 2
    for component in components:
        file_element = component.find("w:File", namespace)
        registry = component.find("w:RegistryValue", namespace)
        assert file_element is not None
        assert registry is not None
        assert file_element.attrib["Source"].startswith("!(bindpath.PayloadRoot)\\")
        assert file_element.attrib["KeyPath"] == "no"
        assert registry.attrib["Root"] == "HKCU"
        assert registry.attrib["KeyPath"] == "yes"


def test_wix_authoring_is_written_outside_payload(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    output = tmp_path / "obj" / "OmniBase.Payload.g.wxs"
    summary = validator.validate_payload(payload)

    validator.write_payload_wxs(
        output,
        validator.render_payload_wxs(summary),
        payload,
    )

    assert output.is_file()
    assert b"ComponentGroup" in output.read_bytes()


def test_payload_requires_exact_root_entrypoint(tmp_path: Path) -> None:
    validator = _validator()
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "omnibase.exe").write_bytes(b"wrong case")

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_entrypoint_missing$",
    ):
        validator.validate_payload(payload)


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        "config/provider.key",
        "config/provider.pfx",
        "state/omnibase.sqlite3",
        "debug/backend.pdb",
        "virtual-disks/runtime.vhdx",
    ],
)
def test_sensitive_or_stateful_payload_files_are_rejected(
    tmp_path: Path,
    relative: str,
) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    target = payload.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"must not be read or packaged")

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_sensitive_path_forbidden$",
    ):
        validator.validate_payload(payload)


def test_empty_payload_file_is_rejected(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    (payload / "empty.txt").write_bytes(b"")

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_file_size_invalid$",
    ):
        validator.validate_payload(payload)


def test_symlink_payload_entry_is_rejected_when_supported(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    source = tmp_path / "outside.txt"
    source.write_bytes(b"outside")
    link = payload / "linked.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("host does not permit unprivileged symlink fixtures")

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_link_reparse_or_hardlink_forbidden$",
    ):
        validator.validate_payload(payload)


def test_hardlinked_payload_entry_is_rejected_when_supported(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    source = tmp_path / "outside.txt"
    source.write_bytes(b"outside")
    link = payload / "hardlinked.txt"
    try:
        os.link(source, link)
    except OSError:
        pytest.skip("host filesystem does not support hardlink fixtures")

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_link_reparse_or_hardlink_forbidden$",
    ):
        validator.validate_payload(payload)


def test_relative_payload_root_is_rejected_without_path_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    validator = _validator()
    marker = "private-relative-payload"
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_payload.py", "--payload-root", marker],
    )

    assert validator.main() == 2
    rendered = capsys.readouterr().out
    assert json.loads(rendered)["error"] == "installer_payload_root_must_be_absolute"
    assert marker not in rendered


def test_generated_authoring_cannot_be_written_inside_payload(tmp_path: Path) -> None:
    validator = _validator()
    payload = _payload(tmp_path)
    summary = validator.validate_payload(payload)

    with pytest.raises(
        validator.PayloadValidationError,
        match="^installer_payload_output_inside_payload$",
    ):
        validator.write_payload_wxs(
            payload / "generated.wxs",
            validator.render_payload_wxs(summary),
            payload,
        )
