"""Tests for Todo 6: enqueue failure safe contract.

Verifies:
- Enqueue failure produces safe document state (no credential leaks)
- Document is NOT falsely marked as 'queued' when enqueue fails
- Error detail is bounded and safe
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

# ────────────────────────────────────────────────────────────
# Test helpers — in-memory fakes
# ────────────────────────────────────────────────────────────


@dataclass
class FakeDocument:
    """In-memory fake of the Document ORM model for unit tests."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    minio_key: str
    page_count: int | None = None
    error_detail: str | None = None
    metadata_: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class FakeAsyncResult:
    """Minimal fake of celery.result.AsyncResult."""

    task_id: str


def _make_fake_minio_client() -> MagicMock:
    """Build a fake MinIO client whose put_object is a no-op."""
    client = MagicMock()
    client.put_object.return_value = None
    return client


def _make_fake_session_factory(document: FakeDocument) -> MagicMock:
    """Build a fake SQLAlchemy session factory."""
    session = MagicMock()
    session.add.return_value = None
    session.commit.return_value = None
    update_result = MagicMock()
    update_result.rowcount = 1
    session.execute.return_value = update_result

    def _fake_refresh(doc: FakeDocument) -> None:
        pass

    session.refresh.side_effect = _fake_refresh

    factory = MagicMock()
    factory.return_value = session
    return factory


def _patch_service_infra(
    monkeypatch: pytest.MonkeyPatch,
    fake_minio: MagicMock,
    fake_factory: MagicMock,
) -> None:
    """Monkeypatch MinIO and DB infrastructure in the service module."""
    monkeypatch.setattr(
        "omnibase.documents.service.get_minio_client", lambda _s: fake_minio
    )
    monkeypatch.setattr(
        "omnibase.documents.service.get_session_factory", lambda _s: fake_factory
    )


def _patch_celery_delay(
    monkeypatch: pytest.MonkeyPatch, delay_obj: object
) -> None:
    """Monkeypatch ingest_document_task.delay with the given callable."""
    from omnibase.workers.tasks import ingest_document_task

    monkeypatch.setattr(ingest_document_task, "delay", delay_obj)


# ────────────────────────────────────────────────────────────
# Enqueue failure safe contract
# ────────────────────────────────────────────────────────────


class TestEnqueueFailureSafeContract:
    """When Celery dispatch fails, the document enters a safe failure state
    with no leaked credentials or internal details."""

    def test_document_failed_when_enqueue_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: MinIO and DB persistence succeed,
        When: the Celery .delay() raises an exception,
        Then: the document status is 'failed' and error_detail is set.
        """
        from omnibase.documents.service import upload_document

        doc = FakeDocument(
            id="doc-fail-001",
            filename="broken.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-fail-001/broken.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        class FailingDelay:
            def __call__(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError("Broker unreachable")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FailingDelay())

        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="broken.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4",
            extract_metadata=False,
        )

        assert result.document.status == "failed", (
            f"Expected status 'failed', got {result.document.status!r}"
        )
        assert result.document.error_detail is not None

    def test_error_detail_excludes_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: enqueue fails with credential-bearing exception,
        Then: error_detail does NOT leak credentials or raw URLs.
        """
        from omnibase.documents.service import upload_document

        doc = FakeDocument(
            id="doc-leak-001",
            filename="leak.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-leak-001/leak.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        class FailingDelayWithCredentials:
            def __call__(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError(
                    "Error 111 connecting to redis://user:super-secret-pw@redis:6379/0"
                )

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FailingDelayWithCredentials())

        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="leak.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4",
            extract_metadata=False,
        )

        error = result.document.error_detail or ""
        assert "super-secret-pw" not in error, "Must not leak credentials"
        assert "redis://" not in error, "Must not contain raw broker URL"
        assert len(error) <= 1000, "error_detail must respect column bound"

    def test_failed_document_not_falsely_queued(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: enqueue fails,
        Then: the document is NOT reported as 'queued'.
        """
        from omnibase.documents.service import upload_document

        doc = FakeDocument(
            id="doc-notqueued-001",
            filename="nq.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-notqueued-001/nq.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        class FailingDelay:
            def __call__(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("Task serialization error")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FailingDelay())

        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="nq.pdf",
            content_type="application/pdf",
            data=b"%PDF",
            extract_metadata=False,
        )

        assert result.document.status != "queued", (
            "Document must NOT be 'queued' when enqueue failed"
        )

    def test_failed_compensation_is_compare_and_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A concurrent worker state is preserved if enqueue reports failure."""
        from omnibase.documents.service import upload_document

        doc = FakeDocument(
            id="doc-cas-001",
            filename="cas.pdf",
            mime_type="application/pdf",
            size_bytes=512,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-cas-001/cas.pdf",
        )
        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)
        session = fake_factory.return_value
        session.execute.return_value.rowcount = 0

        def fake_enqueue(**kwargs: object) -> bool:
            persisted = session.add.call_args.args[0]
            persisted.status = "processing"
            return False

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        monkeypatch.setattr("omnibase.documents.service.enqueue_ingest", fake_enqueue)

        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="cas.pdf",
            content_type="application/pdf",
            data=b"%PDF cas",
            extract_metadata=False,
        )

        assert result.document.status == "processing"
        update_stmt = session.execute.call_args.args[0]
        assert "documents.status = :status_1" in str(update_stmt)
