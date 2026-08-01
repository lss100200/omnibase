"""Celery enqueue helper for document ingestion.

Extracted from documents/service.py to keep module size bounded.
Dispatches the ingest task with five durable identifiers — no bytes,
credentials, headers, or request context are serialized.
"""

from __future__ import annotations

from omnibase.core.logging import get_logger

log = get_logger(__name__)


def enqueue_ingest(
    *,
    schema_name: str,
    document_id: str,
    minio_key: str,
    filename: str,
    mime_type: str,
) -> bool:
    """Dispatch the document ingest task to Celery.

    Returns True if the broker accepted the task, False if dispatch failed.
    """
    # Lazy import: avoids module-level Celery broker connection at import time.
    from omnibase.workers.tasks import ingest_document_task

    try:
        ingest_document_task.delay(
            schema_name,
            document_id,
            minio_key,
            filename,
            mime_type,
        )
    except Exception:
        log.exception(
            "document.enqueue_broker_failed",
            document_id=document_id,
        )
        return False
    return True


__all__ = ["enqueue_ingest"]
