"""Public OmniBase capability-gateway SDK surface."""

from omnibase_sdk.client import OmniBaseClient
from omnibase_sdk.models import (
    ArtifactReadResponse,
    ArtifactWriteResponse,
    Citation,
    CitationReadResponse,
    DerivedChunkWrite,
    DerivedCreateResponse,
    DerivedDeleteResponse,
    GatewayError,
    OrderBy,
    RagSearchResponse,
    RagSearchResult,
    RowsQuery,
    RowsReadResponse,
    SchemaColumn,
    SchemaReadResponse,
)
from omnibase_sdk.transport import (
    HttpTransport,
    StaticCredentialProvider,
    Transport,
    TransportResponse,
    WorkloadCredential,
    WorkloadCredentialProvider,
)

__all__ = [
    "ArtifactReadResponse",
    "ArtifactWriteResponse",
    "Citation",
    "CitationReadResponse",
    "DerivedChunkWrite",
    "DerivedCreateResponse",
    "DerivedDeleteResponse",
    "GatewayError",
    "HttpTransport",
    "OmniBaseClient",
    "OrderBy",
    "RagSearchResponse",
    "RagSearchResult",
    "RowsQuery",
    "RowsReadResponse",
    "SchemaColumn",
    "SchemaReadResponse",
    "StaticCredentialProvider",
    "Transport",
    "TransportResponse",
    "WorkloadCredential",
    "WorkloadCredentialProvider",
]
