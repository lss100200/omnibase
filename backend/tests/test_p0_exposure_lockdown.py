"""Focused regression tests for P0 API exposure lockdown."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from omnibase.api import health
from omnibase.core.config import get_settings
from omnibase.database.router import list_tables
from omnibase.main import create_app
from omnibase.tenants.dependencies import TenantContext
from omnibase.tenants.schemas import TenantRead


def test_tenant_management_fails_closed_without_platform_admin() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/api/v1/tenants")
    assert response.status_code == 404


def test_tenant_response_does_not_expose_schema_name() -> None:
    tenant = TenantRead.model_validate(
        SimpleNamespace(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_default=True,
            is_active=True,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
    )
    assert "schema_name" not in tenant.model_dump()


def test_database_query_route_is_not_mounted() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post("/api/v1/database/query", json={"sql": "SELECT 1"})
    assert response.status_code == 404


def test_table_browser_filters_sensitive_tables_and_columns() -> None:
    db = MagicMock()
    db.execute.side_effect = [
        [("users",), ("documents",), ("embeddings",)],
        [
            ("id", "uuid", "NO", None),
            ("filename", "character varying", "NO", None),
            ("minio_key", "character varying", "NO", None),
            ("error_detail", "character varying", "YES", None),
            ("metadata", "jsonb", "NO", "{}"),
        ],
        SimpleNamespace(scalar=lambda: 3),
    ]
    tenant = SimpleNamespace(id="tenant-id", schema_name="tenant_a1b2c3d4")

    response = list_tables(ctx=TenantContext(tenant=tenant), db=db)

    assert [table.name for table in response.tables] == ["documents"]
    assert [column.name for column in response.tables[0].columns] == ["id", "filename"]
    assert "default" not in response.tables[0].columns[0].model_dump()


def test_auth_me_uses_database_backed_principal() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="alice@example.com",
        is_tenant_admin=True,
        is_active=True,
        created_at="2026-01-01T00:00:00Z",
    )
    principal = SimpleNamespace(user=user)

    from omnibase.auth.router import me_endpoint

    response = me_endpoint(principal=principal)

    assert response.id == "user-1"


def test_docs_disabled_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "production_secret_at_least_32_characters_long")
    get_settings.cache_clear()
    try:
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert "docs" not in client.get("/").json()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_readiness_probe_does_not_return_raw_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "omnibase.core.db.get_engine",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("postgresql://user:secret@host/db")),
    )
    component = await health._probe_database(get_settings())
    assert component.status == "fail"
    assert component.detail is None
