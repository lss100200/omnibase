"""P6.1 native Skill catalog and Browser contract tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omnibase.agent_skills.control import (
    NativeSkillControlError,
    _assert_definition_matches_catalog,
    _assert_version_matches_catalog,
)
from omnibase.agent_skills.native_catalog import (
    filter_native_skills,
    get_native_skill,
    list_native_skills,
    materialize_native_skill,
    native_skill_catalog_digest,
    native_skill_categories,
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
    assert len(items) == 15
    keys = [item.definition.stable_logical_key for item in items]
    assert keys == [
        "omnibase.api-contract-reviewer",
        "omnibase.bug-triager",
        "omnibase.change-reviewer",
        "omnibase.context-curator",
        "omnibase.data-change-planner",
        "omnibase.dependency-risk-reviewer",
        "omnibase.documentation-maintainer",
        "omnibase.evidence-first-researcher",
        "omnibase.observability-planner",
        "omnibase.performance-budget-reviewer",
        "omnibase.personal-security-checker",
        "omnibase.release-checklist",
        "omnibase.requirement-clarifier",
        "omnibase.test-strategist",
        "omnibase.ux-accessibility-reviewer",
    ]
    assert len(keys) == len(set(keys))
    assert len({item.definition.skill_definition_id for item in items}) == 15
    assert len({item.version.skill_version_id for item in items}) == 15
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
        assert 2 <= len(item.tags) <= 5
        assert tuple(sorted(set(item.tags))) == item.tags
        assert 1 <= len(item.recommended_roles) <= 4
        assert item.instructions_bytes == len(item.version.instructions.encode("utf-8"))


def test_catalog_digest_filters_and_categories_are_stable_and_bounded() -> None:
    assert native_skill_catalog_digest() == native_skill_catalog_digest()
    assert len(native_skill_catalog_digest()) == 64
    assert native_skill_categories() == tuple(sorted(native_skill_categories()))
    assert [item.definition.stable_logical_key for item in filter_native_skills(role="ux")] == [
        "omnibase.ux-accessibility-reviewer"
    ]
    assert {
        item.definition.stable_logical_key for item in filter_native_skills(category="engineering")
    } == {"omnibase.bug-triager", "omnibase.change-reviewer"}
    assert [item.definition.stable_logical_key for item in filter_native_skills(q="数据结构")] == [
        "omnibase.data-change-planner"
    ]
    with pytest.raises(ValueError, match="native_skill_query_invalid"):
        filter_native_skills(q="   ")
    with pytest.raises(ValueError, match="native_skill_category_invalid"):
        filter_native_skills(category="marketplace")
    with pytest.raises(ValueError, match="native_skill_role_invalid"):
        filter_native_skills(role="autonomous-worker")


def test_catalog_lookups_return_detached_nested_snapshots() -> None:
    before = native_skill_catalog_digest()
    item = get_native_skill("omnibase.requirement-clarifier")

    item.version.input_schema["additionalProperties"] = True

    assert native_skill_catalog_digest() == before
    assert get_native_skill("omnibase.requirement-clarifier").version.input_schema == {
        "type": "object",
        "additionalProperties": False,
    }


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


def test_materialized_database_rows_must_exactly_match_the_source_catalog() -> None:
    tenant_id = "00000000-0000-0000-0000-000000000001"
    owner_user_id = "00000000-0000-0000-0000-000000000002"
    item = materialize_native_skill(
        get_native_skill("omnibase.change-reviewer"), tenant_id=tenant_id
    )
    definition = SimpleNamespace(
        id=item.definition.skill_definition_id,
        tenant_id=tenant_id,
        stable_logical_key=item.definition.stable_logical_key,
        display_name=item.definition.display_name,
        description=item.definition.description,
        definition_state="active",
        installation_scopes=["workspace"],
        first_party=True,
        created_by=owner_user_id,
    )
    version = SimpleNamespace(
        id=item.version.skill_version_id,
        tenant_id=tenant_id,
        definition_id=item.version.skill_definition_id,
        semantic_version=item.version.version,
        version_state="sealed",
        kind="instruction",
        manifest_payload=item.version.to_dict(),
        manifest_digest=item.version.canonical_digest(),
        instructions=item.version.instructions,
        instructions_digest=item.version.instructions_digest,
        required_tool_ids=[],
        capability_requirements=[],
        network_policy="deny",
        secrets_allowed=False,
        max_tool_calls=0,
        rollback_version_id=item.version.rollback_version_id,
        created_by=owner_user_id,
    )
    _assert_definition_matches_catalog(
        definition, item, tenant_id=tenant_id, owner_user_id=owner_user_id
    )
    _assert_version_matches_catalog(version, item, tenant_id=tenant_id, owner_user_id=owner_user_id)
    definition.display_name = "drifted"
    with pytest.raises(NativeSkillControlError, match="native_skill_definition_catalog_drifted"):
        _assert_definition_matches_catalog(
            definition, item, tenant_id=tenant_id, owner_user_id=owner_user_id
        )
    definition.display_name = item.definition.display_name
    version.instructions_digest = "0" * 64
    with pytest.raises(NativeSkillControlError, match="native_skill_version_catalog_drifted"):
        _assert_version_matches_catalog(
            version, item, tenant_id=tenant_id, owner_user_id=owner_user_id
        )


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
        assert payload["catalog_total"] == 15
        assert payload["schema_version"] == 1
        assert payload["catalog_digest"] == native_skill_catalog_digest()
        assert payload["categories"] == list(native_skill_categories())
        assert all("instructions" not in item for item in payload["items"])
        assert all(item["tags"] and item["recommended_roles"] for item in payload["items"])
        detail = client.get("/api/v1/skills/omnibase.change-reviewer")
        assert detail.status_code == 200
        assert detail.json()["instructions"]
        assert client.get("/api/v1/skills/unknown").status_code == 404

        filtered = client.get("/api/v1/skills", params={"category": "engineering", "role": "qa"})
        assert filtered.status_code == 200
        assert [item["stable_logical_key"] for item in filtered.json()["items"]] == [
            "omnibase.bug-triager"
        ]
        assert filtered.json()["catalog_total"] == 15
        assert client.get("/api/v1/skills", params={"q": " "}).status_code == 422
        assert client.get("/api/v1/skills", params={"role": "worker"}).status_code == 422


def test_main_mounts_exact_native_skill_router() -> None:
    paths = {route.path for route in router.routes}
    assert paths == {
        "/skills",
        "/skills/{stable_key}",
        "/skills/{stable_key}/install",
        "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations",
        "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations/{installation_id}/disable",
    }
