#!/usr/bin/env python3
"""Validate the offline P34.7 Trust Policy R1-A assignment contract.

Exit codes:

* 0: the assignment document is structurally valid.  The report may still be
  ``r1_assignment/valid_incomplete`` and always remains non-production.
* 1: contract violation (``invalid/veto``).
* 2: ``--verify`` was requested.  R1-A cannot independently authenticate the
  assignments or review receipts and therefore always remains not proven.

This command never approves a policy, writes a digest, runs a key ceremony,
collects production evidence, starts a service, reads the root ``.env`` or
accesses a database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from omnibase.production.composition import ConfigurationError  # noqa: E402
from omnibase.production.trust_policy_r1_assignment import (  # noqa: E402
    validate_trust_policy_r1_assignment_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_trust_policy_r1_assignment_file(args.assignment, args.repo_root)
    except ConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "invalid/veto",
                    "reason": str(exc),
                    "trust_policy_approved": False,
                    "approved_digest_written": False,
                    "activation_allowed": False,
                    "p34_7_production_total_gate": "blocked/not_proven",
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    independently_verified = (
        report.authority_authentication_verified
        and report.independent_review_receipts_verified
        and report.custody_attestations_verified
        and report.environment_evidence_verified
        and report.production_blockers_closed
    )
    if args.verify and not independently_verified:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
