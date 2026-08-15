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

import hashlib
import io
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import desc, exists, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from omnibase.control_plane.models import ResourceRecord
from omnibase.control_plane.service import register_resource
from omnibase.core.config import Settings, get_settings
from omnibase.core.db import get_session_factory
from omnibase.core.logging import get_logger
from omnibase.db.tenant import Document
from omnibase.documents.enqueue import enqueue_ingest
from omnibase.storage.minio_client import get_minio_client
from omnibase.tenants.context import tenant_scope
from omnibase.tenants.schema_manager import validate_schema_name
from omnibase.workspaces.models import ResourceScopeBinding, Workspace, WorkspaceMembership

log = get_logger(__name__)


class _ObjectRemovalClient(Protocol):
    def remove_object(self, bucket_name: str, object_name: str) -> object: ...


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


def _preflight_workspace_upload(
    *,
    schema_name: str,
    tenant_id: str | None,
    workspace_id: str | None,
    actor_user_id: str | None,
    settings: Settings,
) -> None:
    if workspace_id is None:
        return
    if not tenant_id or not actor_user_id:
        raise DocumentError("Workspace upload identity is required")
    _require_workspace_upload_access(
        schema_name=schema_name,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        settings=settings,
    )


def _revalidate_workspace_upload(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> None:
    """Revalidate the live Workspace aggregate under the stable lock order."""

    _require_workspace_upload_access_in_session(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        lock=True,
    )


def _compensate_uploaded_object(
    client: _ObjectRemovalClient,
    *,
    bucket_name: str,
    minio_key: str,
    document_id: str,
) -> bool:
    """Remove an object whose initial metadata transaction did not commit."""

    try:
        client.remove_object(bucket_name, minio_key)
    except Exception as cleanup_exc:
        log.warning(
            "document.workspace_upload_compensation_failed",
            document_id=document_id,
            error_type=type(cleanup_exc).__name__,
        )
        return False
    return True


def _mark_workspace_document_resource_failed(
    session: Session,
    *,
    document_id: str,
    tenant_id: str | None,
    workspace_id: str | None,
) -> None:
    if workspace_id is None or tenant_id is None:
        return
    session.execute(
        update(ResourceRecord)
        .where(
            ResourceRecord.id == document_id,
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.kind == "document",
            ResourceRecord.state == "provisioning",
        )
        .values(
            state="failed",
            version=ResourceRecord.version + 1,
        )
        .execution_options(synchronize_session=False)
    )


def _persist_initial_document(
    session: Session,
    *,
    document_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    minio_key: str,
    data_sha256: str,
    tenant_id: str | None,
    actor_user_id: str | None,
    workspace_id: str | None,
) -> Document:
    """Atomically create Document metadata and its optional Workspace binding."""

    workspace_bound = (
        workspace_id is not None and actor_user_id is not None and tenant_id is not None
    )
    if workspace_bound:
        assert tenant_id is not None
        assert workspace_id is not None
        assert actor_user_id is not None
        _revalidate_workspace_upload(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
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
    if workspace_bound:
        assert tenant_id is not None
        assert workspace_id is not None
        assert actor_user_id is not None
        register_resource(
            session,
            tenant_id=tenant_id,
            kind="document",
            owner_type="workspace",
            owner_id=workspace_id,
            display_name=filename,
            policy_class="workspace_private",
            resource_id=document_id,
            state="provisioning",
            metadata={
                "content_sha256": data_sha256,
                "media_type": content_type,
                "size_bytes": size_bytes,
            },
            created_by_actor_id=actor_user_id,
        )
        session.add(
            ResourceScopeBinding(
                resource_id=document_id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=workspace_id,
                version=1,
            )
        )
    session.commit()
    session.refresh(document)
    return document


def _extract_document_metadata(
    document: Document,
    *,
    data: bytes,
    content_type: str,
) -> bool:
    """Apply best-effort synchronous metadata without changing lifecycle state."""

    try:
        from omnibase.documents.metadata import extract_pdf_metadata

        metadata = extract_pdf_metadata(data, content_type)
        if not metadata:
            return False
        document.page_count = metadata.get("page_count")
        document.metadata_ = metadata
        log.info(
            "document.metadata_extracted",
            document_id=document.id,
            page_count=metadata.get("page_count"),
        )
        return True
    except Exception as exc:
        log.warning(
            "document.metadata_extraction_failed",
            document_id=document.id,
            error=str(exc)[:200],
        )
        return False


def upload_document(
    *,
    schema_name: str,
    filename: str,
    content_type: str | None,
    data: bytes,
    settings: Settings | None = None,
    extract_metadata: bool = True,
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
    workspace_id: str | None = None,
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

    _preflight_workspace_upload(
        schema_name=schema_name,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        settings=settings,
    )

    if content_type is None:
        content_type = _guess_mime_type(filename) or "application/octet-stream"

    document_id = str(uuid.uuid4())
    minio_key = make_minio_key(schema_name, document_id, filename)
    # Resolve the factory before creating the object so configuration failure
    # cannot leave an object without any possible metadata transaction.
    factory = get_session_factory(settings)

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
    metadata_extracted = False
    metadata_committed = False
    with tenant_scope(schema_name):
        session = factory()
        try:
            document = _persist_initial_document(
                session,
                document_id=document_id,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                minio_key=minio_key,
                data_sha256=hashlib.sha256(data).hexdigest(),
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
            )
            metadata_committed = True

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
                metadata_extracted = _extract_document_metadata(
                    document,
                    data=data,
                    content_type=content_type,
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
                    _mark_workspace_document_resource_failed(
                        session,
                        document_id=document.id,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                    )
                session.commit()
                session.refresh(document)
                log.warning(
                    "document.enqueue_failed",
                    document_id=document.id,
                    filename=filename,
                    compensated=compensated,
                )

            return UploadResult(document=document, metadata_extracted=metadata_extracted)
        except Exception as exc:
            session.rollback()
            if not metadata_committed:
                compensated = _compensate_uploaded_object(
                    client,
                    bucket_name=settings.minio_bucket,
                    minio_key=minio_key,
                    document_id=document_id,
                )
                if not compensated:
                    raise StorageError(
                        "Upload metadata failed and object cleanup could not be verified"
                    ) from exc
                if not isinstance(exc, DocumentError):
                    raise DocumentError("Failed to persist upload metadata") from exc
            raise
        finally:
            session.close()


def _require_workspace_upload_access(
    *,
    schema_name: str,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    settings: Settings,
) -> None:
    """Revalidate one active Workspace membership before object upload."""

    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            _require_workspace_upload_access_in_session(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                lock=False,
            )
        finally:
            session.close()


def _require_workspace_upload_access_in_session(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    lock: bool,
) -> None:
    """Validate Workspace then membership in stable aggregate lock order."""

    workspace_query = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.tenant_id == tenant_id,
        Workspace.desired_state != "archived",
        ~Workspace.observed_state.in_(("archiving", "archived", "failed")),
    )
    membership_query = select(WorkspaceMembership).where(
        WorkspaceMembership.tenant_id == tenant_id,
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == actor_user_id,
        WorkspaceMembership.state == "active",
        WorkspaceMembership.role.in_(("member", "operator", "maintainer", "owner")),
    )
    if lock:
        workspace_query = workspace_query.with_for_update()
        membership_query = membership_query.with_for_update()
    workspace = session.execute(workspace_query).scalar_one_or_none()
    membership = session.execute(membership_query).scalar_one_or_none()
    if workspace is None or membership is None:
        raise DocumentError("Workspace upload is unavailable")


def _document_visibility_clause(
    *,
    tenant_id: str | None,
    actor_user_id: str | None,
    write: bool,
) -> ColumnElement[bool] | None:
    """Return a public-endpoint visibility predicate for Workspace documents.

    Legacy tenant documents have no Workspace-private ResourceRecord and retain
    their existing tenant-level behavior.  Once a document is registered as
    Workspace-private, however, every Browser read or delete must prove the
    matching live Workspace membership.  Missing bindings fail closed.
    """

    if tenant_id is None or actor_user_id is None:
        return None
    private_resource = exists(
        select(ResourceRecord.id).where(
            ResourceRecord.id == Document.id,
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.kind == "document",
            ResourceRecord.policy_class == "workspace_private",
        )
    )
    allowed_roles = (
        ("member", "operator", "maintainer", "owner")
        if write
        else ("viewer", "member", "operator", "maintainer", "owner")
    )
    authorized_private_resource = exists(
        select(ResourceRecord.id)
        .join(
            ResourceScopeBinding,
            (ResourceScopeBinding.resource_id == ResourceRecord.id)
            & (ResourceScopeBinding.tenant_id == ResourceRecord.tenant_id),
        )
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.tenant_id == ResourceScopeBinding.tenant_id)
            & (WorkspaceMembership.workspace_id == ResourceScopeBinding.workspace_id),
        )
        .where(
            ResourceRecord.id == Document.id,
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.kind == "document",
            ResourceRecord.policy_class == "workspace_private",
            ResourceScopeBinding.scope_class == "workspace_private",
            WorkspaceMembership.user_id == actor_user_id,
            WorkspaceMembership.state == "active",
            WorkspaceMembership.role.in_(allowed_roles),
        )
    )
    return or_(~private_resource, authorized_private_resource)


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
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
) -> tuple[list[Document], int]:
    """Return (documents, total_count) for the current tenant."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).order_by(desc(Document.created_at)).limit(limit).offset(offset)
            visibility = _document_visibility_clause(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                write=False,
            )
            if visibility is not None:
                stmt = stmt.where(visibility)
            if status_filter:
                stmt = stmt.where(Document.status == status_filter)

            documents = list(session.execute(stmt).scalars())

            count_stmt = select(func.count()).select_from(Document)
            if visibility is not None:
                count_stmt = count_stmt.where(visibility)
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
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
) -> Document:
    """Fetch a single document by id (raises DocumentNotFound)."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).where(Document.id == document_id)
            visibility = _document_visibility_clause(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                write=False,
            )
            if visibility is not None:
                stmt = stmt.where(visibility)
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
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
) -> tuple[str, str]:
    """Return (presigned_url, filename) for a document."""
    from datetime import timedelta

    settings = settings or get_settings()
    document = get_document(
        schema_name=schema_name,
        document_id=document_id,
        settings=settings,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
    )

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
    tenant_id: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """Delete document metadata + MinIO object."""
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    with tenant_scope(schema_name):
        session = factory()
        try:
            stmt = select(Document).where(Document.id == document_id).with_for_update()
            visibility = _document_visibility_clause(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                write=True,
            )
            if visibility is not None:
                stmt = stmt.where(visibility)
            document = session.execute(stmt).scalar_one_or_none()
            if document is None:
                raise DocumentNotFound(f"Document {document_id} not found")
            if document.status in {"pending", "queued", "processing"}:
                raise DocumentDeleteConflict(document.id, document.status)

            minio_key = document.minio_key
            filename = document.filename

            if tenant_id is not None:
                resource = session.execute(
                    select(ResourceRecord).where(
                        ResourceRecord.id == document_id,
                        ResourceRecord.tenant_id == tenant_id,
                        ResourceRecord.kind == "document",
                    )
                ).scalar_one_or_none()
                if resource is not None:
                    resource.state = "archived"
                    resource.version += 1

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
