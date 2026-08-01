"""RAG schemas: request/response models for the RAG API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """POST /api/rag/search body."""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    document_id: str | None = Field(default=None, description="Restrict to one document")


class ChunkResult(BaseModel):
    """A single search result chunk."""

    chunk_id: str
    document_id: str
    content: str
    score: float = Field(..., description="Final score after reranking")
    rrf_score: float | None = None
    chunk_index: int
    page_number: int = 1
    char_start: int | None = None
    char_end: int | None = None
    chunk_type: str = "paragraph"


class SearchResponse(BaseModel):
    """POST /api/rag/search response."""

    query: str
    results: list[ChunkResult]
    total_found: int
    latency_ms: float


class PlaygroundRequest(BaseModel):
    """POST /api/rag/playground body (returns full retrieval debug info)."""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    vector_top_k: int = Field(default=100, ge=10, le=500)
    enable_rerank: bool = True


class RetrievalDebug(BaseModel):
    """Safe debug info showing each retrieval stage without body/error leakage."""

    query_embedded: bool
    vector_results_count: int
    bm25_results_count: int
    fused_count: int
    reranked_count: int
    reranker_available: bool
    index_lane: str = "v1"
    search_mode: str = "online"
    vector_failed: bool = False
    bm25_failed: bool = False
    failed_stages: list[str] = Field(default_factory=list)


class PlaygroundResponse(BaseModel):
    """POST /api/rag/playground response with full retrieval trace."""

    query: str
    results: list[ChunkResult]
    debug: RetrievalDebug
    latency_ms: float


class AskRequest(BaseModel):
    """POST /api/rag/ask body (LLM Q&A with citations)."""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    stream: bool = Field(default=True, description="Use SSE streaming")


class Citation(BaseModel):
    """A citation linking back to source text."""

    index: int
    chunk_id: str
    document_id: str
    snippet: str = Field(..., description="First 200 chars of the chunk")
    page_number: int = 1
    score: float


class AskResponse(BaseModel):
    """POST /api/rag/ask response (non-streaming)."""

    answer: str
    citations: list[Citation]
    latency_ms: float
