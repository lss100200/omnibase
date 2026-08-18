from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnibase.desktop_local import (
    DesktopDatabaseUnavailable,
    DesktopLocalConfig,
    UnsafeDataRoot,
    default_user_data_root,
    open_database,
    validate_data_root,
)


def test_configuration_uses_only_explicit_values_not_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ambient = tmp_path / "ambient-must-not-be-used"
    explicit = tmp_path / "explicit-data"
    monkeypatch.setenv("LOCALAPPDATA", str(ambient))
    monkeypatch.setenv("OMNIBASE_DATA_ROOT", str(ambient))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///ambient-must-not-be-used.sqlite3")

    config = DesktopLocalConfig(data_root=explicit, application_version="1.0.0")
    assert config.data_root == explicit
    assert config.database_path == explicit / "state" / "omnibase.sqlite3"
    assert default_user_data_root(tmp_path) == tmp_path / "OmniBase"


def test_data_root_must_be_absolute_and_not_filesystem_root(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_must_be_absolute$"):
        validate_data_root(Path("relative/data"))
    with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_must_not_be_filesystem_root$"):
        validate_data_root(Path(tmp_path.anchor))


def test_data_root_rejects_parent_traversal_and_windows_nonlocal_forms(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_parent_traversal_not_allowed$"):
        validate_data_root(tmp_path / "selected" / ".." / "escaped")
    if os.name == "nt":
        with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_must_be_local$"):
            validate_data_root(Path(r"\\server\share\OmniBase"))
        with pytest.raises(
            UnsafeDataRoot, match="^desktop_data_root_alternate_stream_not_allowed$"
        ):
            validate_data_root(tmp_path / "OmniBase:alternate")


def test_existing_file_cannot_be_used_as_data_root(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("bounded fixture", encoding="utf-8")
    with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_not_directory$") as caught:
        DesktopLocalConfig(data_root=target, application_version="1.0.0")
    assert str(target) not in str(caught.value)


def test_symlink_data_root_is_rejected_when_supported(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit unprivileged directory symlinks")
    with pytest.raises(UnsafeDataRoot, match="^desktop_data_root_reparse_not_allowed$"):
        DesktopLocalConfig(data_root=link, application_version="1.0.0")


def test_database_open_error_is_stable_and_redacts_physical_path(tmp_path: Path) -> None:
    config = DesktopLocalConfig(
        data_root=tmp_path / "private-user-data", application_version="1.0.0"
    )
    state_root = config.data_root / "state"
    state_root.mkdir(parents=True)
    config.database_path.mkdir()

    with pytest.raises(
        DesktopDatabaseUnavailable, match="^desktop_database_target_not_safe$"
    ) as caught:
        open_database(config)
    rendered = str(caught.value)
    assert str(config.data_root) not in rendered
    assert config.database_path.name not in rendered
    assert "private-user-data" not in rendered


def test_database_hardlink_target_is_rejected_without_modifying_source(tmp_path: Path) -> None:
    config = DesktopLocalConfig(data_root=tmp_path / "private-data", application_version="1.0.0")
    config.database_path.parent.mkdir(parents=True)
    source = tmp_path / "unrelated.sqlite3"
    original = b"not an omnibase database"
    source.write_bytes(original)
    try:
        os.link(source, config.database_path)
    except OSError:
        pytest.skip("host filesystem does not support hardlink fixtures")

    with pytest.raises(
        DesktopDatabaseUnavailable, match="^desktop_database_target_not_safe$"
    ) as caught:
        open_database(config)
    assert source.read_bytes() == original
    assert str(source) not in str(caught.value)
    assert str(config.database_path) not in str(caught.value)


def test_unsupported_sqlite_fails_before_creating_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from omnibase.desktop_local import database as local_database

    config = DesktopLocalConfig(data_root=tmp_path / "not-created", application_version="1.0.0")
    monkeypatch.setattr(local_database.sqlite3, "sqlite_version_info", (3, 36, 0))
    with pytest.raises(DesktopDatabaseUnavailable, match="^desktop_sqlite_version_unsupported$"):
        open_database(config)
    assert not config.data_root.exists()


def test_invalid_configuration_bounds_fail_before_filesystem_mutation(tmp_path: Path) -> None:
    target = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="application_version"):
        DesktopLocalConfig(data_root=target, application_version="")
    assert not target.exists()
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        DesktopLocalConfig(data_root=target, application_version="1.0.0", busy_timeout_ms=99)
    assert not target.exists()


def test_path_error_does_not_include_ambient_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "sensitive-home-marker"
    monkeypatch.setenv("HOME", str(marker))
    with pytest.raises(UnsafeDataRoot) as caught:
        validate_data_root(Path("relative"))
    assert str(marker) not in str(caught.value)
    assert os.environ["HOME"] == str(marker)
