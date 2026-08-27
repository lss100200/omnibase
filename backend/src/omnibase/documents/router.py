"""Documents router - upload / list / get / download / delete.

All endpoints are tenant-scoped via the get_current_tenant + get_tenant_db
dependency chain (omnibase.tenants.dependencies). The schema is resolved
from the JWT; no tenant_id query param is accepted.

Endpoints (all under /api/v1/documents):
- POST   /           : multipart upload (auth required)
- GET    /           : paginated list (auth required)
- GET    /{id}       : single document metadata (auth required)
- GET    /{id}/download : presigned download URL (auth required)
- DELETE /{id}       : delete document + MinIO object (auth required)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger
from omnibase.core.rate_limit import enforce_upload_rate_limit
from omnibase.documents.schemas import (
    DocumentDeleteResponse,
    DocumentDownloadURL,
    DocumentList,
    DocumentRead,
    DocumentUploadResponse,
)
from omnibase.documents.service import (
    DocumentDeleteConflict,
    DocumentError,
    DocumentNotFound,
    InvalidFile,
    StorageError,
    delete_document,
    get_document,
    get_download_url,
    list_documents,
    upload_document,
)
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

router = APIRouter(prefix="/documents", tags=["documents"])
log = get_logger(__name__)


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


# -----------------------------------------------------------
# POST /api/v1/documents
# -----------------------------------------------------------
@router.post(
    "",
    dependencies=[Depends(enforce_upload_rate_limit)],
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document (multipart/form-data)",
    description=(
        "Accepts a single file upload. The file is persisted to object storage "
        "and the ingestion task is dispatched to the background worker. The "
        "returned document has status='queued' indicating ingestion will "
        "proceed asynchronously. Poll GET /documents/{id} for progress."
    ),
)
async def upload_endpoint(
    file: UploadFile = File(..., description="File to upload"),
    workspace_id: UUID | None = Form(
        default=None,
        description="Optional Workspace that may retrieve this document after indexing",
    ),
    ctx: TenantContext = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
) -> DocumentUploadResponse:
    """Upload a file."""
    # Read the file into memory (Phase 0: small files only, <=50MB by default).
    # Phase 1 will switch to chunked streaming via SpooledTemporaryFile.
    try:
        data = await file.read()
    except Exception as exc:
        log.error("upload.read_failed", error=str(exc))
        raise _error("read_failed", "Failed to read uploaded file", 400) from exc

    try:
        result = upload_document(
            schema_name=ctx.schema_name,
            filename=file.filename or "unnamed",
            content_type=file.content_type,
            data=data,
            settings=settings,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=str(workspace_id) if workspace_id is not None else None,
        )
    except InvalidFile as exc:
        raise _error(exc.code, exc.message, 422) from exc
    except StorageError as exc:
        raise _error("storage_failed", str(exc), 503) from exc
    except DocumentError as exc:
        log.error("upload.failed", error=str(exc), exc_info=True)
        raise _error("upload_failed", "Upload failed", 500) from exc

    return DocumentUploadResponse(
        document=DocumentRead.model_validate(result.document),
        message=(
            "File uploaded and queued for ingestion."
            if result.document.status == "queued"
            else "File uploaded; ingestion enqueue failed."
        ),
    )


# -----------------------------------------------------------
# GET /api/v1/documents
# -----------------------------------------------------------
@router.get(
    "",
    response_model=DocumentList,
    summary="List documents (paginated)",
)
def list_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(
        default=None, pattern="^(pending|queued|processing|indexed|failed)$"
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> DocumentList:
    """List documents for the current tenant."""
    documents, total = list_documents(
        schema_name=ctx.schema_name,
        limit=limit,
        offset=offset,
        status_filter=status,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
    )
    return DocumentList(
        items=[DocumentRead.model_validate(d) for d in documents],
        total=total,
    )


# -----------------------------------------------------------
# GET /api/v1/documents/{id}
# -----------------------------------------------------------
@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get a single document by id",
)
def get_endpoint(
    document_id: str,
    ctx: TenantContext = Depends(get_current_tenant),
) -> DocumentRead:
    """Fetch document metadata."""
    try:
        document = get_document(
            schema_name=ctx.schema_name,
            document_id=document_id,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
        )
    except DocumentNotFound as exc:
        raise _error("not_found", str(exc), 404) from exc
    return DocumentRead.model_validate(document)


# -----------------------------------------------------------
# GET /api/v1/documents/{id}/download
# -----------------------------------------------------------
@router.get(
    "/{document_id}/download",
    response_model=DocumentDownloadURL,
    summary="Get a presigned download URL",
    description=(
        "Returns a short-lived (1 hour) presigned URL that the client can "
        "use to download the file directly from MinIO. This avoids proxying "
        "file bytes through the API server."
    ),
)
def download_endpoint(
    document_id: str,
    ctx: TenantContext = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
) -> DocumentDownloadURL:
    """Generate presigned download URL."""
    try:
        url, filename = get_download_url(
            schema_name=ctx.schema_name,
            document_id=document_id,
            settings=settings,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
        )
    except DocumentNotFound as exc:
        raise _error("not_found", str(exc), 404) from exc
    except StorageError as exc:
        raise _error("storage_failed", str(exc), 503) from exc

    return DocumentDownloadURL(
        url=url,
        expires_in_seconds=3600,
        filename=filename,
    )


# -----------------------------------------------------------
# DELETE /api/v1/documents/{id}
# -----------------------------------------------------------
@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a document and its underlying file",
)
def delete_endpoint(
    document_id: str,
    ctx: TenantContext = Depends(get_current_tenant),
) -> DocumentDeleteResponse:
    """Delete document + MinIO object."""
    try:
        delete_document(
            schema_name=ctx.schema_name,
            document_id=document_id,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
        )
    except DocumentNotFound as exc:
        raise _error("not_found", str(exc), 404) from exc
    except DocumentDeleteConflict as exc:
        raise _error(
            "document_delete_conflict",
            "Document cannot be deleted while ingestion is pending or active",
            409,
        ) from exc
    except DocumentError as exc:
        log.error("delete.failed", document_id=document_id, error=str(exc))
        raise _error("delete_failed", "Delete failed", 500) from exc

    return DocumentDeleteResponse(id=document_id, deleted=True)


__all__ = ["router"]
