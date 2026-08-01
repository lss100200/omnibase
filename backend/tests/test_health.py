"""Unit tests for the liveness endpoint.

The liveness endpoint is the simplest possible test target - it doesn't
touch any external resources, so it's safe to run without docker compose.

Tests for /health/ready require the full stack and live in
tests/integration/ (added in B7).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from omnibase import __version__
from omnibase.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to a fresh app instance."""
    # NOTE: we use create_app() per-test to get isolation. The lifespan
    # startup hook is currently tolerant of missing DB/MinIO/Redis in dev,
    # so this works without docker compose running.
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


def response_json(response: Any) -> dict[str, Any]:
    """Helper: assert 200 and return JSON."""
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    return response.json()


class TestLiveness:
    """Liveness probe (GET /health)."""

    def test_returns_200(self, client: TestClient) -> None:
        """Liveness must return 200 unconditionally (process is alive)."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        """Response contains the documented fields."""
        data = response_json(client.get("/health"))
        assert data["status"] == "ok"
        assert data["version"] == __version__
        assert data["env"] in {"development", "staging", "production"}
        assert data["components"] == {}

    def test_versioned_api_prefix_works_too(self, client: TestClient) -> None:
        """Health is also reachable under the public versioned API prefix."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_unversioned_api_prefix_is_not_mounted(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 404

    def test_request_id_is_returned_and_safe_client_value_is_preserved(
        self, client: TestClient
    ) -> None:
        response = client.get("/health", headers={"X-Request-Id": "client-request_123"})
        assert response.headers["X-Request-Id"] == "client-request_123"

    def test_invalid_request_id_is_replaced(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-Id": "unsafe request id"})
        request_id = response.headers["X-Request-Id"]
        assert request_id != "unsafe request id"
        assert 1 <= len(request_id) <= 64

    def test_versioned_api_cors_preflight_uses_explicit_allowlists(
        self, client: TestClient
    ) -> None:
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-request-id",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "POST" in response.headers["access-control-allow-methods"]
        assert response.headers["X-Request-Id"]

    def test_unapproved_cors_origin_is_not_echoed(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/health",
            headers={"Origin": "https://untrusted.example"},
        )
        assert "access-control-allow-origin" not in response.headers

    def test_http_exception_headers_survive_error_normalization(self) -> None:
        app = create_app()

        @app.get("/_test/retry", include_in_schema=False)
        def retry_endpoint() -> None:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests; retry later",
                    }
                },
                headers={"Retry-After": "17"},
            )

        response = TestClient(app, raise_server_exceptions=False).get("/_test/retry")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "17"
        assert response.headers["X-Request-Id"]

    def test_root_banner(self, client: TestClient) -> None:
        """Root path returns app banner with doc links."""
        data = response_json(client.get("/"))
        assert data["name"]
        assert "/docs" in data["docs"]
        assert "/health" in data["health"]
