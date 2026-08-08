#!/usr/bin/env python3
"""P34.7 Trust Policy Candidate R0 validator (engineering-only, offline).

Validates one candidate trust policy plus its external approval packet
against the frozen R0 governance contract.  The highest positive status is
``candidate/valid_not_approved``: this tool NEVER approves a trust policy,
never writes a digest into ``joint_gate._APPROVED_TRUST_POLICY_SHA256`` and
never changes the P34.7 production decision (which stays blocked/not_proven).

Exit codes:

* 0 -- raw bytes verified and lifecycle is ``candidate``
  (``candidate/valid_not_approved``, ``production_approved=false``,
  ``activation_allowed=false``);
* 1 -- anything else: ``invalid/veto`` (structural contract violation),
  ``candidate/structural_valid`` (raw digest not verified) or any
  ``<lifecycle>/not_approved`` non-candidate outcome.

The tool never reads the root ``.env``, never accesses a database, never
opens a network connection and never executes hostile code.

Usage:

    python scripts/production/validate_p34_7_trust_policy_candidate.py \
      --candidate deployment/production/p34-7-trust-policy-candidate.example.json \
      --approval-packet deployment/production/p34-7-trust-policy-approval-packet.example.json \
      --validate-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from omnibase.production.composition import ConfigurationError  # noqa: E402
from omnibase.production.trust_policy_candidate import (  # noqa: E402
    validate_trust_policy_candidate_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, required=True, help="candidate trust-policy JSON file"
    )
    parser.add_argument(
        "--approval-packet", type=Path, required=True, help="external approval-packet JSON file"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the candidate contract only (never approves)",
    )
    mode.add_argument(
        "--verify-candidate",
        action="store_true",
        help="verify the candidate chain (same fail-closed contract; never approves)",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="repository root (default: repo root)"
    )
    args = parser.parse_args()
    try:
        report = validate_trust_policy_candidate_files(
            args.candidate, args.approval_packet, args.repo_root
        )
    except ConfigurationError as exc:
        payload = {
            "status": "invalid/veto",
            "reason": str(exc),
            "production_approved": False,
            "approved_digest_written": False,
            "activation_allowed": False,
        }
        print(json.dumps(payload, indent=2))
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.status == "candidate/valid_not_approved" else 1


if __name__ == "__main__":
    raise SystemExit(main())
