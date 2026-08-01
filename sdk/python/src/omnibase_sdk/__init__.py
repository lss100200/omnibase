"""Public OmniBase capability-gateway SDK surface."""

from omnibase_sdk.client import OmniBaseClient
from omnibase_sdk.models import (
    Citation,
    CitationReadResponse,
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
    "Citation",
    "CitationReadResponse",
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
