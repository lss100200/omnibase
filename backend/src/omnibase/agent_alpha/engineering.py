"""Engineering-only activation for the tool-free Agent Alpha composition.

The Alpha composition stays ``UnavailableAgentAlpha`` (HTTP 503
``agent_alpha_unavailable``) unless **every** engineering condition holds:

- ``AGENT_ALPHA_ENGINEERING_ENABLED`` is exactly ``true`` (missing/empty/
  ``false`` disable; any other token is a configuration error, never silently
  accepted through loose bool coercion);
- the process environment is exactly ``development`` (production and any
  unknown environment always reject);
- all three Phase 5 Feature Gates remain exactly ``false`` (an enabled Alpha
  must never flip or imply any other gate);
- a provider-configured Model Gateway is available;
- the database reports migration head ``0015``.

The seam performs no work at module import time: no database connection, no
provider connection, no migration, no background thread, no event-loop task,
no network request and no root ``.env`` read.  It builds the DB-backed service
only when a request asks for the dependency.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from omnibase.agent_alpha.adapters import (
    LedgerInvocationAdapter,
    RagKnowledgeRetriever,
    RegistryProfileResolver,
)
from omnibase.agent_alpha.service import AgentAlphaService, UnavailableAgentAlpha
from omnibase.core.config import Settings, get_settings
from omnibase.core.db import get_session_factory
from omnibase.model_gateway import ModelGateway, UnavailableModelGateway
from omnibase.model_gateway.service import configured_model_gateway
from omnibase.user_settings.gateway import UserModelGatewayResolver

AGENT_ALPHA_ENGINEERING_FLAG = "AGENT_ALPHA_ENGINEERING_ENABLED"
_ALLOWED_ENGINEERING_ENVIRONMENTS = frozenset({"development"})
_EXPECTED_MIGRATION_HEAD = "0015"
_PHASE5_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


class EngineeringAlphaConfigurationError(RuntimeError):
    """Stable configuration failure; never a leaked truthy token."""


def resolve_engineering_alpha_flag(raw: str | None) -> bool:
    """Closed-set parse of the engineering flag: exactly true/false, else error."""
    if raw is None or raw == "":
        return False
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise EngineeringAlphaConfigurationError(
        "agent_alpha_engineering_flag_invalid: expected exactly true or false"
    )


def _phase5_gates_are_false() -> bool:
    all_false = True
    for name in _PHASE5_GATE_ENV_NAMES:
        raw = os.environ.get(name)
        if raw is None or raw == "" or raw == "false":
            continue
        if raw == "true":
            all_false = False
            continue
        raise EngineeringAlphaConfigurationError(
            f"phase5_feature_gate_invalid: {name} must be exactly true or false"
        )
    return all_false


def _migration_head(factory: sessionmaker[Any], settings: Settings) -> str | None:
    try:
        session = factory()
    except Exception:
        return None
    try:
        row = session.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one_or_none()
        return str(row) if row is not None else None
    except Exception:
        return None
    finally:
        session.close()


def build_engineering_agent_alpha(
    *,
    flag: str | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Any] | None = None,
    gateway: ModelGateway | UnavailableModelGateway | None = None,
    expected_migration_head: str = _EXPECTED_MIGRATION_HEAD,
) -> AgentAlphaService | UnavailableAgentAlpha:
    """Composition seam: only exact engineering conditions produce the service.

    Every failure returns the fail-closed ``UnavailableAgentAlpha`` (the
    Browser API then answers ``503 agent_alpha_unavailable``); no partial
    service is ever assembled.
    """
    if flag is None:
        flag = os.environ.get(AGENT_ALPHA_ENGINEERING_FLAG)
    enabled = resolve_engineering_alpha_flag(flag)
    if not enabled:
        return UnavailableAgentAlpha()

    settings = settings or get_settings()
    if settings.env.value not in _ALLOWED_ENGINEERING_ENVIRONMENTS:
        return UnavailableAgentAlpha()

    if not _phase5_gates_are_false():
        return UnavailableAgentAlpha()

    if gateway is None:
        gateway = configured_model_gateway()
    if isinstance(gateway, UnavailableModelGateway):
        return UnavailableAgentAlpha()

    factory = session_factory or get_session_factory(settings)
    head = _migration_head(factory, settings)
    if head != expected_migration_head:
        return UnavailableAgentAlpha()

    personal_gateway = UserModelGatewayResolver(
        factory,
        settings=settings,
        operator_gateway=gateway,
    )

    return AgentAlphaService(
        profiles=RegistryProfileResolver(factory),
        knowledge=RagKnowledgeRetriever(factory),
        ledger=LedgerInvocationAdapter(factory),
        gateway=gateway,
        gateway_resolver=personal_gateway,
        preferences_resolver=personal_gateway,
    )


def engineering_alpha_status(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Any] | None = None,
    gateway: ModelGateway | UnavailableModelGateway | None = None,
) -> dict[str, bool]:
    """Read-only engineering posture for the status endpoint."""
    enabled = resolve_engineering_alpha_flag(os.environ.get(AGENT_ALPHA_ENGINEERING_FLAG))
    settings = settings or get_settings()
    environment_allowed = settings.env.value in _ALLOWED_ENGINEERING_ENVIRONMENTS
    phase5_gates_all_false = _phase5_gates_are_false()
    gateway = gateway or configured_model_gateway()
    gateway_configured = not isinstance(gateway, UnavailableModelGateway)
    migration_ready = False
    if enabled and environment_allowed and phase5_gates_all_false and gateway_configured:
        factory = session_factory or get_session_factory(settings)
        migration_ready = _migration_head(factory, settings) == _EXPECTED_MIGRATION_HEAD
    return {
        "engineering_flag_enabled": enabled,
        "environment_allowed": environment_allowed,
        "phase5_gates_all_false": phase5_gates_all_false,
        "assembled": bool(
            enabled
            and environment_allowed
            and phase5_gates_all_false
            and gateway_configured
            and migration_ready
        ),
    }


__all__ = [
    "AGENT_ALPHA_ENGINEERING_FLAG",
    "EngineeringAlphaConfigurationError",
    "build_engineering_agent_alpha",
    "engineering_alpha_status",
    "resolve_engineering_alpha_flag",
]
