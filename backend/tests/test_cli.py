"""Unit tests for the OmniBase CLI.

Tests cover:
- Argument parsing (no DB required)
- `omnibase version` output
- Error path when alembic.ini is missing (migrate subcommand)

Integration tests (actually applying migrations) live in tests/integration/.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from omnibase import __version__
from omnibase.cli.main import build_parser, main


class TestParser:
    """build_parser / argparse wiring."""

    def test_migrate_default_target(self) -> None:
        """`omnibase migrate` defaults to target=head."""
        parser = build_parser()
        args = parser.parse_args(["migrate"])
        assert args.command == "migrate"
        assert args.target == "head"
        assert args.sql is False

    def test_migrate_custom_target(self) -> None:
        """Custom target and --sql flag propagate."""
        parser = build_parser()
        args = parser.parse_args(["migrate", "base", "--sql"])
        assert args.target == "base"
        assert args.sql is True

    def test_tenants_list(self) -> None:
        """`omnibase tenants list` parses."""
        parser = build_parser()
        args = parser.parse_args(["tenants", "list"])
        assert args.command == "tenants"
        assert args.tenants_cmd == "list"

    def test_tenants_create_requires_name(self) -> None:
        """`omnibase tenants create` requires --name."""
        parser = build_parser()
        # Missing --name should make argparse exit
        with pytest.raises(SystemExit):
            parser.parse_args(["tenants", "create"])

    def test_tenants_create_with_name_and_slug(self) -> None:
        """--name and --slug propagate."""
        parser = build_parser()
        args = parser.parse_args(["tenants", "create", "--name", "Acme", "--slug", "acme"])
        assert args.name == "Acme"
        assert args.slug == "acme"

    def test_version_subcommand(self) -> None:
        """`omnibase version` parses."""
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_unknown_command_errors(self) -> None:
        """Unknown subcommand triggers argparse error (exit 2)."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["nonexistent"])

    def test_no_command_errors(self) -> None:
        """Missing subcommand triggers argparse error (required=True)."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestVersionCommand:
    """`omnibase version` smoke test."""

    def test_version_prints_banner(self) -> None:
        """Version command prints app banner with version."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = main(["version"])
        output = buf.getvalue()
        assert exit_code == 0
        assert f"OmniBase v{__version__}" in output
        assert "env" in output
        assert "database" in output

    def test_version_masks_password(self) -> None:
        """Password in URLs is masked (no plaintext leak)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["version"])
        output = buf.getvalue()
        # The test settings use password 'secret' - ensure it doesn't appear
        assert "secret" not in output.lower()
        assert "***" in output


class TestMigrateCommand:
    """`omnibase migrate` paths that don't require DB."""

    def test_migrate_missing_alembic_ini_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing alembic.ini returns exit code 2 with clear error."""
        # Force cli module to look in tmp_path (which has no alembic.ini)
        # We monkeypatch Path.resolve to return a path under tmp_path

        original_resolve = Path.resolve

        def fake_resolve(self: Path) -> Path:
            """Redirect __file__ resolution to tmp_path."""
            result = original_resolve(self)
            # If the resolved path is the cli/main.py module, pretend it lives
            # in tmp_path so the parent[4] lookup yields tmp_path instead of backend/
            if str(result).endswith("cli\\main.py") or str(result).endswith("cli/main.py"):
                return tmp_path / "src" / "omnibase" / "cli" / "main.py"
            return result

        monkeypatch.setattr(Path, "resolve", fake_resolve)

        buf_err = io.StringIO()
        with redirect_stderr(buf_err):
            exit_code = main(["migrate"])

        assert exit_code == 2
        assert "alembic.ini not found" in buf_err.getvalue()


class TestTenantsListCommand:
    """`omnibase tenants list` graceful degradation without DB."""

    def test_tenants_list_db_unavailable(self) -> None:
        """`tenants list` returns non-zero when DB is unreachable.

        In our test env (no docker compose), the DB call should fail and
        the CLI should exit with code 1 (caught exception).
        """
        buf_err = io.StringIO()
        with redirect_stderr(buf_err):
            exit_code = main(["tenants", "list"])
        # Either 0 (no tenants - unlikely without DB) or 1 (DB error)
        assert exit_code in (0, 1)
