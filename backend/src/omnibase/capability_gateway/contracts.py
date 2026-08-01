"""Closed HTTP and application contracts for the read-only gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

GatewayAction = Literal[
    "data.schema.read",
    "data.rows.read",
    "rag.search",
    "rag.citation.read",
]


class GatewayModel(BaseModel):
    """Every gateway DTO rejects undeclared fields, including forged scope."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


JsonScalar = str | int | float | bool | None


class ErrorBody(GatewayModel):
    code: str
    message: str


class ErrorEnvelope(GatewayModel):
    error: ErrorBody


class CompareFilter(GatewayModel):
    kind: Literal["compare"] = "compare"
    column_id: UUID
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null"]
    value: JsonScalar | list[JsonScalar] = None

    @model_validator(mode="after")
    def validate_value(self) -> CompareFilter:
        if self.op == "in":
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 50:
                raise ValueError("in requires between 1 and 50 scalar values")
        elif self.op == "is_null":
            if not isinstance(self.value, bool):
                raise ValueError("is_null requires a boolean value")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.op} requires one scalar value")
        return self


class BooleanFilter(GatewayModel):
    kind: Literal["and", "or"]
    clauses: list[Annotated[CompareFilter | BooleanFilter, Field(discriminator="kind")]] = Field(
        min_length=1,
        max_length=10,
    )


FilterNode = Annotated[CompareFilter | BooleanFilter, Field(discriminator="kind")]


class OrderBy(GatewayModel):
    column_id: UUID
    direction: Literal["asc", "desc"] = "asc"


class ReadQuery(GatewayModel):
    columns: list[UUID] = Field(min_length=1, max_length=50)
    filter: FilterNode | None = None
    order_by: list[OrderBy] = Field(default_factory=list, max_length=5)
    cursor: str | None = Field(default=None, min_length=16, max_length=512)
    limit: StrictInt = Field(default=50, ge=1, le=100)
    timeout_ms: StrictInt = Field(default=2000, ge=1, le=5000)
    max_bytes: StrictInt = Field(default=262_144, ge=1, le=1_048_576)

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("columns must be unique")
        return value

    @model_validator(mode="after")
    def bounded_filter(self) -> ReadQuery:
        if self.filter is None:
            return self
        count = 0

        def walk(node: CompareFilter | BooleanFilter, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > 4 or count > 32:
                raise ValueError("filter exceeds the maximum depth or node count")
            if isinstance(node, BooleanFilter):
                for clause in node.clauses:
                    walk(clause, depth + 1)

        walk(self.filter, 1)
        return self


class ResourceRequest(GatewayModel):
    resource_id: UUID


class DataRowsRequest(ResourceRequest):
    query: ReadQuery


class RagSearchRequest(ResourceRequest):
    query: str = Field(min_length=1, max_length=2000)
    top_k: StrictInt = Field(default=10, ge=1, le=20)
    timeout_ms: StrictInt = Field(default=3000, ge=1, le=5000)
    max_bytes: StrictInt = Field(default=262_144, ge=1, le=1_048_576)


class CitationReadRequest(ResourceRequest):
    citation_ids: list[UUID] = Field(min_length=1, max_length=20)
    timeout_ms: StrictInt = Field(default=3000, ge=1, le=5000)
    max_bytes: StrictInt = Field(default=262_144, ge=1, le=1_048_576)

    @field_validator("citation_ids")
    @classmethod
    def unique_citations(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("citation_ids must be unique")
        return value


class ColumnRead(GatewayModel):
    id: UUID
    display_name: str
    type: str
    nullable: bool


class DataSchemaResponse(GatewayModel):
    resource_id: UUID
    resource_version: int
    columns: list[ColumnRead]


class DataRowsResponse(GatewayModel):
    resource_id: UUID
    resource_version: int
    rows: list[dict[str, JsonScalar]]
    next_cursor: str | None = None
    row_count: int
    bytes_out: int
    truncated: bool


class SearchHitRead(GatewayModel):
    citation_id: UUID
    document_id: UUID
    score: float
    snippet: str | None = None
    page_number: int | None = None


class RagSearchResponse(GatewayModel):
    resource_id: UUID
    results: list[SearchHitRead]
    total_found: int
    bytes_out: int
    truncated: bool


class CitationRead(GatewayModel):
    citation_id: UUID
    document_id: UUID
    content: str
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None


class CitationReadResponse(GatewayModel):
    resource_id: UUID
    citations: list[CitationRead]
    bytes_out: int
    truncated: bool


@dataclass(frozen=True)
class CapabilityConstraints:
    max_rows: int = 100
    max_bytes: int = 1_048_576
    max_timeout_ms: int = 5000
    max_top_k: int = 20


@dataclass(frozen=True)
class VerifiedCapability:
    """Scope derived only by the trusted verifier, never from request DTOs."""

    tenant_id: str
    workspace_id: str
    runtime_instance_id: str
    actor_user_id: str | None
    grant_id: str
    token_jti: str
    actions: frozenset[str]
    resource_ids: frozenset[str]
    constraints: CapabilityConstraints = field(default_factory=CapabilityConstraints)
    core_verification: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class TrustedWorkloadContext:
    """Identity established by an out-of-band attestor, never HTTP headers."""

    opaque_identity: str
    tenant_id: str
    workspace_id: str
    runtime_instance_id: str
    certificate_thumbprint: str


@dataclass(frozen=True)
class WorkloadCredential:
    authorization: str
    identity: str
    trusted_context: TrustedWorkloadContext


@dataclass(frozen=True)
class ResourceDescriptor:
    id: str
    tenant_id: str
    kind: str
    owner_type: str
    owner_id: str | None
    parent_id: str | None
    state: str
    version: int
    policy_class: str


@dataclass(frozen=True)
class DataRowsResult:
    rows: list[dict[str, JsonScalar]]
    next_cursor: str | None
    bytes_out: int
    truncated: bool


@dataclass(frozen=True)
class RagSearchResult:
    hits: list[SearchHitRead]
    bytes_out: int
    truncated: bool


@dataclass(frozen=True)
class CitationResult:
    citations: list[CitationRead]
    bytes_out: int
    truncated: bool


BooleanFilter.model_rebuild()


__all__ = [
    "BooleanFilter",
    "CapabilityConstraints",
    "CitationRead",
    "CitationReadRequest",
    "CitationReadResponse",
    "CitationResult",
    "ColumnRead",
    "CompareFilter",
    "DataRowsRequest",
    "DataRowsResponse",
    "DataRowsResult",
    "DataSchemaResponse",
    "ErrorBody",
    "ErrorEnvelope",
    "FilterNode",
    "GatewayAction",
    "OrderBy",
    "RagSearchRequest",
    "RagSearchResponse",
    "RagSearchResult",
    "ReadQuery",
    "ResourceDescriptor",
    "ResourceRequest",
    "SearchHitRead",
    "TrustedWorkloadContext",
    "VerifiedCapability",
    "WorkloadCredential",
]
