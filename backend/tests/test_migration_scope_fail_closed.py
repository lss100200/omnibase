"""Fail-closed contracts for the P34 multi-schema migration chain."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "src" / "omnibase" / "migrations" / "versions"


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


def test_0016_to_0015_online_downgrade_is_explicitly_tenant_first() -> None:
    migration_env = (
        Path(__file__).resolve().parents[1] / "src" / "omnibase" / "migrations" / "env.py"
    ).read_text(encoding="utf-8")
    assert "def _is_exact_0016_to_0015_downgrade() -> bool:" in migration_env
    assert 'getattr(command, "__name__", None) == "downgrade"' in migration_env
    assert 'tuple(sys.argv[1:]) == ("downgrade", "0015")' in migration_env
    assert 'raw_revision == "0015"' in migration_env
    tenant_first = migration_env.split("def _run_exact_0016_to_0015_downgrade(", 1)[1].split(
        "def run_migrations_offline() -> None:", 1
    )[0]
    assert tenant_first.index("_run_one_tenant_migration") < tenant_first.index(
        "_run_global_migrations"
    )
    assert "with connectable.begin() as connection:" in tenant_first
