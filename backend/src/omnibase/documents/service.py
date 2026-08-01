"""Documents service - upload / download / list / delete.

Coordinates three layers:
- MinIO for object storage (raw file bytes)
- Per-tenant PostgreSQL schema for metadata (documents table)
- PDF metadata extraction (B6) - runs synchronously in Phase 0

Multi-tenancy:
- All operations are scoped to the current tenant's schema via contextvars
  (see omnibase.tenants.context.tenant_scope). The SQLAlchemy after_begin
  event hook reads the contextvar and auto-applies SET search_path.
- MinIO keys are namespaced: <tenant_schema>/<document_id>/<filename>
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from sqlalchemy import desc, func, select, update

from omnibase.core.config import Settings, get_settings
from omnibase.core.db import get_session_factory
from omnibase.core.logging import get_logger
from omnibase.db.tenant import Document
from omnibase.documents.enqueue import enqueue_ingest
from omnibase.storage.minio_client import get_minio_client
from omnibase.tenants.context import tenant_scope
from omnibase.tenants.schema_manager import validate_schema_name

log = get_logger(__name__)


# -----------------------------------------------------------
# Errors
# -----------------------------------------------------------
class DocumentError(Exception):
    """Base class for document business errors."""


class InvalidFile(DocumentError):
    """File rejected by validation (size/type)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class DocumentNotFound(DocumentError):
    """Document id does not exist in this tenant."""


class DocumentDeleteConflict(DocumentError):
    """Document cannot be deleted while ingestion may still be running."""

    def __init__(self, document_id: str, document_status: str) -> None:
        super().__init__(
            f"Document {document_id} cannot be deleted while status is {document_status!r}"
        )
        self.document_id = document_id
        self.document_status = document_status


class StorageError(DocumentError):
    """Underlying object storage (MinIO) operation failed."""


# -----------------------------------------------------------
# Validation
# -----------------------------------------------------------
def validate_upload(
    *,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    settings: Settings | None = None,
) -> None:
    """Validate a candidate upload. Raises InvalidFile on rejection."""
    settings = settings or get_settings()

    if not filename or len(filename) > 255:
        raise InvalidFile("invalid_filename", "Filename must be 1-255 characters")

    if size_bytes <= 0:
        raise InvalidFile("empty_file", "File is empty")
    if size_bytes > settings.max_upload_size_bytes:
        raise InvalidFile(
            "file_too_large",
            f"File exceeds {settings.max_upload_size_mb} MB limit",
        )

    if content_type and content_type not in settings.allowed_mime_types:
        raise InvalidFile(
            "unsupported_type",
            f" MIME type {content_type!r} not allowed. "
            f"Allowed: {', '.join(settings.allowed_mime_types)}",
        )


# -----------------------------------------------------------
# MinIO key helpers
# -----------------------------------------------------------
def make_minio_key(schema_name: str, document_id: str, filename: str) -> str:
    """Build a MinIO object key namespaced by tenant schema.

    Format: <schema>/<doc_id>/<filename>
    """
    validate_schema_name(schema_name)
    safe_filename = filename.replace("\\", "/").split("/")[-1]
    if not safe_filename:
        safe_filename = "unnamed"
    return f"{schema_name}/{document_id}/{safe_filename}"


# -----------------------------------------------------------
# Upload
# -----------------------------------------------------------
@dataclass(frozen=True)
class UploadResult:
    """Result of a successful upload."""

    document: Document
    metadata_extracted: bool


