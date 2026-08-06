"""Server-owned onboarding for the first real AI Workspace.

The onboarding slice creates only two immutable, low-risk building blocks:

* one versioned Workspace template; and
* one sealed, tool-free single-Agent Alpha definition/version.

It never enables Planner, multi-Agent, MCP, Skills, shell, SQL, arbitrary HTTP
or hostile-code execution.  All writes use the existing Workspace and Agent
Registry services in the caller-owned transaction, so tenant locks,
idempotency, resources and audit records remain one atomic lifecycle.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import AgentDefinitionModel, AgentVersionModel
from omnibase.agent_registry.service import RegistryPersistenceService
from omnibase.production.phase5_registry_contract import (
    AgentDefinition,
    AgentVersionManifest,
    BindingState,
    DefaultBudgetPolicy,
    DefinitionState,
    RiskLevel,
    VersionState,
    WorkspaceAgentBinding,
)
from omnibase.workspaces.models import NodeAttestation, Workspace, WorkspaceNode, WorkspaceTemplate
from omnibase.workspaces.service import (
    WorkspaceNotFound,
    canonical_digest,
    get_active_attested_node,
    get_workspace,
    register_template,
)

DEFAULT_TEMPLATE_KEY = "omnibase.ai-workbench"
DEFAULT_TEMPLATE_VERSION = 1
DEFAULT_AGENT_KEY = "omnibase.tool-free-research-assistant"
DEFAULT_AGENT_VERSION = "1.0.0"
_BUILTIN_CREATED_AT = "2026-08-05T00:00:00+00:00"
_LOCAL_RUNTIME_VERIFIER = "omnibase-local-model-gateway-v1"
_LOCAL_RUNTIME_ATTESTATION_TTL = timedelta(minutes=10)


def _resolve_local_runtime_deployment_id(configured: str | None) -> str:
    """Return an explicit deployment ID or a process-boot identity.

    A hostname is deliberately insufficient: two backend processes or a
    restarted bare-metal process on the same host must not inherit the same
    trusted Node identity.  Operators can provide a stable deployment ID;
    otherwise this module import receives a fresh, process-local boot ID.
    """
    normalized = (configured or "").strip()
    return normalized or f"process-{uuid4()}"


_LOCAL_RUNTIME_DEPLOYMENT_ID = _resolve_local_runtime_deployment_id(
    os.environ.get("OMNIBASE_DEPLOYMENT_INSTANCE_ID")
)
_BUDGET = DefaultBudgetPolicy(
    max_tokens=16_384,
    max_cost_units=100_000,
    max_wall_clock_seconds=120,
    max_tool_calls=1,
)


def _tenant_uuid(tenant_id: str) -> UUID:
    return UUID(tenant_id)


def _stable_id(tenant_id: str, logical_name: str) -> str:
    return str(uuid5(_tenant_uuid(tenant_id), logical_name))


def default_agent_ids(tenant_id: str) -> tuple[str, str]:
    """Return the deterministic Definition and Version IDs for a tenant."""
    return (
        _stable_id(tenant_id, f"agent-definition:{DEFAULT_AGENT_KEY}"),
        _stable_id(
            tenant_id,
            f"agent-version:{DEFAULT_AGENT_KEY}:{DEFAULT_AGENT_VERSION}",
        ),
    )


def local_model_runtime_identity_digest(tenant_id: str, workspace_id: str) -> str:
    """Identify this deployment's in-process, tool-free Model Gateway anchor."""
    return canonical_digest(
        {
            "kind": "tool_free_local_model_gateway",
            "deployment_instance_id": _LOCAL_RUNTIME_DEPLOYMENT_ID,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "verifier": _LOCAL_RUNTIME_VERIFIER,
        }
    )


