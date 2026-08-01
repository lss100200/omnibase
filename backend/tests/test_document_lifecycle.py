"""Tests for asynchronous document lifecycle contract (Todo 3).

Validates the state machine: pending -> queued -> processing -> indexed|failed.
Tests the error_detail field, status constraint, and schema serialization.
"""

from __future__ import annotations

import pytest

from omnibase.documents.schemas import DocumentRead, DocumentStatus
from omnibase.db.tenant import Document


class TestDocumentLifecycleContract:
    """Document lifecycle state machine contract."""

    def test_valid_states_include_queued_and_processing(self) -> None:
        """The DocumentStatus type includes all expected state values."""
        # Given/When: we enumerate expected valid states
        valid_states: set[str] = {"pending", "queued", "processing", "indexed", "failed"}

        # Then: DocumentStatus should cover all of them (it's a Literal)
        # We verify by checking that each state is a valid DocumentStatus value
        import typing

        status_values = typing.get_args(DocumentStatus)
        for state in valid_states:
            assert state in status_values, f"{state!r} must be a valid DocumentStatus"

    def test_document_orm_has_error_detail(self) -> None:
        """Document ORM model includes error_detail column."""
        # Given/When: Document has an 'error_detail' attribute
        assert hasattr(Document, "error_detail"), "Document must have error_detail column"

        # Then: it is a string-typed column (nullable)
        col = Document.__table__.columns.get("error_detail")
        assert col is not None, "error_detail must exist as a table column"
        assert col.nullable, "error_detail must be nullable"

    def test_document_schema_serializes_new_states(self) -> None:
        """DocumentRead schema can serialize queued/processing/failed with error_detail."""
        from datetime import datetime

        # Given: a Document-like dict with queued status and error_detail
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "queued",
            "error_detail": None,
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When: constructing DocumentRead
        doc = DocumentRead.model_validate(doc_data)

        # Then: status is queued, error_detail is None
        assert doc.status == "queued"
        assert doc.error_detail is None

    def test_document_schema_serializes_failed_with_error_detail(self) -> None:
        """DocumentRead schema serializes failed status with error_detail."""
        from datetime import datetime

        # Given: a document with failed status and error_detail
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "failed",
            "error_detail": "Parse error: unsupported format",
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When: constructing DocumentRead
        doc = DocumentRead.model_validate(doc_data)

        # Then: status is failed, error_detail is captured
        assert doc.status == "failed"
        assert doc.error_detail == "Parse error: unsupported format"

    def test_document_schema_serializes_processing(self) -> None:
        """DocumentRead schema serializes processing status."""
        from datetime import datetime

        # Given: a document with processing status
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "processing",
            "error_detail": None,
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When: constructing DocumentRead
        doc = DocumentRead.model_validate(doc_data)

        # Then: status is processing
        assert doc.status == "processing"

    def test_document_schema_rejects_invalid_status(self) -> None:
        """DocumentRead schema rejects invalid status values."""
        from datetime import datetime

        # Given: a document with an invalid status
        doc_data = {
            "id": "test-id",
            "filename": "test.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "status": "nonexistent",  # Invalid state
            "error_detail": None,
            "page_count": None,
            "meta": {},
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # When/Then: construction raises validation error
        with pytest.raises(ValueError, match="status"):
            DocumentRead.model_validate(doc_data)

    def test_document_orm_error_detail_bounded_length(self) -> None:
        """error_detail column has a bounded max length (VARCHAR, not TEXT)."""
        col = Document.__table__.columns.get("error_detail")
        assert col is not None
        # Should be VARCHAR with defined length (not TEXT/unbounded)
        type_str = str(col.type)
        assert "VARCHAR" in type_str.upper() or "CHARACTER VARYING" in type_str.upper(), (
            f"error_detail should be VARCHAR, got {type_str}"
        )


class TestDocumentStatusConstraint:
    """Status CHECK constraint on the documents table."""

    def test_status_check_constraint_name(self) -> None:
        """The CHECK constraint is named 'documents_status_check'."""
        # Search for the constraint on the table
        constraints = list(Document.__table__.constraints)
        constraint_names = {c.name for c in constraints}
        assert "documents_status_check" in constraint_names, (
            "Constraint 'documents_status_check' must exist on documents table"
        )

    def test_status_check_includes_all_new_states(self) -> None:
        """The CHECK constraint allows all lifecycle states."""
        import re

        constraints = list(Document.__table__.constraints)
        for c in constraints:
            if c.name == "documents_status_check":
                sql_text = str(c.sqltext)
                # Extract the allowed values from the IN clause
                allowed = set(re.findall(r"'(\w+)'", sql_text))
                for expected in ("pending", "queued", "processing", "indexed", "failed"):
                    assert expected in allowed, (
                        f"State {expected!r} must be in documents_status_check"
                    )
                break