def upload_document(
    *,
    schema_name: str,
    filename: str,
    content_type: str | None,
    data: bytes,
    settings: Settings | None = None,
    extract_metadata: bool = True,
) -> UploadResult:
    """Upload a file to MinIO + record metadata row."""
    settings = settings or get_settings()
    size_bytes = len(data)
    validate_upload(
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        settings=settings,
    )

    if content_type is None:
        content_type = _guess_mime_type(filename) or "application/octet-stream"

    document_id = str(uuid.uuid4())
    minio_key = make_minio_key(schema_name, document_id, filename)

    # 1. Upload to MinIO
    client = get_minio_client(settings)
    try:
        stream = io.BytesIO(data)
        client.put_object(
            bucket_name=settings.minio_bucket,
            object_name=minio_key,
            data=stream,
            length=size_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        log.error(
            "document.minio_upload_failed",
            minio_key=minio_key,
            error=str(exc),
        )
        raise StorageError(f"Failed to upload file: {exc}") from exc

    # 2. Insert metadata row + extract metadata (search_path auto-set via contextvar)
    factory = get_session_factory(settings)
    metadata_extracted = False
    with tenant_scope(schema_name):
        session = factory()
        try:
            document = Document(
                id=document_id,
                filename=filename,
                mime_type=content_type,
                size_bytes=size_bytes,
                status="pending",
                minio_key=minio_key,
                page_count=None,
                metadata_={},
            )
            session.add(document)
            session.commit()
            session.refresh(document)

            log.info(
                "document.uploaded",
                document_id=document.id,
                filename=filename,
                size_bytes=size_bytes,
                mime_type=content_type,
                minio_key=minio_key,
            )

            # 3. Optional synchronous metadata extraction (B6)
            #    Metadata extraction is best-effort and does not introduce a
            #    separate lifecycle state.
            if extract_metadata:
                try:
                    from omnibase.documents.metadata import extract_pdf_metadata

                    meta = extract_pdf_metadata(data, content_type)
                    if meta:
                        document.page_count = meta.get("page_count")
                        document.metadata_ = meta
                        metadata_extracted = True
                        log.info(
                            "document.metadata_extracted",
                            document_id=document.id,
                            page_count=meta.get("page_count"),
                        )
                except Exception as exc:
                    log.warning(
                        "document.metadata_extraction_failed",
                        document_id=document.id,
                        error=str(exc)[:200],
                    )

            # 4. Persist queued before dispatch. A worker may start immediately
            #    after the broker accepts the task, so upload must never write
            #    queued after dispatch and overwrite processing/indexed/failed.
            document.status = "queued"
            document.error_detail = None
            session.commit()
            session.refresh(document)

            # Only five durable identifiers are passed — no bytes, headers,
            # credentials, or request context.
            try:
                enqueued = enqueue_ingest(
                    schema_name=schema_name,
                    document_id=document.id,
                    minio_key=document.minio_key,
                    filename=filename,
                    mime_type=content_type,
                )
            except Exception:
                log.exception(
                    "document.enqueue_unexpected_failure",
                    document_id=document.id,
                )
                enqueued = False

            if enqueued:
                log.info(
                    "document.enqueued",
                    document_id=document.id,
                    filename=filename,
                )
            else:
                # Compensate only while the row is still queued. This compare-
                # and-set prevents a concurrent worker or duplicate delivery
                # from having its newer lifecycle state overwritten.
                compensation = session.execute(
                    update(Document)
                    .where(Document.id == document.id, Document.status == "queued")
                    .values(
                        status="failed",
                        error_detail="Failed to enqueue ingestion task",
                    )
                    .execution_options(synchronize_session=False)
                )
                compensated = getattr(compensation, "rowcount", 0) == 1
                if compensated:
                    document.status = "failed"
                    document.error_detail = "Failed to enqueue ingestion task"
                session.commit()
                session.refresh(document)
                log.warning(
                    "document.enqueue_failed",
                    document_id=document.id,
                    filename=filename,
                    compensated=compensated,
                )

            return UploadResult(document=document, metadata_extracted=metadata_extracted)
        finally:
            session.close()


# -----------------------------------------------------------
# List
# -----------------------------------------------------------
def list_documents(
    *,
    schema_name: str,
    limit: int = 50,
    offset: int = 0,
    status_filter: str | None = None,
    settings: Settings | None = None,
) -> tuple[list[Document], int]:
    """Return (documents, total_count) for the current tenant."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).order_by(desc(Document.created_at)).limit(limit).offset(offset)
            if status_filter:
                stmt = stmt.where(Document.status == status_filter)

            documents = list(session.execute(stmt).scalars())

            count_stmt = select(func.count()).select_from(Document)
            if status_filter:
                count_stmt = count_stmt.where(Document.status == status_filter)
            total = int(session.execute(count_stmt).scalar() or 0)

            return documents, total
        finally:
            session.close()


# -----------------------------------------------------------
# Get single document
# -----------------------------------------------------------
def get_document(
    *,
    schema_name: str,
    document_id: str,
    settings: Settings | None = None,
) -> Document:
    """Fetch a single document by id (raises DocumentNotFound)."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).where(Document.id == document_id)
            document = session.execute(stmt).scalar_one_or_none()
            if document is None:
                raise DocumentNotFound(f"Document {document_id} not found")
            return document
        finally:
            session.close()


# -----------------------------------------------------------
# Presigned download URL
# -----------------------------------------------------------
def get_download_url(
    *,
    schema_name: str,
    document_id: str,
    expires_seconds: int = 3600,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Return (presigned_url, filename) for a document."""
    from datetime import timedelta

    settings = settings or get_settings()
    document = get_document(schema_name=schema_name, document_id=document_id, settings=settings)

    client = get_minio_client(settings)
    try:
        url = client.presigned_get_object(
            bucket_name=settings.minio_bucket,
            object_name=document.minio_key,
            expires=timedelta(seconds=expires_seconds),
        )
    except Exception as exc:
        log.error(
            "document.presigned_url_failed",
            document_id=document_id,
            minio_key=document.minio_key,
            error=str(exc),
        )
        raise StorageError(f"Failed to generate download URL: {exc}") from exc

    return str(url), document.filename


# -----------------------------------------------------------
# Delete
# -----------------------------------------------------------
def delete_document(
    *,
    schema_name: str,
    document_id: str,
    settings: Settings | None = None,
) -> None:
    """Delete document metadata + MinIO object."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).where(Document.id == document_id).with_for_update()
            document = session.execute(stmt).scalar_one_or_none()
            if document is None:
                raise DocumentNotFound(f"Document {document_id} not found")
            if document.status in {"pending", "queued", "processing"}:
                raise DocumentDeleteConflict(document.id, document.status)

            minio_key = document.minio_key
            filename = document.filename

            session.delete(document)
            session.commit()
        finally:
            session.close()

    # Delete from MinIO (best-effort: log if fails, don't fail the request)
    client = get_minio_client(settings)
    try:
        client.remove_object(settings.minio_bucket, minio_key)
    except Exception as exc:
        log.warning(
            "document.minio_delete_failed",
            document_id=document_id,
            minio_key=minio_key,
            error=str(exc),
        )

    log.info(
        "document.deleted",
        document_id=document_id,
        filename=filename,
        minio_key=minio_key,
    )


# -----------------------------------------------------------
# MIME-type guessing (fallback when client doesn't send Content-Type)
# -----------------------------------------------------------
_EXTENSION_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _guess_mime_type(filename: str) -> str | None:
    """Infer MIME type from extension. Returns None if unknown."""
    import os

    _, ext = os.path.splitext(filename.lower())
    return _EXTENSION_MAP.get(ext)


__all__ = [
    "DocumentDeleteConflict",
    "DocumentError",
    "DocumentNotFound",
    "InvalidFile",
    "StorageError",
    "UploadResult",
    "delete_document",
    "get_document",
    "get_download_url",
    "list_documents",
    "make_minio_key",
    "upload_document",
    "validate_upload",
]
