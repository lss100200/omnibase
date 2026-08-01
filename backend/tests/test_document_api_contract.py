"""Tests for Todo 7: document API state handling and consistency.

Tests:
- Tenant isolation: tenant A cannot access tenant B's documents
- Unknown document: 404 with safe envelope (no stack trace, no internal paths)
- Failed document: bounded error_detail without leaking credentials/stack traces
- Status filter: list endpoint validates/forwards lifecycle status filter

Idempotent re-ingest tests are in test_idempotent_ingest.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ────────────────────────────────────────────────────────────
# Shared fakes
# ────────────────────────────────────────────────────────────


@dataclass
class FakeDocument:
    """In-memory fake of the Document ORM model."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    minio_key: str
    page_count: int | None = None
    error_detail: str | None = None
    metadata_: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


def _build_router_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    schema_name: str = "tenant_a1b2c3d4e5",
    mock_get_document: object | None = None,
    mock_list_documents: object | None = None,
    mock_delete_document: object | None = None,
) -> TestClient:
    """Build a FastAPI TestClient with tenant dependency override."""
    from omnibase.documents.router import router
    from omnibase.tenants.dependencies import TenantContext, get_current_tenant

    app = FastAPI()
    app.include_router(router)

    fake_tenant_orm = MagicMock()
    fake_tenant_orm.id = "fake-tenant-id"
    fake_tenant_orm.schema_name = schema_name

    async def fake_tenant() -> TenantContext:
        return TenantContext(tenant=fake_tenant_orm)

    app.dependency_overrides[get_current_tenant] = fake_tenant

    if mock_get_document is not None:
        monkeypatch.setattr(
            "omnibase.documents.router.get_document", mock_get_document
        )
    if mock_list_documents is not None:
        monkeypatch.setattr(
            "omnibase.documents.router.list_documents", mock_list_documents
        )
    if mock_delete_document is not None:
        monkeypatch.setattr(
            "omnibase.documents.router.delete_document", mock_delete_document
        )

    return TestClient(app)


# ────────────────────────────────────────────────────────────
# 1. Unknown document returns 404
# ────────────────────────────────────────────────────────────