def ensure_local_model_runtime_anchor(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    workspace: Workspace,
) -> WorkspaceNode:
    """Create or renew the bounded local Model Gateway node for a Workspace.

    This is a server-created logical process identity, not proof of Sandbox,
    Runner, host isolation, or hostile-code execution.  The authenticated
    caller must retain ``workspace.run`` permission, while Node lifecycle is
    server-owned and never borrows the Browser caller's ``nodes.manage`` role.
    """
    workspace_id = str(workspace.id)
    live_workspace = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.run",
        lock=True,
    )
    identity_digest = local_model_runtime_identity_digest(tenant_id, workspace_id)
    node = session.execute(
        select(WorkspaceNode)
        .where(
            WorkspaceNode.tenant_id == tenant_id,
            WorkspaceNode.workspace_id == workspace_id,
            WorkspaceNode.identity_digest == identity_digest,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if node is not None:
        try:
            return get_active_attested_node(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                node_id=node.id,
                lock=True,
            )
        except WorkspaceNotFound:
            # A revoked Node is an append-only security fact.  Never clear its
            # revoked marker or silently reuse its identity.  A new deployment
            # instance receives a different identity digest and therefore a
            # new Node; an expired attestation for the same still-active
            # deployment may be renewed below.
            if node.state != "active" or node.revoked_at is not None:
                raise WorkspaceNotFound("local model runtime node is revoked") from None
            now = session.execute(select(func.now())).scalar_one()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            if node.attestation_state != "verified":
                raise WorkspaceNotFound("local model runtime node is not renewable") from None
            node.last_seen_at = now
            node.version += 1
            session.add(
                NodeAttestation(
                    tenant_id=tenant_id,
                    node_id=node.id,
                    nonce_digest=canonical_digest(
                        {"node_id": node.id, "nonce": str(uuid4()), "verified_at": now.isoformat()}
                    ),
                    evidence_digest=canonical_digest(
                        {
                            "identity_digest": identity_digest,
                            "deployment_instance_id": _LOCAL_RUNTIME_DEPLOYMENT_ID,
                            "runtime": "in_process_model_gateway",
                            "tools_enabled": False,
                            "planner_enabled": False,
                            "multi_agent_enabled": False,
                        }
                    ),
                    verifier=_LOCAL_RUNTIME_VERIFIER,
                    state="verified",
                    verified_at=now,
                    expires_at=now + _LOCAL_RUNTIME_ATTESTATION_TTL,
                )
            )
            session.flush()
            return node

    now = session.execute(select(func.now())).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    node = WorkspaceNode(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=live_workspace.owner_user_id,
        display_name="OmniBase Local Model Gateway",
        identity_digest=identity_digest,
        state="active",
        attestation_state="verified",
        last_seen_at=now,
    )
    session.add(node)
    session.flush()
    session.add(
        NodeAttestation(
            tenant_id=tenant_id,
            node_id=node.id,
            nonce_digest=canonical_digest(
                {"node_id": node.id, "nonce": str(uuid4()), "verified_at": now.isoformat()}
            ),
            evidence_digest=canonical_digest(
                {
                    "identity_digest": identity_digest,
                    "deployment_instance_id": _LOCAL_RUNTIME_DEPLOYMENT_ID,
                    "runtime": "in_process_model_gateway",
                    "tools_enabled": False,
                    "planner_enabled": False,
                    "multi_agent_enabled": False,
                }
            ),
            verifier=_LOCAL_RUNTIME_VERIFIER,
            state="verified",
            verified_at=now,
            expires_at=now + _LOCAL_RUNTIME_ATTESTATION_TTL,
        )
    )
    session.flush()
    return node


def ensure_default_onboarding_assets(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    request_id: str,
) -> tuple[WorkspaceTemplate, AgentVersionModel]:
    """Create or replay the safe template and sealed tool-free AgentVersion."""
    template = register_template(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        template_key=DEFAULT_TEMPLATE_KEY,
        version=DEFAULT_TEMPLATE_VERSION,
        display_name="AI 工作台 / AI Workbench",
        template_spec={
            "workspace_kind": "ai_workbench",
            "agent_profile": "tool_free_single_alpha",
            "knowledge_mode": "workspace_read_only",
        },
        request_id=request_id,
    )

    definition_id, version_id = default_agent_ids(tenant_id)
    definition_row = session.execute(
        select(AgentDefinitionModel).where(
            AgentDefinitionModel.id == definition_id,
            AgentDefinitionModel.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    version_row = session.execute(
        select(AgentVersionModel).where(
            AgentVersionModel.id == version_id,
            AgentVersionModel.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if definition_row is not None or version_row is not None:
        if definition_row is None or version_row is None:
            raise RuntimeError("onboarding_agent_identity_partial")
        return template, version_row

    registry = RegistryPersistenceService(session)
    definition = AgentDefinition(
        schema_version=1,
        agent_definition_id=definition_id,
        tenant_id=tenant_id,
        stable_logical_key=DEFAULT_AGENT_KEY,
        display_name="OmniBase Research Assistant",
        description=(
            "A tool-free single Agent that reasons over read-only Workspace "
            "knowledge and records every invocation in the durable Task Ledger."
        ),
        risk_level=RiskLevel.LOW,
        allowed_installation_scopes=("workspace",),
        definition_state=DefinitionState.ACTIVE,
        created_by=actor_user_id,
        created_at=_BUILTIN_CREATED_AT,
        metadata_version=1,
    )
    registry.register_definition(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        definition=definition,
        idempotency_key=f"builtin-definition:{definition_id}",
    )

    manifest = AgentVersionManifest(
        schema_version=1,
        agent_version_id=version_id,
        agent_definition_id=definition_id,
        tenant_id=tenant_id,
        version=DEFAULT_AGENT_VERSION,
        manifest_digest="0" * 64,
        model_policy_id=_stable_id(tenant_id, "model-policy:tool-free-default"),
        instructions_digest=("a4fef8585a04b3548e8dbe58dbf92cdcb1eeb2283cd4e27751b95efc34748998"),
        max_context_tokens=16_384,
        allowed_tool_ids=(),
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
        },
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        risk_level=RiskLevel.LOW,
        memory_policy_id=None,
        max_concurrency=1,
        default_budget=_BUDGET,
        version_state=VersionState.SEALED,
        created_by=actor_user_id,
        created_at=_BUILTIN_CREATED_AT,
    )
    manifest = replace(manifest, manifest_digest=manifest.canonical_digest())
    version_row = registry.seal_version(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        version=manifest,
        idempotency_key=f"builtin-version:{version_id}",
    )
    return template, version_row


def install_default_agent_for_workspace(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    workspace: Workspace,
    request_id: str,
) -> None:
    """Install the built-in Alpha only for the built-in onboarding template."""
    template = session.execute(
        select(WorkspaceTemplate).where(
            WorkspaceTemplate.id == workspace.template_id,
            WorkspaceTemplate.tenant_id == tenant_id,
        )
    ).scalar_one()
    if (
        template.template_key != DEFAULT_TEMPLATE_KEY
        or template.version != DEFAULT_TEMPLATE_VERSION
    ):
        return

    _, version = ensure_default_onboarding_assets(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    binding_id = str(
        uuid5(
            UUID(str(workspace.id)),
            f"agent-binding:{version.id}:generation:{workspace.generation}",
        )
    )
    binding = WorkspaceAgentBinding(
        schema_version=1,
        workspace_agent_binding_id=binding_id,
        tenant_id=tenant_id,
        workspace_id=str(workspace.id),
        workspace_generation=workspace.generation,
        agent_definition_id=version.definition_id,
        agent_version_id=version.id,
        agent_version_digest=version.manifest_digest,
        installation_state=BindingState.INSTALLED,
        # Database trigger and public contract both require opaque logical
        # identifiers here, not action names containing punctuation.
        resource_scopes=("workspace_read", "workspace_knowledge_read"),
        default_budget_policy=_BUDGET,
        installed_by=actor_user_id,
        approval_id=None,
        created_at=datetime.now(UTC).isoformat(),
        disabled_at=None,
        superseded_by=None,
    )
    RegistryPersistenceService(session).install_binding(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        binding=binding,
        idempotency_key=f"builtin-binding:{binding_id}",
    )
    ensure_local_model_runtime_anchor(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        workspace=workspace,
    )


__all__ = [
    "DEFAULT_AGENT_KEY",
    "DEFAULT_AGENT_VERSION",
    "DEFAULT_TEMPLATE_KEY",
    "DEFAULT_TEMPLATE_VERSION",
    "default_agent_ids",
    "ensure_default_onboarding_assets",
    "ensure_local_model_runtime_anchor",
    "install_default_agent_for_workspace",
    "local_model_runtime_identity_digest",
]
