#!/usr/bin/env python3
"""Manage the bounded P5 personal single-Owner Runtime canary.

This controller is filesystem-only. It never reads the root ``.env``, opens a
database connection, changes process environment variables, starts a Runtime
process or activates Planner/Multi-Agent. The Runtime consumes the resulting
server-owned config and append-only state directory through explicit mounts.

Exit codes:

* 0 -- requested validation or state transition completed successfully;
* 1 -- invalid config, invalid/tampered state, binding drift or unsafe input;
* 2 -- a valid but non-active status (inactive, expired, rolled back, killed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from omnibase.production.personal_runtime_activation import (  # noqa: E402
    PersonalRuntimeCanaryConfig,
    PersonalRuntimeConfigurationError,
    PersonalRuntimeState,
    PersonalRuntimeStatus,
    activate_personal_runtime_canary,
    kill_personal_runtime_canary,
    load_personal_runtime_canary_config,
    personal_runtime_status_binding_valid,
    read_personal_runtime_status,
    rollback_personal_runtime_canary,
)


def _absolute_path(value: str, *, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PersonalRuntimeConfigurationError(f"{name} must be absolute")
    return path


def _load_config(args: argparse.Namespace) -> PersonalRuntimeCanaryConfig:
    return load_personal_runtime_canary_config(
        _absolute_path(args.config, name="config path"),
        repo_root=_absolute_path(args.repo_root, name="repo root"),
        verify_owner_readiness=True,
    )


def _binding_valid(
    config: PersonalRuntimeCanaryConfig,
    status: PersonalRuntimeStatus,
) -> bool:
    if status.events == 0:
        return status.state in {
            PersonalRuntimeState.INACTIVE,
            PersonalRuntimeState.KILLED,
        }
    return personal_runtime_status_binding_valid(config, status)


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _config_payload(
    operation: str,
    config: PersonalRuntimeCanaryConfig,
) -> dict[str, object]:
    plan = config.activation_plan()
    return {
        "canary_id": config.canary_id,
        "config_sha256": config.canonical_digest(),
        "contract_valid": True,
        "operation": operation,
        "plan": plan.to_dict(),
        "plan_sha256": plan.canonical_digest(),
        "production_runtime_started": False,
        "profile": config.profile,
    }


def _status_payload(
    operation: str,
    status: PersonalRuntimeStatus,
    *,
    binding_valid: bool | None = None,
) -> dict[str, object]:
    payload = status.to_dict()
    payload.update(
        {
            "operation": operation,
            "production_runtime_started": False,
        }
    )
    if binding_valid is not None:
        payload["binding_valid"] = binding_valid
    return payload


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="absolute canonical canary config path")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="absolute public repository root used to verify the readiness seal",
    )


def _add_state(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        required=True,
        help="absolute run-scoped activation state directory",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate config and readiness seal")
    _add_config(validate)

    plan = commands.add_parser("plan", help="emit the deterministic activation plan")
    _add_config(plan)

    activate = commands.add_parser("activate-canary", help="append the activation receipt")
    _add_config(activate)
    _add_state(activate)
    activate.add_argument(
        "--confirm-plan-sha256",
        required=True,
        help="exact plan SHA-256 printed by the plan command",
    )

    status = commands.add_parser("status", help="verify the event chain and config binding")
    _add_config(status)
    _add_state(status)

    rollback = commands.add_parser("rollback", help="append one terminal rollback receipt")
    _add_config(rollback)
    _add_state(rollback)
    rollback.add_argument("--reason-code", required=True)

    kill = commands.add_parser(
        "kill",
        help="write an irreversible kill marker without trusting config or ledger",
    )
    _add_state(kill)
    kill.add_argument("--canary-id", required=True)
    kill.add_argument("--reason-code", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    if args.command == "kill":
        result = kill_personal_runtime_canary(
            state_dir=_absolute_path(args.state_dir, name="state directory"),
            canary_id=args.canary_id,
            reason_code=args.reason_code,
        )
        _emit(_status_payload("kill", result))
        return 0 if result.state is PersonalRuntimeState.KILLED else 1

    config = _load_config(args)
    if args.command in {"validate", "plan"}:
        _emit(_config_payload(args.command, config))
        return 0

    state_dir = _absolute_path(args.state_dir, name="state directory")
    if args.command == "activate-canary":
        result = activate_personal_runtime_canary(
            config,
            state_dir=state_dir,
            confirmed_plan_sha256=args.confirm_plan_sha256,
        )
        _emit(_status_payload(args.command, result, binding_valid=True))
        return 0
    if args.command == "rollback":
        result = rollback_personal_runtime_canary(
            config,
            state_dir=state_dir,
            reason_code=args.reason_code,
        )
        _emit(_status_payload(args.command, result, binding_valid=True))
        return 0
    if args.command == "status":
        result = read_personal_runtime_status(state_dir)
        binding_valid = _binding_valid(config, result)
        _emit(_status_payload(args.command, result, binding_valid=binding_valid))
        if result.state is PersonalRuntimeState.INVALID or not binding_valid:
            return 1
        return 0 if result.state is PersonalRuntimeState.ACTIVE else 2
    raise PersonalRuntimeConfigurationError("unsupported command")


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(_parse_args(argv))
    except (OSError, PersonalRuntimeConfigurationError, json.JSONDecodeError) as exc:
        _emit(
            {
                "error": str(exc),
                "operation": "invalid/veto",
                "production_runtime_started": False,
                "state": "invalid/veto",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
