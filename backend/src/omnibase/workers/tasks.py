"""Celery tasks for document ingest.

The task accepts only durable identifiers, downloads the source object, and
runs all tenant database work in an explicit tenant scope. Only a narrow set
of infrastructure failures is retried; terminal failures are persisted once.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from urllib3.exceptions import (
    ConnectTimeoutError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
)

from omnibase.control_plane.models import ResourceRecord
from omnibase.core.config import get_settings
from omnibase.core.db import get_session_factory
from omnibase.core.logging import get_logger
from omnibase.db.models import Tenant
from omnibase.db.tenant import Document
from omnibase.rag.ingest import ingest_document
from omnibase.storage.minio_client import get_minio_client
from omnibase.tenants.context import tenant_scope
from omnibase.workers.app import celery_app
from omnibase.workers.backfill import backfill_document

log = get_logger(__name__)

_ERROR_DETAIL_MAX_LEN = 1000
_TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OperationalError,
    ConnectTimeoutError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
)


class _DocumentMissingError(Exception):
    """Signal that a deleted document should stop processing without failure."""


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    name="ingest_document_task",
)
def ingest_document_task(
    self: Any,
    schema_name: str,
    document_id: str,
    minio_key: str,
    filename: str,
    mime_type: str,
) -> dict[str, Any]:
    """Ingest a document from MinIO into the RAG pipeline."""
    log.info(
        "task.ingest.start",
        document_id=document_id,
        filename=filename,
        schema=schema_name,
    )

    try:
        file_data = _download_file(minio_key)
        if not file_data:
            raise ValueError("Downloaded object is empty")

        with tenant_scope(schema_name):
            _process_ingest(
                schema_name=schema_name,
                document_id=document_id,
                file_data=file_data,
                filename=filename,
                mime_type=mime_type,
            )
    except _DocumentMissingError:
        log.info("task.ingest.document_missing_noop", document_id=document_id)
        return {"document_id": document_id, "status": "missing", "error_detail": None}
    except _TRANSIENT_EXCEPTIONS as exc:
        return _retry_or_fail(
            self,
            schema_name=schema_name,
            document_id=document_id,
            exc=exc,
        )
    except Exception as exc:
        error_detail = _safe_error_detail(exc)
        log.warning(
            "task.ingest.terminal_failure",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        try:
            _set_document_failed(
                schema_name=schema_name,
                document_id=document_id,
                error_detail=error_detail,
            )
        except _TRANSIENT_EXCEPTIONS as persist_exc:
            return _retry_or_fail(
                self,
                schema_name=schema_name,
                document_id=document_id,
                exc=persist_exc,
            )
        return {
            "document_id": document_id,
            "status": "failed",
            "error_detail": error_detail,
        }

    log.info(
        "task.ingest.complete",
        document_id=document_id,
        filename=filename,
        status="indexed",
    )
    return {"document_id": document_id, "status": "indexed", "error_detail": None}


def _download_file(minio_key: str) -> bytes:
    """Download an object and always release the underlying HTTP response."""
    settings = get_settings()
    client = get_minio_client(settings)
    response = client.get_object(settings.minio_bucket, minio_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _retry_or_fail(
    task: Any,
    *,
    schema_name: str,
    document_id: str,
    exc: BaseException,
) -> dict[str, Any]:
    """Explicitly retry a transient failure or persist failure at exhaustion."""
    retries = task.request.retries
    max_retries = task.max_retries
    if retries < max_retries:
        log.warning(
            "task.ingest.retry",
            document_id=document_id,
            error_type=type(exc).__name__,
            retry=retries + 1,
            max_retries=max_retries,
        )
        raise task.retry(exc=exc)

    error_detail = _safe_error_detail(exc, retry_exhausted=True)
    log.error(
        "task.ingest.retries_exhausted",
        document_id=document_id,
        error_type=type(exc).__name__,
        max_retries=max_retries,
    )
    _set_document_failed(
        schema_name=schema_name,
        document_id=document_id,
        error_detail=error_detail,
    )
    return {
        "document_id": document_id,
        "status": "failed",
        "error_detail": error_detail,
    }


def _safe_error_detail(
    error: BaseException | str,
    *,
    retry_exhausted: bool = False,
) -> str:
    """Return bounded failure information without persisting exception text."""
    if isinstance(error, BaseException):
        failure_type = type(error).__name__
        detail = f"Document ingestion failed ({failure_type})"
    else:
        detail = "Document parsing failed"

    if retry_exhausted:
        detail = f"{detail}; retries exhausted"
    return detail[:_ERROR_DETAIL_MAX_LEN]


def _process_ingest(
    *,
    schema_name: str,
    document_id: str,
    file_data: bytes,
    filename: str,
    mime_type: str,
) -> None:
    """Update status, run the ingest pipeline, and persist its outcome.

    This function runs inside ``tenant_scope``.
    """
    factory = get_session_factory()

    session = factory()
    try:
        stmt = select(Document).where(Document.id == document_id)
        doc = session.execute(stmt).scalar_one_or_none()
        if doc is None:
            raise _DocumentMissingError

        doc.status = "processing"
        doc.error_detail = None
        session.commit()
    finally:
        session.close()

    result = ingest_document(
        schema_name=schema_name,
        document_id=document_id,
        file_data=file_data,
        filename=filename,
        mime_type=mime_type,
    )

    session = factory()
    try:
        stmt = select(Document).where(Document.id == document_id)
        doc = session.execute(stmt).scalar_one_or_none()
        if doc is None:
            raise _DocumentMissingError

        if result.parse_error:
            doc.status = "indexed" if result.chunks_created > 0 else "failed"
            doc.error_detail = _safe_error_detail(result.parse_error)
        else:
            doc.status = "indexed"
            doc.error_detail = None

        resource = session.execute(
            select(ResourceRecord)
            .join(Tenant, Tenant.id == ResourceRecord.tenant_id)
            .where(
                ResourceRecord.id == document_id,
                ResourceRecord.kind == "document",
                ResourceRecord.policy_class == "workspace_private",
                ResourceRecord.state == "provisioning",
                Tenant.schema_name == schema_name,
            )
        ).scalar_one_or_none()
        if resource is not None:
            resource.state = "active" if doc.status == "indexed" else "failed"
            resource.version += 1

        doc.metadata_ = {
            **(doc.metadata_ or {}),
            "rag_chunks": result.chunks_created,
            "rag_embedded": result.chunks_embedded,
        }
        session.commit()
    finally:
        session.close()


def _set_document_failed(
    *,
    schema_name: str,
    document_id: str,
    error_detail: str,
) -> None:
    """Persist failed status in an independent tenant-scoped session."""
    with tenant_scope(schema_name):
        factory = get_session_factory()
        session = factory()
        try:
            stmt = select(Document).where(Document.id == document_id)
            doc = session.execute(stmt).scalar_one_or_none()
            if doc is None:
                log.info(
                    "task.ingest.document_missing_on_fail_noop",
                    document_id=document_id,
                )
                return

            doc.status = "failed"
            doc.error_detail = error_detail[:_ERROR_DETAIL_MAX_LEN]
            resource = session.execute(
                select(ResourceRecord)
                .join(Tenant, Tenant.id == ResourceRecord.tenant_id)
                .where(
                    ResourceRecord.id == document_id,
                    ResourceRecord.kind == "document",
                    ResourceRecord.policy_class == "workspace_private",
                    ResourceRecord.state == "provisioning",
                    Tenant.schema_name == schema_name,
                )
            ).scalar_one_or_none()
            if resource is not None:
                resource.state = "failed"
                resource.version += 1
            session.commit()
        finally:
            session.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    acks_late=True,
    queue="omnibase.backfill",
    name="backfill_document_v2_task",
)
def backfill_document_v2_task(
    self: Any,
    schema_name: str,
    document_id: str,
) -> dict[str, Any]:
    """Identifier-only, finite-retry v2 shadow backfill task."""
    try:
        result = backfill_document(schema_name, document_id)
        return {
            "document_id": result.document_id,
            "status": result.status,
            "chunks_upserted": result.chunks_upserted,
            "attempt_count": result.attempt_count,
        }
    except _TRANSIENT_EXCEPTIONS as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return {
            "document_id": document_id,
            "status": "failed",
            "chunks_upserted": 0,
            "attempt_count": self.request.retries + 1,
        }
    except Exception as exc:
        log.warning(
            "task.backfill.terminal_failure",
            document_id=document_id,
            error_type=type(exc).__name__,
        )
        return {
            "document_id": document_id,
            "status": "failed",
            "chunks_upserted": 0,
            "attempt_count": self.request.retries + 1,
        }


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
    queue="omnibase.backfill",
    name="backfill_all_documents_v2_task",
)
def backfill_all_documents_v2_task(
    self: Any,
    schema_name: str,
) -> dict[str, Any]:
    """Enumerate indexed documents and dispatch v2 backfill for each.

    Idempotent: skips documents that already have a 'ready' v2 index state.
    Returns counts of dispatched, skipped, and failed dispatches.
    """
    from sqlalchemy import select, text

    from omnibase.core.db import get_session_factory

    log.info("task.backfill_all.start", schema=schema_name)
    factory = get_session_factory()
    dispatched = 0
    skipped = 0
    failed_dispatch = 0

    with tenant_scope(schema_name):
        session = factory()
        try:
            # Get all indexed documents
            docs = (
                session.execute(select(Document.id).where(Document.status == "indexed"))
                .scalars()
                .all()
            )

            # Get documents that already have ready v2 state
            ready_docs = set()
            try:
                ready_rows = (
                    session.execute(
                        text(
                            "SELECT document_id FROM rag_document_index_state "
                            "WHERE index_version = 2 AND readiness = 'ready'"
                        )
                    )
                    .scalars()
                    .all()
                )
                ready_docs = set(ready_rows)
            except Exception:
                pass  # Table may not exist yet; proceed with all docs

            for doc_id in docs:
                if doc_id in ready_docs:
                    skipped += 1
                    continue
                try:
                    backfill_document_v2_task.delay(schema_name, doc_id)
                    dispatched += 1
                except Exception as exc:
                    log.warning(
                        "task.backfill_all.dispatch_failed",
                        document_id=doc_id,
                        error_type=type(exc).__name__,
                    )
                    failed_dispatch += 1
        finally:
            session.close()

    result = {
        "schema": schema_name,
        "total_indexed": len(docs),
        "dispatched": dispatched,
        "skipped": skipped,
        "failed_dispatch": failed_dispatch,
    }
    log.info("task.backfill_all.complete", **result)
    return result


__all__ = [
    "backfill_all_documents_v2_task",
    "backfill_document_v2_task",
    "ingest_document_task",
]
