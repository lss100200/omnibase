"""Validate a run-scoped hardened P34.7 joint evidence bundle offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from omnibase.production.joint_gate import validate_joint_evidence  # noqa: E402
from omnibase.production.composition import ConfigurationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
        report = validate_joint_evidence(args.run_dir, payload)
        result = report.to_dict()
    except (ConfigurationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = {
            "schema": "omnibase.p34-7.hardened-joint-evidence.v1",
            "status": "invalid/veto",
            "passed": False,
            "blockers": [],
            "vetoes": [str(exc)],
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "runtime_activated": False,
        }
        code = 1
    else:
        code = 0 if report.passed else 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
