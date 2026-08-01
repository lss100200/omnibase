"""Unit tests for the tenant module.

Pure unit tests (no DB / no Docker) for:
- slug validation & generation
- schema name derivation
- schema name validation

Integration tests (requiring PostgreSQL) live in tests/integration/ and are
skipped automatically when DB is unreachable (marked via the `integration`
pytest marker).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from omnibase.core import db as db_module
from omnibase.core.db import (
    TENANT_CONTEXT_REQUIRED_SESSION_KEY,
    TENANT_SCHEMA_SESSION_KEY,
)
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.tenants.context import get_current_schema, tenant_scope
from omnibase.tenants.dependencies import (
    CurrentPrincipal,
    TenantContext,
    get_current_principal,
    get_current_tenant,
    get_tenant_db,
    require_tenant_admin,
)
from omnibase.tenants.schema_manager import (
    SchemaError,
    TenantSession,
    drop_schema,
    make_schema_name,
    set_search_path,
    validate_schema_name,
)
from omnibase.tenants.service import (
    InvalidTenantSlug,
    _generate_unique_slug,
    create_tenant,
    validate_slug,
)


class TestSchemaNameDerivation:
    """make_schema_name / validate_schema_name."""

    def test_make_schema_name_from_uuid(self) -> None:
        """Valid UUID derives a valid schema name."""
        uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        name = make_schema_name(uuid)
        assert name == "tenant_a1b2c3d4"
        validate_schema_name(name)  # must not raise

    def test_make_schema_name_lowercase(self) -> None:
        """Uppercase hex in UUID becomes lowercase schema name."""
        name = make_schema_name("ABCDEF12-3456-7890-ABCD-EF1234567890")
        assert name == "tenant_abcdef12"

    def test_make_schema_name_rejects_empty(self) -> None:
        """Empty tenant_id raises SchemaError."""
        with pytest.raises(SchemaError):
            make_schema_name("")

    def test_make_schema_name_rejects_short(self) -> None:
        """Tenant_id that yields < 8 hex chars raises SchemaError."""
        with pytest.raises(SchemaError):
            make_schema_name("short")

    def test_validate_schema_name_accepts_valid(self) -> None:
        """Valid tenant_<8hex> schema names pass."""
        for valid in ["tenant_a1b2c3d4", "tenant_deadbeef", "tenant_c0ffee99"]:
            validate_schema_name(valid)  # no raise

    @pytest.mark.parametrize(
        "invalid",
        [
            "public",  # missing tenant_ prefix
            "tenant_short",  # < 8 chars
            "tenant_ABCDEF12",  # uppercase
            "tenant_!@#$%^&*",  # special chars
            "tenantwithhyphen",  # no underscore
            "tenant_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2g3",  # too long
            "",  # empty
        ],
    )
    def test_validate_schema_name_rejects_invalid(self, invalid: str) -> None:
        """Invalid schema names raise SchemaError."""
        with pytest.raises(SchemaError):
            validate_schema_name(invalid)


class TestTenantIsolation:
    def test_tenant_scope_restores_nested_context(self) -> None:
        assert get_current_schema() is None
        with tenant_scope("tenant_deadbeef"):
            assert get_current_schema() == "tenant_deadbeef"
            with tenant_scope("tenant_c0ffee99"):
                assert get_current_schema() == "tenant_c0ffee99"
            assert get_current_schema() == "tenant_deadbeef"
        assert get_current_schema() is None

    @pytest.mark.asyncio
    async def test_current_tenant_dependency_resets_token(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        user = User(
            id="11111111-1111-1111-1111-111111111111",
            email="user@example.com",
            password_hash="hash",  # noqa: S106 - inert ORM fixture value
            is_active=True,
            is_tenant_admin=False,
        )
        payload = MagicMock(tenant_id=str(tenant.id), sub=str(user.id))
        session = MagicMock()
        session.info = {}
        session.execute.return_value.scalar_one_or_none.return_value = user
        with (
            patch(
                "omnibase.tenants.dependencies._extract_access_payload",
                return_value=payload,
            ),
            patch("omnibase.tenants.dependencies.get_tenant_by_id", return_value=tenant),
            patch(
                "omnibase.tenants.dependencies.get_session_factory",
                return_value=lambda: session,
            ),
        ):
            dependency = get_current_principal("Bearer token")
            principal = await anext(dependency)
            context = get_current_tenant(principal)
            assert context.schema_name == tenant.schema_name
            assert context.user_id == str(user.id)
            assert get_current_schema() == tenant.schema_name
            await dependency.aclose()
        session.close.assert_called_once()
        assert get_current_schema() is None

    def test_current_tenant_fastapi_cleanup_stays_in_context(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        app = FastAPI()

        @app.get("/tenant")
        async def tenant_endpoint(
            ctx: TenantContext = Depends(get_current_tenant),
        ) -> dict[str, str | None]:
            return {
                "schema_name": ctx.schema_name,
                "active_schema": get_current_schema(),
            }

        user = User(
            id="11111111-1111-1111-1111-111111111111",
            email="user@example.com",
            password_hash="hash",  # noqa: S106 - inert ORM fixture value
            is_active=True,
            is_tenant_admin=False,
        )
        payload = MagicMock(tenant_id=str(tenant.id), sub=str(user.id))
        session = MagicMock()
        session.info = {}
        session.execute.return_value.scalar_one_or_none.return_value = user
        with (
            patch(
                "omnibase.tenants.dependencies._extract_access_payload",
                return_value=payload,
            ),
            patch("omnibase.tenants.dependencies.get_tenant_by_id", return_value=tenant),
            patch(
                "omnibase.tenants.dependencies.get_session_factory",
                return_value=lambda: session,
            ),
        ):
            response = TestClient(app).get(
                "/tenant",
                headers={"Authorization": "Bearer token"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "schema_name": tenant.schema_name,
            "active_schema": tenant.schema_name,
        }
        assert get_current_schema() is None

    @pytest.mark.asyncio
    async def test_inactive_user_is_rejected_on_principal_resolution(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        payload = MagicMock(
            tenant_id=str(tenant.id),
            sub="11111111-1111-1111-1111-111111111111",
        )
        session = MagicMock()
        session.info = {}
        session.execute.return_value.scalar_one_or_none.return_value = None
        with (
            patch(
                "omnibase.tenants.dependencies._extract_access_payload",
                return_value=payload,
            ),
            patch("omnibase.tenants.dependencies.get_tenant_by_id", return_value=tenant),
            patch(
                "omnibase.tenants.dependencies.get_session_factory",
                return_value=lambda: session,
            ),
        ):
            dependency = get_current_principal("Bearer token")
            with pytest.raises(HTTPException) as raised:
                await anext(dependency)
        assert raised.value.status_code == 401

    def test_tenant_admin_requirement_uses_current_database_role(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        user = User(
            id="11111111-1111-1111-1111-111111111111",
            email="admin@example.com",
            password_hash="hash",  # noqa: S106 - inert ORM fixture value
            is_active=True,
            is_tenant_admin=False,
        )
        ctx = TenantContext(tenant=tenant, user=user)
        with pytest.raises(HTTPException) as raised:
            require_tenant_admin(ctx)
        assert raised.value.status_code == 403

        user.is_tenant_admin = True
        assert require_tenant_admin(ctx) is ctx

    def test_token_schema_claim_is_not_used_for_database_selection(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        user = User(
            id="11111111-1111-1111-1111-111111111111",
            email="user@example.com",
            password_hash="hash",  # noqa: S106 - inert ORM fixture value
            is_active=True,
            is_tenant_admin=False,
        )
        token = MagicMock(schema_name="tenant_deadbeef")
        principal = CurrentPrincipal(tenant=tenant, user=user, token=token)
        assert principal.schema_name == tenant.schema_name
        assert principal.schema_name != token.schema_name

    def test_tenant_db_marks_session_as_context_required(self) -> None:
        tenant = Tenant(
            id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            name="Acme",
            slug="acme",
            schema_name="tenant_a1b2c3d4",
            is_active=True,
            is_default=False,
        )
        session = MagicMock()
        session.info = {}
        with patch(
            "omnibase.tenants.dependencies.get_session_factory", return_value=lambda: session
        ):
            dependency = get_tenant_db(TenantContext(tenant=tenant))
            assert next(dependency) is session
            assert session.info[TENANT_SCHEMA_SESSION_KEY] == tenant.schema_name
            assert session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] is True
            dependency.close()
        session.close.assert_called_once()

    def test_checkout_always_resets_baseline_and_commits(self) -> None:
        engine = create_engine("sqlite://")
        captured: dict[str, object] = {}

        def capture_listener(target: object, name: str):
            def decorator(fn: object) -> object:
                captured[name] = fn
                return fn

            return decorator

        with patch.object(db_module.event, "listens_for", side_effect=capture_listener):
            db_module._install_search_path_hook(engine)

        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        captured["checkout"](connection, object(), object())  # type: ignore[operator]
        cursor.execute.assert_called_once_with("SET search_path TO omnibase_meta, public")
        connection.commit.assert_called_once()

    def test_after_begin_uses_set_local_and_fails_closed(self) -> None:
        engine = create_engine("sqlite://")
        captured: dict[str, object] = {}

        def capture_listener(target: object, name: str):
            def decorator(fn: object) -> object:
                captured[name] = fn
                return fn

            return decorator

        with patch.object(db_module.event, "listens_for", side_effect=capture_listener):
            db_module._install_search_path_hook(engine)

        session = MagicMock()
        session.info = {
            TENANT_SCHEMA_SESSION_KEY: "tenant_deadbeef",
            TENANT_CONTEXT_REQUIRED_SESSION_KEY: True,
        }
        transaction = MagicMock()
        transaction.parent = None
        connection = MagicMock()
        connection.engine = engine
        with tenant_scope("tenant_deadbeef"):
            captured["after_begin"](session, transaction, connection)  # type: ignore[operator]
        statement = str(connection.execute.call_args.args[0])
        assert statement == 'SET LOCAL search_path TO "tenant_deadbeef", omnibase_meta, public'

        with pytest.raises(RuntimeError, match="requires an active tenant context"):
            captured["after_begin"](session, transaction, connection)  # type: ignore[operator]

        connection.execute.side_effect = RuntimeError("SET failed")
        with tenant_scope("tenant_deadbeef"), pytest.raises(RuntimeError, match="SET failed"):
            captured["after_begin"](session, transaction, connection)  # type: ignore[operator]

    def test_set_search_path_is_transaction_local_and_rejects_rebinding(self) -> None:
        session = MagicMock()
        session.info = {}
        session.in_transaction.return_value = True
        set_search_path(session, "tenant_deadbeef")
        assert str(session.execute.call_args.args[0]).startswith("SET LOCAL search_path")
        with pytest.raises(SchemaError, match="different tenant"):
            set_search_path(session, "tenant_c0ffee99")

    def test_tenant_session_honors_supplied_engine(self) -> None:
        engine = create_engine("sqlite://")
        with TenantSession(engine, "tenant_deadbeef") as session:
            assert session.get_bind() is engine
            assert session.info[TENANT_SCHEMA_SESSION_KEY] == "tenant_deadbeef"


class TestSchemaSafety:
    def test_drop_schema_defaults_to_restrict(self) -> None:
        engine = MagicMock()
        connection = MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        drop_schema(engine, "tenant_deadbeef")
        assert str(connection.execute.call_args.args[0]) == 'DROP SCHEMA "tenant_deadbeef" RESTRICT'

    def test_drop_schema_expected_name_guard(self) -> None:
        with pytest.raises(SchemaError, match="does not match"):
            drop_schema(
                MagicMock(),
                "tenant_deadbeef",
                cascade=True,
                expected_schema_name="tenant_c0ffee99",
            )


class TestTenantProvisioning:
    def test_create_tenant_uses_session_connection_and_no_orphan_reuse(self) -> None:
        session = MagicMock()
        connection = session.connection.return_value
        with (
            patch("omnibase.tenants.service.make_schema_name", return_value="tenant_deadbeef"),
            patch("omnibase.tenants.service.create_schema") as create_schema_mock,
            patch("omnibase.tenants.service._initialize_tenant_schema") as initialize_mock,
        ):
            tenant = create_tenant(name="Acme", slug="acme", session=session)

        create_schema_mock.assert_called_once_with(
            connection,
            "tenant_deadbeef",
            if_not_exists=False,
        )
        initialize_mock.assert_called_once_with(connection, "tenant_deadbeef")
        session.commit.assert_not_called()
        assert tenant.schema_name == "tenant_deadbeef"


class TestSlugValidation:
    """validate_slug / _generate_unique_slug."""

    def test_validate_slug_accepts_valid(self) -> None:
        """Valid slugs pass."""
        for valid in ["acme", "acme-corp", "my-tenant-123", "a1b2"]:
            validate_slug(valid)  # no raise

    @pytest.mark.parametrize(
        "invalid",
        [
            "",  # empty
            "ab",  # < 3 chars
            "Acme",  # uppercase
            "1acme",  # starts with digit
            "-acme",  # starts with hyphen
            "acme_corp",  # underscore not allowed
            "acme.corp",  # dot not allowed
            "a" * 51,  # > 50 chars
        ],
    )
    def test_validate_slug_rejects_invalid(self, invalid: str) -> None:
        """Invalid slugs raise InvalidTenantSlug."""
        with pytest.raises(InvalidTenantSlug):
            validate_slug(invalid)

    def test_generate_unique_slug_from_name(self) -> None:
        """Display name is converted to URL-safe slug with random suffix."""
        slug = _generate_unique_slug("Acme Corp")
        assert slug.startswith("acme-corp-")
        # 4-char hex suffix
        suffix = slug.removeprefix("acme-corp-")
        assert len(suffix) == 4
        int(suffix, 16)  # parses as hex

    def test_generate_unique_slug_normalizes_special_chars(self) -> None:
        """Special chars collapse to single hyphen."""
        slug = _generate_unique_slug("ACME!!! Corp@@@")
        assert slug.startswith("acme-corp-")

    def test_generate_unique_slug_handles_non_alpha_start(self) -> None:
        """Name starting with non-alpha falls back to 'tenant' base."""
        slug = _generate_unique_slug("123 Numbers")
        assert slug.startswith("tenant-") or slug.startswith("numbers-")

    def test_generate_unique_slug_truncates_long_name(self) -> None:
        """Long names are truncated to leave room for the suffix."""
        long_name = "a" * 200
        slug = _generate_unique_slug(long_name)
        # base (30 max) + "-" + 4 hex suffix = 35 max
        assert len(slug) <= 35

    def test_generate_unique_slug_randomness(self) -> None:
        """Two calls produce different slugs (random suffix)."""
        slug1 = _generate_unique_slug("Acme")
        slug2 = _generate_unique_slug("Acme")
        assert slug1 != slug2
