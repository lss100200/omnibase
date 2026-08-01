"""End-to-end auth integration test.

Requires the full docker compose stack running (postgres + minio + redis).
Run with:
    OMNIBASE_INTEGRATION_TESTS=1 pytest -m integration tests/integration/test_auth_e2e.py
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jose import jwt

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestAuthE2E:
    """Full register -> login -> /me flow against real DB."""

    def test_register_creates_tenant_and_user(self, clean_db: object, tenant_slug: str) -> None:
        """Registration creates a tenant + user and returns tokens."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        email = f"{tenant_slug}@example.com"
        response = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass123"},
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["access_token"]
        assert data["refresh_token"]
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email
        assert data["user"]["is_tenant_admin"] is True
        assert data["tenant"]["slug"]
        assert data["tenant"]["name"]

    def test_register_weak_password_rejected(self, clean_db: object, tenant_slug: str) -> None:
        """Weak passwords are rejected with 422 (Pydantic schema validation)."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{tenant_slug}@example.com",
                "password": "weak",  # too short, no digits
            },
        )
        # Pydantic schema (min_length=8) should reject before reaching service
        assert (
            response.status_code == 422
        ), f"Expected 422, got {response.status_code}. Body: {response.text}"

    def test_login_after_register_succeeds(self, clean_db: object, tenant_slug: str) -> None:
        """Login with registered credentials returns fresh tokens."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        email = f"{tenant_slug}@example.com"
        password = "StrongPass123"

        # Register
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert reg.status_code == 201

        # Login
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        data = login.json()
        assert data["access_token"]
        assert data["refresh_token"]

    def test_login_wrong_password_rejected(self, clean_db: object, tenant_slug: str) -> None:
        """Wrong password returns 401 (no user enumeration)."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        email = f"{tenant_slug}@example.com"
        # Register
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass123"},
        )
        # Login with wrong password
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword999"},
        )
        assert login.status_code == 401

    def test_me_with_valid_token(self, clean_db: object, tenant_slug: str) -> None:
        """/me returns user info when authed."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        email = f"{tenant_slug}@example.com"
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass123"},
        )
        token = reg.json()["access_token"]

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email

    def test_me_without_token_rejected(self, clean_db: object) -> None:
        """/me without Authorization header returns 401."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 401

    def test_inactive_tenant_cannot_login_or_refresh(
        self,
        clean_db: object,
        tenant_slug: str,
    ) -> None:
        """Deactivating a tenant prevents new login and refresh credentials."""
        from omnibase.core.config import get_settings
        from omnibase.main import create_app
        from omnibase.tenants.service import deactivate_tenant

        client = TestClient(create_app(), raise_server_exceptions=False)
        email = f"{tenant_slug}@example.com"
        password = "StrongPass123"
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        data = registered.json()

        settings = get_settings()
        claims = jwt.decode(
            data["access_token"],
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        deactivate_tenant(claims["tenant_id"])

        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 401, login.text

        refresh = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": data["refresh_token"]},
        )
        assert refresh.status_code == 401, refresh.text

    def test_refresh_uses_canonical_registry_schema(
        self,
        clean_db: object,
        tenant_slug: str,
    ) -> None:
        """A stale refresh-token schema claim is never propagated."""
        from omnibase.auth.security import create_token_pair
        from omnibase.core.config import get_settings
        from omnibase.main import create_app
        from omnibase.tenants.service import get_tenant_by_id

        client = TestClient(create_app(), raise_server_exceptions=False)
        email = f"{tenant_slug}@example.com"
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass123"},
        )
        assert registered.status_code == 201, registered.text

        settings = get_settings()
        original = jwt.decode(
            registered.json()["access_token"],
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        tenant = get_tenant_by_id(original["tenant_id"])
        _, stale_refresh, _, _ = create_token_pair(
            user_id=original["sub"],
            tenant_id=original["tenant_id"],
            schema_name="tenant_deadbeef",
            email=email,
            settings=settings,
        )

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": stale_refresh},
        )
        assert response.status_code == 200, response.text
        refreshed = jwt.decode(
            response.json()["access_token"],
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        assert refreshed["schema_name"] == tenant.schema_name
        assert refreshed["schema_name"] != "tenant_deadbeef"

    def test_refresh_returns_new_access_token(self, clean_db: object, tenant_slug: str) -> None:
        """Refresh endpoint exchanges refresh token for new access token."""
        from omnibase.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        email = f"{tenant_slug}@example.com"
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass123"},
        )
        refresh_token = reg.json()["refresh_token"]

        refresh = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh.status_code == 200, refresh.text
        assert refresh.json()["access_token"]
