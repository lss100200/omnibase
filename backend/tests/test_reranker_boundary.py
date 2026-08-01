"""Tests for the reranker's offline-first model boundary."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from omnibase.rag import reranker


def test_missing_local_reranker_fails_fast_without_remote_import(
    monkeypatch,
    tmp_path,
) -> None:
    missing_path = tmp_path / "missing-reranker"
    monkeypatch.setattr(reranker, "_reranker", None)
    monkeypatch.setattr(reranker, "_reranker_initialized", False)
    monkeypatch.setattr(
        reranker,
        "get_settings",
        lambda: SimpleNamespace(
            reranker_model_path=str(missing_path),
            model_download_enabled=False,
            model_cache_dir=str(tmp_path),
        ),
    )

    assert reranker._get_reranker() is None
    assert reranker._reranker_initialized is True
    assert reranker._get_reranker() is None


def test_local_reranker_is_loaded_without_network(
    monkeypatch,
    tmp_path,
) -> None:
    model_path = tmp_path / "reranker"
    model_path.mkdir()
    model = MagicMock()
    cross_encoder = MagicMock(return_value=model)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(CrossEncoder=cross_encoder),
    )
    monkeypatch.setattr(reranker, "_reranker", None)
    monkeypatch.setattr(reranker, "_reranker_initialized", False)
    monkeypatch.setattr(
        reranker,
        "get_settings",
        lambda: SimpleNamespace(
            reranker_model_path=str(model_path),
            model_download_enabled=False,
            model_cache_dir=str(tmp_path),
        ),
    )

    assert reranker._get_reranker() is model
    cross_encoder.assert_called_once_with(
        str(model_path),
        device="cpu",
        max_length=512,
        cache_dir=str(tmp_path),
        local_files_only=True,
    )
