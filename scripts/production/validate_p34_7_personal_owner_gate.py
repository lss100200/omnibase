"""Validate the personal single-Owner Gate contract or one live request.

Validate-only mode is filesystem-only. Live mode resolves the tenant schema
from the server-owned tenant registry and performs no activation or mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from omnibase.core.config import get_settings  # noqa: E402
from omnibase.core.db import get_engine  # noqa: E402
from omnibase.db.models import Tenant  # noqa: E402
from omnibase.production.personal_owner_gate import (  # noqa: E402
    PersonalGateState,
    PersonalOwnerGate,
    PersonalOwnerGateRequest,
    load_personal_owner_gate_config,
)
from omnibase.tenants.schema_manager import set_search_path  # noqa: E402

DEFAULT_CONFIG = REPO_ROOT / "deployment" / "production" / "personal-single-owner.example.json"


def _load_request(path: Path) -> PersonalOwnerGateRequest:
    metadata = os.lstat(path)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("personal Gate request must be a regular non-link file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("personal Gate request must be an object")
    return PersonalOwnerGateRequest.from_mapping(payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--request", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config_path = args.config.resolve(strict=True)
    config = load_personal_owner_gate_config(config_path)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "contract_valid": True,
                    "profile": config.policy.profile,
                    "migration_head": config.migration_head,
                    "migration_0013_created": config.migration_0013_created,
                    "runtime_activated": False,
                },
                sort_keys=True,
            )
        )
        return 0

    assert args.request is not None
    request = _load_request(args.request.resolve(strict=True))
    engine = get_engine(get_settings())
    with Session(engine) as session, session.begin():
        tenant = session.execute(
            select(Tenant).where(Tenant.id == request.tenant_id, Tenant.is_active.is_(True))
        ).scalar_one_or_none()
        if tenant is None:
            print(
                json.dumps(
                    {
                        "state": PersonalGateState.INVALID.value,
                        "personal_activation_ready": False,
                        "runtime_activated": False,
                        "vetoes": ["live tenant is unavailable"],
                    },
                    sort_keys=True,
                )
            )
            return 1
        set_search_path(session, tenant.schema_name)
        report = PersonalOwnerGate(REPO_ROOT).verify(
            session,
            config=config,
            request=request,
        )
        print(json.dumps(report.to_dict(), sort_keys=True))
        if report.state is PersonalGateState.READY:
            return 0
        if report.state is PersonalGateState.BLOCKED:
            return 2
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
