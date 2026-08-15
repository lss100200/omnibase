"""Personal-edition production canary composition for tool-free Agent Alpha.

The production path is deliberately separate from the P5.2C engineering
builder.  It assembles only when all of the following independently hold:

* ``PERSONAL_RUNTIME_PROFILE`` is exactly ``personal_single_owner``;
* the server-owned Phase 5 gates resolve to Runtime=true, Planner=false and
  Multi-Agent=false;
* a canonical, exact-scope canary config and an ACTIVE, unexpired activation
  ledger are mounted at explicit absolute paths;
* the application environment is production, either the request-scoped
  personal Provider resolver or an operator Model Gateway is safely
  configured, and the database migration head is exactly ``0016``;
* the current request is the configured Tenant/Workspace/Owner and the live
  tenant schema still contains exactly that one active Owner, who is also an
  active tenant administrator.

The same live single-Owner check is injected into Ledger transaction A, so a
membership change between dependency construction and durable reservation
fails closed.  The canary remains no-tool, one Workspace, one AgentVersion,
one interactive Run at a time, with no Planner/Multi-Agent/Sandbox execution.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from omnibase.agent_alpha.adapters import (
    AlphaAdapterUnavailable,
    LedgerInvocationAdapter,
    RagKnowledgeRetriever,
    RegistryProfileResolver,
)
from omnibase.agent_alpha.contracts import AlphaAgentProfile, AlphaStreamEvent
from omnibase.agent_alpha.service import (
    AgentAlphaService,
    AgentAlphaUnavailable,
    UnavailableAgentAlpha,
)
from omnibase.agent_memory.compiler import SqlAlchemyMemoryCompiler
from omnibase.agent_memory.crypto import MemoryContentCipher, MemoryCryptoUnavailable
from omnibase.agent_skills.resolver import SqlAlchemySkillResolver
from omnibase.core.config import Environment, Settings, get_settings
from omnibase.core.db import get_session_factory
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.model_gateway import ModelGateway, UnavailableModelGateway
from omnibase.model_gateway.adaptation import ReasoningGear
from omnibase.model_gateway.service import configured_model_gateway
from omnibase.production.personal_runtime_activation import (
    PersonalRuntimeCanaryConfig,
    PersonalRuntimeConfigurationError,
    PersonalRuntimeState,
    load_personal_runtime_canary_config,
    personal_runtime_status_binding_valid,
    read_personal_runtime_status,
)
from omnibase.production.phase5_admission import (
    FeatureGateConfigurationError,
    resolve_feature_gates,
)
from omnibase.task_ledger.service import TaskAdmissionContext
from omnibase.tenants.schema_manager import set_search_path
from omnibase.user_settings.crypto import CredentialCipher, CredentialCryptoUnavailable
from omnibase.user_settings.gateway import UserModelGatewayResolver
from omnibase.workspaces.models import WorkspaceMembership, WorkspaceRun

_SAFE_PERSONAL_RUNTIME_CODE = re.compile(r"^personal_runtime_[a-z0-9_]{1,96}$")
PERSONAL_RUNTIME_PROFILE_ENV = "PERSONAL_RUNTIME_PROFILE"
PERSONAL_RUNTIME_CONFIG_ENV = "PERSONAL_RUNTIME_CANARY_CONFIG"
PERSONAL_RUNTIME_STATE_DIR_ENV = "PERSONAL_RUNTIME_STATE_DIR"
PERSONAL_RUNTIME_READINESS_ROOT_ENV = "PERSONAL_RUNTIME_READINESS_ROOT"
PERSONAL_RUNTIME_PROFILE = "personal_single_owner"
_EXPECTED_MIGRATION_HEAD = "0016"
_ACTIVE_WORKSPACE_RUN_STATES = frozenset(
    {"queued", "leased", "starting", "running", "pausing", "paused", "stopping"}
)


class PersonalAlphaConfigurationError(RuntimeError):
    """Stable configuration failure for the personal Runtime composition."""


@dataclass(frozen=True, slots=True)
class PersonalAlphaPosture:
    profile_selected: bool
    feature_gates_valid: bool
    runtime_gate_enabled: bool
    planner_gate_enabled: bool
    multi_agent_gate_enabled: bool
    canary_state: str
    canary_active: bool
    canary_id: str | None
    canary_expires_at: str | None
    scope_matches: bool
    live_owner_verified: bool
    environment_allowed: bool
    gateway_configured: bool
    memory_crypto_configured: bool
    migration_ready: bool
    assembled: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "assembled": self.assembled,
            "blockers": list(self.blockers),
            "canary_active": self.canary_active,
            "canary_expires_at": self.canary_expires_at,
            "canary_id": self.canary_id,
            "canary_state": self.canary_state,
            "environment_allowed": self.environment_allowed,
            "feature_gates_valid": self.feature_gates_valid,
            "gateway_configured": self.gateway_configured,
            "memory_crypto_configured": self.memory_crypto_configured,
            "live_owner_verified": self.live_owner_verified,
            "migration_ready": self.migration_ready,
            "multi_agent_gate_enabled": self.multi_agent_gate_enabled,
            "planner_gate_enabled": self.planner_gate_enabled,
            "profile": PERSONAL_RUNTIME_PROFILE,
            "profile_selected": self.profile_selected,
            "runtime_gate_enabled": self.runtime_gate_enabled,
            "scope_matches": self.scope_matches,
        }


def resolve_personal_runtime_profile(raw: str | None) -> bool:
    if raw is None or raw == "":
        return False
    if raw == PERSONAL_RUNTIME_PROFILE:
        return True
    raise PersonalAlphaConfigurationError(
        "personal_runtime_profile_invalid: expected personal_single_owner or empty"
    )


def _absolute_path(raw: str | None, *, name: str) -> Path:
    if raw is None or raw == "":
        raise PersonalAlphaConfigurationError(f"{name}_missing")
    path = Path(raw)
    if not path.is_absolute():
        raise PersonalAlphaConfigurationError(f"{name}_must_be_absolute")
    return path


def _absolute_directory(raw: str | None, *, name: str) -> Path:
    path = _absolute_path(raw, name=name)
    try:
        current = path
        while current != current.parent:
            metadata = os.lstat(current)
            is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            if os.path.islink(current) or is_reparse:
                raise PersonalAlphaConfigurationError(f"{name}_contains_link")
            current = current.parent
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PersonalAlphaConfigurationError(f"{name}_unavailable") from exc
    if not resolved.is_dir():
        raise PersonalAlphaConfigurationError(f"{name}_must_be_directory")
    return resolved


def _feature_gates(values: Mapping[str, object]) -> tuple[bool, bool, bool]:
    try:
        gates = resolve_feature_gates(values)
    except FeatureGateConfigurationError as exc:
        raise PersonalAlphaConfigurationError(
            f"personal_runtime_feature_gate_invalid: {exc}"
        ) from exc
    if not gates.agent_runtime_enabled or gates.agent_planner_enabled or gates.multi_agent_enabled:
        raise PersonalAlphaConfigurationError(
            "personal_runtime_requires_runtime_true_planner_false_multi_false"
        )
    return (
        gates.agent_runtime_enabled,
        gates.agent_planner_enabled,
        gates.multi_agent_enabled,
    )


def _migration_head(factory: sessionmaker[Any]) -> str | None:
    try:
        session = factory()
    except Exception:
        return None
    try:
        value = session.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one_or_none()
        return str(value) if value is not None else None
    except Exception:
        return None
    finally:
        session.close()


def _migration_head_in_session(session: Session) -> str | None:
    try:
        value = session.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one_or_none()
        return str(value) if value is not None else None
    except SQLAlchemyError:
        return None


def _database_now(session: Session) -> datetime:
    value = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    if not isinstance(value, datetime):
        raise PersonalAlphaConfigurationError("personal_runtime_database_clock_unavailable")
    return value


def _open_tenant_session(factory: sessionmaker[Any], tenant_id: str) -> Session:
    session = factory()
    try:
        schema_name = session.execute(
            select(Tenant.schema_name).where(
                Tenant.id == tenant_id,
                Tenant.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if not isinstance(schema_name, str):
            raise PersonalAlphaConfigurationError("personal_runtime_tenant_unavailable")
        set_search_path(session, schema_name)
        return session
    except Exception:
        session.rollback()
        session.close()
        raise


def _verify_live_single_owner(
    session: Session,
    *,
    config: PersonalRuntimeCanaryConfig,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    profile: AlphaAgentProfile | None = None,
    lock: bool,
) -> None:
    if (
        tenant_id != config.tenant_id
        or workspace_id != config.workspace_id
        or actor_user_id != config.owner_user_id
        or (profile is not None and profile.agent_version_id != config.agent_version_id)
    ):
        raise PersonalAlphaConfigurationError("personal_runtime_scope_mismatch")
    statement = select(WorkspaceMembership).where(
        WorkspaceMembership.tenant_id == tenant_id,
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.state == "active",
    )
    if lock:
        statement = statement.with_for_update()
    memberships = list(session.scalars(statement))
    if (
        len(memberships) != 1
        or memberships[0].role != "owner"
        or memberships[0].user_id != actor_user_id
    ):
        raise PersonalAlphaConfigurationError("personal_runtime_requires_exactly_one_live_owner")
    user_statement = select(User).where(User.id == actor_user_id)
    if lock:
        user_statement = user_statement.with_for_update()
    owner = session.execute(user_statement).scalar_one_or_none()
    if owner is None or not owner.is_active or not owner.is_tenant_admin:
        raise PersonalAlphaConfigurationError("personal_runtime_owner_not_live_admin")


def _verify_runtime_control_state(
    *,
    config: PersonalRuntimeCanaryConfig,
    config_path: Path,
    readiness_root: Path,
    state_path: Path,
    gate_values: Mapping[str, object],
    settings: Settings,
    now: datetime | None = None,
) -> None:
    if settings.env is not Environment.PRODUCTION:
        raise PersonalAlphaConfigurationError("personal_runtime_requires_production")
    live_config = load_personal_runtime_canary_config(
        config_path,
        repo_root=readiness_root,
        verify_owner_readiness=True,
    )
    if live_config.canonical_digest() != config.canonical_digest():
        raise PersonalAlphaConfigurationError("personal_runtime_config_drifted")
    _feature_gates(gate_values)
    status = read_personal_runtime_status(state_path, now=now)
    if not status.active:
        raise PersonalAlphaConfigurationError("personal_runtime_canary_not_active")
    if not personal_runtime_status_binding_valid(live_config, status):
        raise PersonalAlphaConfigurationError("personal_runtime_ledger_binding_drifted")


def _runtime_checkpoint(
    *,
    config: PersonalRuntimeCanaryConfig,
    config_path: Path,
    readiness_root: Path,
    state_path: Path,
    gate_values: Mapping[str, object],
    settings: Settings,
):
    def guard() -> None:
        try:
            _verify_runtime_control_state(
                config=config,
                config_path=config_path,
                readiness_root=readiness_root,
                state_path=state_path,
                gate_values=gate_values,
                settings=settings,
            )
        except (OSError, PersonalAlphaConfigurationError, PersonalRuntimeConfigurationError) as exc:
            raise AgentAlphaUnavailable("personal_runtime_control_state_unavailable") from exc

    return guard


def _invocation_guard_error_code(exc: BaseException) -> str:
    if isinstance(exc, PersonalAlphaConfigurationError):
        code = str(exc)
        if _SAFE_PERSONAL_RUNTIME_CODE.fullmatch(code) is not None:
            return code
        return "personal_runtime_invocation_guard_unavailable"
    if isinstance(exc, SQLAlchemyError):
        return "personal_runtime_database_guard_unavailable"
    return "personal_runtime_control_state_unavailable"


def _invocation_guard(
    config: PersonalRuntimeCanaryConfig,
    *,
    config_path: Path,
    readiness_root: Path,
    state_path: Path,
    gate_values: Mapping[str, object],
    settings: Settings,
):
    def guard(
        session: Session,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        profile: AlphaAgentProfile,
        context: TaskAdmissionContext,
        current_workspace_run_id: str | None,
    ) -> None:
        try:
            _verify_runtime_control_state(
                config=config,
                config_path=config_path,
                readiness_root=readiness_root,
                state_path=state_path,
                gate_values=gate_values,
                settings=settings,
                now=_database_now(session),
            )
            if _migration_head_in_session(session) != _EXPECTED_MIGRATION_HEAD:
                raise PersonalAlphaConfigurationError("personal_runtime_migration_drifted")
            if (
                context.tenant.id != config.tenant_id
                or context.workspace.id != config.workspace_id
                or context.actor.id != config.owner_user_id
                or context.actor_membership.user_id != config.owner_user_id
                or context.actor_membership.role != "owner"
                or context.actor_membership.state != "active"
                or context.binding.id != profile.workspace_agent_binding_id
                or context.definition.id != profile.agent_definition_id
                or context.version.id != config.agent_version_id
                or profile.agent_version_id != config.agent_version_id
            ):
                raise PersonalAlphaConfigurationError("personal_runtime_locked_scope_drifted")
            if not context.actor.is_active or not context.actor.is_tenant_admin:
                raise PersonalAlphaConfigurationError("personal_runtime_owner_not_live_admin")
            memberships = list(
                session.scalars(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.tenant_id == tenant_id,
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.state == "active",
                    )
                )
            )
            if (
                len(memberships) != 1
                or memberships[0].id != context.actor_membership.id
                or memberships[0].role != "owner"
                or memberships[0].user_id != actor_user_id
            ):
                raise PersonalAlphaConfigurationError(
                    "personal_runtime_requires_exactly_one_live_owner"
                )
            active_runs = tuple(
                session.scalars(
                    select(WorkspaceRun.id)
                    .where(
                        WorkspaceRun.tenant_id == tenant_id,
                        WorkspaceRun.workspace_id == workspace_id,
                        WorkspaceRun.observed_state.in_(_ACTIVE_WORKSPACE_RUN_STATES),
                    )
                    .with_for_update()
                )
            )
            unexpected_runs = tuple(
                run_id
                for run_id in active_runs
                if current_workspace_run_id is None or run_id != current_workspace_run_id
            )
            if unexpected_runs or len(active_runs) > 1:
                raise PersonalAlphaConfigurationError("personal_runtime_invocation_slot_occupied")
            if current_workspace_run_id is not None and active_runs != (current_workspace_run_id,):
                raise PersonalAlphaConfigurationError("personal_runtime_invocation_slot_drifted")
        except (
            OSError,
            PersonalAlphaConfigurationError,
            PersonalRuntimeConfigurationError,
            SQLAlchemyError,
        ) as exc:
            raise AlphaAdapterUnavailable(_invocation_guard_error_code(exc)) from exc

    return guard


class PersonalCanaryAgentAlpha:
    """Exact-scope facade over the existing tool-free Alpha service."""

    def __init__(self, delegate: AgentAlphaService, config: PersonalRuntimeCanaryConfig) -> None:
        self._delegate = delegate
        self._config = config

    def _scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> None:
        if (
            tenant_id != self._config.tenant_id
            or workspace_id != self._config.workspace_id
            or actor_user_id != self._config.owner_user_id
        ):
            raise AgentAlphaUnavailable("personal_runtime_scope_mismatch")

    def list_profiles(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> tuple[AlphaAgentProfile, ...]:
        self._scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        profiles = self._delegate.list_profiles(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        return tuple(
            profile
            for profile in profiles
            if profile.agent_version_id == self._config.agent_version_id
        )

    def invoke(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        workspace_id: str,
        actor_user_id: str,
        agent_version_id: str,
        message: str,
        top_k: int,
        reasoning_gear: ReasoningGear = "standard",
        idempotency_key: str,
        retry_of: str | None,
        employee_role_id: str = "parent",
    ) -> Iterator[AlphaStreamEvent]:
        self._scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        if agent_version_id != self._config.agent_version_id:
            raise AgentAlphaUnavailable("personal_runtime_agent_version_mismatch")
        if top_k > self._config.max_top_k:
            raise AgentAlphaUnavailable("personal_runtime_top_k_exceeded")
        return self._delegate.invoke(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_version_id=agent_version_id,
            message=message,
            top_k=top_k,
            reasoning_gear=reasoning_gear,
            idempotency_key=idempotency_key,
            retry_of=retry_of,
            employee_role_id=employee_role_id,
        )

    def cancel(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        invocation_id: str,
    ) -> bool:
        self._scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        return self._delegate.cancel(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            invocation_id=invocation_id,
        )


def _load_runtime_inputs(
    *,
    profile: str | None,
    config_path: str | None,
    state_dir: str | None,
    readiness_root: str | None,
) -> tuple[PersonalRuntimeCanaryConfig, Path, Path, Path]:
    if not resolve_personal_runtime_profile(profile):
        raise PersonalAlphaConfigurationError("personal_runtime_profile_not_selected")
    config_file = _absolute_path(config_path, name="personal_runtime_canary_config")
    state_path = _absolute_path(state_dir, name="personal_runtime_state_dir")
    readiness_path = _absolute_directory(
        readiness_root,
        name="personal_runtime_readiness_root",
    )
    try:
        config = load_personal_runtime_canary_config(
            config_file,
            repo_root=readiness_path,
            verify_owner_readiness=True,
        )
    except (OSError, PersonalRuntimeConfigurationError) as exc:
        raise PersonalAlphaConfigurationError(f"personal_runtime_config_invalid: {exc}") from exc
    return config, config_file, state_path, readiness_path


def _record_configuration_failure(
    blockers: list[str],
    exc: PersonalAlphaConfigurationError | PersonalRuntimeConfigurationError,
) -> None:
    message = str(exc)
    if message not in {"profile_not_selected", "personal_runtime_profile_not_selected"}:
        blockers.append(message)


def _memory_crypto_available(settings: Settings) -> bool:
    try:
        MemoryContentCipher.from_settings(settings)
    except MemoryCryptoUnavailable:
        return False
    return True


def _personal_gateway_resolver_available(settings: Settings) -> bool:
    """Prove the request-scoped credential path can assemble without reading a key."""

    if not settings.provider_endpoint_allowlist:
        return False
    try:
        CredentialCipher.from_settings(settings)
    except CredentialCryptoUnavailable:
        return False
    return True


def _personal_posture_assembled(
    *,
    profile_selected: bool,
    feature_gates_valid: bool,
    runtime_gate: bool,
    planner_gate: bool,
    multi_gate: bool,
    canary_active: bool,
    scope_matches: bool,
    live_owner_verified: bool,
    environment_allowed: bool,
    gateway_configured: bool,
    memory_crypto_configured: bool,
    migration_ready: bool,
    blockers: list[str],
) -> bool:
    return bool(
        profile_selected
        and feature_gates_valid
        and runtime_gate
        and not planner_gate
        and not multi_gate
        and canary_active
        and scope_matches
        and live_owner_verified
        and environment_allowed
        and gateway_configured
        and memory_crypto_configured
        and migration_ready
        and not blockers
    )


def _append_external_posture_blockers(
    blockers: list[str],
    *,
    environment_allowed: bool,
    gateway_configured: bool,
    memory_crypto_configured: bool,
) -> None:
    if not environment_allowed:
        blockers.append("personal Runtime requires the production environment")
    if not gateway_configured:
        blockers.append("Model Gateway is unavailable")
    if not memory_crypto_configured:
        blockers.append("Memory content encryption key is unavailable")


def personal_alpha_posture(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    profile: str | None = None,
    config_path: str | None = None,
    state_dir: str | None = None,
    readiness_root: str | None = None,
    gate_values: Mapping[str, object] | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Any] | None = None,
    gateway: ModelGateway | UnavailableModelGateway | None = None,
) -> PersonalAlphaPosture:
    blockers: list[str] = []
    profile_selected = False
    feature_gates_valid = False
    runtime_gate = False
    planner_gate = False
    multi_gate = False
    canary_state = PersonalRuntimeState.INACTIVE.value
    canary_active = False
    canary_id: str | None = None
    canary_expires_at: str | None = None
    scope_matches = False
    live_owner_verified = False
    migration_ready = False
    settings = settings or get_settings()
    environment_allowed = settings.env is Environment.PRODUCTION
    gateway = gateway if gateway is not None else configured_model_gateway()
    gateway_configured = bool(
        not isinstance(gateway, UnavailableModelGateway)
        or _personal_gateway_resolver_available(settings)
    )
    memory_crypto_configured = _memory_crypto_available(settings)
    factory: sessionmaker[Any] | None = session_factory
    try:
        selected_profile = (
            profile if profile is not None else os.environ.get(PERSONAL_RUNTIME_PROFILE_ENV)
        )
        selected_config = (
            config_path if config_path is not None else os.environ.get(PERSONAL_RUNTIME_CONFIG_ENV)
        )
        selected_state = (
            state_dir if state_dir is not None else os.environ.get(PERSONAL_RUNTIME_STATE_DIR_ENV)
        )
        selected_readiness_root = (
            readiness_root
            if readiness_root is not None
            else os.environ.get(PERSONAL_RUNTIME_READINESS_ROOT_ENV)
        )
        profile_selected = resolve_personal_runtime_profile(selected_profile)
        if not profile_selected:
            blockers.append("personal Runtime profile is not selected")
            raise PersonalAlphaConfigurationError("profile_not_selected")
        runtime_gate, planner_gate, multi_gate = _feature_gates(
            gate_values if gate_values is not None else os.environ
        )
        feature_gates_valid = True
        config, _, state_path, _ = _load_runtime_inputs(
            profile=selected_profile,
            config_path=selected_config,
            state_dir=selected_state,
            readiness_root=selected_readiness_root,
        )
        status = read_personal_runtime_status(state_path)
        canary_state = status.state.value
        canary_active = status.active
        canary_id = status.canary_id
        canary_expires_at = status.expires_at
        if not status.active:
            blockers.extend(status.blockers or status.vetoes or ("personal canary is inactive",))
        if not personal_runtime_status_binding_valid(config, status):
            blockers.append("personal canary ledger binding drifted")
        scope_matches = (
            tenant_id == config.tenant_id
            and workspace_id == config.workspace_id
            and actor_user_id == config.owner_user_id
        )
        if not scope_matches:
            blockers.append("request is outside the personal canary scope")
        factory = factory or get_session_factory(settings)
        migration_ready = _migration_head(factory) == _EXPECTED_MIGRATION_HEAD
        if not migration_ready:
            blockers.append(f"migration head is not {_EXPECTED_MIGRATION_HEAD}")
        if scope_matches:
            session = _open_tenant_session(factory, tenant_id)
            try:
                _verify_live_single_owner(
                    session,
                    config=config,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    lock=False,
                )
                live_owner_verified = True
            finally:
                session.rollback()
                session.close()
    except (PersonalAlphaConfigurationError, PersonalRuntimeConfigurationError) as exc:
        _record_configuration_failure(blockers, exc)
    except OSError:
        blockers.append("personal_runtime_filesystem_unavailable")
    except SQLAlchemyError:
        blockers.append("personal_runtime_database_unavailable")
    _append_external_posture_blockers(
        blockers,
        environment_allowed=environment_allowed,
        gateway_configured=gateway_configured,
        memory_crypto_configured=memory_crypto_configured,
    )
    assembled = _personal_posture_assembled(
        profile_selected=profile_selected,
        feature_gates_valid=feature_gates_valid,
        runtime_gate=runtime_gate,
        planner_gate=planner_gate,
        multi_gate=multi_gate,
        canary_active=canary_active,
        scope_matches=scope_matches,
        live_owner_verified=live_owner_verified,
        environment_allowed=environment_allowed,
        gateway_configured=gateway_configured,
        memory_crypto_configured=memory_crypto_configured,
        migration_ready=migration_ready,
        blockers=blockers,
    )
    return PersonalAlphaPosture(
        profile_selected=profile_selected,
        feature_gates_valid=feature_gates_valid,
        runtime_gate_enabled=runtime_gate,
        planner_gate_enabled=planner_gate,
        multi_agent_gate_enabled=multi_gate,
        canary_state=canary_state,
        canary_active=canary_active,
        canary_id=canary_id,
        canary_expires_at=canary_expires_at,
        scope_matches=scope_matches,
        live_owner_verified=live_owner_verified,
        environment_allowed=environment_allowed,
        gateway_configured=gateway_configured,
        memory_crypto_configured=memory_crypto_configured,
        migration_ready=migration_ready,
        assembled=assembled,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def build_personal_agent_alpha(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    profile: str | None = None,
    config_path: str | None = None,
    state_dir: str | None = None,
    readiness_root: str | None = None,
    gate_values: Mapping[str, object] | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Any] | None = None,
    gateway: ModelGateway | UnavailableModelGateway | None = None,
) -> PersonalCanaryAgentAlpha | UnavailableAgentAlpha:
    selected_profile = (
        profile if profile is not None else os.environ.get(PERSONAL_RUNTIME_PROFILE_ENV)
    )
    if not resolve_personal_runtime_profile(selected_profile):
        return UnavailableAgentAlpha()
    settings = settings or get_settings()
    gateway = gateway if gateway is not None else configured_model_gateway()
    factory = session_factory or get_session_factory(settings)
    posture = personal_alpha_posture(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        profile=selected_profile,
        config_path=config_path,
        state_dir=state_dir,
        readiness_root=readiness_root,
        gate_values=gate_values,
        settings=settings,
        session_factory=factory,
        gateway=gateway,
    )
    if not posture.assembled:
        return UnavailableAgentAlpha()
    selected_config = (
        config_path if config_path is not None else os.environ.get(PERSONAL_RUNTIME_CONFIG_ENV)
    )
    selected_state = (
        state_dir if state_dir is not None else os.environ.get(PERSONAL_RUNTIME_STATE_DIR_ENV)
    )
    selected_readiness_root = (
        readiness_root
        if readiness_root is not None
        else os.environ.get(PERSONAL_RUNTIME_READINESS_ROOT_ENV)
    )
    config, config_file, state_path, readiness_path = _load_runtime_inputs(
        profile=selected_profile,
        config_path=selected_config,
        state_dir=selected_state,
        readiness_root=selected_readiness_root,
    )
    live_gate_values = gate_values if gate_values is not None else os.environ
    personal_gateway = UserModelGatewayResolver(
        factory,
        settings=settings,
        operator_gateway=gateway,
    )
    try:
        memory_compiler = SqlAlchemyMemoryCompiler(
            factory,
            cipher=MemoryContentCipher.from_settings(settings),
        )
    except MemoryCryptoUnavailable:
        return UnavailableAgentAlpha()
    delegate = AgentAlphaService(
        profiles=RegistryProfileResolver(factory),
        knowledge=RagKnowledgeRetriever(factory),
        ledger=LedgerInvocationAdapter(
            factory,
            invocation_guard=_invocation_guard(
                config,
                config_path=config_file,
                readiness_root=readiness_path,
                state_path=state_path,
                gate_values=live_gate_values,
                settings=settings,
            ),
        ),
        gateway=gateway,
        gateway_resolver=personal_gateway,
        preferences_resolver=personal_gateway,
        memory_compiler=memory_compiler,
        skill_resolver=SqlAlchemySkillResolver(factory),
        runtime_guard=_runtime_checkpoint(
            config=config,
            config_path=config_file,
            readiness_root=readiness_path,
            state_path=state_path,
            gate_values=live_gate_values,
            settings=settings,
        ),
    )
    return PersonalCanaryAgentAlpha(delegate, config)


__all__ = [
    "PERSONAL_RUNTIME_CONFIG_ENV",
    "PERSONAL_RUNTIME_PROFILE",
    "PERSONAL_RUNTIME_PROFILE_ENV",
    "PERSONAL_RUNTIME_READINESS_ROOT_ENV",
    "PERSONAL_RUNTIME_STATE_DIR_ENV",
    "PersonalAlphaConfigurationError",
    "PersonalAlphaPosture",
    "PersonalCanaryAgentAlpha",
    "build_personal_agent_alpha",
    "personal_alpha_posture",
    "resolve_personal_runtime_profile",
]
