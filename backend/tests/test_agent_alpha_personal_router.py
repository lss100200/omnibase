"""Router selection tests for the personal Runtime canary lane."""

from __future__ import annotations

from types import SimpleNamespace

from omnibase.agent_alpha.personal import PersonalAlphaPosture
from omnibase.agent_alpha.router import alpha_status, get_agent_alpha
from omnibase.agent_alpha.service import UnavailableAgentAlpha

WORKSPACE_ID = "00000000-0000-0000-0000-000000000102"
TENANT_ID = "00000000-0000-0000-0000-000000000101"
OWNER_ID = "00000000-0000-0000-0000-000000000103"


def _ctx(*, tenant_id: str = TENANT_ID, owner_id: str = OWNER_ID) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=tenant_id,
        schema_name="tenant_personal",
        user_id=owner_id,
    )


def _lite(*, enabled: bool = False) -> dict[str, object]:
    return {
        "activation_allowed": False,
        "engineering_composition_ready": enabled,
        "expected_migration_head": "0015",
        "formal_builder_integration": "proven_engineering_only",
        "lite_gate_enabled": enabled,
    }


def _engineering() -> dict[str, bool]:
    return {
        "assembled": False,
        "engineering_flag_enabled": False,
        "environment_allowed": False,
        "phase5_gates_all_false": True,
    }


def _personal_posture(*, assembled: bool = True) -> PersonalAlphaPosture:
    return PersonalAlphaPosture(
        profile_selected=True,
        feature_gates_valid=True,
        runtime_gate_enabled=True,
        planner_gate_enabled=False,
        multi_agent_gate_enabled=False,
        canary_state="active" if assembled else "inactive",
        canary_active=assembled,
        canary_id="00000000-0000-0000-0000-000000000100" if assembled else None,
        canary_expires_at="2026-08-10T16:00:00Z" if assembled else None,
        scope_matches=assembled,
        live_owner_verified=assembled,
        environment_allowed=True,
        gateway_configured=True,
        memory_crypto_configured=True,
        migration_ready=True,
        assembled=assembled,
        blockers=() if assembled else ("canary has not been activated",),
    )


def test_profile_absent_preserves_default_locked_behavior(monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_RUNTIME_PROFILE", raising=False)
    monkeypatch.setattr("omnibase.agent_alpha.router.runtime_lite_agent_enabled", lambda: False)
    personal_called = False

    def personal_builder(**_kwargs):
        nonlocal personal_called
        personal_called = True
        return object()

    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_personal_agent_alpha",
        personal_builder,
    )

    result = get_agent_alpha(WORKSPACE_ID, _ctx())

    assert isinstance(result, UnavailableAgentAlpha)
    assert personal_called is False


def test_profile_absent_preserves_engineering_lite_selection(monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_RUNTIME_PROFILE", raising=False)
    engineering = UnavailableAgentAlpha()
    monkeypatch.setattr("omnibase.agent_alpha.router.runtime_lite_agent_enabled", lambda: True)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_engineering_agent_alpha",
        lambda: engineering,
    )

    assert get_agent_alpha(WORKSPACE_ID, _ctx()) is engineering


def test_personal_profile_routes_exact_live_request_scope(monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_RUNTIME_PROFILE", "personal_single_owner")
    personal = UnavailableAgentAlpha()
    received: dict[str, str] = {}

    def personal_builder(**kwargs: str):
        received.update(kwargs)
        return personal

    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_personal_agent_alpha",
        personal_builder,
    )

    assert get_agent_alpha(WORKSPACE_ID, _ctx()) is personal
    assert received == {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "actor_user_id": OWNER_ID,
    }


def test_invalid_personal_profile_fails_closed_without_engineering_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_RUNTIME_PROFILE", "true")
    engineering_called = False

    def engineering_builder():
        nonlocal engineering_called
        engineering_called = True
        return object()

    monkeypatch.setattr("omnibase.agent_alpha.router.runtime_lite_agent_enabled", lambda: True)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_engineering_agent_alpha",
        engineering_builder,
    )

    result = get_agent_alpha(WORKSPACE_ID, _ctx())

    assert isinstance(result, UnavailableAgentAlpha)
    assert engineering_called is False


def test_status_discloses_only_the_exact_active_personal_posture(monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_RUNTIME_PROFILE", "personal_single_owner")
    monkeypatch.setattr("omnibase.agent_alpha.router.engineering_alpha_status", _engineering)
    monkeypatch.setattr("omnibase.agent_alpha.router.lite_agent_posture", _lite)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.personal_alpha_posture",
        lambda **_kwargs: _personal_posture(),
    )

    response = alpha_status(WORKSPACE_ID, _ctx())

    assert response.runtime_profile == "personal_single_owner"
    assert response.personal_runtime_state == "active"
    assert response.personal_runtime_active is True
    assert response.production_activation_allowed is True
    assert response.tools_enabled is False
    assert response.multi_agent_enabled is False
    assert response.supported_invocation_modes == ["no_tool"]


def test_invalid_profile_status_is_locked_and_vetoed(monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_RUNTIME_PROFILE", "production")
    monkeypatch.setattr("omnibase.agent_alpha.router.engineering_alpha_status", _engineering)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.lite_agent_posture",
        lambda: _lite(enabled=True),
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.personal_alpha_posture",
        lambda **_kwargs: _personal_posture(assembled=False),
    )

    response = alpha_status(WORKSPACE_ID, _ctx())

    assert response.runtime_profile == "locked"
    assert response.personal_runtime_state == "invalid/veto"
    assert response.personal_runtime_active is False
    assert response.production_activation_allowed is False
