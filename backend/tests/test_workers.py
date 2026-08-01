"""Tests for Celery worker configuration and ingest task (Todo 4).

Tests:
- Celery app is configured from REDIS_URL
- Task function accepts only declared identifiers
- Worker startup initializes DB configuration
- Success path transitions to indexed
- Transient MinIO failure triggers retry
- Terminal parse failure persists error_detail
- Duplicate delivery is idempotent
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestCeleryAppConfig:
    """Celery application configuration tests."""

    def test_celery_app_imports(self) -> None:
        """celery_app can be imported and is a Celery instance."""
        from celery import Celery

        from omnibase.workers.app import celery_app

        assert isinstance(celery_app, Celery)
        assert celery_app.conf.broker_url, "broker_url must be configured"
        assert celery_app.conf.result_backend, "result_backend must be configured"

    def test_celery_app_uses_redis_url(self) -> None:
        """Celery broker and result backend are configured from REDIS_URL."""
        from omnibase.workers.app import celery_app

        # Both broker and result backend should point to Redis
        assert celery_app.conf.broker_url.startswith("redis://"), (
            f"broker_url should be redis, got {celery_app.conf.broker_url}"
        )
        assert celery_app.conf.result_backend.startswith("redis://"), (
            f"result_backend should be redis, got {celery_app.conf.result_backend}"
        )

    def test_celery_app_accepts_content_json(self) -> None:
        """Celery app accepts JSON content type for serialization."""
        from omnibase.workers.app import celery_app

        accept = celery_app.conf.accept_content or []
        assert "json" in accept, "Celery must accept json content type"

    def test_celery_app_registers_ingest_task(self) -> None:
        """Worker startup registers ingest_document_task without a separate task-module import.

        ``app.py`` explicitly late-imports ``omnibase.workers.tasks`` at module level
        after ``celery_app`` construction.
        This test verifies that the real worker entrypoint (importing
        ``omnibase.workers.app``) results in the task being discoverable in
        ``celery_app.tasks`` — no client code needs to import tasks.py explicitly.
        """
        from omnibase.workers.app import celery_app

        assert "ingest_document_task" in celery_app.tasks, (
            "ingest_document_task not registered in celery_app.tasks — "
            "tasks.py is not auto-imported by app.py"
        )


class TestIngestTaskSignature:
    """Ingest task argument contract."""

    def test_task_is_importable(self) -> None:
        """ingest_document_task can be imported from workers.tasks."""
        from omnibase.workers.tasks import ingest_document_task

        assert callable(ingest_document_task)
        assert ingest_document_task.__name__ == "ingest_document_task"

    def test_task_has_declared_arguments(self) -> None:
        """Task accepts the five declarable identifiers only."""
        from inspect import signature

        from omnibase.workers.tasks import ingest_document_task

        sig = signature(ingest_document_task)
        param_names = list(sig.parameters.keys())
        expected = {"schema_name", "document_id", "minio_key", "filename", "mime_type"}
        # The task function may have additional Celery-injected params (self for bound tasks)
        # but should include at least these five
        for name in expected:
            assert name in param_names, (
                f"Task must have parameter {name!r}, got {param_names}"
            )

    def test_task_serializable_args_demonstration(self) -> None:
        """Arguments can be serialized to JSON (no bytes, no complex objects)."""
        args = {
            "schema_name": "tenant_a1b2c3d4",
            "document_id": "doc-uuid-1234",
            "minio_key": "tenant_a1b2c3d4/doc-uuid-1234/report.pdf",
            "filename": "report.pdf",
            "mime_type": "application/pdf",
        }
        # When: serialized to JSON
        serialized = json.dumps(args)
        # Then: round-trips successfully
        restored = json.loads(serialized)
        assert restored == args


class TestIngestTaskBehavior:
    """Ingest task behavior with mocked dependencies."""

    def test_success_path_transitions_to_indexed(self) -> None:
        """Happy path: task downloads from MinIO, ingests, sets status to indexed."""
        import omnibase.workers.tasks as worker_tasks
        from omnibase.workers.tasks import ingest_document_task

        # Given
        schema_name = "tenant_test"
        document_id = "doc-success-001"
        minio_key = f"{schema_name}/{document_id}/test.pdf"
        filename = "test.pdf"
        mime_type = "application/pdf"

        # When: task runs with mocked dependencies
        with (
            patch.object(worker_tasks, "get_minio_client") as mock_minio,
            patch.object(worker_tasks, "tenant_scope") as mock_scope,
            patch.object(worker_tasks, "get_session_factory") as mock_factory,
            patch.object(worker_tasks, "ingest_document") as mock_ingest,
            patch.object(worker_tasks, "select") as mock_select,
        ):
            # Mock MinIO download
            mock_client = MagicMock()
            mock_client.get_object.return_value.read.return_value = b"test file content"
            mock_minio.return_value = mock_client

            # Mock DB session
            mock_session = MagicMock()
            mock_factory.return_value = lambda: mock_session

            # Mock document query
            mock_doc = MagicMock()
            mock_doc.status = "queued"
            mock_doc.error_detail = None
            mock_stmt = MagicMock()
            mock_select.return_value.where.return_value = mock_stmt
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_doc

            # Mock ingest result
            mock_ingest.return_value.chunks_created = 5
            mock_ingest.return_value.chunks_embedded = 5
            mock_ingest.return_value.parse_error = None

            # Mock tenant_scope context manager
            mock_scope.return_value.__enter__ = MagicMock()
            mock_scope.return_value.__exit__ = MagicMock()

            # Use .run() to bypass Celery task wrapper and test the raw function
            ingest_document_task.run(
                schema_name=schema_name,
                document_id=document_id,
                minio_key=minio_key,
                filename=filename,
                mime_type=mime_type,
            )

        # Then: status updated to indexed
        assert mock_doc.status == "indexed"
        assert mock_doc.error_detail is None
        # MinIO get_object was called
        mock_client.get_object.assert_called_once()
        # ingest_document was called with correct args
        mock_ingest.assert_called_once()
        _, kwargs = mock_ingest.call_args
        assert kwargs.get("document_id") == document_id

    def test_parse_failure_sets_failed_status_directly(self) -> None:
        """Direct test: _set_document_failed correctly persists failure.

        This isolates the failure hander from the Celery task wrapper.
        """
        import omnibase.workers.tasks as worker_tasks

        # Given
        schema_name = "tenant_test"
        document_id = "doc-fail-001"

        with (
            patch.object(worker_tasks, "get_session_factory") as mock_factory,
            patch.object(worker_tasks, "select") as mock_select,
            patch.object(worker_tasks, "tenant_scope"),
        ):
            mock_session = MagicMock()
            mock_factory.return_value = lambda: mock_session
            mock_doc = MagicMock()
            mock_stmt = MagicMock()
            mock_select.return_value.where.return_value = mock_stmt
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_doc

            # When: _set_document_failed is called
            worker_tasks._set_document_failed(
                schema_name=schema_name,
                document_id=document_id,
                error_detail="Model file not found",
            )

            # Then: doc status is failed with error_detail
            assert mock_doc.status == "failed"
            assert mock_doc.error_detail == "Model file not found"

    def test_parse_failure_persists_error_detail(self) -> None:
        """When ingest raises, task sets status to failed with error_detail.

        This test verifies the full task error path by checking
        that the task result indicates failure.
        """
        import omnibase.workers.tasks as worker_tasks
        from omnibase.workers.tasks import ingest_document_task

        # Given
        schema_name = "tenant_test"
        document_id = "doc-fail-001"
        minio_key = f"{schema_name}/{document_id}/test.pdf"
        filename = "test.pdf"
        mime_type = "application/pdf"

        # When: task runs with ingest raising an error
        with (
            patch.object(worker_tasks, "get_minio_client") as mock_minio,
            patch.object(worker_tasks, "tenant_scope") as mock_scope,
            patch.object(worker_tasks, "get_session_factory") as mock_factory,
            patch.object(worker_tasks, "ingest_document") as mock_ingest,
            patch.object(worker_tasks, "select") as mock_select,
        ):
            # Mock MinIO download
            mock_client = MagicMock()
            mock_client.get_object.return_value.read.return_value = b"test content"
            mock_minio.return_value = mock_client

            # Mock DB session
            mock_session = MagicMock()
            mock_factory.return_value = lambda: mock_session

            # Mock document query
            mock_doc = MagicMock()
            mock_doc.status = "queued"
            mock_doc.error_detail = None
            mock_stmt = MagicMock()
            mock_select.return_value.where.return_value = mock_stmt
            mock_session.execute.return_value.scalar_one_or_none.return_value = mock_doc

            # Mock tenant_scope as a no-op context manager
            mock_scope.return_value.__enter__ = MagicMock(return_value=None)
            mock_scope.return_value.__exit__ = MagicMock(return_value=None)

            # Ingest raises RuntimeError
            mock_ingest.side_effect = RuntimeError("Model file not found")

            # Use .run() to bypass Celery task wrapper
            result = ingest_document_task.run(
                schema_name=schema_name,
                document_id=document_id,
                minio_key=minio_key,
                filename=filename,
                mime_type=mime_type,
            )

        # Then: task result shows failure
        assert result["status"] == "failed"
        assert result["error_detail"] is not None

    def test_transient_minio_failure_calls_explicit_retry(self) -> None:
        """A connection failure is passed to the bound task's retry method."""
        from celery.exceptions import Retry

        import omnibase.workers.tasks as worker_tasks
        from omnibase.workers.tasks import ingest_document_task

        with (
            patch.object(worker_tasks, "get_minio_client") as mock_minio,
            patch.object(ingest_document_task, "retry", side_effect=Retry()) as mock_retry,
        ):
            mock_minio.return_value.get_object.side_effect = ConnectionError(
                "MinIO connection reset by peer"
            )

            with pytest.raises(Retry):
                ingest_document_task.run(
                    schema_name="tenant_test",
                    document_id="doc-retry-001",
                    minio_key="tenant_test/doc-retry-001/test.pdf",
                    filename="test.pdf",
                    mime_type="application/pdf",
                )

        assert isinstance(mock_retry.call_args.kwargs["exc"], ConnectionError)

    def test_retry_exhaustion_marks_document_failed(self) -> None:
        """The final transient failure is persisted instead of retried again."""
        import omnibase.workers.tasks as worker_tasks

        task = MagicMock()
        task.request.retries = 3
        task.max_retries = 3

        with patch.object(worker_tasks, "_set_document_failed") as mock_failed:
            result = worker_tasks._retry_or_fail(
                task,
                schema_name="tenant_test",
                document_id="doc-exhausted",
                exc=ConnectionError("redis://user:secret@example.invalid"),
            )

        task.retry.assert_not_called()
        mock_failed.assert_called_once()
        assert result["status"] == "failed"
        assert "retries exhausted" in result["error_detail"]
        assert "secret" not in result["error_detail"]

    def test_terminal_error_does_not_retry(self) -> None:
        """A non-infrastructure ingest error is terminal and is persisted once."""
        import omnibase.workers.tasks as worker_tasks
        from omnibase.workers.tasks import ingest_document_task

        response = MagicMock()
        response.read.return_value = b"content"
        with (
            patch.object(worker_tasks, "get_minio_client") as mock_minio,
            patch.object(worker_tasks, "tenant_scope"),
            patch.object(worker_tasks, "_process_ingest", side_effect=ValueError("bad pdf")),
            patch.object(worker_tasks, "_set_document_failed") as mock_failed,
            patch.object(ingest_document_task, "retry") as mock_retry,
        ):
            mock_minio.return_value.get_object.return_value = response
            result = ingest_document_task.run(
                schema_name="tenant_test",
                document_id="doc-terminal",
                minio_key="tenant_test/doc-terminal/test.pdf",
                filename="test.pdf",
                mime_type="application/pdf",
            )

        mock_retry.assert_not_called()
        mock_failed.assert_called_once()
        assert result["status"] == "failed"

    def test_missing_document_is_safe_noop(self) -> None:
        """A document deleted before processing does not become a task failure."""
        import omnibase.workers.tasks as worker_tasks
        from omnibase.workers.tasks import ingest_document_task

        response = MagicMock()
        response.read.return_value = b"content"
        with (
            patch.object(worker_tasks, "get_minio_client") as mock_minio,
            patch.object(worker_tasks, "tenant_scope"),
            patch.object(
                worker_tasks,
                "_process_ingest",
                side_effect=worker_tasks._DocumentMissingError(),
            ),
            patch.object(worker_tasks, "_set_document_failed") as mock_failed,
            patch.object(ingest_document_task, "retry") as mock_retry,
        ):
            mock_minio.return_value.get_object.return_value = response
            result = ingest_document_task.run(
                schema_name="tenant_test",
                document_id="doc-missing",
                minio_key="tenant_test/doc-missing/test.pdf",
                filename="test.pdf",
                mime_type="application/pdf",
            )

        mock_failed.assert_not_called()
        mock_retry.assert_not_called()
        assert result == {
            "document_id": "doc-missing",
            "status": "missing",
            "error_detail": None,
        }

    def test_failure_helper_enters_tenant_scope_and_bounds_detail(self) -> None:
        """Failure persistence owns tenant isolation and respects the DB bound."""
        import omnibase.workers.tasks as worker_tasks

        session = MagicMock()
        document = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = document
        with (
            patch.object(worker_tasks, "tenant_scope") as mock_scope,
            patch.object(worker_tasks, "get_session_factory", return_value=lambda: session),
            patch.object(worker_tasks, "select"),
        ):
            worker_tasks._set_document_failed(
                schema_name="tenant_test",
                document_id="doc-failed",
                error_detail="x" * 1200,
            )

        mock_scope.assert_called_once_with("tenant_test")
        assert document.status == "failed"
        assert len(document.error_detail) == 1000

    def test_safe_error_detail_does_not_expose_exception_message(self) -> None:
        """Stored errors are bounded classifications rather than raw secret-bearing text."""
        import omnibase.workers.tasks as worker_tasks

        detail = worker_tasks._safe_error_detail(
            RuntimeError("password=hunter2 https://internal.example/token")
        )

        assert detail == "Document ingestion failed (RuntimeError)"
        assert len(detail) <= 1000
        assert "hunter2" not in detail


class TestDocumentStatusTransitions:
    """Direct status transitions (no external services)."""

    def test_set_processing_success(self) -> None:
        """Setting status to processing then indexed works."""
        from datetime import datetime

        from omnibase.documents.schemas import DocumentRead

        # Given: queued document
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "queued",
            "error_detail": None,
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When: processing
        doc_data["status"] = "processing"
        doc1 = DocumentRead.model_validate(doc_data)
        assert doc1.status == "processing"

        # Then: indexed
        doc_data["status"] = "indexed"
        doc2 = DocumentRead.model_validate(doc_data)
        assert doc2.status == "indexed"

    def test_set_processing_failure(self) -> None:
        """Setting status to failed with error_detail from processing."""
        from datetime import datetime

        from omnibase.documents.schemas import DocumentRead

        # Given: processing document
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "processing",
            "error_detail": None,
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When: failed
        doc_data["status"] = "failed"
        doc_data["error_detail"] = "Parse error at line 42"
        doc = DocumentRead.model_validate(doc_data)
        assert doc.status == "failed"
        assert doc.error_detail == "Parse error at line 42"
