"""Tests for Todo 7: idempotent re-ingest contract.

Verifies that duplicate delivery does not produce duplicate chunks —
the ingest pipeline must delete old chunks before inserting new ones.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _make_parsed_document() -> object:
    """Build a ParsedDocument with one page for ingest pipeline mocking."""
    from omnibase.rag.parser import ParsedDocument, ParsedPage

    return ParsedDocument(
        filename="test.pdf",
        mime_type="application/pdf",
        pages=[
            ParsedPage(
                page_number=1,
                text="test content for chunking",
                char_offset=0,
            )
        ],
    )


# ────────────────────────────────────────────────────────────
# Idempotent re-ingest — delete before insert
# ────────────────────────────────────────────────────────────


class TestIdempotentReingest:
    """Re-ingesting a document deletes old chunks before inserting new."""

    def test_delete_chunks_called_before_insert(self) -> None:
        """ingest_document calls delete_document_chunks before insert_chunks."""
        from omnibase.rag.ingest import ingest_document

        call_order: list[str] = []

        with (
            patch(
                "omnibase.rag.ingest.parse_document",
                return_value=_make_parsed_document(),
            ),
            patch("omnibase.rag.ingest.chunk_document") as mock_chunk,
            patch("omnibase.rag.ingest.embed_batch") as mock_embed,
            patch(
                "omnibase.rag.ingest.delete_document_chunks"
            ) as mock_delete,
            patch("omnibase.rag.ingest.insert_chunks") as mock_insert,
        ):
            from omnibase.rag.chunker import TextChunk

            single_chunk = TextChunk(
                chunk_index=0,
                content="test content",
                char_start=0,
                char_end=12,
                chunk_type="paragraph",
                metadata={},
            )
            mock_chunk.return_value = [single_chunk]
            mock_embed.return_value = [[0.1] * 512]
            mock_insert.return_value = 1

            mock_delete.side_effect = lambda *a, **kw: call_order.append(
                "delete"
            )
            mock_insert.side_effect = lambda *a, **kw: call_order.append(
                "insert"
            ) or 1

            result = ingest_document(
                schema_name="tenant_test",
                document_id="doc-ide",
                file_data=b"test file",
                filename="test.pdf",
                mime_type="application/pdf",
            )

        assert call_order == ["delete", "insert"], (
            f"Expected delete before insert, got {call_order}"
        )
        assert result.chunks_created == 1

    def test_delete_targets_correct_document(self) -> None:
        """delete_document_chunks receives the same document_id being ingested."""
        from omnibase.rag.ingest import ingest_document

        with (
            patch(
                "omnibase.rag.ingest.parse_document",
                return_value=_make_parsed_document(),
            ),
            patch("omnibase.rag.ingest.chunk_document") as mock_chunk,
            patch("omnibase.rag.ingest.embed_batch", return_value=[[0.1] * 512]),
            patch(
                "omnibase.rag.ingest.delete_document_chunks"
            ) as mock_delete,
            patch(
                "omnibase.rag.ingest.insert_chunks", return_value=1
            ),
        ):
            from omnibase.rag.chunker import TextChunk

            mock_chunk.return_value = [
                TextChunk(
                    chunk_index=0,
                    content="c",
                    char_start=0,
                    char_end=1,
                    chunk_type="paragraph",
                    metadata={},
                )
            ]

            ingest_document(
                schema_name="tenant_iso",
                document_id="doc-target-123",
                file_data=b"x",
                filename="x.pdf",
                mime_type="application/pdf",
            )

        mock_delete.assert_called_once_with("tenant_iso", "doc-target-123")

    def test_delete_failure_stops_insert(self) -> None:
        """A failed cleanup aborts re-ingest instead of creating duplicates."""
        from omnibase.rag.ingest import ingest_document

        with (
            patch(
                "omnibase.rag.ingest.parse_document",
                return_value=_make_parsed_document(),
            ),
            patch("omnibase.rag.ingest.chunk_document") as mock_chunk,
            patch("omnibase.rag.ingest.embed_batch", return_value=[[0.1] * 512]),
            patch(
                "omnibase.rag.ingest.delete_document_chunks",
                side_effect=RuntimeError("cleanup failed"),
            ),
            patch("omnibase.rag.ingest.insert_chunks") as mock_insert,
        ):
            from omnibase.rag.chunker import TextChunk

            mock_chunk.return_value = [
                TextChunk(
                    chunk_index=0,
                    content="c",
                    char_start=0,
                    char_end=1,
                    chunk_type="paragraph",
                    metadata={},
                )
            ]

            with pytest.raises(RuntimeError, match="cleanup failed"):
                ingest_document(
                    schema_name="tenant_iso",
                    document_id="doc-delete-failure",
                    file_data=b"x",
                    filename="x.pdf",
                    mime_type="application/pdf",
                )

        mock_insert.assert_not_called()

    def test_double_ingest_does_not_double_chunks(self) -> None:
        """Two sequential ingest calls each delete-before-insert, producing
        chunk_count = N (one set), not 2N."""
        from omnibase.rag.ingest import ingest_document

        delete_calls: list[tuple] = []
        insert_calls: list[tuple] = []

        with (
            patch(
                "omnibase.rag.ingest.parse_document",
                return_value=_make_parsed_document(),
            ),
            patch("omnibase.rag.ingest.chunk_document") as mock_chunk,
            patch("omnibase.rag.ingest.embed_batch") as mock_embed,
            patch(
                "omnibase.rag.ingest.delete_document_chunks"
            ) as mock_delete,
            patch(
                "omnibase.rag.ingest.insert_chunks",
                side_effect=lambda *a, **kw: insert_calls.append((a, kw)) or 3,
            ),
        ):
            from omnibase.rag.chunker import TextChunk

            chunks = [
                TextChunk(
                    chunk_index=i,
                    content=f"chunk {i}",
                    char_start=i * 10,
                    char_end=(i + 1) * 10,
                    chunk_type="paragraph",
                    metadata={},
                )
                for i in range(3)
            ]
            vectors = [[0.1] * 512 for _ in range(3)]
            mock_chunk.return_value = chunks
            mock_embed.return_value = vectors
            mock_delete.side_effect = lambda s, d: delete_calls.append((s, d))

            r1 = ingest_document(
                schema_name="tenant_d",
                document_id="doc-dup",
                file_data=b"x",
                filename="x.pdf",
                mime_type="application/pdf",
            )
            r2 = ingest_document(
                schema_name="tenant_d",
                document_id="doc-dup",
                file_data=b"x",
                filename="x.pdf",
                mime_type="application/pdf",
            )

        assert len(delete_calls) == 2, "Should delete before each ingest"
        assert len(insert_calls) == 2, "Should insert on each ingest"
        assert all(dc == ("tenant_d", "doc-dup") for dc in delete_calls)
        assert r1.chunks_created == 3
        assert r2.chunks_created == 3
