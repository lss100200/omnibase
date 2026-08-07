"""Validate or verify a run-scoped hardened P34.7 joint evidence bundle offline.

Two mutually exclusive operating modes are supported and never blurred:

* ``--validate-only`` parses the static contract without verifying any real
  evidence and therefore always reports ``blocked/not_proven`` because direct
  evidence was not executed.
* ``--verify-evidence <run-dir>`` may report ``passed`` only when every
  mandatory real, sealed, component-specific artifact exists under ``run-dir``
  and all cross-component identities, hashes, chronology, semantics, attack
  results and cleanup checks verify against the actual file bytes.

The validator is offline: it never starts a service, opens a network
connection, reads the root ``.env``, accesses a database, executes code or
activates the production Runtime.  Exit codes are ``0`` for a fully verified
real evidence chain, ``2`` for valid evidence that is still
``blocked/not_proven`` and ``1`` for malformed or unsafe evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from omnibase.production.composition import ConfigurationError  # noqa: E402
from omnibase.production.joint_gate import (  # noqa: E402
    JointGateReport,
    validate_joint_evidence_contract,
    verify_joint_evidence,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--verify-evidence", type=Path, metavar="RUN_DIR")
    parser.add_argument("--evidence", type=Path, help="evidence JSON bundle path")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path OUTSIDE the repository; stdout is always emitted",
    )
    return parser.parse_args(argv)


def _load_evidence(path: Path | None) -> object:
    if path is None:
        raise SystemExit("evidence bundle path is required")
    return json.loads(path.read_text(encoding="utf-8"))


def _emit(report: JointGateReport, output: Path | None) -> None:
    payload = report.to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.validate_only:
            payload = _load_evidence(args.evidence)
            report = validate_joint_evidence_contract(payload)
            code = 2
        else:
            assert args.verify_evidence is not None  # noqa: S101
            run_dir: Path = args.verify_evidence
            payload = _load_evidence(args.evidence)
            report = verify_joint_evidence(run_dir, payload)
            code = 0 if report.passed else 2
    except (ConfigurationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = {
            "schema": "omnibase.p34-7.hardened-joint-evidence.v2",
            "schema_version": 2,
            "status": "invalid/veto",
            "passed": False,
            "blockers": [],
            "vetoes": [str(exc)],
            "root_env_accessed": "not_proven",
            "business_database_accessed": "not_proven",
            "business_database_migrated": "not_proven",
            "runtime_activated": "not_proven",
        }
        text = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    _emit(report, args.output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
