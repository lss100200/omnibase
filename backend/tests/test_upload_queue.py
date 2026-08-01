"""Tests for Todo 6: persisted upload queues with Celery enqueue.

Pure unit tests — no real Redis/MinIO/DB/model calls.

Verifies:
- Enqueue dispatch payload contains only five identifier strings (no bytes)
- Document status transitions to "queued" after successful enqueue
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from omnibase.documents.service import upload_document

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
    metadata_: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


def _make_fake_minio_client() -> MagicMock:
    """Build a fake MinIO client whose put_object is a no-op."""
    client = MagicMock()
    client.put_object.return_value = None
    return client


def _make_fake_session_factory(document: FakeDocument) -> MagicMock:
    """Build a fake SQLAlchemy session factory that returns a session
    pre-loaded with the given fake document."""
    session = MagicMock()
    # session.add + commit + refresh should update the in-memory document
    session.add.return_value = None
    session.commit.return_value = None
    update_result = MagicMock()
    update_result.rowcount = 1
    session.execute.return_value = update_result

    def _fake_refresh(doc: FakeDocument) -> None:
        pass  # in-memory fake — document already up to date

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
    """Monkeypatch ingest_document_task.delay with the given callable.

    Must be called AFTER conftest has set env vars (inside test body).
    """
    from omnibase.workers.tasks import ingest_document_task

    monkeypatch.setattr(ingest_document_task, "delay", delay_obj)


# ────────────────────────────────────────────────────────────
# 1. Enqueue payload — identifier-only dispatch
# ────────────────────────────────────────────────────────────


class TestEnqueuePayloadIdentifiersOnly:
    """The Celery .delay() call receives exactly five string identifiers."""

    def test_delay_receives_five_identifier_strings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: a valid upload of a small PDF,
        When: upload_document() runs with mocked infrastructure,
        Then: ingest_document_task.delay() is called with five string args
              (schema_name, document_id, minio_key, filename, mime_type)
              and NO bytes, credentials, headers, or request context.
        """
        # ── Setup fakes ──
        doc = FakeDocument(
            id="doc-abc-123",
            filename="report.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-abc-123/report.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        # Capture the .delay() call arguments
        delay_calls: list[tuple] = []

        class FakeDelay:
            """Stand-in for ingest_document_task.delay."""

            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                delay_calls.append((args, kwargs))
                return FakeAsyncResult(task_id="task-xyz-001")

        # Monkeypatch infrastructure
        monkeypatch.setattr(
            "omnibase.documents.service.get_minio_client", lambda _s: fake_minio
        )
        monkeypatch.setattr(
            "omnibase.documents.service.get_session_factory", lambda _s: fake_factory
        )
        # Patch the _enqueue_ingest_task helper to capture delay calls
        from omnibase.workers.tasks import ingest_document_task

        monkeypatch.setattr(ingest_document_task, "delay", FakeDelay())

        # ── When ──
        _ = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="report.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4 fake pdf content",
            extract_metadata=False,
        )

        # ── Then ──
        assert len(delay_calls) == 1, "Expected exactly one .delay() call"
        args, kwargs = delay_calls[0]

        # Must be positional args (not kwargs) with exactly 5 strings
        assert len(args) == 5, f"Expected 5 positional args, got {len(args)}: {args}"
        assert not kwargs, "Expected no keyword arguments to .delay()"

        schema_name, document_id, minio_key, filename, mime_type = args
        assert isinstance(schema_name, str)
        assert isinstance(document_id, str)
        assert isinstance(minio_key, str)
        assert isinstance(filename, str)
        assert isinstance(mime_type, str)

        # Verify identifier values
        assert schema_name == "tenant_a1b2c3d4e5"
        # document_id is a generated UUID, not the fake doc's id
        assert isinstance(document_id, str)
        assert len(document_id) == 36
        assert minio_key == f"tenant_a1b2c3d4e5/{document_id}/report.pdf"
        assert filename == "report.pdf"
        assert mime_type == "application/pdf"

        # Verify NO bytes passed
        for i, arg in enumerate(args):
            assert not isinstance(arg, bytes), (
                f"Arg {i} is bytes — must not pass file data: {arg!r}"
            )

    def test_delay_not_called_with_dict_or_complex_objects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: a valid upload,
        When: upload_document() runs with mocked infrastructure,
        Then: the .delay() call contains only simple JSON-serializable types
              (strings) — no dicts, lists, or objects.
        """
        doc = FakeDocument(
            id="doc-def-456",
            filename="notes.txt",
            mime_type="text/plain",
            size_bytes=256,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-def-456/notes.txt",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        delay_calls: list[tuple] = []

        class FakeDelay:
            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                delay_calls.append((args, kwargs))
                return FakeAsyncResult(task_id="task-xyz-002")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FakeDelay())

        # ── When ──
        upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="notes.txt",
            content_type="text/plain",
            data=b"hello world",
            extract_metadata=False,
        )

        # ── Then ──
        args, _ = delay_calls[0]
        for i, arg in enumerate(args):
            assert not isinstance(arg, (dict, list, tuple, set, bytes)), (
                f"Arg {i} is {type(arg).__name__} — must be plain string: {arg!r}"
            )


# ────────────────────────────────────────────────────────────
# 2. Document status — "queued" after successful enqueue
# ────────────────────────────────────────────────────────────


class TestDocumentStatusQueued:
    """Document transitions to 'queued' after Celery task is accepted."""

    def test_status_queued_after_successful_enqueue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Given: MinIO persistence and DB commit succeed,
        When: the Celery .delay() call returns without error,
        Then: the document status is set to 'queued'.
        """
        doc = FakeDocument(
            id="doc-queued-001",
            filename="paper.pdf",
            mime_type="application/pdf",
            size_bytes=4096,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-queued-001/paper.pdf",
        )

        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)

        class FakeDelay:
            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                # Simulate successful broker dispatch
                return FakeAsyncResult(task_id="task-ok-001")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, FakeDelay())

        # ── When ──
        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="paper.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4 test",
            extract_metadata=False,
        )

        # ── Then ──
        assert result.document.status == "queued", (
            f"Expected status 'queued', got {result.document.status!r}"
        )

    def test_queued_commit_happens_before_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The queued lifecycle state is durable before Celery dispatch."""
        doc = FakeDocument(
            id="doc-order-001",
            filename="order.pdf",
            mime_type="application/pdf",
            size_bytes=128,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-order-001/order.pdf",
        )
        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)
        session = fake_factory.return_value
        events: list[str] = []

        def record_commit() -> None:
            persisted = session.add.call_args.args[0]
            events.append(f"commit:{persisted.status}")

        class RecordingDelay:
            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                persisted = session.add.call_args.args[0]
                events.append(f"dispatch:{persisted.status}")
                return FakeAsyncResult(task_id="task-order-001")

        session.commit.side_effect = record_commit
        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, RecordingDelay())

        upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="order.pdf",
            content_type="application/pdf",
            data=b"%PDF order",
            extract_metadata=False,
        )

        assert events == ["commit:pending", "commit:queued", "dispatch:queued"]

    def test_worker_state_is_not_overwritten_after_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Upload does not commit queued again after a worker advances the row."""
        doc = FakeDocument(
            id="doc-race-001",
            filename="race.pdf",
            mime_type="application/pdf",
            size_bytes=128,
            status="pending",
            minio_key="tenant_a1b2c3d4e5/doc-race-001/race.pdf",
        )
        fake_minio = _make_fake_minio_client()
        fake_factory = _make_fake_session_factory(doc)
        session = fake_factory.return_value

        class AdvancingDelay:
            def __call__(self, *args: object, **kwargs: object) -> FakeAsyncResult:
                persisted = session.add.call_args.args[0]
                persisted.status = "processing"
                return FakeAsyncResult(task_id="task-race-001")

        _patch_service_infra(monkeypatch, fake_minio, fake_factory)
        _patch_celery_delay(monkeypatch, AdvancingDelay())

        result = upload_document(
            schema_name="tenant_a1b2c3d4e5",
            filename="race.pdf",
            content_type="application/pdf",
            data=b"%PDF race",
            extract_metadata=False,
        )

        assert result.document.status == "processing"
        assert session.commit.call_count == 2


# ────────────────────────────────────────────────────────────
# Fake async result (mimics Celery AsyncResult)
# ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeAsyncResult:
    """Minimal fake of celery.result.AsyncResult."""

    task_id: str
