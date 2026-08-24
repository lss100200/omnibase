"""Router tests for Todo 6: HTTP 202 upload semantics.

Tests the FastAPI upload endpoint returns 202 Accepted with queued status.
Pure unit tests — no real Redis/MinIO/DB/model calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnibase.documents.schemas import DocumentRead, DocumentUploadResponse
from omnibase.documents.service import UploadResult

# ────────────────────────────────────────────────────────────
# Shared fakes (minimal subset needed for router tests)
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


# ────────────────────────────────────────────────────────────
# Router tests — HTTP 202
# ────────────────────────────────────────────────────────────


class TestRouter202Accepted:
    """The upload endpoint returns HTTP 202 with 'queued' status."""

    @pytest.fixture
    def client_with_mocks(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
        """Build a FastAPI TestClient with dependency overrides."""
        from omnibase.documents.router import router
        from omnibase.tenants.dependencies import TenantContext, get_current_tenant

        app = FastAPI()
        app.include_router(router)

        fake_tenant_orm = MagicMock()
        fake_tenant_orm.id = "fake-tenant-id"
        fake_tenant_orm.schema_name = "tenant_a1b2c3d4e5"

        async def fake_tenant() -> TenantContext:
            return TenantContext(tenant=fake_tenant_orm)

        app.dependency_overrides[get_current_tenant] = fake_tenant

        def fake_upload(**kwargs: object) -> UploadResult:
            doc = FakeDocument(
                id="router-test-doc",
                filename=kwargs.get("filename", "test.pdf"),
                mime_type=kwargs.get("content_type", "application/pdf"),
                size_bytes=len(kwargs.get("data", b"")),
                status="queued",
                minio_key=f"tenant_a1b2c3d4e5/router-test-doc/{kwargs.get('filename', 'test.pdf')}",
            )
            return UploadResult(
                document=DocumentRead.model_validate(doc),
                metadata_extracted=False,
            )

        monkeypatch.setattr(
            "omnibase.documents.router.upload_document",
            fake_upload,
        )

        with TestClient(app) as client:
            yield client

    def test_upload_returns_202_status(self, client_with_mocks: TestClient) -> None:
        """Given: a multipart upload POST,
        When: the service layer successfully enqueues,
        Then: the HTTP response has status 202 Accepted.
        """
        response = client_with_mocks.post(
            "/documents",
            files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert response.status_code == 202, (
            f"Expected HTTP 202, got {response.status_code}"
        )

    def test_upload_response_indicates_queued_status(
        self, client_with_mocks: TestClient
    ) -> None:
        """Given: a successful upload+enqueue,
        Then: the document status is 'queued' in the response.
        """
        response = client_with_mocks.post(
            "/documents",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
        body = response.json()
        assert body["document"]["status"] == "queued", (
            f"Expected queued status, got {body['document']['status']}"
        )

    def test_upload_response_is_valid_schema(self, client_with_mocks: TestClient) -> None:
        """Given: a 202 response,
        Then: the body validates against DocumentUploadResponse schema.
        """
        response = client_with_mocks.post(
            "/documents",
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        )
        body = response.json()
        parsed = DocumentUploadResponse.model_validate(body)
        assert parsed.document.status == "queued"
