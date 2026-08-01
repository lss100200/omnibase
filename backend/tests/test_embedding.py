"""Focused tests for version-aware embedding adapters."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from omnibase.rag import embedding
from omnibase.rag.index_metadata import DimensionMismatchError, IndexVersion


class _Vector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


def test_legacy_dimension_and_wrappers_remain_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    model = Mock()
    model.encode.return_value = _Vector([0.1] * 512)
    monkeypatch.setattr(embedding, "_get_model", lambda version=IndexVersion.V1: model)

    assert embedding.get_embedding_dim() == 512
    assert embedding.embed_query("query") == [0.1] * 512
    assert embedding.embed_query("   ") is None


def test_legacy_query_still_degrades_on_encode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Mock()
    model.encode.side_effect = RuntimeError("boom")
    monkeypatch.setattr(embedding, "_get_model", lambda version=IndexVersion.V1: model)

    assert embedding.embed_query("query") is None


def test_bge_m3_query_api_returns_1024_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Mock()
    model.encode.return_value = _Vector([0.2] * 1024)
    requested: list[IndexVersion] = []

    def get_model(version: IndexVersion) -> Mock:
        requested.append(version)
        return model

    monkeypatch.setattr(embedding, "_get_model", get_model)

    result = embedding.embed_query_v2("query")

    assert len(result) == 1024
    assert requested == [IndexVersion.V2]


def test_bge_m3_document_api_returns_1024_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Mock()
    model.encode.return_value = [_Vector([0.3] * 1024), _Vector([0.4] * 1024)]
    monkeypatch.setattr(embedding, "_get_model", lambda version: model)

    result = embedding.embed_documents_v2(["one", "two"])

    assert [len(vector) for vector in result] == [1024, 1024]


def test_strict_api_raises_when_model_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedding, "_get_model", lambda version: None)

    with pytest.raises(embedding.EmbeddingModelUnavailableError):
        embedding.embed_query_v2("query")


def test_strict_api_raises_on_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Mock()
    model.encode.return_value = _Vector([0.1] * 512)
    monkeypatch.setattr(embedding, "_get_model", lambda version: model)

    with pytest.raises(DimensionMismatchError) as exc_info:
        embedding.embed_query_v2("query")

    assert exc_info.value.expected == 1024
    assert exc_info.value.actual == 512


def test_strict_document_api_rejects_empty_values_before_model_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_model = Mock()
    monkeypatch.setattr(embedding, "_get_model", get_model)

    with pytest.raises(ValueError, match="empty"):
        embedding.embed_documents_v2(["valid", " "])

    get_model.assert_not_called()


def test_models_and_locks_are_distinct_per_version() -> None:
    assert set(embedding._models) == {IndexVersion.V1, IndexVersion.V2}
    assert embedding._model_locks[IndexVersion.V1] is not embedding._model_locks[IndexVersion.V2]