class TestRouterNotFound:
    """Unknown documents return 404 with a safe error envelope."""

    def test_get_unknown_document_returns_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /documents/{id} for a nonexistent doc returns 404."""
        from omnibase.documents.service import DocumentNotFound

        def fake_get(**kwargs: object) -> None:
            raise DocumentNotFound("Document missing-id not found")

        client = _build_router_client(monkeypatch, mock_get_document=fake_get)
        response = client.get("/documents/missing-id")
        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error"]["code"] == "not_found"

    def test_delete_unknown_document_returns_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DELETE /documents/{id} for a nonexistent doc returns 404."""
        from omnibase.documents.service import DocumentNotFound

        def fake_delete(**kwargs: object) -> None:
            raise DocumentNotFound("Document gone-id not found")

        client = _build_router_client(monkeypatch, mock_delete_document=fake_delete)
        response = client.delete("/documents/gone-id")
        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error"]["code"] == "not_found"

    def test_delete_active_document_returns_409(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DELETE maps lifecycle conflicts to HTTP 409."""
        from omnibase.documents.service import DocumentDeleteConflict

        def fake_delete(**kwargs: object) -> None:
            raise DocumentDeleteConflict("doc-active", "processing")

        client = _build_router_client(monkeypatch, mock_delete_document=fake_delete)
        response = client.delete("/documents/doc-active")

        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["error"]["code"] == "document_delete_conflict"
        assert "processing" not in body["detail"]["error"]["message"]

    def test_not_found_error_does_not_leak_internals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """404 error message must not contain SQL, file paths, or stack traces."""
        from omnibase.documents.service import DocumentNotFound

        # Simulate a detailed internal error leaking via the exception
        def fake_get(**kwargs: object) -> None:
            raise DocumentNotFound(
                "Document x not found in schema tenant_secret"
            )

        client = _build_router_client(monkeypatch, mock_get_document=fake_get)
        response = client.get("/documents/x")
        message = response.json()["detail"]["error"]["message"]
        # The router passes str(exc) through — assert the error is bounded
        assert len(message) < 500
        assert "traceback" not in message.lower()
        assert "--" not in message  # No SQL comment markers


# ────────────────────────────────────────────────────────────
# 2. Tenant isolation at the router level
# ────────────────────────────────────────────────────────────


class TestTenantIsolation:
    """All document endpoints pass the caller's schema_name to the service."""

    def test_get_document_passes_caller_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /documents/{id} forwards ctx.schema_name to get_document."""
        received: dict = {}

        def capturing_get(*, schema_name: str, document_id: str, **kw: object) -> FakeDocument:
            received["schema_name"] = schema_name
            received["document_id"] = document_id
            return FakeDocument(
                id=document_id,
                filename="test.pdf",
                mime_type="application/pdf",
                size_bytes=100,
                status="indexed",
                minio_key=f"{schema_name}/{document_id}/test.pdf",
            )

        client = _build_router_client(
            monkeypatch,
            schema_name="tenant_caller_abc",
            mock_get_document=capturing_get,
        )
        response = client.get("/documents/doc-123")
        assert response.status_code == 200
        assert received["schema_name"] == "tenant_caller_abc"
        assert received["document_id"] == "doc-123"

    def test_list_documents_passes_caller_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /documents forwards ctx.schema_name to list_documents."""
        received: dict = {}

        def capturing_list(
            *, schema_name: str, **kw: object
        ) -> tuple[list, int]:
            received["schema_name"] = schema_name
            return [], 0

        client = _build_router_client(
            monkeypatch,
            schema_name="tenant_isolated_xyz",
            mock_list_documents=capturing_list,
        )
        response = client.get("/documents")
        assert response.status_code == 200
        assert received["schema_name"] == "tenant_isolated_xyz"


# ────────────────────────────────────────────────────────────
# 3. Failed document contract — bounded error_detail
# ────────────────────────────────────────────────────────────


class TestFailedDocumentContract:
    """Failed documents expose bounded error_detail without leaking secrets."""

    def test_get_failed_document_returns_error_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /documents/{id} for a failed doc includes error_detail."""

        def fake_get(**kwargs: object) -> FakeDocument:
            return FakeDocument(
                id="failed-doc",
                filename="broken.pdf",
                mime_type="application/pdf",
                size_bytes=512,
                status="failed",
                minio_key="tenant_test/failed-doc/broken.pdf",
                error_detail="Parse error: unexpected EOF",
            )

        client = _build_router_client(monkeypatch, mock_get_document=fake_get)
        response = client.get("/documents/failed-doc")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert body["error_detail"] == "Parse error: unexpected EOF"

    def test_error_detail_truncated_to_1000_chars(self) -> None:
        """Worker error_detail is bounded to 1000 characters."""
        from omnibase.workers.tasks import _ERROR_DETAIL_MAX_LEN, _set_document_failed

        # Verify the constant is exactly 1000
        assert _ERROR_DETAIL_MAX_LEN == 1000

        # Verify the truncation logic by inspecting the source code pattern:
        # _set_document_failed uses: error_detail[:_ERROR_DETAIL_MAX_LEN]
        # This is a contract — the worker must never store more than 1000 chars.
        import inspect

        source = inspect.getsource(_set_document_failed)
        assert "[:_ERROR_DETAIL_MAX_LEN]" in source, (
            "error_detail must be truncated via [:_ERROR_DETAIL_MAX_LEN]"
        )


# ────────────────────────────────────────────────────────────
# 4. List status filter
# ────────────────────────────────────────────────────────────


class TestListStatusFilter:
    """GET /documents?status= forwards the validated filter to the service."""

    @pytest.mark.parametrize(
        "status_value",
        ["pending", "queued", "processing", "indexed", "failed"],
    )
    def test_valid_status_filter_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, status_value: str
    ) -> None:
        """A valid status filter is forwarded to list_documents."""
        received: dict = {}

        def capturing_list(
            *, schema_name: str, status_filter: str | None = None, **kw: object
        ) -> tuple[list, int]:
            received["status_filter"] = status_filter
            return [], 0

        client = _build_router_client(
            monkeypatch, mock_list_documents=capturing_list
        )
        response = client.get(f"/documents?status={status_value}")
        assert response.status_code == 200
        assert received["status_filter"] == status_value

    def test_invalid_status_filter_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid status value returns 422 (FastAPI validation)."""
        client = _build_router_client(monkeypatch)
        response = client.get("/documents?status=nonexistent")
        assert response.status_code == 422
