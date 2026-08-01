"""Unit tests for documents.service validation + helpers (no DB/MinIO).

Pure unit tests for:
- validate_upload (size/type/filename validation)
- make_minio_key (key format + schema validation)
- _guess_mime_type (extension-based MIME inference)

Integration tests (real MinIO + DB) live in tests/integration/.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from omnibase.documents.service import (
    DocumentDeleteConflict,
    InvalidFile,
    _guess_mime_type,
    delete_document,
    make_minio_key,
    validate_upload,
)


class TestValidateUpload:
    """validate_upload - file size / type / filename validation."""

    def test_valid_pdf_under_limit_passes(self) -> None:
        """A normal small PDF passes validation."""
        validate_upload(
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
        )

    def test_empty_file_rejected(self) -> None:
        """Zero-byte files are rejected."""
        with pytest.raises(InvalidFile, match="empty"):
            validate_upload(
                filename="empty.pdf",
                content_type="application/pdf",
                size_bytes=0,
            )

    def test_file_too_large_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Files exceeding max_upload_size_mb are rejected."""
        from omnibase.core.config import get_settings

        settings = get_settings()
        # Build a size just over the limit
        too_big = settings.max_upload_size_bytes + 1
        with pytest.raises(InvalidFile, match="exceeds"):
            validate_upload(
                filename="huge.pdf",
                content_type="application/pdf",
                size_bytes=too_big,
                settings=settings,
            )

    def test_unsupported_mime_rejected(self) -> None:
        """Non-allowlisted MIME types are rejected."""
        with pytest.raises(InvalidFile, match="unsupported"):
            validate_upload(
                filename="movie.mp4",
                content_type="video/mp4",
                size_bytes=1024,
            )

    def test_empty_filename_rejected(self) -> None:
        """Empty filenames are rejected."""
        with pytest.raises(InvalidFile, match="Filename"):
            validate_upload(
                filename="",
                content_type="application/pdf",
                size_bytes=1024,
            )

    def test_oversized_filename_rejected(self) -> None:
        """Filenames longer than 255 chars are rejected."""
        with pytest.raises(InvalidFile, match="Filename"):
            validate_upload(
                filename="a" * 300,
                content_type="application/pdf",
                size_bytes=1024,
            )

    def test_none_content_type_skips_mime_check(self) -> None:
        """A None content_type does not trigger unsupported_type."""
        # Should NOT raise on MIME; size/filename still validated
        validate_upload(
            filename="mystery.pdf",
            content_type=None,
            size_bytes=1024,
        )


class TestMakeMinioKey:
    """make_minio_key - tenant-scoped object key format."""

    def test_basic_format(self) -> None:
        """Key format: <schema>/<doc_id>/<filename>."""
        key = make_minio_key("tenant_abc12345", "doc-uuid-1", "report.pdf")
        assert key == "tenant_abc12345/doc-uuid-1/report.pdf"

    def test_strips_windows_path_separators(self) -> None:
        """Windows-style backslash paths collapse to basename."""
        key = make_minio_key("tenant_abc12345", "doc-1", "C:\\Users\\me\\report.pdf")
        assert key == "tenant_abc12345/doc-1/report.pdf"

    def test_strips_unix_path_separators(self) -> None:
        """POSIX-style paths collapse to basename."""
        key = make_minio_key("tenant_abc12345", "doc-1", "/home/me/report.pdf")
        assert key == "tenant_abc12345/doc-1/report.pdf"

    def test_empty_filename_falls_back_to_unnamed(self) -> None:
        """Empty filename after stripping becomes 'unnamed'."""
        key = make_minio_key("tenant_abc12345", "doc-1", "")
        assert key == "tenant_abc12345/doc-1/unnamed"

    def test_invalid_schema_rejected(self) -> None:
        """Non-tenant schema names are rejected."""
        from omnibase.tenants.schema_manager import SchemaError

        with pytest.raises(SchemaError):
            make_minio_key("public", "doc-1", "x.pdf")
        with pytest.raises(SchemaError):
            make_minio_key("tenant_short", "doc-1", "x.pdf")
        with pytest.raises(SchemaError):
            make_minio_key("evil'; DROP SCHEMA;--", "doc-1", "x.pdf")


class TestGuessMimeType:
    """_guess_mime_type - extension-based MIME inference."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("doc.pdf", "application/pdf"),
            ("doc.PDF", "application/pdf"),  # case-insensitive
            (
                "doc.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ("doc.txt", "text/plain"),
            ("doc.md", "text/markdown"),
            ("README.MD", "text/markdown"),
        ],
    )
    def test_known_extensions(self, filename: str, expected: str) -> None:
        """Known extensions map to expected MIME types."""
        assert _guess_mime_type(filename) == expected

    @pytest.mark.parametrize(
        "filename",
        [
            "noext",  # no extension
            "movie.mp4",  # unsupported extension
            "archive.zip",  # unsupported
            "data.json",  # unsupported (we only allow docs)
            "doc.pdf.exe",  # last extension wins
        ],
    )
    def test_unknown_extensions_return_none(self, filename: str) -> None:
        """Unknown / unsupported extensions return None."""
        if filename == "doc.pdf.exe":
            # .exe is unknown - returns None
            assert _guess_mime_type(filename) is None
        else:
            assert _guess_mime_type(filename) is None


class TestDeleteDocument:
    """delete_document enforces lifecycle-safe deletion."""

    @pytest.mark.parametrize("document_status", ["pending", "queued", "processing"])
    def test_active_document_delete_conflicts(
        self, monkeypatch: pytest.MonkeyPatch, document_status: str
    ) -> None:
        document = MagicMock()
        document.id = "doc-active"
        document.status = document_status
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = document
        factory = MagicMock(return_value=session)
        minio = MagicMock()
        monkeypatch.setattr(
            "omnibase.documents.service.get_session_factory", lambda _settings: factory
        )
        monkeypatch.setattr(
            "omnibase.documents.service.get_minio_client", lambda _settings: minio
        )

        with pytest.raises(DocumentDeleteConflict) as exc_info:
            delete_document(
                schema_name="tenant_a1b2c3d4e5",
                document_id="doc-active",
            )

        assert exc_info.value.document_status == document_status
        session.delete.assert_not_called()
        session.commit.assert_not_called()
        minio.remove_object.assert_not_called()

    @pytest.mark.parametrize("document_status", ["indexed", "failed"])
    def test_terminal_document_can_be_deleted(
        self, monkeypatch: pytest.MonkeyPatch, document_status: str
    ) -> None:
        document = MagicMock()
        document.id = "doc-terminal"
        document.status = document_status
        document.minio_key = "tenant_a1b2c3d4e5/doc-terminal/report.pdf"
        document.filename = "report.pdf"
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = document
        factory = MagicMock(return_value=session)
        minio = MagicMock()
        monkeypatch.setattr(
            "omnibase.documents.service.get_session_factory", lambda _settings: factory
        )
        monkeypatch.setattr(
            "omnibase.documents.service.get_minio_client", lambda _settings: minio
        )

        delete_document(
            schema_name="tenant_a1b2c3d4e5",
            document_id="doc-terminal",
        )

        session.delete.assert_called_once_with(document)
        session.commit.assert_called_once_with()
        minio.remove_object.assert_called_once()
        stmt = session.execute.call_args.args[0]
        assert stmt._for_update_arg is not None
