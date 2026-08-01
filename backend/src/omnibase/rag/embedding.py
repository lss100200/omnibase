"""Version-aware embedding adapters for OmniBase RAG.

The legacy ``embed_query``/``embed_batch`` surface remains the graceful v1
adapter. New version-aware query/document APIs are strict: unavailable models,
encoding failures, and dimension violations raise typed exceptions.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger
from omnibase.rag.index_metadata import (
    DimensionMismatchError,
    IndexVersion,
    get_index_metadata,
    validate_dimension,
)

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = get_logger(__name__)


class EmbeddingError(RuntimeError):
    """Base error for strict embedding operations."""


class EmbeddingModelUnavailableError(EmbeddingError):
    """Raised when a requested embedding model cannot be loaded."""

    def __init__(self, version: IndexVersion) -> None:
        self.version = version
        metadata = get_index_metadata(version)
        super().__init__(f"Embedding model unavailable for {version}: {metadata.model_name}")


class EmbeddingEncodingError(EmbeddingError):
    """Raised when a model fails to encode otherwise valid input."""


# Each index contract owns an independent lazy singleton and initialization lock.
_models: dict[IndexVersion, SentenceTransformer | None] = dict.fromkeys(IndexVersion)
_model_locks: dict[IndexVersion, threading.Lock] = {
    version: threading.Lock() for version in IndexVersion
}

# Local model directories checked before HuggingFace download.
# Maps HuggingFace model_name to a filesystem path if available.
_LOCAL_MODEL_PATHS: dict[str, str] = {
    "BAAI/bge-m3": "/app/models/bge-m3",
}

# Preserve private v1 names used by existing diagnostics/tests.
_model: SentenceTransformer | None = None
_model_lock = _model_locks[IndexVersion.V1]
_model_name = get_index_metadata(IndexVersion.V1).model_name
_embedding_dim = get_index_metadata(IndexVersion.V1).dimension


def _get_model(
    version: IndexVersion | int | str = IndexVersion.V1,
) -> SentenceTransformer | None:
    """Lazily load one model singleton per supported index version."""
    global _model

    resolved = IndexVersion.parse(version)
    if resolved is IndexVersion.V1 and _model is not None:
        _models[resolved] = _model
    model = _models[resolved]
    if model is not None:
        return model

    with _model_locks[resolved]:
        if resolved is IndexVersion.V1 and _model is not None:
            _models[resolved] = _model
        model = _models[resolved]
        if model is not None:
            return model

        metadata = get_index_metadata(resolved)
        try:
            from sentence_transformers import SentenceTransformer

            settings = get_settings()
            local_path = _LOCAL_MODEL_PATHS.get(metadata.model_name)
            model_source = (
                local_path if local_path and Path(local_path).is_dir() else metadata.model_name
            )
            local_files_only = Path(model_source).is_dir() or not settings.model_download_enabled

            log.info(
                "rag.embedding.model_loading",
                model=metadata.model_name,
                source=model_source,
                local_files_only=local_files_only,
                index_version=str(resolved),
            )
            model = SentenceTransformer(
                model_source,
                device="cpu",
                cache_folder=settings.model_cache_dir,
                local_files_only=local_files_only,
            )
            actual_dim = model.get_sentence_embedding_dimension()
            if actual_dim != metadata.dimension:
                # Legacy behavior logs this mismatch and lets old wrappers degrade
                # naturally. Strict APIs validate every returned vector below.
                log.warning(
                    "rag.embedding.dim_mismatch",
                    expected=metadata.dimension,
                    actual=actual_dim,
                    index_version=str(resolved),
                )
            _models[resolved] = model
            if resolved is IndexVersion.V1:
                _model = model
            log.info(
                "rag.embedding.model_ready",
                model=metadata.model_name,
                dim=actual_dim,
                device="cpu",
                index_version=str(resolved),
            )
        except ImportError:
            log.error(
                "rag.embedding.import_error",
                msg="sentence-transformers not installed; run 'uv sync --group dev'",
                index_version=str(resolved),
            )
        except Exception as exc:
            log.error(
                "rag.embedding.model_load_failed",
                model=metadata.model_name,
                index_version=str(resolved),
                error=str(exc),
                exc_info=True,
            )

        return model


def get_embedding_dim(
    version: IndexVersion | int | str = IndexVersion.V1,
) -> int:
    """Return the contracted dimension; no argument preserves v1's 512."""
    return get_index_metadata(version).dimension


def _to_vector(value: Any) -> list[float]:
    vector = value.tolist() if hasattr(value, "tolist") else list(value)
    return [float(item) for item in vector]


def _validate_vector(vector: list[float], version: IndexVersion) -> list[float]:
    validate_dimension(len(vector), version)
    return vector


