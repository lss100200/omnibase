"""Fail-closed contracts for the P34 multi-schema migration chain."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from omnibase.migrations.order import is_exact_0016_to_0015_downgrade_cli

VERSIONS = Path(__file__).resolve().parents[1] / "src" / "omnibase" / "migrations" / "versions"
MIGRATION_ENV = Path(__file__).resolve().parents[1] / "src" / "omnibase" / "migrations" / "env.py"


def _load_migration(filename: str) -> ModuleType:
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "filename",
    [
        "0004_p34_1_control_plane_foundation.py",
        "0005_p34_2_capability_ledger.py",
    ],
)
def test_global_only_migrations_accept_only_the_closed_scope_vocabulary(
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration(filename)

    def set_scope(scope: object, *, present: bool = True) -> None:
        attributes = {"migration_schema_scope": scope} if present else {}
        context = SimpleNamespace(config=SimpleNamespace(attributes=attributes))
        monkeypatch.setattr(migration.op, "get_context", lambda: context)

    for scope in ("global", "tenant"):
        set_scope(scope)
        assert migration._migration_schema_scope() == scope

    for invalid in (None, "", "GLOBAL", "workspace", 1):
        set_scope(invalid)
        with pytest.raises(RuntimeError, match="unsupported migration_schema_scope"):
            migration._migration_schema_scope()

    set_scope(None, present=False)
    with pytest.raises(RuntimeError, match="unsupported migration_schema_scope"):
        migration._migration_schema_scope()


def test_offline_migration_generation_explicitly_uses_global_scope() -> None:
    migration_env = (
        Path(__file__).resolve().parents[1] / "src" / "omnibase" / "migrations" / "env.py"
    ).read_text(encoding="utf-8")
    offline_body = migration_env.split("def run_migrations_offline() -> None:", 1)[1].split(
        "def run_migrations_online() -> None:", 1
    )[0]
    assert 'config.attributes["migration_schema_scope"] = "global"' in offline_body


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["downgrade", "0015"], True),
        (["-x", "unsafe=1", "downgrade", "0015"], False),
        (["--name", "alternate", "downgrade", "0015"], False),
        (["downgrade", "0015", "--tag", "unexpected"], False),
        (["downgrade", "0015", "--sql"], False),
        (["downgrade", "0016:0015"], False),
        (["downgrade", "-1"], False),
        ([], False),
        (["upgrade", "0015"], False),
        (["downgrade", "0014"], False),
    ],
)
def test_0016_to_0015_tenant_first_selector_accepts_only_exact_cli(
    arguments: list[str],
    expected: bool,
) -> None:
    assert is_exact_0016_to_0015_downgrade_cli(arguments) is expected


def test_0016_to_0015_online_downgrade_is_explicitly_tenant_first() -> None:
    migration_env = MIGRATION_ENV.read_text(encoding="utf-8")
    assert "def _is_exact_0016_to_0015_downgrade() -> bool:" in migration_env
    assert "return is_exact_0016_to_0015_downgrade_cli(sys.argv[1:])" in migration_env
    assert "metadata_matches" not in migration_env
    tenant_first = migration_env.split("def _run_exact_0016_to_0015_downgrade(", 1)[1].split(
        "def run_migrations_offline() -> None:", 1
    )[0]
    assert tenant_first.index("_run_one_tenant_migration") < tenant_first.index(
        "_run_global_migrations"
    )
    assert "with connectable.begin() as connection:" in tenant_first
