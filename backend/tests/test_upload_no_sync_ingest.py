"""Tests for Todo 6: no synchronous RAG ingestion in upload path.

Verifies upload_document() dispatches via Celery only and never calls
the synchronous ingest pipeline (omnibase.rag.ingest.ingest_document).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import MagicMock

import pytest

# ────────────────────────────────────────────────────────────
# Minimal fakes (duplicated from test_upload_queue.py to keep
# each test file self-contained and ≤250 LOC)
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


@dataclass(frozen=True)
class FakeAsyncResult:
    """Minimal fake of celery.result.AsyncResult."""

    task_id: str


def _make_fake_minio_client() -> MagicMock:
    client = MagicMock()
    client.put_object.return_value = None
    return client


def _make_fake_session_factory(document: FakeDocument) -> MagicMock:
    session = MagicMock()
    session.add.return_value = None
    session.commit.return_value = None

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
    monkeypatch.setattr(
        "omnibase.documents.service.get_minio_client", lambda _s: fake_minio
    )
    monkeypatch.setattr(
        "omnibase.documents.service.get_session_factory", lambda _s: fake_factory
    )


def _patch_celery_delay(
    monkeypatch: pytest.MonkeyPatch, delay_obj: object
) -> None:
    from omnibase.workers.tasks import ingest_document_task

    monkeypatch.setattr(ingest_document_task, "delay", delay_obj)


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


class TestNoSyncIngest:
    """upload_document() must not call the synchronous ingest pipeline."""

    def test_sync_ingest_function_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: a valid upload,
        When: upload_document() executes with mocked MinIO/DB,
        Then: omnibase.rag.ingest.ingest_document is NEVER called.
        """
        from omnibase.documents.service import upload_document

        doc = FakeDocument(
            id="doc-nosync-001",
            filename="doc.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-nosync-001/doc.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        sync_ingest_calls: list[object] = []

        def fake_sync_ingest(*args: object, **kwargs: object) -> object:
            sync_ingest_calls.append((args, kwargs))
            raise AssertionError("sync ingest must not be called")

        monkeypatch.setattr(
            "omnibase.rag.ingest.ingest_document", fake_sync_ingest
        )

        class FakeDelay:
            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                return FakeAsyncResult(task_id="task-nosync")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FakeDelay())

        _ = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="doc.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4",
            extract_metadata=False,
        )

        assert len(sync_ingest_calls) == 0, (
            "upload_document must NOT call omnibase.rag.ingest.ingest_document "
            "synchronously — ingestion is dispatched via Celery only"
        )

    def test_service_module_does_not_import_sync_ingest(self) -> None:
        """The service module source does not import ingest_document from
        omnibase.rag.ingest."""
        import inspect

        from omnibase.documents.service import upload_document

        source = inspect.getsource(upload_document)
        assert "from omnibase.rag.ingest import" not in source, (
            "service.py must not import ingest_document from omnibase.rag.ingest"
        )
        assert "omnibase.rag.ingest" not in source, (
            "service.py must not reference omnibase.rag.ingest"
        )
