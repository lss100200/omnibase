"""OmniBase CLI entry points.

Usage:
    omnibase migrate          # Run all pending migrations (global + per-tenant)
    omnibase migrate --sql    # Emit SQL instead of executing
    omnibase tenants list     # List all active tenants
    omnibase tenants create --name "Acme" --slug acme
    omnibase version          # Print version + config summary

The CLI is a thin wrapper around Alembic + omnibase.tenants.service.
It exists so users don't need to remember alembic invocations and so
docker-compose can call a single command in `make migrate`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from omnibase import __version__
from omnibase.core.config import get_settings
from omnibase.core.logging import configure_logging, get_logger


def cmd_migrate(args: argparse.Namespace) -> int:
    """Run Alembic migrations (online by default, offline if --sql)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    configure_logging()
    log = get_logger("omnibase.cli.migrate")

    # Locate alembic.ini (lives in backend/)
    backend_root = Path(__file__).resolve().parents[4]
    alembic_ini = backend_root / "alembic.ini"
    if not alembic_ini.exists():
        log.error("alembic_ini_not_found", path=str(alembic_ini))
        print(f"ERROR: alembic.ini not found at {alembic_ini}", file=sys.stderr)
        return 2

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(backend_root / "src" / "omnibase" / "migrations"))

    settings = get_settings()
    config.set_main_option("sqlalchemy.url", str(settings.database_url))

    target = args.target or "head"
    try:
        if args.sql:
            log.info("migrate.offline", target=target)
            command.upgrade(config, target, sql=True)
        else:
            log.info("migrate.online", target=target)
            command.upgrade(config, target)
    except Exception as exc:
        log.error("migrate.failed", error=str(exc), exc_info=True)
        print(f"ERROR: migration failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK: migrated to {target}")
    return 0


def cmd_tenants(args: argparse.Namespace) -> int:
    """Tenant management subcommands."""
    configure_logging()
    log = get_logger("omnibase.cli.tenants")

    if args.tenants_cmd == "list":
        from omnibase.tenants.service import get_all_active_tenant_schemas

        try:
            schemas = get_all_active_tenant_schemas()
        except Exception as exc:
            log.error("tenants.list_failed", error=str(exc))
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not schemas:
            print("(no active tenants)")
            return 0
        print(f"Active tenants ({len(schemas)}):")
        for s in schemas:
            print(f"  - {s}")
        return 0

    if args.tenants_cmd == "create":
        from omnibase.tenants.service import create_tenant

        try:
            tenant = create_tenant(name=args.name, slug=args.slug)
        except Exception as exc:
            log.error("tenants.create_failed", error=str(exc))
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(
            f"OK: created tenant\n"
            f"  id      : {tenant.id}\n"
            f"  name    : {tenant.name}\n"
            f"  slug    : {tenant.slug}\n"
            f"  schema  : {tenant.schema_name}"
        )
        return 0

    print(f"Unknown tenants subcommand: {args.tenants_cmd}", file=sys.stderr)
    return 2


def cmd_version(_args: argparse.Namespace) -> int:
    """Print version + active configuration."""
    settings = get_settings()
    print(f"OmniBase v{__version__}")
    print(f"  env            : {settings.env.value}")
    print(f"  log_level      : {settings.log_level.value}")
    print(f"  database       : {_mask_url(str(settings.database_url))}")
    print(f"  minio_endpoint : {settings.minio_endpoint}")
    print(f"  redis_url      : {_mask_url(str(settings.redis_url))}")
    print(f"  jwt_algorithm  : {settings.jwt_algorithm}")
    return 0


def _mask_url(url: str) -> str:
    """Mask password in URL for safe display."""
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{host}"
    return url


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="omnibase",
        description="OmniBase CLI - manage migrations, tenants, and inspect config.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # omnibase migrate [target] [--sql]
    p_migrate = sub.add_parser("migrate", help="Run database migrations")
    p_migrate.add_argument(
        "target",
        nargs="?",
        default="head",
        help="Migration target (default: head)",
    )
    p_migrate.add_argument(
        "--sql",
        action="store_true",
        help="Emit SQL instead of applying (offline mode)",
    )
    p_migrate.set_defaults(func=cmd_migrate)

    # omnibase tenants <list|create>
    p_tenants = sub.add_parser("tenants", help="Tenant management")
    p_ten_sub = p_tenants.add_subparsers(dest="tenants_cmd", required=True)
    p_ten_list = p_ten_sub.add_parser("list", help="List active tenants")
    p_ten_list.set_defaults(func=cmd_tenants)
    p_ten_create = p_ten_sub.add_parser("create", help="Create a new tenant")
    p_ten_create.add_argument("--name", required=True, help="Tenant display name")
    p_ten_create.add_argument("--slug", default=None, help="URL-safe slug (auto if omitted)")
    p_ten_create.set_defaults(func=cmd_tenants)

    # omnibase version
    p_version = sub.add_parser("version", help="Print version and config")
    p_version.set_defaults(func=cmd_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_parser", "main"]
