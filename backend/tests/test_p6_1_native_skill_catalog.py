"""P6.1 native Skill catalog and Browser contract tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnibase.agent_skills.native_catalog import (
    get_native_skill,
    list_native_skills,
    materialize_native_skill,
)
from omnibase.agent_skills.router import router
from omnibase.main import create_app
from omnibase.tenants.dependencies import CurrentPrincipal, get_current_principal


def _principal() -> CurrentPrincipal:
    class Tenant:
        id = "00000000-0000-0000-0000-000000000001"
        schema_name = "tenant_00000000"

    class User:
        id = "00000000-0000-0000-0000-000000000002"

    return CurrentPrincipal(tenant=Tenant(), user=User(), token=None)  # type: ignore[arg-type]


def test_native_catalog_is_a_closed_first_party_instruction_set() -> None:
    items = list_native_skills()
    assert 3 <= len(items) <= 15
    keys = [item.definition.stable_logical_key for item in items]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    for item in items:
        assert item.definition.first_party is True
        assert item.definition.allowed_installation_scopes == ("workspace",)
        assert item.version.kind.value == "instruction"
        assert item.version.required_tool_ids == ()
        assert item.version.capability_requirements == ()
        assert item.version.network_policy == "deny"
        assert item.version.secrets_allowed is False
        assert item.version.budget.max_tool_calls == 0
        assert len(item.version.canonical_digest()) == 64


def test_native_catalog_lookup_is_exact_and_never_falls_back() -> None:
    assert get_native_skill("omnibase.change-reviewer").definition.display_name == "变更审阅员"
    for value in (
        "OMNIBASE.CHANGE-REVIEWER",
        "omnibase.change-reviewer/../x",
        "change-reviewer",
        "",
    ):
        with pytest.raises(KeyError, match="native_skill_not_found"):
            get_native_skill(value)


def test_catalog_items_contain_no_authority_smuggling() -> None:
    forbidden = (
        "read .env",
        "execute shell",
        "arbitrary http",
        "ignore security",
        "bypass",
    )
    for item in list_native_skills():
        lowered = item.version.instructions.lower()
        assert all(marker not in lowered for marker in forbidden)


def test_native_database_identity_is_deterministic_and_tenant_scoped() -> None:
    item = get_native_skill("omnibase.change-reviewer")
    tenant_a = materialize_native_skill(item, tenant_id="00000000-0000-0000-0000-000000000001")
    tenant_a_replay = materialize_native_skill(
        item, tenant_id="00000000-0000-0000-0000-000000000001"
    )
    tenant_b = materialize_native_skill(item, tenant_id="00000000-0000-0000-0000-000000000002")

    assert tenant_a == tenant_a_replay
    assert tenant_a.definition.skill_definition_id != tenant_b.definition.skill_definition_id
    assert tenant_a.version.skill_version_id != tenant_b.version.skill_version_id
    assert tenant_a.version.skill_definition_id == tenant_a.definition.skill_definition_id
    assert tenant_a.version.canonical_digest() != tenant_b.version.canonical_digest()


def test_browser_catalog_is_authenticated_and_does_not_leak_instructions_in_list() -> None:
    app = create_app()
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/skills").status_code == 401
    app.dependency_overrides[get_current_principal] = _principal
    with TestClient(app) as client:
        response = client.get("/api/v1/skills")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == len(list_native_skills())
        assert all("instructions" not in item for item in payload["items"])
        detail = client.get("/api/v1/skills/omnibase.change-reviewer")
        assert detail.status_code == 200
        assert detail.json()["instructions"]
        assert client.get("/api/v1/skills/unknown").status_code == 404


def test_main_mounts_exact_native_skill_router() -> None:
    paths = {route.path for route in router.routes}
    assert paths == {
        "/skills",
        "/skills/{stable_key}",
        "/skills/{stable_key}/install",
        "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations",
        "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations/{installation_id}/disable",
    }
