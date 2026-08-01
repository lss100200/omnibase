"""Focused tests for opt-in structured benchmark scaffolding."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr

from omnibase.rag.benchmark import main, run_benchmark


def test_benchmark_reports_cold_warm_timings_and_rss() -> None:
    calls = 0

    def fake_embed(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[float(calls)] for _ in texts]

    report = run_benchmark(
        embed_batch=fake_embed,
        samples=["one", "two"],
        warm_iterations=2,
        provider="fake.provider",
        model_name="fake/model",
    )
    assert calls == 3
    assert report.cold.output_count == 2
    assert len(report.warm) == 2
    assert report.peak_rss_bytes > 0
    assert json.loads(report.model_dump_json())["schema_version"] == 1


def test_cli_refuses_model_import_without_explicit_opt_in(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "samples.json"
    input_path.write_text('["sample"]', encoding="utf-8")
    imported = False

    def fail_import(_name: str):
        nonlocal imported
        imported = True
        raise AssertionError("provider must not be imported")

    monkeypatch.setattr("omnibase.rag.benchmark.importlib.import_module", fail_import)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["--input", str(input_path)])
    assert exit_code == 2
    assert not imported
    assert "--allow-model-load" in stderr.getvalue()
