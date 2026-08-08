"""Cross-platform desktop lifecycle entry point.

Exposes bounded lifecycle verbs over an approved, allowlisted profile:

    doctor / capabilities
    ports
    ports-suggest
    start --profile lite|local [--service ...]
    status [--service ...]
    health
    logs --tail N [--service ...]
    stop [--service ...]

The CLI never builds shell command strings from user input, never accepts
arbitrary services or verbs, never claims Hardened start support, and always
runs Compose verbs with an explicit ``--env-file .env.example``. Status/health/
log output passes through the safe diagnostic redactor.

Windows, Linux and macOS behave differently; evidence from one host is not
generalized to another (see the platform matrix in the capability report).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from omnibase.runtime import lifecycle


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dump(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, default=str))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OmniBase local desktop lifecycle (safe, allowlisted)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="probe host capabilities")
    doctor_parser.add_argument("--port", type=int, action="append", default=[8000, 3000])

    capabilities_parser = sub.add_parser("capabilities", help="alias of doctor")
    capabilities_parser.add_argument("--port", type=int, action="append", default=[8000, 3000])

    ports_parser = sub.add_parser("ports", help="report advisory port availability")
    ports_parser.add_argument("--port", type=int, action="append", default=[8000, 3000])

    suggest_parser = sub.add_parser("ports-suggest", help="suggest an advisory free port")
    suggest_parser.add_argument("preferred", type=int)

    start_parser = sub.add_parser("start", help="start allowlisted services")
    _add_profile_args(start_parser)

    status_parser = sub.add_parser("status", help="compose ps for allowlisted services")
    _add_service_args(status_parser)
    status_parser.add_argument(
        "--profile", choices=sorted(lifecycle.APPROVED_PROFILES), default="lite"
    )

    sub.add_parser("health", help="advisory capability + port + service health")

    logs_parser = sub.add_parser("logs", help="bounded, redacted compose logs")
    _add_profile_args(logs_parser)
    logs_parser.add_argument("--tail", type=int, default=lifecycle.DEFAULT_LOG_TAIL)

    stop_parser = sub.add_parser("stop", help="stop allowlisted services")
    _add_service_args(stop_parser)
    stop_parser.add_argument(
        "--profile", choices=sorted(lifecycle.APPROVED_PROFILES), default="lite"
    )

    return parser


def _add_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=sorted(lifecycle.APPROVED_PROFILES),
        required=True,
        help="approved product profile (hardened is blocked)",
    )
    _add_service_args(parser)


def _add_service_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--service",
        action="append",
        default=[],
        help=f"allowlisted service (one of: {sorted(lifecycle.ALLOWED_SERVICES)})",
    )


def _services_or_default(services: Sequence[str], profile: str) -> list[str]:
    if services:
        return list(services)
    # Default service set per profile. Lite keeps only read/probe surfaces;
    # local adds the full orchestration. Never includes hostile-code paths.
    if profile == "local":
        return ["backend", "frontend", "celery-worker", "postgres", "minio", "redis"]
    return ["backend", "postgres", "minio", "redis"]


def _lifecycle_result(run: Callable[[], lifecycle.LifecycleResult]) -> int:
    """Run one allowlisted lifecycle verb and dump its safe result.

    A missing container engine or missing ``.env.example`` is an environment
    precondition: the wrapper fails closed with a JSON error and exit code 2
    (never a raw traceback), and no Compose subprocess is ever attempted.
    """
    try:
        result = run()
    except FileNotFoundError as exc:
        _dump({"error": str(exc)})
        return 2
    _dump(result.to_dict())
    return 0 if result.exit_code == 0 else 1


def _run_lifecycle_verb(args: argparse.Namespace, repo_root: Path, verb: str) -> int:
    """Validate and dispatch one of the four Compose lifecycle verbs."""
    services = _services_or_default(args.service, args.profile)
    try:
        if verb == "start":
            request = lifecycle.validate_request(
                args.profile,
                services,
                timeout_seconds=lifecycle.DEFAULT_LIFECYCLE_TIMEOUT,
            )
        elif verb == "logs":
            request = lifecycle.validate_request(args.profile, services, tail_lines=args.tail)
        else:
            request = lifecycle.validate_request(args.profile, services)
    except ValueError as exc:
        _dump({"error": str(exc)})
        return 2
    if verb == "start":
        return _lifecycle_result(lambda: lifecycle.start(request, repo_root=repo_root))
    if verb == "status":
        return _lifecycle_result(lambda: lifecycle.status(request, repo_root=repo_root))
    if verb == "logs":
        return _lifecycle_result(lambda: lifecycle.logs(request, repo_root=repo_root))
    return _lifecycle_result(lambda: lifecycle.stop(request, repo_root=repo_root))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = _repo_root()

    if args.command in {"doctor", "capabilities"}:
        payload = lifecycle.doctor(repo_root=repo_root, ports=args.port)
        _dump(payload)
        return 0

    if args.command == "ports":
        payload = lifecycle.ports_status(ports=args.port)
        _dump(payload)
        return 0

    if args.command == "ports-suggest":
        payload = lifecycle.suggest_port_command(args.preferred)
        _dump(payload)
        return 0 if payload["suggested"] is not None else 2

    if args.command in {"start", "status", "logs", "stop"}:
        return _run_lifecycle_verb(args, repo_root, args.command)

    if args.command == "health":
        payload = lifecycle.health(repo_root=repo_root)
        _dump(payload)
        return 0

    parser.error(f"unknown_command:{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
