"""Focused Phase 1.6 dual-lane retrieval, store, and backfill contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from omnibase.rag.index_metadata import DimensionMismatchError, IndexVersion, get_index_lane
from omnibase.rag.retriever import hybrid_search_detailed
from omnibase.rag.store import (
    ChunkToInsert,
    SearchMode,
    SearchResult,
    SearchStageResult,
    search_vector_lane,
    upsert_chunks_v2,
)


def test_v2_upsert_uses_closed_table_and_document_chunk_conflict() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    factory = MagicMock(return_value=session)
    chunk = ChunkToInsert(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        chunk_index=3,
        content="stored text",
        embedding=[0.1] * 1024,
        char_start=1,
        char_end=12,
    )
    with patch("omnibase.rag.store.get_session_factory", return_value=factory):
        assert upsert_chunks_v2("tenant_test", [chunk]) == 1
    sql = str(session.execute.call_args.args[0])
    params = session.execute.call_args.args[1]
    assert "INSERT INTO embeddings_v2" in sql
    assert "ON CONFLICT (document_id, chunk_index) DO UPDATE" in sql
    assert params["c0_id"] == chunk.chunk_id


def test_strict_lane_rejects_wrong_vector_dimension_before_database() -> None:
    with pytest.raises(DimensionMismatchError):
        search_vector_lane(
            "tenant_test",
            [0.1] * 512,
            lane=get_index_lane(IndexVersion.V2),
        )


def test_online_search_distinguishes_stage_failure_from_zero_hits() -> None:
    hit = SearchResult("c1", "d1", "text", 0.8, 0, 0, 4, "paragraph", {})
    with (
        patch("omnibase.rag.retriever.get_settings") as settings,
        patch("omnibase.rag.retriever.embed_query_for_version", side_effect=RuntimeError("offline")),
        patch(
            "omnibase.rag.retriever.search_bm25_lane",
            return_value=SearchStageResult([hit]),
        ),
    ):
        settings.return_value.embedding_index_version = IndexVersion.V1
        response = hybrid_search_detailed("tenant_test", "query", mode=SearchMode.ONLINE)
    assert len(response.results) == 1
    assert response.trace.vector_failed is True
    assert response.trace.bm25_failed is False
    assert response.trace.bm25_results_count == 1
    assert response.trace.failed_stages == ("embedding",)


def test_same_lane_is_passed_to_vector_and_bm25() -> None:
    lane = get_index_lane(IndexVersion.V2)
    with (
        patch("omnibase.rag.retriever.embed_query_for_version", return_value=[0.1] * 1024),
        patch(
            "omnibase.rag.retriever.search_vector_lane",
            return_value=SearchStageResult([]),
        ) as vector,
        patch(
            "omnibase.rag.retriever.search_bm25_lane",
            return_value=SearchStageResult([]),
        ) as bm25,
    ):
        response = hybrid_search_detailed("tenant_test", "query", lane=lane)
    assert vector.call_args.kwargs["lane"] is lane
    assert bm25.call_args.kwargs["lane"] is lane
    assert response.trace.vector_failed is False
    assert response.trace.bm25_failed is False
    assert response.trace.fused_count == 0


def test_backfill_reads_v1_and_preserves_chunk_identifier() -> None:
    from omnibase.workers.backfill import backfill_document

    source = ChunkToInsert(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        chunk_index=0,
        content="authoritative v1",
        embedding=None,
        char_start=0,
        char_end=16,
        metadata={"page": 1},
    )
    with (
        patch("omnibase.workers.backfill._mark_building", return_value=2),
        patch("omnibase.workers.backfill.read_document_chunks", return_value=[source]),
        patch("omnibase.workers.backfill.embed_documents_for_version", return_value=[[0.2] * 1024]),
        patch("omnibase.workers.backfill.upsert_chunks_v2", return_value=1) as upsert,
        patch("omnibase.workers.backfill._mark_ready") as ready,
    ):
        result = backfill_document("tenant_test", source.document_id)
    target = upsert.call_args.args[1][0]
    assert target.chunk_id == source.chunk_id
    assert target.document_id == source.document_id
    assert target.chunk_index == source.chunk_index
    assert result.status == "ready"
    ready.assert_called_once_with("tenant_test", source.document_id, 1)


def test_backfill_resume_is_idempotent_via_upsert_on_conflict() -> None:
    """Running backfill twice must not create duplicate V2 rows.

    The idempotency is enforced at the database level by the
    ``ON CONFLICT (document_id, chunk_index) DO UPDATE`` clause in
    ``upsert_chunks_v2``.  This test verifies that the same chunk_id
    and document_id are passed through on a second run, confirming the
    upsert will update (not insert) the same row.
    """
    from omnibase.workers.backfill import backfill_document

    source = ChunkToInsert(
        chunk_id="11111111-1111-1111-1111-111111111111",
        document_id="22222222-2222-2222-2222-222222222222",
        chunk_index=5,
        content="stable v1 content",
        embedding=None,
        char_start=100,
        char_end=117,
    )
    for run_number in (1, 2):
        with (
            patch("omnibase.workers.backfill._mark_building", return_value=run_number),
            patch("omnibase.workers.backfill.read_document_chunks", return_value=[source]),
            patch(
                "omnibase.workers.backfill.embed_documents_for_version",
                return_value=[[0.3] * 1024],
            ),
            patch("omnibase.workers.backfill.upsert_chunks_v2", return_value=1) as upsert,
            patch("omnibase.workers.backfill._mark_ready"),
        ):
            result = backfill_document("tenant_test", source.document_id)

        assert result.status == "ready"
        assert result.chunks_upserted == 1
        target = upsert.call_args.args[1][0]
        assert target.chunk_id == source.chunk_id, f"run {run_number}: chunk_id drift"
        assert target.document_id == source.document_id


def test_v2_query_embedding_applies_instruction_prefix() -> None:
    """BGE-M3 requires a query instruction prefix for asymmetric retrieval."""
    from omnibase.rag.embedding import _V2_QUERY_INSTRUCTION, embed_query_for_version

    mock_model = MagicMock()
    mock_model.encode.return_value = [0.1] * 1024
    mock_model.get_sentence_embedding_dimension.return_value = 1024

    with patch("omnibase.rag.embedding._models", {IndexVersion.V1: None, IndexVersion.V2: mock_model}):
        embed_query_for_version("test query", IndexVersion.V2)

    encoded_text = mock_model.encode.call_args.args[0]
    assert encoded_text.startswith(_V2_QUERY_INSTRUCTION)
    assert "test query" in encoded_text


def test_v1_query_embedding_does_not_apply_instruction_prefix() -> None:
    """V1 (bge-small-zh) should encode raw text without any prefix."""
    from omnibase.rag.embedding import _V2_QUERY_INSTRUCTION, embed_query_for_version

    mock_model = MagicMock()
    mock_model.encode.return_value = [0.1] * 512
    mock_model.get_sentence_embedding_dimension.return_value = 512

    with patch("omnibase.rag.embedding._models", {IndexVersion.V1: mock_model, IndexVersion.V2: None}):
        embed_query_for_version("test query", IndexVersion.V1)

    encoded_text = mock_model.encode.call_args.args[0]
    assert not encoded_text.startswith(_V2_QUERY_INSTRUCTION)
    assert encoded_text == "test query"
