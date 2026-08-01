"""Integration test configuration with fail-closed destructive DB guards.

Integration tests run only when both ``OMNIBASE_INTEGRATION_TESTS=1`` and an
explicit ``TEST_DATABASE_URL`` are present. Before any fixture may mutate the
database, the live connection must prove that it targets a specially named,
sentinel-marked database through a restricted non-owner role.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.cleanup import (
    DestructiveTestSafetyError,
    TenantResource,
    delete_exact_tenant,
    require_destructive_test_environment,
    verify_sqlalchemy_connection,
)


@dataclass
class RunOwnedResources:
    """Exact database resources created during the current integration run."""

    tenants: dict[str, TenantResource] = field(default_factory=dict)

    def add(self, tenant_id: str, schema_name: str) -> None:
        self.tenants[tenant_id] = TenantResource(tenant_id=tenant_id, schema_name=schema_name)


@pytest.fixture(scope="session", autouse=True)
def integration_test_environment() -> Iterator[str]:
    """Require both explicit opt-ins; importing this module never cleans."""
    if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "Integration tests require OMNIBASE_INTEGRATION_TESTS=1",
            allow_module_level=True,
        )
    try:
        test_database_url = require_destructive_test_environment()
    except DestructiveTestSafetyError as exc:
        pytest.fail(str(exc))

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_database_url

    from omnibase.core.config import get_settings
    from omnibase.core.db import dispose_engines

    dispose_engines()
    get_settings.cache_clear()
    yield test_database_url
    dispose_engines()
    get_settings.cache_clear()

    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url


@pytest.fixture(scope="session")
def db_settings(integration_test_environment: str) -> Any:
    """Settings bound only to the explicit integration-test database URL."""
    from omnibase.core.config import get_settings

    settings = get_settings()
    if str(settings.database_url) != integration_test_environment:
        pytest.fail("DATABASE_URL did not resolve to the explicit TEST_DATABASE_URL")
    return settings


@pytest.fixture(scope="session")
def db_engine(db_settings: Any) -> Iterator[Any]:
    """Guard and yield a session engine connected through the restricted role."""
    from omnibase.core.db import get_engine

    engine = get_engine(db_settings)
    try:
        with engine.connect() as connection:
            verify_sqlalchemy_connection(connection)
    except Exception:
        engine.dispose()
        raise

    yield engine
    engine.dispose()


@pytest.fixture
def run_owned_resources(db_engine: Any) -> Iterator[RunOwnedResources]:
    """Track and remove only exact tenant resources owned by one test."""
    resources = RunOwnedResources()
    yield resources

    with db_engine.begin() as connection:
        verify_sqlalchemy_connection(connection)
        for resource in reversed(tuple(resources.tenants.values())):
            # Successful controlled-data tests emit immutable audit rows.  In
            # that case the exact tenant must remain until this suite's entire
            # sentinel database is destroyed by isolated Compose teardown.
            delete_exact_tenant(connection, resource, retain_if_audited=True)


@pytest.fixture
def clean_db(
    db_engine: Any,
    run_owned_resources: RunOwnedResources,
    tenant_slug: str,
) -> Iterator[RunOwnedResources]:
    """Track tenants carrying this test's unguessable slug marker, then clean exactly those."""
    yield run_owned_resources

    from sqlalchemy import text

    with db_engine.connect() as connection:
        verify_sqlalchemy_connection(connection)
        rows = connection.execute(
            text(
                "SELECT id, schema_name FROM omnibase_meta.tenants "
                "WHERE slug LIKE :owned_slug ESCAPE '!'"
            ),
            {"owned_slug": f"{tenant_slug}!-%"},
        )
        for tenant_id, schema_name in rows:
            run_owned_resources.add(str(tenant_id), schema_name)


@pytest.fixture
def tenant_slug() -> str:
    """Unique slug for each test (avoids cross-test collisions)."""
    return f"int-{secrets.token_hex(3)}"


pytestmark = pytest.mark.integration
