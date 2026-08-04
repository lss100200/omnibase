"""Validate the sealed P34.5A4 Linux Runner attack-Gate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_CASES = (
    "RUN-03",
    "RUN-04",
    "RUN-05",
    "FS-01",
    "FS-02",
    "FS-03",
    "NET-01",
    "NET-02",
    "PROC-01",
    "PROC-02",
    "HOST-01",
    "CROSS-01",
)


class EvidenceError(RuntimeError):
    """Raised when sealed evidence no longer matches the public source tree."""


def _load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"evidence file is unavailable or a symlink: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceError(f"evidence root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_passed_cases(report: dict[str, Any]) -> None:
    if report.get("gate") != "passed":
        raise EvidenceError("Runner attack Gate is not passed")
    if report.get("attack_matrix") != list(EXPECTED_CASES):
        raise EvidenceError("Runner attack matrix does not match the sealed 12-case set")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(EXPECTED_CASES):
        raise EvidenceError("Runner attack result count is invalid")
    result_cases = [item.get("case") for item in results if isinstance(item, dict)]
    if result_cases != list(EXPECTED_CASES):
        raise EvidenceError("Runner attack result ordering or identity drifted")
    if any(item.get("passed") is not True for item in results):
        raise EvidenceError("Runner attack report contains a failed case")
    if report.get("post_gate_service_healthy") is not True:
        raise EvidenceError("Runner was not healthy after the attack Gate")
    host = report.get("host")
    if not isinstance(host, dict) or type(host.get("service_uid")) is not int:
        raise EvidenceError("Runner host identity evidence is missing")
    if host["service_uid"] <= 0:
        raise EvidenceError("Runner service must remain non-root")


def validate(  # noqa: C901 - a linear closed-set evidence audit is easier to review
    repo_root: Path,
) -> None:
    root = repo_root.resolve(strict=True)
    evidence_root = root / "docs" / "evidence" / "p34-5"
    summary = _load_object(evidence_root / "linux-runner-attack-gate.json")
    if summary.get("schema_version") != 2 or summary.get("gate") != "passed":
        raise EvidenceError("Runner evidence summary is not the sealed schema v2 pass")
    if summary.get("root_env_accessed") is not False:
        raise EvidenceError("Runner evidence does not prove root .env exclusion")
    if summary.get("business_database_accessed") is not False:
        raise EvidenceError("Runner evidence does not prove business database exclusion")

    source_sha256 = summary.get("source_sha256")
    if not isinstance(source_sha256, dict) or not source_sha256:
        raise EvidenceError("Runner evidence has no source hash manifest")
    for relative, expected in sorted(source_sha256.items()):
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise EvidenceError("Runner source manifest entry is invalid")
        path = (root / relative).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise EvidenceError(f"Runner source path escaped the repository: {relative}") from exc
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"Runner source path is unavailable or a symlink: {relative}")
        if _sha256(path) != expected:
            raise EvidenceError(f"Runner source hash drifted: {relative}")

    raw = summary.get("raw_report")
    if not isinstance(raw, dict):
        raise EvidenceError("Runner raw report binding is missing")
    raw_relative = raw.get("path")
    raw_sha256 = raw.get("sha256")
    if not isinstance(raw_relative, str) or not isinstance(raw_sha256, str):
        raise EvidenceError("Runner raw report binding is invalid")
    raw_path = (root / raw_relative).resolve(strict=True)
    try:
        raw_path.relative_to(root)
    except ValueError as exc:
        raise EvidenceError("Runner raw report escaped the repository") from exc
    if _sha256(raw_path) != raw_sha256:
        raise EvidenceError("Runner raw report hash drifted")
    raw_report = _load_object(raw_path)
    _require_passed_cases(raw_report)

    summary_cases = summary.get("results")
    if not isinstance(summary_cases, list):
        raise EvidenceError("Runner summary result list is missing")
    if [item.get("id") for item in summary_cases if isinstance(item, dict)] != list(EXPECTED_CASES):
        raise EvidenceError("Runner summary result identities drifted")
    if any(item.get("passed") is not True for item in summary_cases):
        raise EvidenceError("Runner summary contains a failed case")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate(args.repo_root)
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"P34.5A4 Runner evidence validation failed: {exc}")
        return 2
    print("P34.5A4 Runner evidence seal passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
