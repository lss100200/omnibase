"""Opt-in embedding benchmark with structured cold/warm timing and RSS output.

The benchmark does not run during imports or tests.  The caller must pass
``--allow-model-load`` before the embedding provider is imported, preventing an
accidental normal-test model download.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TimingSample(_FrozenModel):
    duration_ms: float = Field(ge=0)
    rss_before_bytes: int = Field(ge=0)
    rss_after_bytes: int = Field(ge=0)
    rss_delta_bytes: int
    output_count: int = Field(ge=0)


class BenchmarkReport(_FrozenModel):
    schema_version: int = 1
    provider: str
    model_name: str
    sample_count: int
    warm_iterations: int
    cold: TimingSample
    warm: tuple[TimingSample, ...]
    peak_rss_bytes: int


def _rss_bytes() -> int:
    """Return current RSS without adding a benchmark-only dependency."""

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.WorkingSetSize)

    from resource import RUSAGE_SELF, getrusage

    rss = getrusage(RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def _measure(call: Callable[[], Sequence[Any]]) -> TimingSample:
    before = _rss_bytes()
    started = time.perf_counter_ns()
    output = call()
    duration_ms = (time.perf_counter_ns() - started) / 1_000_000
    after = _rss_bytes()
    return TimingSample(
        duration_ms=duration_ms,
        rss_before_bytes=before,
        rss_after_bytes=after,
        rss_delta_bytes=after - before,
        output_count=len(output),
    )


def run_benchmark(
    *,
    embed_batch: Callable[[list[str]], Sequence[Any]],
    samples: list[str],
    warm_iterations: int,
    provider: str,
    model_name: str,
) -> BenchmarkReport:
    if not samples:
        raise ValueError("at least one benchmark sample is required")
    if warm_iterations < 1:
        raise ValueError("warm_iterations must be at least 1")
    cold = _measure(lambda: embed_batch(samples))
    warm = tuple(_measure(lambda: embed_batch(samples)) for _ in range(warm_iterations))
    return BenchmarkReport(
        provider=provider,
        model_name=model_name,
        sample_count=len(samples),
        warm_iterations=warm_iterations,
        cold=cold,
        warm=warm,
        peak_rss_bytes=max(
            [cold.rss_after_bytes, *(sample.rss_after_bytes for sample in warm)]
        ),
    )


def _load_provider(module_name: str) -> tuple[Callable[[list[str]], Sequence[Any]], str]:
    """Adapt current/planned embedding APIs at the CLI boundary."""

    module = importlib.import_module(module_name)
    embed_batch = getattr(module, "embed_batch", None)
    if not callable(embed_batch):
        raise TypeError(f"{module_name} must expose callable embed_batch(texts)")
    model_name = getattr(module, "get_model_name", None)
    if callable(model_name):
        return embed_batch, str(model_name())
    metadata = getattr(module, "get_active_metadata", None)
    if callable(metadata):
        return embed_batch, str(metadata().model_name)
    return embed_batch, str(getattr(module, "_model_name", "unknown"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in embedding benchmark")
    parser.add_argument(
        "--allow-model-load",
        action="store_true",
        help="Required acknowledgement that model loading/download may occur",
    )
    parser.add_argument(
        "--provider", default="omnibase.rag.embedding", help="Provider module"
    )
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSON string array")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    parser.add_argument("--warm-iterations", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.allow_model_load:
        print(
            "ERROR: benchmark is opt-in; pass --allow-model-load to permit model loading",
            file=sys.stderr,
        )
        return 2
    os.environ["OMNIBASE_BENCHMARK_ACTIVE"] = "1"
    samples = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(samples, list) or not all(isinstance(item, str) for item in samples):
        print("ERROR: --input must contain a JSON array of strings", file=sys.stderr)
        return 2
    embed_batch, model_name = _load_provider(args.provider)
    report = run_benchmark(
        embed_batch=embed_batch,
        samples=samples,
        warm_iterations=args.warm_iterations,
        provider=args.provider,
        model_name=model_name,
    )
    payload = report.model_dump_json(indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BenchmarkReport", "TimingSample", "build_parser", "main", "run_benchmark"]