# BGE-M3 query instruction prefix (per BGE-M3 paper).
# Applied only to V2 queries, not documents. V1 uses no prefix.
_V2_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def embed_query_for_version(
    text: str,
    version: IndexVersion | int | str,
) -> list[float]:
    """Strictly embed a retrieval query under a declared index contract.

    For V2 (BGE-M3), applies the standard query instruction prefix before
    encoding.  V1 (bge-small-zh) encodes the raw text unchanged.
    """
    resolved = IndexVersion.parse(version)
    if not text or not text.strip():
        raise ValueError("Query text must not be empty")

    model = _get_model(resolved)
    if model is None:
        raise EmbeddingModelUnavailableError(resolved)

    encode_text = text
    if resolved is IndexVersion.V2:
        encode_text = f"{_V2_QUERY_INSTRUCTION}{text}"

    try:
        vector = _to_vector(
            model.encode(
                encode_text,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
        )
        return _validate_vector(vector, resolved)
    except DimensionMismatchError:
        raise
    except Exception as exc:
        raise EmbeddingEncodingError(f"Query embedding failed for {resolved}: {exc}") from exc


def embed_documents_for_version(
    texts: list[str],
    version: IndexVersion | int | str,
    batch_size: int = 32,
) -> list[list[float]]:
    """Strictly embed non-empty documents under a declared index contract."""
    resolved = IndexVersion.parse(version)
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if any(not text or not text.strip() for text in texts):
        raise ValueError("Document texts must not contain empty values")
    if not texts:
        return []

    model = _get_model(resolved)
    if model is None:
        raise EmbeddingModelUnavailableError(resolved)

    results: list[list[float]] = []
    try:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = model.encode(
                batch,
                batch_size=len(batch),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vectors = [_validate_vector(_to_vector(value), resolved) for value in encoded]
            if len(vectors) != len(batch):
                raise EmbeddingEncodingError(
                    f"Document embedding count mismatch: expected {len(batch)}, got {len(vectors)}"
                )
            results.extend(vectors)
    except (DimensionMismatchError, EmbeddingEncodingError):
        raise
    except Exception as exc:
        raise EmbeddingEncodingError(f"Document embedding failed for {resolved}: {exc}") from exc
    return results


def embed_query_v2(text: str) -> list[float]:
    """Strict BGE-M3 query API returning exactly 1024 dimensions."""
    return embed_query_for_version(text, IndexVersion.V2)


def embed_documents_v2(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Strict BGE-M3 document API returning exactly 1024 dimensions."""
    return embed_documents_for_version(texts, IndexVersion.V2, batch_size=batch_size)


def embed_query(text: str) -> list[float] | None:
    """Legacy graceful v1 query adapter; behavior is intentionally unchanged."""
    if not text or not text.strip():
        return None

    model = _get_model()
    if model is None:
        return None

    try:
        vector = model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vector.tolist()
    except Exception as exc:
        log.error("rag.embedding.query_failed", error=str(exc), text_preview=text[:100])
        return None


def embed_batch(
    texts: list[str],
    batch_size: int = 32,
) -> list[list[float] | None]:
    """Legacy graceful v1 document adapter; behavior is intentionally unchanged."""
    if not texts:
        return []

    model = _get_model()
    if model is None:
        return [None] * len(texts)

    results: list[list[float] | None] = []
    total = len(texts)

    for i in range(0, total, batch_size):
        batch = texts[i : i + batch_size]
        non_empty_indices = [j for j, text in enumerate(batch) if text and text.strip()]
        non_empty_texts = [batch[j] for j in non_empty_indices]

        if not non_empty_texts:
            results.extend([None] * len(batch))
            continue

        try:
            vectors = model.encode(
                non_empty_texts,
                batch_size=len(non_empty_texts),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            batch_results: list[list[float] | None] = [None] * len(batch)
            for idx, vec in zip(non_empty_indices, vectors, strict=False):
                batch_results[idx] = vec.tolist()
            results.extend(batch_results)
        except Exception as exc:
            log.error(
                "rag.embedding.batch_failed",
                batch_start=i,
                batch_size=len(batch),
                error=str(exc),
            )
            results.extend([None] * len(batch))

        if (i // batch_size) % 5 == 0:
            log.debug(
                "rag.embedding.batch_progress",
                done=min(i + batch_size, total),
                total=total,
            )

    log.info(
        "rag.embedding.batch_complete",
        total=total,
        embedded=sum(1 for result in results if result),
    )
    return results


def is_available(
    version: IndexVersion | int | str = IndexVersion.V1,
) -> bool:
    """Check whether a requested model singleton can be loaded."""
    return _get_model(version) is not None


__all__ = [
    "EmbeddingEncodingError",
    "EmbeddingError",
    "EmbeddingModelUnavailableError",
    "embed_batch",
    "embed_documents_for_version",
    "embed_documents_v2",
    "embed_query",
    "embed_query_for_version",
    "embed_query_v2",
    "get_embedding_dim",
    "is_available",
]
