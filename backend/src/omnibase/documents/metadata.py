"""PDF metadata extraction (Phase 0).

Scope:
- Extract page count and document-level metadata (title, author) only
- Do NOT extract text content, do NOT chunk (that arrives in Phase 1)
- Designed to be fast and tolerant: a malformed PDF should still produce
  a partial result (e.g. page count) rather than fail the whole upload

Returns a plain dict so the service layer can store it directly in the
documents.metadata JSONB column.
"""

from __future__ import annotations

from typing import Any

from omnibase.core.logging import get_logger

log = get_logger(__name__)


# -----------------------------------------------------------
# Public API
# -----------------------------------------------------------
def extract_pdf_metadata(
    data: bytes,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Extract metadata from a PDF byte string.

    Args:
        data: Raw PDF bytes (must start with %PDF).
        content_type: Optional MIME type (used to skip non-PDFs quickly).

    Returns:
        Dict with keys like page_count, title, author, producer, created.
        Returns {} if the data is not a PDF or extraction fails entirely.
        Returns partial dict if some fields are extractable but not all.
    """
    if not _looks_like_pdf(data, content_type):
        return {}

    result: dict[str, Any] = {}

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        log.warning("metadata.pypdf_unavailable", error=str(exc))
        return {}

    try:
        # pypdf accepts a path, file-like, or bytes; we use BytesIO for safety
        import io

        reader = PdfReader(io.BytesIO(data), strict=False)

        # 1. Page count (most reliable)
        try:
            page_count = len(reader.pages)
            result["page_count"] = page_count
        except Exception as exc:
            log.debug("metadata.page_count_failed", error=str(exc))

        # 2. Document info dict (title, author, producer, etc.)
        try:
            meta = reader.metadata
            if meta is not None:
                # pypdf returns IndirectObject-wrapped strings; coerce to plain
                _extract_doc_info(meta, result)
        except Exception as exc:
            log.debug("metadata.doc_info_failed", error=str(exc))

        # 3. PDF version (helpful for debugging downstream tools)
        try:
            pdf_header = reader.stream._data[:20] if hasattr(reader, "stream") else b""  # type: ignore[union-attr]
            version = _parse_pdf_version(pdf_header)
            if version:
                result["pdf_version"] = version
        except Exception:
            # Best-effort: skip silently
            pass

        log.debug(
            "metadata.extracted",
            page_count=result.get("page_count"),
            has_title="title" in result,
            fields=list(result.keys()),
        )

    except PdfReadError as exc:
        log.warning("metadata.pdf_read_error", error=str(exc))
        result["error"] = f"PDF read error: {exc}"[:300]
    except Exception as exc:
        log.warning("metadata.unexpected_error", error=str(exc))
        result["error"] = f"Unexpected error: {exc}"[:300]

    return result


# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------
def _looks_like_pdf(data: bytes, content_type: str | None) -> bool:
    """Cheap check: PDFs start with %PDF and have an application/pdf MIME."""
    if content_type and content_type != "application/pdf":
        return False
    if not data:
        return False
    # PDF magic bytes: %PDF-1.x or %PDF-2.x
    return data[:5] == b"%PDF-"


def _extract_doc_info(meta: Any, result: dict[str, Any]) -> None:
    """Copy standard PDF metadata fields into result dict."""
    # Map of (pypdf attribute, output key, sanitize function)
    field_map: list[tuple[str, str]] = [
        ("/Title", "title"),
        ("/Author", "author"),
        ("/Subject", "subject"),
        ("/Keywords", "keywords"),
        ("/Creator", "creator"),
        ("/Producer", "producer"),
    ]

    for src_key, dst_key in field_map:
        try:
            value = meta.get(src_key)  # type: ignore[union-attr]
            if value is None:
                continue
            # pypdf returns PdfObject subclasses; str() gives the text
            text = str(value).strip()
            if text:
                result[dst_key] = text[:500]  # cap to keep storage sane
        except Exception as exc:
            log.debug("metadata.field_failed", field=src_key, error=str(exc))

    # Dates (CreationDate, ModDate) - parse to ISO if possible
    for src_key, dst_key in [("/CreationDate", "created"), ("/ModDate", "modified")]:
        try:
            value = meta.get(src_key)  # type: ignore[union-attr]
            if value is None:
                continue
            text = str(value).strip()
            iso = _parse_pdf_date(text)
            if iso:
                result[dst_key] = iso
        except Exception:
            pass


def _parse_pdf_version(header: bytes) -> str | None:
    """Extract PDF version from header bytes (e.g. %PDF-1.7 -> '1.7')."""
    if not header:
        return None
    try:
        text = header.decode("latin-1", errors="ignore")
        # Match %PDF-X.Y
        idx = text.find("%PDF-")
        if idx == -1:
            return None
        # Take next 3 chars (e.g. "1.7" or "2.0")
        version_str = text[idx + 5 : idx + 8]
        if len(version_str) == 3 and version_str[1] == ".":
            return version_str
    except Exception:
        pass
    return None


def _parse_pdf_date(raw: str) -> str | None:
    """Parse PDF date format (D:YYYYMMDDhhmmss+TZ) into ISO 8601.

    Returns None if parsing fails. We deliberately keep this tolerant - a
    malformed date string should not break the whole extraction.
    """
    if not raw:
        return None

    # PDF dates look like: D:20240115143022-05'00'
    text = raw.removeprefix("D:").strip()
    if len(text) < 4:
        return None

    # Take the year (4 chars) and optional month/day/time
    year = text[:4]
    month = text[4:6] if len(text) >= 6 else "01"
    day = text[6:8] if len(text) >= 8 else "01"
    hour = text[8:10] if len(text) >= 10 else "00"
    minute = text[10:12] if len(text) >= 12 else "00"
    second = text[12:14] if len(text) >= 14 else "00"

    # Validate ranges (defensive)
    try:
        if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
            return None
    except ValueError:
        return None

    # Ignore timezone parsing (PDF uses 'HHmm offset which is fiddly); emit UTC
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"


__all__ = ["extract_pdf_metadata"]
